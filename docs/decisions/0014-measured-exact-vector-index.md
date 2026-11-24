# 0014: Measured exact vector indexing with MySQL authority

Date: 2026-11-16
Status: Implemented

## Context

Retrieval decoded and validated every BLOB and computed each cosine in Python
on every query. F039 needs every compatible semantic score before common-corpus
lexical fusion; a semantic shortlist could lose lexical winners. MySQL already
owns publication, profiles and F035 resumable embedding generations.

## Decision

Introduce `RetrievalIndex.scores` with scalar and exact flat NumPy implementations.
Default `auto` uses cached immutable matrices from 1,000 eligible rows per
collection, within the qualified 50,000-row and 512 MiB vector-payload limits.
Keep MySQL as persistent authority. Cache identity compares actual IDs/bytes,
full profile and collection; serialize complete snapshot publication. Small or
oversized snapshots, allocation failure and explicit rollback use scalar scoring.
There is no new service, schema or dependency.

Predeclared gates require recall@6=1.0, cosine error ≤0.000002, warm snapshot-plus-
scoring p95 ≤150 ms, ≥2× speedup from 1,000 rows and cold/edit/delete/restart
rebuilds ≤5 seconds through 50,000 vectors of dimension 1024. The recorded local
CPU passes every gate. Cached real local vectors preserve all F039 development
and heldout result IDs/orders. See [requirements and evidence](../vector-search-scaling.md).

## Alternatives and consequences

The scalar scan repeats decoding/validation. The exact matrix avoids that cost
while preserving full-score fusion. It still scans exhaustively. HNSW/FAISS
requires separate candidate-recall evidence and a lexical candidate route;
changing the database would not itself satisfy those requirements.

Cache payloads and view count are bounded; total RSS and in-flight snapshots
are outside that bound. SQL, filtering, lexical fusion, embedding, generation
and concurrency need deployment measurement. Frequent writes rebuild a complete
view. Revisit this choice above the qualified envelope or when budgets fail.

## Recovery and verification

Restart rebuilds from MySQL bytes; there is no disk index to restore. Set
`VECTOR_SEARCH_BACKEND=scalar` and restart for rollback. Stored embeddings,
profiles and F035 generation pointers remain intact.

Offline regressions cover invalid/incompatible vectors, parent guards,
migrated/base vectors, edits/deletes, profiles, mutable buffers, concurrent
publication, eviction and allocation fallback. Benchmarks record timing samples
and source identities. Historical F039/F040 artifacts remain unchanged, with
F041 providing current-code equivalence evidence.
