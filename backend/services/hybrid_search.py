"""The development-selected hybrid policy over profile-compatible source snapshots."""

from services.lexical_index import INDEX_CACHE, hybrid_score


POLICY_VERSION = 'fa-bm25-hybrid-v1'
LEXICAL_MODE = 'words'
LEXICAL_WEIGHT = 0.2
LEXICAL_SCALE = 8.0


def rerank(results, query, *, kind, profile, cache=INDEX_CACHE):
    if not results or not query:
        return results
    documents = [(hit['id'], hit['question'] + '\n' + hit['answer'] if kind == 'qa' else hit['content'])
                 for hit in results]
    index = cache.get(documents, namespace=(kind, profile.identity), mode=LEXICAL_MODE)
    scores = index.scores(query)
    for hit in results:
        lexical_score = scores.get(hit['id'], 0.0)
        hit['lexical_score'] = lexical_score
        hit['score'] = hybrid_score(hit['semantic_score'], lexical_score, weight=LEXICAL_WEIGHT, scale=LEXICAL_SCALE)
    return results
