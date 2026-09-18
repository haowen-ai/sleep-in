"""SQL input binding and atomicity oracles, using isolated synthetic SQLite."""
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from taskconsole.store import Store
from taskconsole.workflows_sql import create_connection, execute_sql, sql_text, value_json


@pytest.fixture
def receiver(tmp_path):
    store = Store(tmp_path, 'sqlite:///' + str(tmp_path / 'state.sqlite'))
    connection = create_connection(store, {'name': 'Contract fixture', 'dialect': 'sqlite',
        'config': {'synthetic': True}, 'write_enabled': True})
    node = {'id': 'sql', 'kind': 'sql', 'source': 'SELECT * FROM orders ORDER BY order_id',
        'config': {'dialect': 'sqlite', 'connection_id': connection['id'], 'mode': 'query'}}
    yield store, node
    store.engine.dispose()


def test_query_preserves_rows_null_and_decimal_text(receiver):
    store, node = receiver
    result = execute_sql(store, node, {}, 'wf')
    data = result['output']['data']
    assert data['rowCount'] == 3
    assert data['rows'][1] == {'order_id': 'A002', 'amount': '20.25', 'region': None}
    assert 'affectedRows' not in data


def test_bound_input_cannot_change_query(receiver):
    store, node = receiver
    node['source'] = 'SELECT * FROM orders WHERE order_id = :id'
    assert execute_sql(store, node, {'id': "A001' OR 1=1 --"}, 'wf')['output']['data']['rows'] == []
    assert execute_sql(store, node, {'id': 'A001'}, 'wf')['output']['data']['rowCount'] == 1


def test_failed_second_write_rolls_back_first(receiver):
    store, node = receiver
    writer = {**node, 'config': {**node['config'], 'mode': 'write', 'statements': [
        {'sql': 'UPDATE orders SET amount = :amount WHERE order_id = :id',
         'bindings': {'amount': '999.00', 'id': 'A001'}},
        {'sql': 'INSERT INTO orders(order_id,amount) VALUES(:id,:amount)',
         'bindings': {'amount': '1.00', 'id': 'A001'}}]}}
    with pytest.raises(Exception):
        execute_sql(store, writer, {}, 'wf')
    assert execute_sql(store, node, {}, 'wf')['output']['data']['rows'][0]['amount'] == '10.50'


def test_query_mode_rejects_writes_and_attaching_host_file(receiver):
    store, node = receiver
    for source in ['DELETE FROM orders', "ATTACH DATABASE ':memory:' AS outside"]:
        with pytest.raises(Exception):
            execute_sql(store, {**node, 'source': source}, {}, 'wf')
    assert execute_sql(store, node, {}, 'wf')['output']['data']['rowCount'] == 3


@pytest.mark.parametrize('invalid_output', ['schema', 'quota'])
def test_write_output_failure_rolls_back_transaction(receiver, invalid_output):
    store, node = receiver
    writer = {**node, 'source': 'UPDATE orders SET amount = :amount WHERE order_id = :id RETURNING order_id,amount',
        'config': {**node['config'], 'mode': 'write'}}
    amount = '999.00'
    if invalid_output == 'schema':
        writer['outputs'] = {'type': 'object', 'required': ['missing_field']}
    else:
        writer['config']['max_output_bytes'] = 1024 * 1024
        amount = '9' * (1024 * 1024 + 1)
    with pytest.raises(ValueError):
        execute_sql(store, writer, {'amount': amount, 'id': 'A001'}, 'wf')
    assert execute_sql(store, node, {}, 'wf')['output']['data']['rows'][0]['amount'] == '10.50'


@pytest.mark.parametrize('dialect', ['postgresql', 'mysql'])
def test_placeholder_translation_preserves_quoted_text_comments_and_cast(dialect):
    source = "SELECT ':literal', \"colon:key\", :value -- :comment\n/* :block */ WHERE :other = 2"
    expected = "SELECT ':literal', \"colon:key\", %(value)s -- :comment\n/* :block */ WHERE %(other)s = 2"
    assert sql_text(source, dialect) == expected
    if dialect == 'postgresql':
        assert sql_text('SELECT :value::integer, $$:literal$$, $body$:more$body$', dialect) == (
            'SELECT %(value)s::integer, $$:literal$$, $body$:more$body$')


def test_numeric_precision_and_timezone_values_are_explicit():
    assert value_json(Decimal('9999999999999999.123456')) == '9999999999999999.123456'
    assert value_json(9007199254740993) == '9007199254740993'
    assert value_json(datetime(2026, 9, 17, 7, tzinfo=timezone.utc)).endswith('+00:00')
    # Never quietly present a database timestamp with no timezone as a known instant.
    with pytest.raises(ValueError, match='timezone'):
        value_json(datetime(2026, 9, 17, 7))
