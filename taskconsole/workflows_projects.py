"""Content-addressed source projects; archive validation precedes extraction."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path,PurePosixPath
import stat
import zipfile
from .store import uid,stamp

MAX_BYTES=100*1024*1024
MAX_FILES=2000


def relative_path(value):
    if not isinstance(value,str) or not value or '\\' in value or '\x00' in value:raise ValueError('Invalid project path')
    path=PurePosixPath(value)
    if path.is_absolute() or any(p in {'..','.'} for p in value.split('/')) or ':' in path.parts[0]:raise ValueError('Project path escapes its directory')
    return path.as_posix()


def file_hash(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def manifest_tree(directory):
    root=Path(directory)
    result={}
    for path in sorted(root.rglob('*')):
        if '__pycache__' in path.parts:continue
        name=path.relative_to(root).as_posix()
        if path.is_symlink():result[name]='symlink:'+os.readlink(path)
        elif path.is_file():result[name]=file_hash(path)
    return result


class SourceProjects:
    def __init__(self,store):self.store=store
    def list(self):
        with self.store.transaction() as tx:return [{k:v for k,v in r.items() if k!='directory'} for r in tx.all('source_project')]
    def get(self,pid,private=False):
        with self.store.transaction() as tx:record=tx.get('source_project',pid)
        if not record:raise ValueError('Source project not found')
        return record if private else {k:v for k,v in record.items() if k!='directory'}
    def create(self,data):
        files=data.get('files')
        if not isinstance(files,dict) or not files or len(files)>MAX_FILES:raise ValueError('Project requires a bounded files object')
        payload={relative_path(name):value.encode('utf-8') for name,value in files.items() if isinstance(value,str)}
        if len(payload)!=len(files):raise ValueError('Source files must contain text')
        return self._save(data,payload)
    def _save(self,data,files):
        if sum(len(v) for v in files.values())>MAX_BYTES:raise ValueError('Project exceeds 100 MiB')
        entry=relative_path(data.get('entrypoint',''))
        if entry not in files:raise ValueError('Project entrypoint is missing')
        manifest={name:hashlib.sha256(value).hexdigest() for name,value in sorted(files.items())}
        identity={'files':manifest,'language':data.get('language'),'entrypoint':entry,'config':data.get('config',{})}
        digest=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        directory=self.store.path/'source-projects'/digest
        directory.mkdir(parents=True,exist_ok=True)
        for name,value in files.items():
            target=directory/name;target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists() and file_hash(target)!=manifest[name]:raise ValueError('Immutable project content changed')
            if not target.exists():target.write_bytes(value)
        record={'id':uid(),'name':str(data.get('name','Project'))[:200],'language':data.get('language'),'entrypoint':entry,'config':data.get('config',{}),'digest':digest,'manifest':manifest,'files':sorted(files),'size':sum(len(v) for v in files.values()),'directory':str(directory),'created_at':stamp()}
        with self.store.transaction() as tx:tx.put('source_project',record)
        return {k:v for k,v in record.items() if k!='directory'}
    def upload(self,name,language,entrypoint,payload,filename,config=None):
        if len(payload)>MAX_BYTES:raise ValueError('Upload exceeds 100 MiB')
        suffix=Path(filename).suffix.lower();files={}
        if suffix=='.zip':
            try:
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                    entries=archive.infolist()
                    if len(entries)>MAX_FILES or sum(i.file_size for i in entries)>MAX_BYTES:raise ValueError('Archive quota exceeded')
                    seen=set()
                    for item in entries:
                        key=relative_path(item.filename.rstrip('/'))
                        if key in seen:raise ValueError('Duplicate archive path')
                        seen.add(key)
                        if stat.S_ISLNK(item.external_attr>>16):raise ValueError('Archive symlinks are not allowed')
                        if item.flag_bits&1:raise ValueError('Encrypted archives are not supported')
                        if not item.is_dir():files[key]=archive.read(item)
            except zipfile.BadZipFile as exc:raise ValueError('Invalid ZIP project') from exc
        else:
            files[relative_path(entrypoint)]=payload
            if suffix=='.jar':config={**(config or {}),'project_type':'jar'}
        return self._save({'name':name,'language':language,'entrypoint':entrypoint,'config':config or {}},files)
