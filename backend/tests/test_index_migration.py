"""Persisted migrations, live retrieval and rollback through the production services."""

import hashlib
import importlib
import json
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from core_support import OfflineCoreFixture, module_double
from embedding_fixtures import profile
from ocr_fixture import PdfConnection, PdfCursor
from test_persian_text import KnowledgeDatabase


class IndexCursor(PdfCursor):
    def execute(self, statement, parameters=()):
        statement = statement.replace('INSERT IGNORE', 'INSERT OR IGNORE').replace(' FOR UPDATE', '')
        statement = statement.replace('UTC_TIMESTAMP(6)', 'CURRENT_TIMESTAMP')
        if statement.startswith('SET TRANSACTION'):
            return
        statement = statement.replace('ON DUPLICATE KEY UPDATE',
                                      'ON CONFLICT(index_id, source_kind, source_id) DO UPDATE SET')
        statement = statement.replace('VALUES(source_hash)', 'excluded.source_hash')
        statement = statement.replace('VALUES(embedding)', 'excluded.embedding')
        return super().execute(statement, parameters)


class IndexConnection(PdfConnection):
    def __init__(self, filename):
        super().__init__(filename)
        self.raw.create_function('SHA2', 2, lambda value, bits: hashlib.sha256(value.encode()).hexdigest())

    def cursor(self):
        return IndexCursor(self.raw.cursor())


class IndexDatabase(KnowledgeDatabase):
    def __init__(self, filename):
        super().__init__(filename)
        with closing(sqlite3.connect(self.filename)) as connection:
            connection.executescript('''
                CREATE TABLE embedding_indexes (
                    index_id TEXT PRIMARY KEY, profile TEXT, model_id TEXT, model_path TEXT,
                    status TEXT DEFAULT 'building', embedded_count INTEGER DEFAULT 0,
                    error_code TEXT, validated_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE embedding_index_vectors (
                    index_id TEXT, source_kind TEXT, source_id INTEGER, source_hash TEXT, embedding BLOB,
                    PRIMARY KEY(index_id, source_kind, source_id));
                CREATE TABLE embedding_index_state (
                    singleton INTEGER PRIMARY KEY, active_id TEXT, previous_id TEXT, requested_id TEXT,
                    requested_model TEXT, requested_path TEXT, request_token TEXT,
                    configured_model TEXT, configured_path TEXT, error_code TEXT);
            ''')

    def connect(self):
        return IndexConnection(self.filename)


class IndexMigrationTests(OfflineCoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.migration = importlib.import_module('services.index_migration')
        self.storage = importlib.import_module('services.index_store')
        self.batch = importlib.import_module('embedding_profile').EmbeddingBatch
        self.retrieval = importlib.import_module('services.retrieval')
        directory = self.resources.enter_context(TemporaryDirectory())
        self.db = self.make_database(directory)
        self.store = self.storage.MySQLIndexStore(connect=self.db.connect)
        self.old = self.make_runtime('old', [1, 0, 0])
        self.new = self.make_runtime('new', [0, 1, 0])
        self.load = Mock(side_effect=lambda model_id, path: {'old': self.old, 'new': self.new}[model_id])
        self.service = self.make_service()
        self.service.initialize(self.old)
        self.resources.enter_context(patch.object(self.scoring, 'get_conn', self.db.connect))
        self.add_qa(1, 'first question')
        self.add_qa(2, 'second question')
        self.add_qa(3, 'third question')
        self.sql("INSERT INTO pdf_documents (id, filename, document_hash, file_size, status, embedding_dim, embedding_profile) "
                 "VALUES (1, 'source.pdf', %s, 6, 'completed', 3, %s)",
                 (hashlib.sha256(b'source').hexdigest(), self.old.profile.to_json()))
        for index in range(1, 4):
            self.sql('INSERT INTO pdf_chunks (id, document_id, chunk_index, page_start, page_end, content, '
                     'embedding, embedding_dim, embedding_profile) VALUES (%s,1,%s,1,1,%s,%s,3,%s)',
                     (index, index, f'source content {index}', np.array([1, 0, 0], np.float32).tobytes(),
                      self.old.profile.to_json()))

    def sql(self, statement, parameters=()):
        with self.store.transaction() as cursor:
            cursor.execute(statement, parameters)
            return list(cursor.fetchall())

    def make_database(self, directory):
        return IndexDatabase(Path(directory) / 'indexes.sqlite')

    def add_qa(self, index, text, runtime=None):
        runtime = self.old if runtime is None else runtime
        self.sql('INSERT INTO qa_documents (id, question, answer, question_hash, embedding, embedding_dim, '
                 'embedding_profile) VALUES (%s,%s,%s,%s,%s,3,%s)',
                 (index, text, f'answer {index}', hashlib.sha256(text.encode()).hexdigest(),
                  runtime.vector.tobytes(), runtime.profile.to_json()))

    def make_runtime(self, name, vector):
        runtime = SimpleNamespace(model_id=name, path=f'/immutable/{name}', profile=profile(revision=name),
                                  vector=np.array(vector, dtype=np.float32))
        runtime.embed = Mock(side_effect=lambda texts: self.batch(np.tile(runtime.vector, (len(texts), 1)), runtime.profile))
        return runtime

    def make_service(self):
        return self.migration.IndexMigrationService(store=self.store, load=self.load, batch_size=2)

    def hits(self, service=None):
        service = self.service if service is None else service
        return self.retrieval.RetrievalService(
            embed=service.embed,
            search_qa=lambda vector, **options: self.scoring._search_qa(vector, index_id=options['profile'].identity, **options),
            search_pdf=lambda vector, **options: self.scoring._search_pdf(vector, index_id=options['profile'].identity, **options),
        ).retrieve('source content first question', limit=20)

    def finish(self, service=None):
        service = self.service if service is None else service
        for _ in range(50):
            service.tick()
            state = self.store.state()
            if state['request_token'] is None and self.store.get(state['active_id'])['validated_at'] is not None:
                return
        self.fail('Migration did not finish within bounded batches')

    def request_new(self):
        self.finish()
        self.service.request('new', self.new.path)

    def test_queries_remain_available_through_equal_dimension_migration_and_rollback(self):
        self.request_new()
        captured = self.service.embed(['query'])
        for _ in range(8):
            self.assertEqual(len(self.hits()), 6)
            self.service.tick()
        self.assertEqual(self.store.state()['active_id'], self.new.profile.identity)
        self.assertEqual(self.store.state()['previous_id'], self.old.profile.identity)
        self.assertEqual(len(self.hits()), 6)
        # A request embedded before cutover can still search its captured generation.
        self.assertEqual(len(self.scoring._search_qa(captured.vectors[0], profile=captured.profile,
                                                   index_id=captured.profile.identity)), 3)
        self.store.rollback()
        self.finish()
        self.assertEqual(self.store.state()['active_id'], self.old.profile.identity)
        self.assertEqual(len(self.hits()), 6)

    def test_controlled_import_provenance_survives_index_cutover_and_rollback(self):
        from test_controlled_imports import samples

        ingestion = importlib.import_module('services.ingestion')
        processor = ingestion.IngestionService(connect=self.db.connect, embed=self.old.embed,
                                                hash_question=self.database.hash_question, audit=Mock())
        for filename, raw in samples().items():
            processor.ingest_import(raw, filename)
        before = {table: self.db.rows(table) for table in ('qa_documents', 'pdf_documents', 'pdf_chunks')}

        def assert_sources():
            vectors = self.service.embed(['query'])
            options = {'profile': vectors.profile, 'index_id': vectors.profile.identity, 'k': 20}
            hits = self.scoring._search_qa(vectors.vectors[0], **options)
            hits += self.scoring._search_pdf(vectors.vectors[0], **options)
            imported = [hit for hit in hits if hit.get('source_metadata')]
            self.assertEqual({hit['source_metadata']['format'] for hit in imported}, {'text', 'markdown', 'csv', 'docx'})

        self.request_new()
        assert_sources()
        self.finish()
        assert_sources()
        self.store.rollback()
        self.finish()
        assert_sources()
        for table, rows in before.items():
            self.assertEqual(self.db.rows(table), rows)

    def test_production_adapters_reject_an_upload_staged_before_cutover(self):
        self.request_new()
        ingestion = importlib.import_module('services.ingestion')

        def switch_after_embedding(progress):
            if progress['completed'] == progress['total'] and progress['completed']:
                self.finish()

        processor = ingestion.IngestionService(
            connect=self.db.connect, hash_question=self.database.hash_question,
            hash_document=self.database.sha256_bytes, audit=Mock(),
            progress=switch_after_embedding, batch_size=2,
        )
        raw = json.dumps([{'question': 'upload straddles switch', 'answer': 'answer'}]).encode()
        before = self.db.rows()
        with patch.object(self.migration, '_service', self.service):
            with self.assertRaises(self.storage.IndexChanged):
                processor.ingest_qa(raw)
            self.assertEqual(self.db.rows(), before)
            processor.progress = None
            processor.ingest_qa(raw)
            hits = self.retrieval.RetrievalService().retrieve('source content first question', limit=20)
        self.assertEqual(len(hits), 7)

    def test_lost_worker_lease_cannot_checkpoint_a_finished_inference_batch(self):
        self.request_new()
        self.service.guard = Mock(side_effect=RuntimeError('lease lost'))
        with self.assertRaises(RuntimeError):
            self.service.tick()
        self.assertEqual(self.sql('SELECT * FROM embedding_index_vectors WHERE index_id=%s',
                                  (self.new.profile.identity,)), [])
        self.assertEqual(self.store.state()['active_id'], self.old.profile.identity)

    def test_restart_resumes_committed_batches_after_failure_without_reembedding_prefix(self):
        self.request_new()
        self.service.tick()  # two Q&A vectors committed
        self.new.embed.side_effect = RuntimeError('candidate stopped')
        with self.assertRaises(RuntimeError):
            self.service.tick()
        self.assertEqual(self.store.get(self.new.profile.identity)['status'], 'failed')
        self.assertEqual(len(self.hits()), 6)
        resumed = self.make_service()
        resumed.initialize(self.old)
        self.store.retry()
        seen = []

        def embed(texts):
            seen.extend(texts)
            return self.batch(np.tile(self.new.vector, (len(texts), 1)), self.new.profile)

        self.new.embed.side_effect = embed
        self.finish(resumed)
        self.assertNotIn('first question', seen)
        self.assertNotIn('second question', seen)
        self.assertEqual(len(seen), 4)
        self.assertEqual(len(self.hits(resumed)), 6)

    def test_process_death_leaves_building_checkpoint_and_pending_request_resumable(self):
        self.request_new()
        self.service.tick()
        resumed = self.make_service()
        resumed.initialize(self.old)
        self.new.embed.reset_mock()
        self.finish(resumed)
        self.assertEqual(sum(len(call.args[0]) for call in self.new.embed.call_args_list), 4)
        self.assertEqual(self.store.state()['active_id'], self.new.profile.identity)

    def test_loading_resumed_candidate_does_not_block_serving_runtime_or_retrieval(self):
        self.request_new()
        self.service.tick()
        restored = self.make_service()
        restored.initialize(self.old)
        entered, release = threading.Event(), threading.Event()

        def load(model_id, path):
            entered.set()
            if not release.wait(10):
                raise AssertionError('Candidate load barrier was not released')
            return self.new

        restored.load = load
        with ThreadPoolExecutor(max_workers=2) as executor:
            candidate = executor.submit(restored.tick)
            try:
                self.assertTrue(entered.wait(5))
                query = executor.submit(self.hits, restored)
                self.assertEqual(len(query.result(timeout=5)), 6)
            finally:
                release.set()
            candidate.result(timeout=10)

    def test_source_edits_additions_deletions_and_completed_pdf_filter_are_reconciled(self):
        self.request_new()
        for _ in range(4):
            self.service.tick()  # all six source vectors, not yet published
        self.sql("UPDATE qa_documents SET question='changed question' WHERE id=1")
        self.sql('DELETE FROM qa_documents WHERE id=2')
        self.add_qa(4, 'late source')
        self.sql("UPDATE pdf_documents SET status='processing' WHERE id=1")
        state = self.store.state()
        with self.assertRaises(self.storage.IndexChanged):
            self.store.validate_and_activate(self.new.profile.identity, token=state['request_token'])
        self.assertEqual(self.store.state()['active_id'], self.old.profile.identity)
        self.finish()
        self.assertEqual(self.store.get(self.new.profile.identity)['embedded_count'], 3)
        self.assertEqual(len(self.hits()), 3)

    def test_corrupt_candidate_and_wrong_profile_never_replace_serving_generation(self):
        self.request_new()
        self.new.embed.side_effect = lambda texts: self.batch(np.ones((len(texts), 3)), self.old.profile)
        with self.assertRaises(ValueError):
            self.service.tick()
        self.assertEqual(self.store.state()['active_id'], self.old.profile.identity)
        self.assertEqual(len(self.hits()), 6)
        self.store.retry()
        self.new.embed.side_effect = lambda texts: self.batch(np.tile(self.new.vector, (len(texts), 1)), self.new.profile)
        for _ in range(4):
            self.service.tick()
        self.sql("UPDATE embedding_index_vectors SET embedding=%s WHERE index_id=%s AND source_kind='qa' AND source_id=1",
                 (np.full(3, np.nan, np.float32).tobytes(), self.new.profile.identity))
        with self.assertRaises(ValueError):
            self.service.tick()
        self.assertEqual(self.store.state()['active_id'], self.old.profile.identity)
        self.assertEqual(len(self.hits()), 6)

        self.store.retry()
        self.new.embed.reset_mock()
        self.finish()
        self.assertEqual(sum(len(call.args[0]) for call in self.new.embed.call_args_list), 1)

    def test_superseded_request_cannot_publish_and_restart_rejects_changed_retained_artifacts(self):
        self.request_new()
        self.service.tick()
        stale = self.store.state()
        self.service.request('old', self.old.path)
        with self.assertRaises(self.storage.IndexChanged):
            self.store.validate_and_activate(self.new.profile.identity, token=stale['request_token'])
        self.finish()
        self.load.side_effect = lambda *args: self.new
        restarted = self.make_service()
        with self.assertRaises(ValueError):
            restarted.initialize(self.new)

    def test_stale_ingestion_is_fenced_and_new_ingestion_is_immediately_searchable(self):
        self.request_new()
        self.finish()
        with closing(self.db.connect()) as connection:
            connection.begin()
            with self.assertRaises(self.storage.IndexChanged):
                self.store.publication_guard(connection, self.old.profile)
            connection.rollback()
            connection.begin()
            self.store.publication_guard(connection, self.new.profile)
            connection.commit()
        self.add_qa(4, 'post switch question', self.new)
        self.assertEqual(len(self.hits()), 7)
        self.store.rollback()
        self.finish()
        self.assertEqual(len(self.hits()), 7)
        self.assertIn(['post switch question'], [call.args[0] for call in self.old.embed.call_args_list])

    def test_legacy_vectors_are_rebuilt_without_rewriting_original_knowledge(self):
        self.sql('UPDATE qa_documents SET embedding_profile=NULL WHERE id=1')
        before = self.db.rows()
        self.finish()
        self.assertEqual(self.db.rows(), before)
        self.assertEqual(len(self.hits()), 6)

    def test_queries_cannot_request_an_index_for_another_profile(self):
        with self.assertRaises(ValueError):
            self.scoring._search_qa(self.old.vector, profile=self.old.profile, index_id=self.new.profile.identity)

    def test_different_dimension_migration_never_uses_the_previous_vector_shape(self):
        self.new.profile = profile(revision='new-dimension', dimension=2)
        self.new.vector = np.array([0, 1], np.float32)
        self.request_new()
        self.finish()
        self.assertEqual(self.service.embed(['query']).vectors.shape, (1, 2))
        self.assertEqual(len(self.hits()), 6)

    def test_startup_restores_serving_runtime_and_defers_changed_configuration_to_worker(self):
        self.finish()
        self.load.reset_mock()
        restored = self.make_service()
        registry = module_double('model_registry', get_active_model_id=lambda: 'new',
                                 get_model_definition=lambda *args: {'embedding_path': self.new.path})
        with patch.dict('sys.modules', {'model_registry': registry}), \
                patch.object(self.migration, '_service', None), \
                patch.object(self.migration, 'IndexMigrationService', return_value=restored), \
                patch.object(self.migration.atexit, 'register'):
            self.assertIs(self.migration.prepare_index_migration(), restored)
        self.load.assert_called_once_with('old', self.old.path)
        self.assertEqual(self.store.state()['active_id'], self.old.profile.identity)
        self.assertEqual(self.store.state()['requested_model'], 'new')
        self.assertEqual(len(self.hits(restored)), 6)
        self.finish(restored)
        self.store.rollback()
        self.finish(restored)
        restarted = self.make_service()
        with patch.dict('sys.modules', {'model_registry': registry}), \
                patch.object(self.migration, '_service', None), \
                patch.object(self.migration, 'IndexMigrationService', return_value=restarted), \
                patch.object(self.migration.atexit, 'register'):
            self.migration.prepare_index_migration()
        self.assertIsNone(self.store.state()['request_token'])
        self.assertEqual(self.store.state()['active_id'], self.old.profile.identity)


if __name__ == '__main__':
    unittest.main()
