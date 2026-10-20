import os
import time
import re
from pathlib import Path

import fitz
import numpy as np
from flask import request, jsonify

import json

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from database import get_conn, init_db,hash_question, logger, sha256_bytes
# from logger import logDrop


from generator import set_generator_model
from model_registry import get_active_model_id, get_model_catalog, set_active_model

from interface import (
    embed_texts,
    embedding_dim,
    set_embedding_model,
    ADMIN_DIR,
    ADMIN_PORT,
    MAX_UPLOAD_BYTES,
    ALLOWED_EXT,
)



admin_app = Flask('admin', static_folder=ADMIN_DIR, static_url_path='')

CORS(
    admin_app,
    origins=["null"],
    supports_credentials=True
)
# CORS(
#     admin_app,
#     # resources={
#     #     r"/admin/api/*": {
            
#     #     }
#     # }
#     "origins": ["null"],
#     supports_credentials=True
#     )



@admin_app.route('/')
@logger.operation
def admin_index():
    return send_from_directory(ADMIN_DIR, 'admin.html')



@admin_app.route('/admin/api/models', methods=['GET'])
@logger.operation
def admin_models():
    return jsonify({
        'active_model': get_active_model_id(),
        'models': get_model_catalog(),
    })


@admin_app.route('/admin/api/models', methods=['POST'])
@logger.operation
def admin_set_model():
    data = request.get_json(silent=True) or {}
    model_id = str(data.get('model_id') or '').strip()

    if not model_id:
        return jsonify({'error': 'model_id is required'}), 400

    try:
        set_active_model(model_id)
        set_embedding_model(model_id)
        set_generator_model(model_id)
        return jsonify({
            'ok': True,
            'active_model': get_active_model_id(),
            'message': 'Model selection updated. New requests will use the selected model.',
        })
    except FileNotFoundError as exc:
        return jsonify({'error': str(exc)}), 409
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:
        return jsonify({'error': f'Unable to change model: {exc}'}), 500
@admin_app.route('/admin/api/stats')
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



@admin_app.route('/admin/api/upload', methods=['POST'])
@logger.operation
def admin_upload():
    logger.logDrop('Received a knowledge-base JSON upload')
    started = time.time()

    if 'file' not in request.files:
        return jsonify({'error': 'فایلی ارسال نشده است'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'نام فایل خالی است'}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXT:
        return jsonify({'error': 'فرمت فایل باید JSON باشد'}), 400

    raw = file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        return jsonify({'error': 'حجم فایل بیش از حد مجاز است'}), 413

    try:
        payload = json.loads(raw.decode('utf-8'))
    except UnicodeDecodeError:
        return jsonify({'error': 'فایل باید با انکودینگ UTF-8 ذخیره شده باشد'}), 400
    except json.JSONDecodeError as e:
        return jsonify({'error': f'JSON نامعتبر: {e.msg} (موقعیت {e.pos})'}), 400

    if not isinstance(payload, list):
        return jsonify({'error': 'ساختار فایل باید آرایه‌ای از آبجکت‌ها باشد'}), 400

    replace = request.form.get('replace', 'false').lower() in ('1', 'true', 'yes', 'on')

    rows, errors, seen = [], [], set()
    for i, item in enumerate(payload):
        if not isinstance(item, dict):
            errors.append(f'ردیف {i + 1}: باید آبجکت باشد')
            continue
        q = item.get('question')
        a = item.get('answer')
        if not isinstance(q, str) or not q.strip():
            errors.append(f'ردیف {i + 1}: فیلد question خالی یا نامعتبر')
            continue
        if not isinstance(a, str) or not a.strip():
            errors.append(f'ردیف {i + 1}: فیلد answer خالی یا نامعتبر')
            continue
        q, a = q.strip(), a.strip()
        h = hash_question(q)
        if h in seen:
            continue
        seen.add(h)
        rows.append((q, a, h))

    if not rows:
        return jsonify({
            'error':  'هیچ ردیف معتبری در فایل یافت نشد',
            'errors': errors[:50],
        }), 400

    try:
        vectors = embed_texts([r[0] for r in rows])
    except Exception as e:
        return jsonify({'error': f'خطا در بردارسازی: {e}'}), 500

    dim = int(vectors.shape[1])

    conn = get_conn()
    inserted = updated = skipped = 0
    try:
        with conn.cursor() as cur:
            for (q, a, h), vec in zip(rows, vectors):
                blob = vec.tobytes()

                cur.execute("SELECT id FROM qa_documents WHERE question_hash = %s", (h,))
                existing = cur.fetchone()

                if existing:
                    if replace:
                        cur.execute(
                            "UPDATE qa_documents "
                            "   SET answer=%s, embedding=%s, embedding_dim=%s "
                            " WHERE id=%s",
                            (a, blob, dim, existing['id'])
                        )
                        logger.logDrop(f"Updated knowledge-base record id={existing['id']}, embedding_dim={dim}")
                        updated += 1
                    else:
                        skipped += 1
                    continue

                cur.execute(
                    "INSERT INTO qa_documents "
                    "  (question, answer, question_hash, embedding, embedding_dim) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (q, a, h, blob, dim)
                )
                logger.logDrop(f'Inserted knowledge-base record id={cur.lastrowid}, embedding_dim={dim}')
                inserted += 1
    finally:
        conn.close()

    logger.logDrop(f'JSON import completed: inserted={inserted}, updated={updated}, skipped={skipped}')
    if errors:
        logger.logDrop(f'JSON import ignored {len(errors)} invalid entries', logger.WARNING)

    return jsonify({
        'received':     len(payload),
        'inserted':     inserted,
        'updated':      updated,
        'skipped':      skipped,
        'embedded':     inserted + updated,
        'duration_sec': round(time.time() - started, 2),
        'errors':       errors[:50],
    })



@admin_app.route('/admin/api/documents')
@logger.operation
def admin_documents():
    try:
        page     = max(1, int(request.args.get('page', 1)))
        per_page = min(100, max(1, int(request.args.get('per_page', 10))))
    except ValueError:
        return jsonify({'error': 'پارامترهای صفحه نامعتبر'}), 400

    q      = (request.args.get('q') or '').strip()
    offset = (page - 1) * per_page

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if q:
                like = f'%{q}%'
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


@admin_app.route('/admin/api/documents/<int:doc_id>', methods=['DELETE'])
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




PDF_ALLOWED_EXT = {'.pdf'}

MAX_PDF_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

PDF_CHUNK_SIZE = 900
PDF_CHUNK_OVERLAP = 160

PDF_EMBED_BATCH_SIZE = 16



@logger.operation
def split_text(text, chunk_size=1200, overlap=200):

    # Remove layout noise introduced by PDF extraction before embedding.
    text = text.replace('\u00ad', '')
    text = re.sub(r'(?<=\w)-\n(?=\w)', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()

    if not text:
        return []

    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    chunks = []

    start = 0
    text_len = len(text)

    while start < text_len:

        end = min(start + chunk_size, text_len)

        # Try to find a natural breaking point.
        if end < text_len:
            candidates = [
                text.rfind("\n\n", start, end),
                text.rfind("\n", start, end),
                text.rfind(". ", start, end),
                text.rfind("؟ ", start, end),
                text.rfind("! ", start, end),
            ]

            best = max(candidates)

            if best > start + chunk_size * 0.5:
                end = best + 1

        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end >= text_len:
            break

        start = max(0, end - overlap)

    return chunks


@admin_app.route('/admin/api/pdf/upload', methods=['POST'])
@logger.operation
def admin_pdf_upload():

    started = time.time()

    if 'file' not in request.files:
        return jsonify({
            'error': 'فایلی ارسال نشده است'
        }), 400

    file = request.files['file']

    if not file.filename:
        return jsonify({
            'error': 'نام فایل خالی است'
        }), 400

    filename = file.filename

    ext = Path(filename).suffix.lower()

    if ext not in PDF_ALLOWED_EXT:
        return jsonify({
            'error': 'فقط فایل PDF پذیرفته می‌شود'
        }), 400

    raw = file.read()

    if len(raw) > MAX_PDF_UPLOAD_BYTES:
        return jsonify({
            'error': 'حجم فایل بیش از حد مجاز است'
        }), 413

    if not raw:
        return jsonify({
            'error': 'فایل PDF خالی است'
        }), 400


    try:
        pdf = fitz.open(stream=raw, filetype='pdf')
    except Exception as e:
        return jsonify({
            'error': f'فایل PDF معتبر نیست: {e}'
        }), 400

    pdf_page_count = pdf.page_count

    if pdf.page_count == 0:
        pdf.close()

        return jsonify({
            'error': 'فایل PDF هیچ صفحه‌ای ندارد'
        }), 400

    document_hash = sha256_bytes(raw)


    conn = get_conn()

    try:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT id, filename, status
                FROM pdf_documents
                WHERE document_hash = %s
                """,
                (document_hash,)
            )

            existing = cur.fetchone()

    finally:
        conn.close()

    replace = request.form.get(
        'replace',
        'false'
    ).lower() in ('1', 'true', 'yes', 'on')

    if existing and not replace:

        pdf.close()

        return jsonify({
            'success': False,
            'duplicate': True,
            'message': 'این فایل PDF قبلاً بارگذاری شده است.',
            'document_id': existing['id'],
            'filename': existing['filename'],
            'status': existing['status']
        }), 409



    pages = []

    try:

        for page_number in range(pdf.page_count):

            page = pdf.load_page(page_number)

            text = page.get_text("text", sort=True)

            text = text.strip()

            if text:
                pages.append({
                    'page': page_number + 1,
                    'text': text
                })

    except Exception as e:

        pdf.close()

        return jsonify({
            'error': f'خطا در استخراج متن PDF: {e}'
        }), 500

    finally:
        pdf.close()

    if not pages:

        return jsonify({
            'error': (
                'هیچ متن قابل استخراجی از PDF پیدا نشد. '
                'ممکن است PDF اسکن‌شده باشد.'
            )
        }), 400


    chunks = []

    chunk_index = 0

    for page in pages:

        page_chunks = split_text(
            page['text'],
            chunk_size=PDF_CHUNK_SIZE,
            overlap=PDF_CHUNK_OVERLAP
        )

        for content in page_chunks:

            chunks.append({
                'chunk_index': chunk_index,
                'page_start': page['page'],
                'page_end': page['page'],
                'content': content
            })

            chunk_index += 1

    if not chunks:

        return jsonify({
            'error': 'هیچ chunk قابل استفاده‌ای از PDF تولید نشد'
        }), 400

    try:

        texts = [
            chunk['content']
            for chunk in chunks
        ]

        vectors = embed_texts(texts)

        if vectors is None:
            raise RuntimeError(
                'embed_texts هیچ برداری برنگرداند'
            )

        vectors = np.asarray(vectors)

        if vectors.ndim != 2:
            raise RuntimeError(
                f'ابعاد embedding نامعتبر است: {vectors.shape}'
            )

        if vectors.shape[0] != len(chunks):
            raise RuntimeError(
                'تعداد embeddingها با تعداد chunkها برابر نیست'
            )

        dim = int(vectors.shape[1])

    except Exception as e:

        return jsonify({
            'error': f'خطا در بردارسازی PDF: {e}'
        }), 500

    conn = get_conn()

    document_id = None

    try:

        with conn.cursor() as cur:

            if existing and replace:

                document_id = existing['id']

                cur.execute(
                    """
                    DELETE FROM pdf_documents
                    WHERE id = %s
                    """,
                    (document_id,)
                )
            cur.execute(
                """
                INSERT INTO pdf_documents
                (
                    filename,
                    document_hash,
                    file_size,
                    page_count,
                    chunk_count,
                    embedded_chunk_count,
                    embedding_dim,
                    status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    filename,
                    document_hash,
                    len(raw),
                    pdf_page_count,
                    len(chunks),
                    0,
                    dim,
                    'processing'
                )
            )

            document_id = cur.lastrowid


            for chunk, vector in zip(chunks, vectors):

                blob = np.asarray(
                    vector,
                    dtype=np.float32
                ).tobytes()

                cur.execute(
                    """
                    INSERT INTO pdf_chunks
                    (
                        document_id,
                        chunk_index,
                        page_start,
                        page_end,
                        content,
                        embedding,
                        embedding_dim
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        document_id,
                        chunk['chunk_index'],
                        chunk['page_start'],
                        chunk['page_end'],
                        chunk['content'],
                        blob,
                        dim
                    )
                )

            # ---------------------------------------------
            # Update status
            # ---------------------------------------------

            cur.execute(
                """
                UPDATE pdf_documents
                SET
                    embedded_chunk_count = %s,
                    status = 'completed'
                WHERE id = %s
                """,
                (
                    len(chunks),
                    document_id
                )
            )

        conn.commit()

    except Exception as e:

        conn.rollback()

        try:
            with conn.cursor() as cur:

                if document_id:

                    cur.execute(
                        """
                        UPDATE pdf_documents
                        SET
                            status = 'failed',
                            error_message = %s
                        WHERE id = %s
                        """,
                        (
                            str(e),
                            document_id
                        )
                    )

            conn.commit()

        except Exception:
            pass

        return jsonify({
            'error': f'خطا در ذخیره PDF در دیتابیس: {e}'
        }), 500

    finally:
        conn.close()


    return jsonify({

        'success': True,

        'document_id': document_id,

        'filename': filename,

        'file_size': len(raw),

        'page_count': pdf_page_count,

        'chunk_count': len(chunks),

        'embedded_chunks': len(chunks),

        'embedding_dim': dim,

        'duration_sec': round(
            time.time() - started,
            2
        )

    }), 200



@admin_app.route('/admin/api/pdf/documents')
@logger.operation
def admin_pdf_documents():

    try:

        page = max(
            1,
            int(request.args.get('page', 1))
        )

        per_page = min(
            100,
            max(
                1,
                int(request.args.get('per_page', 10))
            )
        )

    except ValueError:

        return jsonify({
            'error': 'پارامترهای صفحه نامعتبر'
        }), 400

    q = (
        request.args.get('q') or ''
    ).strip()

    offset = (
        page - 1
    ) * per_page

    conn = get_conn()

    try:

        with conn.cursor() as cur:

            if q:

                like = f'%{q}%'

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


@admin_app.route(
    '/admin/api/pdf/documents/<int:doc_id>',
    methods=['DELETE']
)
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



@admin_app.route('/admin/api/pdf/stats')
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


@logger.operation
def admin_run():
    logger.logDrop(f'Starting the admin service on port {ADMIN_PORT}')
    admin_app.run(
        host='0.0.0.0',
        port=ADMIN_PORT,
        debug=True,
        use_reloader=False,
        threaded=True,
    )






#logger api
@admin_app.route("/admin/api/logs", methods=["GET"])
@logger.operation
def get_admin_logs():
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    id,
                    _level,
                    _from,
                    message,
                    warninglevel,
                    created_at
                FROM adminlogger
                ORDER BY created_at DESC, id DESC
            """)

            rows = cur.fetchall()

        return jsonify({
            "success": True,
            "logs": rows
        }), 200

    except Exception as e:

        logger.logDrop(f'Unable to read application logs: {e}', logger.FAILED)

        return jsonify({
            "success": False,
            "message": str(e),
            "logs": []
        }), 500

    finally:
        if conn:
            conn.close()











#00000000000000000000000000000000000000000000000000000000000000000000000000000000000000


from flask import request, jsonify

# ============================================================
# ADMIN CHAT API
# ============================================================

@admin_app.route('/admin/api/chat/sessions', methods=['GET'])
@logger.operation
def admin_chat_sessions():

    try:
        # -----------------------------
        # Parameters
        # -----------------------------
        try:
            limit = int(request.args.get('limit', 300))
        except (TypeError, ValueError):
            return jsonify({
                'error': 'پارامتر limit نامعتبر است'
            }), 400

        limit = max(1, min(1000, limit))

        q = (request.args.get('q') or '').strip()

        conn = get_conn()

        try:
            with conn.cursor() as cur:

                # -----------------------------------------
                # Search sessions
                # -----------------------------------------
                if q:

                    like = f'%{q}%'

                    sql = """
                        SELECT
                            user_id,
                            ip_address,
                            COUNT(*) AS message_count,

                            COALESCE(
                                SUM(
                                    CASE
                                        WHEN feedback = 'like'
                                        THEN 1
                                        ELSE 0
                                    END
                                ),
                                0
                            ) AS likes,

                            COALESCE(
                                SUM(
                                    CASE
                                        WHEN feedback = 'dislike'
                                        THEN 1
                                        ELSE 0
                                    END
                                ),
                                0
                            ) AS dislikes,

                            MIN(created_at) AS started_at,
                            MAX(created_at) AS last_activity

                        FROM chat_history

                        WHERE
                            user_id LIKE %s
                            OR ip_address LIKE %s
                            OR user_message LIKE %s
                            OR bot_response LIKE %s

                        GROUP BY
                            user_id,
                            ip_address

                        ORDER BY
                            last_activity DESC

                        LIMIT %s
                    """

                    cur.execute(
                        sql,
                        (
                            like,
                            like,
                            like,
                            like,
                            limit
                        )
                    )

                # -----------------------------------------
                # Get all sessions
                # -----------------------------------------
                else:

                    sql = """
                        SELECT
                            user_id,
                            ip_address,
                            COUNT(*) AS message_count,

                            COALESCE(
                                SUM(
                                    CASE
                                        WHEN feedback = 'like'
                                        THEN 1
                                        ELSE 0
                                    END
                                ),
                                0
                            ) AS likes,

                            COALESCE(
                                SUM(
                                    CASE
                                        WHEN feedback = 'dislike'
                                        THEN 1
                                        ELSE 0
                                    END
                                ),
                                0
                            ) AS dislikes,

                            MIN(created_at) AS started_at,
                            MAX(created_at) AS last_activity

                        FROM chat_history

                        GROUP BY
                            user_id,
                            ip_address

                        ORDER BY
                            last_activity DESC

                        LIMIT %s
                    """

                    cur.execute(sql, (limit,))

                sessions = cur.fetchall()

                # -----------------------------------------
                # Add first message preview
                # -----------------------------------------
                for session in sessions:

                    cur.execute(
                        """
                        SELECT user_message
                        FROM chat_history
                        WHERE
                            user_id = %s
                            AND ip_address = %s
                        ORDER BY
                            created_at ASC,
                            id ASC
                        LIMIT 1
                        """,
                        (
                            session['user_id'],
                            session['ip_address']
                        )
                    )

                    first_message = cur.fetchone()

                    session['first_message'] = (
                        first_message['user_message']
                        if first_message
                        else None
                    )

                    # -------------------------------------
                    # Normalize numbers
                    # -------------------------------------
                    session['message_count'] = int(
                        session.get('message_count') or 0
                    )

                    session['likes'] = int(
                        session.get('likes') or 0
                    )

                    session['dislikes'] = int(
                        session.get('dislikes') or 0
                    )

                    # -------------------------------------
                    # Serialize dates
                    # -------------------------------------
                    if session.get('started_at'):
                        session['started_at'] = (
                            session['started_at']
                            .isoformat(sep=' ')
                        )

                    if session.get('last_activity'):
                        session['last_activity'] = (
                            session['last_activity']
                            .isoformat(sep=' ')
                        )

        finally:
            conn.close()

        return jsonify({
            'sessions': sessions,
            'count': len(sessions)
        }), 200

    except Exception as e:

        logger.logDrop(f'Unable to list chat sessions: {e}', logger.FAILED)

        return jsonify({
            'error': 'خطا در دریافت گفتگوها',
            'details': str(e)
        }), 500




@admin_app.route('/admin/api/chat/messages', methods=['GET'])
@logger.operation
def admin_chat_messages():
    """
    Get messages belonging to a session.

    JS calls:

    GET /admin/api/chat/messages
        ?user_id=...
        &ip=...
    """

    try:

        user_id = (
            request.args.get('user_id') or ''
        ).strip()

        ip = (
            request.args.get('ip') or ''
        ).strip()

        # -----------------------------------------
        # Validate user_id
        # -----------------------------------------

        if not user_id:

            return jsonify({
                'error': 'پارامتر user_id الزامی است'
            }), 400

        conn = get_conn()

        try:

            with conn.cursor() as cur:

                # -------------------------------------
                # Specific user + IP
                # -------------------------------------

                if ip:

                    cur.execute(
                        """
                        SELECT
                            id,
                            user_id,
                            ip_address,
                            user_message,
                            bot_response,
                            feedback,
                            feedback_at,
                            created_at

                        FROM chat_history

                        WHERE
                            user_id = %s
                            AND ip_address = %s

                        ORDER BY
                            created_at ASC,
                            id ASC
                        """,
                        (
                            user_id,
                            ip
                        )
                    )

                # -------------------------------------
                # User only
                # -------------------------------------

                else:

                    cur.execute(
                        """
                        SELECT
                            id,
                            user_id,
                            ip_address,
                            user_message,
                            bot_response,
                            feedback,
                            feedback_at,
                            created_at

                        FROM chat_history

                        WHERE
                            user_id = %s

                        ORDER BY
                            created_at ASC,
                            id ASC
                        """,
                        (user_id,)
                    )

                messages = cur.fetchall()

        finally:
            conn.close()

        # -----------------------------------------
        # Convert database values to JSON values
        # -----------------------------------------

        for message in messages:

            message['id'] = int(
                message.get('id') or 0
            )

            if message.get('created_at'):

                message['created_at'] = (
                    message['created_at']
                    .isoformat(sep=' ')
                )

            if message.get('feedback_at'):

                message['feedback_at'] = (
                    message['feedback_at']
                    .isoformat(sep=' ')
                )

        return jsonify({
            'messages': messages,
            'count': len(messages)
        }), 200

    except Exception as e:

        logger.logDrop(f'Unable to list chat messages: {e}', logger.FAILED)

        return jsonify({
            'error': 'خطا در دریافت پیام‌ها',
            'details': str(e)
        }), 500


# ============================================================
# CHAT STATISTICS
# ============================================================

@admin_app.route('/admin/api/chat/stats', methods=['GET'])
@logger.operation
def admin_chat_stats():
    """
    Get global chat statistics.

    JS calls:
    GET /admin/api/chat/stats
    """

    try:

        conn = get_conn()

        try:

            with conn.cursor() as cur:

                cur.execute(
                    """
                    SELECT

                        COUNT(*) AS total_messages,

                        COUNT(
                            DISTINCT user_id,
                            ip_address
                        ) AS total_sessions,

                        COUNT(
                            DISTINCT ip_address
                        ) AS total_ips,

                        COALESCE(
                            SUM(
                                CASE
                                    WHEN feedback = 'like'
                                    THEN 1
                                    ELSE 0
                                END
                            ),
                            0
                        ) AS likes,

                        COALESCE(
                            SUM(
                                CASE
                                    WHEN feedback = 'dislike'
                                    THEN 1
                                    ELSE 0
                                END
                            ),
                            0
                        ) AS dislikes

                    FROM chat_history
                    """
                )

                row = cur.fetchone()

        finally:
            conn.close()

        if not row:

            return jsonify({
                'total_messages': 0,
                'total_sessions': 0,
                'total_ips': 0,
                'likes': 0,
                'dislikes': 0
            }), 200

        return jsonify({

            'total_messages': int(
                row.get('total_messages') or 0
            ),

            'total_sessions': int(
                row.get('total_sessions') or 0
            ),

            'total_ips': int(
                row.get('total_ips') or 0
            ),

            'likes': int(
                row.get('likes') or 0
            ),

            'dislikes': int(
                row.get('dislikes') or 0
            )

        }), 200

    except Exception as e:

        logger.logDrop(f'Unable to read chat statistics: {e}', logger.FAILED)

        return jsonify({
            'error': 'خطا در دریافت آمار',
            'details': str(e)
        }), 500


# ============================================================
# DELETE CHAT SESSION
# ============================================================

@admin_app.route(
    '/admin/api/chat/sessions',
    methods=['DELETE']
)
@logger.operation
def admin_chat_delete_session():

    try:

        user_id = (
            request.args.get('user_id') or ''
        ).strip()

        ip = (
            request.args.get('ip') or ''
        ).strip()

        if not user_id:

            return jsonify({
                'error': 'پارامتر user_id الزامی است'
            }), 400

        conn = get_conn()

        try:

            with conn.cursor() as cur:

                if ip:

                    cur.execute(
                        """
                        DELETE FROM chat_history
                        WHERE
                            user_id = %s
                            AND ip_address = %s
                        """,
                        (
                            user_id,
                            ip
                        )
                    )

                else:

                    cur.execute(
                        """
                        DELETE FROM chat_history
                        WHERE user_id = %s
                        """,
                        (user_id,)
                    )

                deleted = cur.rowcount

            conn.commit()

        except Exception:

            conn.rollback()
            raise

        finally:
            conn.close()

        if deleted <= 0:

            return jsonify({
                'error': 'گفتگویی یافت نشد'
            }), 404

        return jsonify({
            'ok': True,
            'deleted': deleted
        }), 200

    except Exception as e:

        logger.logDrop(f'Unable to delete a chat session: {e}', logger.FAILED)

        return jsonify({
            'error': 'خطا در حذف گفتگو',
            'details': str(e)
        }), 500


# ============================================================
# DELETE SINGLE MESSAGE
# ============================================================

@admin_app.route(
    '/admin/api/chat/logs',
    methods=['DELETE']
)
@logger.operation
def admin_chat_delete_message():

    try:

        raw_id = request.args.get('id', '0')

        try:
            log_id = int(raw_id)
        except (TypeError, ValueError):

            return jsonify({
                'error': 'پارامتر id نامعتبر است'
            }), 400

        if log_id <= 0:

            return jsonify({
                'error': 'پارامتر id الزامی است'
            }), 400

        conn = get_conn()

        try:

            with conn.cursor() as cur:

                cur.execute(
                    """
                    DELETE FROM chat_history
                    WHERE id = %s
                    """,
                    (log_id,)
                )

                deleted = cur.rowcount

            conn.commit()

        except Exception:

            conn.rollback()
            raise

        finally:
            conn.close()

        if deleted <= 0:

            return jsonify({
                'error': 'پیام یافت نشد'
            }), 404

        return jsonify({
            'ok': True,
            'deleted': log_id
        }), 200

    except Exception as e:

        logger.logDrop(f'Unable to delete a chat message: {e}', logger.FAILED)

        return jsonify({
            'error': 'خطا در حذف پیام',
            'details': str(e)
        }), 500

#00000000000000000000000000000000000000000000000000000000000000000000000000000000000000






if __name__ == '__main__':
    init_db()
    print(f'[admin] http://localhost:{ADMIN_PORT}')
    admin_run()
