"""Verify the browser fixture's real Flask guards and disposable in-memory data."""

import os
import re
import subprocess
import sys
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pymysql

from browser_support import BrowserApps, PASSWORD, REPLY
import browser_smoke


class BrowserFixtureTests(unittest.TestCase):
    def setUp(self):
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        self.apps = self.resources.enter_context(BrowserApps('http://localhost:5000', 'http://localhost:8000'))
        self.user = self.apps.ui_app.test_client()
        self.admin = self.apps.admin_app.test_client()
        self.headers = {'X-Browser-Fixture': self.apps.token}

    def admin_open(self, path, method='GET', **options):
        return self.admin.open(path, method=method, base_url='http://localhost:8000', **options)

    def login(self, username='browser-admin', password=PASSWORD):
        form = self.admin_open('/login')
        token = re.search(r'name="csrf_token" value="([^"]+)"', form.get_data(as_text=True))[1]
        return self.admin_open('/login', 'POST', data={
            'username': username, 'password': password, 'csrf_token': token,
        })

    def test_fixture_controls_require_a_runner_issued_token(self):
        self.apps.sessions['keep'] = {'id': 1}
        self.assertEqual(self.user.post('/__browser__/reset', json={}).status_code, 404)
        self.assertEqual(self.user.get('/__browser__/state').status_code, 404)
        self.assertIn('keep', self.apps.sessions)
        response = self.user.post('/__browser__/reset', json={}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.apps.sessions, {})

    def test_login_checks_credentials_and_logout_revokes_the_session(self):
        self.assertEqual(self.login(password='incorrect').status_code, 401)
        self.assertEqual(len(self.apps.sessions), 0)
        self.assertEqual(self.login().status_code, 302)
        current = self.admin_open('/admin/api/session')
        self.assertEqual(current.status_code, 200)
        self.assertTrue(current.json['user']['is_admin'])
        self.assertEqual(len(self.apps.sessions), 1)
        self.assertEqual(self.admin_open('/logout', 'POST').status_code, 403)
        self.assertEqual(self.admin_open('/logout', 'POST', headers={
            'X-CSRF-Token': current.json['csrf_token'],
        }).status_code, 302)
        self.assertEqual(len(self.apps.sessions), 0)
        self.assertEqual(self.admin_open('/admin/api/session').status_code, 401)

    def test_login_theme_script_is_public_without_exposing_dashboard_assets(self):
        response = self.admin_open('/login-theme.js')
        self.assertEqual(response.status_code, 200)
        self.assertIn('javascript', response.mimetype)
        self.assertIn('liquid-theme', response.get_data(as_text=True))
        self.assertEqual(self.admin_open('/theme.js').status_code, 302)
        self.assertEqual(self.admin_open('/dashboard.js').status_code, 302)

    def test_feature_and_empty_roles_use_real_operation_policy(self):
        self.assertEqual(self.login(username='browser-pdf').status_code, 302)
        response = self.admin_open('/admin/api/session')
        self.assertEqual(response.json['allowed_pages'], ['admin/admin_pdf.html'])
        self.assertEqual(self.admin_open('/admin/admin_pdf.html').status_code, 200)
        self.assertEqual(self.admin_open('/admin/admin.html').status_code, 403)
        self.assertEqual(self.admin_open('/admin/api/admin-users').status_code, 403)
        self.admin = self.apps.admin_app.test_client()
        self.assertEqual(self.login(username='browser-empty').status_code, 302)
        self.assertEqual(self.admin_open('/admin/api/session').json['allowed_pages'], [])

    def test_feedback_failure_is_retryable_and_keeps_ownership_checks(self):
        self.assertEqual(self.user.post('/__browser__/reset', json={
            'feedback_failure': True,
        }, headers=self.headers).status_code, 200)
        chat = self.user.post('/chat', json={'message': 'browser journey'})
        self.assertEqual(chat.status_code, 200)
        self.assertEqual(chat.json['reply'], REPLY)
        message_id = chat.json['message_id']
        payload = {'message_id': message_id, 'feedback': 'like'}
        self.assertEqual(self.user.post('/feedback', json=payload).status_code, 503)
        self.assertIsNone(self.apps.history.rows[message_id]['feedback'])
        self.assertEqual(self.user.post('/feedback', json=payload).status_code, 200)
        self.assertEqual(self.apps.history.rows[message_id]['feedback'], 'like')
        stranger = self.apps.ui_app.test_client()
        self.assertEqual(stranger.post('/chat', json={'message': 'other owner'}).status_code, 200)
        self.assertEqual(stranger.post('/feedback', json=payload).status_code, 404)
        self.assertEqual(self.apps.feedback_attempts, 3)

    def test_voice_failure_does_not_disable_chat(self):
        self.assertEqual(self.user.post('/__browser__/reset', json={
            'voice_unavailable': True,
        }, headers=self.headers).status_code, 200)
        health = self.user.get('/voice/health')
        self.assertEqual(health.status_code, 503)
        self.assertEqual(health.json['code'], 'voice_unavailable')
        self.assertEqual(self.user.post('/chat', json={'message': 'typed message'}).status_code, 200)
        self.assertEqual(len(self.apps.history.rows), 1)


class BrowserFixtureIsolationTests(unittest.TestCase):
    def test_swallowed_forbidden_work_fails_fixture_and_restores_environment(self):
        original_connect = pymysql.connect
        original_path = sys.path
        with patch.dict(os.environ, {'DB_HOST': 'production.invalid', 'APP_ENV': 'production'}):
            with self.assertRaises(AssertionError):
                with BrowserApps('http://localhost:5000', 'http://localhost:8000'):
                    self.assertEqual(os.environ['DB_HOST'], 'browser-tests.invalid')
                    try:
                        pymysql.connect(host='production.invalid')
                    except AssertionError:
                        pass
            self.assertEqual(os.environ['DB_HOST'], 'production.invalid')
            self.assertEqual(os.environ['APP_ENV'], 'production')
        self.assertIs(pymysql.connect, original_connect)
        self.assertIs(sys.path, original_path)


class BrowserRunnerTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(sys, 'argv', ['browser_smoke.py']))
        self.enterContext(patch.object(browser_smoke.shutil, 'which', return_value='test-node'))
        self.enterContext(patch.object(browser_smoke, 'print', create=True))

    def test_second_bind_failure_closes_the_first_socket(self):
        server = Mock()
        with patch.object(browser_smoke, 'make_server', side_effect=[server, OSError('Bind failed')]):
            self.assertEqual(browser_smoke.main(), 2)
        server.server_close.assert_called_once()

    def test_timeout_cleans_servers_threads_fixture_and_strips_deployment_secrets(self):
        servers = [Mock(server_port=51001), Mock(server_port=51002)]
        workers = [Mock(), Mock()]
        fixture = MagicMock()
        fixture.__enter__.return_value = SimpleNamespace(token='a' * 64, ui_app=object(), admin_app=object())
        with patch.dict(os.environ, {'DB_PASS': 'production-secret', 'ADMIN_SESSION_SECRET': 'deployment-secret'}), \
                patch.object(browser_smoke, 'make_server', side_effect=servers), \
                patch.object(browser_smoke, 'BrowserApps', return_value=fixture), \
                patch.object(browser_smoke.threading, 'Thread', side_effect=workers), \
                patch.object(browser_smoke.subprocess, 'run', side_effect=subprocess.TimeoutExpired('test-node', 300)) as run:
            self.assertEqual(browser_smoke.main(), 2)
        environment = run.call_args.kwargs['env']
        self.assertNotIn('DB_PASS', environment)
        self.assertNotIn('ADMIN_SESSION_SECRET', environment)
        self.assertEqual(environment['BROWSER_UI_URL'], 'http://127.0.0.1:51001')
        self.assertEqual(environment['BROWSER_ADMIN_URL'], 'http://127.0.0.1:51002')
        for server in servers:
            server.shutdown.assert_called_once()
            server.server_close.assert_called_once()
        for worker in workers:
            worker.join.assert_called_once_with(5)
        fixture.__exit__.assert_called_once()
