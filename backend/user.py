from database import get_conn, get_client_ip
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
Loggerlevel = 'user'


ui_app = Flask('ui', static_folder=BASE_DIR, static_url_path='')
CORS(ui_app, origins=["*"])





@ui_app.route('/')
def ui_index():
    return send_from_directory(BASE_DIR, 'index.html')


@ui_app.route('/health')
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

def _retrieve_top(query: str, k: int = 6):
    qvec = np.asarray(embed_texts([query])[0], dtype=np.float32)

    qa_results  = _search_qa(qvec,  k=5)
    pdf_results = _search_pdf(qvec, k=8, query=query)

    combined = [r for r in (qa_results + pdf_results) if r["score"] >= MIN_SCORE]
    combined.sort(key=lambda x: x["score"], reverse=True)

    return combined[:k]
#!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!


@ui_app.route('/chat', methods=['POST'])
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
        return jsonify({
            'reply': fallback,
            'recommended_questions': [],
        })

    top_row = hits[0]
    top_score = top_row['score']


    context_results = hits[:6]

    try:
        reply = generate_reply(message, context_results)
    except Exception as e:
        # Fallback to retrieved answer if generation fails
        print('[chat] generation failed:', e)
        reply = top_row.get('answer') or top_row.get('content') or fallback

    recommended = [h['question'] for h in hits[1:4] if h.get('question')]

#+_+_+_+_+_+_+_+_+_++_+_+_+___+_+_+_+_+_+++_+_+_+_+_+_+_+_+_++_++_+__+_+_+_+_+_+_+__+_+_+_+_+_+_++

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO chat_history (user_id, ip_address, user_message, bot_response, created_at)
        VALUES (%s, %s, %s, %s, NOW())
    """, (user_id, ip, message, reply))
    conn.commit()
    message_id = cursor.lastrowid
    cursor.close()
    conn.close()


#+_+_+_+_+_+_+_+_+_++_+_+_+___+_+_+_+_+_+++_+_+_+_+_+_+_+_+_++_++_+__+_+_+_+_+_+_+__+_+_+_+_+_+_++


    return jsonify({
        'reply': reply,
        'recommended_questions': recommended,
        'score': round(top_score, 4),
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
def feedback():
    data = request.get_json()
    message_id = data.get('message_id')
    feedback_value = data.get('feedback')

    if feedback_value not in ('like', 'dislike'):
        return jsonify({'error': 'Invalid feedback'}), 400

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE chat_history
        SET feedback = %s, feedback_at = NOW()
        WHERE id = %s
    """, (feedback_value, message_id))
    conn.commit()
    affected = cursor.rowcount
    cursor.close()
    conn.close()

    if affected == 0:
        return jsonify({'error': 'Message not found'}), 404

    return jsonify({'status': 'ok'})


 



#+_+_+_+_+_+_+_+_+_++_+_+_+___+_+_+_+_+_+++_+_+_+_+_+_+_+_+_++_++_+__+_+_+_+_+_+_+__+_+_+_+_+_+_++










def user_run():
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
