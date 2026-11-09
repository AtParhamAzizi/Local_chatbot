"""Offline abuse-control regression tests with deterministic clocks and doubles."""

import hashlib
import hmac
import importlib
import json
import sys
import threading
import types
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import numpy as np
from flask import Response


BACKEND_DIR = Path(__file__).resolve().parents[1]
with patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path]):
    from abuse_controls import ClientLimiter


class ClientLimiterTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0

    def limiter(self, **options):
        return ClientLimiter(2, 60, clock=lambda: self.now, **options)

    def test_window_recovers_and_denials_never_extend_it(self):
        limiter = self.limiter()
        for attempt in range(2):
            self.assertEqual(limiter.acquire('client'), 0)
            limiter.release('client')
        self.assertEqual(limiter.acquire('client'), 60)
        self.now += 59.2
        self.assertEqual(limiter.acquire('client'), 1)
        self.now += 0.8
        self.assertEqual(limiter.acquire('client'), 0)

    def test_sliding_window_does_not_reset_early(self):
        limiter = self.limiter()
        self.assertEqual(limiter.acquire('client'), 0)
        limiter.release('client')
        self.now += 30
        self.assertEqual(limiter.acquire('client'), 0)
        limiter.release('client')
        self.now += 30
        self.assertEqual(limiter.acquire('client'), 0)
        limiter.release('client')
        self.assertEqual(limiter.acquire('client'), 30)

    def test_concurrent_admission_is_atomic(self):
        limiter = self.limiter()
        with ThreadPoolExecutor(max_workers=16) as executor:
            results = list(executor.map(limiter.acquire, ['client'] * 32))
        self.assertEqual(results.count(0), 1)
        self.assertTrue(all(delay == 5 for delay in results if delay))
        limiter.release('client')
        self.assertEqual(limiter.acquire('client'), 0)

    def test_capacity_is_bounded_without_evicting_live_limits(self):
        limiter = self.limiter(max_clients=2)
        for identity in ('first', 'second'):
            self.assertEqual(limiter.acquire(identity), 0)
            limiter.release(identity)
        for identity in range(100):
            self.assertGreater(limiter.acquire(str(identity)), 0)
        self.assertEqual(len(limiter.clients), 2)
        self.assertEqual(limiter.acquire('first'), 0)
        limiter.release('first')
        self.assertGreater(limiter.acquire('first'), 0)
        self.now += 60
        self.assertEqual(limiter.acquire('new'), 0)
        self.assertEqual(len(limiter.clients), 1)

    def test_active_requests_survive_expiry_and_capacity_cleanup(self):
        limiter = self.limiter(max_clients=1)
        self.assertEqual(limiter.acquire('active'), 0)
        self.now += 120
        self.assertGreater(limiter.acquire('new'), 0)
        self.assertGreater(limiter.acquire('active'), 0)
        limiter.release('active')
        self.assertEqual(limiter.acquire('new'), 0)


class TestConnectionClosed(Exception):
    pass


class FakeSocket:
    def __init__(self, messages=()):
        self.messages = iter(messages)
        self.sent = []

    def send(self, message):
        self.sent.append(json.loads(message))

    def receive(self, timeout=None):
        try:
            return next(self.messages)
        except StopIteration:
            raise TestConnectionClosed() from None


class FakeSock:
    def __init__(self, app):
        self.app = app
        self.websocket = FakeSocket()
        app.extensions['test_voice_socket'] = self

    def route(self, path):
        def decorate(function):
            def dispatch():
                function(self.websocket)
                return Response(status=204)
            self.app.add_url_rule(path, endpoint=function.__name__, view_func=dispatch)
            return function
        return decorate


class AbuseEndpointFixture:
    @classmethod
    def setUpClass(cls):
        cls.resources = ExitStack()
        cls.addClassCleanup(cls.resources.close)
        cls.resources.enter_context(patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path]))
        cls.resources.enter_context(patch.dict('os.environ', {
            'APP_ENV': 'test', 'UI_ALLOWED_ORIGINS': 'http://localhost:5000',
            'ADMIN_ALLOWED_ORIGINS': 'http://localhost:8000', 'TRUSTED_PROXY_HOPS': '0',
            'TRUSTED_PROXY_CIDRS': '', 'ADMIN_SESSION_SECRET': 'abuse-tests-only-not-a-deployment-secret',
        }, clear=True))

        def module_double(name, **attributes):
            module = types.ModuleType(name)
            module.__dict__.update(attributes)
            return module

        cls.forbidden_model = Mock(side_effect=AssertionError('Real model access is forbidden'))
        doubles = {
            'numpy': np,
            'interface': module_double('interface', embed_texts=cls.forbidden_model,
                                       embedding_dim=cls.forbidden_model, set_embedding_model=cls.forbidden_model,
                                       BASE_DIR=str(BACKEND_DIR), ADMIN_DIR=str(BACKEND_DIR / 'admin'),
                                       ADMIN_PORT=8000, UI_PORT=5000, MIN_SCORE=0.35,
                                       MAX_UPLOAD_BYTES=20 * 1024 * 1024, ALLOWED_EXT={'.json'}),
            'generator': module_double('generator', generate_reply=cls.forbidden_model,
                                       set_generator_model=cls.forbidden_model),
            'searchForAnswer': module_double('searchForAnswer', _search_pdf=cls.forbidden_model,
                                             _search_qa=cls.forbidden_model, build_context=cls.forbidden_model),
            'fitz': module_double('fitz', open=cls.forbidden_model),
            'faster_whisper': module_double('faster_whisper', WhisperModel=cls.forbidden_model),
            'flask_sock': module_double('flask_sock', Sock=FakeSock),
            'simple_websocket': module_double('simple_websocket', ConnectionClosed=TestConnectionClosed),
            'admin': None, 'user': None, 'voice': None,
        }
        cls.resources.enter_context(patch.dict(sys.modules, doubles))
        for name in ('admin', 'user', 'voice'):
            sys.modules.pop(name, None)
        from core_support import clear_admin_routes
        clear_admin_routes()
        database = cls.database = importlib.import_module('database')
        cls.resources.enter_context(patch.object(database.logger, '_write', return_value=True))
        cls.resources.enter_context(patch.object(
            database, 'get_conn', side_effect=AssertionError('Real database access is forbidden'),
        ))
        cls.admin = importlib.import_module('admin_routes.auth')
        cls.admin_accounts = importlib.import_module('admin_routes.accounts')
        cls.user = importlib.import_module('user')
        cls.voice = importlib.import_module('voice')
        application = importlib.import_module('application')
        cls.admin_app = application.create_admin_app()
        cls.ui_app = application.create_ui_app()
        cls.ingestion = cls.admin_app.extensions['ingestion_service']
        from core_support import LegacyIngestionJobs
        cls.admin_app.extensions['ingestion_jobs'] = LegacyIngestionJobs(cls.ingestion)
        cls.lifecycle = cls.admin_app.extensions['model_lifecycle']

    def setUp(self):
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        self.now = 100.0
        self.current_user = None
        self.forbidden_model.reset_mock()
        self.resources.enter_context(patch.object(
            self.admin, 'load_admin_session', side_effect=lambda token: self.current_user,
        ))
        self.resources.enter_context(patch.object(self.voice, '_model_status', return_value=('ready', None)))
        self.slot = threading.BoundedSemaphore(1)
        self.resources.enter_context(patch.object(self.voice, '_transcription_slot', self.slot))
        for app in (self.admin_app, self.ui_app):
            app.extensions['abuse_controls'] = {
                name: ClientLimiter(limiter.limit, limiter.window, clock=lambda: self.now)
                for name, limiter in app.extensions['abuse_controls'].items()
            }
        self.admin_client = self.admin_app.test_client()
        self.ui_client = self.ui_app.test_client()
        self.sock = self.ui_app.extensions['test_voice_socket']
        self.sock.websocket = FakeSocket()

    def tearDown(self):
        self.forbidden_model.assert_not_called()

    def admin_open(self, path, method='GET', client=None, address='192.0.2.1', **options):
        return (client or self.admin_client).open(
            path, method=method, base_url='http://localhost:8000',
            environ_overrides={'REMOTE_ADDR': address}, **options,
        )

    def voice_open(self, **options):
        return self.ui_client.get('/voice-stream', headers={'Origin': 'http://localhost:5000'}, **options)

    def login_proof(self, client=None):
        client = client or self.admin_client
        self.assertEqual(self.admin_open('/login', client=client).status_code, 200)
        with client.session_transaction() as signed_session:
            return signed_session['_login_csrf_token']

    def authenticated_headers(self):
        self.current_user = {'id': 1, 'username': 'operator', 'is_active': True, 'is_admin': True, 'permissions': []}
        token = 's' * 43
        with self.admin_client.session_transaction() as signed_session:
            signed_session['admin_session'] = token
        proof = hmac.new(
            self.admin_app.secret_key.encode(), b'admin-csrf\x00' + token.encode(), hashlib.sha256,
        ).hexdigest()
        return {'X-CSRF-Token': proof}

    def assert_limited(self, response, *, html=False):
        self.assertEqual(response.status_code, 429)
        retry_after = int(response.headers['Retry-After'])
        self.assertGreater(retry_after, 0)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        if html:
            self.assertIn(f'retry in {retry_after} seconds', response.get_data(as_text=True))
            self.assertIn('name="csrf_token"', response.get_data(as_text=True))
        else:
            self.assertEqual(response.json['retry_after'], retry_after)
            self.assertEqual(response.json['code'], 'rate_limited')
        return retry_after


class AbuseEndpointTests(AbuseEndpointFixture, unittest.TestCase):
    def test_login_limit_survives_cookie_and_username_changes_and_recovers(self):
        proof = self.login_proof()
        with patch.object(self.admin, 'authenticate', return_value=None) as authenticate:
            for attempt in range(5):
                response = self.admin_open('/login', 'POST', data={
                    'username': f'account-{attempt}', 'password': 'wrong', 'csrf_token': proof,
                })
                self.assertEqual(response.status_code, 401)
            other_browser = self.admin_app.test_client()
            other_proof = self.login_proof(other_browser)
            response = self.admin_open('/login', 'POST', client=other_browser, data={
                'username': 'target', 'password': 'correct', 'csrf_token': other_proof,
            }, headers={'X-Forwarded-For': '198.51.100.2'})
            self.assertEqual(self.assert_limited(response, html=True), 60)
            self.assertEqual(authenticate.call_count, 5)
            self.now += 59.2
            response = self.admin_open('/login', 'POST', data={
                'username': 'target', 'password': 'correct', 'csrf_token': proof,
            })
            self.assertEqual(self.assert_limited(response, html=True), 1)
            self.now += 0.8
            authenticate.return_value = {'id': 1, 'session_version': 1}
            with patch.object(self.admin, 'create_admin_session', return_value='new-session'), \
                    patch.object(self.admin, 'revoke_admin_session'):
                response = self.admin_open('/login', 'POST', data={
                    'username': 'target', 'password': 'correct', 'csrf_token': proof,
                })
            self.assertEqual(response.status_code, 302)
            self.assertEqual(authenticate.call_count, 6)

    def test_attacking_an_account_does_not_lock_out_another_client(self):
        proof = self.login_proof()
        with patch.object(self.admin, 'authenticate', return_value=None):
            for attempt in range(5):
                self.admin_open('/login', 'POST', data={
                    'username': 'target', 'password': 'wrong', 'csrf_token': proof,
                })
        ordinary_client = self.admin_app.test_client()
        ordinary_proof = self.login_proof(ordinary_client)
        with patch.object(self.admin, 'authenticate', return_value={'id': 1}), \
                patch.object(self.admin, 'create_admin_session', return_value='ordinary-session'), \
                patch.object(self.admin, 'revoke_admin_session'):
            response = self.admin_open('/login', 'POST', client=ordinary_client, address='192.0.2.2', data={
                'username': 'target', 'password': 'correct', 'csrf_token': ordinary_proof,
            })
        self.assertEqual(response.status_code, 302)

    def test_chat_limits_before_retrieval_and_recovers(self):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.lastrowid = 1
        with patch.object(self.user, '_retrieve_top', return_value=[]) as retrieve, \
                patch.object(self.user, 'get_conn', return_value=connection):
            for attempt in range(20):
                response = self.ui_client.post('/chat', json={'message': 'hello', 'user_id': str(attempt)})
                self.assertEqual(response.status_code, 200)
            response = self.ui_client.post('/chat', json={'message': 'hello', 'user_id': 'new'}, headers={
                'X-Forwarded-For': '192.0.2.8', 'Origin': 'http://localhost:5000',
            })
            self.assertEqual(self.assert_limited(response), 60)
            self.assertEqual(response.headers['Access-Control-Expose-Headers'], 'Retry-After, API-Version')
            self.assertEqual(retrieve.call_count, 20)
            self.assertEqual(self.ui_client.get('/health').status_code, 200)
            self.now += 60
            self.assertEqual(self.ui_client.post('/chat', json={'message': 'hello'}).status_code, 200)
            self.assertEqual(retrieve.call_count, 21)

    def test_chat_concurrency_releases_after_handler_failure(self):
        blocked = []

        def retrieve(*args, **kwargs):
            blocked.append(self.ui_client.post('/chat', json={'message': 'parallel'}))
            raise RuntimeError('retrieval failed')

        with patch.object(self.user, '_retrieve_top', side_effect=retrieve):
            self.assertEqual(self.ui_client.post('/chat', json={'message': 'first'}).status_code, 500)
        self.assert_limited(blocked[0])
        self.assertEqual(self.ui_client.post('/chat', json={'message': ''}).status_code, 400)

    def test_uploads_share_one_limit_and_keep_authentication_and_csrf(self):
        paths = ('/admin/api/upload', '/admin/api/pdf/upload')
        for path in paths:
            self.assertEqual(self.admin_open(path, 'POST').status_code, 401)
        headers = self.authenticated_headers()
        for path in paths:
            self.assertEqual(self.admin_open(path, 'POST').status_code, 403)
        for attempt in range(6):
            response = self.admin_open(paths[attempt % 2], 'POST', headers=headers, content_type='multipart/form-data')
            self.assertEqual(response.status_code, 400)
        for path in paths:
            self.assertEqual(self.assert_limited(self.admin_open(path, 'POST', headers=headers)), 300)
        response = self.admin_open(paths[0], 'POST', headers=headers, address='192.0.2.2', content_type='multipart/form-data')
        self.assertEqual(response.status_code, 400)
        self.now += 300
        self.assertEqual(self.admin_open(paths[1], 'POST', headers=headers, content_type='multipart/form-data').status_code, 400)

    def test_voice_admission_is_before_upgrade_and_origin_rejection_does_not_consume_it(self):
        for attempt in range(8):
            self.assertEqual(self.ui_client.get('/voice-stream').status_code, 403)
        for attempt in range(6):
            self.assertEqual(self.voice_open().status_code, 204)
        self.assertEqual(self.assert_limited(self.voice_open()), 60)
        self.now += 60
        self.assertEqual(self.voice_open().status_code, 204)

    def test_voice_health_polling_has_a_separate_recoverable_limit(self):
        with patch.object(self.voice, '_start_model_loading') as load:
            for attempt in range(60):
                self.assertEqual(self.ui_client.get('/voice/health').status_code, 200)
            self.assert_limited(self.ui_client.get('/voice/health'))
            self.assertEqual(load.call_count, 60)
            self.assertEqual(self.voice_open().status_code, 204)
            self.now += 60
            self.assertEqual(self.ui_client.get('/voice/health').status_code, 200)

    def test_voice_audio_budget_stops_fast_senders_and_releases_slots(self):
        packet = bytes(self.voice.MAX_PACKET_BYTES)
        self.sock.websocket = FakeSocket([packet, packet])
        with patch.object(self.voice, 'MAX_SESSION_AUDIO_BYTES', len(packet)):
            self.assertEqual(self.voice_open().status_code, 204)
        error = self.sock.websocket.sent[-1]
        self.assertEqual(error['code'], 'rate_limited')
        self.assertEqual(error['retry_after'], 5)
        self.assertIn('audio limit', error['message'])
        self.assertTrue(self.slot.acquire(blocking=False))
        self.slot.release()
        self.sock.websocket = FakeSocket(['{"type":"stop"}'])
        self.assertEqual(self.voice_open().status_code, 204)
        self.assertEqual(self.sock.websocket.sent[-1]['type'], 'done')

    def test_voice_duration_limit_keeps_pending_work_in_global_slot_until_complete(self):
        pending = Future()
        audio_stream = Mock()
        audio_stream.next_job.return_value = types.SimpleNamespace(audio='samples')
        self.sock.websocket = FakeSocket([None])
        with patch.object(self.voice, 'AudioStream', return_value=audio_stream), \
                patch.object(self.voice._executor, 'submit', return_value=pending), \
                patch.object(self.voice.time, 'monotonic', side_effect=[100, 100, 100, 220]):
            self.assertEqual(self.voice_open().status_code, 204)
        self.assertIn('time limit', self.sock.websocket.sent[-1]['message'])
        self.assertFalse(self.slot.acquire(blocking=False))
        self.sock.websocket = FakeSocket()
        self.assertEqual(self.voice_open().status_code, 204)
        self.assertEqual(self.sock.websocket.sent[-1]['retry_after'], 5)
        pending.set_result('finished')
        self.assertTrue(self.slot.acquire(blocking=False))
        self.slot.release()

    def test_mapped_ipv4_and_ipv4_share_the_same_limit(self):
        for attempt in range(20):
            self.assertEqual(self.ui_client.post('/chat', json={'message': ''}, environ_overrides={
                'REMOTE_ADDR': '::ffff:192.0.2.1',
            }).status_code, 400)
        response = self.ui_client.post('/chat', json={'message': ''}, environ_overrides={'REMOTE_ADDR': '192.0.2.1'})
        self.assert_limited(response)


if __name__ == '__main__':
    unittest.main()
