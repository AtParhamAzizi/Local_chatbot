"""Isolated Flask applications for browser journeys, with no real DB or models."""

import importlib
import secrets
import sys
import threading
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import pymysql
from flask import jsonify, request
from pymysql.err import OperationalError

from core_support import module_double
from run_tests import offline_guards
from test_chat_ownership import MemoryHistory


BACKEND_DIR = Path(__file__).resolve().parents[1]
PASSWORD = 'browser-tests-only-password'
REPLY = '\u067e\u0627\u0633\u062e \u0622\u0632\u0645\u0627\u06cc\u0634\u06cc'
FOLLOW_UP = '\u0633\u0648\u0627\u0644 \u0628\u0639\u062f\u06cc'


class BrowserApps:
    def __init__(self, ui_origin, admin_origin):
        self.ui_origin = ui_origin
        self.admin_origin = admin_origin
        self.token = secrets.token_hex(32)
        self.history = MemoryHistory()
        self.sessions = {}
        self.lock = threading.RLock()
        self.feedback_attempts = 0
        self.feedback_failures = 0
        self.voice_status = 'ready'
        self.forbidden = Mock(side_effect=AssertionError('Real database/model work is forbidden'))

    def __enter__(self):
        self.resources = ExitStack()
        try:
            self.resources.enter_context(offline_guards())
            self.resources.enter_context(patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path]))
            self.resources.enter_context(patch.dict('os.environ', {
                'APP_ENV': 'test', 'ADMIN_SESSION_SECRET': secrets.token_hex(32),
                'UI_ALLOWED_ORIGINS': self.ui_origin, 'ADMIN_ALLOWED_ORIGINS': self.admin_origin,
                'DB_HOST': 'browser-tests.invalid', 'DB_NAME': 'browser_tests_never_connect',
                'DB_USER': 'browser_tests', 'DB_PASS': secrets.token_hex(32),
                'STT_LOCAL_FILES_ONLY': '1',
            }, clear=True))
            doubles = {
                'interface': module_double(
                    'interface', BASE_DIR=str(BACKEND_DIR), ADMIN_DIR=str(BACKEND_DIR / 'admin'),
                    ADMIN_PORT=8000, UI_PORT=5000, MIN_SCORE=0.35, ALLOWED_EXT={'.json'},
                    embed_texts=self.forbidden, embedding_dim=self.forbidden,
                    set_embedding_model=self.forbidden,
                ),
                'generator': module_double('generator', generate_reply=Mock(return_value=REPLY),
                                           set_generator_model=self.forbidden),
                'voice': module_double('voice', configure_voice=self.configure_voice),
                'fitz': module_double('fitz', open=self.forbidden),
            }
            self.resources.enter_context(patch.dict(sys.modules, doubles))
            for name in ('database', 'settings', 'admin', 'user', 'security', 'admin_users',
                         'admin_sessions', 'chat_sessions', 'admin_csrf', 'abuse_controls',
                         'request_validation', 'searchForAnswer', 'model_registry', 'serving'):
                sys.modules.pop(name, None)
            self.database = importlib.import_module('database')
            self.resources.enter_context(patch.object(self.database, 'get_conn', self.forbidden))
            self.resources.enter_context(patch.object(self.database.logger, '_write', return_value=True))
            for name in ('connect', 'Connect', 'Connection'):
                self.resources.enter_context(patch.object(pymysql, name, self.forbidden))
            self.resources.enter_context(patch.object(pymysql.connections, 'Connection', self.forbidden))
            self.accounts = importlib.import_module('admin_users')
            self.admin = importlib.import_module('admin')
            self.user = importlib.import_module('user')
            self.ui_app = self.user.ui_app
            self.admin_app = self.admin.admin_app
            password_hash = self.accounts._hash_password(PASSWORD)
            self.rows = {
                username: dict(id=account_id, username=username, password_hash=password_hash,
                               is_active=True, is_admin=administrator, permissions=permissions,
                               session_version=1)
                for account_id, username, administrator, permissions in (
                    (1, 'browser-admin', True, []),
                    (2, 'browser-pdf', False, ['pdf_upload']),
                    (3, 'browser-empty', False, []),
                )
            }
            self.resources.enter_context(patch.object(self.accounts, 'find_user',
                                                       side_effect=lambda username: self.rows.get(username)))
            self.admin.create_admin_session = self.create_session
            self.admin.load_admin_session = self.load_session
            self.admin.revoke_admin_session = self.revoke_session
            self.user.get_conn = self.connect_history
            self.user._retrieve_top = Mock(return_value=[
                dict(source_type='qa', score=1.0, question='Browser question', answer=REPLY),
                dict(source_type='qa', score=0.9, question=FOLLOW_UP, answer=REPLY),
            ])
            responses = {
                'admin_stats': {'total_documents': 0, 'embedded_documents': 0, 'dim': 3},
                'admin_documents': {'items': [], 'total': 0, 'page': 1, 'per_page': 10},
                'admin_pdf_stats': {'documents': 0, 'chunks': 0, 'embedded_chunks': 0, 'dim': 3},
                'admin_pdf_documents': {'items': [], 'total': 0, 'page': 1, 'per_page': 10},
                'admin_models': {'models': [], 'active_model': 'browser-test'},
                'admin_chat_sessions': {'sessions': [], 'count': 0},
                'admin_chat_stats': {'total_messages': 0, 'total_sessions': 0, 'total_ips': 0, 'likes': 0, 'dislikes': 0},
                'get_admin_logs': {'success': True, 'logs': []},
                'admin_users_collection': {'users': [], 'current_user_id': 1},
            }
            for endpoint, payload in responses.items():
                self.admin_app.view_functions[endpoint] = lambda payload=payload: jsonify(payload)
            self.ui_app.add_url_rule('/__browser__/reset', view_func=self.reset, methods=['POST'])
            self.ui_app.add_url_rule('/__browser__/state', view_func=self.state)
            return self
        except BaseException:
            self.resources.close()
            raise

    def __exit__(self, error_type, error, traceback):
        try:
            self.forbidden.assert_not_called()
        finally:
            self.resources.close()

    def configure_voice(self, app):
        @app.get('/voice/health')
        def voice_health():
            if self.voice_status == 'ready':
                return jsonify(status='ready')
            return jsonify(status=self.voice_status, code='voice_unavailable'), 503

    def create_session(self, user):
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.sessions[token] = dict(user)
        return token

    def load_session(self, token):
        with self.lock:
            return self.sessions.get(token)

    def revoke_session(self, token):
        with self.lock:
            self.sessions.pop(token, None)

    def connect_history(self):
        with self.lock:
            if request.endpoint == 'feedback':
                self.feedback_attempts += 1
                if self.feedback_failures:
                    self.feedback_failures -= 1
                    raise OperationalError(2003, 'Injected browser feedback failure')
            return self.history.connect()

    def authorized_fixture_request(self):
        return secrets.compare_digest(request.headers.get('X-Browser-Fixture', ''), self.token)

    def reset(self):
        if not self.authorized_fixture_request():
            return jsonify(code='not_found'), 404
        options = request.get_json(silent=True) or {}
        with self.lock:
            self.history.rows.clear()
            self.history.queries.clear()
            self.sessions.clear()
            self.feedback_attempts = 0
            self.feedback_failures = 1 if options.get('feedback_failure') else 0
            self.voice_status = 'unavailable' if options.get('voice_unavailable') else 'ready'
            for app in (self.ui_app, self.admin_app):
                for limiter in app.extensions['abuse_controls'].values():
                    limiter.clients.clear()
        return jsonify(status='reset')

    def state(self):
        if not self.authorized_fixture_request():
            return jsonify(code='not_found'), 404
        with self.lock:
            return jsonify(
                messages=list(self.history.rows.values()), feedback_attempts=self.feedback_attempts,
                sessions=len(self.sessions),
            )
