"""Database receivers: managed SQLite plus explicitly configured optional drivers."""
import importlib.util
import json
import sqlite3
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from .store import uid, stamp
from .workflows_runtime import check_schema, portable

DIALECTS={'sqlite','postgresql','mysql','oracle'}


def driver_status(dialect):
    module={'sqlite':'sqlite3','postgresql':'psycopg','mysql':'pymysql','oracle':'oracledb'}.get(dialect)
    return bool(module and importlib.util.find_spec(module))


def public_connection(value):
    return {k:v for k,v in value.items() if k not in {'encrypted_config','config'}}


def create_connection(store,data):
    dialect=data.get('dialect','sqlite')
    if dialect not in DIALECTS: raise ValueError('Unsupported database dialect')
    config=data.get('config',{})
    if not isinstance(config,dict): raise ValueError('Connection config must be an object')
    if dialect=='sqlite' and any(k in config for k in ('path','file','database','url')): raise ValueError('SQLite uses managed storage, not host paths')
    connection={'id':uid(),'name':str(data.get('name','Connection'))[:200],'dialect':dialect,'write_enabled':bool(data.get('write_enabled',False)),'allowed_workflows':data.get('allowed_workflows',[]),'revision':uid(),'created_at':stamp(),'status':'untested','driver_available':driver_status(dialect),'encrypted_config':store.fernet.encrypt(json.dumps(config).encode()).decode()}
    if dialect=='sqlite':
        folder=store.path/'workflow-connections';folder.mkdir(exist_ok=True)
        with sqlite3.connect(folder/(connection['id']+'.sqlite')) as db:
            if config.get('synthetic'):
                db.execute('CREATE TABLE orders(order_id TEXT PRIMARY KEY NOT NULL, amount TEXT NOT NULL, region TEXT)')
                db.executemany('INSERT INTO orders VALUES(?,?,?)',[('A001','10.50','华东'),('A002','20.25',None),('A003','0.00','西部')])
    with store.transaction() as tx:tx.put('connection',connection)
    return public_connection(connection)


def update_connection(store,cid,data):
    """Rotate authorized driver configuration without exposing or resetting secrets."""
    import copy
    from .workflows import WorkflowError
    if not isinstance(data,dict):raise ValueError('Connection update must be an object')
    patch=data.get('config',{})
    if not isinstance(patch,dict):raise ValueError('Connection config must be an object')
    def merge(old,new):
        result=copy.deepcopy(old)
        for key,value in new.items():
            if value is None or value=='':continue
            result[key]=merge(result.get(key,{}) if isinstance(result.get(key),dict) else {},value) if isinstance(value,dict) else copy.deepcopy(value)
        return result
    with store.transaction() as tx:
        record=tx.get('connection',cid)
        if not record:raise WorkflowError('Connection not found','not_found')
        if data.get('dialect',record['dialect'])!=record['dialect']:raise ValueError('Connection dialect is immutable; create another connection')
        if record['dialect']=='sqlite' and any(k in patch for k in ('path','file','database','url')):raise ValueError('SQLite uses managed storage, not host paths')
        config=merge(json.loads(store.fernet.decrypt(record['encrypted_config'].encode())),patch)
        if 'name' in data:
            if not isinstance(data['name'],str) or not data['name'].strip():raise ValueError('Connection name is required')
            record['name']=data['name'].strip()[:200]
        if 'write_enabled' in data:
            if type(data['write_enabled']) is not bool:raise ValueError('write_enabled must be boolean')
            record['write_enabled']=data['write_enabled']
        if 'allowed_workflows' in data:
            allowed=data['allowed_workflows']
            if not isinstance(allowed,list) or any(not isinstance(w,str) or not w for w in allowed):raise ValueError('Allowed workflows must be an ID array')
            record['allowed_workflows']=list(dict.fromkeys(allowed))
        record.update(encrypted_config=store.fernet.encrypt(json.dumps(config,allow_nan=False).encode()).decode(),revision=uid(),status='untested',updated_at=stamp())
        record.pop('last_test_at',None);tx.put('connection',record)
    return public_connection(record)


def connect(store,connection,write=False,timeout=None):
    config=json.loads(store.fernet.decrypt(connection['encrypted_config'].encode()))
    dialect=connection['dialect']
    if write and not connection.get('write_enabled'): raise ValueError('Connection does not permit writes')
    if not driver_status(dialect): raise ValueError(f'Install the {dialect} driver and test this connection')
    if dialect=='sqlite':
        path=store.path/'workflow-connections'/(connection['id']+'.sqlite')
        db=sqlite3.connect(f'file:{path}?mode={"rw" if write else "ro"}',uri=True,timeout=5)
        if not write:db.execute('PRAGMA query_only=ON')
        forbidden={sqlite3.SQLITE_ATTACH,sqlite3.SQLITE_DETACH}
        db.set_authorizer(lambda action,*args:sqlite3.SQLITE_DENY if action in forbidden else sqlite3.SQLITE_OK)
        return db
    if dialect=='postgresql':
        import psycopg
        if timeout is not None:config['connect_timeout']=min(config.get('connect_timeout',timeout),timeout)
        db=psycopg.connect(**config)
        if not write:db.execute('SET TRANSACTION READ ONLY')
        if timeout is not None:db.execute("SELECT set_config('statement_timeout', %s, true)",(str(timeout*1000),))
        return db
    if dialect=='mysql':
        import pymysql
        if timeout is not None:
            for key in ('connect_timeout','read_timeout','write_timeout'):config[key]=min(config.get(key) or timeout,timeout)
        db=pymysql.connect(**config)
        if not write:
            with db.cursor() as cur:cur.execute('SET TRANSACTION READ ONLY')
        db.begin();return db
    import oracledb
    db=oracledb.connect(**config)
    if timeout is not None:db.call_timeout=timeout*1000
    def number_handler(cursor,metadata):
        if metadata.type_code==oracledb.DB_TYPE_NUMBER:
            return cursor.var(oracledb.DB_TYPE_VARCHAR,arraysize=cursor.arraysize,outconverter=Decimal)
    db.outputtypehandler=number_handler
    if not write:
        with db.cursor() as cur:cur.execute('SET TRANSACTION READ ONLY')
    return db


def value_json(value):
    if isinstance(value,Decimal):return str(value)
    if isinstance(value,datetime) and value.utcoffset() is None:
        raise ValueError('Timestamp requires an explicit timezone; project a timezone-qualified value in SQL')
    if isinstance(value,(datetime,date)):return value.isoformat()
    if type(value) is int and abs(value)>9007199254740991:return str(value)
    if isinstance(value,bytes):raise ValueError('Binary columns require an explicit encoded SQL projection')
    return value


def sql_text(source,dialect):
    if dialect not in {'postgresql','mysql'}:return source
    # DB-API drivers parse percent placeholders even inside SQL string literals.
    # Escape original percent characters, then translate only executable :names.
    pieces=[];index=0;length=len(source)
    def literal(text):pieces.append(text.replace('%','%%'))
    while index<length:
        start=index;char=source[index]
        if source.startswith('--',index) or (dialect=='mysql' and char=='#'):
            end=source.find('\n',index)
            index=length if end<0 else end
        elif source.startswith('/*',index):
            index+=2;depth=1
            while index<length and depth:
                if source.startswith('/*',index):depth+=1;index+=2
                elif source.startswith('*/',index):depth-=1;index+=2
                else:index+=1
        elif char in "'\"`":
            index+=1
            while index<length:
                if source[index]=='\\' and (dialect=='mysql' or (char=="'" and start>0 and source[start-1] in 'eE')):
                    index+=2
                elif source[index]==char:
                    index+=1
                    if index<length and source[index]==char:index+=1
                    else:break
                else:index+=1
        elif dialect=='postgresql' and char=='$' and (tag:=re.match(r'\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$',source[index:])):
            marker=tag.group();end=source.find(marker,index+len(marker))
            index=length if end<0 else end+len(marker)
        elif char==':' and (index==0 or source[index-1]!=':') and (name:=re.match(r':([A-Za-z_][A-Za-z0-9_]*)',source[index:])):
            pieces.append('%('+name.group(1)+')s');index+=len(name.group());continue
        else:index+=1
        literal(source[start:index])
    return ''.join(pieces)


def execute_sql(store,node,inputs,workflow_id,cancelled=lambda:False):
    config=node.get('config',{});cid=config.get('connection_id')
    with store.transaction() as tx:connection=tx.get('connection',cid)
    if not connection:raise ValueError('Choose an available database connection')
    if connection['dialect']!=config.get('dialect','sqlite'):raise ValueError('Connection dialect does not match SQL receiver')
    if connection.get('allowed_workflows') and workflow_id not in connection['allowed_workflows']:raise ValueError('Connection not authorized for this workflow')
    write=config.get('mode','query')=='write'
    statements=config.get('statements') if write else None
    if statements is not None and (not isinstance(statements,list) or not statements):raise ValueError('statements must be a nonempty list')
    statements=statements or [{'sql':node.get('source',''),'bindings':inputs}]
    quota=config.get('max_output_bytes',100*1024*1024)
    if type(quota) is not int or not 1<=quota<=100*1024*1024:raise ValueError('SQL dataset quota must be 1..104857600 bytes')
    timeout=config.get('timeout',30)
    if type(timeout) is not int or not 1<=timeout<=3600:raise ValueError('SQL timeout must be 1..3600 seconds')
    db=connect(store,connection,write,timeout=timeout)
    try:
        if connection['dialect']=='sqlite':
            import time
            deadline=time.monotonic()+int(config.get('timeout',30))
            db.set_progress_handler(lambda:int(cancelled() or time.monotonic()>deadline),1000)
        rows=[];columns=[];affected=0
        for statement in statements:
            if cancelled():raise ValueError('cancelled')
            if not isinstance(statement,dict) or not isinstance(statement.get('sql'),str):raise ValueError('Statement requires SQL and bound parameters')
            bindings=statement.get('bindings',inputs)
            if not isinstance(bindings,dict):raise ValueError('SQL bindings must be an object')
            # Statement bindings may explicitly name input fields, never interpolate SQL text.
            bindings={key:inputs[value['input']] if isinstance(value,dict) and set(value)=={'input'} else value for key,value in bindings.items()}
            cur=db.cursor()
            try:
                cur.execute(sql_text(statement['sql'],connection['dialect']),bindings)
                if cur.description:
                    names=[x[0] for x in cur.description]
                    if len(names)!=len(set(names)):raise ValueError('Duplicate SQL column names; add explicit aliases')
                    rows=[];result_bytes=0
                    while batch:=cur.fetchmany(100):
                        for row in batch:
                            item=dict(zip(names,[value_json(v) for v in row]))
                            result_bytes+=len(json.dumps(item,ensure_ascii=False,allow_nan=False).encode())+1
                            if result_bytes>quota:raise ValueError('SQL output exceeds dataset quota')
                            rows.append(item)
                    columns=[{'name':name,'type':next(('boolean' if isinstance(r[name],bool) else 'integer' if isinstance(r[name],int) else 'number' if isinstance(r[name],float) else 'string' for r in rows if r[name] is not None),'string'),'nullable':any(r[name] is None for r in rows)} for name in names]
                if write:affected=None if affected is None or cur.rowcount<0 else affected+cur.rowcount
            finally:cur.close()
        data={'rows':rows,'columns':columns,'rowCount':len(rows)}
        if write:data['affectedRows']=affected
        if len(json.dumps(data,ensure_ascii=False,allow_nan=False).encode())>quota:raise ValueError('SQL output exceeds dataset quota')
        portable(data);check_schema(data,node.get('outputs'))
        if cancelled():raise ValueError('cancelled')
        if write:db.commit()
        else:db.rollback()
        return {'status':'succeeded','output':{'schemaVersion':1,'data':data,'artifacts':[]},'stdout':'','stderr':'','credential_revision':connection['revision']}
    except BaseException:
        try:db.rollback()
        except Exception:pass
        raise
    finally:db.close()
