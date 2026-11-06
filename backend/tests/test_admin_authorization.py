"""Offline authorization matrix: no database, model weights, or GPU required."""

import hashlib
import hmac
import importlib
import json
import sys
import threading
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import urlsplit

from flask import Flask, Response


BACKEND_DIR = Path(__file__).resolve().parents[1]
with patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path]):
    import admin_policy


FEATURES = ('json_upload', 'pdf_upload', 'models', 'chats', 'system_reports', 'controller')
ROUTE_MATRIX = [
    ('admin_login', 'GET', '/login', 'public'),
    ('admin_login', 'POST', '/login', 'public'),
    ('login_css', 'GET', '/login.css', 'public'),
    ('login_js', 'GET', '/login.js', 'public'),
    ('login_theme', 'GET', '/login-theme.js', 'public'),
    ('admin_index', 'GET', '/', 'authenticated'),
    ('dashboard_asset', 'GET', '/dashboard.css', 'authenticated'),
    ('dashboard_asset', 'GET', '/dashboard.js', 'authenticated'),
    ('dashboard_asset', 'GET', '/theme.js', 'authenticated'),
    ('dashboard_asset', 'GET', '/admin-csrf.js', 'authenticated'),
    ('admin_session', 'GET', '/admin/api/session', 'authenticated'),
    ('admin_logout', 'POST', '/logout', 'authenticated'),
    ('admin_users_collection', 'GET', '/admin/api/admin-users', 'administrator'),
    ('admin_users_collection', 'POST', '/admin/api/admin-users', 'administrator'),
    ('admin_user_item', 'PATCH', '/admin/api/admin-users/2', 'administrator'),
    ('admin_user_item', 'DELETE', '/admin/api/admin-users/2', 'administrator'),
    ('admin_stats', 'GET', '/admin/api/stats', 'json_upload'),
    ('admin_documents', 'GET', '/admin/api/documents', 'json_upload'),
    ('admin_upload', 'POST', '/admin/api/upload', 'json_upload'),
    ('admin_delete', 'DELETE', '/admin/api/documents/2', 'json_upload'),
    ('admin_pdf_stats', 'GET', '/admin/api/pdf/stats', 'pdf_upload'),
    ('admin_pdf_documents', 'GET', '/admin/api/pdf/documents', 'pdf_upload'),
    ('admin_pdf_upload', 'POST', '/admin/api/pdf/upload', 'pdf_upload'),
    ('admin_pdf_delete', 'DELETE', '/admin/api/pdf/documents/2', 'pdf_upload'),
    ('admin_models', 'GET', '/admin/api/models', 'models'),
    ('admin_set_model', 'POST', '/admin/api/models', 'models'),
    ('admin_chat_sessions', 'GET', '/admin/api/chat/sessions', 'chats'),
    ('admin_chat_messages', 'GET', '/admin/api/chat/messages', 'chats'),
    ('admin_chat_stats', 'GET', '/admin/api/chat/stats', 'chats'),
    ('admin_chat_delete_session', 'DELETE', '/admin/api/chat/sessions', 'chats'),
    ('admin_chat_delete_message', 'DELETE', '/admin/api/chat/logs', 'chats'),
    ('get_admin_logs', 'GET', '/admin/api/logs', 'system_reports'),
    ('controller_page', 'GET', '/controller', 'controller'),
    ('controller_status', 'GET', '/admin/api/controller/status', 'controller'),
    ('controller_action', 'POST', '/admin/api/controller/action', 'controller'),
]
PAGE_MATRIX = {
    'admin': 'json_upload',
    'admin_pdf': 'pdf_upload',
    'adminmodel': 'models',
    'adminchat': 'chats',
    'adminlogger': 'system_reports',
    'controller': 'controller',
    'admin-user-control': 'administrator',
}
ROUTE_MATRIX += [
    ('admin_page_asset', 'GET', f'/admin/{stem}{extension}', permission)
    for stem, permission in PAGE_MATRIX.items()
    for extension in ('.html', '.css', '.js')
]
TEST_ROUTES = {
    '/admin/api/unmapped', '/admin/api/models/unmapped', '/unmapped-page',
    '/admin/api/stats/new-method', '/unmapped-asset.js',
}


def account(permissions=(), *, administrator=False, username='operator', active=True):
    return {
        'id': 1, 'username': username, 'is_active': active,
        'is_admin': administrator, 'permissions': list(permissions),
    }


class OperationPolicyTests(unittest.TestCase):
    def test_feature_allow_deny_matrix(self):
        for operation, permission in admin_policy.OPERATION_POLICIES.items():
            for feature in FEATURES:
                with self.subTest(operation=operation, feature=feature):
                    expected = permission in {'public', 'authenticated', feature}
                    self.assertEqual(admin_policy.permits(account([feature]), operation), expected)

    def test_username_is_never_authority(self):
        for operation, permission in admin_policy.OPERATION_POLICIES.items():
            with self.subTest(operation=operation):
                self.assertEqual(
                    admin_policy.permits(account(username='administrator'), operation),
                    permission in {'public', 'authenticated'},
                )
                self.assertTrue(admin_policy.permits(account(administrator=True, username='renamed'), operation))

    def test_wildcards_do_not_grant_account_administration(self):
        for permissions in ['*', ['*'], ['user_control'], {'models': True}, 'models', None, 123]:
            user = account()
            user['permissions'] = permissions
            with self.subTest(permissions=permissions):
                self.assertFalse(admin_policy.permits(user, 'users.list'))
                self.assertFalse(admin_policy.permits(user, 'users.view'))
                self.assertEqual(admin_policy.permits(user, 'models.list'), permissions in ('*', ['*']))

    def test_unknown_and_inactive_are_denied(self):
        for user in [None, account(), account(['*']), account(administrator=True)]:
            for operation in [None, '', 'future.operation']:
                with self.subTest(user=user, operation=operation):
                    self.assertFalse(admin_policy.permits(user, operation))
        for operation, permission in admin_policy.OPERATION_POLICIES.items():
            if permission != 'public':
                self.assertFalse(admin_policy.permits(account(['*'], administrator=True, active=False), operation))

    def test_only_exact_assets_and_methods_resolve(self):
        for filename in ['future.html', 'nested/admin.html', '../admin.html', 'admin.html.bak', 'api/models/new']:
            with self.subTest(filename=filename):
                self.assertIsNone(admin_policy.request_operation('admin_page_asset', 'GET', '/unused', {'filename': filename}))
        self.assertIsNone(admin_policy.request_operation('admin_page_asset', 'POST', '/unused', {'filename': 'admin.html'}))
        self.assertIsNone(admin_policy.request_operation('admin_stats', 'POST', '/admin/api/stats', {}))
        self.assertIsNone(admin_policy.request_operation('dashboard_asset', 'GET', '/future.js', {}))


class AdminAuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resources = ExitStack()
        cls.addClassCleanup(cls.resources.close)
        cls.resources.enter_context(patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path]))
        cls.resources.enter_context(patch.dict('os.environ', {
            'APP_ENV': 'test', 'UI_ALLOWED_ORIGINS': 'http://localhost:5000',
            'ADMIN_ALLOWED_ORIGINS': 'http://localhost:8000', 'TRUSTED_PROXY_HOPS': '0',
            'TRUSTED_PROXY_CIDRS': '', 'ADMIN_SESSION_SECRET': 'authorization-tests-only-not-a-deployment-secret',
        }, clear=True))

        def module_double(name, **attributes):
            module = types.ModuleType(name)
            module.__dict__.update(attributes)
            return module

        cls.model_loader = Mock(side_effect=AssertionError('Model loading is forbidden in authorization tests'))
        cls.persist_state = Mock()
        doubles = {
            'generator': module_double('generator', set_generator_model=cls.model_loader,
                                       get_generator=cls.model_loader, _gen_model=None, _gen_device=None,
                                       MODEL_ID='test', _gen_lock=threading.Lock()),
            'interface': module_double('interface', embed_texts=cls.model_loader, embedding_dim=cls.model_loader,
                                       set_embedding_model=cls.model_loader, get_embedder=cls.model_loader,
                                       ADMIN_DIR=str(BACKEND_DIR / 'admin'), ADMIN_PORT=8000,
                                       MAX_UPLOAD_BYTES=20 * 1024 * 1024, ALLOWED_EXT={'.json'},
                                       _model=None, _dim=None, MODEL_ID='test', _lock=threading.Lock()),
            'voice': module_double('voice', IMPORT_ERROR=None, _model_future=None, MODEL_NAME='test',
                                   _model_lock=threading.Lock(), _start_model_loading=cls.model_loader),
            'controller': module_double('controller', _state={'voice_enabled': True, 'chat_enabled': True},
                                        _set_state=cls.persist_state, _free_memory=Mock(), _gpu_info=Mock(return_value={})),
            'user': module_double('user', ui_app=Flask('authorization_test_ui')),
            'numpy': module_double('numpy'),
            'fitz': module_double('fitz'),
        }
        for name in ('admin', 'controller_api'):
            doubles[name] = None
        cls.resources.enter_context(patch.dict(sys.modules, doubles))
        for name in ('admin', 'controller_api'):
            sys.modules.pop(name, None)
        cls.database = importlib.import_module('database')
        cls.forbidden_database = cls.resources.enter_context(patch.object(
            cls.database, 'get_conn', side_effect=AssertionError('Database access is forbidden in authorization tests'),
        ))
        cls.resources.enter_context(patch.object(cls.database.logger, '_write', return_value=True))
        cls.admin = importlib.import_module('admin')
        cls.controller = importlib.import_module('controller_api')
        cls.app = importlib.import_module('application').create_admin_app()
        cls.controller.register(sys.modules['user'].ui_app, cls.app)
        cls.unmapped_handler_spy = Mock(return_value=Response(status=204))

        def unmapped_handler():
            return cls.unmapped_handler_spy()

        for path in ('/admin/api/unmapped', '/admin/api/models/unmapped', '/unmapped-page'):
            cls.app.add_url_rule(path, endpoint='unmapped_operation', view_func=unmapped_handler, methods=['GET', 'POST'])
        cls.app.add_url_rule('/admin/api/stats/new-method', endpoint='admin_stats',
                             view_func=cls.app.view_functions['admin_stats'], methods=['POST'])
        cls.app.add_url_rule('/unmapped-asset.js', endpoint='dashboard_asset',
                             view_func=cls.app.view_functions['dashboard_asset'])

    def setUp(self):
        self.test_resources = ExitStack()
        self.addCleanup(self.test_resources.close)
        self.client = self.app.test_client()
        self.token = 's' * 43
        with self.client.session_transaction() as signed_session:
            signed_session['admin_session'] = self.token
        self.user = account()
        self.session_loader = self.test_resources.enter_context(patch.object(
            self.admin, 'load_admin_session', side_effect=lambda token: self.user,
        ))
        self.forbidden_database.reset_mock()
        self.model_loader.reset_mock()
        self.persist_state.reset_mock()
        self.unmapped_handler_spy.reset_mock()

    def tearDown(self):
        self.forbidden_database.assert_not_called()
        self.model_loader.assert_not_called()

    def csrf_headers(self):
        secret = self.app.secret_key.encode('utf-8')
        proof = hmac.new(secret, b'admin-csrf\x00' + self.token.encode('utf-8'), hashlib.sha256).hexdigest()
        return {'X-CSRF-Token': proof}

    def open(self, path, method='GET', **kwargs):
        return self.client.open(path, method=method, base_url='http://localhost:8000', **kwargs)

    def test_registered_routes_are_covered_by_matrix_and_policy(self):
        adapter = self.app.url_map.bind('localhost')
        covered = set()
        for endpoint, method, path, permission in ROUTE_MATRIX:
            methods = (method, 'HEAD', 'OPTIONS') if method == 'GET' else (method, 'OPTIONS')
            for candidate in methods:
                rule, arguments = adapter.match(path, method=candidate, return_rule=True)
                covered.add((rule.rule, candidate))
                operation = admin_policy.request_operation(rule.endpoint, candidate, path, arguments)
                with self.subTest(path=path, method=candidate):
                    if candidate != 'OPTIONS':
                        self.assertEqual(rule.endpoint, endpoint)
                    self.assertEqual(admin_policy.OPERATION_POLICIES.get(operation), permission)
        registered = set()
        for rule in self.app.url_map.iter_rules():
            if rule.rule in TEST_ROUTES:
                continue
            path = rule.rule.replace('<int:user_id>', '2').replace('<int:doc_id>', '2').replace('<path:filename>', 'admin.html')
            for method in rule.methods:
                selected, arguments = adapter.match(path, method=method, return_rule=True)
                if selected == rule:
                    registered.add((rule.rule, method))
        self.assertEqual(covered, registered)
        self.assertNotIn('static', self.app.view_functions)
        disk_assets = {path.name for path in (BACKEND_DIR / 'admin').iterdir() if path.suffix in {'.html', '.css', '.js'}}
        self.assertEqual(set(admin_policy.ASSET_OPERATIONS), disk_assets)
        self.assertEqual(set(admin_policy.CONTROLLER_ACTION_OPERATIONS), set(self.controller.TOGGLES) | set(self.controller.MODEL_ACTIONS))

    def test_page_api_and_controller_allow_deny_matrix(self):
        profiles = [None, account(), account(username='administrator'), account(['user_control']),
                    account(['*']), account(administrator=True, username='renamed')]
        profiles += [account([feature]) for feature in FEATURES]
        endpoints = {endpoint for endpoint, method, path, permission in ROUTE_MATRIX}
        spies = {endpoint: Mock(return_value=Response(status=204)) for endpoint in endpoints}
        with patch.dict(self.app.view_functions, spies), patch.object(self.admin, 'valid_csrf_token', return_value=True):
            for user in profiles:
                self.user = user
                for endpoint, method, path, permission in ROUTE_MATRIX:
                    methods = (method, 'HEAD', 'OPTIONS') if method == 'GET' else (method, 'OPTIONS')
                    for candidate in methods:
                        with self.subTest(user=user, path=path, method=candidate):
                            expected = permission == 'public' or user is not None and (
                                permission == 'authenticated' or user['is_admin']
                                or permission != 'administrator' and (permission in user['permissions'] or '*' in user['permissions'])
                            )
                            before = sum(spy.call_count for spy in spies.values())
                            response = self.open(path, candidate)
                            if expected:
                                self.assertEqual(response.status_code, 200 if candidate == 'OPTIONS' else 204)
                            elif user is None:
                                self.assertEqual(response.status_code, 401 if path.startswith('/admin/api/') or path == '/logout' else 302)
                            else:
                                self.assertEqual(response.status_code, 403)
                            after = sum(spy.call_count for spy in spies.values())
                            self.assertEqual(after - before, int(expected and candidate != 'OPTIONS'))

    def test_new_routes_methods_and_assets_are_denied_even_for_administrators(self):
        for user in (account(['*']), account(administrator=True)):
            self.user = user
            for path, method in [
                ('/admin/api/unmapped', 'GET'), ('/admin/api/unmapped', 'POST'),
                ('/admin/api/models/unmapped', 'GET'), ('/unmapped-page', 'GET'),
                ('/admin/api/stats/new-method', 'POST'), ('/unmapped-asset.js', 'GET'),
                ('/admin/new.html', 'GET'), ('/admin/nested/adminmodel.html', 'GET'),
                ('/admin/../adminmodel.html', 'GET'), ('/admin/adminmodel.html.bak', 'GET'),
            ]:
                with self.subTest(user=user, path=path, method=method):
                    self.assertEqual(self.open(path, method, headers=self.csrf_headers()).status_code, 403)
            for path in ('/adminmodel.html', '/adminmodel.js', '/adminmodel.css'):
                self.assertEqual(self.open(path).status_code, 404)
        self.unmapped_handler_spy.assert_not_called()

    def test_all_mutations_still_require_session_bound_csrf(self):
        self.user = account(administrator=True)
        for endpoint, method, path, permission in ROUTE_MATRIX:
            if method in {'GET', 'HEAD', 'OPTIONS'} or path == '/login':
                continue
            handler = Mock(return_value=Response(status=204))
            with patch.dict(self.app.view_functions, {endpoint: handler}):
                for headers in ({}, {'X-CSRF-Token': 'invalid'}):
                    with self.subTest(path=path, method=method, headers=headers):
                        self.assertEqual(self.open(path, method, headers=headers).status_code, 403)
                        handler.assert_not_called()
                self.assertEqual(self.open(path, method, headers=self.csrf_headers()).status_code, 204)
                handler.assert_called_once()

    def test_unauthenticated_api_and_logout_do_not_dispatch(self):
        self.user = None
        for path, method in [('/admin/api/controller/status', 'GET'), ('/admin/api/controller/action', 'POST'), ('/logout', 'POST')]:
            self.assertEqual(self.open(path, method, headers=self.csrf_headers()).status_code, 401)
        with self.client.session_transaction() as signed_session:
            self.assertNotIn('admin_session', signed_session)

    def test_session_returns_server_authorized_dashboard_pages(self):
        for user, expected in [
            (account(['models']), ['admin/adminmodel.html']),
            (account(username='administrator'), []),
            (account(['user_control']), []),
            (account(administrator=True), [f'admin/{stem}.html' for stem in PAGE_MATRIX]),
        ]:
            self.user = user
            response = self.open('/admin/api/session')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['allowed_pages'], expected)
            self.assertIn('csrf_token', response.json)

    def test_permitted_assets_and_controller_page_are_really_served(self):
        for stem, permission in PAGE_MATRIX.items():
            self.user = account(administrator=True) if permission == 'administrator' else account([permission])
            for extension in ('.html', '.css', '.js'):
                with self.subTest(stem=stem, extension=extension):
                    response = self.open(f'/admin/{stem}{extension}')
                    self.assertEqual(response.status_code, 200)
                    response.close()
        self.user = account(['controller'])
        response = self.open('/controller')
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_controller_action_matrix_checks_before_side_effects(self):
        actions = [*self.controller.TOGGLES, *self.controller.MODEL_ACTIONS]
        with patch.object(self.controller, 'get_status', return_value={'ok': True}), \
                patch.object(self.controller, '_set_state') as set_state, \
                patch.object(self.controller, '_start_model_action', return_value=True) as start_action:
            for user in [account(), account(['models']), account(username='administrator'), account(['controller']), account(administrator=True)]:
                self.user = user
                for action in actions:
                    with self.subTest(user=user, action=action):
                        set_state.reset_mock()
                        start_action.reset_mock()
                        response = self.open('/admin/api/controller/action', 'POST', json={'action': action}, headers=self.csrf_headers())
                        allowed = user['is_admin'] or 'controller' in user['permissions']
                        self.assertEqual(response.status_code, (200 if action in self.controller.TOGGLES else 202) if allowed else 403)
                        self.assertEqual(set_state.called, allowed and action in self.controller.TOGGLES)
                        self.assertEqual(start_action.called, allowed and action in self.controller.MODEL_ACTIONS)
                        if start_action.called:
                            start_action.assert_called_once_with(action)
                        if set_state.called:
                            key, value = self.controller.TOGGLES[action]
                            set_state.assert_called_once_with(**{key: value})

    def test_unregistered_controller_actions_are_denied_before_dispatch(self):
        self.user = account(administrator=True)
        with patch.dict(self.controller.MODEL_ACTIONS, {'future_load': self.model_loader}), \
                patch.dict(self.controller.TOGGLES, {'future_disable': ('chat_enabled', False)}), \
                patch.object(self.controller, '_start_model_action') as start_action:
            for action in ['future_load', 'future_disable', 'unknown', None, [], {}, 12]:
                with self.subTest(action=action):
                    response = self.open('/admin/api/controller/action', 'POST', json={'action': action}, headers=self.csrf_headers())
                    self.assertEqual(response.status_code, 403 if isinstance(action, str) else 400)
            start_action.assert_not_called()
            self.persist_state.assert_not_called()

    def test_controller_rejects_malformed_and_unexpected_payloads_without_dispatch(self):
        self.user = account(administrator=True)
        with patch.object(self.controller, '_start_model_action') as start_action:
            for payload in ([], None, {}, {'action': 'x' * 65},
                            {'action': 'generator_load', 'unexpected': True}):
                with self.subTest(payload=payload):
                    response = self.open('/admin/api/controller/action', 'POST', data=json.dumps(payload),
                                         content_type='application/json', headers=self.csrf_headers())
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.json['code'], 'invalid_request')
            start_action.assert_not_called()
            self.persist_state.assert_not_called()

    def test_user_deletion_uses_role_and_identity_not_username(self):
        self.user = account(administrator=True, username='renamed-admin')
        response = self.open('/admin/api/admin-users/1', 'DELETE', headers=self.csrf_headers())
        self.assertEqual(response.status_code, 400)
        self.forbidden_database.assert_not_called()
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {'id': 2, 'username': 'administrator'}
        with patch.object(self.admin, 'get_conn', return_value=connection):
            response = self.open('/admin/api/admin-users/2', 'DELETE', headers=self.csrf_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(cursor.execute.call_args.args, ('DELETE FROM `admin-users` WHERE id = %s', (2,)))
        connection.commit.assert_called_once()
        connection.close.assert_called_once()

    def test_user_directory_identifies_current_account(self):
        self.user = account(administrator=True)
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchall.return_value = []
        with patch.object(self.admin, 'get_conn', return_value=connection):
            response = self.open('/admin/api/admin-users')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {'users': [], 'current_user_id': 1})

    def test_login_still_requires_prelogin_csrf(self):
        self.user = None
        authenticate = Mock(return_value=None)
        with patch.object(self.admin, 'authenticate', authenticate):
            self.assertEqual(self.open('/login', 'POST', data={'username': 'test', 'password': 'unused'}).status_code, 403)
            authenticate.assert_not_called()
            response = self.open('/login')
            self.assertEqual(response.status_code, 200)
            with self.client.session_transaction() as signed_session:
                proof = signed_session['_login_csrf_token']
            response = self.open('/login', 'POST', data={'username': 'test', 'password': 'unused', 'csrf_token': proof})
            self.assertEqual(response.status_code, 401)
            authenticate.assert_called_once_with('test', 'unused')

    def test_origin_denial_precedes_session_and_operation_policy(self):
        response = self.open('/admin/api/controller/action', 'POST', json={'action': 'chat_disable'},
                             headers={**self.csrf_headers(), 'Origin': 'http://evil.example'})
        self.assertEqual(response.status_code, 403)
        self.session_loader.assert_not_called()

    def test_user_feature_routes_remain_public(self):
        client = sys.modules['user'].ui_app.test_client()
        self.assertEqual(client.get('/controller/features').status_code, 200)
        self.assertEqual(client.get('/controller/features.js').status_code, 200)

    def test_conversation_inspection_requires_admin_authority_not_known_identifiers(self):
        path = '/admin/api/chat/messages?user_id=chat_known-public-id&ip=192.0.2.1'
        for user, status in ((None, 401), (account(), 403), (account(['models']), 403)):
            self.user = user
            self.assertEqual(self.open(path).status_code, status)
        self.forbidden_database.assert_not_called()
        self.user = account(['chats'])
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [{
            'id': 1, 'user_id': 'chat_known-public-id', 'ip_address': '192.0.2.1',
            'user_message': 'question', 'bot_response': 'answer', 'feedback': None,
            'feedback_at': None, 'created_at': None,
        }]
        with patch.object(self.admin, 'get_conn', return_value=connection):
            response = self.open(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['messages'][0]['id'], 1)
        query, parameters = cursor.execute.call_args.args
        self.assertNotIn('owner_hash', query)
        self.assertEqual(parameters, ('chat_known-public-id', '192.0.2.1'))


if __name__ == '__main__':
    unittest.main()
