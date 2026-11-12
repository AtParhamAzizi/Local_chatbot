"""Versioned Persian retrieval evaluation without application startup or a database."""

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from persian_text import NORMALIZATION_VERSION, normalize_persian, persian_terms


DATASET_DIR = Path(__file__).resolve().parents[1] / 'evaluation' / 'persian-retrieval' / 'v1'
CUTOFFS = (1, 3, 6)
METRIC_VERSION = 'persian-retrieval-metrics-v1'
TAGS = {'direct', 'orthographic', 'terminology', 'near_match', 'unanswerable', 'multi_source'}


class EvaluationError(ValueError):
    pass


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _require(condition, message):
    if not condition:
        raise EvaluationError(message)


def _object(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value, f'Duplicate JSON key: {key}')
        value[key] = item
    return value


def read_json(path):
    try:
        _require(path.stat().st_size <= 20 * 1024 * 1024, 'Evaluation file exceeds 20 MiB')
        return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_object,
                          parse_constant=lambda value: (_require(False, f'Nonfinite JSON value: {value}')))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EvaluationError(f'Cannot read evaluation JSON: {path.name}') from error


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9-]{1,79}', value) is not None


@dataclass(frozen=True)
class Dataset:
    directory: Path
    manifest: dict
    sources: tuple
    questions: tuple
    identity: dict


def load_dataset(directory=DATASET_DIR, split='development'):
    _require(split in {'development', 'heldout'}, 'Select development or heldout explicitly')
    directory = Path(directory)
    manifest = read_json(directory / 'manifest.json')
    _require(isinstance(manifest, dict) and type(manifest.get('format_version')) is int
             and manifest['format_version'] == 1, 'Unsupported dataset manifest')
    _require(manifest.get('dataset_id') == 'persian-retrieval' and _text(manifest.get('version'))
             and re.fullmatch(r'\d+\.\d+\.\d+', manifest['version']),
             'Unsupported dataset identity')
    review = manifest.get('review', {})
    _require(isinstance(review, dict) and review.get('status') in {'agent-reviewed', 'human-reviewed'}
             and _text(review.get('reviewer')) and _text(review.get('method')), 'Dataset review record is missing')
    specifications = manifest.get('splits', {})
    _require(isinstance(specifications, dict) and set(specifications) == {'development', 'heldout'},
             'Both split specifications are required')
    for name, specification in specifications.items():
        _require(isinstance(specification, dict) and isinstance(specification.get('families'), list)
                 and specification['families'] and all(_identifier(family) for family in specification['families'])
                 and len(set(specification['families'])) == len(specification['families']), f'Invalid {name} families')
    _require(not set(specifications['development']['families']) & set(specifications['heldout']['families']),
             'Development and heldout families overlap')
    sources = read_json(directory / 'corpus.json')
    questions = read_json(directory / (split + '.json'))
    _require(canonical_hash(sources) == manifest.get('corpus_sha256'), 'Corpus checksum mismatch; version the dataset')
    _require(canonical_hash(questions) == specifications[split].get('sha256'), 'Question checksum mismatch; version the dataset')
    _require(isinstance(sources, list) and sources and len(sources) == manifest.get('source_count'), 'Invalid source inventory')
    catalog = {}
    families = set(specifications['development']['families'] + specifications['heldout']['families'])
    for source in sources:
        _require(isinstance(source, dict) and _identifier(source.get('id')), 'Invalid source ID')
        _require(source['id'] not in catalog, 'Duplicate source ID')
        _require(source.get('family') in families and source.get('kind') in {'qa', 'pdf', 'document'}, 'Invalid source family/kind')
        _require(all(_text(source.get(field)) for field in ('title', 'filename', 'text')), 'Missing source text/provenance')
        location = source.get('location')
        _require(isinstance(location, dict), 'Missing source location')
        expected = {'qa': {'record'}, 'pdf': {'page'}, 'document': {'line_start', 'line_end'}}[source['kind']]
        _require(set(location) == expected and all(type(value) is int and value > 0 for value in location.values()),
                 'Invalid source coordinates')
        if source['kind'] == 'document':
            _require(location['line_end'] >= location['line_start'], 'Reversed source lines')
        if source['kind'] == 'qa':
            _require(_text(source.get('question')), 'Q&A source needs its indexed question')
        catalog[source['id']] = source
    _require(isinstance(questions, list) and questions and len(questions) == specifications[split].get('question_count'),
             'Invalid question inventory')
    identifiers, normalized_questions, observed_families, observed_tags = set(), set(), set(), set()
    for question in questions:
        _require(isinstance(question, dict) and _identifier(question.get('id')), 'Invalid question ID')
        _require(question['id'] not in identifiers, 'Duplicate question ID')
        identifiers.add(question['id'])
        _require(question.get('family') in specifications[split]['families'], 'Question family leaks across splits')
        observed_families.add(question['family'])
        _require(_text(question.get('question')) and re.search('[آ-ی]', question['question']), 'Expected a Persian question')
        normalized = normalize_persian(question['question']).casefold()
        _require(normalized not in normalized_questions, 'Duplicate normalized question')
        normalized_questions.add(normalized)
        tags = question.get('tags')
        _require(isinstance(tags, list) and tags and set(tags) <= TAGS and len(set(tags)) == len(tags), 'Invalid question tags')
        observed_tags.update(tags)
        _require(type(question.get('answerable')) is bool and _text(question.get('review_note')), 'Missing answerability/review')
        relevance = question.get('relevance')
        _require(isinstance(relevance, list) and bool(relevance) == question['answerable'], 'Answerability disagrees with labels')
        _require((_text(question.get('expected_answer')) if question['answerable'] else question.get('expected_answer') is None),
                 'Invalid expected answer')
        positives = set()
        for judgment in relevance:
            _require(isinstance(judgment, dict) and judgment.get('source_id') in catalog, 'Unknown relevant source')
            source = catalog[judgment['source_id']]
            _require(source['family'] == question['family'], 'Relevant source belongs to another question family')
            _require(judgment['source_id'] not in positives and type(judgment.get('grade')) is int
                     and judgment['grade'] in {1, 2}, 'Duplicate/invalid relevance judgment')
            _require(_text(judgment.get('evidence')) and judgment['evidence'] in source['text']
                     and _text(judgment.get('reason')), 'Relevant evidence must quote the source exactly')
            positives.add(judgment['source_id'])
        _require(not question['answerable'] or any(item['grade'] == 2 for item in relevance), 'Answerable case needs complete evidence')
        negatives = question.get('hard_negatives')
        _require(isinstance(negatives, list) and negatives, 'Each case needs reviewed near-match evidence')
        negative_ids = set()
        for negative in negatives:
            _require(isinstance(negative, dict) and negative.get('source_id') in catalog
                     and negative['source_id'] not in positives and negative['source_id'] not in negative_ids
                     and _text(negative.get('reason')), 'Invalid/contradictory hard negative')
            negative_ids.add(negative['source_id'])
        _require(('unanswerable' in tags) == (not question['answerable']), 'Unanswerable tag disagrees with labels')
    _require(observed_families == set(specifications[split]['families']) and observed_tags == TAGS, 'Incomplete split coverage')
    identity = {'id': manifest['dataset_id'], 'version': manifest['version'], 'split': split,
                'manifest_sha256': canonical_hash(manifest), 'corpus_sha256': manifest['corpus_sha256'],
                'questions_sha256': specifications[split]['sha256']}
    return Dataset(directory, manifest, tuple(sources), tuple(questions), identity)


def validate_dataset(directory=DATASET_DIR):
    development = load_dataset(directory, 'development')
    heldout = load_dataset(directory, 'heldout')
    _require(not {item['id'] for item in development.questions} & {item['id'] for item in heldout.questions},
             'Question IDs overlap across splits')
    development_text = {normalize_persian(item['question']).casefold() for item in development.questions}
    _require(not development_text & {normalize_persian(item['question']).casefold() for item in heldout.questions},
             'Normalized questions overlap across splits')
    return {'dataset': development.manifest['version'], 'sources': len(development.sources),
            'development': len(development.questions), 'heldout': len(heldout.questions),
            'review': development.manifest['review']}


def indexed_text(source):
    return source['question'] if source['kind'] == 'qa' else source['text']


def lexical_run(dataset):
    source_terms = {source['id']: persian_terms(indexed_text(source)) for source in dataset.sources}
    rankings = {}
    for question in dataset.questions:
        terms = persian_terms(question['question'])
        hits = [{'source_id': source_id, 'score': len(terms & words) / len(terms)}
                for source_id, words in source_terms.items() if terms and terms & words]
        hits.sort(key=lambda hit: (-hit['score'], hit['source_id']))
        rankings[question['id']] = hits[:max(CUTOFFS)]
    return {'format_version': 1, 'dataset': dataset.identity,
            'system': {'name': 'lexical-overlap', 'version': '1', 'normalization': NORMALIZATION_VERSION,
                       'normalization_sha256': canonical_hash(Path(__file__).with_name('persian_text.py').read_text(encoding='utf-8')),
                       'indexed_text': 'qa-question-or-document-text', 'minimum_score': 'strictly-positive',
                       'tie_break': 'source-id-ascending', 'limit': max(CUTOFFS)}, 'rankings': rankings}


def validate_run(dataset, run):
    _require(isinstance(run, dict) and type(run.get('format_version')) is int and run['format_version'] == 1,
             'Unsupported ranking run')
    _require(run.get('dataset') == dataset.identity, 'Ranking run uses a different dataset/split/checksum')
    system = run.get('system')
    _require(isinstance(system, dict) and all(_text(system.get(field)) for field in ('name', 'version', 'normalization')),
             'Ranking system name/version/normalization are required')
    canonical_hash(system)
    rankings = run.get('rankings')
    _require(isinstance(rankings, dict) and set(rankings) == {question['id'] for question in dataset.questions},
             'Rankings must contain every selected question exactly once, including empty results')
    source_ids = {source['id'] for source in dataset.sources}
    for hits in rankings.values():
        _require(isinstance(hits, list) and len(hits) <= len(source_ids), 'Invalid ranking length')
        seen, previous = set(), math.inf
        for hit in hits:
            _require(isinstance(hit, dict) and set(hit) == {'source_id', 'score'}, 'Invalid ranking hit')
            _require(hit['source_id'] in source_ids and hit['source_id'] not in seen, 'Unknown/duplicate ranked source')
            score = hit['score']
            _require(type(score) in {int, float} and math.isfinite(score) and score <= previous, 'Scores must be finite and descending')
            seen.add(hit['source_id'])
            previous = score


def question_metrics(question, hits):
    grades = {item['source_id']: item['grade'] for item in question['relevance']}
    ranked = [hit['source_id'] for hit in hits[:max(CUTOFFS)]]
    negatives = {item['source_id'] for item in question['hard_negatives']}
    result = {'answerable': question['answerable'], 'abstained': not ranked,
              'hard_negative_at_1': bool(ranked and ranked[0] in negatives)}
    if not grades:
        return {**result, 'recall': None, 'mrr_at_6': None, 'ndcg_at_6': None}
    recall = {str(cutoff): len(set(ranked[:cutoff]) & grades.keys()) / len(grades) for cutoff in CUTOFFS}
    first_rank = next((rank for rank, source_id in enumerate(ranked, 1) if source_id in grades), None)
    dcg = sum((2 ** grades.get(source_id, 0) - 1) / math.log2(rank + 1) for rank, source_id in enumerate(ranked, 1))
    ideal = sum((2 ** grade - 1) / math.log2(rank + 1)
                for rank, grade in enumerate(sorted(grades.values(), reverse=True)[:max(CUTOFFS)], 1))
    return {**result, 'recall': recall, 'mrr_at_6': round(1 / first_rank, 10) if first_rank else 0.0,
            'ndcg_at_6': round(dcg / ideal, 10)}


def _mean(values):
    return round(sum(values) / len(values), 10) if values else None


def _summary(rows):
    answerable = [row for row in rows if row['answerable']]
    unanswerable = [row for row in rows if not row['answerable']]
    return {'questions': len(rows), 'answerable': len(answerable), 'unanswerable': len(unanswerable),
            'recall': {str(cutoff): _mean([row['recall'][str(cutoff)] for row in answerable]) for cutoff in CUTOFFS},
            'mrr_at_6': _mean([row['mrr_at_6'] for row in answerable]),
            'ndcg_at_6': _mean([row['ndcg_at_6'] for row in answerable]),
            'answerable_abstention_rate': _mean([int(row['abstained']) for row in answerable]),
            'unanswerable_false_positive_rate': _mean([int(not row['abstained']) for row in unanswerable]),
            'hard_negative_at_1_rate': _mean([int(row['hard_negative_at_1']) for row in rows])}


def evaluate_run(dataset, run):
    validate_run(dataset, run)
    rows = {question['id']: question_metrics(question, run['rankings'][question['id']]) for question in dataset.questions}
    catalog = {source['id']: source for source in dataset.sources}
    return {'report_version': 1, 'metric_version': METRIC_VERSION, 'cutoffs': list(CUTOFFS), 'run': run,
            'summary': _summary(list(rows.values())),
            'by_tag': {tag: _summary([rows[question['id']] for question in dataset.questions if tag in question['tags']])
                       for tag in sorted(TAGS)},
            'by_source_kind': {kind: _summary([rows[question['id']] for question in dataset.questions
                                              if any(catalog[item['source_id']]['kind'] == kind for item in question['relevance'])])
                               for kind in ('qa', 'pdf', 'document')}, 'per_question': rows}


def compare_reports(dataset, candidate, baseline):
    _require(isinstance(baseline, dict) and baseline.get('report_version') == 1
             and baseline.get('metric_version') == METRIC_VERSION and baseline.get('cutoffs') == list(CUTOFFS),
             'Incompatible baseline report/metric version/cutoffs')
    _require(evaluate_run(dataset, baseline.get('run')) == baseline, 'Baseline metrics do not match its ranked results')
    changes = {}
    for metric in ('mrr_at_6', 'ndcg_at_6', 'answerable_abstention_rate', 'unanswerable_false_positive_rate', 'hard_negative_at_1_rate'):
        previous, current = baseline['summary'][metric], candidate['summary'][metric]
        changes[metric] = {'baseline': previous, 'candidate': current,
                           'delta': round(current - previous, 10) if previous is not None and current is not None else None}
    for cutoff in CUTOFFS:
        previous, current = baseline['summary']['recall'][str(cutoff)], candidate['summary']['recall'][str(cutoff)]
        changes[f'recall_at_{cutoff}'] = {'baseline': previous, 'candidate': current,
                                        'delta': round(current - previous, 10) if previous is not None and current is not None else None}
    return changes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('validate', 'run', 'score', 'check-baseline'))
    parser.add_argument('--dataset', type=Path, default=DATASET_DIR)
    parser.add_argument('--split', choices=('development', 'heldout'), default='development')
    parser.add_argument('--ranker', choices=('lexical', 'local'), default='lexical')
    parser.add_argument('--model-path', type=Path)
    parser.add_argument('--model-id', default='qwen3.5-2b')
    parser.add_argument('--predictions', type=Path)
    parser.add_argument('--compare', type=Path)
    parser.add_argument('--output', type=Path)
    options = parser.parse_args(argv)
    try:
        if options.command == 'validate':
            result = validate_dataset(options.dataset)
        else:
            dataset = load_dataset(options.dataset, options.split)
            if options.command == 'score':
                _require(options.predictions is not None, 'score requires --predictions')
                run = read_json(options.predictions)
            elif options.command == 'run' and options.ranker == 'local':
                _require(options.model_path is not None, 'Local evaluation requires an explicit --model-path')
                from retrieval_eval_local import local_run
                run = local_run(dataset, options.model_id, options.model_path)
            else:
                _require(options.ranker == 'lexical', 'check-baseline uses the frozen lexical reference')
                run = lexical_run(dataset)
            result = evaluate_run(dataset, run)
            if options.command == 'check-baseline':
                baseline = read_json(options.dataset / 'baselines' / ('lexical-' + options.split + '.json'))
                compare_reports(dataset, result, baseline)
                _require(result == baseline, 'Lexical reference drifted from its versioned baseline')
            if options.compare is not None:
                result = {'report': result, 'comparison': compare_reports(dataset, result, read_json(options.compare))}
        output = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'
        if options.output:
            destination = options.output.resolve()
            _require(not destination.is_relative_to(options.dataset.resolve()), 'Reports must not overwrite versioned dataset artifacts')
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(output, encoding='utf-8')
        else:
            print(output, end='')
        return 0
    except (EvaluationError, OSError, TypeError, ValueError) as error:
        print(f'Retrieval evaluation failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
