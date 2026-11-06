"""Administrator pdf HTTP routes."""

from flask import current_app, g, jsonify

from abuse_controls import limit_requests
from database import logger
from request_validation import MAX_PDF_UPLOAD_BYTES, PAGE_FIELDS, bounded_ids, query_schema, read_upload
from services.adapters import connect_database as get_conn
from services.ingestion import IngestionError


@query_schema(PAGE_FIELDS)
@logger.operation
def admin_pdf_documents():
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
                    """
                    SELECT COUNT(*) AS c
                    FROM pdf_documents
                    WHERE filename LIKE %s
                    """,
                    (like,)
                )
                total = cur.fetchone()['c']
                cur.execute(
                    """
                    SELECT
                        id,
                        filename,
                        file_size,
                        page_count,
                        chunk_count,
                        embedded_chunk_count,
                        embedding_dim,
                        status,
                        created_at
                    FROM pdf_documents
                    WHERE filename LIKE %s
                    ORDER BY id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (
                        like,
                        per_page,
                        offset
                    )
                )
            else:
                cur.execute(
                    """
                    SELECT COUNT(*) AS c
                    FROM pdf_documents
                    """
                )
                total = cur.fetchone()['c']
                cur.execute(
                    """
                    SELECT
                        id,
                        filename,
                        file_size,
                        page_count,
                        chunk_count,
                        embedded_chunk_count,
                        embedding_dim,
                        status,
                        created_at
                    FROM pdf_documents
                    ORDER BY id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (
                        per_page,
                        offset
                    )
                )
            items = cur.fetchall()
    finally:
        conn.close()
    return jsonify({
        'total': total,
        'page': page,
        'per_page': per_page,
        'items': items
    })


@bounded_ids(doc_id=2147483647)
@logger.operation
def admin_pdf_delete(doc_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM pdf_documents
                WHERE id = %s
                """,
                (doc_id,)
            )
            deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    if not deleted:
        return jsonify({
            'error': 'رکورد PDF یافت نشد'
        }), 404
    return jsonify({
        'ok': True,
        'deleted': doc_id
    })


@logger.operation
def admin_pdf_stats():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS documents,
                    COALESCE(
                        SUM(chunk_count),
                        0
                    ) AS chunks,
                    COALESCE(
                        SUM(embedded_chunk_count),
                        0
                    ) AS embedded_chunks
                FROM pdf_documents
                """
            )
            row = cur.fetchone()
            cur.execute(
                """
                SELECT embedding_dim
                FROM pdf_documents
                WHERE embedding_dim IS NOT NULL
                ORDER BY id DESC
                LIMIT 1
                """
            )
            dim_row = cur.fetchone()
    finally:
        conn.close()

    return jsonify({
        'documents':
            int(row['documents']),
        'chunks':
            int(row['chunks']),
        'embedded_chunks':
            int(row['embedded_chunks']),
        'dim':
            (
                int(dim_row['embedding_dim'])
                if dim_row
                else None
            )
    })


@limit_requests('upload')
@logger.operation
def admin_pdf_upload():
    raw, filename, replace = read_upload({'.pdf'}, MAX_PDF_UPLOAD_BYTES)
    try:
        return jsonify(current_app.extensions['ingestion_service'].ingest_pdf(raw, filename, replace=replace))
    except IngestionError as error:
        return jsonify(error.payload), error.status


def register_routes(app):
    app.add_url_rule('/admin/api/pdf/upload', view_func=admin_pdf_upload, methods=['POST'])
    app.add_url_rule('/admin/api/pdf/documents', view_func=admin_pdf_documents)
    app.add_url_rule('/admin/api/pdf/documents/<int:doc_id>', view_func=admin_pdf_delete, methods=['DELETE'])
    app.add_url_rule('/admin/api/pdf/stats', view_func=admin_pdf_stats)
