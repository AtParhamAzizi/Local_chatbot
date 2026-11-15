"""Mixed-source ranking, evidence admission, source symmetry and evaluator parity."""

import importlib
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import numpy as np

from core_support import BACKEND_DIR, OfflineCoreFixture
from embedding_fixtures import profile, with_profile


def hit(identifier, kind='qa', *, text='shared evidence', semantic=0.8, score=0.8):
    return {'id': identifier, 'source_type': kind, 'semantic_score': semantic, 'score': score,
            'question': '', 'answer': text, 'content': text}


class SourceFusionTests(OfflineCoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.module = importlib.import_module('services.source_fusion')
        self.selected = profile()
        self.cache = importlib.import_module('services.lexical_index').LexicalIndexCache()

    def fuse(self, hits, query='evidence', **options):
        return self.module.fuse(hits, query, profile=self.selected, cache=self.cache, **options)

    def test_shared_calibration_arithmetic_and_input_preservation(self):
        hits = [hit(1, semantic=0.9), hit(1, 'pdf', semantic=0.9), hit(2, 'document', semantic=0.8)]
        cache = Mock()
        cache.get.return_value.scores.return_value = {('qa', 1): 8, ('document', 1): 8}
        fused = self.module.fuse(hits, 'query', profile=self.selected, cache=cache)
        by_key = {self.module.source_key(item): item for item in fused}
        for key in (('qa', 1), ('document', 1)):
            self.assertAlmostEqual(by_key[key]['score'], 0.8 * 0.9 + 0.2 * 8 / 24)
        self.assertAlmostEqual(by_key['document', 2]['score'], 0.8 * 0.8)
        self.assertTrue(all('lexical_score' not in item for item in hits))

    def test_collection_score_scales_and_candidate_order_cannot_affect_fusion(self):
        hits = [hit(1, text='password recovery', semantic=0.95),
                hit(1, 'pdf', text='shipping hours', semantic=0.7),
                hit(2, 'document', text='password guide', semantic=0.8)]
        expected = self.fuse(hits, 'password')
        changed = [{**item, 'score': (100000 if item['source_type'] == 'pdf' else -9000)} for item in reversed(hits)]
        self.assertEqual(self.fuse(changed, 'password'), expected)

    def test_paired_mixed_source_evaluation_is_balanced_even_with_unequal_collection_sizes(self):
        wins = {'qa': 0, 'pdf': 0, 'document': 0}
        for kind in wins:
            for query_id in range(6):
                relevant = hit(100, kind, text=f'target{query_id} instructions', semantic=0.95)
                others = [hit(index, 'pdf' if kind == 'qa' else 'qa', text=f'unrelated{index}', semantic=0.65)
                          for index in range(20)]
                results = self.fuse(others + [relevant], f'target{query_id}')
                self.assertEqual(results[0]['id'], 100)
                wins[kind] += results[0]['source_type'] == kind
                swapped = [{**item, 'source_type': 'pdf' if item['source_type'] == 'qa' else 'qa'}
                           for item in others + [relevant]]
                self.assertEqual([item['id'] for item in self.fuse(swapped, f'target{query_id}')],
                                 [item['id'] for item in results])
        self.assertEqual(wins, {'qa': 6, 'pdf': 6, 'document': 6})

    def test_weak_candidates_do_not_get_promoted_by_rank_and_no_source_quota_is_forced(self):
        self.assertEqual(self.fuse([hit(1, semantic=0.1), hit(1, 'pdf', semantic=0.2)], 'unmatched'), [])
        strong = [hit(index, text=f'guide{index}', semantic=0.8) for index in range(8)]
        results = self.fuse(strong + [hit(1, 'pdf', semantic=0.1)], 'unmatched')
        self.assertEqual(len(results), 6)
        self.assertEqual({item['source_type'] for item in results}, {'qa'})
        self.assertTrue(all(item['lexical_score'] == 0 for item in results))
        self.assertEqual(self.fuse(strong, limit=0), [])
        self.assertEqual(self.fuse([]), [])

    def test_equal_signals_have_source_independent_scores_and_deterministic_ties(self):
        hits = [hit(1), hit(1, 'pdf'), hit(2, 'document')]
        expected = self.fuse(hits)
        self.assertEqual(expected, self.fuse(list(reversed(hits))))
        self.assertEqual(len({item['score'] for item in expected}), 1)
        self.assertEqual(len(expected), 3)  # table-local ID collision is not deduplication
        with self.assertRaises(ValueError):
            self.fuse([hit(1), hit(1)])
        for value in (math.nan, math.inf):
            with self.assertRaises(ValueError):
                self.fuse([hit(1, semantic=value)])

    def test_combined_snapshot_tracks_edits_deletes_profile_and_input_order(self):
        hits = [hit(1), hit(1, 'pdf')]
        self.fuse(hits)
        old = list(self.cache.entries.values())[0]
        self.fuse(list(reversed(hits)))
        self.assertEqual(len(self.cache.entries), 1)
        self.fuse([hits[0], hit(1, 'pdf', text='replacement')])
        self.assertEqual(len(self.cache.entries), 2)
        self.fuse([hits[0]])
        self.assertEqual(len(self.cache.entries), 2)
        self.assertTrue(old.scores('evidence'))
        self.module.fuse(hits, 'evidence', profile=profile(revision='new'), cache=self.cache)
        self.assertEqual(len(self.cache.entries), 2)

    def test_default_service_requests_every_compatible_candidate_before_fusion(self):
        retrieval = importlib.import_module('services.retrieval')
        qa = Mock(return_value=[hit(index, text=f'other{index}') for index in range(9)] +
                               [hit(10, text='specific instructions')])
        pdf = Mock(return_value=[hit(1, 'pdf', semantic=0.6)])
        service = retrieval.RetrievalService(embed=with_profile(lambda texts: np.ones((len(texts), 3))),
                                              search_qa=qa, search_pdf=pdf)
        self.assertEqual(service.strategy, 'fusion')
        self.assertEqual(service.retrieve('specific')[0]['id'], 10)
        for search in (qa, pdf):
            self.assertIsNone(search.call_args.kwargs['k'])
            self.assertEqual(search.call_args.kwargs['strategy'], 'fusion')

    def test_evaluator_matches_full_production_search_and_default_service(self):
        evaluation = importlib.import_module('retrieval_eval')
        experiment = importlib.import_module('source_fusion_eval')
        dataset = evaluation.load_dataset()
        documents = {source['id']: np.array([1, (index + 1) / 30, (index % 4) / 4], dtype=np.float32)
                     for index, source in enumerate(dataset.sources)}
        queries = {question['id']: np.array([0.2, 0.7, 1], dtype=np.float32) for question in dataset.questions}
        qa_rows, pdf_rows = [], []
        for source in dataset.sources:
            row = {'id': source['id'], 'embedding': documents[source['id']].tobytes(), 'embedding_dim': 3,
                   'embedding_profile': self.selected.to_json(), 'source_metadata': None}
            if source['kind'] == 'qa':
                qa_rows.append({**row, 'question': source['question'], 'answer': source['text']})
            else:
                pdf_rows.append({**row, 'document_id': 1, 'filename': source['filename'], 'chunk_index': 0,
                                 'page_start': 1, 'page_end': 1, 'content': source['text'],
                                 'document_embedding_profile': self.selected.to_json(), 'document_embedding_dim': 3})
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchall.side_effect = [qa_rows, pdf_rows] * len(dataset.questions)
        retrieval = importlib.import_module('services.retrieval')
        cache = importlib.import_module('services.lexical_index').LexicalIndexCache()
        for question in dataset.questions:
            embed = with_profile(lambda texts: np.array([queries[question['id']]]))
            service = retrieval.RetrievalService(embed=embed, search_qa=self.scoring._search_qa, search_pdf=self.scoring._search_pdf)
            with patch.object(self.scoring, 'get_conn', return_value=connection):
                actual = service.retrieve(question['question'])
            expected = experiment.rank(dataset, self.selected, {'documents': documents, 'queries': queries}, question, cache=cache)
            self.assertEqual([item['id'] for item in actual], [item['source_id'] for item in expected])
            for observed, wanted in zip(actual, expected):
                self.assertAlmostEqual(observed['score'], wanted['score'])


class SourceFusionEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.path_patch = patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path])
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.module = importlib.import_module('source_fusion_eval')

    def test_freeze_rejects_heldout_before_loading_vectors(self):
        with patch.object(self.module, 'load_vectors') as load, patch('builtins.print'):
            status = self.module.main(['freeze', '--split', 'heldout', '--vectors', 'unused', '--output', 'unused'])
        self.assertEqual(status, 1)
        load.assert_not_called()

    def test_frozen_implementation_drift_is_rejected_before_evaluation(self):
        evaluation = importlib.import_module('retrieval_eval')
        selection = {'format_version': 1, 'passed': True, 'policy': {**self.module.policy(), 'lexical_scale': 999},
                     'development_dataset': evaluation.load_dataset().identity,
                     'embedding_profile_id': profile().identity}
        with (patch.object(self.module, 'load_vectors', return_value=(profile(), {})),
              patch.object(self.module, 'read_json', return_value=selection),
              patch.object(self.module, 'compare') as compare, patch('builtins.print')):
            status = self.module.main(['evaluate', '--vectors', 'unused', '--selection', 'frozen', '--output', 'unused'])
        self.assertEqual(status, 1)
        compare.assert_not_called()

    def test_committed_evidence_matches_the_shipped_policy_and_replays_by_source_kind(self):
        evaluation = importlib.import_module('retrieval_eval')
        directory = BACKEND_DIR.parent / 'docs/evaluations'
        development = evaluation.read_json(directory / 'f039-development.json')
        heldout = evaluation.read_json(directory / 'f039-heldout.json')
        self.assertEqual(development['policy'], self.module.policy())
        self.assertEqual(heldout['policy'], self.module.policy())
        self.assertEqual(heldout['selection_sha256'], evaluation.canonical_hash(development))
        self.assertEqual(len(development['development_candidates']), 48)
        selected = development['development_candidates'][0]
        self.assertEqual((selected['weight'], selected['scale']),
                         (self.module.LEXICAL_WEIGHT, self.module.LEXICAL_SCALE))
        for split, record in (('development', development), ('heldout', heldout)):
            dataset = evaluation.load_dataset(split=split)
            self.assertTrue(record['passed'])
            for strategy in ('baseline', 'fusion'):
                self.assertEqual(evaluation.evaluate_run(dataset, record[strategy]['run'])['summary'],
                                 record[strategy]['summary'])
                self.assertEqual(self.module.source_metrics(dataset, record[strategy]['run']['rankings']),
                                 record['source_recall'][strategy])
            for kind in ('qa', 'pdf', 'document'):
                self.assertGreaterEqual(record['source_recall']['fusion'][kind]['recall']['6'],
                                        record['source_recall']['baseline'][kind]['recall']['6'])

    def test_policy_selection_rejects_heldout_without_scoring(self):
        evaluation = importlib.import_module('retrieval_eval')
        with patch.object(self.module, 'compare') as compare:
            with self.assertRaises(evaluation.EvaluationError):
                self.module.select(evaluation.load_dataset(split='heldout'), None, {})
        compare.assert_not_called()

    def test_source_recall_gives_no_cross_kind_credit(self):
        dataset = SimpleNamespace(sources=[{'id': 'q', 'kind': 'qa'}, {'id': 'p', 'kind': 'pdf'}],
                                  questions=[{'id': 'question', 'relevance': [{'source_id': 'q', 'grade': 3},
                                                                           {'source_id': 'p', 'grade': 2}]}])
        metrics = self.module.source_metrics(dataset, {'question': [{'source_id': 'q', 'score': 1}]})
        self.assertEqual(metrics['qa']['recall']['6'], 1)
        self.assertEqual(metrics['pdf']['recall']['6'], 0)
        self.assertIsNone(metrics['document']['recall']['6'])

    def test_failed_acceptance_still_writes_report_and_returns_nonzero(self):
        result = {'passed': False, 'quality_passed': False, 'source_balance_passed': True, 'source_recall': {}}
        with (patch.object(self.module, 'load_vectors', return_value=(profile(), {})),
              patch.object(self.module, 'read_json', return_value={}),
              patch.object(self.module, 'compare', return_value=result),
              patch.object(self.module, 'select', return_value=[]),
              patch.object(self.module, 'write_json') as write, patch('builtins.print')):
            status = self.module.main(['freeze', '--vectors', 'unused', '--output', 'report.json'])
        self.assertEqual(status, 1)
        write.assert_called_once_with(Path('report.json'), result)
