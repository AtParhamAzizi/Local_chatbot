"""Upgrade representative unversioned databases on disposable MySQL, never deployment DBs."""

import hashlib
import importlib
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymysql

from mysql_support import require_target


BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLES = ('admin_sessions', 'admin-users', 'pdf_chunks', 'pdf_documents',
          'qa_documents', 'chat_history', 'adminlogger', 'schema_migrations')
LEGACY_ROWS = """
INSERT INTO qa_documents (id, question, answer, question_hash, embedding, embedding_dim)
VALUES (13, 'پرسش قدیمی', 'پاسخ محفوظ', REPEAT('a', 64), X'0000803F00000000', 2);
INSERT INTO `admin-users` (id, username, password_hash, is_active, is_admin, permissions)
VALUES (7, 'existing-admin', 'unchanged-password-hash', 0, 1, '["json_upload"]');
INSERT INTO pdf_documents (id, filename, document_hash, file_size, page_count, chunk_count,
embedded_chunk_count, embedding_dim, status)
VALUES (19, 'قدیمی.pdf', REPEAT('b', 64), 1290, 1, 1, 1, 2, 'completed');
INSERT INTO pdf_chunks (id, document_id, chunk_index, page_start, page_end, content, embedding, embedding_dim)
VALUES (23, 19, 0, 1, 1, 'متن سند قدیمی', X'0000803F00000000', 2);
INSERT INTO chat_history (id, user_id, ip_address, user_message, bot_response, feedback, feedback_at)
VALUES (29, 'legacy-client-id', '192.0.2.8', 'پیام قدیمی', 'پاسخ قدیمی', 'like', '2025-01-02 03:04:05');
INSERT INTO adminlogger (id, _level, _from, message, warninglevel)
VALUES (31, 'old.py', 'operation', 'لاگ قبلی', 2);
"""


class SchemaUpgradeMySQLTests(unittest.TestCase):
    def setUp(self):
        self.target = require_target()
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        self.resources.enter_context(patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path]))
        self.migrations = importlib.import_module('schema_migrations')
        self.connect = lambda: pymysql.connect(**self.target.config())
        self.directory = self.resources.enter_context(TemporaryDirectory())
        self.backup = Path(self.directory) / 'legacy.sql'
        self.reset_database()
        self.addCleanup(self.reset_database)

    def reset_database(self):
        if require_target() != self.target:
            raise AssertionError('Refusing migration fixtures outside the disposable database')
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT DATABASE() AS name')
            if cursor.fetchone()['name'] != self.target.name:
                raise AssertionError('Refusing to clear an unexpected schema')
            for table in TABLES:
                cursor.execute(f'DROP TABLE IF EXISTS `{table}`')

    def legacy_database(self):
        sql = self.migrations.MIGRATIONS[0].sql()
        sql += '\nALTER TABLE adminlogger MODIFY COLUMN message VARCHAR(255) NOT NULL;\n'
        sql += LEGACY_ROWS
        self.backup.write_text(sql, encoding='utf-8')
        with self.connect() as connection, connection.cursor() as cursor:
            for statement in sql.split(';'):
                if statement.strip():
                    cursor.execute(statement)

    def snapshot(self, *, sessions=False):
        with self.connect() as connection, connection.cursor() as cursor:
            result = {}
            for table in TABLES:
                if table == 'schema_migrations' or table == 'admin_sessions' and not sessions:
                    continue
                order = 'token_hash' if table == 'admin_sessions' else 'id'
                cursor.execute(f'SELECT * FROM `{table}` ORDER BY `{order}`')
                result[table] = list(cursor.fetchall())
            return result

    def assert_preserved(self, before, after):
        self.assertEqual(before.keys(), after.keys())
        for table, rows in before.items():
            self.assertEqual(len(rows), len(after[table]), table)
            for old, current in zip(rows, after[table], strict=True):
                self.assertEqual(old, {key: current[key] for key in old}, table)

    def test_legacy_upgrade_preserves_all_records_blobs_feedback_credentials_and_reports_version(self):
        self.legacy_database()
        before = self.snapshot()
        self.assertEqual(self.migrations.schema_status(connect=self.connect)['schema_version'], 0)
        report = self.migrations.upgrade(backup=self.backup, connect=self.connect)
        self.assertEqual(report['schema_version'], 4)
        self.assertEqual(self.migrations.require_current_schema(connect=self.connect), report)
        after = self.snapshot()
        self.assert_preserved(before, after)
        self.assertIsNone(after['chat_history'][0]['owner_hash'])
        self.assertEqual(after['admin-users'][0]['session_version'], 1)
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT * FROM schema_migrations ORDER BY version')
            history = cursor.fetchall()
            self.assertEqual([row['version'] for row in history], [1, 2, 3, 4])
            self.assertTrue(all(row['status'] == 'applied' and row['applied_at'] for row in history))
            self.assertTrue(all(row['backup_sha256'] == hashlib.sha256(self.backup.read_bytes()).hexdigest() for row in history))
            cursor.execute("SHOW COLUMNS FROM adminlogger LIKE 'message'")
            self.assertEqual(cursor.fetchone()['Type'], 'text')
            cursor.execute("INSERT INTO adminlogger (_level, _from, message, warninglevel) VALUES ('test', 'test', %s, 1)",
                           ('x' * 3000,))
            cursor.execute('SELECT LENGTH(message) AS size FROM adminlogger ORDER BY id DESC LIMIT 1')
            self.assertEqual(cursor.fetchone()['size'], 3000)
        self.assertEqual(self.migrations.upgrade(connect=self.connect), report)
        self.assertEqual(self.migrations.schema_status(connect=self.connect), report)

    def test_already_current_but_unversioned_schema_is_adopted_without_changing_sessions(self):
        self.legacy_database()
        with self.connect() as connection, connection.cursor() as cursor:
            for migration in self.migrations.MIGRATIONS[1:]:
                migration.apply(cursor)
            cursor.execute("UPDATE `admin-users` SET session_version = 8 WHERE id = 7")
            cursor.execute("UPDATE chat_history SET owner_hash = %s WHERE id = 29", ('c' * 64,))
            cursor.execute('INSERT INTO admin_sessions VALUES (%s, 7, 8, 1999999999, 1777777777)', ('d' * 64,))
        before = self.snapshot(sessions=True)
        self.migrations.upgrade(backup=self.backup, connect=self.connect)
        self.assert_preserved(before, self.snapshot(sessions=True))

    def test_missing_backup_blocks_changes_and_real_named_lock_blocks_competing_upgrade(self):
        self.legacy_database()
        before = self.snapshot()
        with self.assertRaises(self.migrations.MigrationError):
            self.migrations.upgrade(connect=self.connect)
        self.assert_preserved(before, self.snapshot())
        with self.connect() as connection, connection.cursor() as cursor:
            self.assertNotIn('schema_migrations', self.migrations._tables(cursor))
            with self.migrations._migration_lock(cursor):
                with self.assertRaisesRegex(self.migrations.MigrationError, 'schema lock'):
                    self.migrations.upgrade(backup=self.backup, connect=self.connect)

    def test_failed_implicitly_committed_upgrade_resumes_without_losing_records(self):
        self.legacy_database()
        before = self.snapshot()
        original_apply = self.migrations.Migration.apply

        def fail_after_ddl(migration, cursor):
            original_apply(migration, cursor)
            if migration.version == 2:
                raise RuntimeError('Failure after MySQL committed session DDL')

        with patch.object(self.migrations.Migration, 'apply', fail_after_ddl):
            with self.assertRaises(RuntimeError):
                self.migrations.upgrade(backup=self.backup, connect=self.connect)
        status = self.migrations.schema_status(connect=self.connect)
        self.assertEqual(status['schema_version'], 1)
        self.assertEqual(status['incomplete'], [2])
        with self.assertRaises(self.migrations.MigrationError):
            self.migrations.require_current_schema(connect=self.connect)
        with self.assertRaises(self.migrations.MigrationError):
            self.migrations.upgrade(backup=self.backup, connect=self.connect)
        report = self.migrations.upgrade(backup=self.backup, resume=True, connect=self.connect)
        self.assertEqual(report['schema_version'], 4)
        self.assert_preserved(before, self.snapshot())

    def test_fresh_database_migrates_without_a_backup(self):
        self.assertEqual(self.migrations.schema_status(connect=self.connect)['schema_version'], 0)
        report = self.migrations.upgrade(connect=self.connect)
        self.assertEqual(report['schema_version'], 4)
        self.assertTrue(all(not rows for rows in self.snapshot(sessions=True).values()))


if __name__ == '__main__':
    unittest.main()
