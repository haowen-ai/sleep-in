"""Authenticated encrypted owner backups. Restore only into a fresh stopped instance."""
import base64
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import zipfile

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from fastapi import Request
from fastapi.responses import Response
from .store import stamp

MAGIC=b'SLEEPIN2\0'
MAX_BYTES=256*1024*1024
FOLDERS={'scripts','runs','workflow-runs','workflow-connections','source-projects'}
TRANSIENT={'session','attempt','workflow_worker','workflow_lease','workflow_cursor','workflow_occurrence','workflow_notification','workflow_notice_seen','workflow_notice_state'}


def cipher(password,salt):
    if not isinstance(password,str) or not 12<=len(password)<=1024:raise ValueError('Backup password must have 12–1024 characters')
    key=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,600000)
    return Fernet(base64.urlsafe_b64encode(key))


def create_backup(store,password):
    output=io.BytesIO();hashes={};total=0
    with store.transaction() as tx:
        for kind in ('workflow_run','execution'):
            if any(r['status'] in {'queued','running','cancelling'} for r in tx.all(kind)):raise ValueError('Finish or cancel active executions before backup')
        if any(r.get('status')=='building' for r in tx.all('runtime_version')+tx.all('version')):raise ValueError('Wait for runtime builds before backup')
        records={}
        for kind,payload in tx.conn.execute(select(store.table.c.kind,store.table.c.payload)):
            if kind in TRANSIENT or kind=='meta' and payload['id'] in {'workflow_worker','workflow_cleanup'}:continue
            records.setdefault(kind,[]).append(payload)
        manifest={'format':2,'created_at':stamp(),'source_root':str(store.path),'variable_key':(store.path/'variable-key').read_text().strip(),'records':records,'files':hashes}
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(FOLDERS):
                if (store.path/name).is_symlink():raise ValueError('Backup refuses symlink managed roots')
                for path in sorted((store.path/name).rglob('*')):
                    if path.is_symlink():
                        # Installed dependencies are rebuilt, not archived through run-local links.
                        if name=='workflow-runs' and path.name=='node_modules' and path.parent.name=='project' and path.resolve().is_relative_to((store.path/'runtime-packs').resolve()):continue
                        raise ValueError('Backup refuses symlinks; copy source files into managed storage')
                    if not path.is_file():continue
                    size=path.stat().st_size;total+=size
                    if total>MAX_BYTES or len(hashes)>=20000:raise ValueError('Backup exceeds 256 MiB / 20,000 files')
                    relative=path.relative_to(store.path).as_posix();data=path.read_bytes()
                    hashes[relative]=hashlib.sha256(data).hexdigest();archive.writestr(relative,data)
            archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False))
    salt=os.urandom(16)
    return MAGIC+salt+cipher(password,salt).encrypt(output.getvalue())


def _read(blob,password):
    if len(blob)>MAX_BYTES*2 or not blob.startswith(MAGIC) or len(blob)<len(MAGIC)+17:raise ValueError('Invalid backup format or size')
    salt=blob[len(MAGIC):len(MAGIC)+16]
    try:plain=cipher(password,salt).decrypt(blob[len(MAGIC)+16:])
    except InvalidToken as exc:raise ValueError('Incorrect password or damaged backup') from exc
    try:
        archive=zipfile.ZipFile(io.BytesIO(plain));members=archive.infolist();seen=set()
        if sum(m.file_size for m in members)>MAX_BYTES+16*1024*1024 or len(members)>20001:raise ValueError('Expanded backup exceeds limits')
        for member in members:
            p=PurePosixPath(member.filename);mode=(member.external_attr>>16)&0o170000
            if p.is_absolute() or '..' in p.parts or '\\' in member.filename or member.filename in seen or mode not in {0,0o100000,0o040000}:raise ValueError('Unsafe backup path')
            if member.filename!='manifest.json' and (len(p.parts)<2 or p.parts[0] not in FOLDERS):raise ValueError('Unsupported backup content')
            seen.add(member.filename)
        manifest=json.loads(archive.read('manifest.json'))
        if manifest.get('format')!=2 or not isinstance(manifest.get('records'),dict):raise ValueError('Unsupported backup schema')
        if set(manifest.get('files',{}))!=seen-{'manifest.json'}:raise ValueError('Incomplete backup manifest')
        for name,digest in manifest['files'].items():
            if hashlib.sha256(archive.read(name)).hexdigest()!=digest:raise ValueError('Backup checksum mismatch')
        for kind,rows in manifest['records'].items():
            if not re.fullmatch(r'[a-z_]{1,32}',kind) or not isinstance(rows,list):raise ValueError('Invalid backup records')
            ids=[]
            for row in rows:
                if not isinstance(row,dict) or not isinstance(row.get('id'),str) or len(row['id'])>128:raise ValueError('Invalid record identifier')
                ids.append(row['id'])
            if len(ids)!=len(set(ids)):raise ValueError('Duplicate backup records')
        Fernet(manifest['variable_key'].encode())
        return archive,manifest
    except (KeyError,zipfile.BadZipFile,TypeError,json.JSONDecodeError) as exc:raise ValueError('Invalid backup contents') from exc


def inspect_backup(blob,password):
    archive,manifest=_read(blob,password);archive.close()
    return {'format':manifest['format'],'created_at':manifest['created_at'],'counts':{k:len(v) for k,v in manifest['records'].items()},'files':len(manifest['files']),'restored_schedules':'disabled','runtime_rebuild_required':True}


def restore_backup(store,blob,password):
    archive,manifest=_read(blob,password)
    old_cipher=Fernet(manifest['variable_key'].encode());old_root=manifest['source_root']
    path_keys={'path','directory','executable','compiler','java','python','home','argv'}
    business_keys={'params','inputs','data','source','shared_files'}
    def convert(value,trail=()):
        key=trail[-1] if trail else ''
        if isinstance(value,dict):return {k:convert(v,trail+(k,)) for k,v in value.items()}
        if isinstance(value,list):return [convert(v,trail) for v in value]
        if isinstance(value,str):
            if len(trail)==1 and key in {'encrypted','encrypted_config','encrypted_url','secret'} and value.startswith('gAAAA'):
                return store.fernet.encrypt(old_cipher.decrypt(value.encode())).decode()
            if key in path_keys and not business_keys.intersection(trail) and value.startswith(old_root+'/'):return str(store.path)+value[len(old_root):]
        return value
    moved=[]
    def rollback_files():
        for directory,existed in reversed(moved):
            shutil.rmtree(directory)
            if existed:directory.mkdir(mode=0o700)
    try:
        from .local import InstanceLock
        # Rollback callbacks run before the supervisor lock is released.
        with InstanceLock(store.path), ExitStack() as undo, tempfile.TemporaryDirectory(prefix='sleep-in-restore-',dir=store.path.parent) as scratch:
            undo.callback(rollback_files)
            temp=Path(scratch)
            for name in manifest['files']:
                target=temp/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read(name));target.chmod(0o600)
            with store.transaction() as tx:
                existing=tx.conn.execute(select(store.table.c.kind)).scalars()
                if any(kind!='meta' for kind in existing):raise ValueError('Restore requires a fresh, stopped instance')
                for name in FOLDERS:
                    destination=store.path/name
                    if destination.is_symlink() or destination.exists() and (not destination.is_dir() or any(destination.iterdir())):raise ValueError('Restore requires empty managed data directories')
                for name in FOLDERS:
                    if (temp/name).exists():
                        destination=store.path/name;existed=destination.exists()
                        if existed:destination.rmdir()
                        shutil.move(str(temp/name),destination);moved.append((destination,existed))
                for kind,rows in manifest['records'].items():
                    if kind in TRANSIENT:continue
                    for raw in rows:
                        row=convert(raw)
                        if kind in {'workflow','task'}:
                            row.update(enabled=False,next_run=None)
                            for trigger in row.get('triggers',[]):trigger['enabled']=False
                        if kind in {'runtime_version','runtime_toolchain','version'}:row.update(status='failed',reason='Restored: rebuild dependencies/toolchains and republish before execution')
                        if kind=='workflow_version':row['restore_requires_rebuild']=True
                        if kind=='workflow_run':row.pop('callback_token',None)
                        if kind=='meta' and row['id']=='settings':row.update(last_tick=None,worker=None)
                        tx.put(kind,row)
                tx.put('meta',{'id':'workflow_maintenance','paused':True,'reason':'Restored backup: rebuild runtimes and review schedules'})
                tx.put('meta',{'id':'workflow_schema','version':2,'restored_at':stamp()})
            undo.pop_all()
    finally:archive.close()
    return {'restored':True,'schedules_enabled':False,'runtime_rebuild_required':True}


def register_backup_routes(app,store,require):
    def auth(request):
        with store.transaction() as tx:require(request,tx,True)
    @app.post('/api/workflow-backups')
    def backup(request:Request,data:dict):
        auth(request);blob=create_backup(store,data.get('password'))
        return Response(blob,media_type='application/octet-stream',headers={'Content-Disposition':'attachment; filename="sleep-in-backup.sleepin"','Cache-Control':'no-store'})
    @app.post('/api/workflow-backups/inspect')
    def inspect(request:Request,data:dict):
        auth(request)
        try:blob=base64.b64decode(data.get('archive',''),validate=True)
        except (ValueError,TypeError) as exc:raise ValueError('Invalid archive encoding') from exc
        return inspect_backup(blob,data.get('password'))
