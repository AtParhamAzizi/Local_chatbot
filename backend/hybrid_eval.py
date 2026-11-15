"""Development-only lexical selection, frozen heldout evaluation and latency replay."""

import argparse
import json
import math
import platform
import socket
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from persian_text import NORMALIZATION_VERSION, normalize_persian, persian_terms
from retrieval_eval import DATASET_DIR, EvaluationError, canonical_hash, evaluate_run, indexed_text, load_dataset, read_json
from services.lexical_index import LexicalIndexCache, TOKENIZER_VERSION, hybrid_score


def lexical_source_hash():
    source = Path(__file__).with_name('services') / 'lexical_index.py'
    return canonical_hash(source.read_text(encoding='utf-8'))


def prepare(dataset, model_path, output):
    import torch
    from embedding_profile import checked_batch
    from retrieval_eval_local import _forbidden_network

    torch.set_num_threads(4)
    with ExitStack() as guards:
        for name in ('create_connection', 'getaddrinfo'):
            guards.enter_context(patch.object(socket, name, _forbidden_network))
        for name in ('connect', 'connect_ex', 'sendto'):
            guards.enter_context(patch.object(socket.socket, name, _forbidden_network))
        from interface import load_embedding_runtime
        runtime = load_embedding_runtime('qwen3.5-2b', str(model_path.resolve()))
        result = {'format_version': 1, 'dataset': dataset.identity, 'profile': runtime.profile.to_json(),
                  'threads': torch.get_num_threads(), 'documents': {}, 'queries': {}}
        for label, records in (('documents', dataset.sources), ('queries', dataset.questions)):
            for start in range(0, len(records), 8):
                batch = records[start:start + 8]
                texts = [indexed_text(item) if label == 'documents' else item['question'] for item in batch]
                vectors, profile = checked_batch(runtime.embed([normalize_persian(value) for value in texts]), rows=len(texts))
                if profile != runtime.profile:
                    raise EvaluationError('Embedding profile changed during evaluation preparation')
                result[label].update({item['id']: vector.tolist() for item, vector in zip(batch, vectors)})
                print(f'{label}: {min(start + 8, len(records))}/{len(records)}', flush=True)
        write_json(output, result)


def load_vectors(dataset, path):
    import numpy as np
    from embedding_profile import EmbeddingBatch, EmbeddingProfile, checked_batch

    artifact = read_json(path)
    if artifact.get('format_version') != 1 or artifact.get('dataset') != dataset.identity:
        raise EvaluationError('Embedding cache belongs to another dataset/split')
    profile = EmbeddingProfile.from_json(artifact['profile'])
    if profile.normalization_version != NORMALIZATION_VERSION:
        raise EvaluationError('Embedding cache uses another normalization policy')
    prepared = {}
    for label, records in (('documents', dataset.sources), ('queries', dataset.questions)):
        if set(artifact[label]) != {item['id'] for item in records}:
            raise EvaluationError('Embedding cache has missing/extra sources or queries')
        checked_batch(EmbeddingBatch(np.array([artifact[label][item['id']] for item in records]), profile), rows=len(records))
        prepared[label] = {identifier: np.array(vector, dtype=np.float32) for identifier, vector in artifact[label].items()}
    return profile, prepared


def rank(dataset, vectors, query, *, policy=None, cache=None):
    import numpy as np

    query_vector = vectors['queries'][query['id']]
    query_norm = float(np.linalg.norm(query_vector))
    terms = persian_terms(query['question'])
    lexical_scores = {}
    if policy is not None:
        for kind in ('qa', 'document'):
            documents = [(source['id'], source['question'] + '\n' + source['text'] if kind == 'qa' else source['text'])
                         for source in dataset.sources if (source['kind'] == 'qa') == (kind == 'qa')]
            lexical_scores.update(cache.get(documents, namespace=kind, mode=policy['mode']).scores(query['question']))
    qa_hits, document_hits = [], []
    for source in dataset.sources:
        vector = vectors['documents'][source['id']]
        semantic = float(np.dot(query_vector, vector) / (query_norm * float(np.linalg.norm(vector))))
        if policy is not None:
            score = hybrid_score(semantic, lexical_scores.get(source['id'], 0), weight=policy['weight'], scale=policy['scale'])
        elif source['kind'] == 'qa':
            score = semantic
        else:
            overlap = len(terms & persian_terms(source['text'])) / len(terms) if terms else 0
            score = 0.82 * semantic + 0.18 * overlap
        (qa_hits if source['kind'] == 'qa' else document_hits).append({'source_id': source['id'], 'score': score})
    qa_hits.sort(key=lambda hit: (-hit['score'], hit['source_id']))
    document_hits.sort(key=lambda hit: (-hit['score'], hit['source_id']))
    combined = [hit for hit in qa_hits[:5] + document_hits[:8] if hit['score'] >= 0.35]
    combined.sort(key=lambda hit: (-hit['score'], hit['source_id']))
    return combined[:6]


def report(dataset, profile, vectors, policy=None):
    cache = LexicalIndexCache()
    run = {'format_version': 1, 'dataset': dataset.identity,
           'system': {'name': 'indexed-bm25-hybrid' if policy else 'pre-f038-cosine-pdf-overlap',
                      'version': '1', 'normalization': NORMALIZATION_VERSION,
                      'embedding_profile': profile.to_json(), 'embedding_profile_id': profile.identity,
                      'policy': policy, 'tokenizer_version': TOKENIZER_VERSION if policy else None,
                      'qa_lexical_text': 'question-and-answer' if policy else None,
                      'qa_candidates': 5, 'document_candidates': 8, 'minimum_score': 0.35,
                      'limit': 6, 'tie_break': 'source-id-ascending'},
           'rankings': {question['id']: rank(dataset, vectors, question, policy=policy, cache=cache)
                        for question in dataset.questions}}
    return evaluate_run(dataset, run)


def select(dataset, profile, vectors):
    if dataset.identity['split'] != 'development':
        raise EvaluationError('Policy selection is permitted only on development')
    baseline = report(dataset, profile, vectors)
    candidates = []
    for mode in ('words', 'filtered-words', 'character-3'):
        for weight in (0.1, 0.2, 0.3, 0.4):
            for scale in (1.0, 4.0, 8.0):
                policy = {'mode': mode, 'weight': weight, 'scale': scale}
                result = report(dataset, profile, vectors, policy)
                candidates.append({'policy': policy, 'summary': result['summary']})
    candidates.sort(key=lambda item: (-item['summary']['ndcg_at_6'], -item['summary']['recall']['6'],
                                       -item['summary']['mrr_at_6'], item['summary']['unanswerable_false_positive_rate'],
                                       item['policy']['weight'], item['policy']['scale'], item['policy']['mode']))
    return {'format_version': 1, 'development_dataset': dataset.identity,
            'embedding_profile': profile.to_json(), 'embedding_profile_id': profile.identity,
            'implementation': {'tokenizer_version': TOKENIZER_VERSION, 'bm25_k1': 1.2, 'bm25_b': 0.75,
                               'qa_lexical_text': 'question-and-answer', 'document_lexical_text': 'content',
                               'lexical_normalization': 'bm25/(bm25+scale)',
                               'lexical_source_sha256': lexical_source_hash()},
            'selection_rule': 'max nDCG@6, recall@6, MRR@6; min unanswerable false positives, weight, scale, mode',
            'baseline_summary': baseline['summary'], 'selected': candidates[0], 'candidates': candidates,
            'acceptance': {'minimum_ndcg_gain': 0.01, 'recall_may_regress': False, 'mrr_may_regress': False,
                           'unanswerable_false_positives_may_increase': False,
                           'warm_p95_limit': 'max(legacy_p95 * 1.25, legacy_p95 + 5ms)',
                           'cold_limit': 'max(legacy_p95 * 2, 500ms)', 'scales': [30, 3000]}}


def latency(dataset, vectors, policy, *, scales=(30, 3000), repetitions=3):
    measurements = []
    for size in scales:
        sources, documents = [], {}
        for index in range(size):
            original = dataset.sources[index % len(dataset.sources)]
            identifier = original['id'] + '-' + str(index).zfill(6)
            sources.append({**original, 'id': identifier})
            documents[identifier] = vectors['documents'][original['id']]
        scaled = SimpleNamespace(sources=tuple(sources))
        prepared = {**vectors, 'documents': documents}
        row = {'sources': size, 'samples_per_strategy': len(dataset.questions) * repetitions}
        for name, strategy in (('legacy', None), ('hybrid', policy)):
            cache = LexicalIndexCache()
            started = time.perf_counter()
            rank(scaled, prepared, dataset.questions[0], policy=strategy, cache=cache)
            cold = (time.perf_counter() - started) * 1000
            samples = []
            for _ in range(repetitions):
                for question in dataset.questions:
                    started = time.perf_counter()
                    rank(scaled, prepared, question, policy=strategy, cache=cache)
                    samples.append((time.perf_counter() - started) * 1000)
            samples.sort()
            row[name] = {'cold_ms': round(cold, 4), 'p50_ms': round(samples[len(samples) // 2], 4),
                         'p95_ms': round(samples[math.ceil(0.95 * len(samples)) - 1], 4)}
        row['warm_p95_budget_ms'] = max(row['legacy']['p95_ms'] * 1.25, row['legacy']['p95_ms'] + 5)
        row['cold_budget_ms'] = max(row['legacy']['p95_ms'] * 2, 500)
        row['passed'] = (row['hybrid']['p95_ms'] <= row['warm_p95_budget_ms']
                         and row['hybrid']['cold_ms'] <= row['cold_budget_ms'])
        measurements.append(row)
    return {'platform': platform.platform(), 'python': platform.python_version(), 'measurements': measurements,
            'scope': 'in-process scoring and indexing; cached real vectors; excludes model inference and database I/O; duplicated source text at scale'}


def evaluate(dataset, profile, vectors, selection, *, measure_latency=True):
    development = load_dataset(split='development')
    if (selection.get('format_version') != 1 or selection.get('development_dataset') != development.identity
            or selection.get('embedding_profile_id') != profile.identity):
        raise EvaluationError('Frozen development selection has an incompatible dataset/model')
    if selection.get('implementation', {}).get('lexical_source_sha256') != lexical_source_hash():
        raise EvaluationError('Lexical implementation differs from the frozen development selection')
    policy = selection['selected']['policy']
    baseline, candidate = report(dataset, profile, vectors), report(dataset, profile, vectors, policy)
    before, after = baseline['summary'], candidate['summary']
    quality_passed = (after['ndcg_at_6'] >= before['ndcg_at_6'] + 0.01
                      and after['recall']['6'] >= before['recall']['6'] and after['mrr_at_6'] >= before['mrr_at_6']
                      and after['unanswerable_false_positive_rate'] <= before['unanswerable_false_positive_rate'])
    timing = latency(dataset, vectors, policy) if measure_latency else None
    return {'format_version': 1, 'selection_sha256': canonical_hash(selection), 'policy': policy,
            'baseline': baseline, 'hybrid': candidate, 'quality_passed': quality_passed, 'latency': timing,
            'passed': quality_passed and timing is not None and all(row['passed'] for row in timing['measurements'])}


def write_json(path, result):
    if path.resolve().is_relative_to(DATASET_DIR.resolve()):
        raise EvaluationError('Do not overwrite frozen F037 dataset artifacts')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'select', 'evaluate'))
    parser.add_argument('--split', choices=('development', 'heldout'), default='development')
    parser.add_argument('--model-path', type=Path)
    parser.add_argument('--vectors', type=Path)
    parser.add_argument('--selection', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args(argv)
    try:
        if options.command == 'select' and options.split != 'development':
            raise EvaluationError('Never select a policy on heldout questions')
        dataset = load_dataset(split=options.split)
        if options.command == 'prepare':
            if options.model_path is None:
                raise EvaluationError('prepare requires --model-path')
            prepare(dataset, options.model_path, options.output)
        else:
            if options.vectors is None:
                raise EvaluationError('A profiled --vectors artifact is required')
            profile, vectors = load_vectors(dataset, options.vectors)
            if options.command == 'select':
                result = select(dataset, profile, vectors)
                result['vectors_sha256'] = canonical_hash(read_json(options.vectors))
                print(json.dumps(result['selected'], ensure_ascii=False), flush=True)
            else:
                if options.selection is None:
                    raise EvaluationError('evaluate requires the frozen development --selection')
                result = evaluate(dataset, profile, vectors, read_json(options.selection))
                result['vectors_sha256'] = canonical_hash(read_json(options.vectors))
                print(json.dumps({'quality_passed': result['quality_passed'], 'passed': result['passed'],
                                  'baseline': result['baseline']['summary'], 'hybrid': result['hybrid']['summary'],
                                  'latency': result['latency']}, ensure_ascii=False), flush=True)
            write_json(options.output, result)
            if options.command == 'evaluate' and not result['passed']:
                return 1
        return 0
    except (EvaluationError, OSError, ValueError, KeyError) as error:
        print(f'Hybrid evaluation failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
