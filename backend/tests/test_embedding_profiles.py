"""Equal dimensions cannot establish embedding compatibility or provenance."""

import importlib
import importlib.util
import json
import shutil
import sqlite3
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import numpy as np

from core_support import BACKEND_DIR, OfflineCoreFixture, module_double
from embedding_fixtures import profile
from test_persian_text import KnowledgeDatabase


class EmbeddingProfileTests(OfflineCoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.module = importlib.import_module('embedding_profile')

    def test_profile_roundtrip_is_immutable_canonical_and_covers_every_compatibility_field(self):
        original = profile()
        reordered = json.dumps(dict(reversed(list(json.loads(original.to_json()).items()))), indent=2)
        self.assertEqual(self.module.EmbeddingProfile.from_json(reordered), original)
        self.assertEqual(len(original.identity), 64)
        for change in ({'model_id': 'fixture/other-model'}, {'revision': 'other-weights'},
                       {'tokenizer': {'implementation': 'fixture-v2', 'max_length': 512}},
                       {'tokenizer': {'implementation': 'fixture-v1', 'max_length': 256}},
                       {'pooling': {'method': 'mean', 'l2_normalize': True}},
                       {'normalization_version': 'fa-v2'}, {'dimension': 4},
                       {'runtime': {'implementation': 'fixture-v2'}}):
            changed = profile(**change)
            with self.subTest(change=change):
                self.assertNotEqual(original.identity, changed.identity)
                self.assertFalse(self.module.compatible_profile(changed.to_json(), original))
        with self.assertRaises(AttributeError):
            original.revision = 'changed'
        mutable = {'max_length': 512}
        copied = profile(tokenizer=mutable)
        mutable['max_length'] = 256
        self.assertEqual(json.loads(copied.tokenizer_config), {'max_length': 512})

    def test_unknown_malformed_or_incomplete_metadata_never_matches(self):
        original = profile()
        metadata = json.loads(original.to_json())
        for raw in (None, '', '{}', '[]', 'null', '{broken', json.dumps({**metadata, 'version': 2}),
                    json.dumps({**metadata, 'dimension': True}), json.dumps({**metadata, 'storage_dtype': 'float64'}),
                    json.dumps({**metadata, 'tokenizer': {}}), json.dumps({**metadata, 'revision': ''}),
                    json.dumps({**metadata, 'extra': 'unknown'})):
            with self.subTest(raw=raw):
                self.assertFalse(self.module.compatible_profile(raw, original))

    def test_revision_uses_actual_local_artifacts_and_is_independent_of_installation_path(self):
        directory = Path(self.resources.enter_context(TemporaryDirectory()))
        source, copy = directory / 'a', directory / 'b'
        source.mkdir()
        (source / 'config.json').write_text('{"hidden_size":3}', encoding='utf-8')
        (source / 'model.safetensors').write_bytes(b'weights-a')
        (source / 'tokenizer.json').write_text('{"vocab":"first"}', encoding='utf-8')
        first, manifest = self.module.local_artifact_revision(source)
        shutil.copytree(source, copy)
        self.assertEqual(self.module.local_artifact_revision(copy)[0], first)
        self.module.require_unchanged_artifacts(manifest)
        for name, content in (('model.safetensors', b'weights-b'), ('tokenizer.json', b'{"vocab":"other"}'),
                              ('config.json', b'{"hidden_size":4}')):
            path = source / name
            before = path.read_bytes()
            path.write_bytes(content)
            with self.subTest(artifact=name):
                self.assertNotEqual(self.module.local_artifact_revision(source)[0], first)
                with self.assertRaises(ValueError):
                    self.module.require_unchanged_artifacts(manifest)
            path.write_bytes(before)
        with self.assertRaises(ValueError):
            self.module.local_artifact_revision(directory / 'missing')
        _, manifest = self.module.local_artifact_revision(source)
        (source / 'added_tokens.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.module.require_unchanged_artifacts(manifest)

    def test_unprofiled_batches_wrong_dimensions_and_nonfinite_vectors_fail_before_search(self):
        retrieval = importlib.import_module('services.retrieval')
        Batch = self.module.EmbeddingBatch
        for result in (np.ones((1, 3)), Batch(np.ones((1, 2)), profile(3)),
                       Batch(np.full((1, 3), np.nan), profile(3)), Batch(np.zeros((1, 3)), profile(3)),
                       Batch(np.ones((1, 3)), profile(3, normalization_version='fa-v2'))):
            qa, pdf = Mock(), Mock()
            service = retrieval.RetrievalService(embed=Mock(return_value=result), search_qa=qa, search_pdf=pdf)
            with self.subTest(result=type(result).__name__), self.assertRaises(ValueError):
                service.retrieve('query')
            qa.assert_not_called()
            pdf.assert_not_called()
        with self.assertRaises(ValueError):
            self.scoring._search_qa(np.ones(3))
        with self.assertRaises(ValueError):
            self.scoring._search_pdf(np.ones(3), profile=profile(2))

    def test_inflight_embedding_keeps_its_own_profile_while_selection_waits(self):
        registry = module_double('model_registry', get_active_model_id=lambda: 'fixture',
                                 get_model_definition=lambda identifier: {'embedding_path': 'unused'})
        torch = module_double('torch', no_grad=lambda: lambda function: function)
        transformers = module_double('transformers', AutoTokenizer=Mock(), AutoModel=Mock())
        with patch.dict(sys.modules, {'torch': torch, 'transformers': transformers, 'model_registry': registry}):
            spec = importlib.util.spec_from_file_location('profile_embedding_fixture', BACKEND_DIR / 'interface.py')
            embedder = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(embedder)
        original = profile()
        embedder._profile = original
        embedder.get_embedder = lambda: (None, None)
        entered, release, selected = threading.Event(), threading.Event(), threading.Event()

        def embed(*arguments):
            entered.set()
            if not release.wait(5):
                raise AssertionError('Test did not release the in-flight embedding')
            return np.ones((1, 3), dtype=np.float32)

        def select():
            selected.set()
            embedder.set_embedding_model('another-generator')

        embedder._embed = embed
        with ThreadPoolExecutor(max_workers=2) as executor:
            pending = executor.submit(embedder.embed_batch, ['query'])
            try:
                self.assertTrue(entered.wait(5))
                selection = executor.submit(select)
                self.assertTrue(selected.wait(5))
                self.assertFalse(selection.done())
            finally:
                release.set()
            batch = pending.result(timeout=5)
            selection.result(timeout=5)
        self.assertEqual(batch.profile, original)
        self.assertIsNone(embedder._profile)

    def test_loader_records_actual_artifact_revision_configuration_and_not_generator_selection(self):
        directory = Path(self.resources.enter_context(TemporaryDirectory()))
        (directory / 'config.json').write_text('{"hidden_size":3}', encoding='utf-8')
        weights = directory / 'model.safetensors'
        weights.write_bytes(b'weights-a')
        (directory / 'tokenizer.json').write_text('{"vocab":"first"}', encoding='utf-8')
        registry = module_double('model_registry', get_active_model_id=lambda: 'generator-a',
                                 get_model_definition=lambda identifier: {
                                     'embedding_path': str(directory), 'embedding_model_id': 'real/embedding-model'})
        torch = module_double('torch', no_grad=lambda: lambda function: function, float32='fixture-float32')
        tokenizer = SimpleNamespace(padding_side='left')
        model = Mock(config=SimpleNamespace(hidden_size=3))
        model.to.return_value.eval.return_value = model
        transformers = module_double('transformers', AutoTokenizer=Mock(), AutoModel=Mock())
        transformers.AutoTokenizer.from_pretrained.return_value = tokenizer
        transformers.AutoModel.from_pretrained.return_value = model
        with patch.dict(sys.modules, {'torch': torch, 'transformers': transformers, 'model_registry': registry}):
            spec = importlib.util.spec_from_file_location('loaded_profile_fixture', BACKEND_DIR / 'interface.py')
            embedder = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(embedder)
        embedder.version = lambda name: 'fixture-1'
        embedder.get_embedder()
        first = embedder._profile
        self.assertEqual(first.model_id, 'real/embedding-model')
        self.assertEqual(first.dimension, 3)
        self.assertEqual(json.loads(first.tokenizer_config)['max_length'], embedder.MAX_SEQ_LEN)
        self.assertEqual(json.loads(first.pooling_config), {'method': 'last-non-padding-token-v1', 'l2_normalize': True})
        embedder.set_embedding_model('generator-b')
        embedder.get_active_model_id = lambda: 'generator-b'
        embedder.get_embedder()
        self.assertEqual(embedder._profile, first)
        weights.write_bytes(b'weights-b')
        embedder.set_embedding_model('generator-b')
        embedder.get_embedder()
        self.assertNotEqual(embedder._profile.revision, first.revision)
        self.assertEqual(embedder._profile.dimension, first.dimension)

    def test_profile_migrations_are_additive_nullable_and_resumable(self):
        migrations = importlib.import_module('schema_migrations')
        for migration, table in zip(migrations.MIGRATIONS[6:9], ('qa_documents', 'pdf_documents', 'pdf_chunks'), strict=True):
            cursor = Mock()
            cursor.fetchone.return_value = None
            migration.apply(cursor)
            self.assertEqual(cursor.execute.call_args.args[0],
                             f'ALTER TABLE {table} ADD COLUMN embedding_profile JSON NULL')
            cursor.execute.reset_mock()
            cursor.fetchone.return_value = {'Field': 'embedding_profile', 'Type': 'json'}
            migration.apply(cursor)
            self.assertEqual(cursor.execute.call_count, 1)
            self.assertTrue(cursor.execute.call_args.args[0].startswith('SHOW COLUMNS'))


class EmbeddingCompatibilityTests(OfflineCoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.module = importlib.import_module('embedding_profile')
        self.ingestion = importlib.import_module('services.ingestion')
        directory = Path(self.resources.enter_context(TemporaryDirectory()))
        self.knowledge = KnowledgeDatabase(directory / 'knowledge.sqlite')
        self.selected = profile()
        self.embed = Mock(side_effect=lambda texts: self.module.EmbeddingBatch(
            np.tile(np.array([1, 0, 0], dtype=np.float32), (len(texts), 1)), self.selected))
        self.service = self.ingestion.IngestionService(
            connect=self.knowledge.connect, embed=self.embed, hash_question=self.database.hash_question,
            hash_document=self.database.sha256_bytes, open_pdf=self.pdf, audit=Mock(), batch_size=1)
        self.retrieval = importlib.import_module('services.retrieval').RetrievalService(
            embed=self.embed, search_qa=self.scoring._search_qa, search_pdf=self.scoring._search_pdf)
        self.resources.enter_context(patch.object(self.scoring, 'get_conn', self.knowledge.connect))

    def pdf(self, **options):
        document = Mock(page_count=2)
        document.load_page.side_effect = [Mock(get_text=Mock(return_value='original source page one')),
                                         Mock(get_text=Mock(return_value='original source page two'))]
        return document

    def raw(self, question='original question', answer='original answer'):
        return json.dumps([{'question': question, 'answer': answer}]).encode()

    def mutate(self, table, column, value):
        assert table in {'qa_documents', 'pdf_documents', 'pdf_chunks'}
        assert column in {'embedding_profile', 'embedding_dim', 'embedding'}
        with closing(sqlite3.connect(self.knowledge.filename)) as connection:
            connection.execute(f'UPDATE {table} SET {column}=?', (value,))
            connection.commit()

    def test_equal_dimension_profiles_are_isolated_in_qa_pdf_and_combined_retrieval(self):
        first = self.selected
        self.service.ingest_qa(self.raw('question A'))
        a_pdf = self.service.ingest_pdf(b'pdf-a', 'A.pdf')['document_id']
        self.selected = profile(model_id='another/model')
        self.assertEqual(self.retrieval.retrieve('original source'), [])
        self.service.ingest_qa(self.raw('question B'))
        b_pdf = self.service.ingest_pdf(b'pdf-b', 'B.pdf')['document_id']
        for expected, question, document_id in ((self.selected, 'question B', b_pdf), (first, 'question A', a_pdf)):
            self.selected = expected
            hits = self.retrieval.retrieve('original source')
            self.assertEqual({hit['question'] for hit in hits if hit['source_type'] == 'qa'}, {question})
            self.assertEqual({hit['document_id'] for hit in hits if hit['source_type'] == 'pdf'}, {document_id})
            self.assertEqual(len(hits), 3)
        for table in ('qa_documents', 'pdf_documents', 'pdf_chunks'):
            for row in self.knowledge.rows(table):
                metadata = self.module.EmbeddingProfile.from_json(row['embedding_profile'])
                self.assertEqual(metadata.dimension, row['embedding_dim'])
                self.assertIn(metadata.model_id, {'fixture/embedder', 'another/model'})

    def test_every_profile_difference_and_unknown_metadata_is_rejected_before_vector_or_lexical_scoring(self):
        self.service.ingest_qa(self.raw())
        self.service.ingest_pdf(b'pdf', 'original.pdf')
        original = self.selected.to_json()
        variants = [profile(model_id='other').to_json(), profile(revision='other').to_json(),
                    profile(tokenizer={'max_length': 42}).to_json(), profile(pooling={'method': 'mean'}).to_json(),
                    profile(normalization_version='fa-v0').to_json(), profile(runtime={'implementation': 'v2'}).to_json(),
                    None, '{}', '{broken']
        for table in ('qa_documents', 'pdf_chunks', 'pdf_documents'):
            for metadata in variants:
                with self.subTest(table=table, metadata=metadata):
                    self.mutate(table, 'embedding_profile', metadata)
                    search = self.scoring._search_qa if table == 'qa_documents' else self.scoring._search_pdf
                    with patch.object(self.scoring, '_cosine_score', side_effect=AssertionError('Must not score')):
                        self.assertEqual(search(np.array([1, 0, 0], dtype=np.float32), profile=self.selected), [])
                self.mutate(table, 'embedding_profile', original)

    def test_metadata_dimension_blob_and_document_chunk_disagreement_are_rejected(self):
        self.service.ingest_qa(self.raw())
        self.service.ingest_pdf(b'pdf', 'original.pdf')
        for table in ('qa_documents', 'pdf_documents', 'pdf_chunks'):
            self.mutate(table, 'embedding_dim', 2)
            search = self.scoring._search_qa if table == 'qa_documents' else self.scoring._search_pdf
            self.assertEqual(search(np.array([1, 0, 0], dtype=np.float32), profile=self.selected), [])
            self.mutate(table, 'embedding_dim', 3)
        for table in ('qa_documents', 'pdf_chunks'):
            for blob in (b'bad', np.ones(2, dtype=np.float32).tobytes(), np.full(3, np.nan, dtype=np.float32).tobytes()):
                self.mutate(table, 'embedding', blob)
                search = self.scoring._search_qa if table == 'qa_documents' else self.scoring._search_pdf
                self.assertEqual(search(np.array([1, 0, 0], dtype=np.float32), profile=self.selected), [])

    def test_legacy_vectors_are_preserved_until_explicit_replacement_adds_metadata(self):
        self.service.ingest_qa(self.raw())
        self.service.ingest_pdf(b'pdf', 'original.pdf')
        for table in ('qa_documents', 'pdf_documents', 'pdf_chunks'):
            self.mutate(table, 'embedding_profile', None)
        before = self.knowledge.rows()
        self.assertEqual(self.retrieval.retrieve('original source'), [])
        skipped = self.service.ingest_qa(self.raw())
        self.assertEqual(skipped['skipped'], 1)
        self.assertEqual(self.knowledge.rows(), before)
        self.service.ingest_qa(self.raw(), replace=True)
        self.service.ingest_pdf(b'pdf', 'original.pdf', replace=True)
        after = self.knowledge.rows()[0]
        self.assertEqual({key: after[key] for key in before[0]}, {**before[0], 'embedding_profile': self.selected.to_json()})
        self.assertEqual(len(self.retrieval.retrieve('original source')), 3)

    def test_same_dimension_profile_switch_between_batches_prevents_all_publication(self):
        for kind in ('json', 'pdf'):
            first, second = profile(), profile(revision='other')
            self.embed.side_effect = [self.module.EmbeddingBatch(np.ones((1, 3)), first),
                                     self.module.EmbeddingBatch(np.ones((1, 3)), second)]
            with self.subTest(kind=kind), self.assertRaises(self.ingestion.IngestionError):
                if kind == 'json':
                    raw = json.dumps([{'question': 'one', 'answer': 'answer'}, {'question': 'two', 'answer': 'answer'}]).encode()
                    self.service.ingest_qa(raw)
                else:
                    self.service.ingest_pdf(b'pdf', 'original.pdf')
            for table in ('qa_documents', 'pdf_documents', 'pdf_chunks'):
                self.assertEqual(self.knowledge.rows(table), [])


if __name__ == '__main__':
    unittest.main()
