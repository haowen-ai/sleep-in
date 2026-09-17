"""Portable application backup; credentials and variable values stay separate."""
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path,PurePosixPath
from .runtime import build_environment
from .service import ACTIVE

KINDS=('meta','script','version','task','execution','audit')
ID_PATTERN=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')


def _valid_id(value):
    return isinstance(value,str) and ID_PATTERN.fullmatch(value) is not None


def _validate_records(records):
    if not isinstance(records,dict) or any(k not in KINDS for k in records):
        raise ValueError('Backup contains unsupported records')
    for kind,rows in records.items():
        if not isinstance(rows,list):raise ValueError('Backup record collections must be lists')
        seen=set()
        for row in rows:
            if not isinstance(row,dict) or not _valid_id(row.get('id')):
                raise ValueError('Backup contains an unsafe record identifier')
            if row['id'] in seen:raise ValueError('Backup contains duplicate record identifiers')
            seen.add(row['id'])


def backup(store,destination,include_runs=False):
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('Backup destination already exists')
    included=[store.path/'scripts']+([store.path/'runs'] if include_runs else [])
    if any(destination.is_relative_to(folder.resolve()) for folder in included):
        raise ValueError('Backup destination must be outside included data directories')
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w+b',prefix='.taskconsole-backup-',suffix='.tmp',dir=destination.parent,delete=False) as output:
            temporary=Path(output.name);os.chmod(temporary,0o600)
            with store.transaction() as tx:
                if any(r['status'] in ACTIVE for r in tx.all('execution')):raise ValueError('Finish or cancel active executions before backup')
                records={kind:tx.all(kind) for kind in KINDS}
                if any(v['status']=='building' for v in records['version']):raise ValueError('Wait for publications to finish before backup')
                if not include_runs:
                    for run in records['execution']:run['logs_expired']=True
                with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr('records.json',json.dumps(records,ensure_ascii=False))
                    folders=['scripts']+(['runs'] if include_runs else [])
                    for name in folders:
                        for path in (store.path/name).rglob('*'):
                            if path.is_file() and not path.is_symlink():archive.write(path,path.relative_to(store.path).as_posix())
            output.flush();os.fsync(output.fileno())
        try:os.link(temporary,destination)
        except FileExistsError as exc:raise ValueError('Backup destination already exists') from exc
    finally:
        if temporary:temporary.unlink(missing_ok=True)


def restore(store,archive_path):
    with store.transaction() as tx:
        if tx.all('task') or tx.all('user') or tx.all('version'):raise ValueError('Restore requires a fresh, stopped instance')
    with tempfile.TemporaryDirectory(prefix='console-restore-') as temp_name:
        temp=Path(temp_name)
        with zipfile.ZipFile(archive_path) as archive:
            members=archive.infolist()
            if sum(m.file_size for m in members)>2*1024**3 or len(members)>50000:raise ValueError('Backup exceeds supported restore limits')
            seen=set()
            for info in members:
                path=PurePosixPath(info.filename)
                kind=(info.external_attr>>16)&0o170000
                valid_root = path == PurePosixPath('records.json') or (len(path.parts)>=2 and path.parts[0] in {'scripts','runs'})
                if path.is_absolute() or '..' in path.parts or '\\' in info.filename or path in seen or not valid_root or kind not in {0,0o040000,0o100000}:raise ValueError('Unsafe backup path')
                if path.parts[0] in {'scripts','runs'} and not _valid_id(path.parts[1]):raise ValueError('Unsafe backup path identifier')
                seen.add(path)
            if PurePosixPath('records.json') not in seen:raise ValueError('Backup is missing records.json')
            archive.extractall(temp)
        records=json.loads((temp/'records.json').read_text(encoding='utf-8'))
        _validate_records(records)
        for version in records.get('version',[]):
            if version.get('status') in {'published','retired'} and not (temp/'scripts'/version['id']/'main.py').is_file():
                raise ValueError('Backup is missing source for a published version')
        for name in ('scripts','runs'):
            if (temp/name).exists():shutil.copytree(temp/name,store.path/name,dirs_exist_ok=True)
        for version in records.get('version',[]):
            if version['status'] not in {'published','retired'}:continue
            source=store.path/'scripts'/version['id']
            frozen=version.get('freeze','')
            if frozen:(source/'requirements.txt').write_text(frozen)
            version['runtime']=build_environment(source,store.path/'runtimes'/version['id'])
        with store.transaction() as tx:
            for kind,rows in records.items():
                for row in rows:
                    if kind=='task':row.update(enabled=False,next_run=None,revision=row.get('revision',0)+1)
                    if kind=='script' and row.get('draft_dir'):row['draft_dir']=str(store.path/'scripts'/('draft-'+row['id']))
                    if kind=='meta':row.update(last_tick=None,worker=None)
                    if kind=='execution' and row['status'] in ACTIVE:row.update(status='interrupted',reason='restored')
                    tx.put(kind,row)
