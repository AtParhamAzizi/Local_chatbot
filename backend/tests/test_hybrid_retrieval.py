"""Persian BM25 arithmetic, snapshot lifecycle, query policy and publication safety."""

import importlib
import json
import math
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

import numpy as np

from core_support import BACKEND_DIR, OfflineCoreFixture
from embedding_fixtures import profile, with_profile
from test_persian_text import KnowledgeDatabase


class LexicalIndexTests(unittest.TestCase):
    def setUp(self):
        resources = ExitStack()
        self.addCleanup(resources.close)
        resources.enter_context(patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path]))
        self.module = importlib.import_module('services.lexical_index')

    def test_bm25_matches_hand_calculation_and_length_normalization(self):
        index = self.module.LexicalIndex([('first', 'کتاب کتاب دیگر'), ('second', 'کتاب')])
        scores = index.scores('کتاب')
        weight = math.log1p(0.5 / 2.5)
        self.assertAlmostEqual(scores['first'], weight * 2 * 2.2 / (2 + 1.2 * (0.25 + 0.75 * 3 / 2)))
        self.assertAlmostEqual(scores['second'], weight * 2.2 / (1 + 1.2 * (0.25 + 0.75 / 2)))
        self.assertEqual(index.scores('کتاب کتاب'), scores)
        self.assertEqual(index.scores('ناشناخته'), {})
        self.assertEqual(self.module.LexicalIndex([]).scores('کتاب'), {})
        self.assertEqual(self.module.LexicalIndex([('empty', '')]).scores('کتاب'), {})
        with self.assertRaises(ValueError):
            self.module.LexicalIndex([('same', 'one'), ('same', 'two')])

    def test_persian_variants_share_terms_without_stemming_numbers_or_negation_away(self):
        expected = self.module.tokenize('ثبت نام کاربری 123 نیست')
        self.assertEqual(self.module.tokenize('ثبت\u200cنام كاربري ١٢٣ نيست'), expected)
        self.assertNotEqual(self.module.tokenize('کد ۱۲۳'), self.module.tokenize('کد ۱۲۴'))
        self.assertIn('نیست', self.module.tokenize('فعال نیست', 'filtered-words'))
        self.assertIn('نه', self.module.tokenize('نه فقط', 'filtered-words'))
        self.assertEqual(self.module.tokenize('کتاب', 'character-3'), ['کتا', 'تاب'])
        with self.assertRaises(ValueError):
            self.module.tokenize('کتاب', 'unknown')

    def test_query_looks_up_postings_without_retokenizing_documents(self):
        documents = [(index, f'راهنما موضوع{index}') for index in range(100)]
        cache = self.module.LexicalIndexCache()
        cached = cache.get(documents, namespace='qa')
        with patch.object(self.module, 'tokenize', wraps=self.module.tokenize) as tokenize:
            self.assertIs(cache.get(list(documents), namespace='qa'), cached)
            self.assertEqual(set(cached.scores('موضوع42')), {42})
            tokenize.assert_called_once_with('موضوع42', 'words')

    def test_snapshot_edits_deletes_modes_and_profiles_invalidate_without_mutating_old_readers(self):
        cache = self.module.LexicalIndexCache(capacity=2)
        old = cache.get([(1, 'قدیمی')], namespace=('qa', 'old-profile'))
        new = cache.get([(1, 'تازه')], namespace=('qa', 'old-profile'))
        self.assertEqual(set(old.scores('قدیمی')), {1})
        self.assertEqual(new.scores('قدیمی'), {})
        self.assertEqual(old.scores('تازه'), {})
        other = cache.get([(1, 'تازه')], namespace=('qa', 'new-profile'))
        self.assertIsNot(other, new)
        self.assertEqual(len(cache.entries), 2)
        empty = cache.get([], namespace=('qa', 'new-profile'))
        self.assertEqual(empty.scores('تازه'), {})
        with self.assertRaises(TypeError):
            new.postings['تازه'][1] = 5
        cache.clear()
        self.assertEqual(len(cache.entries), 0)
        self.assertEqual(set(old.scores('قدیمی')), {1})

    def test_concurrent_cold_readers_share_one_build(self):
        cache = self.module.LexicalIndexCache()
        entered, release = threading.Event(), threading.Event()
        original = self.module.LexicalIndex

        def build(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('Index build was not released')
            return original(*args, **kwargs)

        with ThreadPoolExecutor(max_workers=4) as executor, patch.object(self.module, 'LexicalIndex', side_effect=build) as constructor:
            futures = [executor.submit(cache.get, [(1, 'کتاب')], namespace='qa') for _ in range(4)]
            try:
                self.assertTrue(entered.wait(3))
            finally:
                release.set()
            results = [future.result(timeout=5) for future in futures]
        self.assertTrue(all(result is results[0] for result in results))
        self.assertEqual(constructor.call_count, 1)

    def test_hybrid_score_is_bounded_and_monotonic_in_lexical_evidence(self):
        self.assertAlmostEqual(self.module.hybrid_score(0.5, 8, weight=0.2, scale=8), 0.5)
        self.assertAlmostEqual(self.module.hybrid_score(0.5, 0, weight=0.2, scale=8), 0.4)
        self.assertLess(self.module.hybrid_score(1, 1000, weight=0.2, scale=8), 1)
        for weight, scale in ((-1, 8), (2, 8), (0.2, 0)):
            with self.assertRaises(ValueError):
                self.module.hybrid_score(0.5, 1, weight=weight, scale=scale)

    def test_shipped_policy_matches_the_frozen_development_selection(self):
        policy = importlib.import_module('services.hybrid_search')
        evaluation = importlib.import_module('retrieval_eval')
        selection = evaluation.read_json(BACKEND_DIR.parent / 'docs/evaluations/f038-development-selection.json')
        self.assertEqual(selection['selected']['policy'], {'mode': policy.LEXICAL_MODE, 'weight': policy.LEXICAL_WEIGHT, 'scale': policy.LEXICAL_SCALE})
        self.assertEqual(selection['implementation']['lexical_source_sha256'],
                         evaluation.canonical_hash(Path(self.module.__file__).read_text(encoding='utf-8')))
        modes = {candidate['policy']['mode'] for candidate in selection['candidates']}
        self.assertEqual(modes, {'words', 'filtered-words', 'character-3'})
        self.assertEqual(selection['development_dataset']['split'], 'development')


class HybridRetrievalTests(OfflineCoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.ingestion = importlib.import_module('services.ingestion')
        self.retrieval = importlib.import_module('services.retrieval')
        self.lexical = importlib.import_module('services.lexical_index')
        self.lexical.INDEX_CACHE.clear()
        self.addCleanup(self.lexical.INDEX_CACHE.clear)
        directory = self.resources.enter_context(TemporaryDirectory())
        self.db = self.make_database(directory)
        self.resources.enter_context(patch.object(self.scoring, 'get_conn', self.db.connect))
        self.selected = profile()
        self.embed = with_profile(lambda texts: np.tile([1, 0, 0], (len(texts), 1)))
        self.service = self.ingestion.IngestionService(connect=self.db.connect, embed=self.embed, audit=lambda *args: None)

    def make_database(self, directory):
        return KnowledgeDatabase(Path(directory) / 'hybrid.sqlite')

    def ingest(self, question, answer, **options):
        return self.service.ingest_qa(json.dumps([{'question': question, 'answer': answer}]).encode(), **options)

    def hits(self, query, strategy='hybrid'):
        return self.retrieval.RetrievalService(embed=self.embed, search_qa=self.scoring._search_qa,
                                              search_pdf=self.scoring._search_pdf, strategy=strategy).retrieve(query)

    def test_qa_answer_terms_rescue_correct_source_and_cache_tracks_committed_replacement_and_delete(self):
        self.ingest('راهنمای اول', 'بازیابی گذرواژه با ایمیل تأییدشده انجام می‌شود')
        self.ingest('راهنمای دوم', 'تغییر گذرواژه با رمز فعلی انجام می‌شود')
        self.assertEqual(self.hits('تغییر رمز فعلی')[0]['question'], 'راهنمای دوم')
        self.assertEqual(self.hits('تغییر رمز فعلی', 'legacy')[0]['question'], 'راهنمای اول')
        self.ingest('راهنمای اول', 'جدیدواژه فقط در این راهنماست', replace=True)
        self.assertEqual(self.hits('جدیدواژه')[0]['question'], 'راهنمای اول')
        source_id = self.hits('جدیدواژه')[0]['id']
        with closing(self.db.connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute('DELETE FROM qa_documents WHERE id=%s', (source_id,))
            connection.commit()
        self.assertNotIn(source_id, [hit['id'] for hit in self.hits('جدیدواژه')])

    def test_failed_publication_keeps_old_lexical_snapshot_searchable(self):
        self.ingest('راهنمای اول', 'واژهقدیمی')
        before = self.hits('واژهقدیمی')
        def cancel(event):
            if event['stage'] == 'publishing' and event['completed'] == 1:
                raise RuntimeError('Canceled publication')
        with self.assertRaises(RuntimeError):
            self.ingest('راهنمای اول', 'واژهجدید', replace=True, progress=cancel)
        self.assertEqual(self.hits('واژهقدیمی'), before)
        self.assertEqual(self.hits('واژهجدید')[0]['lexical_score'], 0)

    def test_unknown_or_incompatible_profiles_never_enter_lexical_index(self):
        self.ingest('راهنمای اول', 'واژهآزمایشی')
        with closing(self.db.connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute('UPDATE qa_documents SET embedding_profile=%s', (profile(revision='other').to_json(),))
            connection.commit()
        with patch.object(self.scoring, 'rerank', wraps=self.scoring.rerank) as rerank:
            self.assertEqual(self.hits('واژهآزمایشی'), [])
            self.assertTrue(all(not call.args[0] for call in rerank.call_args_list))
        self.assertEqual(len(self.lexical.INDEX_CACHE.entries), 0)

    def test_default_and_rollback_strategy_are_captured_per_service(self):
        with patch.dict('os.environ', {'RETRIEVAL_STRATEGY': 'legacy'}):
            service = self.retrieval.RetrievalService()
        self.assertEqual(service.strategy, 'legacy')
        with self.assertRaises(ValueError):
            self.retrieval.RetrievalService(strategy='unknown')
        with patch.object(self.scoring, 'get_conn', side_effect=AssertionError('Must validate first')):
            with self.assertRaises(ValueError):
                self.scoring._search_qa(np.ones(3), profile=self.selected, strategy='unknown')

    def test_evaluation_ranking_matches_production_for_both_strategies(self):
        evaluation = importlib.import_module('retrieval_eval')
        experiment = importlib.import_module('hybrid_eval')
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
        for strategy, policy in (('legacy', None), ('hybrid', {'mode': 'words', 'weight': 0.2, 'scale': 8.0})):
            connection = MagicMock()
            connection.cursor.return_value.__enter__.return_value.fetchall.side_effect = [qa_rows, pdf_rows] * len(dataset.questions)
            cache = self.lexical.LexicalIndexCache()
            for question in dataset.questions:
                with patch.object(self.scoring, 'get_conn', return_value=connection):
                    qa = self.scoring._search_qa(queries[question['id']], query=question['question'], profile=self.selected, strategy=strategy)
                    pdf = self.scoring._search_pdf(queries[question['id']], k=8, query=question['question'], profile=self.selected, strategy=strategy)
                hits = sorted([hit for hit in qa + pdf if hit['score'] >= 0.35], key=lambda hit: (-hit['score'], hit['id']))[:6]
                observed = experiment.rank(dataset, {'documents': documents, 'queries': queries}, question, policy=policy, cache=cache)
                self.assertEqual([hit['id'] for hit in hits], [hit['source_id'] for hit in observed])
                for actual, expected in zip(hits, observed):
                    self.assertAlmostEqual(actual['score'], expected['score'])


class HybridEvaluationTests(unittest.TestCase):
    def test_frozen_evaluation_rejects_lexical_implementation_drift_before_scoring(self):
        with patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path]):
            experiment = importlib.import_module('hybrid_eval')
            evaluation = importlib.import_module('retrieval_eval')
            selection = evaluation.read_json(BACKEND_DIR.parent / 'docs/evaluations/f038-development-selection.json')
            selection['implementation']['lexical_source_sha256'] = 'changed'
            with patch.object(experiment, 'report') as report:
                with self.assertRaisesRegex(evaluation.EvaluationError, 'differs from the frozen'):
                    experiment.evaluate(evaluation.load_dataset(split='heldout'),
                                        Mock(identity=selection['embedding_profile_id']), {}, selection)
                report.assert_not_called()

    def test_cli_returns_failure_when_frozen_acceptance_fails_but_writes_the_report(self):
        with patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path]):
            experiment = importlib.import_module('hybrid_eval')
            result = {'passed': False, 'quality_passed': False, 'baseline': {'summary': {}},
                      'hybrid': {'summary': {}}, 'latency': None}
            with (patch.object(experiment, 'load_vectors', return_value=(None, {})),
                  patch.object(experiment, 'read_json', return_value={}),
                  patch.object(experiment, 'evaluate', return_value=result),
                  patch.object(experiment, 'write_json') as write,
                  patch('builtins.print')):
                status = experiment.main(['evaluate', '--split', 'heldout', '--vectors', 'vectors.json',
                                          '--selection', 'selection.json', '--output', 'result.json'])
            self.assertEqual(status, 1)
            write.assert_called_once_with(Path('result.json'), result)

    def test_heldout_cannot_select_and_acceptance_does_not_treat_missing_latency_as_a_pass(self):
        with patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path]):
            experiment = importlib.import_module('hybrid_eval')
            evaluation = importlib.import_module('retrieval_eval')
            dataset = evaluation.load_dataset(split='heldout')
            with self.assertRaisesRegex(evaluation.EvaluationError, 'only on development'):
                experiment.select(dataset, None, {})
            with patch.object(experiment, 'report') as report:
                report.return_value = {'summary': {'ndcg_at_6': 0.5, 'recall': {'6': 1}, 'mrr_at_6': 1,
                                                   'unanswerable_false_positive_rate': 1}}
                selection = evaluation.read_json(BACKEND_DIR.parent / 'docs/evaluations/f038-development-selection.json')
                captured = Mock(identity=selection['embedding_profile_id'])
                result = experiment.evaluate(dataset, captured, {}, selection, measure_latency=False)
                self.assertFalse(result['passed'])
