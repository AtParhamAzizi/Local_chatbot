"""Offline ownership tests: independent cookie jars, scoped SQL, and legacy rows."""

import importlib
import sys
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from flask import Flask, Response
from itsdangerous import BadSignature


BACKEND_DIR = Path(__file__).resolve().parents[1]


class MemoryHistory:
    def __init__(self):
        self.rows = {}
        self.queries = []

    def connect(self):
        connection = MagicMock()
        connection.cursor.return_value = MemoryCursor(self)
        return connection


class MemoryCursor:
    def __init__(self, history):
        self.history = history
        self.rowcount = 0
        self.lastrowid = None
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, traceback):
        return False

    def execute(self, query, parameters):
        query = ' '.join(query.split())
        self.history.queries.append((query, parameters))
        if query.startswith('INSERT INTO chat_history'):
            columns = query.partition('(')[2].partition(')')[0].split(',')
            row = dict(zip((column.strip() for column in columns), parameters))
            self.lastrowid = max(self.history.rows, default=0) + 1
            row.update(id=self.lastrowid, feedback=None)
            self.history.rows[self.lastrowid] = row
            self.rowcount = 1
        elif query.startswith(('UPDATE chat_history', 'SELECT id FROM chat_history')):
            if 'WHERE id = %s AND owner_hash = %s' not in query:
                raise AssertionError('Ownership must be checked in every feedback SQL statement')
            message_id, owner_hash = parameters[-2:]
            row = self.history.rows.get(message_id)
            owned = row is not None and owner_hash is not None and row.get('owner_hash') == owner_hash
            self.result = {'id': message_id} if owned else None
            self.rowcount = 0
            if query.startswith('UPDATE') and owned:
                feedback = parameters[0]
                self.rowcount = int(row['feedback'] != feedback)
                row['feedback'] = feedback
        else:
            raise AssertionError(f'Unexpected history query: {query}')

    def fetchone(self):
        return self.result


class ChatOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.class_resources = ExitStack()
        cls.addClassCleanup(cls.class_resources.close)
        cls.class_resources.enter_context(patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path]))
        cls.class_resources.enter_context(patch.dict('os.environ', {
            'APP_ENV': 'test', 'UI_ALLOWED_ORIGINS': 'http://localhost:5000',
            'ADMIN_ALLOWED_ORIGINS': 'http://localhost:8000', 'TRUSTED_PROXY_HOPS': '0',
            'TRUSTED_PROXY_CIDRS': '', 'ADMIN_SESSION_SECRET': 'ownership-tests-only-not-a-deployment-secret',
        }, clear=True))

        def module_double(name, **attributes):
            module = types.ModuleType(name)
            module.__dict__.update(attributes)
            return module

        cls.forbidden_model = Mock(side_effect=AssertionError('Real model access is forbidden'))
        doubles = {
            'interface': module_double('interface', BASE_DIR=str(BACKEND_DIR), UI_PORT=5000,
                                       MIN_SCORE=0.35, embed_texts=cls.forbidden_model),
            'generator': module_double('generator', generate_reply=cls.forbidden_model),
            'searchForAnswer': module_double('searchForAnswer', _search_pdf=cls.forbidden_model,
                                             _search_qa=cls.forbidden_model, build_context=cls.forbidden_model),
            'voice': module_double('voice', configure_voice=Mock()),
            'numpy': module_double('numpy'),
            'user': None,
        }
        cls.class_resources.enter_context(patch.dict(sys.modules, doubles))
        sys.modules.pop('user', None)
        cls.database = importlib.import_module('database')
        cls.class_resources.enter_context(patch.object(cls.database.logger, '_write', return_value=True))
        cls.class_resources.enter_context(patch.object(
            cls.database, 'get_conn', side_effect=AssertionError('Real database access is forbidden'),
        ))
        cls.user = importlib.import_module('user')
        cls.sessions = importlib.import_module('chat_sessions')
        cls.app = cls.user.ui_app

    def setUp(self):
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        self.history = MemoryHistory()
        self.database_calls = self.resources.enter_context(patch.object(
            self.user, 'get_conn', side_effect=self.history.connect,
        ))
        self.retrieve = self.resources.enter_context(patch.object(self.user, '_retrieve_top', return_value=[]))
        self.forbidden_model.reset_mock()
        for limiter in self.app.extensions['abuse_controls'].values():
            limiter.clients.clear()
        self.owner = self.app.test_client()
        self.other = self.app.test_client()

    def tearDown(self):
        self.forbidden_model.assert_not_called()

    def chat(self, client, **options):
        payload = options.pop('json', {'message': 'hello'})
        response = client.post('/chat', json=payload, **options)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        return response.json['message_id']

    def feedback(self, client, message_id, feedback='like', **options):
        return client.post('/feedback', json={'message_id': message_id, 'feedback': feedback}, **options)

    def test_chat_ignores_client_identity_and_keeps_secrets_out_of_responses_and_rows(self):
        message_id = self.chat(self.owner, json={'message': 'hello', 'user_id': 'victim'})
        first = self.history.rows[message_id]
        next_id = self.chat(self.owner, json={'message': 'again', 'user_id': 'someone-else'})
        second = self.history.rows[next_id]
        self.assertEqual(first['owner_hash'], second['owner_hash'])
        self.assertEqual(first['user_id'], second['user_id'])
        self.assertNotEqual(first['user_id'], 'victim')
        self.assertEqual(len(first['owner_hash']), 64)
        with self.owner.session_transaction() as signed_session:
            token = signed_session['chat_owner']
        self.assertEqual(len(token), 43)
        self.assertNotIn(token, repr(self.history.rows))
        response = self.feedback(self.owner, message_id)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(token, response.get_data(as_text=True))
        self.assertNotIn(first['owner_hash'], response.get_data(as_text=True))

    def test_owner_can_rate_change_and_repeat_feedback(self):
        message_id = self.chat(self.owner)
        for feedback in ('like', 'like', 'dislike', 'dislike'):
            response = self.feedback(self.owner, message_id, feedback)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['feedback'], feedback)
            self.assertEqual(self.history.rows[message_id]['feedback'], feedback)
        self.assertTrue(any(query.startswith('SELECT id') for query, parameters in self.history.queries))

    def test_same_ip_different_sessions_cannot_rate_each_others_messages(self):
        owned_id = self.chat(self.owner)
        other_id = self.chat(self.other)
        self.assertNotEqual(self.history.rows[owned_id]['owner_hash'], self.history.rows[other_id]['owner_hash'])
        self.assertNotEqual(self.history.rows[owned_id]['user_id'], self.history.rows[other_id]['user_id'])
        for client, message_id in ((self.other, owned_id), (self.owner, other_id)):
            response = self.feedback(client, message_id)
            self.assertEqual(response.status_code, 404)
            unknown = self.feedback(client, 999999)
            self.assertEqual(response.json, unknown.json)
            self.assertIsNone(self.history.rows[message_id]['feedback'])

    def test_message_id_without_session_is_rejected_before_database_access(self):
        message_id = self.chat(self.owner)
        before = self.database_calls.call_count
        response = self.feedback(self.other, message_id)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.database_calls.call_count, before)
        self.assertIsNone(self.other.get_cookie(self.app.config['SESSION_COOKIE_NAME']))
        self.assertIsNone(self.history.rows[message_id]['feedback'])

    def test_claimed_owner_fields_and_headers_do_not_grant_ownership(self):
        message_id = self.chat(self.owner)
        victim = self.history.rows[message_id]
        self.chat(self.other, json={'message': 'hello', 'user_id': victim['user_id'], 'owner_hash': victim['owner_hash']})
        response = self.other.post('/feedback', json={
            'message_id': message_id, 'feedback': 'like',
            'user_id': victim['user_id'], 'owner_hash': victim['owner_hash'],
        }, headers={'X-Forwarded-For': victim['ip_address'], 'X-Owner-Hash': victim['owner_hash']})
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(victim['feedback'])

    def test_reload_and_ip_change_keep_ownership_without_refreshing_cookie(self):
        response = self.owner.get('/')
        self.assertEqual(response.status_code, 200)
        response.close()
        cookie = self.owner.get_cookie(self.app.config['SESSION_COOKIE_NAME']).value
        message_id = self.chat(self.owner)
        response = self.owner.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Set-Cookie', response.headers)
        response.close()
        self.assertEqual(self.owner.get_cookie(self.app.config['SESSION_COOKIE_NAME']).value, cookie)
        response = self.feedback(self.owner, message_id, environ_overrides={'REMOTE_ADDR': '192.0.2.100'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Set-Cookie', response.headers)

    def test_losing_cookie_cannot_reclaim_messages_by_old_public_user_id(self):
        message_id = self.chat(self.owner)
        public_id = self.history.rows[message_id]['user_id']
        self.owner.delete_cookie(self.app.config['SESSION_COOKIE_NAME'])
        self.assertEqual(self.feedback(self.owner, message_id).status_code, 401)
        new_id = self.chat(self.owner, json={'message': 'hello', 'user_id': public_id})
        self.assertNotEqual(self.history.rows[new_id]['user_id'], public_id)
        self.assertEqual(self.feedback(self.owner, message_id).status_code, 404)

    def test_legacy_rows_without_proof_are_not_claimed(self):
        self.history.rows[100] = {'id': 100, 'user_id': 'legacy-user', 'owner_hash': None, 'feedback': None}
        self.chat(self.owner, json={'message': 'hello', 'user_id': 'legacy-user'})
        self.assertEqual(self.feedback(self.owner, 100).status_code, 404)
        self.assertIsNone(self.history.rows[100]['feedback'])

    def test_tampered_cookie_does_not_prove_ownership(self):
        message_id = self.chat(self.owner)
        cookie_name = self.app.config['SESSION_COOKIE_NAME']
        payload, signature = self.owner.get_cookie(cookie_name).value.rsplit('.', 1)
        invalid_signature = ('a' if signature[0] != 'a' else 'b') + signature[1:]
        self.owner.set_cookie(cookie_name, payload + '.' + invalid_signature)
        before = self.database_calls.call_count
        self.assertEqual(self.feedback(self.owner, message_id).status_code, 401)
        self.assertEqual(self.database_calls.call_count, before)

    def test_expired_cookie_does_not_prove_ownership(self):
        with patch('time.time', return_value=1800000000):
            message_id = self.chat(self.owner)
        before = self.database_calls.call_count
        with patch('time.time', return_value=1800000000 + 86401):
            self.assertEqual(self.feedback(self.owner, message_id).status_code, 401)
        self.assertEqual(self.database_calls.call_count, before)

    def test_cookie_flags_and_signing_key_are_separate_from_admin(self):
        response = self.owner.get('/')
        header = response.headers['Set-Cookie']
        response.close()
        self.assertTrue(header.startswith('local_chatbot_chat_dev='))
        for attribute in ('HttpOnly', 'SameSite=Lax', 'Path=/', 'Expires='):
            self.assertIn(attribute, header)
        self.assertNotIn('Domain=', header)
        self.assertNotIn('Secure', header)
        administrator = Flask('ownership_test_admin')
        administrator.secret_key = 'ownership-tests-only-not-a-deployment-secret'
        admin_serializer = administrator.session_interface.get_signing_serializer(administrator)
        chat_serializer = self.app.session_interface.get_signing_serializer(self.app)
        cookie = self.owner.get_cookie(self.app.config['SESSION_COOKIE_NAME']).value
        with self.assertRaises(BadSignature):
            admin_serializer.loads(cookie)
        with self.assertRaises(BadSignature):
            chat_serializer.loads(admin_serializer.dumps({'chat_owner': 'a' * 43}))

    def test_production_cookie_is_host_only_secure_and_httponly(self):
        production = Flask('ownership_test_production')
        with patch.dict('os.environ', {
            'APP_ENV': 'production', 'DB_USER': 'chatbot_app', 'DB_PASS': 'test-only', 'DB_NAME': 'test',
        }):
            self.sessions.configure_chat_sessions(production)

        def initialize():
            self.sessions.chat_owner_hash(create=True)
            return Response('ready')

        production.add_url_rule('/', endpoint='ui_index', view_func=initialize)
        response = production.test_client().get('/', base_url='https://chat.example.test')
        header = response.headers['Set-Cookie']
        self.assertTrue(header.startswith('__Host-local_chatbot_chat='))
        for attribute in ('Secure', 'HttpOnly', 'SameSite=Lax', 'Path=/'):
            self.assertIn(attribute, header)
        self.assertNotIn('Domain=', header)

    def test_public_routes_do_not_expose_conversations_by_id(self):
        message_id = self.chat(self.owner)
        for path in (
            f'/messages/{message_id}', f'/chat/{message_id}',
            f'/admin/api/chat/messages?user_id={self.history.rows[message_id]["user_id"]}',
        ):
            self.assertEqual(self.other.get(path).status_code, 404)

    def test_feedback_database_failure_remains_retryable(self):
        message_id = self.chat(self.owner)
        with patch.object(self.user, 'get_conn', side_effect=RuntimeError('database unavailable')):
            response = self.feedback(self.owner, message_id)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.feedback(self.owner, message_id).status_code, 200)

    def test_history_migration_preserves_legacy_rows_and_is_idempotent(self):
        for existing in (None, {'Field': 'owner_hash'}):
            connection = MagicMock()
            cursor = connection.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = existing
            with patch.object(self.database, 'get_conn', return_value=connection):
                self.database.init_history_table()
            queries = [call.args[0] for call in cursor.execute.call_args_list]
            migrations = [query for query in queries if query.startswith('ALTER')]
            self.assertEqual(len(migrations), int(existing is None))
            if migrations:
                self.assertIn('owner_hash', migrations[0])
                self.assertIn('NULL DEFAULT NULL', migrations[0])
            self.assertFalse(any(query.lstrip().startswith(('UPDATE', 'DELETE')) for query in queries))
            connection.commit.assert_called_once()
            connection.close.assert_called_once()

    def test_failed_migration_closes_connection_and_does_not_start_with_unsafe_schema(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        cursor.execute.side_effect = [None, None, RuntimeError('migration failed')]
        with patch.object(self.database, 'get_conn', return_value=connection):
            with self.assertRaises(RuntimeError):
                self.database.init_history_table()
        connection.close.assert_called_once()
        connection.commit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
