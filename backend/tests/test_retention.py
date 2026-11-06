"""Execute retention mutations against an in-memory SQL adapter, never live MySQL."""

import importlib
import io
import json
import sqlite3
import sys
import types
import unittest
from contextlib import ExitStack, redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 5, 12, 0, 0)
PRIVATE = 'private question 192.0.2.123 owner_hash password=/private/model SQL secret'


class MemoryStore:
    def __init__(self):
        self.sql = sqlite3.connect(':memory:')
        self.sql.row_factory = sqlite3.Row
        self.sql.execute('CREATE TABLE chat_history (id INTEGER PRIMARY KEY, user_id TEXT, owner_hash TEXT, '
                         'ip_address TEXT, user_message TEXT, bot_response TEXT, feedback TEXT, '
                         'feedback_at TEXT, created_at TEXT)')
        self.sql.execute('CREATE TABLE adminlogger (id INTEGER PRIMARY KEY, message TEXT, created_at TEXT)')
        self.sql.execute('CREATE TABLE qa_documents (id INTEGER PRIMARY KEY, answer TEXT)')
        self.sql.execute('INSERT INTO qa_documents VALUES (1, ?)', (PRIVATE,))
        self.queries = []
        self.acquired = 1
        self.mutations = 0
        self.fail_mutation = None
        self.closed = 0
        self.rollbacks = 0

    def add_chat(self, identity, days, *, feedback='like'):
        created = (NOW - timedelta(days=days)).isoformat(' ') if days is not None else None
        self.sql.execute('INSERT INTO chat_history VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                         (identity, PRIVATE, PRIVATE, '192.0.2.123', PRIVATE, PRIVATE, feedback,
                          NOW.isoformat(' '), created))
        self.sql.commit()

    def add_log(self, identity, days):
        created = (NOW - timedelta(days=days)).isoformat(' ') if days is not None else None
        self.sql.execute('INSERT INTO adminlogger VALUES (?, ?, ?)', (identity, PRIVATE, created))
        self.sql.commit()

    def cursor(self):
        return MemoryCursor(self)

    def begin(self):
        self.sql.execute('BEGIN')

    def commit(self):
        self.sql.commit()

    def rollback(self):
        self.rollbacks += 1
        self.sql.rollback()

    def close(self):
        self.closed += 1

    def rows(self, table):
        return [dict(row) for row in self.sql.execute(f'SELECT * FROM {table} ORDER BY id')]


class MemoryCursor:
    def __init__(self, store):
        self.store = store
        self.result = None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, traceback):
        return False

    def execute(self, query, parameters=()):
        self.store.queries.append((query, parameters))
        if query.startswith('SELECT GET_LOCK'):
            self.result = {'acquired': self.store.acquired}
            return
        if query.startswith('SELECT RELEASE_LOCK') or query.startswith('SET time_zone'):
            return
        if query.startswith('SELECT UTC_TIMESTAMP'):
            self.result = {'cutoff_time': NOW}
            return
        if query.startswith(('UPDATE ', 'DELETE ')):
            self.store.mutations += 1
            if self.store.fail_mutation == self.store.mutations:
                raise RuntimeError(PRIVATE)
            mutation, separator, predicate = query.partition(' WHERE ')
            table = 'chat_history' if 'chat_history' in mutation else 'adminlogger'
            query = f'{mutation} WHERE id IN (SELECT id FROM {table} WHERE {predicate})'
        query = query.replace('%s', '?')
        parameters = tuple(value.isoformat(' ') if isinstance(value, datetime) else value for value in parameters)
        cursor = self.store.sql.execute(query, parameters)
        self.rowcount = cursor.rowcount
        self.result = cursor.fetchone() if query.startswith('SELECT') else None

    def fetchone(self):
        return self.result


class RetentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resources = ExitStack()
        cls.addClassCleanup(cls.resources.close)
        cls.resources.enter_context(patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path]))
        cls.settings = importlib.import_module('settings')
        cls.retention = importlib.import_module('retention')

    def setUp(self):
        self.store = MemoryStore()
        self.addCleanup(self.store.sql.close)
        self.policy = self.settings.RetentionPolicy()
        self.records = []

    def run_job(self, **options):
        return self.retention.run_retention(policy=options.pop('policy', self.policy),
                                            connect=lambda: self.store, emit=self.records.append, **options)

    def test_defaults_delete_expired_chats_and_logs_clear_all_persisted_ips(self):
        for identity, days in enumerate((31, 30, 29, 7, 1, -1, None), start=1):
            self.store.add_chat(identity, days)
        for identity, days in enumerate((15, 14, 13, None), start=1):
            self.store.add_log(identity, days)
        record = self.run_job()
        self.assertEqual(record['status'], 'completed')
        self.assertEqual(record['counts'], {'deleted_chats': 3, 'cleared_ips': 4,
                                           'cleared_feedback': 0, 'deleted_logs': 3})
        self.assertEqual([row['id'] for row in self.store.rows('chat_history')], [3, 4, 5, 6])
        self.assertTrue(all(row['ip_address'] == '' for row in self.store.rows('chat_history')))
        self.assertEqual([row['id'] for row in self.store.rows('adminlogger')], [3])
        self.assertEqual(self.store.rows('qa_documents')[0]['answer'], PRIVATE)
        self.assertEqual(self.store.closed, 1)
        self.assertTrue(any(query == "SET time_zone = '+00:00'" for query, parameters in self.store.queries))

    def test_independent_field_lifetimes_do_not_extend_from_feedback_updates(self):
        for identity, days in enumerate((30, 14, 7, 6, -1, None), start=1):
            self.store.add_chat(identity, days)
        record = self.run_job(policy=replace(self.policy, chat_days=60, ip_days=14, feedback_days=7))
        rows = self.store.rows('chat_history')
        self.assertEqual(record['counts']['cleared_ips'], 2)
        self.assertEqual(record['counts']['cleared_feedback'], 3)
        self.assertEqual(rows[2]['ip_address'], '192.0.2.123')
        self.assertIsNone(rows[2]['feedback'])
        self.assertIsNone(rows[2]['feedback_at'])
        self.assertEqual(rows[3]['feedback'], 'like')

    def test_zero_retention_purges_even_future_dated_legacy_data(self):
        self.store.add_chat(1, -1)
        self.store.add_log(1, -1)
        record = self.run_job(policy=replace(self.policy, chat_days=0, log_days=0))
        self.assertEqual(record['counts']['deleted_chats'], 1)
        self.assertEqual(record['counts']['deleted_logs'], 1)
        self.assertEqual(self.store.rows('chat_history'), [])
        self.assertEqual(self.store.rows('adminlogger'), [])

    def test_deletion_pause_performs_no_database_access(self):
        connect = Mock(side_effect=AssertionError('Paused deletion must not access the store'))
        record = self.retention.run_retention(policy=replace(self.policy, delete_enabled=False),
                                              connect=connect, emit=self.records.append)
        self.assertEqual(record['status'], 'skipped_policy')
        connect.assert_not_called()
        self.assertEqual(len(self.records), 1)

    def test_dry_run_can_preview_paused_policy_without_mutations(self):
        self.store.add_chat(1, 31)
        self.store.add_chat(2, 1)
        self.store.add_log(1, 20)
        before = self.store.rows('chat_history'), self.store.rows('adminlogger')
        record = self.run_job(dry_run=True, policy=replace(self.policy, delete_enabled=False))
        self.assertEqual(record['status'], 'completed')
        self.assertEqual(record['counts'], {'deleted_chats': 1, 'cleared_ips': 2,
                                           'cleared_feedback': 1, 'deleted_logs': 1})
        self.assertEqual(before, (self.store.rows('chat_history'), self.store.rows('adminlogger')))
        self.assertEqual(self.store.mutations, 0)

    def test_batch_caps_report_partial_and_repeated_jobs_converge(self):
        for identity in range(1, 5):
            self.store.add_chat(identity, 31)
        policy = replace(self.policy, batch_size=1, max_batches=1)
        record = self.run_job(policy=policy)
        self.assertEqual(record['status'], 'partial')
        self.assertTrue(record['batch_limit_reached'])
        self.assertEqual(record['counts']['deleted_chats'], 1)
        for attempt in range(4):
            record = self.run_job(policy=policy)
        self.assertEqual(record['status'], 'completed')
        self.assertFalse(any(record['counts'].values()))
        self.assertEqual(self.store.rows('chat_history'), [])

    def test_failure_rolls_back_current_batch_and_reports_only_committed_counts(self):
        self.store.add_chat(1, 31)
        self.store.add_chat(2, 1)
        self.store.fail_mutation = 2
        record = self.run_job()
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['code'], 'retention_failed')
        self.assertEqual(record['counts']['deleted_chats'], 1)
        self.assertEqual(record['counts']['cleared_ips'], 0)
        self.assertEqual(self.store.rows('chat_history')[0]['ip_address'], '192.0.2.123')
        self.assertEqual(self.store.rollbacks, 1)
        self.assertTrue(any('RELEASE_LOCK' in query for query, parameters in self.store.queries))
        self.assertEqual(self.store.closed, 1)
        self.assertNotIn(PRIVATE, json.dumps(record))

    def test_busy_lock_skips_cleanup_and_closes_connection(self):
        self.store.acquired = 0
        record = self.run_job()
        self.assertEqual(record['status'], 'skipped_locked')
        self.assertEqual(self.store.mutations, 0)
        self.assertEqual(self.store.closed, 1)
        self.assertFalse(any('RELEASE_LOCK' in query for query, parameters in self.store.queries))

    def test_completion_has_only_non_sensitive_fields_even_on_connection_failure(self):
        record = self.retention.run_retention(policy=self.policy, connect=Mock(side_effect=RuntimeError(PRIVATE)),
                                              emit=self.records.append)
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(set(record), {'event', 'status', 'dry_run', 'finished_at', 'counts',
                                       'batch_limit_reached', 'code'})
        self.assertTrue(record['finished_at'].endswith('+00:00'))
        self.assertEqual(self.records, [record])
        self.assertNotIn(PRIVATE, json.dumps(record))

    def test_settings_defaults_bounds_and_invalid_values_are_safe(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(self.settings.retention_settings(), self.policy)
        for name, value in [('RETENTION_CHAT_DAYS', '-1'), ('RETENTION_LOG_DAYS', '3651'),
                            ('RETENTION_IP_DAYS', 'forever'), ('RETENTION_FEEDBACK_DAYS', PRIVATE),
                            ('RETENTION_DELETE_ENABLED', PRIVATE), ('RETENTION_BATCH_SIZE', '0'),
                            ('RETENTION_MAX_BATCHES', '1001'), ('RETENTION_INTERVAL_SECONDS', '59')]:
            with self.subTest(name=name), patch.dict('os.environ', {name: value}, clear=True):
                with self.assertRaises(self.settings.ConfigurationError) as raised:
                    self.settings.retention_settings()
                self.assertNotIn(PRIVATE, str(raised.exception))

    def test_cli_defaults_to_preview_and_apply_honors_policy(self):
        with patch.object(self.retention, 'validate_settings'), \
                patch.object(self.retention, 'run_retention', return_value={'status': 'completed'}) as run, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(self.retention.main([]), 0)
            run.assert_called_once_with(dry_run=True)
            run.reset_mock()
            self.assertEqual(self.retention.main(['--apply']), 0)
            run.assert_called_once_with(dry_run=False)

    def test_worker_is_shared_stoppable_and_never_started_on_module_import(self):
        ui = Flask('retention_test_ui')
        admin = Flask('retention_test_admin')
        with patch.object(self.retention, 'RetentionWorker') as worker_type, \
                patch.object(self.retention.atexit, 'register'):
            worker = worker_type.return_value
            first = self.retention.start_retention_worker(ui, admin)
            self.assertIs(first, self.retention.start_retention_worker(ui, admin))
            self.assertIs(ui.extensions['retention_worker'], admin.extensions['retention_worker'])
            worker.thread.start.assert_called_once()
        worker = self.retention.RetentionWorker(self.policy)
        worker.stopped = Mock()
        worker.stopped.is_set.return_value = False
        worker.stopped.wait.return_value = True
        with patch.object(self.retention, 'run_retention') as run:
            worker._run()
            run.assert_called_once_with(policy=self.policy)
        worker.stopped.wait.assert_called_once_with(3600)
        worker.stop()
        worker.stopped.set.assert_called_once()

    def test_dry_run_completion_does_not_write_database_logs(self):
        database = types.ModuleType('database')
        database.logger = Mock()
        record = {'status': 'completed', 'dry_run': True, 'counts': {}}
        with patch.dict(sys.modules, {'database': database}), self.assertLogs('chatbot.retention') as captured:
            self.retention.emit_completion(record)
        database.logger.logDrop.assert_not_called()
        self.assertIn('completed', captured.output[0])

    def test_serving_initializes_tables_and_models_before_starting_shared_worker(self):
        calls = Mock()
        modules = {}
        for name, functions in {
            'database': ('init_logger_table', 'init_db', 'init_admin_users_table', 'init_documents_table',
                         'init_documents_chunk_table', 'init_history_table'),
            'interface': ('get_embedder',), 'generator': ('get_generator',),
            'controller': ('_load_state',),
        }.items():
            module = types.ModuleType(name)
            for function in functions:
                operation = Mock()
                setattr(module, function, operation)
                calls.attach_mock(operation, function)
            modules[name] = module
        modules['database'].logger = Mock()
        expected_apps = (Flask('startup_user'), Flask('startup_admin'))
        with patch.dict(sys.modules, modules), patch.dict('os.environ', {'APP_ENV': 'test'}, clear=True), \
                patch.object(self.retention, 'start_retention_worker') as start, \
                redirect_stdout(io.StringIO()):
            calls.attach_mock(start, 'start_retention_worker')
            main = importlib.import_module('main')
            with patch.object(main, 'validate_settings'), patch.object(main, 'bind_host', return_value='127.0.0.1'), \
                    patch.object(main, 'create_apps', return_value=expected_apps):
                apps = main.create_serving_apps()
        self.assertEqual(apps, expected_apps)
        self.assertEqual([call[0] for call in calls.mock_calls], [
            'init_logger_table', 'init_db', 'init_admin_users_table', 'init_documents_table',
            'init_documents_chunk_table', 'init_history_table', '_load_state', 'get_embedder', 'get_generator',
            'start_retention_worker',
        ])

    def test_development_runner_only_serves_and_omits_access_logs(self):
        serving = importlib.import_module('serving')
        app = Mock()
        with patch.object(serving, 'validate_settings'), patch.object(serving, 'is_development', return_value=True), \
                patch.object(serving, 'bind_host', return_value='127.0.0.1'), \
                patch.object(self.retention, 'start_retention_worker') as start:
            serving.serve_development(app, 5000)
        start.assert_not_called()
        self.assertIs(app.run.call_args.kwargs['request_handler'], serving.NoAccessLogRequestHandler)
        with patch.object(serving.WSGIRequestHandler, 'log_request') as log:
            serving.NoAccessLogRequestHandler.log_request(Mock(), '200', '100')
        log.assert_not_called()


if __name__ == '__main__':
    unittest.main()
