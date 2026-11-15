"""Offline local-model benchmark; development selection precedes heldout scoring."""

import argparse
import json
import math
import platform
import socket
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from hybrid_eval import load_vectors, write_json
from persian_text import NORMALIZATION_VERSION
from retrieval_eval import EvaluationError, canonical_hash, evaluate_run, load_dataset, read_json
from source_fusion_eval import policy as fusion_policy, source_metrics
from services.lexical_index import LexicalIndexCache
from services.local_reranker import (BATCH_SIZE, CANDIDATE_LIMIT, MAX_LENGTH, MODEL_WEIGHT, POLICY_VERSION, RANK_CONSTANT,
                                     QwenRerankerRuntime, RerankerConfig, order_candidates)
from services.source_fusion import fuse, lexical_text


HARDWARE_PROFILE = 'gtx1050-4gb'
GATES = {'minimum_ndcg_gain': 0.01, 'maximum_warm_p95_ms': 5000,
         'maximum_cold_load_seconds': 60, 'maximum_gpu_peak_mib': 2048,
         'maximum_process_increment_mib': 4096, 'maximum_coexist_gpu_mib': 3840,
         'maximum_coexist_process_mib': 8192,
         'recall_or_mrr_loss_allowed': False,
         'source_recall_loss_allowed': False, 'false_positive_increase_allowed': False}


def policy():
    root = Path(__file__).parent
    return {'version': POLICY_VERSION, 'candidates': CANDIDATE_LIMIT, 'batch_size': BATCH_SIZE,
            'max_length': MAX_LENGTH, 'model_weight': MODEL_WEIGHT, 'first_stage': fusion_policy(),
            'fusion': 'weighted-rrf', 'rank_constant': RANK_CONSTANT,
            'hardware_profile': HARDWARE_PROFILE, 'gates': GATES,
            'source_sha256': {name: canonical_hash((root / name).read_text(encoding='utf-8'))
                              for name in ('services/local_reranker.py', 'reranker_eval.py')}}


def candidates(dataset, profile, vectors):
    import numpy as np

    cache, output = LexicalIndexCache(), {}
    for question in dataset.questions:
        query = vectors['queries'][question['id']]
        hits = []
        for source in dataset.sources:
            vector = vectors['documents'][source['id']]
            semantic = float(np.dot(query, vector) / (float(np.linalg.norm(query)) * float(np.linalg.norm(vector))))
            hits.append({'id': source['id'], 'source_type': source['kind'], 'semantic_score': semantic,
                         'question': source.get('question'), 'answer': source['text'], 'content': source['text']})
        output[question['id']] = fuse(hits, question['question'], profile=profile, cache=cache, limit=CANDIDATE_LIMIT)
    return output


def ranked_report(dataset, profile, rows, scores=None, weight=MODEL_WEIGHT, method='rrf', rank_constant=RANK_CONSTANT):
    rankings = {}
    for identifier, hits in rows.items():
        if scores is None:
            ranked = hits
        elif method == 'rrf':
            ranked = order_candidates(hits, scores[identifier], weight=weight, rank_constant=rank_constant)
        else:
            # Development-only alternatives; serving uses the frozen rank fusion.
            values = scores[identifier]
            if method == 'logit':
                values = [(max(-10, min(10, math.log(max(1e-6, value) / max(1e-6, 1 - value)))) + 10) / 20
                          for value in values]
            ranked = sorted([{**hit, 'rerank_score': weight * value + (1 - weight) * hit['score']}
                             for hit, value in zip(hits, values)], key=lambda hit: -hit['rerank_score'])
        key = 'score' if scores is None else 'rerank_score'
        rankings[identifier] = [{'source_id': hit['id'], 'score': hit[key]} for hit in ranked[:6]]
    run = {'format_version': 1, 'dataset': dataset.identity,
           'system': {'name': 'fusion' if scores is None else POLICY_VERSION, 'version': '1',
                      'normalization': NORMALIZATION_VERSION,
                      'embedding_profile': profile.to_json(), 'model_weight': weight if scores else None,
                      'fusion': method if scores else None, 'rank_constant': rank_constant if scores and method == 'rrf' else None},
           'rankings': rankings}
    return evaluate_run(dataset, run)


def quality(dataset, baseline, after):
    before, selected = baseline['summary'], after['summary']
    source_before = source_metrics(dataset, baseline['run']['rankings'])
    source_after = source_metrics(dataset, after['run']['rankings'])
    checks = {'ndcg_gain': selected['ndcg_at_6'] >= before['ndcg_at_6'] + GATES['minimum_ndcg_gain'],
              'recall': selected['recall']['6'] >= before['recall']['6'],
              'mrr': selected['mrr_at_6'] >= before['mrr_at_6'],
              'false_positives': selected['unanswerable_false_positive_rate'] <= before['unanswerable_false_positive_rate'],
              'source_balance': all(source_after[kind]['recall']['6'] >= value['recall']['6']
                                    for kind, value in source_before.items() if value['answerable_questions'])}
    return checks, {'baseline': source_before, 'reranked': source_after}


def select(dataset, profile, rows, scores):
    if dataset.identity['split'] != 'development':
        raise EvaluationError('Selection requires development questions')
    baseline = ranked_report(dataset, profile, rows)
    choices = []
    for method, constant in (('rrf', 1), ('rrf', 5), ('rrf', 60), ('probability', 0), ('logit', 0)):
        for weight in (.1, .2, .25, .3, .4, .5, .6, .75, .9, 1.0):
            after = ranked_report(dataset, profile, rows, scores, weight, method, constant)
            checks, _ = quality(dataset, baseline, after)
            choices.append({'method': method, 'rank_constant': constant, 'weight': weight,
                            'summary': after['summary'], 'checks': checks, 'passed': all(checks.values())})
    choices.sort(key=lambda row: (-row['passed'], -row['summary']['ndcg_at_6'],
                                  -row['summary']['recall']['6'], -row['summary']['mrr_at_6'], row['weight'],
                                  row['rank_constant'], row['method']))
    return choices


def process_peak_mib(*, peak=True):
    if sys.platform != 'win32':
        if not peak:
            import os
            if sys.platform.startswith('linux'):
                return int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE') / 1024**2
            raise EvaluationError('Current process memory measurement is unavailable on this platform')
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value / (1024**2 if sys.platform == 'darwin' else 1024)
    import ctypes
    from ctypes import wintypes

    class MemoryCounters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                                               'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage',
                                               'PagefileUsage', 'PeakPagefileUsage')]
    counters = MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    process = ctypes.windll.kernel32.GetCurrentProcess
    process.restype = wintypes.HANDLE
    memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
    memory_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
    if not memory_info(process(), ctypes.byref(counters), counters.cb):
        raise EvaluationError('Cannot measure process peak memory')
    return (counters.PeakWorkingSetSize if peak else counters.WorkingSetSize) / 1024**2


def load_generator():
    """Match production startup: the default generator is resident before reranking."""
    import torch
    from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    path = Path(__file__).resolve().parent / 'modelgen/qwen3.5-2B'
    config = AutoConfig.from_pretrained(str(path), local_files_only=True, trust_remote_code=False)
    if config.model_type == 'qwen3_5':
        config = config.text_config
    generator = AutoModelForCausalLM.from_pretrained(
        str(path), config=config, local_files_only=True, trust_remote_code=False, dtype=torch.float16,
        quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4',
                                              bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True),
        device_map={'': 'cuda:0'}, attn_implementation='eager').eval()
    tokenizer = AutoTokenizer.from_pretrained(str(path), local_files_only=True, trust_remote_code=False)
    return generator, tokenizer


def coexist_smoke(runtime, question, documents, *, generator_bundle=None):
    """Serial inference beside the resident default generator, without app/DB state."""
    import torch

    generator, tokenizer = load_generator() if generator_bundle is None else generator_bundle
    torch.cuda.reset_peak_memory_stats()
    runtime.score(question, documents)
    prompt = tokenizer.apply_chat_template([{'role': 'user', 'content': question}], tokenize=False,
                                           add_generation_prompt=True, enable_thinking=False)
    inputs = tokenizer(prompt, return_tensors='pt').to('cuda')
    with torch.inference_mode():
        output = generator.generate(**inputs, max_new_tokens=16, do_sample=False)
    torch.cuda.synchronize()
    return {'generator': 'Qwen3.5-2B', 'quantization': 'NF4-double-quant-fp16',
            'prompt_tokens': inputs['input_ids'].shape[1], 'maximum_new_tokens': 16,
            'generated_tokens': output.shape[1] - inputs['input_ids'].shape[1],
            'rerank_candidates': len(documents), 'gpu_peak_reserved_mib': torch.cuda.max_memory_reserved() / 1024**2,
            'process_peak_mib': process_peak_mib(),
            'passed': (torch.cuda.max_memory_reserved() / 1024**2 <= GATES['maximum_coexist_gpu_mib']
                       and process_peak_mib() <= GATES['maximum_coexist_process_mib'])}


def measure(dataset, rows, config, *, repeats=2, expected_model=None):
    import gc
    import torch

    torch.set_num_threads(4)
    if config.device != 'cuda' or not torch.cuda.is_available():
        raise EvaluationError('Named hardware profile requires CUDA')
    hardware = {'profile': HARDWARE_PROFILE, 'gpu': torch.cuda.get_device_name(0),
                'gpu_total_mib': torch.cuda.get_device_properties(0).total_memory / 1024**2,
                'platform': platform.platform(), 'processor': platform.processor(),
                'cpu_threads': torch.get_num_threads(),
                'scope': 'reranker increments with idle NF4 Qwen3.5-2B resident; embedding vectors cached'}
    started = time.perf_counter()
    generator_bundle = load_generator()
    generator_load_seconds = time.perf_counter() - started
    gc.collect()
    torch.cuda.empty_cache()
    baseline = {'gpu_reserved_mib': torch.cuda.memory_reserved() / 1024**2,
                'gpu_allocated_mib': torch.cuda.memory_allocated() / 1024**2,
                'process_working_set_mib': process_peak_mib(peak=False), 'generator_load_seconds': generator_load_seconds}
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    runtime = QwenRerankerRuntime(config)
    if expected_model is not None and runtime.identity != expected_model:
        raise EvaluationError('Heldout model/runtime differs from frozen selection')
    torch.cuda.synchronize()
    cold_seconds = time.perf_counter() - started
    scores, timing = {}, []
    # Separate one warm-up from all measured query samples.
    warm = next(question for question in dataset.questions if len(rows[question['id']]) > 1)
    runtime.score(warm['question'], [lexical_text(hit) for hit in rows[warm['id']]])
    stress_documents = [lexical_text(rows[warm['id']][0]) * 1000] * CANDIDATE_LIMIT
    stress_tokens = runtime.inputs(warm['question'], stress_documents[:BATCH_SIZE])['input_ids'].shape[1]
    if stress_tokens != MAX_LENGTH:
        raise EvaluationError('Stress fixture does not exercise the full token bound')
    torch.cuda.synchronize()
    started = time.perf_counter()
    runtime.score(warm['question'], stress_documents)
    torch.cuda.synchronize()
    stress = {'candidates': CANDIDATE_LIMIT, 'tokens_per_pair': stress_tokens,
              'milliseconds': (time.perf_counter() - started) * 1000}
    for repeat in range(repeats):
        for question in dataset.questions:
            documents = [lexical_text(hit) for hit in rows[question['id']]]
            torch.cuda.synchronize()
            started = time.perf_counter()
            result = runtime.score(question['question'], documents) if len(documents) > 1 else [0.0] * len(documents)
            torch.cuda.synchronize()
            timing.append({'query_id': question['id'], 'repeat': repeat, 'candidates': len(documents),
                           'milliseconds': (time.perf_counter() - started) * 1000})
            if repeat == 0:
                scores[question['id']] = result
            print(f"{dataset.identity['split']} {repeat + 1}/{repeats} {question['id']}: {timing[-1]['milliseconds']:.1f} ms", flush=True)
    elapsed = sorted(row['milliseconds'] for row in timing)
    resources = {'cold_load_seconds': cold_seconds, 'warm_p50_ms': elapsed[math.ceil(len(elapsed) * .5) - 1],
                 'warm_p95_ms': elapsed[math.ceil(len(elapsed) * .95) - 1], 'samples': timing,
                 'gpu_peak_allocated_increment_mib': torch.cuda.max_memory_allocated() / 1024**2 - baseline['gpu_allocated_mib'],
                 'gpu_peak_reserved_increment_mib': torch.cuda.max_memory_reserved() / 1024**2 - baseline['gpu_reserved_mib'],
                 'gpu_absolute_peak_reserved_mib': torch.cuda.max_memory_reserved() / 1024**2,
                 'process_peak_increment_upper_bound_mib': max(0, process_peak_mib() - baseline['process_working_set_mib']),
                 'process_absolute_peak_mib': process_peak_mib(), 'baseline': baseline,
                 'hardware': hardware, 'full_bound_stress': stress}
    try:
        resources['coexistence'] = coexist_smoke(runtime, warm['question'], stress_documents, generator_bundle=generator_bundle)
    except Exception as error:
        resources['coexistence'] = {'passed': False, 'failure': type(error).__name__}
        print(f'Generator coexistence check failed: {error}', file=sys.stderr, flush=True)
    return scores, runtime.identity, resources


def resources_pass(resources):
    return (resources['warm_p95_ms'] <= GATES['maximum_warm_p95_ms']
            and resources['cold_load_seconds'] <= GATES['maximum_cold_load_seconds']
            and resources['gpu_peak_reserved_increment_mib'] <= GATES['maximum_gpu_peak_mib']
            and resources['process_peak_increment_upper_bound_mib'] <= GATES['maximum_process_increment_mib']
            and resources['coexistence']['passed'] is True
            and resources['hardware']['gpu'].removeprefix('NVIDIA ') == 'GeForce GTX 1050'
            and 3900 <= resources['hardware']['gpu_total_mib'] <= 4200)


def validate_selection(selection, profile):
    if (selection.get('passed') is not True or selection.get('policy') != policy()
            or selection.get('development_dataset') != load_dataset(split='development').identity
            or selection.get('embedding_profile_id') != profile.identity):
        raise EvaluationError('Frozen development policy, dataset or embedding profile differs')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('freeze', 'evaluate'))
    parser.add_argument('--split', choices=('development', 'heldout'), default='development')
    parser.add_argument('--vectors', type=Path, required=True)
    parser.add_argument('--model', type=Path, default=Path(__file__).parent / 'model/Qwen3-Reranker-0.6B')
    parser.add_argument('--selection', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args(argv)
    try:
        if options.command == 'freeze' and options.split != 'development':
            raise EvaluationError('Freeze only on development')
        dataset = load_dataset(split=options.split)
        profile, vectors = load_vectors(dataset, options.vectors)
        selection = None
        if options.command == 'evaluate':
            if options.selection is None:
                raise EvaluationError('Heldout requires frozen development --selection')
            selection = read_json(options.selection)
            validate_selection(selection, profile)
        rows = candidates(dataset, profile, vectors)
        from retrieval_eval_local import _forbidden_network
        with ExitStack() as guards:
            for name in ('create_connection', 'getaddrinfo'):
                guards.enter_context(patch.object(socket, name, _forbidden_network))
            for name in ('connect', 'connect_ex', 'sendto'):
                guards.enter_context(patch.object(socket.socket, name, _forbidden_network))
            scores, identity, resources = measure(dataset, rows, RerankerConfig(str(options.model.resolve())),
                                                  expected_model=selection['model'] if selection else None)
        baseline = ranked_report(dataset, profile, rows)
        after = ranked_report(dataset, profile, rows, scores)
        checks, by_source = quality(dataset, baseline, after)
        result = {'format_version': 1, 'policy': policy(), 'model': identity, 'baseline': baseline, 'reranked': after,
                  'resources': resources, 'quality_checks': checks, 'source_recall': by_source,
                  'resources_passed': resources_pass(resources), 'quality_passed': all(checks.values()),
                  'vectors_sha256': canonical_hash(read_json(options.vectors)), 'candidate_scores': scores,
                  'candidates': {identifier: [{'source_id': hit['id'], 'score': hit['score']} for hit in hits]
                                 for identifier, hits in rows.items()}}
        result['passed'] = result['resources_passed'] and result['quality_passed']
        if options.command == 'freeze':
            choices = select(dataset, profile, rows, scores)
            result.update(development_candidates=choices, development_dataset=dataset.identity,
                          embedding_profile_id=profile.identity,
                          selection_rule='passed, nDCG@6, recall@6, MRR@6 descending; weight, rank constant, method ascending; development only')
            if (choices[0]['weight'], choices[0]['method'], choices[0]['rank_constant']) != (MODEL_WEIGHT, 'rrf', RANK_CONSTANT):
                result['passed'] = False
                result['selection_error'] = 'Development choice differs from shipped weight'
        else:
            result['selection_sha256'] = canonical_hash(selection)
        write_json(options.output, result)
        print(json.dumps({key: result[key] for key in ('passed', 'quality_checks', 'resources_passed')}, ensure_ascii=False))
        return 0 if result['passed'] else 1
    except (EvaluationError, OSError, ValueError, KeyError) as error:
        print(f'Local reranker evaluation failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
