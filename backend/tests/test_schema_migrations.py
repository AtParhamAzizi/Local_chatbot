"""Offline migration protocol checks; real preservation tests live in mysql/."""

import importlib
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

from core_support import BACKEND_DIR, module_double


class SchemaStore:
    def __init__(self, migrations, *, legacy=False):
        self.migrations = migrations
        self.tables = set(migrations.REQUIRED_COLUMNS) if legacy else set()
        self.records = {}
        self.queries = []
        self.result = []
        self.locked = False
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.execute.side_effect = self.execute
        self.cursor.fetchall.side_effect = lambda: self.result
        self.cursor.fetchone.side_effect = lambda: self.result[0] if self.result else None

    def execute(self, query, parameters=()):
        self.queries.append((query, parameters))
        self.result = []
        if 'information_schema.TABLES' in query:
            self.result = [{'name': table} for table in self.tables]
        elif query.startswith('SELECT version'):
            self.result = [dict(self.records[version]) for version in sorted(self.records)]
        elif 'SELECT DATABASE()' in query:
            self.result = [{'name': 'isolated_schema'}]
        elif 'GET_LOCK' in query:
            self.result = [{'acquired': int(not self.locked)}]
        elif query.startswith('SHOW COLUMNS'):
            table = query.split('`')[1]
            self.result = [{'Field': name} for name in self.migrations.REQUIRED_COLUMNS[table]]
        elif query.strip().startswith('CREATE TABLE IF NOT EXISTS schema_migrations'):
            self.tables.add('schema_migrations')
        elif query.startswith('INSERT INTO schema_migrations'):
            version, name, checksum, backup_path, backup_sha256 = parameters
            self.records[version] = dict(version=version, name=name, checksum=checksum, status='running',
                                         backup_path=backup_path, backup_sha256=backup_sha256)
        elif query.startswith('UPDATE schema_migrations'):
            self.records[parameters[0]]['status'] = 'applied' if "'applied'" in query else 'failed'

    def apply(self, migration, cursor):
        self.tables.update(self.migrations.REQUIRED_COLUMNS)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path]))
        self.migrations = importlib.import_module('schema_migrations')
        self.directory = self.enterContext(TemporaryDirectory())
        self.backup = Path(self.directory) / 'before.sql'
        self.backup.write_bytes(b'Offline backup identity fixture; no real database is connected.\n')
        self.store = SchemaStore(self.migrations)
        self.connect = Mock(return_value=self.store.connection)
        self.apply = self.enterContext(patch.object(
            self.migrations.Migration, 'apply', autospec=True, side_effect=self.store.apply,
        ))

    def run_upgrade(self, **options):
        return self.migrations.upgrade(connect=self.connect, **options)

    def test_empty_schema_records_all_versions_and_reports_current(self):
        report = self.run_upgrade()
        self.assertEqual(report, {'schema_version': 4, 'latest_version': 4, 'pending': [], 'incomplete': []})
        self.assertEqual(self.apply.call_count, 4)
        self.assertEqual([record['status'] for record in self.store.records.values()], ['applied'] * 4)
        self.assertTrue(all(record['backup_sha256'] is None for record in self.store.records.values()))
        self.store.connection.autocommit.assert_called_once_with(True)
        self.store.connection.close.assert_called_once()

    def test_existing_database_requires_backup_before_any_ddl_or_history_write(self):
        self.store.tables.update(self.migrations.REQUIRED_COLUMNS)
        for backup in (None, Path(self.directory), Path(self.directory) / 'missing.sql'):
            with self.subTest(backup=backup), self.assertRaises(self.migrations.MigrationError):
                self.run_upgrade(backup=backup)
        self.backup.write_bytes(b'')
        with self.assertRaises(self.migrations.MigrationError):
            self.run_upgrade(backup=self.backup)
        self.assertEqual(self.store.records, {})
        self.assertFalse(any(query.lstrip().startswith(('CREATE', 'ALTER', 'INSERT', 'UPDATE'))
                             for query, parameters in self.store.queries))
        self.apply.assert_not_called()

    def test_existing_upgrade_records_backup_digest_and_repeat_is_noop(self):
        self.store.tables.update(self.migrations.REQUIRED_COLUMNS)
        report = self.run_upgrade(backup=self.backup)
        path, checksum = self.migrations._backup_identity(self.backup)
        self.assertTrue(all(record['backup_path'] == path and record['backup_sha256'] == checksum
                            for record in self.store.records.values()))
        self.store.queries.clear()
        self.apply.reset_mock()
        self.assertEqual(self.run_upgrade(), report)
        self.apply.assert_not_called()
        self.assertFalse(any(query.lstrip().startswith(('CREATE', 'ALTER', 'INSERT', 'UPDATE'))
                             for query, parameters in self.store.queries))

    def test_failed_ddl_is_recorded_and_requires_explicit_resume_with_backup(self):
        def fail(migration, cursor):
            self.store.apply(migration, cursor)
            if migration.version == 2:
                raise RuntimeError('simulated DDL failure after implicit commit')

        self.apply.side_effect = fail
        with self.assertRaises(RuntimeError):
            self.run_upgrade()
        self.assertEqual(self.store.records[1]['status'], 'applied')
        self.assertEqual(self.store.records[2]['status'], 'failed')
        self.assertNotIn(3, self.store.records)
        with self.assertRaisesRegex(self.migrations.MigrationError, 'interrupted'):
            self.run_upgrade(backup=self.backup)
        with self.assertRaisesRegex(self.migrations.MigrationError, '--backup'):
            self.run_upgrade(resume=True)
        self.apply.side_effect = self.store.apply
        report = self.run_upgrade(resume=True, backup=self.backup)
        self.assertEqual(report['schema_version'], 4)
        self.assertEqual([call.args[0].version for call in self.apply.call_args_list], [1, 2, 2, 3, 4])
        self.assertTrue(any('RELEASE_LOCK' in query for query, parameters in self.store.queries))

    def test_process_interruption_leaves_running_record_and_cannot_serve(self):
        self.run_upgrade()
        self.store.records[4]['status'] = 'running'
        with self.assertRaisesRegex(self.migrations.MigrationError, 'version 3'):
            self.migrations.require_current_schema(connect=self.connect)
        with self.assertRaises(self.migrations.MigrationError):
            self.run_upgrade(backup=self.backup)

    def test_status_and_startup_checks_are_read_only_for_current_and_legacy(self):
        for initialized in (False, True):
            if initialized:
                self.run_upgrade()
            self.store.queries.clear()
            status = self.migrations.schema_status(connect=self.connect)
            self.assertEqual(status['schema_version'], 4 if initialized else 0)
            if initialized:
                self.assertEqual(self.migrations.require_current_schema(connect=self.connect), status)
            else:
                with self.assertRaises(self.migrations.MigrationError):
                    self.migrations.require_current_schema(connect=self.connect)
            self.assertTrue(all(query.startswith('SELECT') for query, parameters in self.store.queries))

    def test_tampered_unknown_or_gapped_history_fails_closed(self):
        self.run_upgrade()
        original = dict(self.store.records[4])
        for changes in ({'checksum': '0' * 64}, {'version': 9}, {'name': 'unknown'}):
            self.store.records[4] = {**original, **changes}
            with self.subTest(changes=changes), self.assertRaises(self.migrations.MigrationError):
                self.run_upgrade(backup=self.backup)
        self.store.records[4] = original
        self.store.records[1]['status'] = 'failed'
        with self.assertRaisesRegex(self.migrations.MigrationError, 'later history'):
            self.migrations.schema_status(connect=self.connect)

    def test_busy_lock_never_creates_ledger_and_closes_connection(self):
        self.store.locked = True
        with self.assertRaisesRegex(self.migrations.MigrationError, 'schema lock'):
            self.run_upgrade()
        self.apply.assert_not_called()
        self.assertEqual(self.store.records, {})
        self.store.connection.close.assert_called_once()

    def test_unknown_legacy_layout_fails_before_ledger_creation(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [{'Field': 'id'}]
        with self.assertRaisesRegex(self.migrations.MigrationError, 'legacy layout'):
            self.migrations._validate_existing(cursor, {'qa_documents'})
        self.assertTrue(all(call.args[0].startswith('SHOW COLUMNS') for call in cursor.execute.call_args_list))

    def test_connection_failure_never_attempts_application_database_logging(self):
        database = module_double('database', DB_CONFIG={'host': 'forbidden.invalid'}, logger=Mock())
        with patch.dict(sys.modules, {'database': database}), \
                patch('pymysql.connect', side_effect=RuntimeError('connection unavailable')) as connect:
            with self.assertRaises(RuntimeError):
                self.migrations.require_current_schema()
        connect.assert_called_once_with(**database.DB_CONFIG)
        database.logger.logDrop.assert_not_called()

    def test_startup_checks_schema_before_any_other_runtime_work(self):
        bootstrap = importlib.import_module('bootstrap')
        with patch.object(self.migrations, 'require_current_schema', side_effect=self.migrations.MigrationError('upgrade required')), \
                patch.object(bootstrap, 'initialize_models') as models, \
                patch('threading.Thread.start') as thread:
            with patch('settings.validate_settings'):
                with self.assertRaises(self.migrations.MigrationError):
                    bootstrap.initialize_runtime(Mock(), Mock())
        models.assert_not_called()
        thread.assert_not_called()

    def test_cli_prints_version_and_fixed_failure_without_private_exception_text(self):
        with patch('settings.validate_settings'), \
                patch.object(self.migrations, 'schema_status', return_value={'schema_version': 4}), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.migrations.main(['status']), 0)
        self.assertEqual(json.loads(output.getvalue()), {'schema_version': 4})
        with patch('settings.validate_settings'), \
                patch.object(self.migrations, 'upgrade', side_effect=RuntimeError('private password')), \
                redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(self.migrations.main(['upgrade', '--backup', str(self.backup), '--resume']), 1)
        self.assertNotIn('private password', errors.getvalue())

    def test_cli_explains_missing_production_settings_before_database_access(self):
        with patch.dict('os.environ', {}, clear=True), \
                patch.object(self.migrations, 'schema_status') as status, \
                patch.object(self.migrations, 'upgrade') as upgrade, \
                redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(self.migrations.main(['upgrade', '--backup', str(self.backup)]), 1)
        self.assertIn('Migration configuration error: Missing required settings:', errors.getvalue())
        for name in ('DB_USER', 'DB_PASS', 'DB_NAME', 'ADMIN_SESSION_SECRET',
                     'UI_ALLOWED_ORIGINS', 'ADMIN_ALLOWED_ORIGINS'):
            self.assertIn(name, errors.getvalue())
        self.assertIn('APP_ENV=development in this terminal', errors.getvalue())
        self.assertIn('VS Code launch settings', errors.getvalue())
        status.assert_not_called()
        upgrade.assert_not_called()

    def test_cli_explains_invalid_development_settings_without_echoing_values(self):
        with patch.dict('os.environ', {'APP_ENV': 'development', 'DB_PORT': 'private password'}, clear=True), \
                patch.object(self.migrations, 'schema_status') as status, \
                redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(self.migrations.main(['status']), 1)
        self.assertIn('Migration configuration error: DB_PORT must be an integer', errors.getvalue())
        self.assertNotIn('private password', errors.getvalue())
        self.assertNotIn('set APP_ENV=development', errors.getvalue())
        status.assert_not_called()


class MigrationDDLTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path]))
        self.migrations = importlib.import_module('schema_migrations')

    def test_column_steps_are_idempotent_and_never_rewrite_records(self):
        for migration in self.migrations.MIGRATIONS[1:]:
            for existing in (None, {'Type': 'text'}):
                if migration.widen_text and existing is None:
                    continue
                cursor = MagicMock()
                cursor.fetchone.return_value = existing
                migration.apply(cursor)
                queries = [call.args[0] for call in cursor.execute.call_args_list]
                self.assertEqual(sum(query.startswith('ALTER') for query in queries), int(existing is None))
                self.assertFalse(any(query.startswith(('DROP', 'DELETE', 'UPDATE', 'INSERT')) for query in queries))

    def test_legacy_log_widening_accepts_only_supported_types(self):
        for kind, altered in (('varchar(255)', True), ('char(200)', True), ('text', False), ('longtext', False)):
            cursor = MagicMock()
            cursor.fetchone.return_value = {'Type': kind}
            self.migrations.MIGRATIONS[2].apply(cursor)
            self.assertEqual(cursor.execute.call_count, 2 if altered else 1)
        for existing in (None, {'Type': 'blob'}):
            cursor.fetchone.return_value = existing
            with self.assertRaises(self.migrations.MigrationError):
                self.migrations.MIGRATIONS[2].apply(cursor)


if __name__ == '__main__':
    unittest.main()
