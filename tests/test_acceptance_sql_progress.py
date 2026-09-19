"""Real SQLite VM work with a real state-store cancellation probe."""
import math
import time
import pytest
from taskconsole.store import Store
from taskconsole.workflows_sql import create_connection, execute_sql


@pytest.fixture
def database(tmp_path):
    store = Store(tmp_path, 'sqlite:///' + str(tmp_path / 'state.sqlite'))
    connection = create_connection(store, {'name': 'Progress probe', 'dialect': 'sqlite', 'config': {}, 'write_enabled': True})
    try:
        yield store, connection
    finally:
        store.engine.dispose()


def test_sqlite_progress_throttles_real_store_probe_without_slowing_query(database, request):
    store, connection = database
    query = 'WITH RECURSIVE slow(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM slow WHERE n<200000) SELECT max(n) AS sentinel FROM slow'
    node = {'kind': 'sql', 'source': query, 'config': {'dialect': 'sqlite', 'connection_id': connection['id'], 'timeout': 20}}
    started = time.monotonic()
    baseline = execute_sql(store, node, {}, 'profile')
    baseline_seconds = time.monotonic() - started
    probes = []
    def cancelled():
        probes.append(time.monotonic())
        with store.transaction() as tx:
            assert tx.get('meta', 'settings') is not None
        return False
    started = time.monotonic()
    result = execute_sql(store, node, {}, 'profile', cancelled)
    elapsed = time.monotonic() - started
    request.node.user_properties.extend([('unobserved_seconds', baseline_seconds), ('store_observed_seconds', elapsed), ('store_probe_count', len(probes))])
    assert result['output']['data']['rows'] == baseline['output']['data']['rows'] == [{'sentinel': 200000}]
    # Initial/final probes are immediate; VM callbacks may consult the store
    # at most once per 50ms. This bound is independent of machine speed.
    assert len(probes) <= math.ceil(elapsed / .05) + 3, (len(probes), elapsed, baseline_seconds)


def test_sqlite_throttled_probe_still_cancels_promptly_and_preserves_deadline(database):
    store, connection = database
    query = 'WITH RECURSIVE slow(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM slow WHERE n<200000000) SELECT max(n) AS sentinel FROM slow'
    node = {'kind': 'sql', 'source': query, 'config': {'dialect': 'sqlite', 'connection_id': connection['id'], 'timeout': 1}}
    started = time.monotonic()
    result = execute_sql(store, node, {}, 'cancel', lambda: time.monotonic() - started >= .12)
    elapsed = time.monotonic() - started
    assert result['status'] == 'cancelled' and .12 <= elapsed < .6
    started = time.monotonic()
    result = execute_sql(store, node, {}, 'timeout', lambda: False)
    elapsed = time.monotonic() - started
    assert result['status'] == 'timed_out' and .9 <= elapsed < 2
