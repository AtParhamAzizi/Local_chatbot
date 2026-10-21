from database import get_conn, get_client_ip, logger
import os
import numpy as np
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
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
ui_app = Flask('ui', static_folder=FRONTEND_DIR, static_url_path='')
CORS(ui_app, origins=["*"])
configure_voice(ui_app)





@ui_app.route('/')
@logger.operation
def ui_index():
    return send_from_directory(FRONTEND_DIR, 'index.html')


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
@logger.operation
def chat():
    data    = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()




    user_id =data.get('user_id', 'anonymous')
    ip = get_client_ip()





    if not message:
        return jsonify({'error': 'پیام خالی است'}), 400

    try:
        hits = _retrieve_top(message, k=6)
    except Exception as e:
        return jsonify({'error': f'خطا در بازیابی: {e}'}), 500

    fallback = ('متاسفانه پاسخ دقیقی برای سوالتون پیدا نشد. '
                'برای ارتباط تلفنی می‌توانید با شماره 1649 تماس بگیرید.')

    if not hits:
        logger.logDrop('No matching knowledge-base entries; returning the fallback reply', logger.WARNING)
        reply = fallback
    else:
        try:
            reply = generate_reply(message, hits[:6])
        except Exception as error:
            logger.logDrop(f'Generation failed; using a retrieved answer: {error}', logger.FAILED)
            reply = hits[0].get('answer') or hits[0].get('content') or fallback

    recommended = [hit['question'] for hit in hits[1:4] if hit.get('question')]

    try:
        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO chat_history
                        (user_id, ip_address, user_message, bot_response, feedback_at, created_at)
                    VALUES (%s, %s, %s, %s, NULL, NOW())
                """, (user_id, ip, message, reply))
                message_id = cursor.lastrowid
            conn.commit()
            logger.logDrop(f'Chat reply saved with message_id={message_id}')
        finally:
            conn.close()
    except Exception as error:
        logger.logDrop(f'Unable to save chat history: {error}', logger.FAILED)
        return jsonify({'error': 'Unable to save chat history. Please try again.'}), 503

    return jsonify({
        'message_id': message_id,
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
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'A JSON object is required'}), 400

    message_id = data.get('message_id')
    feedback_value = data.get('feedback')

    if type(message_id) is not int or not 0 < message_id <= 9223372036854775807:
        return jsonify({'error': 'message_id must be a positive integer'}), 400

    if feedback_value not in ('like', 'dislike'):
        return jsonify({'error': 'Invalid feedback'}), 400

    try:
        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE chat_history
                    SET feedback = %s, feedback_at = NOW()
                    WHERE id = %s
                """, (feedback_value, message_id))
                if cursor.rowcount == 0:
                    cursor.execute('SELECT id FROM chat_history WHERE id = %s', (message_id,))
                    if cursor.fetchone() is None:
                        return jsonify({'error': 'Message not found'}), 404
            conn.commit()
            logger.logDrop(f'Feedback saved: message_id={message_id}, feedback={feedback_value}')
        finally:
            conn.close()
    except Exception as error:
        logger.logDrop(f'Unable to save feedback: {error}', logger.FAILED)
        return jsonify({'error': 'Unable to save feedback. Please try again.'}), 503

    return jsonify({'status': 'ok', 'message_id': message_id, 'feedback': feedback_value})


 



#+_+_+_+_+_+_+_+_+_++_+_+_+___+_+_+_+_+_+++_+_+_+_+_+_+_+_+_++_++_+__+_+_+_+_+_+_+__+_+_+_+_+_+_++










@logger.operation
def user_run():
    logger.logDrop(f'Starting the user service on port {UI_PORT}')
    ui_app.run(
        host='0.0.0.0',
        port=UI_PORT,
        debug=True,
        use_reloader=False,
        threaded=True,
    )





if __name__ == '__main__':
    print(f'[ui] http://localhost:{UI_PORT}')
    user_run()
