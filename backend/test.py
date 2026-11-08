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
