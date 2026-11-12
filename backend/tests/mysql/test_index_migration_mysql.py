"""Run the migration/retrieval scenarios and locking boundaries against disposable MySQL."""

import importlib
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import patch

import pymysql

from mysql_support import require_target
import test_index_migration as migration_scenarios


class MySQLIndexDatabase:
    def __init__(self):
        self.target = require_target()
        migrations = importlib.import_module('schema_migrations')
        migrations.upgrade(connect=self.connect)
        self.clear()

    def connect(self):
        if require_target() != self.target:
            raise AssertionError('Index tests require the provisioned disposable database')
        return pymysql.connect(**self.target.config())

    def clear(self):
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT DATABASE() AS name')
            if cursor.fetchone()['name'] != self.target.name:
                raise AssertionError('Refusing to clear an unexpected index test database')
            for table in ('embedding_index_vectors', 'embedding_index_state', 'embedding_indexes',
                          'pdf_chunks', 'pdf_documents', 'qa_documents'):
                cursor.execute(f'DELETE FROM {table}')

    def rows(self, table='qa_documents'):
        if table not in {'qa_documents', 'pdf_chunks', 'pdf_documents'}:
            raise AssertionError('Unknown fixture table')
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(f'SELECT * FROM {table} ORDER BY id')
            return list(cursor.fetchall())


class IndexMigrationMySQLTests(migration_scenarios.IndexMigrationTests):
    def make_database(self, directory):
        database = MySQLIndexDatabase()
        self.addCleanup(database.clear)
        return database

    def test_retrieval_runs_while_cutover_locks_fence_staged_uploads(self):
        self.request_new()
        for _ in range(4):
            self.service.tick()
        entered, release, upload_entered = threading.Event(), threading.Event(), threading.Event()
        original_check = self.storage.checked_batch

        def paused_check(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise AssertionError('Cutover barrier was not released')
            return original_check(*args, **kwargs)

        def publish_stale_upload():
            with closing(self.db.connect()) as connection:
                connection.begin()
                upload_entered.set()
                try:
                    self.store.publication_guard(connection, self.old.profile)
                finally:
                    connection.rollback()

        with ThreadPoolExecutor(max_workers=2) as executor:
            with patch.object(self.storage, 'checked_batch', side_effect=paused_check):
                cutover = executor.submit(self.service.tick)
                try:
                    self.assertTrue(entered.wait(5))
                    self.assertEqual(len(self.hits()), 6)
                    upload = executor.submit(publish_stale_upload)
                    self.assertTrue(upload_entered.wait(5))
                    self.assertFalse(upload.done())
                finally:
                    release.set()
                cutover.result(timeout=10)
                with self.assertRaises(self.storage.IndexChanged):
                    upload.result(timeout=10)
        self.assertEqual(self.store.state()['active_id'], self.new.profile.identity)
        self.assertEqual(len(self.hits()), 6)

    def test_failed_pointer_update_rolls_back_validation_and_keeps_serving_index(self):
        self.request_new()
        for _ in range(4):
            self.service.tick()
        self.sql("CREATE TRIGGER f035_reject_cutover BEFORE UPDATE ON embedding_index_state "
                 "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='fixture cutover failure'")
        try:
            state = self.store.state()
            with self.assertRaises(pymysql.MySQLError):
                self.store.validate_and_activate(self.new.profile.identity, token=state['request_token'])
            self.assertEqual(self.store.state()['active_id'], self.old.profile.identity)
            self.assertIsNone(self.store.get(self.new.profile.identity)['validated_at'])
            self.assertEqual(len(self.hits()), 6)
        finally:
            self.sql('DROP TRIGGER f035_reject_cutover')
