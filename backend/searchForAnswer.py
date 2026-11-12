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
def _search_qa(qvec: np.ndarray, k: int = 5, *, profile=None):
    _require_query_profile(qvec, profile)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, question, answer, embedding, embedding_dim, embedding_profile
                FROM qa_documents
                WHERE embedding IS NOT NULL
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    results = []
    for r in rows:
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
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:k]


@logger.operation
def _search_pdf(qvec: np.ndarray, k: int = 10, query: str = '', *, profile=None):
    _require_query_profile(qvec, profile)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
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
            "source_type": "pdf",
            "id": r["id"],
            "document_id": r["document_id"],
            "filename": r["filename"],
            "chunk_index": r["chunk_index"],
            "content": r["content"],
            "page_start": r["page_start"],
            "page_end": r["page_end"],
            "question": None,
            "answer": None,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:k]


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

        elif item["source_type"] == "pdf":

            context_parts.append(
                f"""
                    [منبع {i} - PDF]
                    Document ID:
                    {item["document_id"]}

                    Filename:
                    {item.get("filename", "")}

                    Pages:
                    {item["page_start"]}-{item["page_end"]}

                    Content:
                    {item["content"]}

                    Similarity:
                    {item["score"]:.4f}
                    """.strip()
                                )

    return "\n\n".join(context_parts)
