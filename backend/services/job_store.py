"""Durable MySQL ingestion metadata and bounded payload chunks, with no startup work."""

import hashlib
import json
import uuid
from contextlib import contextmanager

from services import adapters
from services.ingestion import IngestionError
from settings import setting_value


PAYLOAD_CHUNK_BYTES = 1024 * 1024
JOB_COLUMNS = ('job_id, kind, filename, replace_existing, file_size, payload_sha256, '
               'status, worker_id, result_json, error_code, error_status, '
               'created_at, started_at, finished_at')


@contextmanager
def database_lock(connection, purpose, *, required=True):
    with connection.cursor() as cursor:
        cursor.execute('SELECT DATABASE() AS name')
        name = 'chatbot-ingestion-' + purpose + '-' + hashlib.sha256(cursor.fetchone()['name'].encode()).hexdigest()[:32]
        cursor.execute('SELECT GET_LOCK(%s, 0) AS acquired', (name,))
        acquired = cursor.fetchone()['acquired'] == 1
    if required and not acquired:
        raise IngestionError({'error': 'The ingestion queue is busy.'}, 503)
    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cursor:
                cursor.execute('SELECT RELEASE_LOCK(%s)', (name,))


class MySQLJobStore:
    def __init__(self, *, connect=adapters.connect_database, capacity=None):
        self.connect = connect
        self.capacity = setting_value('INGESTION_QUEUE_CAPACITY') if capacity is None else capacity

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.begin()
            with connection.cursor() as cursor:
                yield cursor
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def submit(self, kind, raw, filename, replace, submitted_by):
        job_id = uuid.uuid4().hex
        connection = self.connect()
        try:
            with database_lock(connection, 'admission'):
                connection.begin()
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT COUNT(*) AS count FROM ingestion_jobs WHERE status IN ('queued', 'running')")
                        if cursor.fetchone()['count'] >= self.capacity:
                            raise IngestionError({'error': 'The ingestion queue is full.'}, 503)
                        cursor.execute(
                            'INSERT INTO ingestion_jobs '
                            '(job_id, kind, filename, replace_existing, file_size, payload_sha256, submitted_by, created_at) '
                            'VALUES (%s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP(6))',
                            (job_id, kind, filename, bool(replace), len(raw), hashlib.sha256(raw).hexdigest(), submitted_by),
                        )
                        for index, start in enumerate(range(0, len(raw), PAYLOAD_CHUNK_BYTES)):
                            cursor.execute(
                                'INSERT INTO ingestion_job_payloads (job_id, chunk_index, content) VALUES (%s, %s, %s)',
                                (job_id, index, raw[start:start + PAYLOAD_CHUNK_BYTES]),
                            )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        finally:
            connection.close()
        return job_id

    def get(self, job_id, kind):
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(f'SELECT {JOB_COLUMNS} FROM ingestion_jobs WHERE job_id=%s AND kind=%s', (job_id, kind))
                return cursor.fetchone()
        finally:
            connection.close()

    def claim(self, worker_id):
        with self.transaction() as cursor:
            cursor.execute(
                f"SELECT {JOB_COLUMNS} FROM ingestion_jobs WHERE status='queued' "
                'ORDER BY created_at, job_id LIMIT 1 FOR UPDATE SKIP LOCKED',
            )
            job = cursor.fetchone()
            if job is None:
                return None
            cursor.execute(
                "UPDATE ingestion_jobs SET status='running', worker_id=%s, started_at=UTC_TIMESTAMP(6) "
                "WHERE job_id=%s AND status='queued'", (worker_id, job['job_id']),
            )
            job['worker_id'] = worker_id
            job['status'] = 'running'
            return job

    def payload(self, job):
        raw = bytearray(job['file_size'])
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                for index, start in enumerate(range(0, len(raw), PAYLOAD_CHUNK_BYTES)):
                    cursor.execute(
                        'SELECT content FROM ingestion_job_payloads WHERE job_id=%s AND chunk_index=%s',
                        (job['job_id'], index),
                    )
                    row = cursor.fetchone()
                    expected = min(PAYLOAD_CHUNK_BYTES, len(raw) - start)
                    if row is None or len(row['content']) != expected:
                        raise ValueError('Incomplete job payload')
                    raw[start:start + expected] = row['content']
        finally:
            connection.close()
        if hashlib.sha256(raw).hexdigest() != job['payload_sha256']:
            raise ValueError('Job payload checksum mismatch')
        return raw

    def is_running(self, job):
        current = self.get(job['job_id'], job['kind'])
        return current is not None and current['status'] == 'running' and current['worker_id'] == job['worker_id']

    def finish(self, job, *, result=None, error_code=None, error_status=None):
        with self.transaction() as cursor:
            cursor.execute(
                'UPDATE ingestion_jobs SET status=%s, result_json=%s, error_code=%s, error_status=%s, '
                'finished_at=UTC_TIMESTAMP(6) WHERE job_id=%s AND worker_id=%s AND status=\'running\'',
                ('failed' if error_code else 'completed', json.dumps(result) if result is not None else None,
                 error_code, error_status, job['job_id'], job['worker_id']),
            )
            if cursor.rowcount == 1:
                cursor.execute('DELETE FROM ingestion_job_payloads WHERE job_id=%s', (job['job_id'],))

    def cancel(self, job_id, kind):
        with self.transaction() as cursor:
            cursor.execute(
                'SELECT status FROM ingestion_jobs WHERE job_id=%s AND kind=%s FOR UPDATE', (job_id, kind),
            )
            job = cursor.fetchone()
            if job is None:
                raise IngestionError({'error': 'Job not found.'}, 404)
            if job['status'] == 'canceled':
                return
            if job['status'] != 'queued':
                raise IngestionError({'error': 'Only queued jobs can be canceled.'}, 409)
            cursor.execute(
                "UPDATE ingestion_jobs SET status='canceled', finished_at=UTC_TIMESTAMP(6) WHERE job_id=%s", (job_id,),
            )
            cursor.execute('DELETE FROM ingestion_job_payloads WHERE job_id=%s', (job_id,))

    def recover(self):
        with self.transaction() as cursor:
            cursor.execute(
                "UPDATE ingestion_jobs SET status='failed', error_code='ingestion_interrupted', error_status=500, "
                "finished_at=UTC_TIMESTAMP(6) WHERE status='running'",
            )
            cursor.execute(
                'DELETE payload FROM ingestion_job_payloads AS payload '
                "INNER JOIN ingestion_jobs AS job ON job.job_id=payload.job_id WHERE job.status='failed'",
            )
