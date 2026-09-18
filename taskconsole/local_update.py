"""Owner-selected signed Mac updates with stopped-state backup and rollback.

The default verifier requires Developer ID, matching team identity, hardened runtime,
and Gatekeeper assessment. Injected callbacks exist for isolated fixture tests only;
the CLI and native app never expose a bypass.
"""
import argparse
import fcntl
import json
import hashlib
import secrets
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import time

from .local import read_json, write_json, status


def signature_team(bundle):
    response=subprocess.run(['/usr/bin/codesign','-d','--verbose=4',str(bundle)],capture_output=True,text=True,check=True)
    details=response.stderr+response.stdout
    team=next((line.split('=',1)[1] for line in details.splitlines() if line.startswith('TeamIdentifier=')),None)
    if not team or team=='not set' or 'Authority=Developer ID Application:' not in details or 'runtime' not in details:raise ValueError('Update requires a Developer ID signed application')
    return team


def verify_signed_bundle(bundle,expected_team=None):
    bundle=Path(bundle)
    if bundle.suffix!='.app' or not bundle.is_dir() or bundle.is_symlink():raise ValueError('Choose a downloaded Sleep In.app bundle')
    info=plistlib.loads((bundle/'Contents/Info.plist').read_bytes())
    if info.get('CFBundleIdentifier')!='local.sleepin.companion':raise ValueError('This update is not Sleep In')
    subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(bundle)],capture_output=True,check=True)
    team=signature_team(bundle)
    if expected_team and team!=expected_team:raise ValueError('Update signer differs from the installed application')
    subprocess.run(['/usr/sbin/spctl','--assess','--type','execute',str(bundle)],capture_output=True,check=True)
    return team


def tree_manifest(root):
    result={}
    for path in sorted(Path(root).rglob('*')):
        key=path.relative_to(root).as_posix()
        if path.is_symlink():result[key]={'symlink':os.readlink(path)}
        elif path.is_file():
            digest=hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
            result[key]={'sha256':digest.hexdigest()}
    return result


def launch_command(bundle,action,update_token=None,install_root=None):
    launcher=Path(bundle)/'Contents/Resources/app/launch-mac.command'
    env=dict(os.environ)
    if update_token:env['SLEEP_IN_UPDATE_TOKEN']=update_token
    if install_root:env['SLEEP_IN_INSTALL_DIR']=str(install_root)
    result=subprocess.run(['/bin/bash',str(launcher),action],capture_output=True,text=True,timeout=1200,env=env)
    return result.returncode,result.stdout[-2000:]+result.stderr[-2000:]


def wait_until_stopped(state,timeout=600):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if status(state).get('state')=='stopped':return True
        time.sleep(.5)
    return False


def update_bundle(candidate,current,install_root,*,mode='finish',verify=None,command=launch_command,wait_stopped=None):
    if any(Path(path).is_symlink() for path in (candidate,current,install_root)):raise ValueError('Update paths cannot be symlinks')
    candidate,current,install_root=map(lambda p:Path(p).resolve(),(candidate,current,install_root))
    if candidate==current or candidate in current.parents or current in candidate.parents:raise ValueError('Choose a separate update bundle')
    if mode not in {'finish','cancel'}:raise ValueError('Choose finish or cancel for active work')
    if verify is None:verify=lambda path:verify_signed_bundle(path,signature_team(current))
    verify(candidate)  # No stopping, backup or directory mutation before trust verification.
    state=install_root/'state';was_running=read_json(state/'local-preferences.json',{}).get('running',True)
    wait_stopped=wait_stopped or (lambda:wait_until_stopped(state))
    lock_path=install_root.parent/('.'+install_root.name+'-update.lock')
    with lock_path.open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Another update is already running') from None
        token_path=install_root.parent/('.'+install_root.name+'-update-token')
        token=secrets.token_urlsafe(32);write_json(token_path,{'token':token,'pid':os.getpid()})
        if command is launch_command:command=lambda bundle,action:launch_command(bundle,action,token,install_root)
        # Staging and backups are siblings, never nested in a directory being copied.
        backup=Path(tempfile.mkdtemp(prefix='sleep-in-update-backup-',dir=install_root.parent));backup.chmod(0o700)
        staged=Path(tempfile.mkdtemp(prefix='.sleep-in-update-',dir=current.parent))/'Sleep In.app'
        progress=install_root.parent/'sleep-in-update-progress.json'
        switched=False;stopped=False
        def report(phase,**extra):write_json(progress,{'phase':phase,'backup':str(backup),'at':time.time(),**extra})
        try:
            report('verifying');shutil.copytree(candidate,staged,symlinks=True);verify(staged)
            required=sum(p.stat().st_size for root in (install_root,current) for p in root.rglob('*') if p.is_file())
            if shutil.disk_usage(backup).free<required*2+50*1024*1024:raise ValueError('Not enough free space for a verified update backup')
            report('stopping');code,detail=command(current,'--stop-'+mode)
            if code or not wait_stopped():raise RuntimeError('Background service did not stop; update was not installed')
            stopped=True;report('backing_up')
            shutil.copytree(install_root,backup/'installation',symlinks=True,ignore=shutil.ignore_patterns('installation.lock','installation-recovery.lock'))
            shutil.copytree(current,backup/'Sleep In.app',symlinks=True)
            if tree_manifest(current)!=tree_manifest(backup/'Sleep In.app'):raise RuntimeError('Application backup verification failed')
            original=tree_manifest(install_root)
            original={k:v for k,v in original.items() if not any(part in {'installation.lock','installation-recovery.lock'} for part in Path(k).parts)}
            if original!=tree_manifest(backup/'installation'):raise RuntimeError('Installation backup verification failed')
            prefs=read_json(backup/'installation/state/local-preferences.json',{});prefs['running']=was_running
            write_json(backup/'installation/state/local-preferences.json',prefs)
            write_json(backup/'manifest.json',{'application':tree_manifest(backup/'Sleep In.app'),'installation':tree_manifest(backup/'installation'),'original_installation':original,'restored_running_preference':was_running})
            report('switching');old=current.with_name('.'+current.name+'-previous-'+str(os.getpid()))
            if old.exists():raise ValueError('A previous update requires review before continuing')
            os.replace(current,old)
            try:os.replace(staged,current)
            except BaseException:os.replace(old,current);raise
            switched=True
            report('checking');code,detail=command(current,'--install-only')
            if code:raise RuntimeError('Updated runtime preparation failed: '+detail)
            prefs=read_json(state/'local-preferences.json',{});prefs['running']=was_running;write_json(state/'local-preferences.json',prefs)
            if was_running:
                code,detail=command(current,'--launch')
                if code:raise RuntimeError('Updated service readiness failed: '+detail)
            # Retain the previous signed bundle with the verified backup for owner recovery.
            report('updated',running=was_running)
            return {'status':'updated','backup':str(backup),'running':was_running}
        except BaseException as exc:
            if not switched:
                # Stop may have succeeded before staging failed: restore the preference
                # and restart the unchanged application if it previously ran.
                if stopped:
                    prefs=read_json(state/'local-preferences.json',{});prefs['running']=was_running;write_json(state/'local-preferences.json',prefs)
                    if was_running:command(current,'--launch')
                report('failed',error=str(exc));raise
            report('rolling_back',error=str(exc))
            code,_=command(current,'--stop-cancel')
            if code or not wait_stopped():
                report('rollback_blocked',error='Updated service could not be stopped. Backup retained.')
                raise RuntimeError('Rollback blocked: stop the updated service before restoring the retained backup') from exc
            failed=current.with_name('.'+current.name+'-failed-'+str(os.getpid()));os.replace(current,failed)
            os.replace(old,current)
            # Keep the failed state for inspection; never merge it into the old schema.
            failed_install=backup/'failed-installation';os.replace(install_root,failed_install)
            shutil.copytree(backup/'installation',install_root,symlinks=True)
            restart_error=None
            if was_running:
                code,detail=command(current,'--launch')
                if code:restart_error=detail
            shutil.rmtree(failed)
            report('rolled_back',error=str(exc),restart_error=restart_error)
            return {'status':'rolled_back','backup':str(backup),'error':str(exc),'restart_error':restart_error}
        finally:
            token_path.unlink(missing_ok=True)
            shutil.rmtree(staged.parent,ignore_errors=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('candidate');parser.add_argument('--current',required=True);parser.add_argument('--install-root',required=True);parser.add_argument('--mode',choices=['finish','cancel'],default='finish');args=parser.parse_args()
    try:
        result=update_bundle(args.candidate,args.current,args.install_root,mode=args.mode);print(json.dumps(result));return 0 if result['status']=='updated' else 2
    except Exception as exc:print(json.dumps({'status':'failed','error':str(exc)}));return 1

if __name__=='__main__':raise SystemExit(main())
