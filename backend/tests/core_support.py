"""Scoped, fail-closed doubles for units that import the Flask applications."""

import importlib
import sys
import types
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import numpy as np


BACKEND_DIR = Path(__file__).resolve().parents[1]


class LegacyIngestionJobs:
    """Synchronous queue adapter for existing request validation/contract fixtures."""

    def __init__(self, processor):
        self.processor = processor
        self.pending = {}

    def submit(self, kind, raw, filename, *, replace=False, submitted_by=None):
        import uuid

        job_id = uuid.uuid4().hex
        self.pending[job_id] = (kind, raw, filename, replace)
        return {'job_id': job_id, 'kind': kind, 'status': 'queued'}

    def wait(self, job_id, kind):
        stored_kind, raw, filename, replace = self.pending.pop(job_id)
        assert kind == stored_kind
        if kind == 'json':
            return self.processor.ingest_qa(raw, replace=replace)
        return self.processor.ingest_pdf(raw, filename, replace=replace)


def module_double(name, **attributes):
    module = types.ModuleType(name)
    module.__dict__.update(attributes)
    return module


def clear_admin_routes():
    for name in tuple(sys.modules):
        if name == 'admin_routes' or name.startswith('admin_routes.'):
            sys.modules.pop(name, None)


class OfflineCoreFixture:
    def setUp(self):
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        self.resources.enter_context(patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path]))
        model_directory = self.resources.enter_context(TemporaryDirectory())
        self.resources.enter_context(patch.dict('os.environ', {
            'APP_ENV': 'test',
            'ADMIN_SESSION_SECRET': 'core-unit-tests-only-not-a-deployment-secret',
            'QWEN_EMBEDDING_PATH': model_directory,
            'GEN_MODEL': model_directory,
            'STT_MODEL_DIR': model_directory,
            'STT_LOCAL_FILES_ONLY': '1',
        }, clear=True))
        self.forbidden_model = Mock(side_effect=AssertionError('Real model work is forbidden'))
        doubles = {
            'numpy': np,
            'interface': module_double(
                'interface', embed_texts=self.forbidden_model, embedding_dim=self.forbidden_model,
                set_embedding_model=self.forbidden_model, BASE_DIR=str(BACKEND_DIR),
                ADMIN_DIR=str(BACKEND_DIR / 'admin'), ADMIN_PORT=8000, UI_PORT=5000,
                MIN_SCORE=0.35, ALLOWED_EXT={'.json'},
            ),
            'generator': module_double('generator', generate_reply=self.forbidden_model,
                                       set_generator_model=self.forbidden_model),
            'voice': module_double('voice', configure_voice=Mock()),
            'fitz': module_double('fitz', open=self.forbidden_model),
        }
        self.resources.enter_context(patch.dict(sys.modules, doubles))
        clear_admin_routes()
        for name in ('admin', 'user', 'searchForAnswer', 'admin_users'):
            sys.modules.pop(name, None)
        self.database = importlib.import_module('database')
        self.forbidden_database = self.resources.enter_context(patch.object(
            self.database, 'get_conn', side_effect=AssertionError('Real database work is forbidden'),
        ))
        self.resources.enter_context(patch.object(self.database.logger, '_write', return_value=True))
        self.scoring = importlib.import_module('searchForAnswer')
        self.accounts = importlib.import_module('admin_users')
        self.policy = importlib.import_module('admin_policy')
        self.parsing = importlib.import_module('request_validation')

    def tearDown(self):
        self.forbidden_model.assert_not_called()
        self.forbidden_database.assert_not_called()
