"""Exact index parity, live snapshot safety, bounded resources and acceptance evidence."""

import hashlib
import importlib
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import numpy as np

from core_support import BACKEND_DIR, OfflineCoreFixture
from embedding_fixtures import profile


class VectorIndexTests(OfflineCoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.module = importlib.import_module('services.vector_index')
        self.cache = self.module.VectorIndexCache()
        self.records = [(1, np.array([1, 0, 0], dtype=np.float32).tobytes()),
                        (2, np.array([1, 2, 0], dtype=np.float32).tobytes())]
        self.query = np.array([1, 0, 0], dtype=np.float32)
        self.options = {'dimension': 3, 'namespace': ('qa', profile().identity), 'mode': 'flat'}

    def test_scalar_and_flat_match_with_invalid_and_unnormalized_vectors(self):
        invalid = [None, b'', b'bad', np.ones(2, dtype=np.float32).tobytes(),
                   np.zeros(3, dtype=np.float32).tobytes(), np.full(3, np.nan, dtype=np.float32).tobytes(),
                   np.full(3, np.inf, dtype=np.float32).tobytes()]
        records = self.records + list(enumerate(invalid, start=3))
        flat = self.cache.get(records, **self.options)
        scalar = self.module.ScalarIndex(records, 3)
        self.assertEqual(set(flat.scores(self.query)), {1, 2})
        for identifier, expected in scalar.scores(self.query).items():
            self.assertAlmostEqual(flat.scores(self.query)[identifier], expected, places=6)
        self.assertAlmostEqual(flat.scores(self.query)[2], 1 / np.sqrt(5))
        self.assertFalse(flat.matrix.flags.writeable)
        self.assertFalse(flat.norms.flags.writeable)
        for query in (np.zeros(3, dtype=np.float32), np.full(3, np.nan, dtype=np.float32)):
            self.assertEqual(flat.scores(query), {})
        with self.assertRaises(ValueError):
            flat.scores(np.ones(2))

    def test_snapshot_reuse_checks_actual_bytes_and_is_independent_of_sql_order(self):
        first = self.cache.get(self.records, **self.options)
        fresh = [(identifier, memoryview(blob).tobytes()) for identifier, blob in reversed(self.records)]
        self.assertIs(self.cache.get(fresh, **self.options), first)
        changed = [(1, self.records[1][1]), (2, self.records[0][1])]
        second = self.cache.get(changed, **self.options)
        self.assertIsNot(first, second)
        self.assertEqual(first.scores(self.query)[1], 1)
        self.assertLess(second.scores(self.query)[1], 1)
        third = self.cache.get(changed[:1], **self.options)
        self.assertEqual(set(third.scores(self.query)), {1})
        self.assertEqual(set(first.scores(self.query)), {1, 2})
        self.cache.clear()
        self.assertIsNot(self.cache.get(self.records, **self.options), third)

    def test_mutable_adapter_buffers_cannot_mutate_a_published_snapshot(self):
        buffer = bytearray(self.records[0][1])
        first = self.cache.get([(1, buffer)], **self.options)
        buffer[:] = self.records[1][1]
        second = self.cache.get([(1, buffer)], **self.options)
        self.assertIsNot(first, second)
        self.assertEqual(first.scores(self.query)[1], 1)
        self.assertLess(second.scores(self.query)[1], 1)

    def test_namespaces_profiles_and_dimensions_never_share_an_index(self):
        first = self.cache.get(self.records, **self.options)
        for namespace in (('pdf', profile().identity), ('qa', profile(revision='changed').identity)):
            self.assertIsNot(self.cache.get(self.records, **{**self.options, 'namespace': namespace}), first)
        self.assertEqual(len(self.cache.entries), 2)
        other_dimension = self.cache.get([(1, np.ones(2, dtype=np.float32).tobytes())],
                                         **{**self.options, 'dimension': 2})
        self.assertEqual(other_dimension.dimension, 2)

    def test_crossover_limits_eviction_and_allocation_failure_keep_exact_fallback(self):
        self.assertEqual(self.cache.get(self.records, **{**self.options, 'mode': 'auto'}).backend, 'scalar')
        many = [(identifier, self.records[0][1]) for identifier in range(self.module.AUTO_INDEX_ROWS)]
        self.assertEqual(self.cache.get(many, **{**self.options, 'mode': 'auto'}).backend, 'flat')
        self.assertEqual(self.cache.get(many, **{**self.options, 'mode': 'scalar'}).backend, 'scalar')
        huge = [(identifier, self.records[0][1]) for identifier in range(self.module.MAX_INDEX_ROWS + 1)]
        self.assertEqual(self.cache.get(huge, **self.options).backend, 'scalar')
        tiny_cache = self.module.VectorIndexCache(max_bytes=1)
        self.assertEqual(tiny_cache.get(self.records, **self.options).scores(self.query)[1], 1)
        self.assertFalse(tiny_cache.entries)
        bounded = self.module.VectorIndexCache(max_bytes=64)
        held = bounded.get(self.records, **self.options)
        bounded.get(self.records, **{**self.options, 'namespace': 'other'})
        self.assertLessEqual(sum(index.resident_bytes for index in bounded.entries.values()), 64)
        self.assertEqual(held.scores(self.query)[1], 1)
        self.cache.clear()
        with patch.object(self.module, 'FlatIndex', side_effect=MemoryError):
            self.assertEqual(self.cache.get(self.records, **self.options).backend, 'scalar')
        self.assertFalse(self.cache.entries)
        with self.assertRaises(ValueError):
            self.cache.get(self.records * 2, **self.options)

    def test_parallel_readers_publish_one_complete_snapshot(self):
        original = self.module.FlatIndex
        with patch.object(self.module, 'FlatIndex', wraps=original) as build:
            with ThreadPoolExecutor(max_workers=8) as workers:
                snapshots = list(workers.map(lambda _: self.cache.get(self.records, **self.options), range(16)))
        self.assertEqual(build.call_count, 1)
        self.assertTrue(all(index is snapshots[0] for index in snapshots))
        self.assertEqual(snapshots[0].scores(self.query)[1], 1)

    def test_production_search_preserves_guards_and_all_strategies(self):
        selected = profile()
        qa = [{'id': identifier, 'embedding': blob, 'embedding_dim': 3,
               'embedding_profile': selected.to_json(), 'question': f'evidence {identifier}',
               'answer': 'answer', 'source_metadata': None} for identifier, blob in self.records]
        qa.extend([{**qa[0], 'id': 3, 'embedding_profile': profile(revision='other').to_json()},
                   {**qa[0], 'id': 4, 'embedding': b'bad'},
                   {**qa[0], 'id': 5, 'embedding_dim': 2}])
        pdf = [{**row, 'document_id': 1, 'filename': 'guide.pdf', 'chunk_index': row['id'],
                'content': 'evidence instructions', 'page_start': 1, 'page_end': 1,
                'document_embedding_dim': 3, 'document_embedding_profile': selected.to_json()} for row in qa]
        pdf.append({**pdf[0], 'id': 6, 'document_embedding_profile': None})
        connection = MagicMock()
        with patch.object(self.scoring, 'get_conn', return_value=connection):
            for search, rows in ((self.scoring._search_qa, qa), (self.scoring._search_pdf, pdf)):
                connection.cursor.return_value.__enter__.return_value.fetchall.return_value = rows
                for strategy in ('fusion', 'hybrid', 'legacy'):
                    by_mode = {}
                    for mode in ('scalar', 'flat'):
                        with patch.dict('os.environ', {'VECTOR_SEARCH_BACKEND': mode}):
                            by_mode[mode] = search(self.query, k=None, query='evidence', profile=selected, strategy=strategy)
                    self.assertEqual([hit['id'] for hit in by_mode['flat']], [1, 2])
                    for observed, expected in zip(by_mode['flat'], by_mode['scalar']):
                        self.assertEqual(observed.keys(), expected.keys())
                        self.assertAlmostEqual(observed['score'], expected['score'], places=6)
                with patch.dict('os.environ', {'VECTOR_SEARCH_BACKEND': 'flat'}):
                    replacement = {**rows[0], 'answer': 'updated answer', 'content': 'updated content'}
                    connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [replacement]
                    result = search(self.query, k=None, profile=selected, strategy='fusion')
                    self.assertEqual(len(result), 1)
                    self.assertEqual(result[0]['answer'] if search == self.scoring._search_qa else result[0]['content'],
                                     'updated answer' if search == self.scoring._search_qa else 'updated content')

    def test_migrated_vectors_override_base_profiles_and_base_uploads_remain_visible(self):
        selected = profile()
        rows = [{'id': identifier, 'embedding': blob, 'embedding_dim': 3,
                 'embedding_profile': selected.to_json(), 'question': 'evidence', 'answer': 'answer',
                 'source_metadata': None, 'migrated_embedding': None} for identifier, blob in self.records]
        rows[0].update(embedding_profile=None, migrated_embedding=self.records[0][1])
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchall.return_value = rows
        with (patch.object(self.scoring, 'get_conn', return_value=connection),
              patch.dict('os.environ', {'VECTOR_SEARCH_BACKEND': 'flat'})):
            hits = self.scoring._search_qa(self.query, profile=selected, index_id=selected.identity, strategy='fusion')
        self.assertEqual({hit['id'] for hit in hits}, {1, 2})

    def test_benchmark_smoke_cannot_certify_an_incomplete_envelope(self):
        evaluation = importlib.import_module('vector_index_eval')
        with TemporaryDirectory() as directory, patch('builtins.print'):
            path = Path(directory) / 'report.json'
            status = evaluation.main(['--sizes', '6', '--dimension', '3', '--queries', '2', '--output', str(path)])
            report = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(status, 1)
        self.assertFalse(report['complete_envelope'])
        self.assertFalse(report['passed'])
        self.assertEqual(report['results'][0]['minimum_recall_at_6'], 1)

    def test_committed_measurements_cover_requirements_and_current_sources(self):
        evaluation = importlib.import_module('vector_index_eval')
        fusion = importlib.import_module('source_fusion_eval')
        retrieval = importlib.import_module('retrieval_eval')
        path = BACKEND_DIR.parent / 'docs/evaluations/f041-vector-index.json'
        record = json.loads(path.read_text(encoding='utf-8'))
        self.assertTrue(record['passed'])
        self.assertTrue(record['complete_envelope'])
        self.assertEqual(record['requirements'], evaluation.REQUIREMENTS)
        self.assertEqual([item['rows'] for item in record['results']], evaluation.REQUIREMENTS['corpus_sizes'])
        for name, fingerprint in record['source_sha256'].items():
            self.assertEqual(hashlib.sha256((BACKEND_DIR / name).read_text(encoding='utf-8').encode('utf-8')).hexdigest(),
                             fingerprint)
        for item in record['results']:
            self.assertTrue(all(item['gates'].values()))
            self.assertEqual(item['minimum_recall_at_6'], 1)
            self.assertLessEqual(item['flat_with_fresh_snapshot']['p95_ms'], 150)
            self.assertLessEqual(max(item['rebuild_seconds'].values()), 5)
        self.assertEqual(record['quality']['fusion_policy'], fusion.policy())
        for split, item in record['quality']['splits'].items():
            historical = retrieval.read_json(BACKEND_DIR.parent / 'docs/evaluations' / f'f039-{split}.json')
            self.assertEqual(item['historical_baseline_sha256'], retrieval.canonical_hash(historical))
            self.assertEqual(item['source_recall'], historical['source_recall']['fusion'])
            self.assertTrue(item['identical_rankings'])

    def test_setting_rejects_unknown_backend(self):
        settings = importlib.import_module('settings')
        with self.assertRaises(settings.ConfigurationError):
            settings.setting_value('VECTOR_SEARCH_BACKEND', environ={'VECTOR_SEARCH_BACKEND': 'ann'})
