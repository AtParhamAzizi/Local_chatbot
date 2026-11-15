"""Optional reranking resource bounds, fallbacks, prompt budgets and evidence replay."""

import importlib
import hashlib
import math
import sys
import unittest
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from core_support import BACKEND_DIR, OfflineCoreFixture, module_double
from embedding_fixtures import profile


def hits(count=15):
    return [{'id': index, 'score': .8 - index * .01, 'source_type': 'qa' if index % 2 else 'pdf',
             'question': 'question', 'answer': f'answer {index}', 'content': f'passage {index}',
             'source_metadata': {'location': index}} for index in range(count)]


class LocalRerankerTests(OfflineCoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.module = importlib.import_module('services.local_reranker')
        self.config = self.module.RerankerConfig('local-test-only', candidates=12, max_length=512, model_weight=1)
        self.runtime = Mock()
        self.runtime.score.side_effect = lambda query, docs: [index / len(docs) for index in range(len(docs))]
        self.loader = Mock(return_value=self.runtime)
        self.reranker = self.module.LocalReranker(self.config, loader=self.loader)

    def test_bounded_candidates_preserve_metadata_and_first_stage_scores(self):
        original = hits()
        base = Mock()
        base.retrieve.return_value = original
        service = self.module.RerankingRetrievalService(base, self.reranker)
        result = service.retrieve('query')
        base.retrieve.assert_called_once_with('query', limit=12)
        self.assertEqual([item['id'] for item in result], [11, 10, 9, 8, 7, 6])
        self.assertEqual(len(self.runtime.score.call_args.args[1]), 12)
        self.assertEqual(result[0]['source_metadata'], original[11]['source_metadata'])
        self.assertEqual(result[0]['score'], original[11]['score'])
        self.assertNotIn('rerank_score', original[11])
        self.assertIn('question\nanswer 1', self.runtime.score.call_args.args[1])
        service.retrieve('other')
        self.loader.assert_called_once_with(self.config)

    def test_larger_limit_keeps_unscored_tail_and_zero_limit_does_no_work(self):
        base = Mock()
        base.retrieve.return_value = hits()
        service = self.module.RerankingRetrievalService(base, self.reranker)
        result = service.retrieve('query', limit=15)
        self.assertEqual([item['id'] for item in result[-3:]], [12, 13, 14])
        self.assertEqual(len(result), 15)
        base.reset_mock()
        self.assertEqual(service.retrieve('query', limit=0), [])
        base.retrieve.assert_not_called()
        self.assertEqual(self.reranker.rerank('query', hits(), limit=-1), [])

    def test_busy_model_falls_back_without_waiting_or_loading(self):
        self.reranker.lock.acquire()
        try:
            self.assertEqual(self.reranker.rerank('query', hits()), hits()[:6])
            self.loader.assert_not_called()
        finally:
            self.reranker.lock.release()

    def test_failed_load_is_attempted_once_and_logs_no_private_text(self):
        self.loader.side_effect = RuntimeError('private-path and query')
        with self.assertLogs('chatbot.reranker', level='WARNING') as records:
            for _ in range(2):
                self.assertEqual(self.reranker.rerank('private query', hits()), hits()[:6])
        self.loader.assert_called_once()
        self.assertEqual(len(records.output), 1)
        self.assertNotIn('private', records.output[0])
        self.assertTrue(self.reranker.failed)
        self.assertIsNone(self.reranker.runtime)
        self.assertFalse(self.reranker.lock.locked())

    def test_invalid_model_output_fails_closed_without_partial_reordering(self):
        for scores in ([.9], [math.nan] * 12, [math.inf] * 12, [-.1] * 12, [1.1] * 12):
            with self.subTest(scores=scores):
                runtime = Mock()
                runtime.score.return_value = scores
                reranker = self.module.LocalReranker(self.config, loader=Mock(return_value=runtime))
                with self.assertLogs('chatbot.reranker', level='WARNING'):
                    self.assertEqual(reranker.rerank('query', hits()), hits()[:6])
                self.assertTrue(reranker.failed)

    def test_empty_or_single_candidate_never_loads_model(self):
        for count in (0, 1):
            self.assertEqual(self.reranker.rerank('query', hits(count)), hits(count))
        self.loader.assert_not_called()

    def test_equal_model_scores_retain_source_neutral_first_stage_ties(self):
        original = [{**hit, 'score': .5} for hit in hits(4)]
        result = self.module.order_candidates(original, [.9] * 4, weight=.5)
        self.assertEqual([item['id'] for item in result], [0, 1, 2, 3])
        self.assertAlmostEqual(result[0]['rerank_score'], .7)

    def test_off_and_historical_strategies_use_original_service(self):
        service = Mock()
        self.assertIs(self.module.optional_reranking(service), service)
        with patch.dict('os.environ', {'RERANKER_PROFILE': 'gtx1050-4gb', 'RETRIEVAL_STRATEGY': 'legacy'}):
            self.assertIs(self.module.optional_reranking(service), service)

    def test_factories_share_lazy_model_and_reject_configuration_drift(self):
        with (patch.object(self.module, '_shared_reranker', None),
              patch.dict('os.environ', {'RERANKER_PROFILE': 'gtx1050-4gb', 'RERANKER_MODEL_PATH': 'test-local-model'})):
            one = self.module.optional_reranking(Mock())
            two = self.module.optional_reranking(Mock())
            self.assertIs(one.reranker, two.reranker)
            self.assertIsNone(one.reranker.runtime)
            with patch.dict('os.environ', {'RERANKER_MODEL_PATH': 'changed'}):
                with self.assertRaisesRegex(ValueError, 'Restart'):
                    self.module.optional_reranking(Mock())

    def test_explicit_injected_retrieval_is_preserved_with_profile_enabled(self):
        application = importlib.import_module('application')
        service = Mock()
        with patch.dict('os.environ', {'RERANKER_PROFILE': 'gtx1050-4gb'}):
            self.assertIs(application.create_ui_app(retrieval_service=service).extensions['retrieval_service'], service)

    def test_invalid_profile_and_resource_limits_are_rejected(self):
        settings = importlib.import_module('settings')
        with self.assertRaises(settings.ConfigurationError):
            settings.setting_value('RERANKER_PROFILE', environ={'RERANKER_PROFILE': 'auto'})
        for options in ({'candidates': 0}, {'candidates': 25}, {'batch_size': 5}, {'max_length': 1024},
                        {'model_weight': math.nan}, {'device': 'remote'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.module.RerankerConfig('local', **options)

    def test_token_budget_retains_decision_suffix_and_caps_query(self):
        runtime = self.module.QwenRerankerRuntime.__new__(self.module.QwenRerankerRuntime)
        runtime.config = self.config
        runtime.prefix, runtime.separator, runtime.suffix = [1] * 50, [2], [3] * 10
        runtime.encode = lambda text, limit=None: [4] * min(len(text), limit or len(text))
        runtime.tokenizer = Mock()
        runtime.inputs('q' * 4096, ['d' * 20000, 'short'])
        sequences = runtime.tokenizer.pad.call_args.args[0]['input_ids']
        self.assertEqual(len(sequences[0]), 512)
        self.assertEqual(sequences[0][-10:], [3] * 10)
        self.assertEqual(len(sequences[1]), 50 + 128 + 1 + 5 + 10)
        runtime.tokenizer.pad.return_value.to.assert_called_once_with('cuda')

    def test_forward_batches_use_final_logits_without_generation_or_cache(self):
        class Tensor:
            def __init__(self, values):
                self.values = np.array(values)

            def __getitem__(self, key):
                return Tensor(self.values[key])

            def float(self):
                return self

            def softmax(self, dim):
                values = np.exp(self.values - self.values.max(axis=dim, keepdims=True))
                return Tensor(values / values.sum(axis=dim, keepdims=True))

            def cpu(self):
                return self

            def tolist(self):
                return self.values.tolist()

        runtime = self.module.QwenRerankerRuntime.__new__(self.module.QwenRerankerRuntime)
        runtime.config = self.module.RerankerConfig('local', candidates=5, batch_size=2)
        runtime.torch = SimpleNamespace(inference_mode=nullcontext)
        runtime.manifest = Mock()
        runtime.no_id, runtime.yes_id = 0, 1
        runtime.inputs = Mock(side_effect=lambda query, docs: {'batch': len(docs)})
        runtime.model = Mock(side_effect=lambda batch, **options: SimpleNamespace(logits=Tensor([[[0., 2.]]] * batch)))
        with patch('embedding_profile.require_unchanged_artifacts') as verify:
            scores = runtime.score('query', ['a', 'b', 'c', 'd', 'e'])
        verify.assert_called_once_with(runtime.manifest)
        self.assertEqual([call.kwargs['batch'] for call in runtime.model.call_args_list], [2, 2, 1])
        self.assertTrue(all(call.kwargs['use_cache'] is False and call.kwargs['logits_to_keep'] == 1
                            for call in runtime.model.call_args_list))
        self.assertTrue(all(abs(score - .88079707798) < 1e-8 for score in scores))
        with self.assertRaisesRegex(ValueError, 'budget'):
            runtime.score('query', ['a'] * 6)


class RerankerEvaluationTests(OfflineCoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.module = importlib.import_module('reranker_eval')

    def test_heldout_cannot_select_policy_or_score_before_valid_selection(self):
        evaluation = importlib.import_module('retrieval_eval')
        with self.assertRaises(evaluation.EvaluationError):
            self.module.select(evaluation.load_dataset(split='heldout'), None, {}, {})
        with (patch.object(self.module, 'load_vectors', return_value=(profile(), {})),
              patch.object(self.module, 'read_json', return_value={}),
              patch.object(self.module, 'measure') as measure, patch('builtins.print')):
            self.assertEqual(self.module.main(['evaluate', '--split', 'heldout', '--vectors', 'unused',
                                               '--selection', 'invalid', '--output', 'unused']), 1)
        measure.assert_not_called()

    def test_resource_gate_rejects_each_overrun_and_other_hardware(self):
        resources = {'warm_p95_ms': 4000, 'cold_load_seconds': 30, 'gpu_peak_reserved_mib': 1500,
                     'process_peak_mib': 3000, 'hardware': {'gpu': 'NVIDIA GeForce GTX 1050', 'gpu_total_mib': 4096},
                     'coexistence': {'passed': True}}
        self.assertTrue(self.module.resources_pass(resources))
        for name in ('warm_p95_ms', 'cold_load_seconds', 'gpu_peak_reserved_mib', 'process_peak_mib'):
            self.assertFalse(self.module.resources_pass({**resources, name: 99999}))
        self.assertFalse(self.module.resources_pass({**resources, 'hardware': {'gpu': 'other', 'gpu_total_mib': 4096}}))

    def test_quality_gate_rejects_no_gain_and_any_source_recall_loss(self):
        evaluation = importlib.import_module('retrieval_eval')
        dataset = evaluation.load_dataset(split='development')
        baseline = evaluation.read_json(BACKEND_DIR.parent / 'docs/evaluations/f039-development.json')['fusion']
        checks, _ = self.module.quality(dataset, baseline, baseline)
        self.assertFalse(checks['ndcg_gain'])
        self.assertTrue(checks['source_balance'])
        degraded = {**baseline, 'run': {**baseline['run'], 'rankings': {key: [] for key in baseline['run']['rankings']}}}
        checks, _ = self.module.quality(dataset, baseline, degraded)
        self.assertFalse(checks['source_balance'])

    def test_committed_measurements_replay_and_match_frozen_policy(self):
        evaluation = importlib.import_module('retrieval_eval')
        from embedding_profile import EmbeddingProfile

        directory = BACKEND_DIR.parent / 'docs/evaluations'
        development = evaluation.read_json(directory / 'f040-development.json')
        heldout = evaluation.read_json(directory / 'f040-heldout.json')
        self.assertEqual(heldout['selection_sha256'], evaluation.canonical_hash(development))
        self.assertEqual(development['development_candidates'][0]['weight'], self.module.MODEL_WEIGHT)
        for split, record in (('development', development), ('heldout', heldout)):
            with self.subTest(split=split):
                self.assertEqual(record['policy'], self.module.policy())
                self.assertTrue(record['passed'])
                self.assertTrue(self.module.resources_pass(record['resources']))
                timings = sorted(sample['milliseconds'] for sample in record['resources']['samples'])
                self.assertEqual(len(timings), 60)
                self.assertEqual(record['resources']['warm_p95_ms'], timings[math.ceil(.95 * len(timings)) - 1])
                self.assertEqual(record['model'], development['model'])
                frozen_fusion = evaluation.read_json(directory / f'f039-{split}.json')['fusion']
                self.assertEqual(record['baseline']['summary'], frozen_fusion['summary'])
                self.assertEqual(record['baseline']['run']['rankings'], frozen_fusion['run']['rankings'])
                dataset = evaluation.load_dataset(split=split)
                selected_profile = EmbeddingProfile.from_json(record['baseline']['run']['system']['embedding_profile'])
                rows = {identifier: [{'id': hit['source_id'], 'score': hit['score']} for hit in candidates]
                        for identifier, candidates in record['candidates'].items()}
                self.assertTrue(all(len(row) <= self.module.CANDIDATE_LIMIT for row in rows.values()))
                for key, scores in (('baseline', None), ('reranked', record['candidate_scores'])):
                    self.assertEqual(self.module.ranked_report(dataset, selected_profile, rows, scores), record[key])
                checks, sources = self.module.quality(dataset, record['baseline'], record['reranked'])
                self.assertEqual(checks, record['quality_checks'])
                self.assertEqual(sources, record['source_recall'])
                self.assertTrue(all(checks.values()))


class RerankerPreparationTests(OfflineCoreFixture, unittest.TestCase):
    def test_range_download_resumes_and_checks_hash_before_publishing(self):
        module = importlib.import_module('reranker_prepare')
        payload = b'pinned-model-content'
        metadata = SimpleNamespace(rfilename='model.safetensors',
                                   lfs=SimpleNamespace(size=len(payload), sha256=hashlib.sha256(payload).hexdigest()))
        api = Mock()
        api.model_info.return_value = SimpleNamespace(siblings=[metadata])
        response = SimpleNamespace(status_code=206, headers={'Content-Range': f'bytes 6-{len(payload) - 1}/{len(payload)}'},
                                   content=payload[6:], raise_for_status=Mock())
        get = Mock(return_value=response)
        doubles = {'huggingface_hub': module_double('huggingface_hub', HfApi=Mock(return_value=api)),
                   'httpx2': module_double('httpx2', get=get, Timeout=Mock(), HTTPError=RuntimeError)}
        with TemporaryDirectory() as directory, patch.dict(sys.modules, doubles), patch('builtins.print'):
            output = Path(directory)
            partial = output / 'model.safetensors.partial'
            partial.write_bytes(payload[:6])
            module.download_weights(output)
            self.assertEqual((output / 'model.safetensors').read_bytes(), payload)
            self.assertFalse(partial.exists())
            self.assertEqual(get.call_args.kwargs['headers'], {'Range': f'bytes=6-{len(payload) - 1}'})
            api.model_info.assert_called_once_with(module.REPOSITORY, revision=module.REVISION, files_metadata=True)
            (output / 'model.safetensors').write_bytes(b'x' * len(payload))
            with self.assertRaisesRegex(RuntimeError, 'checksum'):
                module.download_weights(output)
            self.assertEqual(get.call_count, 1)  # a complete local file is verified without fetching again

    def test_range_mismatch_cannot_publish_weights(self):
        module = importlib.import_module('reranker_prepare')
        metadata = SimpleNamespace(rfilename='model.safetensors', lfs=SimpleNamespace(size=3, sha256='unused'))
        api = Mock()
        api.model_info.return_value = SimpleNamespace(siblings=[metadata])
        response = SimpleNamespace(status_code=200, headers={}, content=b'bad', raise_for_status=Mock())
        doubles = {'huggingface_hub': module_double('huggingface_hub', HfApi=Mock(return_value=api)),
                   'httpx2': module_double('httpx2', get=Mock(return_value=response), Timeout=Mock(), HTTPError=RuntimeError)}
        with TemporaryDirectory() as directory, patch.dict(sys.modules, doubles):
            with self.assertRaisesRegex(RuntimeError, 'ranged'):
                module.download_weights(Path(directory))
            self.assertFalse((Path(directory) / 'model.safetensors').exists())
