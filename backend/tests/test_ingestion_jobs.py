"""Durable job transitions, request isolation, restart recovery and bounded workers."""

import importlib
import io
import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from core_support import OfflineCoreFixture
from job_support import JobDatabase


class IngestionJobTests(OfflineCoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.jobs_module = importlib.import_module('services.ingestion_jobs')
        self.store_module = importlib.import_module('services.job_store')
        self.worker_module = importlib.import_module('ingestion_worker')
        directory = self.resources.enter_context(TemporaryDirectory())
        self.database = JobDatabase(Path(directory) / 'jobs.sqlite')
        self.store = self.store_module.MySQLJobStore(connect=self.database.connect, capacity=8)
        self.processor = Mock()
        self.processor.ingest_qa.return_value = {'received': 1, 'inserted': 1}
        self.processor.ingest_pdf.return_value = {'success': True, 'document_id': 7}
        self.jobs = self.jobs_module.IngestionJobs(self.processor, store=self.store)
        self.raw = b'[{"question":"question","answer":"answer"}]'

    def submit(self, kind='json', raw=None):
        return self.jobs.submit(kind, self.raw if raw is None else raw, 'upload.' + kind)['job_id']

    def worker(self, concurrency=1):
        worker = self.worker_module.IngestionWorker(self.jobs, concurrency=concurrency, poll_seconds=0.01)
        self.addCleanup(worker.stop)
        return worker.start()

    def until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail('Timed out waiting for an ingestion worker')
            time.sleep(0.01)

    def test_default_store_and_worker_construct_without_external_work(self):
        jobs = self.jobs_module.IngestionJobs(self.processor)
        worker = self.worker_module.IngestionWorker(jobs)
        self.assertEqual(jobs.store.capacity, 16)
        self.assertEqual(worker.concurrency, 1)
        self.assertEqual(worker.poll_seconds, 1.0)
        self.assertIsNone(worker.supervisor)
        self.assertIsNone(worker.lease)
        self.processor.ingest_qa.assert_not_called()
        self.processor.ingest_pdf.assert_not_called()

    def test_submission_persists_before_return_without_parsing_or_embedding(self):
        job_id = self.submit(raw=b'invalid JSON is validated by the worker')
        self.processor.ingest_qa.assert_not_called()
        restarted_store = self.store_module.MySQLJobStore(connect=self.database.connect)
        restarted = self.jobs_module.IngestionJobs(self.processor, store=restarted_store)
        self.assertEqual(restarted.get(job_id, 'json')['status'], 'queued')
        self.assertTrue(restarted.process_one('worker', lambda: None))
        finished = restarted.get(job_id, 'json')
        self.assertEqual(finished['status'], 'completed')
        self.assertEqual(finished['result'], self.processor.ingest_qa.return_value)
        self.assertIsNotNone(finished['started_at'])
        self.assertIsNotNone(finished['finished_at'])
        self.assertEqual(self.jobs.get(job_id, 'json'), finished)
        self.assertEqual(self.payload_count(job_id), 0)

    def payload_count(self, job_id):
        connection = self.database.connect()
        try:
            return connection.raw.execute('SELECT COUNT(*) FROM ingestion_job_payloads WHERE job_id=?', (job_id,)).fetchone()[0]
        finally:
            connection.close()

    def test_payload_chunks_and_checksum_survive_new_store(self):
        raw = b'x' * (self.store_module.PAYLOAD_CHUNK_BYTES + 17)
        job_id = self.submit(raw=raw)
        job = self.store.claim('worker')
        self.assertEqual(self.payload_count(job_id), 2)
        self.assertEqual(self.store.payload(job), raw)
        connection = self.database.connect()
        try:
            connection.raw.execute('UPDATE ingestion_job_payloads SET content=? WHERE job_id=? AND chunk_index=1',
                                   (b'y' * 17, job_id))
        finally:
            connection.close()
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.store.payload(job)

    def test_failure_is_safe_terminal_and_payload_is_released(self):
        job_id = self.submit()
        self.processor.ingest_qa.side_effect = RuntimeError('private document / password')
        self.jobs.process_one('worker', lambda: None)
        job = self.jobs.get(job_id, 'json')
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['error']['code'], 'internal_error')
        self.assertNotIn('private', json.dumps(job))
        self.assertEqual(self.payload_count(job_id), 0)

    def test_recovery_preserves_queued_and_terminal_states_and_fails_interrupted(self):
        completed, failed, canceled, running, queued = [self.submit() for _ in range(5)]
        for job_id, status in ((completed, 'completed'), (failed, 'failed')):
            job = self.store.claim('old-worker')
            self.assertEqual(job['job_id'], job_id)
            self.store.finish(job, result={'ok': True}) if status == 'completed' else self.store.finish(job, error_code='internal_error', error_status=500)
        self.jobs.cancel(canceled, 'json')
        self.assertEqual(self.store.claim('old-worker')['job_id'], running)
        self.worker()
        self.until(lambda: self.jobs.get(queued, 'json')['status'] == 'completed')
        self.assertEqual([self.jobs.get(job_id, 'json')['status'] for job_id in (completed, failed, canceled, running, queued)],
                         ['completed', 'failed', 'canceled', 'failed', 'completed'])
        self.assertEqual(self.jobs.get(running, 'json')['error']['code'], 'ingestion_interrupted')
        self.processor.ingest_qa.assert_called_once()
        self.assertEqual(self.payload_count(running), 0)

    def test_cancel_is_idempotent_and_cannot_cancel_running_or_another_kind(self):
        job_id = self.submit()
        self.assertEqual(self.jobs.cancel(job_id, 'json')['status'], 'canceled')
        self.assertEqual(self.jobs.cancel(job_id, 'json')['status'], 'canceled')
        self.assertEqual(self.payload_count(job_id), 0)
        self.assertFalse(self.jobs.process_one('worker', lambda: None))
        for candidate, kind, status in ((job_id, 'pdf', 404), ('bad', 'json', 404)):
            with self.assertRaises(self.jobs_module.IngestionError) as raised:
                self.jobs.get(candidate, kind)
            self.assertEqual(raised.exception.status, status)
        running = self.submit()
        self.store.claim('worker')
        with self.assertRaises(self.jobs_module.IngestionError) as raised:
            self.jobs.cancel(running, 'json')
        self.assertEqual(raised.exception.status, 409)

    def test_capacity_is_bounded_and_cancel_frees_admission(self):
        self.store.capacity = 1
        job_id = self.submit()
        with self.assertRaises(self.jobs_module.IngestionError) as raised:
            self.submit()
        self.assertEqual(raised.exception.status, 503)
        self.jobs.cancel(job_id, 'json')
        self.assertEqual(self.jobs.get(self.submit(), 'json')['status'], 'queued')

    def test_pool_concurrency_is_bounded_and_standby_takes_over(self):
        entered, release = threading.Event(), threading.Event()
        mutex = threading.Lock()
        counts = {'active': 0, 'peak': 0, 'calls': 0}

        def process(*args, **kwargs):
            with mutex:
                counts['active'] += 1
                counts['calls'] += 1
                counts['peak'] = max(counts['peak'], counts['active'])
                if counts['active'] == 2:
                    entered.set()
            release.wait(5)
            kwargs['publication_guard']()
            with mutex:
                counts['active'] -= 1
            return {'inserted': 1}

        self.processor.ingest_qa.side_effect = process
        job_ids = [self.submit() for _ in range(5)]
        first = self.worker(concurrency=2)
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(5))
        standby = self.worker(concurrency=2)
        self.assertEqual(counts['calls'], 2)
        release.set()
        self.until(lambda: all(self.jobs.get(job_id, 'json')['status'] == 'completed' for job_id in job_ids))
        self.assertEqual(counts['peak'], 2)
        self.assertTrue(first.stop())
        next_job = self.submit()
        self.until(lambda: self.jobs.get(next_job, 'json')['status'] == 'completed')
        self.assertTrue(standby.stop())

    def test_legacy_wait_is_bounded_without_canceling_durable_work(self):
        job_id = self.submit()
        with self.assertRaises(self.jobs_module.IngestionError) as raised:
            self.jobs.wait(job_id, 'json', timeout=0)
        self.assertEqual(raised.exception.status, 503)
        self.assertEqual(self.jobs.get(job_id, 'json')['status'], 'queued')

    def test_graceful_stop_drains_running_job_and_leaves_pending_work_queued(self):
        entered, release = threading.Event(), threading.Event()

        def process(*args, **kwargs):
            entered.set()
            release.wait(5)
            kwargs['publication_guard']()
            return {'inserted': 1}

        self.processor.ingest_qa.side_effect = process
        running, queued = self.submit(), self.submit()
        worker = self.worker()
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(5))
        self.assertFalse(worker.stop(timeout=0.01))
        release.set()
        self.assertTrue(worker.stop())
        self.assertEqual(self.jobs.get(running, 'json')['status'], 'completed')
        self.assertEqual(self.jobs.get(queued, 'json')['status'], 'queued')

    def test_failed_payload_write_rolls_back_job_metadata_and_all_chunks(self):
        from job_support import JobCursor

        original = JobCursor.execute

        def reject_payload(cursor, query, parameters=()):
            if query.startswith('INSERT INTO ingestion_job_payloads'):
                raise RuntimeError('Injected payload storage failure')
            return original(cursor, query, parameters)

        with patch.object(JobCursor, 'execute', reject_payload), self.assertRaises(RuntimeError):
            self.submit()
        self.assertIsNone(self.store.claim('worker'))

    def test_recovered_job_cannot_be_overwritten_by_a_stale_worker(self):
        job_id = self.submit()
        old_job = self.store.claim('old-worker')
        self.store.recover()
        self.store.finish(old_job, result={'inserted': 1})
        self.assertFalse(self.store.is_running(old_job))
        self.assertEqual(self.jobs.get(job_id, 'json')['error']['code'], 'ingestion_interrupted')

    def test_lost_lease_after_embedding_prevents_knowledge_publication(self):
        import numpy as np

        ingestion = importlib.import_module('services.ingestion')
        lost = threading.Event()
        publish = Mock(side_effect=AssertionError('Lost worker must not publish knowledge'))

        def embed(texts):
            lost.set()
            return np.ones((len(texts), 2), dtype=np.float32)

        def guard():
            if lost.is_set():
                raise RuntimeError('Lease lost')

        self.jobs.processor = ingestion.IngestionService(embed=embed, connect=publish, audit=Mock())
        job_id = self.submit()
        self.jobs.process_one('worker', guard)
        self.assertEqual(self.jobs.get(job_id, 'json')['status'], 'failed')
        publish.assert_not_called()

    def test_worker_pdf_failure_is_reported_as_failed_job(self):
        ingestion = importlib.import_module('services.ingestion')
        self.jobs.processor = ingestion.IngestionService(open_pdf=Mock(side_effect=ValueError('Invalid PDF')), audit=Mock())
        job_id = self.submit('pdf', b'not a PDF')
        self.jobs.process_one('worker', lambda: None)
        self.assertEqual(self.jobs.get(job_id, 'pdf')['error']['code'], 'invalid_request')

    def test_submission_allows_large_files_and_preserves_multipart_guards(self):
        app = importlib.import_module('application').create_admin_app(ingestion_jobs=self.jobs)
        auth = importlib.import_module('admin_routes.auth')
        account = {'id': 1, 'is_active': True, 'is_admin': True, 'permissions': []}
        client = app.test_client()
        with patch.object(auth, 'load_admin_session', return_value=account), \
                patch.object(auth, 'valid_csrf_token', return_value=True):
            for prefix, extension in (('/admin/api/upload/jobs', 'json'), ('/admin/api/pdf/upload/jobs', 'pdf')):
                response = client.post(prefix, data={'file': (io.BytesIO(b'x' * 70000), 'upload.' + extension)})
                self.assertEqual(response.status_code, 202)
                self.assertEqual(client.post(prefix, data={'file': (io.BytesIO(b'x'), 'bad.txt')}).status_code, 400)
                self.assertEqual(client.post(prefix, data={'file': (io.BytesIO(b'x'), 'good.' + extension),
                                                         'replace': 'maybe'}).status_code, 400)

    def test_http_submission_polling_permissions_csrf_and_cancellation(self):
        app = importlib.import_module('application').create_admin_app(ingestion_jobs=self.jobs)
        auth = importlib.import_module('admin_routes.auth')
        account = {'id': 1, 'is_active': True, 'is_admin': False, 'permissions': ['json_upload']}
        client = app.test_client()
        with patch.object(auth, 'load_admin_session', return_value=account), \
                patch.object(auth, 'valid_csrf_token', return_value=True) as csrf:
            response = client.post('/admin/api/upload/jobs', data={'file': (io.BytesIO(self.raw), 'upload.json')})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json['status'], 'queued')
            self.assertEqual(response.headers['Retry-After'], '1')
            location = response.headers['Location']
            self.assertEqual(client.get(location).json['job_id'], response.json['job_id'])
            self.processor.ingest_qa.assert_not_called()
            self.assertEqual(client.get(location.replace('/upload/', '/pdf/upload/')).status_code, 403)
            csrf.return_value = False
            self.assertEqual(client.delete(location).status_code, 403)
            csrf.return_value = True
            self.assertEqual(client.delete(location).json['status'], 'canceled')
            self.assertEqual(client.get(location).headers['Cache-Control'], 'no-store')
            self.assertEqual(client.get(location + '?unknown=1').status_code, 400)
            account['permissions'] = []
            self.assertEqual(client.get(location).status_code, 403)
        self.assertEqual(client.get(location).status_code, 401)


if __name__ == '__main__':
    unittest.main()
