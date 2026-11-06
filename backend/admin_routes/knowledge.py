"""Administrator knowledge HTTP routes."""

from flask import current_app, g, jsonify

from abuse_controls import limit_requests
from database import logger
from request_validation import (
    MAX_JSON_UPLOAD_BYTES, PAGE_FIELDS, bounded_ids, query_schema, read_upload,
)
from services.adapters import connect_database as get_conn, embedding_dim
from services.ingestion import IngestionError


@logger.operation
def admin_stats():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM qa_documents")
            total = cur.fetchone()['c']
            cur.execute("SELECT COUNT(*) AS c FROM qa_documents WHERE embedding IS NOT NULL")
            embedded = cur.fetchone()['c']
    finally:
        conn.close()

    return jsonify({
        'total_documents':    total,
        'embedded_documents': embedded,
        'dim':                embedding_dim(),
    })


@query_schema(PAGE_FIELDS)
@logger.operation
def admin_documents():
    page = g.validated_query.get('page', 1)
    per_page = g.validated_query.get('per_page', 10)
    search_query = g.validated_query.get('q', '')
    offset = (page - 1) * per_page
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if search_query:
                like = f'%{search_query}%'
                cur.execute(
                    "SELECT COUNT(*) AS c FROM qa_documents "
                    "WHERE question LIKE %s OR answer LIKE %s",
                    (like, like)
                )
                total = cur.fetchone()['c']
                cur.execute(
                    "SELECT id, question, answer, "
                    "       (embedding IS NOT NULL) AS has_embedding "
                    "  FROM qa_documents "
                    " WHERE question LIKE %s OR answer LIKE %s "
                    " ORDER BY id DESC "
                    " LIMIT %s OFFSET %s",
                    (like, like, per_page, offset)
                )
            else:
                cur.execute("SELECT COUNT(*) AS c FROM qa_documents")
                total = cur.fetchone()['c']
                cur.execute(
                    "SELECT id, question, answer, "
                    "       (embedding IS NOT NULL) AS has_embedding "
                    "  FROM qa_documents "
                    " ORDER BY id DESC "
                    " LIMIT %s OFFSET %s",
                    (per_page, offset)
                )
            items = cur.fetchall()
    finally:
        conn.close()
    for it in items:
        it['has_embedding'] = bool(it['has_embedding'])
    return jsonify({
        'total':    total,
        'page':     page,
        'per_page': per_page,
        'items':    items,
    })


@bounded_ids(doc_id=2147483647)
@logger.operation
def admin_delete(doc_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM qa_documents WHERE id=%s", (doc_id,))
            deleted = cur.rowcount
    finally:
        conn.close()
    if not deleted:
        return jsonify({'error': 'رکورد یافت نشد'}), 404
    return jsonify({'ok': True, 'deleted': doc_id})


@limit_requests('upload')
@logger.operation
def admin_upload():
    raw, _, replace = read_upload({'.json'}, MAX_JSON_UPLOAD_BYTES)
    try:
        return jsonify(current_app.extensions['ingestion_service'].ingest_qa(raw, replace=replace))
    except IngestionError as error:
        return jsonify(error.payload), error.status


def register_routes(app):
    app.add_url_rule('/admin/api/stats', view_func=admin_stats)
    app.add_url_rule('/admin/api/upload', view_func=admin_upload, methods=['POST'])
    app.add_url_rule('/admin/api/documents', view_func=admin_documents)
    app.add_url_rule('/admin/api/documents/<int:doc_id>', view_func=admin_delete, methods=['DELETE'])
