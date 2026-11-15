"""Freeze and replay mixed-source fusion against the unchanged F038 baseline."""

import argparse
import json
import sys
from pathlib import Path

from hybrid_eval import load_vectors, report as hybrid_report, write_json
from persian_text import NORMALIZATION_VERSION
from retrieval_eval import EvaluationError, canonical_hash, evaluate_run, load_dataset, read_json
from services.lexical_index import LexicalIndexCache
from services.source_fusion import LEXICAL_MODE, LEXICAL_SCALE, LEXICAL_WEIGHT, POLICY_VERSION, fuse


def policy():
    root = Path(__file__).parent
    return {'version': POLICY_VERSION, 'lexical_weight': LEXICAL_WEIGHT, 'lexical_mode': LEXICAL_MODE,
            'lexical_scale': LEXICAL_SCALE, 'minimum_score': 0.35, 'limit': 6,
            'lexical_statistics': 'all-compatible-sources', 'candidate_limits': None,
            'ties': 'normalized-text-sha256, source-identity-sha256',
            'source_sha256': {name: canonical_hash((root / name).read_text(encoding='utf-8'))
                              for name in ('services/source_fusion.py', 'services/lexical_index.py',
                                           'services/hybrid_search.py', 'services/retrieval.py', 'searchForAnswer.py',
                                           'source_fusion_eval.py')}}


def rank(dataset, profile, vectors, question, *, cache, calibration=None):
    import numpy as np

    query = vectors['queries'][question['id']]
    query_norm = float(np.linalg.norm(query))
    hits = []
    for source in dataset.sources:
        vector = vectors['documents'][source['id']]
        semantic = float(np.dot(query, vector) / (query_norm * float(np.linalg.norm(vector))))
        hits.append({'id': source['id'], 'source_type': source['kind'], 'semantic_score': semantic,
                     'question': source.get('question'), 'answer': source['text'], 'content': source['text']})
    return [{'source_id': hit['id'], 'score': hit['score']}
            for hit in fuse(hits, question['question'], profile=profile, cache=cache, **(calibration or {}))]


def source_metrics(dataset, rankings):
    """Recall of relevant evidence by its actual kind, without cross-kind credit."""
    catalog = {source['id']: source for source in dataset.sources}
    metrics = {}
    for kind in ('qa', 'pdf', 'document'):
        relevant = [(question, {item['source_id'] for item in question['relevance']
                                if item['grade'] > 0 and catalog[item['source_id']]['kind'] == kind})
                    for question in dataset.questions]
        relevant = [(question, identifiers) for question, identifiers in relevant if identifiers]
        metrics[kind] = {'answerable_questions': len(relevant),
                         'recall': {str(k): sum(len(identifiers & {hit['source_id'] for hit in rankings[question['id']][:k]})
                                               / len(identifiers) for question, identifiers in relevant) / len(relevant)
                                    if relevant else None for k in (1, 3, 6)}}
    return metrics


def report(dataset, profile, vectors, calibration=None):
    cache = LexicalIndexCache()
    selected_policy = policy()
    if calibration is not None:
        selected_policy.update(lexical_weight=calibration['weight'], lexical_scale=calibration['scale'])
    run = {'format_version': 1, 'dataset': dataset.identity,
           'system': {'name': POLICY_VERSION, 'version': '1', 'normalization': NORMALIZATION_VERSION, 'policy': selected_policy,
                      'embedding_profile': profile.to_json(), 'embedding_profile_id': profile.identity},
           'rankings': {question['id']: rank(dataset, profile, vectors, question, cache=cache, calibration=calibration)
                        for question in dataset.questions}}
    return evaluate_run(dataset, run)


def compare(dataset, profile, vectors, calibration=None):
    before = hybrid_report(dataset, profile, vectors,
                           {'mode': 'words', 'weight': 0.2, 'scale': 8.0})
    after = report(dataset, profile, vectors, calibration)
    before_sources = source_metrics(dataset, before['run']['rankings'])
    after_sources = source_metrics(dataset, after['run']['rankings'])
    # Explicit gates, fixed before heldout access. No enforced source quotas.
    quality = (after['summary']['ndcg_at_6'] >= before['summary']['ndcg_at_6'] - 0.01
               and after['summary']['recall']['6'] >= before['summary']['recall']['6']
               and after['summary']['mrr_at_6'] >= before['summary']['mrr_at_6'] - 0.005
               and after['summary']['unanswerable_false_positive_rate'] <= before['summary']['unanswerable_false_positive_rate'])
    balanced = all(after_sources[kind]['recall']['6'] >= before_sources[kind]['recall']['6']
                   for kind in before_sources if before_sources[kind]['answerable_questions'])
    return {'format_version': 1, 'policy': policy(), 'baseline': before, 'fusion': after,
            'acceptance': {'maximum_ndcg_loss': 0.01, 'maximum_mrr_loss': 0.005,
                           'recall_at_6_may_regress': False, 'per_kind_recall_at_6_may_regress': False,
                           'unanswerable_false_positives_may_increase': False},
            'source_recall': {'baseline': before_sources, 'fusion': after_sources},
            'quality_passed': quality, 'source_balance_passed': balanced, 'passed': quality and balanced}


def select(dataset, profile, vectors):
    if dataset.identity['split'] != 'development':
        raise EvaluationError('Select only on development questions')
    candidates = []
    for scale in (1, 2, 4, 8, 16, 32):
        for weight in (0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5):
            result = compare(dataset, profile, vectors, {'weight': weight, 'scale': scale})
            candidates.append({'weight': weight, 'scale': scale, 'summary': result['fusion']['summary'],
                               'source_recall': result['source_recall']['fusion'],
                               'passed': result['passed'], 'source_balance_passed': result['source_balance_passed']})
    candidates.sort(key=lambda item: (-item['passed'], -item['source_balance_passed'], -item['summary']['ndcg_at_6'],
                                       -item['summary']['recall']['6'], -item['summary']['mrr_at_6'],
                                       item['weight'], item['scale']))
    chosen = candidates[0]
    if (chosen['weight'], chosen['scale']) != (LEXICAL_WEIGHT, LEXICAL_SCALE):
        raise EvaluationError('Development selection differs from the shipped calibration')
    return candidates


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('freeze', 'evaluate'))
    parser.add_argument('--split', choices=('development', 'heldout'), default='development')
    parser.add_argument('--vectors', type=Path, required=True)
    parser.add_argument('--selection', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args(argv)
    try:
        if options.command == 'freeze' and options.split != 'development':
            raise EvaluationError('Freeze only on development questions')
        dataset = load_dataset(split=options.split)
        profile, vectors = load_vectors(dataset, options.vectors)
        if options.command == 'evaluate':
            if options.selection is None:
                raise EvaluationError('evaluate requires the frozen development --selection')
            selection = read_json(options.selection)
            if (selection.get('format_version') != 1 or selection.get('passed') is not True
                    or selection.get('policy') != policy()
                    or selection.get('development_dataset') != load_dataset(split='development').identity
                    or selection.get('embedding_profile_id') != profile.identity):
                raise EvaluationError('Frozen fusion implementation/dataset/profile differs')
        result = compare(dataset, profile, vectors)
        result['vectors_sha256'] = canonical_hash(read_json(options.vectors))
        if options.command == 'freeze':
            result['development_candidates'] = select(dataset, profile, vectors)
            result['selection_rule'] = ('passed, source balance, nDCG@6, recall@6, MRR@6 descending; '
                                        'lexical weight, saturation scale ascending; development only')
            result['development_dataset'] = dataset.identity
            result['embedding_profile_id'] = profile.identity
        else:
            result['selection_sha256'] = canonical_hash(selection)
        write_json(options.output, result)
        print(json.dumps({key: result[key] for key in ('quality_passed', 'source_balance_passed', 'source_recall')},
                         ensure_ascii=False))
        return 0 if result['passed'] else 1
    except (EvaluationError, OSError, ValueError, KeyError) as error:
        print(f'Mixed-source evaluation failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
