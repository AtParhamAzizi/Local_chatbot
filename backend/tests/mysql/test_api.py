"""Real Flask routes and MySQL effects; only model/speech adapters are doubles."""

import hashlib
import importlib
import io
import json
import re
import secrets
import sys
import types
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pymupdf
from pymysql.err import OperationalError

from mysql_support import require_target


BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLES = ('ingestion_job_payloads', 'ingestion_jobs', 'admin_sessions', 'admin-users', 'pdf_chunks', 'pdf_documents',
          'qa_documents', 'chat_history', 'adminlogger')
PASSWORD = 'integration-only-password'
ANSWER = 'Deterministic integration reply'


def module_double(name, **attributes):
    module = types.ModuleType(name)
    module.__dict__.update(attributes)
    return module


class FlaskMySQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.target = require_target()
        cls.resources = ExitStack()
        cls.addClassCleanup(cls.resources.close)
        cls.resources.enter_context(patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path]))
        cls.resources.enter_context(patch.dict('os.environ', {
            'APP_ENV': 'test', 'ADMIN_SESSION_SECRET': secrets.token_hex(32),
            'DB_HOST': '127.0.0.1', 'DB_PORT': str(cls.target.port),
            'DB_NAME': cls.target.name, 'DB_USER': 'f014_app', 'DB_PASS': cls.target.password,
            'STT_LOCAL_FILES_ONLY': '1',
        }, clear=True))
        cls.forbidden_model = Mock(side_effect=AssertionError('Real model loading is forbidden'))
        cls.embed = Mock(side_effect=lambda texts: np.tile(np.array([1, 0, 0], dtype=np.float32), (len(texts), 1)))
        cls.generate = Mock(return_value=ANSWER)
        doubles = {
            'interface': module_double(
                'interface', BASE_DIR=str(BACKEND_DIR), ADMIN_DIR=str(BACKEND_DIR / 'admin'),
                ADMIN_PORT=8000, UI_PORT=5000, MIN_SCORE=0.35, ALLOWED_EXT={'.json'},
                embed_texts=cls.embed, embedding_dim=lambda: 3,
                set_embedding_model=cls.forbidden_model, get_embedder=cls.forbidden_model,
            ),
            'generator': module_double('generator', generate_reply=cls.generate,
                                       set_generator_model=cls.forbidden_model,
                                       get_generator=cls.forbidden_model),
            'voice': module_double('voice', configure_voice=lambda app: None),
            'fitz': pymupdf,
        }
        cls.resources.enter_context(patch.dict(sys.modules, doubles))
        for name in ('database', 'settings', 'admin', 'user', 'admin_users', 'admin_sessions',
                     'admin_csrf', 'chat_sessions', 'security', 'abuse_controls',
                     'searchForAnswer', 'model_registry', 'serving', 'request_validation'):
            sys.modules.pop(name, None)
        cls.database = importlib.import_module('database')
        cls.database.DB_CONFIG = cls.target.config()
        migrations = importlib.import_module('schema_migrations')
        migrations.upgrade()
        cls.accounts = importlib.import_module('admin_users')
        from core_support import clear_admin_routes
        clear_admin_routes()
        cls.admin = importlib.import_module('admin_routes.auth')
        cls.user = importlib.import_module('user')
        application = importlib.import_module('application')
        cls.admin_app = application.create_admin_app()
        cls.ui_app = application.create_ui_app()

    def setUp(self):
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        self.reset_database()
        self.addCleanup(self.reset_database)
        for app in (self.admin_app, self.ui_app):
            for limiter in app.extensions['abuse_controls'].values():
                limiter.clients.clear()
        self.forbidden_model.reset_mock()
        self.embed.reset_mock()
        self.generate.reset_mock()
        self.generate.side_effect = None
        self.administrator_id = self.accounts.create_user('test-admin', PASSWORD, is_admin=True)
        self.operator_id = self.accounts.create_user('test-editor', PASSWORD, permissions=['json_upload'])
        self.client = self.admin_app.test_client()
        self.owner = self.ui_app.test_client()
        from ingestion_worker import IngestionWorker
        self.ingestion_worker = IngestionWorker(self.admin_app.extensions['ingestion_jobs'], poll_seconds=0.01)
        self.addCleanup(self.ingestion_worker.stop)
        self.ingestion_worker.start()

    def tearDown(self):
        self.forbidden_model.assert_not_called()

    def reset_database(self):
        if require_target() != self.target or self.database.DB_CONFIG != self.target.config():
            raise AssertionError('Refusing destructive fixtures outside the disposable database')
        with self.database.get_conn() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT DATABASE() AS name')
                if cursor.fetchone()['name'] != self.target.name:
                    raise AssertionError('Refusing to clear an unexpected schema')
                for table in TABLES:
                    cursor.execute(f'DELETE FROM `{table}`')

    def sql(self, statement, parameters=()):
        with self.database.get_conn() as connection:
            with connection.cursor() as cursor:
                cursor.execute(statement, parameters)
                return cursor.fetchall() if cursor.description else cursor.lastrowid

    def count(self, table):
        self.assertIn(table, TABLES)
        return self.sql(f'SELECT COUNT(*) AS total FROM `{table}`')[0]['total']

    def admin_request(self, path, method='GET', client=None, **options):
        return (client or self.client).open(path, method=method, base_url='http://localhost:8000', **options)

    def login_form(self, client=None):
        response = self.admin_request('/login', client=client)
        self.assertEqual(response.status_code, 200)
        return re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))[1]

    def login(self, client=None, username='test-admin'):
        token = self.login_form(client)
        response = self.admin_request('/login', 'POST', client=client, data={
            'username': username, 'password': PASSWORD, 'csrf_token': token,
        })
        self.assertEqual(response.status_code, 302)
        response = self.admin_request('/admin/api/session', client=client)
        self.assertEqual(response.status_code, 200)
        return {'X-CSRF-Token': response.json['csrf_token']}

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.get_data(as_text=True))
        self.assertEqual(set(response.json), {'code', 'error'})
        self.assertEqual(response.json['code'], code)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.headers['X-Error-Code'], code)
        self.assertNotIn('f014_database_failure', response.get_data(as_text=True))

    def upload_json(self, rows, headers, replace=False):
        return self.admin_request('/admin/api/upload', 'POST', headers=headers, data={
            'file': (io.BytesIO(json.dumps(rows, ensure_ascii=False).encode('utf-8')), 'knowledge.json'),
            'replace': str(replace).lower(),
        })

    def test_preflight_reports_existing_data_without_changing_searchable_rows_or_jobs(self):
        headers = self.login()
        self.assertEqual(self.upload_json([{'question': 'existing', 'answer': 'original'}], headers).status_code, 200)
        raw_pdf = self.pdf_bytes()
        self.assertEqual(self.upload_pdf(raw_pdf, headers).status_code, 200)
        self.embed.reset_mock()
        protected = ('qa_documents', 'pdf_documents', 'pdf_chunks', 'ingestion_jobs', 'ingestion_job_payloads')
        before = {table: self.sql(f'SELECT * FROM `{table}`') for table in protected}
        records = [{'question': 'existing', 'answer': 'replacement'}, {'question': 'existing', 'answer': 'duplicate'},
                   {'question': 'new', 'answer': 'unpublished'}]
        response = self.admin_request('/admin/api/upload/preflight', 'POST', headers=headers, data={
            'file': (io.BytesIO(json.dumps(records).encode()), 'preview.json'), 'replace': 'true',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['can_publish'])
        self.assertEqual(response.json['counts'], dict(records=3, pages=None, duplicates_in_file=1,
                                                       duplicates_existing=1, expected_chunks=2,
                                                       inserts=1, updates=1, skipped=1))
        for replace in (False, True):
            response = self.admin_request('/admin/api/pdf/upload/preflight', 'POST', headers=headers, data={
                'file': (io.BytesIO(raw_pdf), 'preview.pdf'), 'replace': str(replace).lower(),
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['can_publish'], replace)
            self.assertEqual(response.json['counts']['duplicates_existing'], 1)
            self.assertEqual(response.json['samples'][0]['page'], 1)
        response = self.admin_request('/admin/api/upload/preflight', 'POST', headers=headers, data={
            'file': (io.BytesIO(b'[{"question":"bad","answer":3}]'), 'invalid.json'),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['issues'][0]['record'], 1)
        self.assertFalse(response.json['can_publish'])
        self.assertEqual(before, {table: self.sql(f'SELECT * FROM `{table}`') for table in protected})
        self.embed.assert_not_called()

    def test_async_job_submission_survives_worker_restart_and_keeps_terminal_result(self):
        from ingestion_worker import IngestionWorker
        from services.ingestion_jobs import IngestionJobs
        from services.job_store import MySQLJobStore

        headers = self.login()
        self.assertTrue(self.ingestion_worker.stop())
        response = self.admin_request('/admin/api/upload/jobs', 'POST', headers=headers, data={
            'file': (io.BytesIO(b'[{"question":"durable question","answer":"answer"}]'), 'durable.json'),
        })
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json['status'], 'queued')
        job_id = response.json['job_id']
        location = response.headers['Location']
        self.embed.assert_not_called()
        self.assertEqual(self.count('ingestion_job_payloads'), 1)
        restarted = IngestionJobs(self.admin_app.extensions['ingestion_service'], store=MySQLJobStore())
        self.assertEqual(restarted.get(job_id, 'json')['status'], 'queued')
        worker = IngestionWorker(restarted, poll_seconds=0.01)
        self.addCleanup(worker.stop)
        worker.start()
        self.assertEqual(restarted.wait(job_id, 'json', timeout=5)['inserted'], 1)
        self.assertTrue(worker.stop())
        finished = self.admin_request(location, headers=headers).json
        self.assertEqual(finished['status'], 'completed')
        self.assertEqual(finished['result']['inserted'], 1)
        self.assertEqual(self.count('ingestion_job_payloads'), 0)
        self.assertEqual(IngestionJobs(Mock(), store=MySQLJobStore()).get(job_id, 'json'), finished)

    def test_job_restart_recovery_and_queued_cancellation_are_durable(self):
        from services.ingestion_jobs import IngestionJobs
        from services.job_store import MySQLJobStore

        headers = self.login()
        self.assertTrue(self.ingestion_worker.stop())
        jobs = self.admin_app.extensions['ingestion_jobs']
        running = jobs.submit('json', b'[]', 'interrupted.json')['job_id']
        self.assertEqual(jobs.store.claim('crashed-worker')['job_id'], running)
        canceled = jobs.submit('pdf', b'pdf', 'canceled.pdf')['job_id']
        response = self.admin_request('/admin/api/pdf/upload/jobs/' + canceled, 'DELETE', headers=headers)
        self.assertEqual(response.json['status'], 'canceled')
        restarted = IngestionJobs(Mock(), store=MySQLJobStore())
        restarted.store.recover()
        self.assertEqual(restarted.get(running, 'json')['error']['code'], 'ingestion_interrupted')
        self.assertEqual(restarted.get(canceled, 'pdf')['status'], 'canceled')
        self.assertEqual(self.count('ingestion_job_payloads'), 2)
        self.assertTrue(restarted.get(running, 'json')['retryable'])
        self.embed.assert_not_called()

    def pdf_bytes(self):
        with pymupdf.open() as document:
            document.new_page().insert_text((72, 72), 'First page: integration knowledge for support.')
            document.new_page().insert_text((72, 72), 'Second page: contact our support team.')
            return document.tobytes()

    def upload_pdf(self, raw, headers, replace=False):
        return self.admin_request('/admin/api/pdf/upload', 'POST', headers=headers, data={
            'file': (io.BytesIO(raw), 'knowledge.pdf'), 'replace': str(replace).lower(),
        })

    def chat(self, client=None, message='Integration question', **fields):
        response = (client or self.owner).post('/chat', json={'message': message, **fields})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        return response

    def feedback(self, message_id, value='like', client=None):
        return (client or self.owner).post('/feedback', json={'message_id': message_id, 'feedback': value})

    @contextmanager
    def reject_statement(self, table, event):
        self.assertIn(table, TABLES)
        self.assertIn(event, ('INSERT', 'UPDATE', 'DELETE'))
        self.sql(f'CREATE TRIGGER f014_reject BEFORE {event} ON `{table}` '
                 "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'f014_database_failure'")
        try:
            yield
        finally:
            self.sql('DROP TRIGGER f014_reject')

    def test_login_persists_hashed_session_and_logout_revokes_copied_cookie(self):
        headers = self.login()
        with self.client.session_transaction() as signed_session:
            self.assertEqual(set(signed_session), {'admin_session', '_permanent'})
            token = signed_session['admin_session']
        stored = self.sql('SELECT * FROM admin_sessions')
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]['token_hash'], hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(stored[0]['user_id'], self.administrator_id)
        self.assertGreater(stored[0]['expires_at'], stored[0]['last_seen_at'])
        cookie_name = self.admin_app.config['SESSION_COOKIE_NAME']
        copied_cookie = self.client.get_cookie(cookie_name).value
        response = self.admin_request('/logout', 'POST', headers=headers)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.count('admin_sessions'), 0)
        self.client.set_cookie(cookie_name, copied_cookie)
        self.assert_error(self.admin_request('/admin/api/session'), 401, 'authentication_required')
        self.assertGreater(self.count('adminlogger'), 0)

    def test_wrong_unknown_and_disabled_logins_do_not_create_sessions_or_change_accounts(self):
        before = self.sql('SELECT id, password_hash, session_version FROM `admin-users` ORDER BY id')
        self.sql('UPDATE `admin-users` SET is_active = 0 WHERE id = %s', (self.operator_id,))
        for username, password in (('test-admin', 'wrong-password'), ('missing-user', PASSWORD),
                                   ('test-editor', PASSWORD)):
            with self.subTest(username=username):
                token = self.login_form()
                response = self.admin_request('/login', 'POST', data={
                    'username': username, 'password': password, 'csrf_token': token,
                })
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers['X-Error-Code'], 'authentication_required')
                self.assertEqual(self.count('admin_sessions'), 0)
        self.assertEqual(before, self.sql('SELECT id, password_hash, session_version FROM `admin-users` ORDER BY id'))

    def test_login_csrf_anonymous_and_role_forbidden_mutations_leave_data_unchanged(self):
        self.assert_error(self.admin_request('/login', 'POST', data={
            'username': 'test-admin', 'password': PASSWORD,
        }), 403, 'forbidden')
        self.assertEqual(self.count('admin_sessions'), 0)
        self.assert_error(self.admin_request('/admin/api/admin-users', 'POST', json={
            'username': 'injected-user', 'password': PASSWORD,
        }), 401, 'authentication_required')
        headers = self.login(username='test-editor')
        record_id = self.sql('INSERT INTO qa_documents (question, answer, question_hash) VALUES (%s, %s, %s)',
                             ('protected', 'unchanged', self.database.hash_question('protected')))
        for supplied in ({}, {'X-CSRF-Token': 'invalid-proof'}):
            self.assert_error(self.admin_request(f'/admin/api/documents/{record_id}', 'DELETE', headers=supplied),
                              403, 'forbidden')
        for path, method, payload in (
            ('/admin/api/admin-users', 'POST', {'username': 'injected-user', 'password': PASSWORD, 'is_admin': True}),
            (f'/admin/api/admin-users/{self.administrator_id}', 'DELETE', None),
            ('/admin/api/pdf/upload', 'POST', None),
            ('/admin/api/chat/logs?id=1', 'DELETE', None),
            ('/admin/api/models', 'POST', {'model_id': 'not-a-real-model'}),
        ):
            with self.subTest(path=path):
                self.assert_error(self.admin_request(path, method, headers=headers, json=payload), 403, 'forbidden')
        self.assertEqual(self.count('admin-users'), 2)
        self.assertEqual(self.count('qa_documents'), 1)
        self.assertEqual(self.count('pdf_documents'), 0)
        self.assertEqual(self.sql('SELECT answer FROM qa_documents')[0]['answer'], 'unchanged')
        self.embed.assert_not_called()

    def test_security_change_invalidates_database_backed_session(self):
        headers = self.login()
        operator = self.admin_app.test_client()
        self.login(operator, 'test-editor')
        version = self.sql('SELECT session_version FROM `admin-users` WHERE id = %s', (self.operator_id,))[0]
        response = self.admin_request(f'/admin/api/admin-users/{self.operator_id}', 'PATCH',
                                      headers=headers, json={'permissions': []})
        self.assertEqual(response.status_code, 200)
        updated = self.sql('SELECT session_version FROM `admin-users` WHERE id = %s', (self.operator_id,))[0]
        self.assertEqual(updated['session_version'], version['session_version'] + 1)
        self.assert_error(self.admin_request('/admin/api/session', client=operator), 401, 'authentication_required')
        self.assertEqual(self.sql('SELECT * FROM admin_sessions WHERE user_id = %s', (self.operator_id,)), ())

    def test_json_upload_deduplicates_replaces_and_persists_utf8_and_float32_blobs(self):
        headers = self.login()
        item = {'question': '\u0633\u0648\u0627\u0644 \u0622\u0632\u0645\u0627\u06cc\u0634\u06cc', 'answer': '\u067e\u0627\u0633\u062e \u0622\u0632\u0645\u0627\u06cc\u0634\u06cc'}
        response = self.upload_json([item, item], headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json['received'], response.json['inserted']), (2, 1))
        original = self.sql('SELECT * FROM qa_documents')[0]
        self.assertEqual(original['question'], item['question'])
        self.assertEqual(original['answer'], item['answer'])
        self.assertEqual(original['question_hash'], self.database.hash_question(item['question']))
        self.assertEqual(original['embedding_dim'], 3)
        np.testing.assert_array_equal(np.frombuffer(original['embedding'], dtype=np.float32), [1, 0, 0])
        replacement = {**item, 'answer': 'Replacement answer'}
        response = self.upload_json([replacement], headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['skipped'], 1)
        self.assertEqual(self.sql('SELECT answer FROM qa_documents')[0]['answer'], item['answer'])
        response = self.upload_json([replacement], headers, replace=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['updated'], 1)
        self.assertEqual(self.count('qa_documents'), 1)
        self.assertEqual(self.sql('SELECT id, answer FROM qa_documents')[0],
                         {'id': original['id'], 'answer': replacement['answer']})
        listing = self.admin_request('/admin/api/documents?q=Replacement')
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json['total'], 1)
        self.assertTrue(listing.json['items'][0]['has_embedding'])

    def test_invalid_uploads_do_not_embed_or_partially_persist(self):
        headers = self.login()
        for raw, filename in (
            (b'[{}]', 'invalid.json'), (b'{broken', 'invalid.json'), (b'[]', 'invalid.txt'),
            (json.dumps([{'question': 'valid', 'answer': 'valid'}, {'question': 'invalid'}]).encode(), 'mixed.json'),
        ):
            with self.subTest(filename=filename, raw=raw):
                response = self.admin_request('/admin/api/upload', 'POST', headers=headers,
                                              data={'file': (io.BytesIO(raw), filename)})
                self.assert_error(response, 400, 'invalid_request')
                self.assertEqual(self.count('qa_documents'), 0)
        self.assert_error(self.upload_pdf(b'not a PDF', headers), 400, 'invalid_request')
        self.assertEqual(self.count('pdf_documents'), 0)
        self.assertEqual(self.count('pdf_chunks'), 0)
        self.embed.assert_not_called()

    def test_pdf_upload_duplicate_replace_and_delete_cascade(self):
        headers = self.login()
        raw = self.pdf_bytes()
        response = self.upload_pdf(raw, headers)
        self.assertEqual(response.status_code, 200)
        original_id = response.json['document_id']
        stored = self.sql('SELECT * FROM pdf_documents')[0]
        self.assertEqual(stored['document_hash'], hashlib.sha256(raw).hexdigest())
        self.assertEqual((stored['status'], stored['page_count'], stored['chunk_count'], stored['embedded_chunk_count']),
                         ('completed', 2, 2, 2))
        chunks = self.sql('SELECT * FROM pdf_chunks ORDER BY chunk_index')
        self.assertEqual([chunk['page_start'] for chunk in chunks], [1, 2])
        for chunk in chunks:
            self.assertEqual(chunk['page_start'], chunk['page_end'])
            self.assertEqual(chunk['document_id'], original_id)
            np.testing.assert_array_equal(np.frombuffer(chunk['embedding'], dtype=np.float32), [1, 0, 0])
        self.assert_error(self.upload_pdf(raw, headers), 409, 'conflict')
        self.assertEqual((self.count('pdf_documents'), self.count('pdf_chunks')), (1, 2))
        response = self.upload_pdf(raw, headers, replace=True)
        self.assertEqual(response.status_code, 200)
        replacement_id = response.json['document_id']
        self.assertNotEqual(original_id, replacement_id)
        self.assertEqual(self.sql('SELECT * FROM pdf_chunks WHERE document_id = %s', (original_id,)), ())
        response = self.admin_request(f'/admin/api/pdf/documents/{replacement_id}', 'DELETE', headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual((self.count('pdf_documents'), self.count('pdf_chunks')), (0, 0))
        self.assert_error(self.admin_request(f'/admin/api/pdf/documents/{replacement_id}', 'DELETE', headers=headers),
                          404, 'not_found')

    def test_chat_retrieves_uploaded_knowledge_and_persists_server_owned_messages(self):
        self.assertEqual(self.upload_json([{'question': 'Integration question', 'answer': 'Stored knowledge'}],
                                         self.login()).status_code, 200)
        first = self.chat(user_id='forged-owner')
        second = self.chat(message='Next question')
        stranger = self.ui_app.test_client()
        third = self.chat(stranger)
        rows = self.sql('SELECT * FROM chat_history ORDER BY id')
        self.assertEqual([row['id'] for row in rows],
                         [first.json['message_id'], second.json['message_id'], third.json['message_id']])
        self.assertEqual(rows[0]['owner_hash'], rows[1]['owner_hash'])
        self.assertNotEqual(rows[0]['owner_hash'], rows[2]['owner_hash'])
        self.assertEqual(rows[0]['user_id'], 'chat_' + rows[0]['owner_hash'][:32])
        self.assertNotEqual(rows[0]['user_id'], 'forged-owner')
        self.assertEqual(rows[0]['user_message'], 'Integration question')
        self.assertEqual(rows[0]['bot_response'], first.json['reply'])
        self.assertEqual(first.json['reply'], ANSWER)
        self.assertEqual(first.json['sources'][0]['type'], 'qa')
        self.assertEqual(first.json['score'], 1.0)
        self.assertEqual(rows[0]['ip_address'], '')
        self.assertIsNone(rows[0]['feedback'])
        self.assertIsNone(rows[0]['feedback_at'])
        self.assertEqual(self.generate.call_count, 3)

    def test_chat_without_hits_persists_fallback_and_zero_retention_disables_storage(self):
        response = self.chat()
        stored = self.sql('SELECT * FROM chat_history')[0]
        self.assertEqual(response.json['sources'], [])
        self.assertEqual(stored['bot_response'], response.json['reply'])
        self.generate.assert_not_called()
        with patch.dict('os.environ', {'RETENTION_CHAT_DAYS': '0'}):
            fresh_client = self.ui_app.test_client()
            response = self.chat(fresh_client)
            self.assertIsNone(response.json['message_id'])
            self.assertFalse(response.json['feedback_enabled'])
            self.assertNotIn('Set-Cookie', response.headers)
            self.assertEqual(self.count('chat_history'), 1)
            self.assert_error(self.feedback(stored['id']), 403, 'forbidden')

    def test_feedback_repeated_rating_and_overwrite_are_persisted(self):
        message_id = self.chat().json['message_id']
        for value in ('like', 'like', 'dislike'):
            with self.subTest(value=value):
                response = self.feedback(message_id, value)
                self.assertEqual(response.status_code, 200)
                stored = self.sql('SELECT feedback, feedback_at FROM chat_history WHERE id = %s', (message_id,))[0]
                self.assertEqual(stored['feedback'], value)
                self.assertIsNotNone(stored['feedback_at'])
        self.assertEqual(self.count('chat_history'), 1)

    def test_feedback_rejects_anonymous_foreign_unknown_legacy_and_invalid_requests(self):
        message_id = self.chat().json['message_id']
        stranger = self.ui_app.test_client()
        self.assert_error(self.feedback(message_id, client=stranger), 401, 'authentication_required')
        self.chat(stranger)
        self.assert_error(self.feedback(message_id, client=stranger), 404, 'not_found')
        self.assert_error(self.feedback(message_id + 1000000), 404, 'not_found')
        legacy_id = self.sql('INSERT INTO chat_history (user_id, ip_address, user_message, bot_response) '
                             'VALUES (%s, %s, %s, %s)', ('legacy', '', 'old', 'old'))
        self.assert_error(self.feedback(legacy_id), 404, 'not_found')
        for invalid in ({'message_id': True, 'feedback': 'like'},
                        {'message_id': message_id, 'feedback': 'invalid'}):
            self.assert_error(self.owner.post('/feedback', json=invalid), 400, 'invalid_request')
        self.assertEqual(self.sql('SELECT COUNT(*) AS total FROM chat_history WHERE feedback IS NOT NULL')[0]['total'], 0)

    def test_mysql_chat_insert_failure_returns_503_without_history_and_recovers(self):
        with self.reject_statement('chat_history', 'INSERT'):
            self.assert_error(self.owner.post('/chat', json={'message': 'Fail to save'}), 503, 'service_unavailable')
            self.assertEqual(self.count('chat_history'), 0)
        self.chat()
        self.assertEqual(self.count('chat_history'), 1)

    def test_mysql_feedback_failure_keeps_original_rating_and_recovers(self):
        message_id = self.chat().json['message_id']
        self.assertEqual(self.feedback(message_id).status_code, 200)
        original = self.sql('SELECT * FROM chat_history')
        with self.reject_statement('chat_history', 'UPDATE'):
            self.assert_error(self.feedback(message_id, 'dislike'), 503, 'service_unavailable')
            self.assertEqual(self.sql('SELECT * FROM chat_history'), original)
        self.assertEqual(self.feedback(message_id, 'dislike').status_code, 200)
        self.assertEqual(self.sql('SELECT feedback FROM chat_history')[0]['feedback'], 'dislike')

    def test_mysql_json_insert_failure_returns_500_without_a_record_and_recovers(self):
        headers = self.login()
        rows = [{'question': 'New question', 'answer': 'New answer'}]
        with self.reject_statement('qa_documents', 'INSERT'):
            self.assert_error(self.upload_json(rows, headers), 500, 'internal_error')
            self.assertEqual(self.count('qa_documents'), 0)
        self.assertEqual(self.upload_json(rows, headers).status_code, 200)
        self.assertEqual(self.count('qa_documents'), 1)

    def test_mysql_pdf_chunk_failure_rolls_back_and_retry_recovers(self):
        headers = self.login()
        raw = self.pdf_bytes()
        with self.reject_statement('pdf_chunks', 'INSERT'):
            self.assert_error(self.upload_pdf(raw, headers), 500, 'internal_error')
            self.assertEqual(self.count('pdf_documents'), 0)
            self.assertEqual(self.count('pdf_chunks'), 0)
        self.assertEqual(self.upload_pdf(raw, headers, replace=True).status_code, 200)
        self.assertEqual(self.sql('SELECT status FROM pdf_documents')[0]['status'], 'completed')
        self.assertEqual((self.count('pdf_documents'), self.count('pdf_chunks')), (1, 2))

    def test_publication_failure_keeps_old_pdf_searchable_and_safe_retry_commits_once(self):
        headers = self.login()
        raw = self.pdf_bytes()
        self.assertEqual(self.upload_pdf(raw, headers).status_code, 200)
        before_docs = self.sql('SELECT * FROM pdf_documents')
        before_chunks = self.sql('SELECT * FROM pdf_chunks ORDER BY id')
        with self.reject_statement('pdf_chunks', 'INSERT'):
            self.assert_error(self.upload_pdf(raw, headers, replace=True), 500, 'internal_error')
        self.assertEqual(self.sql('SELECT * FROM pdf_documents'), before_docs)
        self.assertEqual(self.sql('SELECT * FROM pdf_chunks ORDER BY id'), before_chunks)
        failed = self.sql("SELECT job_id FROM ingestion_jobs WHERE status='failed'")[0]['job_id']
        location = '/admin/api/pdf/upload/jobs/' + failed
        self.assertEqual(self.admin_request(location + '/retry', 'POST', headers=headers).status_code, 202)
        self.admin_app.extensions['ingestion_jobs'].wait(failed, 'pdf', timeout=10)
        self.assertEqual((self.count('pdf_documents'), self.count('pdf_chunks')), (1, 2))
        self.assert_error(self.admin_request(location + '/retry', 'POST', headers=headers), 409, 'conflict')

    def test_json_later_row_failure_rolls_back_prior_rows_and_job_completion(self):
        headers = self.login()
        self.sql("CREATE TRIGGER f027_second BEFORE INSERT ON qa_documents FOR EACH ROW "
                 "BEGIN IF NEW.question='fail second' THEN SIGNAL SQLSTATE '45000'; END IF; END")
        try:
            self.assert_error(self.upload_json([
                {'question': 'first row', 'answer': 'a'}, {'question': 'fail second', 'answer': 'b'},
            ], headers), 500, 'internal_error')
        finally:
            self.sql('DROP TRIGGER f027_second')
        self.assertEqual(self.count('qa_documents'), 0)
        failed = self.sql("SELECT job_id FROM ingestion_jobs WHERE status='failed'")[0]['job_id']
        location = '/admin/api/upload/jobs/' + failed
        self.assertEqual(self.admin_request(location + '/retry', 'POST', headers=headers).status_code, 202)
        self.assertEqual(self.admin_app.extensions['ingestion_jobs'].wait(failed, 'json', timeout=10)['inserted'], 2)
        self.assertEqual(self.count('qa_documents'), 2)

    def test_cancellation_during_publication_rolls_back_and_never_becomes_searchable(self):
        self.assertTrue(self.ingestion_worker.stop())
        jobs = self.admin_app.extensions['ingestion_jobs']
        job_id = jobs.submit('json', b'[{"question":"first","answer":"a"},{"question":"second","answer":"b"}]', 'cancel.json')['job_id']
        original = jobs.store.progress
        observed = []

        def progress(job, event):
            original(job, event)
            if event['stage'] == 'publishing' and event['completed'] == 1:
                observed.append(self.count('qa_documents'))
                jobs.cancel(job_id, 'json')

        with patch.object(jobs.store, 'progress', side_effect=progress):
            jobs.process_one('manual-worker', lambda: None)
        self.assertEqual(observed, [0])
        self.assertEqual(self.count('qa_documents'), 0)
        self.assertEqual(jobs.get(job_id, 'json')['status'], 'canceled')
        jobs.retry(job_id, 'json')
        jobs.process_one('manual-worker', lambda: None)
        self.assertEqual(self.count('qa_documents'), 2)
        self.assertEqual(jobs.get(job_id, 'json')['status'], 'completed')

    def test_mysql_session_insert_failure_does_not_authenticate_and_recovers(self):
        token = self.login_form()
        with self.reject_statement('admin_sessions', 'INSERT'):
            self.assert_error(self.admin_request('/login', 'POST', data={
                'username': 'test-admin', 'password': PASSWORD, 'csrf_token': token,
            }), 500, 'internal_error')
            self.assertEqual(self.count('admin_sessions'), 0)
            self.assert_error(self.admin_request('/admin/api/session'), 401, 'authentication_required')
        self.login()
        self.assertEqual(self.count('admin_sessions'), 1)

    def test_connection_failure_returns_safe_errors_without_database_mutations(self):
        token = self.login_form()
        unavailable = OperationalError(2003, 'f014_database_failure')
        with patch.object(self.accounts, 'get_conn', side_effect=unavailable):
            self.assert_error(self.admin_request('/login', 'POST', data={
                'username': 'test-admin', 'password': PASSWORD, 'csrf_token': token,
            }), 500, 'internal_error')
        self.assertEqual(self.count('admin_sessions'), 0)
        with patch.object(self.user, 'get_conn', side_effect=unavailable):
            self.assert_error(self.owner.post('/chat', json={'message': 'Connection unavailable'}),
                              503, 'service_unavailable')
        self.assertEqual(self.count('chat_history'), 0)
        self.chat()
        self.assertEqual(self.count('chat_history'), 1)

    def test_administrator_chat_deletion_database_failure_and_success(self):
        message_id = self.chat().json['message_id']
        headers = self.login()
        with self.reject_statement('chat_history', 'DELETE'):
            self.assert_error(self.admin_request(f'/admin/api/chat/logs?id={message_id}', 'DELETE', headers=headers),
                              500, 'internal_error')
            self.assertEqual(self.count('chat_history'), 1)
        response = self.admin_request(f'/admin/api/chat/logs?id={message_id}', 'DELETE', headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.count('chat_history'), 0)
        self.assert_error(self.admin_request(f'/admin/api/chat/logs?id={message_id}', 'DELETE', headers=headers),
                          404, 'not_found')
