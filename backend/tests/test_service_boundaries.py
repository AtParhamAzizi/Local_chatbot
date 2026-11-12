"""Services run without Flask contexts; focused route groups preserve HTTP contracts."""

import hashlib
import importlib
import io
import json
import sys
import threading
import unittest
from concurrent.futures import Future
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import numpy as np
from embedding_fixtures import with_profile
from flask import Flask, has_app_context, has_request_context
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from admin_route_contract import ADMIN_ROUTES
from core_support import BACKEND_DIR, LegacyIngestionJobs, OfflineCoreFixture, clear_admin_routes


class RouteBoundaryTests(OfflineCoreFixture, unittest.TestCase):
    def test_each_administrator_responsibility_imports_and_registers_independently(self):
        groups = ('auth', 'accounts', 'pages', 'knowledge', 'pdf', 'jobs', 'models', 'logs', 'history')
        for group in groups:
            with self.subTest(group=group):
                clear_admin_routes()
                module = importlib.import_module('admin_routes.' + group)
                app = Flask('independent_' + group, static_folder=None)
                module.register_routes(app)
                self.assertTrue(list(app.url_map.iter_rules()))
                imported = {name for name in sys.modules if name.startswith('admin_routes.')}
                self.assertLessEqual(imported, {'admin_routes.' + group, 'admin_routes.paths'})

    def test_all_existing_route_paths_methods_and_endpoint_names_are_unchanged(self):
        app = importlib.import_module('application').create_admin_app()
        expected = set()
        for path, endpoint, methods in ADMIN_ROUTES:
            for method in methods:
                expected.add((path, endpoint, method))
                if method == 'GET':
                    expected.add((path, endpoint, 'HEAD'))
            expected.add((path, endpoint, 'OPTIONS'))
        actual = {(rule.rule, rule.endpoint, method)
                  for rule in app.url_map.iter_rules() for method in rule.methods}
        self.assertEqual(actual, expected)

    def test_app_factories_accept_independent_service_replacements(self):
        application = importlib.import_module('application')
        ingestion, lifecycle, retrieval = Mock(), Mock(), Mock()
        admin = application.create_admin_app(ingestion_service=ingestion, model_lifecycle=lifecycle)
        ui = application.create_ui_app(retrieval_service=retrieval)
        self.assertIs(admin.extensions['ingestion_service'], ingestion)
        self.assertIs(admin.extensions['model_lifecycle'], lifecycle)
        self.assertIs(ui.extensions['retrieval_service'], retrieval)
        ingestion.assert_not_called()
        lifecycle.assert_not_called()
        retrieval.assert_not_called()

    def test_existing_ingestion_and_model_routes_dispatch_through_service_interfaces(self):
        application = importlib.import_module('application')
        ingestion, lifecycle = Mock(), Mock()
        ingestion.ingest_qa.return_value = {'received': 1, 'inserted': 1, 'errors': []}
        ingestion.ingest_pdf.return_value = {'success': True, 'document_id': 17}
        lifecycle.catalog.return_value = {'active_model': 'test', 'models': []}
        lifecycle.select_model.return_value = 'test'
        app = application.create_admin_app(ingestion_service=ingestion, model_lifecycle=lifecycle)
        app.extensions['ingestion_jobs'] = LegacyIngestionJobs(ingestion)
        auth = importlib.import_module('admin_routes.auth')
        client = app.test_client()
        account = {'id': 1, 'is_active': True, 'is_admin': True, 'permissions': []}
        raw = b'[{"question":"question","answer":"answer"}]'
        with patch.object(auth, 'load_admin_session', return_value=account), \
                patch.object(auth, 'valid_csrf_token', return_value=True):
            response = client.post('/admin/api/upload', data={
                'file': (io.BytesIO(raw), 'records.json'), 'replace': 'true',
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, ingestion.ingest_qa.return_value)
            ingestion.ingest_qa.assert_called_once_with(raw, replace=True)
            response = client.post('/admin/api/pdf/upload', data={'file': (io.BytesIO(b'pdf'), 'document.pdf')})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, ingestion.ingest_pdf.return_value)
            ingestion.ingest_pdf.assert_called_once_with(b'pdf', 'document.pdf', replace=False)
            self.assertEqual(client.get('/admin/api/models').json, lifecycle.catalog.return_value)
            response = client.post('/admin/api/models', json={'model_id': 'test'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['active_model'], 'test')
            lifecycle.select_model.assert_called_once_with('test')
            ingestion.ingest_pdf.side_effect = importlib.import_module('services.ingestion').IngestionError(
                {'duplicate': True, 'document_id': 17}, 409,
            )
            response = client.post('/admin/api/pdf/upload', data={'file': (io.BytesIO(b'pdf'), 'duplicate.pdf')})
            self.assertEqual((response.status_code, response.json['code']), (409, 'conflict'))

    def test_chat_uses_the_injected_retrieval_service_without_changing_response_shape(self):
        retrieval = Mock()
        retrieval.retrieve.return_value = []
        with patch.dict('os.environ', {'RETENTION_CHAT_DAYS': '0'}):
            app = importlib.import_module('application').create_ui_app(retrieval_service=retrieval)
            response = app.test_client().post('/chat', json={'message': 'question'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json), {
            'message_id', 'feedback_enabled', 'reply', 'recommended_questions', 'score', 'sources',
        })
        self.assertIsNone(response.json['message_id'])
        self.assertEqual(response.json['sources'], [])
        retrieval.retrieve.assert_called_once_with('question', limit=6)


class ServiceFixture:
    def setUp(self):
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        self.resources.enter_context(patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path]))
        self.resources.enter_context(patch.dict('os.environ', {'APP_ENV': 'production'}, clear=True))
        self.assertFalse(has_app_context())
        self.assertFalse(has_request_context())


class IngestionServiceTests(ServiceFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.module = importlib.import_module('services.ingestion')
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.return_value = None
        self.cursor.lastrowid = 17
        self.connect = Mock(return_value=self.connection)
        self.embed = Mock(side_effect=lambda texts: np.tile(np.array([0.25, 0.5], dtype=np.float32), (len(texts), 1)))
        self.open_pdf = Mock()
        self.service = self.module.IngestionService(
            connect=self.connect, embed=with_profile(self.embed), open_pdf=self.open_pdf,
            hash_question=lambda question: hashlib.sha256(question.strip().encode()).hexdigest(),
            hash_document=lambda raw: hashlib.sha256(raw).hexdigest(), audit=Mock(), clock=lambda: 100.0,
        )

    def payload(self, rows=None):
        return json.dumps(rows if rows is not None else [{'question': 'question', 'answer': 'answer'}]).encode()

    def document(self, texts=('Page one.', 'Page two.')):
        document = Mock(page_count=len(texts))
        document.load_page.side_effect = [Mock(get_text=Mock(return_value=text)) for text in texts]
        self.open_pdf.return_value = document
        return document

    def test_json_ingestion_validates_and_deduplicates_without_a_request(self):
        result = self.service.ingest_qa(self.payload([{'question': ' question ', 'answer': 'answer'}] * 2))
        self.assertEqual(result, {
            'received': 2, 'inserted': 1, 'updated': 0, 'skipped': 0,
            'embedded': 1, 'duration_sec': 0.0, 'errors': [],
        })
        self.embed.assert_called_once_with(['question'])
        parameters = self.cursor.execute.call_args.args[1]
        self.assertEqual(parameters[:2], ('question', 'answer'))
        np.testing.assert_array_equal(np.frombuffer(parameters[3], dtype=np.float32), [0.25, 0.5])
        self.connection.close.assert_called_once()

    def test_json_replacement_and_skip_counts_remain_compatible(self):
        self.cursor.fetchone.return_value = {'id': 9}
        skipped = self.service.ingest_qa(self.payload())
        self.assertEqual((skipped['inserted'], skipped['updated'], skipped['skipped']), (0, 0, 1))
        self.assertEqual(self.cursor.execute.call_count, 1)
        self.cursor.execute.reset_mock()
        replaced = self.service.ingest_qa(self.payload(), replace=True)
        self.assertEqual((replaced['inserted'], replaced['updated'], replaced['skipped']), (0, 1, 0))
        self.assertIn('UPDATE qa_documents', self.cursor.execute.call_args.args[0])
        self.assertEqual(self.cursor.execute.call_args.args[1][-1], 9)

    def test_invalid_or_oversized_json_precedes_all_model_and_database_work(self):
        valid = {'question': 'question', 'answer': 'answer'}
        for raw in (b'bad', b'[]', self.payload([valid, {'question': 'later invalid'}])):
            with self.subTest(raw=raw), self.assertRaises(BadRequest):
                self.service.ingest_qa(raw)
        with self.assertRaises(RequestEntityTooLarge):
            self.service.ingest_qa(self.payload([valid] * 257))
        self.embed.assert_not_called()
        self.connect.assert_not_called()

    def test_json_embedding_failure_is_typed_and_precedes_database_access(self):
        self.embed.side_effect = RuntimeError('private model path')
        with self.assertRaises(self.module.IngestionError) as caught:
            self.service.ingest_qa(self.payload())
        self.assertEqual(caught.exception.status, 500)
        self.assertNotIn('private', str(caught.exception.payload))
        self.connect.assert_not_called()

    def test_json_database_failure_always_closes_connection(self):
        self.cursor.execute.side_effect = RuntimeError('query failed')
        with self.assertRaisesRegex(RuntimeError, 'query failed'):
            self.service.ingest_qa(self.payload())
        self.connection.close.assert_called_once()

    def test_pdf_page_and_text_limits_close_documents_before_model_or_database_work(self):
        too_many_pages = Mock(page_count=201)
        self.open_pdf.return_value = too_many_pages
        with self.assertRaises(RequestEntityTooLarge):
            self.service.ingest_pdf(b'pdf', 'test.pdf')
        too_many_pages.load_page.assert_not_called()
        too_many_pages.close.assert_called_once()
        oversized_text = self.document(('x' * 500001,))
        with self.assertRaises(RequestEntityTooLarge):
            self.service.ingest_pdf(b'pdf', 'test.pdf')
        oversized_text.close.assert_called_once()
        self.embed.assert_not_called()
        self.connect.assert_not_called()

    def test_pdf_duplicate_detection_does_not_embed_or_replace(self):
        document = self.document()
        self.cursor.fetchone.return_value = {'id': 4, 'filename': 'old.pdf', 'status': 'completed'}
        with self.assertRaises(self.module.IngestionError) as caught:
            self.service.ingest_pdf(b'pdf', 'test.pdf')
        self.assertEqual(caught.exception.status, 409)
        self.assertTrue(caught.exception.payload['duplicate'])
        self.assertEqual(caught.exception.payload['document_id'], 4)
        self.assertEqual(self.cursor.execute.call_count, 1)
        self.embed.assert_not_called()
        self.connection.close.assert_called_once()
        document.close.assert_called_once()

    def test_pdf_replacement_embeds_before_deleting_existing_document(self):
        self.document()
        self.cursor.fetchone.return_value = {'id': 4, 'filename': 'old.pdf', 'status': 'completed'}
        self.embed.side_effect = RuntimeError('embedding failed')
        with self.assertRaises(self.module.IngestionError):
            self.service.ingest_pdf(b'pdf', 'test.pdf', replace=True)
        self.assertEqual(self.connect.call_count, 1)
        self.assertTrue(all('DELETE' not in call.args[0] for call in self.cursor.execute.call_args_list))

    def test_pdf_ingestion_preserves_page_metadata_and_float32_storage(self):
        document = self.document()
        self.embed.side_effect = lambda texts: np.ones((len(texts), 3), dtype=np.float64)
        result = self.service.ingest_pdf(b'pdf', 'test.pdf')
        self.assertEqual(result, {
            'success': True, 'document_id': 17, 'filename': 'test.pdf', 'file_size': 3,
            'page_count': 2, 'chunk_count': 2, 'embedded_chunks': 2, 'embedding_dim': 3, 'duration_sec': 0.0,
        })
        chunks = [call.args[1] for call in self.cursor.execute.call_args_list if 'INSERT INTO pdf_chunks' in call.args[0]]
        self.assertEqual([chunk[1:4] for chunk in chunks], [(0, 1, 1), (1, 2, 2)])
        for chunk in chunks:
            self.assertEqual(len(chunk[5]), 3 * np.dtype(np.float32).itemsize)
        self.connection.commit.assert_called_once()
        self.assertEqual(self.connection.close.call_count, 2)
        document.close.assert_called_once()

    def test_pdf_persistence_failure_rolls_back_without_publishing_a_failed_document(self):
        self.document()
        def fail_chunk(query, parameters=None):
            if 'INSERT INTO pdf_chunks' in query:
                raise RuntimeError('private SQL details')
        self.cursor.execute.side_effect = fail_chunk
        with self.assertRaises(self.module.IngestionError) as caught:
            self.service.ingest_pdf(b'pdf', 'test.pdf')
        self.assertEqual(caught.exception.status, 500)
        self.connection.rollback.assert_called_once()
        self.assertFalse(any('error_message' in call.args[0] for call in self.cursor.execute.call_args_list))
        self.connection.commit.assert_not_called()
        self.assertEqual(self.connection.close.call_count, 2)


class ModelLifecycleTests(ServiceFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.module = importlib.import_module('services.lifecycle')
        self.generator = SimpleNamespace(
            get_generator=Mock(), set_generator_model=Mock(), _gen_lock=threading.Lock(),
            _gen_model=object(), _gen_tokenizer=object(), _gen_device='cpu', MODEL_ID='test',
        )
        self.embedder = SimpleNamespace(
            get_embedder=Mock(), set_embedding_model=Mock(), _lock=threading.Lock(),
            _model=object(), _tokenizer=object(), _dim=3, MODEL_ID='test',
        )
        self.voice = SimpleNamespace(
            _start_model_loading=Mock(), _model_lock=threading.Lock(),
            _model_future=None, IMPORT_ERROR=None, MODEL_NAME='test',
        )
        self.registry = SimpleNamespace(
            set_active_model=Mock(), get_active_model_id=Mock(return_value='test'),
            get_model_catalog=Mock(return_value=[{'id': 'test'}]),
        )
        self.free_memory = Mock()
        self.audit = Mock()
        self.threads = Mock()
        self.service = self.module.ModelLifecycleService(
            modules={'generator': self.generator, 'interface': self.embedder,
                     'voice': self.voice, 'model_registry': self.registry},
            release_memory=self.free_memory, gpu_info=lambda: {'available': False},
            audit=self.audit, thread_factory=self.threads,
        )

    def test_warmup_and_model_selection_have_explicit_order(self):
        calls = Mock()
        for name, operation in (
            ('embed', self.embedder.get_embedder), ('generate', self.generator.get_generator),
            ('persist', self.registry.set_active_model), ('select_embed', self.embedder.set_embedding_model),
            ('select_generate', self.generator.set_generator_model),
        ):
            calls.attach_mock(operation, name)
        self.service.warmup()
        self.assertEqual(self.service.select_model('test'), 'test')
        self.assertEqual([call[0] for call in calls.mock_calls],
                         ['embed', 'generate', 'persist', 'select_embed', 'select_generate'])
        self.assertEqual(self.service.catalog(), {'active_model': 'test', 'models': [{'id': 'test'}]})
        self.threads.assert_not_called()

    def test_invalid_selection_does_not_reset_models(self):
        for error in (FileNotFoundError('missing model'), ValueError('unknown model')):
            self.registry.set_active_model.side_effect = error
            with self.subTest(error=error), self.assertRaises(type(error)):
                self.service.select_model('invalid')
        self.embedder.set_embedding_model.assert_not_called()
        self.generator.set_generator_model.assert_not_called()

    def test_unloading_clears_adapter_state_under_its_lock_and_releases_memory(self):
        self.voice._model_future = Future()
        self.service.unload_generator()
        self.service.unload_embedder()
        self.service.unload_voice()
        self.assertIsNone(self.generator._gen_model)
        self.assertIsNone(self.generator._gen_tokenizer)
        self.assertIsNone(self.generator._gen_device)
        self.assertIsNone(self.embedder._model)
        self.assertIsNone(self.embedder._tokenizer)
        self.assertIsNone(self.embedder._dim)
        self.assertIsNone(self.voice._model_future)
        self.assertEqual(self.free_memory.call_count, 3)

    def complete_action(self, index=-1):
        arguments = self.threads.call_args_list[index].kwargs
        arguments['target'](*arguments['args'])

    def test_async_actions_are_exclusive_per_model_and_release_after_completion(self):
        self.assertTrue(self.service.start_action('generator_load'))
        self.assertFalse(self.service.start_action('generator_unload'))
        self.assertTrue(self.service.start_action('embedder_load'))
        self.assertEqual(self.service.status()['busy'], {
            'generator': 'generator_load', 'embedder': 'embedder_load',
        })
        self.complete_action(0)
        self.complete_action(1)
        self.generator.get_generator.assert_called_once()
        self.embedder.get_embedder.assert_called_once()
        self.assertEqual(self.service.busy, {})
        self.assertTrue(self.service.start_action('generator_unload'))
        self.complete_action()

    def test_failed_action_uses_fixed_error_code_and_successful_retry_clears_it(self):
        self.generator.get_generator.side_effect = RuntimeError('private weights path')
        self.service.start_action('generator_load')
        self.complete_action()
        self.assertEqual(self.service.status()['errors'], {'generator': 'model_action_failed'})
        self.assertEqual(self.service.busy, {})
        self.assertNotIn('private', str(self.audit.call_args_list))
        self.generator.get_generator.side_effect = None
        self.service.start_action('generator_load')
        self.complete_action()
        self.assertEqual(self.service.status()['errors'], {})

    def test_thread_creation_failure_and_unknown_actions_do_not_leave_busy_state(self):
        self.threads.return_value.start.side_effect = RuntimeError('thread unavailable')
        with self.assertRaises(RuntimeError):
            self.service.start_action('voice_load')
        self.assertEqual(self.service.busy, {})
        self.threads.reset_mock()
        with self.assertRaises(ValueError):
            self.service.start_action('unknown')
        self.threads.assert_not_called()

    def test_status_preserves_voice_states_and_returns_detached_monitoring_snapshots(self):
        future = Future()
        for state, error, model_future in (
            ('unavailable', ImportError(), None), ('unloaded', None, None), ('loading', None, future),
        ):
            self.voice.IMPORT_ERROR, self.voice._model_future = error, model_future
            self.assertEqual(self.service.status()['models']['voice']['state'], state)
        future.set_result(object())
        self.assertEqual(self.service.status()['models']['voice']['state'], 'loaded')
        future = Future()
        future.set_exception(RuntimeError('private failure'))
        self.voice._model_future = future
        self.assertEqual(self.service.status()['models']['voice']['state'], 'error')
        status = self.service.status()
        status['busy']['future'] = 'must not persist'
        status['errors']['future'] = 'must not persist'
        self.assertEqual(self.service.busy, {})
        self.assertEqual(self.service.errors, {})


if __name__ == '__main__':
    unittest.main()
