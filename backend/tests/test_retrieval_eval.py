"""F037 label integrity, split isolation, metric arithmetic and reproducible runs."""

import copy
import importlib
import io
import json
import math
import socket
import sys
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import numpy as np

from core_support import BACKEND_DIR, OfflineCoreFixture
from embedding_fixtures import profile


class RetrievalEvaluationTests(unittest.TestCase):
    def setUp(self):
        resources = ExitStack()
        self.addCleanup(resources.close)
        resources.enter_context(patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path]))
        self.module = importlib.import_module('retrieval_eval')
        self.dataset = self.module.load_dataset()

    def test_reviewed_inventory_and_intent_splits_have_required_coverage(self):
        result = self.module.validate_dataset()
        self.assertEqual((result['sources'], result['development'], result['heldout']), (30, 30, 30))
        self.assertEqual(result['review']['status'], 'agent-reviewed')
        for split in ('development', 'heldout'):
            dataset = self.module.load_dataset(split=split)
            self.assertEqual(sum(question['answerable'] for question in dataset.questions), 24)
            self.assertEqual(sum(not question['answerable'] for question in dataset.questions), 6)
            self.assertEqual({source['kind'] for source in dataset.sources}, {'qa', 'pdf', 'document'})
            self.assertEqual(len({question['family'] for question in dataset.questions}), 6)

    def test_development_run_never_opens_heldout_questions(self):
        original = self.module.read_json
        opened = []

        def read(path):
            opened.append(path.name)
            self.assertNotEqual(path.name, 'heldout.json')
            return original(path)

        with patch.object(self.module, 'read_json', side_effect=read):
            report = self.module.evaluate_run(self.module.load_dataset(), self.module.lexical_run(self.dataset))
        self.assertEqual(report['run']['dataset']['split'], 'development')
        self.assertEqual(opened, ['manifest.json', 'corpus.json', 'development.json'])

    def test_frozen_lexical_baselines_repeat_exactly_for_each_explicit_split(self):
        for split in ('development', 'heldout'):
            dataset = self.module.load_dataset(split=split)
            baseline = self.module.read_json(dataset.directory / 'baselines' / ('lexical-' + split + '.json'))
            first = self.module.evaluate_run(dataset, self.module.lexical_run(dataset))
            second = self.module.evaluate_run(dataset, self.module.lexical_run(dataset))
            self.assertEqual(first, baseline)
            self.assertEqual(first, second)
            self.assertTrue(all(change['delta'] == 0 for change in self.module.compare_reports(dataset, first, baseline).values()))

    def test_hand_calculated_recall_reciprocal_rank_and_graded_ndcg(self):
        question = {'answerable': True, 'relevance': [{'source_id': 'first', 'grade': 2}, {'source_id': 'second', 'grade': 1}],
                    'hard_negatives': [{'source_id': 'wrong'}]}
        hits = [{'source_id': source_id} for source_id in ('wrong', 'second', 'first')]
        result = self.module.question_metrics(question, hits)
        self.assertEqual(result['recall'], {'1': 0.0, '3': 1.0, '6': 1.0})
        self.assertAlmostEqual(result['mrr_at_6'], 0.5)
        self.assertAlmostEqual(result['ndcg_at_6'], (1 / math.log2(3) + 3 / math.log2(4)) / (3 + 1 / math.log2(3)))
        self.assertTrue(result['hard_negative_at_1'])
        partial = self.module.question_metrics(question, [{'source_id': 'first'}])
        self.assertEqual(partial['recall']['6'], 0.5)
        self.assertLess(partial['ndcg_at_6'], 1)
        self.assertEqual(self.module.question_metrics(question, [])['mrr_at_6'], 0)
        outside_cutoff = [{'source_id': 'unrelated-' + str(index)} for index in range(6)] + [{'source_id': 'first'}]
        self.assertEqual(self.module.question_metrics(question, outside_cutoff)['recall']['6'], 0)

    def test_reference_rankings_do_not_depend_on_answer_labels_or_review_annotations(self):
        altered = copy.deepcopy(self.dataset.questions)
        for question in altered:
            question.update(relevance=[], hard_negatives=[], answerable=False,
                            expected_answer='This annotation must never enter retrieval', review_note='changed')
        candidate = replace(self.dataset, questions=altered)
        self.assertEqual(self.module.lexical_run(self.dataset), self.module.lexical_run(candidate))

    def test_perfect_and_empty_rankings_keep_answerability_denominators_separate(self):
        run = self.module.lexical_run(self.dataset)
        for question in self.dataset.questions:
            run['rankings'][question['id']] = [{'source_id': item['source_id'], 'score': item['grade']}
                                               for item in sorted(question['relevance'], key=lambda item: -item['grade'])]
        perfect = self.module.evaluate_run(self.dataset, run)
        self.assertEqual(perfect['summary']['recall']['6'], 1)
        self.assertEqual(perfect['summary']['mrr_at_6'], 1)
        self.assertEqual(perfect['summary']['ndcg_at_6'], 1)
        self.assertEqual(perfect['summary']['unanswerable_false_positive_rate'], 0)
        self.assertIsNone(perfect['by_tag']['unanswerable']['mrr_at_6'])
        self.assertIsNone(perfect['by_tag']['direct']['unanswerable_false_positive_rate'])
        run['rankings'] = {question['id']: [] for question in self.dataset.questions}
        empty = self.module.evaluate_run(self.dataset, run)
        self.assertEqual(empty['summary']['recall']['6'], 0)
        self.assertEqual(empty['summary']['answerable_abstention_rate'], 1)
        self.assertEqual(empty['summary']['unanswerable_false_positive_rate'], 0)

    def test_invalid_rankings_fail_instead_of_inflating_metrics(self):
        reference = self.module.lexical_run(self.dataset)
        question_id = self.dataset.questions[0]['id']
        bad_hits = ([{'source_id': 'unknown-source', 'score': 1}],
                    [reference['rankings'][question_id][0]] * 2,
                    [{'source_id': self.dataset.sources[0]['id'], 'score': float('nan')}],
                    [{'source_id': self.dataset.sources[0]['id'], 'score': True}],
                    [{'source_id': self.dataset.sources[0]['id'], 'score': 0},
                     {'source_id': self.dataset.sources[1]['id'], 'score': 1}])
        for hits in bad_hits:
            candidate = copy.deepcopy(reference)
            candidate['rankings'][question_id] = hits
            with self.subTest(hits=hits), self.assertRaises(self.module.EvaluationError):
                self.module.evaluate_run(self.dataset, candidate)
        for mutation in ('missing', 'extra', 'split', 'checksum', 'metadata'):
            candidate = copy.deepcopy(reference)
            if mutation == 'missing':
                del candidate['rankings'][question_id]
            elif mutation == 'extra':
                candidate['rankings']['ho-unknown-1'] = []
            elif mutation == 'metadata':
                del candidate['system']['normalization']
            else:
                candidate['dataset'][mutation if mutation == 'split' else 'corpus_sha256'] = 'wrong'
            with self.subTest(mutation=mutation), self.assertRaises(self.module.EvaluationError):
                self.module.evaluate_run(self.dataset, candidate)

    def test_changed_labels_corrupt_quotes_and_cross_split_families_are_rejected(self):
        with TemporaryDirectory() as directory:
            target = Path(directory)
            originals = {name: self.module.read_json(self.dataset.directory / name)
                         for name in ('manifest.json', 'corpus.json', 'development.json', 'heldout.json')}
            for mutation in ('checksum', 'evidence', 'conflict', 'family', 'duplicate', 'unanswerable'):
                files = copy.deepcopy(originals)
                question = files['development.json'][0]
                if mutation == 'evidence':
                    question['relevance'][0]['evidence'] = 'این نقل قول در منبع وجود ندارد'
                elif mutation == 'conflict':
                    question['hard_negatives'][0]['source_id'] = question['relevance'][0]['source_id']
                elif mutation == 'family':
                    files['manifest.json']['splits']['heldout']['families'].append('signup')
                elif mutation == 'duplicate':
                    files['development.json'][1]['question'] = question['question']
                elif mutation == 'unanswerable':
                    question['answerable'] = False
                else:
                    files['corpus.json'][0]['text'] += ' changed'
                if mutation != 'checksum':
                    files['manifest.json']['splits']['development']['sha256'] = self.module.canonical_hash(files['development.json'])
                for name, value in files.items():
                    (target / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
                with self.subTest(mutation=mutation), self.assertRaises(self.module.EvaluationError):
                    self.module.load_dataset(target)

    def test_cross_split_duplicate_questions_fail_even_with_new_valid_checksums(self):
        with TemporaryDirectory() as directory:
            target = Path(directory)
            for name in ('manifest.json', 'corpus.json', 'development.json', 'heldout.json'):
                (target / name).write_text((self.dataset.directory / name).read_text(encoding='utf-8'), encoding='utf-8')
            heldout = self.module.read_json(target / 'heldout.json')
            heldout[0]['question'] = self.dataset.questions[0]['question']
            manifest = self.module.read_json(target / 'manifest.json')
            manifest['splits']['heldout']['sha256'] = self.module.canonical_hash(heldout)
            (target / 'heldout.json').write_text(json.dumps(heldout), encoding='utf-8')
            (target / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            with self.assertRaisesRegex(self.module.EvaluationError, 'overlap across splits'):
                self.module.validate_dataset(target)

    def test_json_rejects_duplicate_keys_and_nonfinite_numbers(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'bad.json'
            for raw in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '\ufffd'):
                target.write_text(raw, encoding='utf-8')
                with self.subTest(raw=raw), self.assertRaises(self.module.EvaluationError):
                    self.module.read_json(target)

    def test_baseline_comparison_rejects_other_split_versions_and_forged_metrics(self):
        report = self.module.evaluate_run(self.dataset, self.module.lexical_run(self.dataset))
        for mutation in ('split', 'metric', 'summary'):
            baseline = copy.deepcopy(report)
            if mutation == 'split':
                baseline['run']['dataset']['split'] = 'heldout'
            elif mutation == 'metric':
                baseline['metric_version'] = 'future'
            else:
                baseline['summary']['ndcg_at_6'] = 999
            with self.subTest(mutation=mutation), self.assertRaises(self.module.EvaluationError):
                self.module.compare_reports(self.dataset, report, baseline)

    def test_cli_runs_and_scores_reproducibly_and_preserves_versioned_inputs(self):
        with TemporaryDirectory() as directory, redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            target = Path(directory)
            first, second = target / 'first.json', target / 'second.json'
            self.assertEqual(self.module.main(['run', '--output', str(first)]), 0)
            self.assertEqual(self.module.main(['run', '--output', str(second)]), 0)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            predictions = target / 'predictions.json'
            predictions.write_text(json.dumps(json.loads(first.read_text(encoding='utf-8'))['run']), encoding='utf-8')
            self.assertEqual(self.module.main(['score', '--predictions', str(predictions), '--output', str(second)]), 0)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(self.module.main(['score']), 1)
            self.assertEqual(self.module.main(['run', '--ranker', 'local']), 1)
            self.assertEqual(self.module.main(['run', '--output', str(self.dataset.directory / 'corpus.json')]), 1)
            self.assertEqual(self.module.main(['check-baseline']), 0)


class LocalRetrievalEvaluationTests(OfflineCoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.module = importlib.import_module('retrieval_eval')
        self.local = importlib.import_module('retrieval_eval_local')
        self.dataset = self.module.load_dataset()
        self.profile = profile()

    def test_local_scoring_matches_production_search_and_retrieval_with_profiled_doubles(self):
        batch_type = importlib.import_module('embedding_profile').EmbeddingBatch
        source_vectors = {source['id']: np.array([1, (index + 1) / 30, (index % 4) / 4], dtype=np.float32)
                          for index, source in enumerate(self.dataset.sources)}
        text_vectors = {self.module.normalize_persian(self.module.indexed_text(source)): source_vectors[source['id']]
                        for source in self.dataset.sources}
        query_vector = np.array([0.2, 0.7, 1], dtype=np.float32)
        runtime = SimpleNamespace(profile=self.profile, embed=lambda texts: batch_type(
            np.stack([text_vectors.get(text, query_vector) for text in texts]), self.profile))
        actual = self.local.profiled_run(self.dataset, runtime)
        self.module.validate_run(self.dataset, actual)
        qa_rows, document_rows = [], []
        for source in self.dataset.sources:
            row = {'id': source['id'], 'embedding': source_vectors[source['id']].tobytes(),
                   'embedding_dim': 3, 'embedding_profile': self.profile.to_json(), 'source_metadata': None}
            if source['kind'] == 'qa':
                qa_rows.append({**row, 'question': source['question'], 'answer': source['text']})
            else:
                document_rows.append({**row, 'document_id': 1, 'filename': source['filename'], 'chunk_index': 0,
                                      'page_start': 1, 'page_end': 1, 'content': source['text'],
                                      'document_embedding_dim': 3, 'document_embedding_profile': self.profile.to_json()})
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect = [qa_rows, document_rows] * len(self.dataset.questions)
        retrieval = importlib.import_module('services.retrieval').RetrievalService(
            embed=runtime.embed, search_qa=self.scoring._search_qa, search_pdf=self.scoring._search_pdf)
        with patch.object(self.scoring, 'get_conn', return_value=connection):
            for question in self.dataset.questions:
                expected = retrieval.retrieve(question['question'])
                observed = actual['rankings'][question['id']]
                self.assertEqual([hit['source_id'] for hit in observed], [hit['id'] for hit in expected])
                for production_hit, evaluation_hit in zip(expected, observed):
                    self.assertAlmostEqual(production_hit['score'], evaluation_hit['score'])

    def test_local_profile_drift_and_invalid_vectors_are_rejected(self):
        batch_type = importlib.import_module('embedding_profile').EmbeddingBatch
        for batch in (batch_type(np.zeros((8, 3)), self.profile),
                      batch_type(np.ones((8, 3)), profile(revision='changed'))):
            runtime = SimpleNamespace(profile=self.profile, embed=Mock(return_value=batch))
            with self.subTest(batch=batch), self.assertRaises(ValueError):
                self.local.profiled_run(self.dataset, runtime)

    def test_missing_model_and_network_access_fail_explicitly(self):
        with self.assertRaises(self.module.EvaluationError):
            self.local.local_run(self.dataset, 'qwen3.5-2b', Path('no-model-here'))
        with self.assertRaises(self.module.EvaluationError):
            self.local._forbidden_network()
        with TemporaryDirectory() as directory:
            model_path = Path(directory)
            (model_path / 'config.json').write_text('{}', encoding='utf-8')
            with patch.object(sys.modules['interface'], 'load_embedding_runtime', create=True,
                              side_effect=lambda *args: socket.create_connection(('127.0.0.1', 3306))) as loader:
                with self.assertRaisesRegex(self.module.EvaluationError, 'cannot access networks or databases'):
                    self.local.local_run(self.dataset, 'qwen3.5-2b', model_path)
                loader.assert_called_once()
