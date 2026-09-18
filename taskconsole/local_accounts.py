"""Public initial credentials, strictly for a pristine loopback local edition."""
import os
from .auth import password_hash, password_valid
from .store import stamp, uid

USERNAME = 'admin'
PASSWORD = 'sleepin123456'
MARKER = 'local-initial-account'


def local_mode():
    if os.environ.get('SLEEP_IN_LOCAL') != '1':
        return False
    if os.environ.get('APP_HOST', '127.0.0.1') not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('The local edition must listen on loopback only')
    return True


def initialize_local_account(store):
    """Never reset or add an account to existing, migrated or restored data."""
    if not local_mode():
        return False
    with store.transaction() as tx:
        if tx.get('meta', MARKER) or any(tx.all(kind) for kind in
            ('user', 'script', 'version', 'task', 'execution', 'workflow', 'workflow_version', 'workflow_run', 'connection')):
            return False
        hashed = password_hash(PASSWORD)
        user = {'id':uid(), 'username':USERNAME, 'password':hashed, 'role':'admin',
                'locale':'en', 'enabled':True, 'created_at':stamp()}
        tx.put('user', user)
        tx.put('meta', {'id':MARKER, 'user_id':user['id'], 'initial_hash':hashed})
        return True


def has_public_default(tx):
    marker = tx.get('meta', MARKER)
    if not marker:
        return False
    user = tx.get('user', marker['user_id'])
    return bool(user and (user.get('password') == marker['initial_hash'] or
                          password_valid(PASSWORD,user.get('password',''))))


def local_account_hint(store):
    if not local_mode():
        return None
    with store.transaction() as tx:
        if not has_public_default(tx): return None
        marker=tx.get('meta',MARKER);user=tx.get('user',marker['user_id'])
        return {'username':user['username'], 'password':PASSWORD} if user.get('enabled') else None
