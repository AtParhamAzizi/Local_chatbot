import json

import numpy as np
from database import get_conn, logger
from persian_text import persian_terms
from embedding_profile import EmbeddingProfile, compatible_profile


def _cosine_score(qvec, blob, embedding_dim):
    """Decode and score a stored normalized embedding safely."""
    if not blob:
        return None
    if len(blob) % np.dtype(np.float32).itemsize:
        return None
    vec = np.frombuffer(blob, dtype=np.float32)
    if not embedding_dim or vec.size != int(embedding_dim):
        return None
    if vec.size != qvec.size:
        return None
    q_norm = float(np.linalg.norm(qvec))
    v_norm = float(np.linalg.norm(vec))
    if not np.isfinite(vec).all() or not np.isfinite(qvec).all() or not np.isfinite(q_norm + v_norm) or q_norm == 0 or v_norm == 0:
        return None
    return float(np.dot(qvec, vec) / (q_norm * v_norm))


def _terms(value):
    return persian_terms(value)


def _lexical_overlap(query, content):
    query_terms = _terms(query)
    content_terms = _terms(content)
    if not query_terms or not content_terms:
        return 0.0
    return len(query_terms & content_terms) / len(query_terms)


@logger.operation
def _search_qa(qvec: np.ndarray, k: int = 5, *, profile=None, index_id=None):
    _require_query_profile(qvec, profile)
    if index_id is not None and index_id != profile.identity:
        raise ValueError('Index identity disagrees with query profile')
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if index_id is not None:
                cur.execute("""
                    SELECT s.id, s.question, s.answer, s.embedding, s.embedding_dim, s.embedding_profile, s.source_metadata,
                           v.embedding AS migrated_embedding
                    FROM qa_documents s LEFT JOIN embedding_index_vectors v
                      ON v.index_id=%s AND v.source_kind='qa' AND v.source_id=s.id
                     AND v.source_hash=SHA2(s.question, 256)
                    WHERE s.embedding IS NOT NULL OR v.embedding IS NOT NULL
                """, (index_id,))
            else:
                cur.execute("""
                SELECT id, question, answer, embedding, embedding_dim, embedding_profile, source_metadata
                FROM qa_documents
                WHERE embedding IS NOT NULL
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    results = []
    for r in rows:
        if index_id is not None and r.get('migrated_embedding') is not None:
            r = dict(r, embedding=r['migrated_embedding'], embedding_dim=profile.dimension,
                     embedding_profile=profile.to_json())
        if not compatible_profile(r.get('embedding_profile'), profile) or r['embedding_dim'] != profile.dimension:
            continue
        score = _cosine_score(qvec, r["embedding"], r["embedding_dim"])
        if score is None:
            continue
        results.append({
            "score": score,
            "source_type": "qa",
            "id": r["id"],
            "question": r["question"],
            "answer": r["answer"],
            "content": None,
            "document_id": None,
            "page_start": None,
            "page_end": None,
            "source_metadata": _source_metadata(r.get('source_metadata')),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:k]


@logger.operation
def _search_pdf(qvec: np.ndarray, k: int = 10, query: str = '', *, profile=None, index_id=None):
    _require_query_profile(qvec, profile)
    if index_id is not None and index_id != profile.identity:
        raise ValueError('Index identity disagrees with query profile')
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if index_id is not None:
                cur.execute("""
                    SELECT c.id, c.document_id, d.filename, c.chunk_index, c.page_start, c.page_end,
                           c.content, c.embedding, c.embedding_dim, c.embedding_profile, c.source_metadata,
                           d.embedding_profile AS document_embedding_profile,
                           d.embedding_dim AS document_embedding_dim, v.embedding AS migrated_embedding
                    FROM pdf_chunks c JOIN pdf_documents d ON d.id=c.document_id
                    LEFT JOIN embedding_index_vectors v
                      ON v.index_id=%s AND v.source_kind='pdf' AND v.source_id=c.id
                     AND v.source_hash=SHA2(c.content, 256)
                    WHERE (c.embedding IS NOT NULL OR v.embedding IS NOT NULL) AND d.status='completed'
                """, (index_id,))
            else:
                cur.execute("""
                SELECT c.id,
                    c.document_id,
                    d.filename,
                    c.chunk_index,
                    c.page_start,
                    c.page_end,
                    c.content,
                    c.embedding,
                    c.embedding_dim,
                    c.embedding_profile,
                    c.source_metadata,
                    d.embedding_profile AS document_embedding_profile,
                    d.embedding_dim AS document_embedding_dim
                FROM pdf_chunks AS c
                INNER JOIN pdf_documents AS d ON d.id = c.document_id
                WHERE c.embedding IS NOT NULL
                AND d.status = 'completed'
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    results = []
    for r in rows:
        if index_id is not None and r.get('migrated_embedding') is not None:
            r = dict(r, embedding=r['migrated_embedding'], embedding_dim=profile.dimension,
                     embedding_profile=profile.to_json(), document_embedding_profile=profile.to_json(),
                     document_embedding_dim=profile.dimension)
        if (not compatible_profile(r.get('embedding_profile'), profile)
                or not compatible_profile(r.get('document_embedding_profile'), profile)
                or r['embedding_dim'] != profile.dimension or r.get('document_embedding_dim') != profile.dimension):
            continue
        semantic_score = _cosine_score(qvec, r["embedding"], r["embedding_dim"])
        if semantic_score is None:
            continue
        score = (semantic_score * 0.82) + (_lexical_overlap(query, r["content"]) * 0.18)
        results.append({
            "score": score,
            "semantic_score": semantic_score,
            "source_type": "document" if r.get('source_metadata') else "pdf",
            "id": r["id"],
            "document_id": r["document_id"],
            "filename": r["filename"],
            "chunk_index": r["chunk_index"],
            "content": r["content"],
            "page_start": r["page_start"],
            "page_end": r["page_end"],
            "question": None,
            "answer": None,
            "source_metadata": _source_metadata(r.get('source_metadata')),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:k]


def _source_metadata(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None
    return value if isinstance(value, dict) else None


def _require_query_profile(qvec, profile):
    if not isinstance(profile, EmbeddingProfile):
        raise ValueError('A query embedding profile is required')
    if qvec.ndim != 1 or qvec.size != profile.dimension or not np.isfinite(qvec).all():
        raise ValueError('Query vector disagrees with its profile')






@logger.operation
def build_context(results):

    context_parts = []

    for i, item in enumerate(results, start=1):

        if item["source_type"] == "qa":

            context_parts.append(
                f"""
                    [منبع {i} - پرسش و پاسخ]
                    Question:
                    {item["question"]}

                    Answer:
                    {item["answer"]}

                    Similarity:
                    {item["score"]:.4f}
                    """.strip()
                                )

        elif item["source_type"] in {"pdf", "document"}:
            source_label = 'PDF' if item['source_type'] == 'pdf' else 'Document'
            location_label = 'Pages' if item['source_type'] == 'pdf' else 'Source location'
            if item['source_type'] == 'pdf':
                source_location = f"{item['page_start']}-{item['page_end']}"
            else:
                source_location = json.dumps((item.get('source_metadata') or {}).get('location', {}), ensure_ascii=False)

            context_parts.append(
                f"""
                    [منبع {i} - {source_label}]
                    Document ID:
                    {item["document_id"]}

                    Filename:
                    {item.get("filename", "")}

                    {location_label}:
                    {source_location}

                    Content:
                    {item["content"]}

                    Similarity:
                    {item["score"]:.4f}
                    """.strip()
                                )

        else:
            continue

        if item.get('source_metadata'):
            context_parts[-1] += '\nSource metadata: ' + json.dumps(item['source_metadata'], ensure_ascii=False, sort_keys=True)

    return "\n\n".join(context_parts)
