"""Request-independent durable job submission, status and execution."""

import json
import logging
import re
import time
from datetime import timezone

from werkzeug.exceptions import HTTPException

from public_errors import public_error
from request_validation import MAX_JSON_UPLOAD_BYTES, MAX_PDF_UPLOAD_BYTES
from services.ingestion import IngestionError
from services.job_store import MySQLJobStore


TERMINAL_STATES = frozenset({'completed', 'failed', 'canceled'})
LOG = logging.getLogger('chatbot.ingestion')


def timestamp(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value is not None else None


class IngestionJobs:
    def __init__(self, processor, *, store=None):
        self.processor = processor
        self.store = MySQLJobStore() if store is None else store

    def submit(self, kind, raw, filename, *, replace=False, submitted_by=None):
        maximum = {'json': MAX_JSON_UPLOAD_BYTES, 'pdf': MAX_PDF_UPLOAD_BYTES}.get(kind)
        if maximum is None or not raw:
            raise IngestionError({'error': 'Invalid upload.'}, 400)
        if len(raw) > maximum:
            raise IngestionError({'error': 'Upload is too large.'}, 413)
        job_id = self.store.submit(kind, raw, filename[:255], replace, submitted_by)
        return {'job_id': job_id, 'kind': kind, 'status': 'queued'}

    def _get(self, job_id, kind):
        if not re.fullmatch(r'[0-9a-f]{32}', job_id):
            raise IngestionError({'error': 'Job not found.'}, 404)
        job = self.store.get(job_id, kind)
        if job is None:
            raise IngestionError({'error': 'Job not found.'}, 404)
        return job

    def get(self, job_id, kind):
        job = self._get(job_id, kind)
        error = None
        if job['status'] == 'failed':
            error = public_error(job['error_status'] or 500, job['error_code'])
        return {
            'job_id': job['job_id'], 'kind': job['kind'], 'filename': job['filename'], 'status': job['status'],
            'created_at': timestamp(job['created_at']), 'started_at': timestamp(job['started_at']),
            'finished_at': timestamp(job['finished_at']),
            'result': json.loads(job['result_json']) if job['result_json'] else None, 'error': error,
            'progress': json.loads(job['progress_json']) if job['progress_json'] else None,
            'checkpoint': 'upload' if job['checkpoint_available'] else None,
            'retryable': bool(job['status'] in {'failed', 'canceled'} and job['checkpoint_available']
                              and job['finished_at'] is not None),
        }

    def cancel(self, job_id, kind):
        self._get(job_id, kind)
        self.store.cancel(job_id, kind)
        return self.get(job_id, kind)

    def retry(self, job_id, kind):
        self._get(job_id, kind)
        self.store.expire_checkpoints()
        self.store.retry(job_id, kind)
        return self.get(job_id, kind)

    def wait(self, job_id, kind, *, timeout=240):
        deadline = time.monotonic() + timeout
        while True:
            job = self._get(job_id, kind)
            if job['status'] == 'completed':
                return json.loads(job['result_json'])
            if job['status'] in {'failed', 'canceled'}:
                status = job['error_status'] or 409
                raise IngestionError(public_error(status, job['error_code']), status)
            if time.monotonic() >= deadline:
                raise IngestionError(public_error(503), 503)
            time.sleep(0.25)

    def process_one(self, worker_id, guard):
        job = self.store.claim(worker_id)
        if job is None:
            return False

        def publication_guard():
            guard()
            if not self.store.is_running(job):
                raise RuntimeError('Job ownership was lost')

        try:
            raw = self.store.payload(job, guard=publication_guard)
            publication_guard()
            if job['kind'] == 'json':
                result = self.processor.ingest_qa(
                    raw, replace=bool(job['replace_existing']), publication_guard=publication_guard,
                    progress=lambda event: self.store.progress(job, event),
                    publication=lambda result: self.store.publication(job, result, publication_guard),
                )
            else:
                result = self.processor.ingest_pdf(
                    raw, job['filename'], replace=bool(job['replace_existing']), publication_guard=publication_guard,
                    progress=lambda event: self.store.progress(job, event),
                    publication=lambda result: self.store.publication(job, result, publication_guard),
                )
            self.store.finish(job, result=result)
        except Exception as error:
            status = error.status if isinstance(error, IngestionError) else error.code if isinstance(error, HTTPException) else 500
            self.store.finish(job, error_code=public_error(status)['code'], error_status=status)
            LOG.warning('Ingestion job failed (%s)', type(error).__name__)
        return True
