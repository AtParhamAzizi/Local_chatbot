from security import configure_security
from abuse_controls import limit_requests
from chat_sessions import chat_owner_hash, configure_chat_sessions
from request_validation import CHAT_FIELDS, FEEDBACK_FIELDS, json_object
from database import get_conn, get_client_ip, logger
from settings import retention_settings, safe_error_message
from serving import serve_development
import os
import numpy as np
from flask import Flask, request, jsonify, redirect, render_template
from generator import generate_reply
from database import get_conn

from interface import embed_texts, BASE_DIR, UI_PORT, MIN_SCORE
from generator import generate_reply
from interface import (
    embed_texts,
    BASE_DIR,
    UI_PORT,
    MIN_SCORE,
)
from voice import configure_voice


FRONTEND_DIR = os.path.join(os.path.dirname(BASE_DIR), 'frontend')
ui_app = Flask('ui', static_folder=FRONTEND_DIR, static_url_path='', template_folder=FRONTEND_DIR)
configure_security(ui_app, service='ui')
configure_chat_sessions(ui_app)
configure_voice(ui_app)





@ui_app.route('/')
@logger.operation
def ui_index():
    policy = retention_settings()
    if policy.chat_days:
        chat_owner_hash(create=True)
    return render_template('index.html', privacy=policy)


@ui_app.route('/index.html')
@logger.operation
def ui_index_alias():
    return redirect('/')


@ui_app.route('/health')
@logger.operation
def health():
    return jsonify({'status': 'ok'})



# def _retrieve_top(query: str, k: int = 5):
#     """Return [(score, row), ...] sorted by cosine similarity (desc)."""
#     qvec = embed_texts([query])[0]

#     conn = get_conn()
#     try:
#         with conn.cursor() as cur:
#             cur.execute(
#                 "SELECT id, question, answer, embedding, embedding_dim "
#                 "  FROM qa_documents "
#                 " WHERE embedding IS NOT NULL"
#             )
#             rows = cur.fetchall()
#     finally:
#         conn.close()

#     scored = []
#     for r in rows:
#         vec = np.frombuffer(r['embedding'], dtype=np.float32)
#         if r['embedding_dim'] and vec.shape[0] != int(r['embedding_dim']):
#             continue
#         if vec.shape[0] != qvec.shape[0]:
#             continue
#         score = float(np.dot(qvec, vec))     # both L2-normalized → cosine
#         scored.append((score, r))

#     scored.sort(key=lambda x: x[0], reverse=True)
#     return scored[:k]


#!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
from searchForAnswer import _search_pdf, _search_qa, build_context

# def _retrieve_top(query: str, k: int = 6):

#     qvec = embed_texts([query])[0]
#     qvec = np.asarray(qvec, dtype=np.float32)

#     qa_results = _search_qa(qvec, k=5)
#     pdf_results = _search_pdf(qvec, k=5)

#     combined = qa_results + pdf_results

#     combined = [
#         r for r in combined
#         if r["score"] >= 0.35
#     ]

#     combined.sort(
#         key=lambda x: x["score"],
#         reverse=True
#     )

    
#     return combined[:k]

@logger.operation
def _retrieve_top(query: str, k: int = 6):
    qvec = np.asarray(embed_texts([query])[0], dtype=np.float32)

    qa_results  = _search_qa(qvec,  k=5)
    pdf_results = _search_pdf(qvec, k=8, query=query)

    combined = [r for r in (qa_results + pdf_results) if r["score"] >= MIN_SCORE]
    combined.sort(key=lambda x: x["score"], reverse=True)

    return combined[:k]
#!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!


@ui_app.route('/chat', methods=['POST'])
@limit_requests('chat')
@logger.operation
def chat():
    data = json_object(CHAT_FIELDS, required=('message',))
    message = data['message']




    policy = retention_settings()
    ip = (get_client_ip() or '') if policy.ip_days else ''





    if not message:
        return jsonify({'error': 'پیام خالی است'}), 400

    owner_hash = chat_owner_hash(create=True) if policy.chat_days else None
    user_id = 'chat_' + owner_hash[:32] if owner_hash else None

    try:
        hits = _retrieve_top(message, k=6)
    except Exception as e:
        logger.logDrop(f'Retrieval failed: {type(e).__name__}', logger.FAILED)
        return jsonify({'error': 'Unable to retrieve an answer. Please try again.'}), 500

    fallback = ('متاسفانه پاسخ دقیقی برای سوالتون پیدا نشد. '
                'برای ارتباط تلفنی می‌توانید با شماره 1649 تماس بگیرید.')

    if not hits:
        logger.logDrop('No matching knowledge-base entries; returning the fallback reply', logger.WARNING)
        reply = fallback
    else:
        try:
            reply = generate_reply(message, hits[:6])
        except Exception as error:
            logger.logDrop(f'Generation failed; using a retrieved answer: {safe_error_message(error)}', logger.FAILED)
            reply = hits[0].get('answer') or hits[0].get('content') or fallback

    recommended = [hit['question'] for hit in hits[1:4] if hit.get('question')]

    message_id = None
    try:
        conn = get_conn() if policy.chat_days else None
        try:
            if conn is not None:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO chat_history
                            (user_id, owner_hash, ip_address, user_message, bot_response, feedback_at, created_at)
                        VALUES (%s, %s, %s, %s, %s, NULL, NOW())
                    """, (user_id, owner_hash, ip, message, reply))
                    message_id = cursor.lastrowid
                conn.commit()
                logger.logDrop('Chat reply saved')
        finally:
            if conn is not None:
                conn.close()
    except Exception as error:
        logger.logDrop(f'Unable to save chat history: {safe_error_message(error)}', logger.FAILED)
        return jsonify({'error': 'Unable to save chat history. Please try again.'}), 503

    return jsonify({
        'message_id': message_id,
        'feedback_enabled': bool(policy.chat_days and policy.feedback_days),
        'reply': reply,
        'recommended_questions': recommended,
        'score': round(hits[0]['score'], 4) if hits else None,
        'sources': [
            {
                'type': h['source_type'],
                'filename': h.get('filename'),
                'pages': [h.get('page_start'), h.get('page_end')]
                    if h['source_type'] == 'pdf' else None,
                'score': round(h['score'], 4),
            }
            for h in hits[:6]
        ],
    })







#+_+_+_+_+_+_+_+_+_++_+_+_+__+_+_+_+_+_+++_+_+_+_+_+_+_+_+_++_++_+__+_+_+_+_+_+_+__+_+_+_+_+_+_++
@ui_app.route('/feedback', methods=['POST'])
@logger.operation
def feedback():
    data = json_object(FEEDBACK_FIELDS, required=('message_id', 'feedback'))

    message_id = data['message_id']
    feedback_value = data['feedback']
    policy = retention_settings()
    if not policy.chat_days or not policy.feedback_days:
        return jsonify({'error': 'Feedback storage is disabled.'}), 403

    owner_hash = chat_owner_hash()
    if owner_hash is None:
        return jsonify({'error': 'A chat session is required.'}), 401

    try:
        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE chat_history
                    SET feedback = %s, feedback_at = NOW()
                    WHERE id = %s AND owner_hash = %s
                """, (feedback_value, message_id, owner_hash))
                if cursor.rowcount == 0:
                    cursor.execute(
                        'SELECT id FROM chat_history WHERE id = %s AND owner_hash = %s',
                        (message_id, owner_hash),
                    )
                    if cursor.fetchone() is None:
                        return jsonify({'error': 'Message not found'}), 404
            conn.commit()
            logger.logDrop('Feedback saved')
        finally:
            conn.close()
    except Exception as error:
        logger.logDrop(f'Unable to save feedback: {safe_error_message(error)}', logger.FAILED)
        return jsonify({'error': 'Unable to save feedback. Please try again.'}), 503

    return jsonify({'status': 'ok', 'message_id': message_id, 'feedback': feedback_value})


 



#+_+_+_+_+_+_+_+_+_++_+_+_+___+_+_+_+_+_+++_+_+_+_+_+_+_+_+_++_++_+__+_+_+_+_+_+_+__+_+_+_+_+_+_++










@logger.operation
def user_run():
    logger.logDrop(f'Starting the user service on port {UI_PORT}')
    serve_development(ui_app, UI_PORT)





if __name__ == '__main__':
    print(f'[ui] http://localhost:{UI_PORT}')
    user_run()
