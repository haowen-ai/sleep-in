"""Small transactional repository. PostgreSQL is the supported deployment store."""
import copy
import os
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import create_engine, MetaData, Table, Column, String, JSON, select, delete, text
from cryptography.fernet import Fernet


def now():
    return datetime.now(timezone.utc)


def stamp():
    return now().isoformat()


def uid():
    return secrets.token_hex(12)


class Transaction:
    def __init__(self, conn, table):
        self.conn, self.table = conn, table

    def get(self, kind, key):
        row = self.conn.execute(select(self.table.c.payload).where(self.table.c.kind == kind, self.table.c.id == key)).first()
        return copy.deepcopy(row[0]) if row else None

    def all(self, kind):
        return [copy.deepcopy(r[0]) for r in self.conn.execute(select(self.table.c.payload).where(self.table.c.kind == kind))]

    def put(self, kind, value):
        where = (self.table.c.kind == kind) & (self.table.c.id == value['id'])
        if self.get(kind, value['id']) is not None:
            self.conn.execute(self.table.update().where(where).values(payload=copy.deepcopy(value)))
        else:
            self.conn.execute(self.table.insert().values(kind=kind, id=value['id'], payload=copy.deepcopy(value)))
        return value

    def remove(self, kind, key):
        self.conn.execute(delete(self.table).where(self.table.c.kind == kind, self.table.c.id == key))


class Store:
    def __init__(self, path, database_url):
        self.path = Path(path).resolve()
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        for folder in ('scripts', 'runs', 'runtimes'):
            (self.path / folder).mkdir(exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        options = {'connect_args': {'check_same_thread': False, 'timeout': 30}} if database_url.startswith('sqlite') else {'pool_pre_ping': True}
        self.engine = create_engine(database_url, **options)
        metadata = MetaData()
        self.table = Table('console_records', metadata, Column('kind', String(32), primary_key=True), Column('id', String(128), primary_key=True), Column('payload', JSON, nullable=False))
        metadata.create_all(self.engine)
        for name in ('setup-token', 'dispatch-token', 'variable-key'):
            value = Fernet.generate_key().decode() if name == 'variable-key' else secrets.token_urlsafe(32)
            try:
                fd = os.open(self.path / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, 'w') as f:
                    f.write(value)
            except FileExistsError:
                pass
        self.fernet = Fernet((self.path / 'variable-key').read_bytes().strip())
        with self.transaction() as tx:
            if not tx.get('meta', 'settings'):
                tx.put('meta', {'id': 'settings', 'schema': 1, 'timezone': 'UTC', 'concurrency': 2, 'log_retention_days': 30, 'metadata_retention_days': 90, 'last_tick': None, 'worker': None})

    @contextmanager
    def transaction(self):
        # A single advisory lock protects transitions (including absent-row inserts).
        # The initial release is deliberately single-workspace, <=100 schedules.
        with self.lock, self.engine.connect() as conn:
            if self.engine.dialect.name == 'sqlite':
                conn.exec_driver_sql('BEGIN IMMEDIATE')
            else:
                conn.begin()
                conn.execute(text('SELECT pg_advisory_xact_lock(781923451)'))
            try:
                yield Transaction(conn, self.table)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
