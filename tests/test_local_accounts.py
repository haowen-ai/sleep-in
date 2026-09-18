import pytest
from taskconsole.store import Store
from taskconsole.auth import password_hash, password_valid


def store_at(tmp_path):
    return Store(tmp_path, f'sqlite:///{tmp_path}/console.db')


def test_public_account_requires_explicit_loopback_local_mode(tmp_path, monkeypatch):
    from taskconsole.local_accounts import initialize_local_account
    store = store_at(tmp_path)
    monkeypatch.delenv('SLEEP_IN_LOCAL', raising=False)
    assert not initialize_local_account(store)
    monkeypatch.setenv('SLEEP_IN_LOCAL', '1')
    monkeypatch.setenv('APP_HOST', '0.0.0.0')
    with pytest.raises(ValueError): initialize_local_account(store)
    with store.transaction() as tx: assert not tx.all('user')


def test_fresh_account_hint_disappears_after_password_changes(tmp_path, monkeypatch):
    from taskconsole.local_accounts import initialize_local_account, local_account_hint
    monkeypatch.setenv('SLEEP_IN_LOCAL', '1')
    monkeypatch.setenv('APP_HOST', '127.0.0.1')
    store = store_at(tmp_path)
    assert initialize_local_account(store)
    assert local_account_hint(store) == {'username':'admin', 'password':'sleepin123456'}
    with store.transaction() as tx:
        user = tx.all('user')[0]
        assert password_valid('sleepin123456', user['password'])
        user['password'] = password_hash('something-private-123')
        tx.put('user', user)
    assert local_account_hint(store) is None
    assert not initialize_local_account(store)
    with store.transaction() as tx: assert password_valid('something-private-123', tx.all('user')[0]['password'])


@pytest.mark.parametrize('kind', ['user', 'script', 'version', 'workflow', 'task', 'connection'])
def test_existing_or_restored_state_never_gets_extra_account(tmp_path, monkeypatch, kind):
    from taskconsole.local_accounts import initialize_local_account
    monkeypatch.setenv('SLEEP_IN_LOCAL','1')
    monkeypatch.setenv('APP_HOST','127.0.0.1')
    store = store_at(tmp_path)
    with store.transaction() as tx: tx.put(kind, {'id':'existing'})
    assert not initialize_local_account(store)
    with store.transaction() as tx: assert len(tx.all('user')) == (1 if kind == 'user' else 0)


def test_disabled_initial_user_hides_hint_but_still_blocks_external_reuse(tmp_path,monkeypatch):
    from taskconsole.local_accounts import initialize_local_account,local_account_hint,has_public_default
    monkeypatch.setenv('SLEEP_IN_LOCAL','1');monkeypatch.setenv('APP_HOST','127.0.0.1')
    store=store_at(tmp_path);initialize_local_account(store)
    with store.transaction() as tx:
        user=tx.all('user')[0];user['enabled']=False;tx.put('user',user)
    assert local_account_hint(store) is None
    with store.transaction() as tx:assert has_public_default(tx)


def test_rehashing_same_public_password_does_not_remove_default_guard(tmp_path,monkeypatch):
    from taskconsole.local_accounts import initialize_local_account,local_account_hint,has_public_default
    monkeypatch.setenv('SLEEP_IN_LOCAL','1');monkeypatch.setenv('APP_HOST','127.0.0.1')
    store=store_at(tmp_path);initialize_local_account(store)
    with store.transaction() as tx:
        user=tx.all('user')[0]
        user['password']=password_hash('sleepin123456')
        tx.put('user',user)
        assert has_public_default(tx)
    assert local_account_hint(store)=={'username':'admin','password':'sleepin123456'}
