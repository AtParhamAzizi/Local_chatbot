"""One database-elected worker pool; no connections or threads during imports/assembly."""

import atexit
import logging
import threading
import uuid

from services.job_store import database_lock
from settings import setting_value


LOG = logging.getLogger('chatbot.ingestion')


class IngestionWorker:
    def __init__(self, jobs, *, concurrency=None, poll_seconds=None):
        self.jobs = jobs
        self.concurrency = setting_value('INGESTION_WORKERS') if concurrency is None else concurrency
        self.poll_seconds = setting_value('INGESTION_POLL_SECONDS') if poll_seconds is None else poll_seconds
        self.worker_id = uuid.uuid4().hex
        self.stopping = threading.Event()
        self.lost_lease = threading.Event()
        self.lease_guard = threading.RLock()
        self.threads = []
        self.supervisor = None
        self.lease = None
        self.lease_context = None

    def guard(self):
        with self.lease_guard:
            if self.lost_lease.is_set() or self.lease is None:
                raise RuntimeError('Ingestion worker lease is unavailable')
            try:
                self.lease.ping(reconnect=False)
            except Exception:
                self.lost_lease.set()
                raise

    def start(self):
        if self.supervisor is not None:
            return self
        self.supervisor = threading.Thread(target=self._supervise, name='ingestion-supervisor', daemon=True)
        self.supervisor.start()
        return self

    def _run(self):
        while not self.stopping.is_set() and not self.lost_lease.is_set():
            try:
                self.guard()
                if self.jobs.process_one(self.worker_id, self.guard):
                    continue
            except Exception as error:
                LOG.warning('Ingestion worker operation failed (%s)', type(error).__name__)
            self.stopping.wait(self.poll_seconds)

    def _supervise(self):
        while not self.stopping.is_set():
            self.threads = []
            self.lost_lease.clear()
            try:
                self.lease = self.jobs.store.connect()
                self.lease_context = database_lock(self.lease, 'workers', required=False)
                if self.lease_context.__enter__():
                    self.jobs.store.recover()
                    self.threads = [threading.Thread(target=self._run, name=f'ingestion-{index}', daemon=True)
                                    for index in range(self.concurrency)]
                    for thread in self.threads:
                        thread.start()
                    while not self.stopping.wait(min(self.poll_seconds, 1.0)):
                        self.guard()
                        if not all(thread.is_alive() for thread in self.threads):
                            raise RuntimeError('Ingestion worker stopped unexpectedly')
            except Exception as error:
                self.lost_lease.set()
                LOG.warning('Ingestion worker pool unavailable (%s)', type(error).__name__)
            finally:
                if not self.stopping.is_set():
                    self.lost_lease.set()
                for thread in self.threads:
                    if thread.ident is not None:
                        thread.join()
                self.lost_lease.set()
                try:
                    self._release()
                except Exception:
                    LOG.warning('Ingestion worker lease release failed')
            self.stopping.wait(self.poll_seconds)

    def _release(self):
        with self.lease_guard:
            try:
                if self.lease_context is not None:
                    self.lease_context.__exit__(None, None, None)
            finally:
                if self.lease is not None:
                    self.lease.close()
                self.lease = None
                self.lease_context = None

    def stop(self, timeout=5):
        self.stopping.set()
        if self.supervisor is not None:
            self.supervisor.join(timeout)
            return not self.supervisor.is_alive()
        return True


def start_ingestion_worker(ui_app, admin_app):
    worker = admin_app.extensions.get('ingestion_worker')
    if worker is None:
        worker = IngestionWorker(admin_app.extensions['ingestion_jobs'])
        admin_app.extensions['ingestion_worker'] = worker
        atexit.register(worker.stop)
    worker.start()
    ui_app.extensions['ingestion_worker'] = worker
    return worker
