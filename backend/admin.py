from security import configure_security
import os
import time
import re
from pathlib import Path

import fitz
import numpy as np
from flask import request, jsonify, redirect, url_for, session, render_template_string, g

from flask import Flask, request, jsonify, send_from_directory
from database import get_conn, init_db,hash_question, logger, sha256_bytes
from settings import admin_session_config, is_development, session_secret, safe_error_message
from pymysql.err import IntegrityError
from serving import serve_development
from admin_users import authenticate, create_user, update_user, serialize_user
from admin_sessions import create_admin_session, load_admin_session, revoke_admin_session
from admin_csrf import csrf_token, valid_csrf_token
from abuse_controls import limit_requests, retry_message
from admin_policy import OPERATION_POLICIES, PUBLIC, allowed_pages, permits, request_operation
from request_validation import (
    CONVERSATION_FIELDS, MAX_ANSWER_CHARS, MAX_BIGINT, MAX_IMPORT_ROWS, MAX_JSON_UPLOAD_BYTES,
    MAX_MESSAGE_CHARS, MAX_PDF_CHUNKS, MAX_PDF_PAGES, MAX_PDF_TEXT_CHARS,
    MAX_PDF_UPLOAD_BYTES, PAGE_FIELDS, USER_FIELDS, USER_UPDATE_FIELDS, InvalidRequest, bounded_ids,
    form_object, json_object, parse_json, query_integer, query_schema, read_upload,
    text, validate_object,
)
from werkzeug.exceptions import RequestEntityTooLarge
# from logger import logDrop


from generator import set_generator_model
from model_registry import get_active_model_id, get_model_catalog, set_active_model

from interface import (
    embed_texts,
    embedding_dim,
    set_embedding_model,
    ADMIN_DIR,
    ADMIN_PORT,
    ALLOWED_EXT,
)



admin_app = Flask('admin', static_folder=None)
admin_app.secret_key = session_secret()
admin_app.config.update(admin_session_config())
configure_security(admin_app, service='admin')
ADMIN_USERS_DIR = os.path.join(os.path.dirname(ADMIN_DIR), 'admin-users')

@admin_app.route('/', methods=['GET'])
@logger.operation
def admin_index():
    if not g.admin_user:
        return redirect(url_for('admin_login'))
    return send_from_directory(os.path.dirname(ADMIN_DIR), 'dashboard.html')


@admin_app.before_request
def require_admin_session():
    g.admin_user = None
    if not is_development() and not request.is_secure:
        return jsonify({'error': 'HTTPS is required for administrator access.'}), 400
    operation = request_operation(request.endpoint, request.method, request.path, request.view_args)
    if operation == 'login.assets':
        return None
    current = load_admin_session(session.get('admin_session'))
    if not current and ('admin_session' in session or 'admin_user' in session):
        session.clear()
    g.admin_user = current
    if not current and OPERATION_POLICIES.get(operation) != PUBLIC:
        if request.path.startswith('/admin/api/') or request.endpoint == 'admin_logout':
            return jsonify({'error': 'Authentication required.'}), 401
        return redirect(url_for('admin_login'))
    if request.routing_exception is not None:
        return None
    if request.method not in {'GET', 'HEAD', 'OPTIONS'} and not valid_csrf_token():
        return jsonify({'error': 'Invalid or missing CSRF token. Reload the page and try again.'}), 403
    if not permits(current, operation):
        return jsonify({'error': 'You do not have access to this operation.'}), 403
    return None


@admin_app.after_request
def protect_admin_response(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


def login_throttled(retry_after):
    return render_template_string(
        Path(os.path.join(ADMIN_USERS_DIR, 'login.html')).read_text(encoding='utf-8'),
        error=retry_message(retry_after),
        csrf_token=csrf_token(),
    )


@admin_app.route('/login', methods=['GET', 'POST'])
@limit_requests('login', methods=('POST',), on_limit=login_throttled)
def admin_login():
    if request.method == 'POST':
        data = form_object({
            'username': text(64, 1), 'password': text(1024, 1, strip=False), 'csrf_token': text(256, 1),
        }, required=('username', 'password'))
        username = data['username']
        password = data['password']
        user = authenticate(username, password)
        if user:
            token = create_admin_session(user)
            revoke_admin_session(session.get('admin_session'))
            session.clear()
            session.permanent = True
            session['admin_session'] = token
            return redirect(url_for('admin_index'))
        return render_template_string(
            Path(os.path.join(ADMIN_USERS_DIR, 'login.html')).read_text(encoding='utf-8'),
            error='Invalid username or password.',
            csrf_token=csrf_token(),
        ), 401
    if g.admin_user:
        return redirect(url_for('admin_index'))
    return render_template_string(
        Path(os.path.join(ADMIN_USERS_DIR, 'login.html')).read_text(encoding='utf-8'),
        error='',
        csrf_token=csrf_token(),
    )


@admin_app.route('/logout', methods=['POST'])
def admin_logout():
    revoke_admin_session(session.get('admin_session'))
    session.clear()
    return redirect(url_for('admin_login'))


@admin_app.route('/admin/api/session')
def admin_session():
    return jsonify({'user': g.admin_user, 'csrf_token': csrf_token(), 'allowed_pages': allowed_pages(g.admin_user)})


@admin_app.route('/admin/api/admin-users', methods=['GET', 'POST'])
def admin_users_collection():
    if request.method == 'POST':
        data = json_object(USER_FIELDS, required=('username', 'password'))
        try:
            user_id = create_user(
                data['username'], data['password'], data.get('permissions'), data.get('is_admin', False)
            )
            return jsonify({'ok': True, 'id': user_id}), 201
        except ValueError:
            return jsonify({'error': 'Invalid account details.'}), 400
        except IntegrityError as error:
            if error.args and error.args[0] == 1062:
                return jsonify({'error': 'That username already exists.'}), 409
            raise
        except Exception:
            return jsonify({'error': 'Unable to create user.'}), 500
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM `admin-users` ORDER BY username')
            users = [serialize_user(row) for row in cur.fetchall()]
    finally:
        conn.close()
    return jsonify({'users': users, 'current_user_id': g.admin_user['id']})


@admin_app.route('/admin/api/admin-users/<int:user_id>', methods=['PATCH', 'DELETE'])
@bounded_ids(user_id=2147483647)
def admin_user_item(user_id):
    data = json_object(USER_UPDATE_FIELDS, nonempty=True) if request.method == 'PATCH' else None
    current = g.admin_user or {}
    if request.method == 'DELETE' and user_id == current.get('id'):
        return jsonify({'error': 'You cannot delete your own account.'}), 400
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM `admin-users` WHERE id = %s', (user_id,))
            row = cur.fetchone()
            if not row:
                return jsonify({'error': 'User not found.'}), 404
            if request.method == 'DELETE':
                cur.execute('DELETE FROM `admin-users` WHERE id = %s', (user_id,))
                conn.commit()
                return jsonify({'ok': True})
            changed = update_user(
                user_id,
                password=data.get('password'),
                permissions=data.get('permissions'),
                is_active=data.get('is_active'),
                is_admin=data.get('is_admin'),
            )
            if changed and user_id == current.get('id'):
                session.clear()
    except ValueError:
        return jsonify({'error': 'Invalid account details.'}), 400
    finally:
        conn.close()
    return jsonify({'ok': True})


@admin_app.route('/login.css')
def login_css():
    return send_from_directory(ADMIN_USERS_DIR, 'login.css')


@admin_app.route('/login.js')
def login_js():
    return send_from_directory(ADMIN_USERS_DIR, 'login.js')

@admin_app.route('/dashboard.css')
@admin_app.route('/dashboard.js')
@admin_app.route('/theme.js')
@admin_app.route('/admin-csrf.js')
def dashboard_asset():
    return send_from_directory(os.path.dirname(ADMIN_DIR), request.path.lstrip('/'))


@admin_app.route('/admin/<path:filename>')
def admin_page_asset(filename):
    return send_from_directory(ADMIN_DIR, filename)



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
    data = json_object({'model_id': text(64, 1)}, required=('model_id',))
    model_id = data['model_id']

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
        return jsonify({'error': 'Selected model files are unavailable.'}), 409
    except ValueError as exc:
        return jsonify({'error': 'Invalid model selection.'}), 400
    except Exception as exc:
        return jsonify({'error': 'Unable to change model.'}), 500
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
@limit_requests('upload')
@logger.operation
def admin_upload():
    logger.logDrop('Received a knowledge-base JSON upload')
    started = time.time()

    raw, filename, replace = read_upload(ALLOWED_EXT, MAX_JSON_UPLOAD_BYTES)
    payload = parse_json(raw)
    if not isinstance(payload, list) or not payload:
        raise InvalidRequest('The uploaded JSON must be a non-empty array of question/answer objects.')
    if len(payload) > MAX_IMPORT_ROWS:
        raise RequestEntityTooLarge()

    rows, seen = [], set()
    for item in payload:
        item = validate_object(item, {
            'question': text(MAX_MESSAGE_CHARS, 1), 'answer': text(MAX_ANSWER_CHARS, 1),
        }, required=('question', 'answer'))
        q, a = item['question'], item['answer']
        h = hash_question(q)
        if h in seen:
            continue
        seen.add(h)
        rows.append((q, a, h))


    try:
        vectors = embed_texts([r[0] for r in rows])
    except Exception as e:
        return jsonify({'error': 'Request failed.'}), 500

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
    return jsonify({
        'received':     len(payload),
        'inserted':     inserted,
        'updated':      updated,
        'skipped':      skipped,
        'embedded':     inserted + updated,
        'duration_sec': round(time.time() - started, 2),
        'errors':       [],
    })



@admin_app.route('/admin/api/documents')
@query_schema(PAGE_FIELDS)
@logger.operation
def admin_documents():
    page = g.validated_query.get('page', 1)
    per_page = g.validated_query.get('per_page', 10)
    q = g.validated_query.get('q', '')
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




PDF_ALLOWED_EXT = {'.pdf'}

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
@limit_requests('upload')
@logger.operation
def admin_pdf_upload():

    started = time.time()
    raw, filename, replace = read_upload(PDF_ALLOWED_EXT, MAX_PDF_UPLOAD_BYTES)



    try:
        pdf = fitz.open(stream=raw, filetype='pdf')
    except Exception as e:
        return jsonify({
            'error': 'Request failed.'
        }), 400

    pdf_page_count = pdf.page_count

    if pdf_page_count > MAX_PDF_PAGES:
        pdf.close()
        raise RequestEntityTooLarge()

    if pdf.page_count == 0:
        pdf.close()

        return jsonify({
            'error': 'فایل PDF هیچ صفحه‌ای ندارد'
        }), 400

    pages = []
    total_text_chars = 0

    try:

        for page_number in range(pdf.page_count):

            page = pdf.load_page(page_number)

            text = page.get_text("text", sort=True)

            total_text_chars += len(text)
            if total_text_chars > MAX_PDF_TEXT_CHARS:
                raise RequestEntityTooLarge()
            text = text.strip()

            if text:
                pages.append({
                    'page': page_number + 1,
                    'text': text
                })

    except RequestEntityTooLarge:
        raise
    except Exception as e:
        return jsonify({'error': 'Unable to extract PDF text.'}), 400

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
            if len(chunks) >= MAX_PDF_CHUNKS:
                raise RequestEntityTooLarge()
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

    document_hash = sha256_bytes(raw)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT id, filename, status FROM pdf_documents WHERE document_hash = %s',
                (document_hash,),
            )
            existing = cur.fetchone()
    finally:
        conn.close()
    if existing and not replace:
        return jsonify({
            'success': False, 'duplicate': True, 'message': 'This PDF has already been uploaded.',
            'document_id': existing['id'], 'filename': existing['filename'], 'status': existing['status'],
        }), 409

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
            'error': 'Request failed.'
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
                            'pdf_processing_failed',
                            document_id
                        )
                    )

            conn.commit()

        except Exception:
            pass

        return jsonify({
            'error': 'Request failed.'
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
@query_schema(PAGE_FIELDS)
@logger.operation
def admin_pdf_documents():

    page = g.validated_query.get('page', 1)
    per_page = g.validated_query.get('per_page', 10)
    q = g.validated_query.get('q', '')
    offset = (page - 1) * per_page

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
    serve_development(admin_app, ADMIN_PORT)






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

        logger.logDrop(f'Unable to read application logs: {safe_error_message(e)}', logger.FAILED)

        return jsonify({
            "success": False,
            "message": "Unable to read application logs.",
            "logs": []
        }), 500

    finally:
        if conn:
            conn.close()











#00000000000000000000000000000000000000000000000000000000000000000000000000000000000000


from flask import request, jsonify, redirect, url_for, session, render_template_string

# ============================================================
# ADMIN CHAT API
# ============================================================

@admin_app.route('/admin/api/chat/sessions', methods=['GET'])
@query_schema({'limit': query_integer(1, 1000), 'q': text(256)})
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

        logger.logDrop(f'Unable to list chat sessions: {safe_error_message(e)}', logger.FAILED)

        return jsonify({
            'error': 'خطا در دریافت گفتگوها',
            'details': 'Request failed.'
        }), 500




@admin_app.route('/admin/api/chat/messages', methods=['GET'])
@query_schema(CONVERSATION_FIELDS, required=('user_id',))
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

        logger.logDrop(f'Unable to list chat messages: {safe_error_message(e)}', logger.FAILED)

        return jsonify({
            'error': 'خطا در دریافت پیام‌ها',
            'details': 'Request failed.'
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

        logger.logDrop(f'Unable to read chat statistics: {safe_error_message(e)}', logger.FAILED)

        return jsonify({
            'error': 'خطا در دریافت آمار',
            'details': 'Request failed.'
        }), 500


# ============================================================
# DELETE CHAT SESSION
# ============================================================

@admin_app.route(
    '/admin/api/chat/sessions',
    methods=['DELETE']
)
@query_schema(CONVERSATION_FIELDS, required=('user_id',))
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

        logger.logDrop(f'Unable to delete a chat session: {safe_error_message(e)}', logger.FAILED)

        return jsonify({
            'error': 'خطا در حذف گفتگو',
            'details': 'Request failed.'
        }), 500


# ============================================================
# DELETE SINGLE MESSAGE
# ============================================================

@admin_app.route(
    '/admin/api/chat/logs',
    methods=['DELETE']
)
@query_schema({'id': query_integer(1, MAX_BIGINT)}, required=('id',))
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

        logger.logDrop(f'Unable to delete a chat message: {safe_error_message(e)}', logger.FAILED)

        return jsonify({
            'error': 'خطا در حذف پیام',
            'details': 'Request failed.'
        }), 500

#00000000000000000000000000000000000000000000000000000000000000000000000000000000000000






if __name__ == '__main__':
    init_db()
    print(f'[admin] http://localhost:{ADMIN_PORT}')
    admin_run()
