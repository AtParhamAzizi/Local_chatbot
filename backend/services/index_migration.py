"""Explicitly started, resumable background re-embedding with retained generations."""

import atexit
import logging
import threading

from embedding_profile import EmbeddingProfile, checked_batch
from persian_text import NORMALIZATION_VERSION, normalize_persian
from services.index_store import IndexChanged, InvalidIndexVector, MySQLIndexStore
from settings import setting_value


LOG = logging.getLogger('chatbot.index_migration')
_service = None


def current_migration_service():
    return _service


class IndexMigrationService:
    def __init__(self, *, store=None, load=None, batch_size=None):
        self.store = MySQLIndexStore() if store is None else store
        self.load = load
        self.batch_size = setting_value('EMBEDDING_BATCH_SIZE') if batch_size is None else batch_size
        if type(self.batch_size) is not int or not 1 <= self.batch_size <= 64:
            raise ValueError('Index batch size must be between 1 and 64')
        self.runtimes = {}
        self.loading = {}
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.thread = None
        self.error_code = None
        self.failed_request = None
        self.guard = lambda: None

    def _load(self, model_id, path):
        if self.load is not None:
            return self.load(model_id, path)
        from interface import load_embedding_runtime
        return load_embedding_runtime(model_id, path)

    def initialize(self, runtime):
        self.runtimes[runtime.profile.identity] = runtime
        self.store.initialize(runtime)
        # Restore the actual serving profile before opening either HTTP listener.
        self.runtime(self.store.state()['active_id'])

    def runtime(self, index_id):
        with self.lock:
            cached = self.runtimes.get(index_id)
            loading = self.loading.setdefault(index_id, threading.Lock())
        if cached is not None:
            return cached
        # Candidate I/O must never hold the serving runtime's cache lock.
        with loading:
            with self.lock:
                cached = self.runtimes.get(index_id)
            if cached is not None:
                return cached
            metadata = self.store.get(index_id)
            expected = EmbeddingProfile.from_json(metadata['profile'])
            runtime = self._load(metadata['model_id'], metadata['model_path'])
            if runtime.profile != expected:
                raise ValueError('Retained model artifacts/runtime no longer match the serving index')
            with self.lock:
                self.runtimes[index_id] = runtime
            return runtime

    def embed(self, texts):
        # Capture once. A switch after this read cannot relabel this query/batch.
        return self.runtime(self.store.state()['active_id']).embed(texts)

    def request(self, model_id, path):
        self.store.request(model_id, path)

    def build_step(self, index_id, *, token=None):
        metadata = self.store.get(index_id)
        if metadata['status'] == 'failed':
            return False
        runtime = self.runtime(index_id)
        if runtime.profile.normalization_version != NORMALIZATION_VERSION:
            raise ValueError('Re-embedding requires the recorded normalization implementation')
        for kind in ('qa', 'pdf'):
            rows = self.store.pending(index_id, kind, self.batch_size)
            if not rows:
                continue
            vectors, actual = checked_batch(runtime.embed([normalize_persian(row['text']) for row in rows]), rows=len(rows))
            if actual != runtime.profile:
                raise ValueError('Candidate embedding profile changed during migration')
            self.guard()
            self.store.save(index_id, kind, rows, vectors)
            return True
        self.guard()
        self.store.validate_and_activate(index_id, token=token)
        self.error_code = None
        state = self.store.state()
        retained = {state['active_id'], state['previous_id'], state['requested_id']}
        with self.lock:
            self.runtimes = {key: value for key, value in self.runtimes.items() if key in retained}
        return False

    def tick(self):
        state = self.store.state()
        active = self.store.get(state['active_id'])
        # Materialize the serving generation before replacing any base vectors;
        # this also refreshes legacy NULL-profile knowledge without source uploads.
        index_id = active['index_id']
        try:
            if active['validated_at'] is None:
                return self.build_step(index_id)
            token = state['request_token']
            if token is None or token == self.failed_request:
                return False
            if state['requested_id'] is None:
                runtime = self._load(state['requested_model'], state['requested_path'])
                with self.lock:
                    self.runtimes[runtime.profile.identity] = runtime
                index_id = self.store.bind(runtime, token)
            else:
                index_id = state['requested_id']
            return self.build_step(index_id, token=token)
        except IndexChanged:
            return True
        except Exception as error:
            self.error_code = 'index_build_failed'
            if isinstance(error, InvalidIndexVector):
                self.store.discard_invalid(index_id, error)
            if index_id != state['active_id'] or active['validated_at'] is None:
                self.store.fail(index_id)
            self.store.fail_request(state['request_token'])
            self.failed_request = state['request_token']
            raise

    def _run(self):
        # A dedicated database lock elects one builder; liveness is checked again
        # after inference and before publication. Request tokens fence cutover.
        from services.job_store import database_lock
        while not self.stopping.is_set():
            try:
                with self.store.connect() as lease:
                    with database_lock(lease, 'embedding-index', required=False) as elected:
                        if elected:
                            self.guard = lambda: lease.ping(reconnect=False)
                            try:
                                while not self.stopping.is_set():
                                    lease.ping(reconnect=False)
                                    if not self.tick():
                                        self.stopping.wait(1)
                            finally:
                                self.guard = lambda: None
            except Exception as error:
                LOG.warning('Embedding migration unavailable (%s)', type(error).__name__)
            self.stopping.wait(1)

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name='embedding-index', daemon=True)
            self.thread.start()
        return self

    def stop(self):
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(5)


def prepare_index_migration():
    """Restore serving resources before warmup; defer candidate loading to the worker."""
    global _service
    if _service is None:
        service = IndexMigrationService()
        state = service.store.state()
        from model_registry import get_active_model_id, get_model_definition
        model_id = get_active_model_id()
        path = get_model_definition(model_id)['embedding_path']
        if state is None or state['active_id'] is None:
            service.initialize(service._load(model_id, path))
        else:
            runtime = service.runtime(state['active_id'])
            service.initialize(runtime)
            if state['request_token'] is None:
                if (model_id, path) != (state['configured_model'], state['configured_path']):
                    service.request(model_id, path)
        _service = service
        atexit.register(service.stop)
    return _service


def start_index_migration_worker(ui_app, admin_app):
    service = prepare_index_migration()
    ui_app.extensions['index_migration'] = service
    admin_app.extensions['index_migration'] = service
    return service.start()
