"""Explicit local-model evaluation of the current QA/document scoring policy."""

import math
import socket
from contextlib import ExitStack
from unittest.mock import patch

from persian_text import NORMALIZATION_VERSION, normalize_persian, persian_terms
from retrieval_eval import CUTOFFS, EvaluationError, indexed_text


def _forbidden_network(*args, **kwargs):
    raise EvaluationError('Local retrieval evaluation cannot access networks or databases')


def profiled_run(dataset, runtime):
    import numpy as np
    from embedding_profile import checked_batch

    profile = runtime.profile
    if profile.normalization_version != NORMALIZATION_VERSION:
        raise EvaluationError('Local model uses another normalization policy')
    vectors = []
    for start in range(0, len(dataset.sources), 8):
        sources = dataset.sources[start:start + 8]
        batch, captured = checked_batch(runtime.embed([normalize_persian(indexed_text(source)) for source in sources]), rows=len(sources))
        if captured != profile:
            raise EvaluationError('Embedding profile changed during source preparation')
        vectors.extend(batch)
    rankings = {}
    for question in dataset.questions:
        query = normalize_persian(question['question'])
        query_vectors, captured = checked_batch(runtime.embed([query]), rows=1)
        if captured != profile:
            raise EvaluationError('Embedding profile changed during query evaluation')
        query_vector = query_vectors[0]
        terms = persian_terms(query)
        qa_hits, document_hits = [], []
        for source, vector in zip(dataset.sources, vectors):
            score = float(np.dot(query_vector, vector) / (float(np.linalg.norm(query_vector)) * float(np.linalg.norm(vector))))
            if not math.isfinite(score):
                raise EvaluationError('Nonfinite cosine score')
            if source['kind'] != 'qa':
                overlap = len(terms & persian_terms(source['text'])) / len(terms) if terms else 0.0
                score = 0.82 * score + 0.18 * overlap
            (qa_hits if source['kind'] == 'qa' else document_hits).append({'source_id': source['id'], 'score': score})
        qa_hits.sort(key=lambda hit: (-hit['score'], hit['source_id']))
        document_hits.sort(key=lambda hit: (-hit['score'], hit['source_id']))
        selected = [hit for hit in qa_hits[:5] + document_hits[:8] if hit['score'] >= 0.35]
        selected.sort(key=lambda hit: (-hit['score'], hit['source_id']))
        rankings[question['id']] = selected[:max(CUTOFFS)]
    return {'format_version': 1, 'dataset': dataset.identity,
            'system': {'name': 'current-scoring-local-model', 'version': '1', 'normalization': NORMALIZATION_VERSION,
                       'embedding_profile': profile.to_json(), 'embedding_profile_id': profile.identity,
                       'qa_candidates': 5, 'document_candidates': 8, 'minimum_score': 0.35,
                       'document_semantic_weight': 0.82, 'document_lexical_weight': 0.18,
                       'limit': max(CUTOFFS), 'tie_break': 'source-id-ascending'}, 'rankings': rankings}


def local_run(dataset, model_id, model_path):
    if not (model_path / 'config.json').is_file():
        raise EvaluationError('Local model path must contain config.json and complete immutable model artifacts')
    with ExitStack() as guards:
        for name in ('create_connection', 'getaddrinfo'):
            guards.enter_context(patch.object(socket, name, _forbidden_network))
        for name in ('connect', 'connect_ex', 'sendto'):
            guards.enter_context(patch.object(socket.socket, name, _forbidden_network))
        from interface import load_embedding_runtime
        runtime = load_embedding_runtime(model_id, str(model_path.resolve()))
        return profiled_run(dataset, runtime)
