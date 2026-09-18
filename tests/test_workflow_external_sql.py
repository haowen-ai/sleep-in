"""Real external receivers. Opt-in, isolated synthetic tables; never mocked drivers.

Enable SLEEP_IN_EXTERNAL_SQL_TESTS=1 after starting deploy/sql-matrix-compose.yaml.
Optionally select SLEEP_IN_TEST_SQL_DIALECTS=postgresql,mysql,oracle.
"""
import copy
import json
import os
import time
import uuid
from decimal import Decimal
import pytest
from taskconsole.store import Store
from taskconsole.workflows_sql import create_connection,connect,execute_sql,sql_text

DIALECTS=['postgresql','mysql','oracle']
PRECISE='12345678901234567890.1234567890'
BIG='9007199254740993'


def fixture_config(dialect):
    override=os.environ.get('SLEEP_IN_TEST_SQL_'+dialect.upper())
    if override:return json.loads(override)
    if dialect=='postgresql':return {'host':'127.0.0.1','port':15432,'dbname':'sleepin','user':'sleepin','password':'sleepin_fixture_password','connect_timeout':10}
    if dialect=='mysql':return {'host':'127.0.0.1','port':13306,'database':'sleepin','user':'sleepin','password':'sleepin_fixture_password','charset':'utf8mb4','connect_timeout':10,'read_timeout':15,'write_timeout':15}
    return {'dsn':'127.0.0.1:11521/FREEPDB1','user':'sleepin','password':'sleepin_fixture_password'}


def wait_for_oracle_fixture_ddl(db,table):
    """Finish fresh-DDL setup before starting a separate read-only snapshot.

    Oracle ORA-01466 documents an object-change/snapshot timestamp conflict.
    LAST_DDL_TIME is a DATE (whole seconds), so cross a complete database-clock
    second after it. This is setup only: no application query/write is retried,
    no error is swallowed, and SET TRANSACTION READ ONLY stays enforced.
    See https://docs.oracle.com/en/error-help/db/ora-01466/ and
    https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/ALL_OBJECTS.html
    """
    deadline=time.monotonic()+5
    with db.cursor() as cursor:
        while time.monotonic()<deadline:
            cursor.execute("SELECT CASE WHEN SYSDATE > LAST_DDL_TIME + 1/86400 THEN 1 ELSE 0 END FROM USER_OBJECTS WHERE OBJECT_NAME=:name AND OBJECT_TYPE='TABLE'",{'name':table.upper()})
            row=cursor.fetchone()
            if row and row[0]==1:return
            time.sleep(.05)
    raise TimeoutError('Oracle fixture DDL timestamp did not settle within 5 seconds')


@pytest.fixture(params=DIALECTS)
def receiver(request,tmp_path):
    dialect=request.param
    if os.environ.get('SLEEP_IN_EXTERNAL_SQL_TESTS')!='1':pytest.skip('Explicit external SQL fixture opt-in required')
    if dialect not in os.environ.get('SLEEP_IN_TEST_SQL_DIALECTS',','.join(DIALECTS)).split(','):pytest.skip('Dialect excluded by fixture selection')
    store=Store(tmp_path,'sqlite:///'+str(tmp_path/'state.sqlite'))
    public=create_connection(store,{'name':'Isolated '+dialect,'dialect':dialect,'config':fixture_config(dialect),'write_enabled':True})
    with store.transaction() as tx:connection=tx.get('connection',public['id'])
    table='si_test_'+uuid.uuid4().hex[:12]
    db=connect(store,connection,True)
    try:
        with db.cursor() as cursor:
            numeric='NUMBER' if dialect=='oracle' else 'DECIMAL'
            text='VARCHAR2(100 CHAR)' if dialect=='oracle' else 'VARCHAR(100)'
            cursor.execute(f'CREATE TABLE {table} (order_id VARCHAR(30) PRIMARY KEY, amount {numeric}(30,10), big_value {numeric}(30,0), region {text})')
            insert=f'INSERT INTO {table}(order_id,amount,big_value,region) VALUES(:id,:amount,:big,:region)'
            for bindings in [{'id':'A001','amount':PRECISE,'big':BIG,'region':'华东 🚀'},{'id':'A002','amount':'0.0000000001','big':'0','region':None}]:cursor.execute(sql_text(insert,dialect),bindings)
        db.commit()
        if dialect=='oracle':wait_for_oracle_fixture_ddl(db,table)
        node={'id':'receiver','kind':'sql','source':f'SELECT order_id AS "order_id", amount AS "amount", big_value AS "big_value", region AS "region" FROM {table} ORDER BY order_id','config':{'dialect':dialect,'connection_id':public['id'],'mode':'query','timeout':2}}
        yield store,node,table,connection
    finally:
        db.rollback()
        with db.cursor() as cursor:cursor.execute('DROP TABLE '+table)
        db.commit();db.close();store.engine.dispose()


def run(receiver,source=None,inputs=None,config=None,outputs=None):
    store,node,_,_=receiver;node=copy.deepcopy(node)
    if source is not None:node['source']=source
    node['config'].update(config or {})
    if outputs is not None:node['outputs']=outputs
    return execute_sql(store,node,inputs or {},'sql-matrix')


def test_external_null_unicode_decimal_and_unsafe_integer(receiver):
    data=run(receiver)['output']['data'];assert data['rowCount']==2
    first,second=data['rows']
    assert first['order_id']=='A001' and first['region']=='华东 🚀'
    assert isinstance(first['amount'],str) and Decimal(first['amount'])==Decimal(PRECISE)
    assert first['big_value']==BIG
    assert second['region'] is None and Decimal(second['amount'])==Decimal('0.0000000001')
    assert next(c for c in data['columns'] if c['name']=='region')['nullable'] is True


def test_external_real_bound_inputs_and_injection(receiver):
    _,node,table,_=receiver
    source=f'SELECT order_id AS "order_id", region AS "region" FROM {table} WHERE order_id=:id'
    assert run(receiver,source,{'id':'A001'})['output']['data']['rows']==[{'order_id':'A001','region':'华东 🚀'}]
    assert run(receiver,source,{'id':"A001' OR 1=1 --"})['output']['data']['rows']==[]
    # Placeholder-looking text inside literals/comments must remain text.
    suffix=' FROM dual' if node['config']['dialect']=='oracle' else ''
    source="SELECT ':literal' AS \"literal\", :value AS \"value\""+suffix+' /* :comment */'
    assert run(receiver,source,{'value':'安全'})['output']['data']['rows']==[{'literal':':literal','value':'安全'}]


def test_external_query_mode_is_database_read_only(receiver):
    table=receiver[2]
    with pytest.raises(Exception):run(receiver,f"UPDATE {table} SET region='changed' WHERE order_id='A001'")
    assert run(receiver)['output']['data']['rows'][0]['region']=='华东 🚀'


def test_external_write_commit_and_failed_second_statement_rollback(receiver):
    table=receiver[2]
    committed=run(receiver,f'UPDATE {table} SET region=:region WHERE order_id=:id',{'region':'committed','id':'A001'},{'mode':'write'})
    assert committed['output']['data']['affectedRows']==1
    assert run(receiver)['output']['data']['rows'][0]['region']=='committed'
    statements=[{'sql':f'UPDATE {table} SET region=:region WHERE order_id=:id','bindings':{'region':'must-roll-back','id':'A001'}},{'sql':f'INSERT INTO {table}(order_id) VALUES(:id)','bindings':{'id':'A001'}}]
    with pytest.raises(Exception):run(receiver,config={'mode':'write','statements':statements})
    assert run(receiver)['output']['data']['rows'][0]['region']=='committed'


def test_external_output_validation_failure_rolls_back(receiver):
    table=receiver[2]
    with pytest.raises(ValueError,match='missing required'):
        run(receiver,f'UPDATE {table} SET region=:region WHERE order_id=:id',{'region':'invalid-output','id':'A001'},{'mode':'write'},outputs={'type':'object','required':['missing']})
    assert run(receiver)['output']['data']['rows'][0]['region']=='华东 🚀'


def test_external_connection_write_permission_is_enforced(receiver):
    store,_,table,connection=receiver
    with store.transaction() as tx:connection['write_enabled']=False;tx.put('connection',connection)
    with pytest.raises(ValueError,match='permit writes'):run(receiver,'DELETE FROM '+table,config={'mode':'write'})
    assert run(receiver)['output']['data']['rowCount']==2


def test_external_real_timeout_is_bounded(receiver):
    dialect=receiver[1]['config']['dialect']
    source={'postgresql':'SELECT pg_sleep(8)','mysql':'SELECT SLEEP(8)','oracle':'BEGIN DBMS_SESSION.SLEEP(8); END;'}[dialect]
    started=time.monotonic()
    with pytest.raises(Exception):run(receiver,source,config={'timeout':1})
    elapsed=time.monotonic()-started
    assert .3<=elapsed<5,('receiver did not enforce its configured deadline',dialect,elapsed)
    assert run(receiver)['output']['data']['rowCount']==2


def test_external_null_bind_and_empty_string_dialect_semantics(receiver):
    table=receiver[2];dialect=receiver[1]['config']['dialect']
    run(receiver,f'UPDATE {table} SET region=:region WHERE order_id=:id',{'region':None,'id':'A001'},{'mode':'write'})
    assert run(receiver)['output']['data']['rows'][0]['region'] is None
    run(receiver,f'UPDATE {table} SET region=:region WHERE order_id=:id',{'region':'','id':'A001'},{'mode':'write'})
    assert run(receiver)['output']['data']['rows'][0]['region']==(None if dialect=='oracle' else '')


@pytest.mark.parametrize('dialect',['postgresql','mysql','oracle'])
def test_driver_deadline_configuration_contract(tmp_path,monkeypatch,dialect):
    """API-level RED/GREEN check; distinct from the opt-in real DB matrix above."""
    import sys,types
    import taskconsole.workflows_sql as sql
    events=[]
    class Cursor:
        description=None;rowcount=0;arraysize=100
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,*args,**kwargs):events.append(('execute',args,kwargs))
        def close(self):pass
        def var(self,*args,**kwargs):return kwargs
    class DB:
        call_timeout=None;outputtypehandler=None
        def cursor(self):return Cursor()
        def execute(self,*args):events.append(('execute',args,{}))
        def begin(self):pass
        def rollback(self):pass
        def commit(self):pass
        def close(self):pass
    db=DB()
    module=types.SimpleNamespace(connect=lambda **config:(events.append(('connect',config)) or db),DB_TYPE_NUMBER='number',DB_TYPE_VARCHAR='varchar')
    monkeypatch.setitem(sys.modules,{'postgresql':'psycopg','mysql':'pymysql','oracle':'oracledb'}[dialect],module)
    monkeypatch.setattr(sql,'driver_status',lambda value:True)
    store=Store(tmp_path,'sqlite:///'+str(tmp_path/'state.sqlite'))
    try:
        connection=create_connection(store,{'dialect':dialect,'config':{'user':'fixture'},'write_enabled':True})
        execute_sql(store,{'kind':'sql','source':'SELECT 1','config':{'dialect':dialect,'connection_id':connection['id'],'timeout':2}},{},'fixture')
        if dialect=='postgresql':assert any('statement_timeout' in str(event) and ('2000' in str(event) or "'2s'" in str(event)) for event in events)
        elif dialect=='mysql':assert next(e[1] for e in events if e[0]=='connect')['read_timeout']<=2
        else:
            assert db.call_timeout==2000
            assert callable(db.outputtypehandler)
            variable=db.outputtypehandler(Cursor(),types.SimpleNamespace(type_code='number',scale=10))
            assert variable['outconverter']('12345678901234567890.1234567890')==Decimal(PRECISE)
    finally:store.engine.dispose()


def test_oracle_fixture_ddl_barrier_uses_database_clock(monkeypatch):
    events=[]
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,query,bindings):events.append((query,bindings))
        def fetchone(self):return (1 if len(events)>=3 else 0,)
    class DB:
        def cursor(self):return Cursor()
    sleeps=[]
    monkeypatch.setattr(time,'sleep',sleeps.append)
    wait_for_oracle_fixture_ddl(DB(),'si_test_example')
    assert len(events)==3 and len(sleeps)==2
    assert all('SYSDATE > LAST_DDL_TIME' in query and 'USER_OBJECTS' in query for query,_ in events)
    assert all(bindings=={'name':'SI_TEST_EXAMPLE'} for _,bindings in events)


def test_oracle_fixture_ddl_barrier_is_bounded(monkeypatch):
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,*args):pass
        def fetchone(self):return (0,)
    class DB:
        def cursor(self):return Cursor()
    ticks=iter([0,0,6])
    monkeypatch.setattr(time,'monotonic',lambda:next(ticks))
    monkeypatch.setattr(time,'sleep',lambda _:None)
    with pytest.raises(TimeoutError,match='Oracle fixture DDL'):
        wait_for_oracle_fixture_ddl(DB(),'si_test_example')
