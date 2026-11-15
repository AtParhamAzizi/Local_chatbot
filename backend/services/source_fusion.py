"""Source-independent fixed score calibration and weighted evidence fusion."""

import hashlib
import math

from persian_text import normalize_persian
from services.lexical_index import INDEX_CACHE, hybrid_score


POLICY_VERSION = 'mixed-source-calibrated-v1'
LEXICAL_MODE = 'words'
LEXICAL_WEIGHT = 0.2
LEXICAL_SCALE = 16.0


def source_key(hit):
    # PDF and controlled documents share the pdf_chunks ID namespace.
    return ('qa' if hit['source_type'] == 'qa' else 'document', hit['id'])


def lexical_text(hit):
    if hit['source_type'] == 'qa':
        return hit['question'] + '\n' + hit['answer']
    return hit['content']


def tie_key(hit):
    # No fixed Q&A/PDF precedence, including overlapping table-local IDs.
    text_hash = hashlib.sha256(normalize_persian(lexical_text(hit)).encode('utf-8')).hexdigest()
    identity_hash = hashlib.sha256(repr(source_key(hit)).encode('utf-8')).hexdigest()
    return text_hash, identity_hash


def fuse(results, query, *, profile, min_score=0.35, limit=6, cache=INDEX_CACHE,
         weight=LEXICAL_WEIGHT, scale=LEXICAL_SCALE):
    """Recompute comparable scores from common signals before admission/ranking.

    Calibration here means a shared fixed scale, not learned probabilities.
    All compatible candidates must reach this function before truncation. The
    optional weight/scale support development evaluation; serving uses constants.
    """
    if not results or limit <= 0:
        return []
    hits = [dict(hit) for hit in results]
    keys = [source_key(hit) for hit in hits]
    if len(set(keys)) != len(keys):
        raise ValueError('Duplicate fusion source identity')
    if any(not math.isfinite(hit['semantic_score']) for hit in hits):
        raise ValueError('Nonfinite fusion evidence')
    # Sorting makes snapshot identity independent of SQL/candidate order.
    documents = sorted(((source_key(hit), lexical_text(hit)) for hit in hits))
    index = cache.get(documents, namespace=(POLICY_VERSION, profile.identity), mode=LEXICAL_MODE)
    lexical = index.scores(query)
    for hit in hits:
        hit['lexical_score'] = lexical.get(source_key(hit), 0.0)
        hit['score'] = hybrid_score(hit['semantic_score'], hit['lexical_score'], weight=weight, scale=scale)
    hits = [hit for hit in hits if hit['score'] >= min_score]
    hits.sort(key=lambda hit: (-hit['score'], tie_key(hit)))
    return hits[:limit]
