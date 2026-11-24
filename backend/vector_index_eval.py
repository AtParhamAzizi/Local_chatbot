"""Measure exact vector indexes without a database, model weights or network."""

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path

import numpy as np

from services.vector_index import (
    AUTO_INDEX_ROWS, INDEX_VERSION, MAX_CACHE_BYTES, MAX_INDEX_ROWS, ScalarIndex, VectorIndexCache,
)


REQUIREMENTS = {
    'corpus_sizes': [30, 1000, 10000, 50000],
    'dimension': 1024,
    'minimum_queries': 24,
    'recall_at_6': 1.0,
    'maximum_absolute_score_error': 0.000002,
    'maximum_warm_p95_ms': 150.0,
    'minimum_p95_speedup_at_index_threshold': 2.0,
    'maximum_rebuild_seconds': 5.0,
    'maximum_cached_vector_bytes': MAX_CACHE_BYTES,
}


def corpus(size, dimension, queries, seed):
    rng = np.random.default_rng(seed)
    centers = rng.standard_normal((min(size, 128), dimension), dtype=np.float32)
    matrix = rng.standard_normal((size, dimension), dtype=np.float32) * 0.25
    matrix += centers[np.arange(size) % len(centers)]
    matrix *= rng.uniform(0.5, 2, size=(size, 1)).astype(np.float32)
    if size >= 6:
        matrix[1:3] = matrix[0]
    query_matrix = rng.standard_normal((queries, dimension), dtype=np.float32)
    for position in range(queries):
        if position % 3:
            query_matrix[position] += centers[(position * 7) % len(centers)]
    records = tuple((identifier, vector.tobytes()) for identifier, vector in enumerate(matrix))
    digest = hashlib.sha256(matrix.tobytes() + query_matrix.tobytes()).hexdigest()
    return records, query_matrix, digest


def elapsed(call):
    start = time.perf_counter()
    result = call()
    return result, time.perf_counter() - start


def top(scores, limit=6):
    return sorted(scores, key=lambda identifier: (-scores[identifier], identifier))[:limit]


def timing(samples):
    return {'samples_ms': [sample * 1000 for sample in samples],
            'p50_ms': float(np.percentile(samples, 50) * 1000),
            'p95_ms': float(np.percentile(samples, 95) * 1000)}


def measure(size, *, dimension, queries, seed):
    records, query_matrix, digest = corpus(size, dimension, queries, seed)
    cache = VectorIndexCache()
    options = {'dimension': dimension, 'namespace': ('benchmark', dimension), 'mode': 'flat'}
    index, cold = elapsed(lambda: cache.get(records, **options))
    reference = ScalarIndex(records, dimension)
    recalls, errors, scalar_times, flat_times, snapshot_times = [], [], [], [], []
    for query in query_matrix:
        wanted, duration = elapsed(lambda: reference.scores(query))
        scalar_times.append(duration)
        observed, duration = elapsed(lambda: index.scores(query))
        flat_times.append(duration)
        fresh_records = tuple((identifier, memoryview(blob).tobytes()) for identifier, blob in records)
        checked, duration = elapsed(lambda: cache.get(fresh_records, **options).scores(query))
        snapshot_times.append(duration)
        recalls.append(len(set(top(wanted)) & set(top(checked))) / min(6, size))
        errors.append(max(abs(wanted[identifier] - observed[identifier]) for identifier in wanted))
    changed = list(records)
    for position in range(max(1, size // 100)):
        changed[position] = (changed[position][0], (-np.frombuffer(changed[position][1], dtype=np.float32)).tobytes())
    replacement, edit = elapsed(lambda: cache.get(changed, **options))
    deleted, deletion = elapsed(lambda: cache.get(changed[:-1], **options))
    cache.clear()
    restarted, restart = elapsed(lambda: cache.get(records, **options))
    scalar_summary, flat_summary, snapshot_summary = map(timing, (scalar_times, flat_times, snapshot_times))
    speedup = scalar_summary['p95_ms'] / snapshot_summary['p95_ms']
    rebuilds = {'cold': cold, 'one_percent_edit': edit, 'deletion': deletion, 'restart': restart}
    gates = {
        'recall': min(recalls) >= REQUIREMENTS['recall_at_6'],
        'score_error': max(errors) <= REQUIREMENTS['maximum_absolute_score_error'],
        'latency': snapshot_summary['p95_ms'] <= REQUIREMENTS['maximum_warm_p95_ms'],
        'speedup': size < AUTO_INDEX_ROWS or speedup >= REQUIREMENTS['minimum_p95_speedup_at_index_threshold'],
        'rebuild': max(rebuilds.values()) <= REQUIREMENTS['maximum_rebuild_seconds'],
        'cache_bytes': index.resident_bytes <= MAX_CACHE_BYTES,
        'indexed': all(item.backend == 'flat' for item in (index, replacement, deleted, restarted)),
    }
    return {'rows': size, 'dimension': dimension, 'queries': queries, 'seed': seed, 'corpus_sha256': digest,
            'scalar': scalar_summary, 'flat_kernel': flat_summary, 'flat_with_fresh_snapshot': snapshot_summary,
            'p95_speedup': speedup, 'minimum_recall_at_6': min(recalls), 'maximum_score_error': max(errors),
            'rebuild_seconds': rebuilds, 'cached_vector_bytes': index.resident_bytes,
            'gates': gates, 'passed': all(gates.values())}


def quality_replay(directory):
    from hybrid_eval import load_vectors
    from retrieval_eval import canonical_hash, load_dataset, read_json
    from services.lexical_index import LexicalIndexCache
    from services.source_fusion import fuse
    from source_fusion_eval import policy, source_metrics

    splits = {}
    for split in ('development', 'heldout'):
        dataset = load_dataset(split=split)
        vector_path = directory / f'{split}-vectors.json'
        profile, vectors = load_vectors(dataset, vector_path)
        historical_path = Path(__file__).parent.parent / 'docs/evaluations' / f'f039-{split}.json'
        historical = read_json(historical_path)
        if historical['fusion']['run']['dataset'] != dataset.identity:
            raise ValueError('Historical baseline belongs to another dataset')
        records = [(source['id'], vectors['documents'][source['id']].tobytes()) for source in dataset.sources]
        cache = VectorIndexCache()
        scalar = ScalarIndex(records, profile.dimension)
        flat = cache.get(records, dimension=profile.dimension, namespace=profile.identity, mode='flat')
        lexical_cache = LexicalIndexCache()
        rankings, errors, identical = {}, [], True
        for question in dataset.questions:
            query = vectors['queries'][question['id']]
            reference, observed = scalar.scores(query), flat.scores(query)
            errors.append(max(abs(reference[identifier] - observed[identifier]) for identifier in reference))
            by_mode = {}
            for mode, semantic in (('scalar', reference), ('flat', observed)):
                hits = [{'id': source['id'], 'source_type': source['kind'], 'semantic_score': semantic[source['id']],
                         'question': source.get('question'), 'answer': source['text'], 'content': source['text']}
                        for source in dataset.sources]
                by_mode[mode] = [{'source_id': item['id'], 'score': item['score']}
                                 for item in fuse(hits, question['question'], profile=profile, cache=lexical_cache)]
            rankings[question['id']] = by_mode['flat']
            expected = historical['fusion']['run']['rankings'][question['id']]
            identical &= ([item['source_id'] for item in by_mode['flat']]
                          == [item['source_id'] for item in by_mode['scalar']]
                          == [item['source_id'] for item in expected])
        splits[split] = {'dataset': dataset.identity, 'embedding_profile_id': profile.identity,
                         'embedding_profile': profile.to_json(), 'vectors_sha256': canonical_hash(read_json(vector_path)),
                         'historical_baseline_sha256': canonical_hash(historical), 'rankings': rankings,
                         'source_recall': source_metrics(dataset, rankings), 'identical_rankings': identical,
                         'maximum_score_error': max(errors),
                         'passed': identical and max(errors) <= REQUIREMENTS['maximum_absolute_score_error']}
    return {'fusion_policy': policy(), 'splits': splits, 'passed': all(item['passed'] for item in splits.values())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes', type=int, nargs='+', default=REQUIREMENTS['corpus_sizes'])
    parser.add_argument('--dimension', type=int, default=REQUIREMENTS['dimension'])
    parser.add_argument('--queries', type=int, default=REQUIREMENTS['minimum_queries'])
    parser.add_argument('--seed', type=int, default=41041)
    parser.add_argument('--quality-vectors', type=Path, help='Directory with independently prepared F038 split vectors')
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args(argv)
    if (len(set(options.sizes)) != len(options.sizes) or any(size < 6 or size > MAX_INDEX_ROWS for size in options.sizes)
            or not 1 <= options.dimension <= 1024 or not 1 <= options.queries <= 1000 or options.seed < 0):
        parser.error('Use unique sizes 6..50000, dimension 1..1024, queries 1..1000 and a nonnegative seed')
    results = []
    for size in options.sizes:
        result = measure(size, dimension=options.dimension, queries=options.queries, seed=options.seed)
        results.append(result)
        print(f"{size} rows: recall@6={result['minimum_recall_at_6']:.6f}, "
              f"p95={result['flat_with_fresh_snapshot']['p95_ms']:.2f}ms, "
              f"speedup={result['p95_speedup']:.2f}x, passed={result['passed']}", flush=True)
    complete = (sorted(options.sizes) == REQUIREMENTS['corpus_sizes']
                and options.dimension == REQUIREMENTS['dimension']
                and options.queries >= REQUIREMENTS['minimum_queries'])
    root = Path(__file__).parent
    report = {'format_version': 1, 'index_version': INDEX_VERSION, 'requirements': REQUIREMENTS,
              'environment': {'python': platform.python_version(), 'numpy': np.__version__,
                              'platform': platform.platform(), 'processor': platform.processor(), 'cpus': os.cpu_count()},
              'source_sha256': {name: hashlib.sha256((root / name).read_text(encoding='utf-8').encode('utf-8')).hexdigest()
                                for name in ('services/vector_index.py', 'vector_index_eval.py', 'searchForAnswer.py')},
              'scope': 'Single-request vector scoring and exact fresh-byte snapshot comparison; no SQL, profile filtering, '
                       'text/lexical fusion, embedding, generation or HTTP; clustered synthetic vectors, no relevance labels.',
              'complete_envelope': complete, 'results': results,
              'passed': complete and all(result['passed'] for result in results)}
    if options.quality_vectors is not None:
        report['quality'] = quality_replay(options.quality_vectors)
        report['passed'] &= report['quality']['passed']
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
