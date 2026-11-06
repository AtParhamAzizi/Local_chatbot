"""Privacy defaults on real user routes with offline database/model doubles."""

import importlib
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask, jsonify

from test_abuse_controls import AbuseEndpointFixture


class PrivacyEndpointTests(AbuseEndpointFixture, unittest.TestCase):
    def test_chat_does_not_persist_ip_or_log_message_identity_by_default(self):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.lastrowid = 17
        database = importlib.import_module('database')
        with patch.object(self.user, '_retrieve_top', return_value=[]), \
                patch.object(self.user, 'get_conn', return_value=connection), \
                patch.object(self.user, 'get_client_ip') as client_ip, \
                patch.object(database.logger, 'logDrop') as log:
            response = self.ui_client.post('/chat', json={'message': 'private text'},
                                           environ_overrides={'REMOTE_ADDR': '192.0.2.123'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['message_id'], 17)
        query, parameters = connection.cursor.return_value.__enter__.return_value.execute.call_args.args
        self.assertEqual(parameters[2], '')
        client_ip.assert_not_called()
        self.assertNotIn('private text', repr(log.call_args_list))
        self.assertNotIn('message_id=', repr(log.call_args_list))

    def test_explicit_ip_retention_uses_only_validated_peer(self):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.lastrowid = 1
        with patch.dict('os.environ', {'RETENTION_IP_DAYS': '7'}), \
                patch.object(self.user, '_retrieve_top', return_value=[]), \
                patch.object(self.user, 'get_conn', return_value=connection):
            response = self.ui_client.post('/chat', json={'message': 'hello'},
                                           environ_overrides={'REMOTE_ADDR': '192.0.2.123'},
                                           headers={'X-Forwarded-For': '198.51.100.1'})
        self.assertEqual(response.status_code, 200)
        parameters = connection.cursor.return_value.__enter__.return_value.execute.call_args.args[1]
        self.assertEqual(parameters[2], '192.0.2.123')

    def test_zero_chat_retention_still_answers_without_history_or_feedback(self):
        with patch.dict('os.environ', {'RETENTION_CHAT_DAYS': '0'}), \
                patch.object(self.user, '_retrieve_top', return_value=[]), \
                patch.object(self.user, 'get_conn') as database:
            response = self.ui_client.post('/chat', json={'message': 'hello'})
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(response.json['message_id'])
            self.assertFalse(response.json['feedback_enabled'])
            self.assertIsNone(self.ui_client.get_cookie(self.ui_app.config['SESSION_COOKIE_NAME']))
            response = self.ui_client.post('/feedback', json={'message_id': 1, 'feedback': 'like'})
            self.assertEqual(response.status_code, 403)
        database.assert_not_called()

    def test_zero_feedback_retention_rejects_storage_and_hides_rating_controls(self):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.lastrowid = 1
        with patch.dict('os.environ', {'RETENTION_FEEDBACK_DAYS': '0'}), \
                patch.object(self.user, '_retrieve_top', return_value=[]), \
                patch.object(self.user, 'get_conn', return_value=connection) as database:
            response = self.ui_client.post('/chat', json={'message': 'hello'})
            self.assertFalse(response.json['feedback_enabled'])
            database.reset_mock()
            response = self.ui_client.post('/feedback', json={'message_id': 1, 'feedback': 'dislike'})
            self.assertEqual(response.status_code, 403)
            database.assert_not_called()

    def test_notice_renders_actual_policy_and_playback_is_unchecked(self):
        with patch.dict('os.environ', {'RETENTION_CHAT_DAYS': '9', 'RETENTION_FEEDBACK_DAYS': '90',
                                       'RETENTION_DELETE_ENABLED': '0', 'RETENTION_LOG_DAYS': '0'}):
            response = self.ui_client.get('/')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('9 روز', html)
        self.assertNotIn('90 روز', html)
        self.assertIn('متوقف', html)
        self.assertIn('سرور همین سامانه', html)
        self.assertIn('سرویس اینترنتی', html)
        self.assertNotIn('{%', html)
        self.assertRegex(html, r'id="voiceReplies"[^>]*aria-describedby="playbackPrivacy"')
        checkbox = html.partition('id="voiceReplies"')[2].partition('>')[0]
        self.assertNotIn('checked', checkbox)
        alias = self.ui_client.get('/index.html')
        self.assertEqual(alias.status_code, 302)
        self.assertEqual(alias.headers['Location'], '/')


class PrivacyLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from contextlib import ExitStack
        import sys
        from pathlib import Path
        cls.resources = ExitStack()
        cls.addClassCleanup(cls.resources.close)
        cls.resources.enter_context(patch.object(sys, 'path', [str(Path(__file__).resolve().parents[1]), *sys.path]))
        cls.resources.enter_context(patch.dict('os.environ', {'APP_ENV': 'test'}, clear=True))
        cls.database = importlib.import_module('database')

    def test_zero_log_retention_prevents_new_database_and_fallback_copies(self):
        with patch.dict('os.environ', {'RETENTION_LOG_DAYS': '0'}), \
                patch.object(self.database, 'get_conn') as connect, \
                patch.object(self.database.logging, 'getLogger') as fallback:
            self.assertFalse(self.database.logger._write('module', 'function', 'private text', 1))
        connect.assert_not_called()
        fallback.assert_not_called()

    def test_operation_logs_public_code_not_response_text_or_rating(self):
        app = Flask('privacy_logging')

        @self.database.logger.operation
        def failing_operation():
            return jsonify(error='private text 192.0.2.123', feedback='like'), 500

        with app.test_request_context('/'), patch.object(self.database.logger, '_write') as write:
            failing_operation()
        message = write.call_args.args[2]
        self.assertIn('internal_error', message)
        self.assertNotIn('private text', message)
        self.assertNotIn('192.0.2.123', message)
        self.assertNotIn('like', message)


if __name__ == '__main__':
    unittest.main()
