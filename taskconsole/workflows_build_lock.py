"""Crash-released advisory build leases, inherited by mutating child processes."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import fcntl
import re
from .store import stamp

_fds=ContextVar('runtime_build_lock_fds',default=())

class BuildBusyError(ValueError):pass


@contextmanager
def build_lock(root,category,key,inherit=True):
    if not re.fullmatch(r'[A-Za-z0-9_-]+',category) or not re.fullmatch(r'[A-Za-z0-9_-]+',key):raise ValueError('Invalid build identity')
    directory=Path(root)/'runtime-build-locks';directory.mkdir(exist_ok=True,mode=0o700)
    handle=(directory/(category+'-'+key+'.lock')).open('a+')
    token=None
    try:
        try:fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:raise BuildBusyError('A build or installation is already in progress') from exc
        if inherit:token=_fds.set((*_fds.get(),handle.fileno()))
        yield
    finally:
        if token is not None:_fds.reset(token)
        # Do not LOCK_UN: a surviving child must retain the shared descriptor lease.
        handle.close()


def subprocess_lock_options():
    return {'pass_fds':_fds.get()} if _fds.get() else {}


def recover_build_record(store,kind,record,category,active_status):
    if record.get('status')!=active_status:return record
    try:
        with build_lock(store.path,category,record['id'],inherit=False):
            with store.transaction() as tx:
                current=tx.get(kind,record['id'])
                if current and current.get('status')==active_status:
                    if current.get('installer')=='system_dialog' and current.get('phase')=='system_consent' and current.get('requested_at'):
                        current.update(status='pending',recovery_required=False,reason='System installation request was interrupted; inspect the macOS dialog and check installation again')
                    else:
                        current.update(status='failed',phase='interrupted',recovery_required=True,reason='Build process was interrupted; explicit retry is available',finished_at=stamp())
                    tx.put(kind,current)
                return current or record
    except BuildBusyError:return record
