# import pymysql, pymysql.cursors
# import os
# from database import get_conn
# conn = get_conn()
# # 
# # 
# # = {
# #     'host':     os.getenv('DB_HOST', 'localhost'),
# #     'port':     int(os.getenv('DB_PORT', 3306)),
# #     'user':     os.getenv('DB_USER', 'root'),
# #     'password': os.getenv('DB_PASS', '159632@qw'),
# #     'database': os.getenv('DB_NAME', 'chatbot_db'),
# #     'charset':  'utf8mb4',
# #     'cursorclass': pymysql.cursors.DictCursor,
# #     'autocommit': True,
# # }
# with conn.cursor() as cur:
#     cur.execute("""
#         SELECT COUNT(*) AS total,
#                SUM(embedding IS NOT NULL) AS with_emb,
#                MIN(embedding_dim) AS min_dim,
#                MAX(embedding_dim) AS max_dim
#         FROM pdf_chunks
#     """)
#     print(cur.fetchone())

#     cur.execute("""
#         SELECT id, filename, status FROM pdf_documents
#     """)
#     for r in cur.fetchall():
#         print(r)
# conn.close()

from database import get_conn
import numpy as np
from interface import embed_texts
from user import _retrieve_top

# 1. embedding model
qvec = np.asarray(embed_texts(["تست"])[0], dtype=np.float32)
print("qvec:", qvec.shape, qvec.dtype, "norm:", np.linalg.norm(qvec))

# 2. retrieval
hits = _retrieve_top("تست", k=6)
print("hits:", len(hits))
for h in hits:
    print(f"  {h['source_type']:>3}  {h['score']:.4f}  "
          f"{(h.get('question') or h.get('content') or '')[:60]}")