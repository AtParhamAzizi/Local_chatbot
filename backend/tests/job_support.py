"""Disk-backed SQLite translation of job SQL for offline restart/concurrency tests."""

import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone


class JobDatabase:
    def __init__(self, path):
        self.path = str(path)
        self.mutex = threading.RLock()
        self.locks = {}
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE ingestion_jobs (
                    job_id TEXT PRIMARY KEY, kind TEXT, filename TEXT, replace_existing INTEGER,
                    file_size INTEGER, payload_sha256 TEXT, submitted_by INTEGER,
                    status TEXT DEFAULT 'queued', worker_id TEXT, result_json TEXT,
                    error_code TEXT, error_status INTEGER, progress_json TEXT, created_at TEXT,
                    started_at TEXT, finished_at TEXT
                );
                CREATE TABLE ingestion_job_payloads (
                    job_id TEXT, chunk_index INTEGER, content BLOB,
                    PRIMARY KEY (job_id, chunk_index)
                );
            ''')

    def connect(self):
        return JobConnection(self)


class JobConnection:
    def __init__(self, database):
        self.database = database
        self.raw = sqlite3.connect(database.path, isolation_level=None, check_same_thread=False, timeout=5)
        self.raw.row_factory = sqlite3.Row
        self.raw.create_function('UTC_TIMESTAMP', 0, lambda: datetime.now(timezone.utc).replace(tzinfo=None).isoformat(' '))

    def cursor(self):
        return JobCursor(self)

    def begin(self):
        self.raw.execute('BEGIN IMMEDIATE')

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def ping(self, reconnect=False):
        self.raw.execute('SELECT 1')

    def close(self):
        with self.database.mutex:
            self.database.locks = {name: owner for name, owner in self.database.locks.items() if owner is not self}
        self.raw.close()


class JobCursor:
    def __init__(self, connection):
        self.connection = connection
        self.raw = connection.raw.cursor()
        self.result = None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.raw.close()

    @property
    def lastrowid(self):
        return self.raw.lastrowid

    def execute(self, query, parameters=()):
        self.result = None
        database = self.connection.database
        if query.startswith('SELECT DATABASE()'):
            self.result = {'name': database.path}
        elif 'GET_LOCK(' in query:
            with database.mutex:
                acquired = parameters[0] not in database.locks
                if acquired:
                    database.locks[parameters[0]] = self.connection
                self.result = {'acquired': int(acquired)}
        elif 'RELEASE_LOCK(' in query:
            with database.mutex:
                if database.locks.get(parameters[0]) is self.connection:
                    del database.locks[parameters[0]]
                self.result = {'released': 1}
        else:
            if query.startswith('DELETE payload FROM'):
                query = "DELETE FROM ingestion_job_payloads WHERE job_id IN (SELECT job_id FROM ingestion_jobs WHERE status='failed')"
            query = query.replace('%s', '?').replace('UTC_TIMESTAMP(6)', 'UTC_TIMESTAMP()')
            query = query.replace('DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 DAY)', "datetime(UTC_TIMESTAMP(), '-1 day')")
            if query.startswith('DELETE FROM ingestion_job_payloads') and query.endswith(' LIMIT 1024'):
                query = query[:-len(' LIMIT 1024')]
            query = query.replace(' FOR UPDATE SKIP LOCKED', '').replace(' FOR UPDATE', '')
            self.raw.execute(query, parameters)
            self.rowcount = self.raw.rowcount

    def fetchone(self):
        row = self.result if self.result is not None else self.raw.fetchone()
        if row is None:
            return None
        row = dict(row)
        for column in ('created_at', 'started_at', 'finished_at'):
            if row.get(column):
                row[column] = datetime.fromisoformat(row[column])
        return row

    def fetchall(self):
        return [dict(row) for row in self.raw.fetchall()]
