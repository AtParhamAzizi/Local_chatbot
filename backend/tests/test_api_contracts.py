"""Documented v1 contracts exercised through real Flask routes and guards."""

import importlib
import io
import json
import re
import unittest
from concurrent.futures import Future
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import numpy as np

from admin_route_contract import ADMIN_ROUTES
from api_contract_support import ApiContract, KEYWORDS
from test_abuse_controls import AbuseEndpointFixture, FakeSocket
from test_chat_ownership import MemoryHistory


class ApiContractTests(AbuseEndpointFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.contract = ApiContract()
        cls.covered = set()
        cls.controller_api = importlib.import_module('controller_api')
        cls.controller_api.register(cls.ui_app, cls.admin_app)
        cls.addClassCleanup(cls.assert_success_coverage)

    @classmethod
    def assert_success_coverage(cls):
        expected = {(path, method, int(status)) for path, method, operation in cls.contract.operations()
                    for status in operation['responses'] if status.isdecimal() and 200 <= int(status) < 400}
        missing = expected - cls.covered
        if missing:
            raise AssertionError(f'Documented success cases lack route coverage: {sorted(missing)}')

    def setUp(self):
        super().setUp()
        self.headers = self.authenticated_headers()
        self.current_user.update(created_at=None, updated_at=None)
        controller = importlib.import_module('controller')
        self.resources.enter_context(patch.dict(self.controller_api._state, {
            'voice_enabled': True, 'chat_enabled': True,
        }, clear=True))
        self.resources.enter_context(patch.object(controller, '_save_state'))
        registry = importlib.import_module('model_registry')
        self.resources.enter_context(patch.object(registry, '_read_saved_id', return_value=None))
        self.resources.enter_context(patch.object(registry, '_has_config', return_value=True))
        lifecycle = importlib.import_module('services.lifecycle')
        self.lifecycle = lifecycle.ModelLifecycleService(modules={
            'generator': SimpleNamespace(_gen_model=None, _gen_device=None, MODEL_ID='qwen3.5-2b'),
            'interface': SimpleNamespace(_model=None, _dim=None, MODEL_ID='qwen3.5-2b'),
            'voice': self.voice, 'model_registry': registry,
        }, gpu_info=lambda: {'available': False}, audit=Mock(), thread_factory=Mock())
        self.resources.enter_context(patch.dict(self.admin_app.extensions, {'model_lifecycle': self.lifecycle}))
        self.speech = Mock()
        self.speech.status.return_value = 'ready'
        self.speech.synthesize.return_value = b'RIFF\x00\x00\x00\x00WAVE'
        self.resources.enter_context(patch.dict(self.ui_app.extensions, {'speech_service': self.speech}))
        self.timestamp = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)

    def check(self, response, path, method='GET', status=200):
        self.assertEqual(response.status_code, status, response.get_data(as_text=True) if response.is_json else path)
        result = self.contract.assert_response(response, path, method)
        self.covered.add((path, method, status))
        return result

    def database_rows(self, *, rows=(), first=None, first_sequence=None, rowcount=1):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = list(rows)
        cursor.fetchone.return_value = first
        if first_sequence is not None:
            cursor.fetchone.side_effect = first_sequence
        cursor.rowcount = rowcount
        cursor.lastrowid = 7
        self.resources.enter_context(patch.object(self.database, 'get_conn', return_value=connection))
        accounts = importlib.import_module('admin_users')
        self.resources.enter_context(patch.object(accounts, 'get_conn', return_value=connection))
        return connection, cursor

    def test_document_schemas_references_versions_and_error_codes_are_consistent(self):
        version = importlib.import_module('api_contract')
        errors = importlib.import_module('public_errors')
        document = self.contract.document
        self.assertEqual(document['info']['version'], version.API_CONTRACT_VERSION)
        self.assertEqual(document['x-api-major'], version.API_MAJOR_VERSION)
        self.assertEqual(document['x-error-codes'], {code: value[0] for code, value in errors.ERRORS.items()})
        self.assertEqual(set(document['components']['schemas']['Error']['properties']['code']['enum']),
                         set(errors.ERRORS) | {'request_failed'})
        operation_ids = [operation['operationId'] for _, _, operation in self.contract.operations()]
        self.assertEqual(len(operation_ids), len(set(operation_ids)))

        def check_schema(schema):
            self.assertFalse(set(schema) - KEYWORDS - {key for key in schema if key.startswith('x-')})
            if '$ref' in schema:
                self.contract.resolve(schema)
            for nested in schema.get('properties', {}).values():
                check_schema(nested)
            for nested in schema.get('anyOf', ()):
                check_schema(nested)
            for key in ('items', 'additionalProperties'):
                if isinstance(schema.get(key), dict):
                    check_schema(schema[key])

        for schema in document['components']['schemas'].values():
            check_schema(schema)
        for _, _, operation in self.contract.operations():
            for parameter in operation.get('parameters', ()):
                check_schema(parameter['schema'])
            contents = list(operation.get('requestBody', {}).get('content', {}).values())
            for response in operation['responses'].values():
                contents.extend(self.contract.resolve(response).get('content', {}).values())
            for content in contents:
                check_schema(content['schema'])

    def test_schema_assertions_detect_drift_and_refuse_unsupported_features(self):
        valid = {'message_id': None, 'feedback_enabled': False, 'reply': 'answer',
                 'recommended_questions': [], 'score': None, 'sources': []}
        schema = {'$ref': '#/components/schemas/ChatReply'}
        self.contract.assert_schema(valid, schema)
        for changed in ({**valid, 'feedback_enabled': 1}, {**valid, 'score': float('nan')},
                        {**valid, 'sources': [{}]}, {**valid, 'secret': 'private'},
                        {key: value for key, value in valid.items() if key != 'message_id'}):
            with self.subTest(changed=changed), self.assertRaises(AssertionError):
                self.contract.assert_schema(changed, schema)
        with self.assertRaises(AssertionError):
            self.contract.assert_schema('value', {'unsupportedKeyword': True})
        with self.assertRaises(AssertionError):
            self.contract.resolve({'$ref': 'https://example.invalid/schema'})

    def test_documented_operations_match_real_routes_permissions_and_legacy_inventory(self):
        for path, method, operation in self.contract.operations():
            app = self.admin_app if operation['x-service'] == 'admin' else self.ui_app
            flask_path = re.sub(r'\{(\w+)\}', r'<int:\1>', path)
            matches = [rule for rule in app.url_map.iter_rules()
                       if rule.rule == flask_path and method in rule.methods]
            self.assertEqual([rule.endpoint for rule in matches], [operation['x-endpoint']], path)
            if operation['x-service'] == 'admin':
                policies = importlib.import_module('admin_policy')
                operation_name = policies.ENDPOINT_OPERATIONS[operation['x-endpoint']][method]
                self.assertEqual(operation['x-permission'], policies.OPERATION_POLICIES[operation_name])
                self.assertEqual(operation['x-csrf-required'], method not in {'GET', 'HEAD', 'OPTIONS'})
                self.assertEqual(operation['security'], [] if path == '/login' else [{'AdminSession': []}])
        documented_admin = {(path, method) for path, method, _ in self.contract.operations() if path.startswith('/admin/api/')}
        actual_admin = {(re.sub(r'<int:(\w+)>', r'{\1}', rule.rule), method)
                        for rule in self.admin_app.url_map.iter_rules() if rule.rule.startswith('/admin/api/')
                        for method in rule.methods - {'HEAD', 'OPTIONS'}}
        self.assertEqual(documented_admin, actual_admin)
        for path, endpoint, methods in ADMIN_ROUTES:
            for method in methods:
                self.assertTrue(any(rule.rule == path and rule.endpoint == endpoint and method in rule.methods
                                    for rule in self.admin_app.url_map.iter_rules()), (path, method))
        legacy_ui = {'/': 'ui_index', '/index.html': 'ui_index_alias', '/health': 'health',
                     '/chat': 'chat', '/feedback': 'feedback', '/voice/health': 'voice_health',
                     '/voice/audio-worklet.js': 'voice_worklet', '/voice-stream': 'voice_stream',
                     '/tts/health': 'tts_health', '/tts/synthesize': 'tts_synthesize',
                     '/controller/features': 'controller_features', '/controller/features.js': 'controller_features_js'}
        for path, endpoint in legacy_ui.items():
            self.assertTrue(any(rule.rule == path and rule.endpoint == endpoint for rule in self.ui_app.url_map.iter_rules()))

    def test_chat_retained_stateless_and_feedback_payloads_preserve_legacy_identity_behavior(self):
        history = MemoryHistory()
        with patch.object(self.user, 'get_conn', side_effect=history.connect), patch.object(
            self.user, '_retrieve_top', return_value=[
                dict(source_type='qa', score=0.9, question='Question', answer='Answer'),
                dict(source_type='pdf', score=0.8, filename='guide.pdf', page_start=1, page_end=2, content='Content'),
            ],
        ), patch.object(self.user, 'generate_reply', return_value='Answer'):
            payload = {'message': 'question', 'user_id': 'legacy-client-identity'}
            self.contract.assert_schema(payload, self.contract.document['components']['schemas']['ChatRequest'])
            reply = self.check(self.ui_client.post('/chat', json=payload), '/chat', 'POST')
            self.assertEqual(reply['sources'][1]['pages'], [1, 2])
            self.assertNotEqual(history.rows[reply['message_id']]['user_id'], payload['user_id'])
            feedback = {'message_id': reply['message_id'], 'feedback': 'like'}
            self.check(self.ui_client.post('/feedback', json=feedback), '/feedback', 'POST')
        with patch.dict('os.environ', {'RETENTION_CHAT_DAYS': '0'}), patch.object(self.user, '_retrieve_top', return_value=[]):
            reply = self.check(self.ui_client.post('/chat', json={'message': 'no matches'}), '/chat', 'POST')
        self.assertIsNone(reply['message_id'])
        self.assertIsNone(reply['score'])
        self.assertFalse(reply['feedback_enabled'])
        self.assertEqual(reply['sources'], [])

    def test_health_features_voice_and_binary_speech_success_shapes(self):
        self.check(self.ui_client.get('/health'), '/health')
        self.check(self.ui_client.get('/controller/features'), '/controller/features')
        with patch.object(self.voice, '_start_model_loading'):
            self.check(self.ui_client.get('/voice/health'), '/voice/health')
        self.check(self.ui_client.get('/tts/health'), '/tts/health')
        response = self.ui_client.post('/tts/synthesize', json={'text': 'hello'})
        self.check(response, '/tts/synthesize', 'POST')
        self.assertEqual(response.data[:4], b'RIFF')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_paginated_qa_and_pdf_collections_populated_empty_and_past_end(self):
        records = {
            '/admin/api/documents': {'id': 7, 'question': 'Question', 'answer': 'Answer', 'has_embedding': 1},
            '/admin/api/pdf/documents': {'id': 7, 'filename': 'guide.pdf', 'file_size': 100, 'page_count': 1,
                'chunk_count': 1, 'embedded_chunk_count': 1, 'embedding_dim': 3,
                'status': 'completed', 'created_at': self.timestamp},
        }
        for path, record in records.items():
            with self.subTest(path=path):
                connection, cursor = self.database_rows(rows=[record], first={'c': 3})
                result = self.check(self.admin_open(path + '?page=2&per_page=2&q=guide'), path)
                self.assertEqual((result['total'], result['page'], result['per_page']), (3, 2, 2))
                self.assertEqual(cursor.execute.call_args.args[1][-2:], (2, 2))
                connection.close.assert_called_once()
                cursor.fetchall.return_value = []
                result = self.check(self.admin_open(path + '?page=10000&per_page=100'), path)
                self.assertEqual((result['total'], result['items'], result['page']), (3, [], 10000))
                self.assertEqual(cursor.execute.call_args.args[1], (100, 999900))
                cursor.fetchone.return_value = {'c': 0}
                result = self.check(self.admin_open(path), path)
                self.assertEqual(result, {'total': 0, 'page': 1, 'per_page': 10, 'items': []})

    def test_stats_accounts_models_and_controller_success_shapes(self):
        self.database_rows(first_sequence=[{'c': 4}, {'c': 2}])
        knowledge = importlib.import_module('admin_routes.knowledge')
        with patch.object(knowledge, 'embedding_dim', return_value=3):
            self.check(self.admin_open('/admin/api/stats'), '/admin/api/stats')
        self.database_rows(first_sequence=[{'documents': 2, 'chunks': 3, 'embedded_chunks': 3}, {'embedding_dim': 3}])
        self.check(self.admin_open('/admin/api/pdf/stats'), '/admin/api/pdf/stats')
        self.check(self.admin_open('/admin/api/session'), '/admin/api/session')
        user = dict(self.current_user, password_hash='must-not-be-returned', session_version=1)
        self.database_rows(rows=[user], first={**user, 'id': 7})
        users = self.check(self.admin_open('/admin/api/admin-users'), '/admin/api/admin-users')
        self.assertNotIn('password_hash', json.dumps(users))
        self.check(self.admin_open('/admin/api/admin-users', 'POST', headers=self.headers,
                                   json={'username': 'new-user', 'password': 'test-password'}),
                   '/admin/api/admin-users', 'POST', 201)
        self.check(self.admin_open('/admin/api/admin-users/7', 'PATCH', headers=self.headers,
                                   json={'is_active': False}), '/admin/api/admin-users/{user_id}', 'PATCH')
        self.check(self.admin_open('/admin/api/admin-users/7', 'DELETE', headers=self.headers),
                   '/admin/api/admin-users/{user_id}', 'DELETE')
        self.check(self.admin_open('/admin/api/models'), '/admin/api/models')
        with patch.object(self.lifecycle, 'select_model', return_value='qwen3.5-2b'):
            self.check(self.admin_open('/admin/api/models', 'POST', headers=self.headers,
                                       json={'model_id': 'qwen3.5-2b'}), '/admin/api/models', 'POST')
        self.check(self.admin_open('/admin/api/controller/status'), '/admin/api/controller/status')
        self.check(self.admin_open('/admin/api/controller/action', 'POST', headers=self.headers,
                                   json={'action': 'chat_disable'}), '/admin/api/controller/action', 'POST')
        self.check(self.admin_open('/admin/api/controller/action', 'POST', headers=self.headers,
                                   json={'action': 'generator_load'}), '/admin/api/controller/action', 'POST', 202)

    def test_real_ingestion_success_payloads_and_document_deletion(self):
        self.database_rows()
        vector = np.array([[0.1, 0.2]], dtype=np.float32)
        with patch.object(self.ingestion, 'embed', return_value=vector):
            records = [{'question': 'question', 'answer': 'answer'}]
            self.contract.assert_schema(records, self.contract.document['components']['schemas']['QaFile'])
            self.check(self.admin_open('/admin/api/upload', 'POST', headers=self.headers,
                       data={'file': (io.BytesIO(json.dumps(records).encode()), 'records.json')}), '/admin/api/upload', 'POST')
            document = Mock(page_count=1)
            document.load_page.return_value.get_text.return_value = 'A short PDF paragraph.'
            with patch.object(self.ingestion, 'open_pdf', return_value=document):
                self.check(self.admin_open('/admin/api/pdf/upload', 'POST', headers=self.headers,
                           data={'file': (io.BytesIO(b'%PDF-test'), 'guide.pdf')}), '/admin/api/pdf/upload', 'POST')
        for path in ('/admin/api/documents/{doc_id}', '/admin/api/pdf/documents/{doc_id}'):
            result = self.check(self.admin_open(path.replace('{doc_id}', '7'), 'DELETE', headers=self.headers), path, 'DELETE')
            self.assertEqual(result['deleted'], 7)

    def test_history_and_log_records_dates_counts_and_legacy_deletion(self):
        session = dict(user_id='chat_abc', ip_address='', message_count=2, likes=1, dislikes=0,
                       started_at=self.timestamp, last_activity=self.timestamp)
        _, cursor = self.database_rows(rows=[session], first={'user_message': 'First'})
        result = self.check(self.admin_open('/admin/api/chat/sessions?limit=1&q=abc'), '/admin/api/chat/sessions')
        self.assertEqual(result['count'], len(result['sessions']))
        self.assertEqual(cursor.execute.call_args_list[0].args[1][-1], 1)
        message = dict(id=7, user_id='chat_abc', ip_address='', user_message='Question', bot_response='Answer',
                       feedback=None, feedback_at=None, created_at=self.timestamp)
        self.database_rows(rows=[message])
        result = self.check(self.admin_open('/admin/api/chat/messages?user_id=chat_abc'), '/admin/api/chat/messages')
        self.assertEqual(result['count'], len(result['messages']))
        self.assertIn('2026-11-08 ', result['messages'][0]['created_at'])
        self.database_rows(first=dict(total_messages=2, total_sessions=1, total_ips=1, likes=1, dislikes=0))
        self.check(self.admin_open('/admin/api/chat/stats'), '/admin/api/chat/stats')
        self.database_rows(rows=[dict(id=7, _level='user.py', _from='chat', message='Completed',
                                      warninglevel=1, created_at=self.timestamp)])
        result = self.check(self.admin_open('/admin/api/logs'), '/admin/api/logs')
        self.assertEqual(result['logs'][0]['created_at'], 'Sun, 08 Nov 2026 12:00:00 GMT')
        for path in ('/admin/api/chat/sessions', '/admin/api/chat/messages', '/admin/api/logs'):
            self.database_rows()
            query = '?user_id=chat_abc' if path.endswith('messages') else ''
            result = self.check(self.admin_open(path + query), path)
            self.assertEqual(result.get('count', 0), 0)
        self.database_rows(rowcount=3)
        result = self.check(self.admin_open('/admin/api/chat/sessions?user_id=chat_abc', 'DELETE', headers=self.headers),
                            '/admin/api/chat/sessions', 'DELETE')
        self.assertEqual(result['deleted'], 3)
        result = self.check(self.admin_open('/admin/api/chat/logs?id=7', 'DELETE', headers=self.headers),
                            '/admin/api/chat/logs', 'DELETE')
        self.assertEqual(result['deleted'], 7)

    def test_documented_json_requests_reject_wrong_types_unknown_keys_and_wrong_media(self):
        for path, method, operation in self.contract.operations():
            content = operation.get('requestBody', {}).get('content', {}).get('application/json')
            if content is None:
                continue
            actual_path = path.replace('{user_id}', '7')
            client = self.admin_client if operation['x-service'] == 'admin' else self.ui_client
            origin = 'http://localhost:8000' if operation['x-service'] == 'admin' else 'http://localhost:5000'
            for payload in ([], {'undocumented': True}):
                with self.subTest(path=path, payload=payload):
                    with self.assertRaises(AssertionError):
                        self.contract.assert_schema(payload, content['schema'])
                    self.now += 1000
                    self.check(client.open(actual_path, method=method, base_url=origin, headers=self.headers,
                                            json=payload), path, method, 400)
            self.now += 1000
            self.check(client.open(actual_path, method=method, base_url=origin, headers=self.headers,
                                    data='{}', content_type='text/plain'), path, method, 415)

    def test_documented_admin_auth_permissions_and_csrf_are_enforced(self):
        for path, method, operation in self.contract.operations():
            if not path.startswith('/admin/api/'):
                continue
            actual_path = path.replace('{user_id}', '7').replace('{doc_id}', '7')
            with self.subTest(path=path, method=method):
                self.current_user = None
                self.check(self.admin_open(actual_path, method), path, method, 401)
                self.headers = self.authenticated_headers()
                self.current_user = {'id': 1, 'username': 'operator', 'is_active': True,
                                     'is_admin': False, 'permissions': [], 'created_at': None, 'updated_at': None}
                if operation['x-permission'] != 'authenticated':
                    self.check(self.admin_open(actual_path, method, headers=self.headers), path, method, 403)
                self.current_user['is_admin'] = True
                if operation['x-csrf-required']:
                    self.check(self.admin_open(actual_path, method), path, method, 403)

    def test_pagination_validation_matches_documented_limits(self):
        for path in ('/admin/api/documents', '/admin/api/pdf/documents'):
            for query in ('?page=0', '?page=10001', '?per_page=0', '?per_page=101',
                          '?page=1&page=2', '?unknown=1', '?per_page=true'):
                with self.subTest(path=path, query=query):
                    self.check(self.admin_open(path + query), path, status=400)
        for query in ('?limit=0', '?limit=1001', '?limit=1&limit=2'):
            self.check(self.admin_open('/admin/api/chat/sessions' + query), '/admin/api/chat/sessions', status=400)

    def test_error_envelopes_statuses_headers_and_readiness_variants(self):
        cases = [
            (self.ui_client.post('/chat', json={}), '/chat', 'POST', 400),
            (self.ui_client.post('/feedback', json={'message_id': 7, 'feedback': 'like'}), '/feedback', 'POST', 401),
            (self.ui_client.get('/health', headers={'Origin': 'null'}), '/health', 'GET', 403),
            (self.ui_client.post('/health'), '/health', 'GET', 405),
            (self.ui_client.post('/chat', data=b'x' * 65537, content_type='application/json'), '/chat', 'POST', 413),
        ]
        for response, path, method, status in cases:
            self.check(response, path, method, status)
        for status, code in ((418, 'request_failed'), (502, 'internal_error')):
            with self.ui_app.test_request_context('/health'):
                response = self.ui_app.process_response(self.ui_app.make_response(({'error': 'private details'}, status)))
                result = self.check(response, '/health', status=status)
                self.assertEqual(result['code'], code)
        with patch.object(self.lifecycle, 'select_model', side_effect=FileNotFoundError('private details')):
            self.check(self.admin_open('/admin/api/models', 'POST', headers=self.headers,
                                       json={'model_id': 'missing'}), '/admin/api/models', 'POST', 409)
        with patch.object(self.user, '_retrieve_top', side_effect=RuntimeError('private details')):
            self.check(self.ui_client.post('/chat', json={'message': 'question'}), '/chat', 'POST', 500)
        for state, code in (('loading', 'voice_loading'), ('unavailable', 'voice_unavailable'), ('error', 'voice_model_failed')):
            self.now += 1000
            with patch.object(self.voice, '_start_model_loading'), patch.object(self.voice, '_model_status', return_value=(state, None)):
                result = self.check(self.ui_client.get('/voice/health'), '/voice/health', status=503)
            self.assertEqual(result['code'], code)
        for state in ('loading', 'unavailable'):
            self.now += 1000
            self.speech.status.return_value = state
            self.check(self.ui_client.get('/tts/health'), '/tts/health', status=503)
        limiter = self.ui_app.extensions['abuse_controls']['chat']
        self.assertEqual(limiter.acquire('127.0.0.1'), 0)
        try:
            response = self.ui_client.post('/chat', json={'message': 'question'})
            result = self.check(response, '/chat', 'POST', 429)
            self.assertEqual(int(response.headers['Retry-After']), result['retry_after'])
        finally:
            limiter.release('127.0.0.1')
        self.database_rows(rowcount=0)
        self.check(self.admin_open('/admin/api/documents/7', 'DELETE', headers=self.headers),
                   '/admin/api/documents/{doc_id}', 'DELETE', 404)

    def test_login_logout_redirects_html_errors_and_legacy_entry_points(self):
        self.current_user = None
        self.check(self.admin_open('/login'), '/login')
        proof = self.login_proof()
        with patch.object(self.admin, 'authenticate', return_value=None):
            for attempt in range(6):
                status = 401 if attempt < 5 else 429
                response = self.admin_open('/login', 'POST', data={'username': 'operator', 'password': 'bad', 'csrf_token': proof})
                self.check(response, '/login', 'POST', status)
                self.assertEqual(response.headers['X-Error-Code'], 'authentication_required' if status == 401 else 'rate_limited')
        self.now += 1000
        with patch.object(self.admin, 'authenticate', return_value={'id': 1}), \
                patch.object(self.admin, 'create_admin_session', return_value='new-session'), \
                patch.object(self.admin, 'revoke_admin_session'):
            self.check(self.admin_open('/login', 'POST', data={'username': 'operator', 'password': 'correct', 'csrf_token': proof}),
                       '/login', 'POST', 302)
        self.headers = self.authenticated_headers()
        self.check(self.admin_open('/login'), '/login', status=302)
        with patch.object(self.admin, 'revoke_admin_session') as revoke:
            self.assertEqual(self.admin_open('/logout').status_code, 405)
            revoke.assert_not_called()
            self.check(self.admin_open('/logout', 'POST', headers=self.headers), '/logout', 'POST', 302)
            revoke.assert_called_once()
        self.check(self.ui_client.get('/index.html'), '/index.html', status=302)
        self.assertEqual(self.ui_client.get('/index.html').headers['Location'], '/')
        self.current_user = None
        for path in ('/login.css', '/login.js', '/login-theme.js'):
            response = self.admin_open(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['API-Version'], '1')

    def test_voice_frame_schemas_stop_flush_and_optional_socket_route(self):
        ready = Future()
        ready.set_result('transcript')
        stream = Mock(has_pending=False)
        stream.next_job.side_effect = [SimpleNamespace(segment_id=0, audio=None, is_final=True), None]
        self.sock.websocket = FakeSocket(['{"type":"stop","legacy_extra":true}'])
        with patch.object(self.voice, 'AudioStream', return_value=stream), \
                patch.object(self.voice._executor, 'submit', return_value=ready):
            self.assertEqual(self.voice_open().status_code, 204)
        schemas = {name.removeprefix('Voice').lower(): schema for name, schema in
                   self.contract.document['components']['schemas'].items() if name in {'VoiceReady', 'VoiceTranscript', 'VoiceDone', 'VoiceError'}}
        self.assertEqual([frame['type'] for frame in self.sock.websocket.sent], ['ready', 'transcript', 'done'])
        for frame in self.sock.websocket.sent:
            self.contract.assert_schema(frame, schemas[frame['type']])
        self.contract.assert_schema({'type': 'stop', 'legacy_extra': True},
                                    self.contract.document['components']['schemas']['VoiceStop'])
        self.sock.websocket = FakeSocket()
        with patch.object(self.voice, '_model_status', return_value=('unavailable', None)):
            self.voice_open()
        self.contract.assert_schema(self.sock.websocket.sent[0], schemas['error'])
        with patch.object(self.voice, 'Sock', None):
            app = importlib.import_module('application').create_ui_app(speech_service=self.speech)
        self.assertFalse(any(rule.rule == '/voice-stream' for rule in app.url_map.iter_rules()))
        self.assertEqual(app.test_client().get('/voice-stream', headers={'Origin': 'http://localhost:5000'}).status_code, 404)

    def test_version_header_is_available_on_errors_head_redirects_and_allowed_cors(self):
        response = self.ui_client.get('/health', headers={'Origin': 'http://localhost:5000'})
        self.assertEqual(response.headers['API-Version'], '1')
        self.assertIn('API-Version', response.headers['Access-Control-Expose-Headers'])
        response = self.ui_client.head('/health')
        self.assertEqual((response.status_code, response.data, response.headers['API-Version']), (200, b'', '1'))
        response = self.ui_client.get('/missing')
        self.assertEqual((response.status_code, response.headers['API-Version']), (404, '1'))
