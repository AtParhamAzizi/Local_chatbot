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
               'status, worker_id, result_json, error_code, error_status, progress_json, '
               'created_at, started_at, finished_at, '
               'EXISTS(SELECT 1 FROM ingestion_job_payloads payload WHERE payload.job_id=ingestion_jobs.job_id '
               'AND (started_at IS NULL OR progress_json IS NOT NULL) '
               'AND (finished_at IS NULL OR finished_at >= DATE_SUB(UTC_TIMESTAMP(6), INTERVAL 1 DAY))) AS checkpoint_available')


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
        self.expire_checkpoints()
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
        self.expire_checkpoints()
        worker_id = uuid.uuid4().hex
        with self.transaction() as cursor:
            cursor.execute(
                f"SELECT {JOB_COLUMNS} FROM ingestion_jobs WHERE status='queued' "
                'ORDER BY created_at, job_id LIMIT 1 FOR UPDATE SKIP LOCKED',
            )
            job = cursor.fetchone()
            if job is None:
                return None
            cursor.execute(
                "UPDATE ingestion_jobs SET status='running', worker_id=%s, started_at=UTC_TIMESTAMP(6), progress_json=%s "
                "WHERE job_id=%s AND status='queued'",
                (worker_id, json.dumps({'stage': 'extraction', 'source': job['kind'], 'completed': 0, 'total': 1}), job['job_id']),
            )
            job['worker_id'] = worker_id
            job['status'] = 'running'
            return job

    def payload(self, job, guard=None):
        raw = bytearray(job['file_size'])
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                for index, start in enumerate(range(0, len(raw), PAYLOAD_CHUNK_BYTES)):
                    if guard is not None:
                        guard()
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
        if guard is not None:
            guard()
        if hashlib.sha256(raw).hexdigest() != job['payload_sha256']:
            raise ValueError('Job payload checksum mismatch')
        return raw

    def is_running(self, job):
        current = self.get(job['job_id'], job['kind'])
        return current is not None and current['status'] == 'running' and current['worker_id'] == job['worker_id']

    def _finish(self, cursor, job, *, result=None, error_code=None, error_status=None):
        cursor.execute(
            'UPDATE ingestion_jobs SET status=%s, result_json=%s, error_code=%s, error_status=%s, '
            'finished_at=UTC_TIMESTAMP(6) WHERE job_id=%s AND worker_id=%s AND status=\'running\'',
            ('failed' if error_code else 'completed', json.dumps(result) if result is not None else None,
             error_code, error_status, job['job_id'], job['worker_id']),
        )
        changed = cursor.rowcount == 1
        if changed and not error_code:
            cursor.execute('DELETE FROM ingestion_job_payloads WHERE job_id=%s', (job['job_id'],))
        return changed

    def finish(self, job, **outcome):
        with self.transaction() as cursor:
            return self._finish(cursor, job, **outcome)

    def progress(self, job, event):
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE ingestion_jobs SET progress_json=%s WHERE job_id=%s AND worker_id=%s AND status='running'",
                    (json.dumps(event), job['job_id'], job['worker_id']),
                )
        finally:
            connection.close()

    @contextmanager
    def publication(self, job, result, guard):
        """Knowledge and successful job outcome commit together, fenced against cancellation.

        Do not lock the job row while writing knowledge: cancellation must be able
        to win during publication. The final locking read sees the latest attempt.
        """
        connection = self.connect()
        try:
            connection.begin()
            yield connection
            guard()
            with connection.cursor() as cursor:
                cursor.execute('SELECT status, worker_id FROM ingestion_jobs WHERE job_id=%s FOR UPDATE', (job['job_id'],))
                current = cursor.fetchone()
                if not current or current['status'] != 'running' or current['worker_id'] != job['worker_id']:
                    raise RuntimeError('Job ownership was lost')
                if not self._finish(cursor, job, result=result):
                    raise RuntimeError('Job ownership was lost')
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

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
            if job['status'] not in {'queued', 'running'}:
                raise IngestionError({'error': 'Job is already finished.'}, 409)
            cursor.execute(
                "UPDATE ingestion_jobs SET status='canceled', finished_at=UTC_TIMESTAMP(6) WHERE job_id=%s", (job_id,),
            )

    def retry(self, job_id, kind):
        connection = self.connect()
        try:
            with database_lock(connection, 'admission'):
                connection.begin()
                with connection.cursor() as cursor:
                    cursor.execute(f'SELECT {JOB_COLUMNS} FROM ingestion_jobs WHERE job_id=%s AND kind=%s FOR UPDATE', (job_id, kind))
                    job = cursor.fetchone()
                    if job is None:
                        raise IngestionError({'error': 'Job not found.'}, 404)
                    if job['status'] == 'queued':
                        connection.commit()
                        return
                    if job['status'] not in {'failed', 'canceled'} or not job['checkpoint_available']:
                        raise IngestionError({'error': 'Job has no retry checkpoint.'}, 409)
                    cursor.execute("SELECT COUNT(*) AS count FROM ingestion_jobs WHERE status IN ('queued', 'running')")
                    if cursor.fetchone()['count'] >= self.capacity:
                        raise IngestionError({'error': 'The ingestion queue is full.'}, 503)
                    cursor.execute(
                        "UPDATE ingestion_jobs SET status='queued', worker_id=NULL, result_json=NULL, "
                        'error_code=NULL, error_status=NULL, progress_json=NULL, started_at=NULL, finished_at=NULL WHERE job_id=%s',
                        (job_id,),
                    )
                connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def expire_checkpoints(self):
        with self.transaction() as cursor:
            cursor.execute(
                'DELETE FROM ingestion_job_payloads WHERE job_id IN '
                "(SELECT job_id FROM ingestion_jobs WHERE status IN ('failed', 'canceled') "
                'AND finished_at < DATE_SUB(UTC_TIMESTAMP(6), INTERVAL 1 DAY)) LIMIT 1024',
            )

    def recover(self):
        with self.transaction() as cursor:
            cursor.execute(
                "UPDATE ingestion_jobs SET status='failed', error_code='ingestion_interrupted', error_status=500, "
                "finished_at=UTC_TIMESTAMP(6) WHERE status='running'",
            )
