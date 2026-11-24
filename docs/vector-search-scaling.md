# Measured vector-search scaling (F041)

MySQL remains authoritative for knowledge, embedding bytes, profiles and F035
generation pointers. `services/vector_index.py` supplies a `RetrievalIndex.scores`
interface returning every eligible source's cosine score. Implementations are
the preceding per-vector `ScalarIndex` and an immutable NumPy `FlatIndex`.
The flat index caches decoding, validation and norms, then computes exact
exhaustive inner products over a contiguous float32 matrix. Query work remains
O(rows × dimension); this is an exact flat vector index, with no ANN, new
database service, schema, dependency or model changes.

## Declared acceptance envelope

These gates were declared in `backend/vector_index_eval.py` before measurement:

| Requirement | Gate |
| --- | --- |
| Eligible corpus | Up to 50,000 vectors at dimension 1024; measured at 30, 1,000, 10,000 and 50,000 rows |
| Recall | Recall@6 = 1.0 against the scalar exact scan for every measured query |
| Accuracy | Maximum absolute cosine difference ≤ 0.000002 |
| Warm latency | p95 ≤ 150 ms, including fresh-byte snapshot comparison and full score materialization |
| Benefit at scale | p95 speedup ≥ 2× at every measured size from 1,000 rows |
| Rebuild | Cold, 1% vector edit, deletion and cache restart each ≤ 5 seconds |
| Resident vector cache | At most two snapshots and 512 MiB of retained BLOB, matrix and norm payloads |

This is a **single-request vector-component budget**. SQL transfer/deserialization,
profile filtering, source text, lexical indexing/fusion, final sorting, embedding,
generation and HTTP are outside timing. SQL still fetches the eligible embeddings
and each request still checks compatibility. These results establish neither an
end-to-end chat SLO nor concurrent throughput. Placing all 50,000 rows in one
collection covers the worst collection size for a combined corpus of that size.

`VECTOR_SEARCH_BACKEND=auto` selects indexing at **1,000 eligible rows per
collection**. Smaller collections use scalar scoring, avoiding a retained matrix
for a small workload. `scalar` restores the preceding scoring implementation;
`flat` exercises indexing below the crossover. All modes retain the row/byte
limits: oversized snapshots or matrix allocation failure use the exact scalar
path, which preserves correctness but carries no latency guarantee. Change the
process environment and restart to apply an operational rollback.

## Measurement and replay

[The committed report](evaluations/f041-vector-index.json) records environment
and source fingerprints, deterministic corpus checksums, all 24 timing samples
per size, recall/error results and rebuild times. Synthetic vectors use up to
128 clusters, independent noise, varied norms and exact duplicates; queries
include cluster-related and independent vectors. New BLOB objects are constructed
before each timed lookup, matching SQL's fresh-byte behavior rather than Python
object identity. Corpus construction and that simulated deserialization are
outside timing. Both kernels materialize all scores. Top-six sorting is outside
timing; speedup compares scalar scoring with indexed snapshot checks plus scoring.

All declared gates pass on the recorded Windows 11, CPython 3.13.14, NumPy 2.5.4,
eight-logical-CPU AMD environment. Exact timings are in the JSON. Hardware,
scheduling and memory bandwidth affect latency; this qualifies that environment,
not every deployment platform. Snapshot reuse avoids allocating another tuple
per immutable input record while still copying mutable adapter buffers.

The report also replays independently prepared, actual local
Qwen3-Embedding-0.6B vectors for all 30 development and 30 heldout Persian
questions. Complete profile/dataset identities are checked. Scalar and indexed
scores pass through the shipped F039 fusion; every admitted result ID and its
order matches both scalar scoring and the frozen F039 results. Each source kind's
recall is preserved. Existing F039/F040 artifacts retain their historical source
hashes; their tests allow the replaced search adapter's historical hash while
F041 verifies the current adapter/index/fusion fingerprints and equivalence.
This is regression replay with no tuning or new relevance claim. F037 synthetic
labels, pending human review and known abstention limitations still apply.

Reproduce the synthetic envelope without models, MySQL or network:

```powershell
.\.venv-tests\Scripts\python.exe -B backend/vector_index_eval.py --output backend/.runtime/f041-scaling.json
```

Include the independently prepared split caches from the
[hybrid evaluation procedure](hybrid-retrieval.md) for quality replay:

```powershell
.\.venv-tests\Scripts\python.exe -B backend/vector_index_eval.py --quality-vectors backend/.runtime/f038 --output backend/.runtime/f041-replay.json
```

Use `--sizes`, `--dimension`, `--queries` and `--seed` for exploration. Incomplete
envelopes and failed gates write reports but exit nonzero and cannot report
overall acceptance. CI checks correctness and committed evidence; it does not
enforce live wall-clock gates on shared runners.

## Safety, rebuild and capacity

Both SQL adapters retain completed-document, complete-profile, parent-dimension
and F035 live-source-hash guards. Compatible base vectors from new uploads remain
visible. Eligible `(source ID, embedding bytes)` records enter snapshots keyed
by collection, complete profile and implementation version. Invalid, nonfinite
and zero vectors stay excluded before lexical fusion. Table-local IDs are
isolated by collection.

Every lookup compares sorted IDs and **actual bytes**. Adds, edits, deletes,
publication and generation changes select a matching view immediately; there is
no TTL or timestamp heuristic. Current MySQL answer/text/citation metadata is
used even when unchanged vectors reuse an index. SQL row order does not rebuild
the index. Mutable adapter buffers are copied. Builders serialize and publish
complete immutable snapshots; existing readers retain their prior view. Failed
allocation scores the current rows through scalar fallback, never a stale view.

Snapshots are disposable and in memory only. First use after restart, mutation
or eviction rebuilds synchronously from MySQL BLOBs, without inference, downloads
or disk-index recovery. Even a one-vector edit rebuilds that collection; frequent
ingestion needs separate cold-path capacity measurement. F035 model re-embedding
and cutover remain independent.

A 50,000 × 1024 snapshot retains 410,000,000 bytes (about 391 MiB) of vector
payloads. The 512 MiB LRU budget counts BLOBs, full matrix allocation and norms,
not Python overhead, source text, lexical caches or total process RSS. Old readers,
SQL rows and a replacement under construction can exceed the resident-cache
budget. Provision additional RAM alongside the local models; this is not a
process peak-memory cap.

Above 50,000 vectors, or when target hardware misses a gate, measure production
vectors, SQL transfer, mutation rate and concurrent requests before increasing
limits. Consider HNSW/FAISS and a durable change feed then. A semantic top-k
shortlist cannot directly replace this interface: F039 requires the complete
eligible lexical corpus and a route for lexical winners outside semantic top-k.
Any replacement needs measured candidate/fused recall and rebuild/recovery
gates. MySQL should continue owning identities and publication unless a separate
decision changes that.
