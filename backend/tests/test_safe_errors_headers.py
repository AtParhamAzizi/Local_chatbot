"""Public failure contracts and response headers with offline application doubles."""

import importlib
import json
import unittest
from concurrent.futures import Future
from unittest.mock import Mock, patch

from flask import Flask, Response
from pymysql.err import IntegrityError
from werkzeug.exceptions import BadRequest, MethodNotAllowed

from test_abuse_controls import AbuseEndpointFixture


INTERNALS = 'mysql://private:password@db/private_table C:/private/model/weights.bin SQL SELECT traceback'


class SafeErrorsHeadersTests(AbuseEndpointFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.real_model_status = staticmethod(cls.voice._model_status)

    def assert_public(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.json['code'], code)
        self.assertEqual(response.headers['X-Error-Code'], code)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertNotIn(INTERNALS, response.get_data(as_text=True))
        self.assertNotIn('details', response.json)
        self.assertNotIn('traceback', response.json)

    def test_unhandled_and_http_exceptions_do_not_reflect_descriptions(self):
        cases = [(RuntimeError(INTERNALS), 500, 'internal_error'),
                 (BadRequest(INTERNALS), 400, 'invalid_request'),
                 (MethodNotAllowed(['GET'], description=INTERNALS), 405, 'method_not_allowed')]
        for error, status, code in cases:
            with self.subTest(error=type(error).__name__), patch.dict(
                self.ui_app.view_functions, {'health': Mock(side_effect=error)},
            ):
                response = self.ui_client.get('/health')
                self.assert_public(response, status, code)
                if status == 405:
                    self.assertIn('GET', response.headers['Allow'])

    def test_returned_errors_are_allowlisted_not_just_secret_redacted(self):
        for body, mimetype in [
            (json.dumps({'error': INTERNALS, 'details': [INTERNALS], 'code': INTERNALS,
                         'status': [INTERNALS], 'traceback': INTERNALS}), 'application/json'),
            (json.dumps([INTERNALS]), 'application/json'),
            ('{invalid-json ' + INTERNALS, 'application/json'),
            ('<html>' + INTERNALS + '</html>', 'text/html'),
        ]:
            with self.subTest(mimetype=mimetype, body=body[:30]), patch.dict(
                self.ui_app.view_functions,
                {'health': lambda: Response(body, status=500, mimetype=mimetype)},
            ):
                self.assert_public(self.ui_client.get('/health'), 500, 'internal_error')

    def test_real_database_model_and_account_failures_are_public(self):
        headers = self.authenticated_headers()
        with patch.object(self.user, '_retrieve_top', side_effect=RuntimeError(INTERNALS)):
            self.assert_public(self.ui_client.post('/chat', json={'message': 'hello'}), 500, 'internal_error')
        with patch.object(self.admin, 'get_conn', side_effect=RuntimeError(INTERNALS)):
            self.assert_public(self.admin_open('/admin/api/documents'), 500, 'internal_error')
        with patch.object(self.admin, 'set_active_model', side_effect=FileNotFoundError(INTERNALS)):
            self.assert_public(self.admin_open('/admin/api/models', 'POST', headers=headers,
                                              json={'model_id': 'test'}), 409, 'conflict')
        for error, status, code in [(ValueError(INTERNALS), 400, 'invalid_request'),
                                    (IntegrityError(1062, INTERNALS), 409, 'conflict'),
                                    (IntegrityError(1048, INTERNALS), 500, 'internal_error')]:
            with self.subTest(error=error), patch.object(self.admin, 'create_user', side_effect=error):
                response = self.admin_open('/admin/api/admin-users', 'POST', headers=headers,
                                           json={'username': 'operator2', 'password': 'test-password'})
                self.assert_public(response, status, code)

    def test_authentication_authorization_and_validation_have_codes(self):
        self.assert_public(self.admin_open('/admin/api/session'), 401, 'authentication_required')
        self.authenticated_headers()
        self.assert_public(self.admin_open('/admin/api/models', 'POST', json={'model_id': 'test'}), 403, 'forbidden')
        self.assert_public(self.ui_client.post('/chat', json={'message': []}), 400, 'invalid_request')
        self.assert_public(self.ui_client.get('/missing.js'), 404, 'not_found')
        self.assert_public(self.ui_client.post('/chat', data='{}'), 415, 'unsupported_media_type')
        self.assert_public(self.ui_client.post('/chat', data='x' * 65537), 413, 'payload_too_large')

    def test_head_and_origin_denials_keep_safe_headers(self):
        response = self.ui_client.head('/missing.js')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data, b'')
        self.assertEqual(response.headers['X-Error-Code'], 'not_found')
        self.assert_headers(response, 'ui')
        denied = self.ui_client.get('/health', headers={'Origin': 'null'})
        self.assert_public(denied, 403, 'forbidden')
        self.assert_headers(denied, 'ui')
        self.assertNotIn('Access-Control-Allow-Origin', denied.headers)

    def test_login_failures_keep_forms_with_public_header_codes(self):
        proof = self.login_proof()
        with patch.object(self.admin, 'authenticate', return_value=None):
            for attempt in range(6):
                response = self.admin_open('/login', 'POST', data={
                    'username': 'operator', 'password': 'wrong-password', 'csrf_token': proof,
                })
                self.assertEqual(response.status_code, 401 if attempt < 5 else 429)
                self.assertEqual(response.headers['X-Error-Code'],
                                 'authentication_required' if attempt < 5 else 'rate_limited')
                self.assertEqual(response.mimetype, 'text/html')
                self.assertIn('name="csrf_token"', response.get_data(as_text=True))
                self.assert_headers(response, 'admin')

    def test_voice_health_and_websocket_failures_have_public_codes(self):
        with patch.object(self.voice, '_start_model_loading'), patch.object(
            self.voice, '_model_status', return_value=('error', INTERNALS),
        ):
            self.assert_public(self.ui_client.get('/voice/health'), 503, 'voice_model_failed')
            self.voice_open()
        failure = self.sock.websocket.sent[-1]
        self.assertEqual(failure['code'], 'voice_model_failed')
        self.assertNotIn(INTERNALS, json.dumps(failure))
        future = Future()
        future.set_exception(RuntimeError(INTERNALS))
        with patch.object(self.voice, 'IMPORT_ERROR', None), patch.object(self.voice, '_model_future', future):
            self.assertEqual(self.real_model_status(), ('error', 'Voice model failed to load.'))
        for status, code in (('loading', 'voice_loading'), ('unavailable', 'voice_unavailable')):
            with patch.object(self.voice, '_model_status', return_value=(status, INTERNALS)), \
                    patch.object(self.voice, '_start_model_loading'):
                response = self.ui_client.get('/voice/health')
                self.assert_public(response, 503, code)
                self.assertEqual(response.json['status'], status)
                self.assertNotIn('model', response.json)

    def test_transcription_exceptions_have_public_websocket_codes(self):
        with patch.object(self.voice, 'AudioStream', side_effect=RuntimeError(INTERNALS)):
            self.voice_open()
        failure = self.sock.websocket.sent[-1]
        self.assertEqual(failure['code'], 'transcription_failed')
        self.assertNotIn(INTERNALS, json.dumps(failure))

    def assert_headers(self, response, service):
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response.headers['Referrer-Policy'], 'strict-origin-when-cross-origin')
        self.assertEqual(response.headers['X-Frame-Options'], 'SAMEORIGIN' if service == 'admin' else 'DENY')
        ancestors = "'self'" if service == 'admin' else "'none'"
        self.assertEqual(response.headers['Content-Security-Policy'], f'frame-ancestors {ancestors}')
        self.assertIn('microphone=(self)' if service == 'ui' else 'microphone=()',
                      response.headers['Permissions-Policy'])
        candidate = response.headers['Content-Security-Policy-Report-Only']
        for directive in ("script-src 'self'", "frame-src 'self' blob:", "worker-src 'self' blob:"):
            self.assertIn(directive, candidate)
        self.assertNotIn('unsafe-eval', candidate)
        self.assertNotIn('upgrade-insecure-requests', candidate)

    def test_headers_cover_pages_assets_redirects_errors_and_preflight(self):
        for path in ('/health', '/', '/script.js', '/style.css', '/voice/audio-worklet.js', '/missing'):
            response = self.ui_client.get(path)
            self.assert_headers(response, 'ui')
            self.assertNotIn('Strict-Transport-Security', response.headers)
            response.close()
        self.assert_headers(self.admin_open('/'), 'admin')
        self.assert_headers(self.admin_open('/login'), 'admin')
        self.authenticated_headers()
        for path in ('/', '/admin/admin.html', '/admin/admin_pdf.html', '/admin-csrf.js', '/theme.js'):
            response = self.admin_open(path)
            self.assertEqual(response.status_code, 200)
            self.assert_headers(response, 'admin')
            response.close()
        preflight = self.ui_client.options('/chat', headers={
            'Origin': 'http://localhost:5000', 'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'Content-Type',
        })
        self.assert_headers(preflight, 'ui')
        self.assertEqual(preflight.headers['Access-Control-Allow-Origin'], 'http://localhost:5000')

    def test_javascript_mime_and_csp_allow_worklet_and_websocket(self):
        for path in ('/script.js', '/voice/audio-worklet.js'):
            response = self.ui_client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(response.mimetype, {'text/javascript', 'application/javascript'})
            self.assertIn('ws://localhost:5000', response.headers['Content-Security-Policy-Report-Only'])
            response.close()

    def test_hsts_requires_production_and_validated_https_on_both_apps(self):
        security = importlib.import_module('security')
        with patch.object(security, 'is_development', return_value=False):
            for client, path in ((self.ui_client, '/health'), (self.admin_client, '/login')):
                self.assertNotIn('Strict-Transport-Security', client.get(path).headers)
                spoofed = client.get(path, headers={'X-Forwarded-Proto': 'https'})
                self.assertNotIn('Strict-Transport-Security', spoofed.headers)
                secure = client.get(path, base_url='https://localhost')
                self.assertEqual(secure.headers['Strict-Transport-Security'], 'max-age=31536000')

    def test_hsts_uses_only_an_explicitly_trusted_forwarding_chain(self):
        security = importlib.import_module('security')
        app = Flask('trusted_https_headers')
        with patch.dict('os.environ', {'TRUSTED_PROXY_HOPS': '1', 'TRUSTED_PROXY_CIDRS': '192.0.2.10'}):
            security.configure_security(app, service='ui')
        app.add_url_rule('/health', view_func=lambda: {'status': 'ok'})
        with patch.object(security, 'is_development', return_value=False):
            for peer, expected in (('192.0.2.10', True), ('192.0.2.11', False)):
                response = app.test_client().get('/health', headers={
                    'X-Forwarded-For': '198.51.100.1', 'X-Forwarded-Proto': 'https',
                }, environ_overrides={'REMOTE_ADDR': peer})
                self.assertEqual('Strict-Transport-Security' in response.headers, expected)


if __name__ == '__main__':
    unittest.main()
