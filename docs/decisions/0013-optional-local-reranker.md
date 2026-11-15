# Optional bounded local reranking

Date: 2026-11-15. Status: implemented. Extends
[0012](0012-comparable-source-ranking.md).

## Context

Common-scale fusion makes source types comparable but does not jointly read the
query and passage. A cross-encoder can improve evidence order, with additional
model storage, resident memory, latency and contention on small machines.

## Decision

Keep fusion as the default. Compose an optional reranking retrieval wrapper in
the UI application factory, preserving injected services and the frozen F039
implementation. A named CUDA quality profile uses a pinned local
Qwen3-Reranker-0.6B snapshot with bounded candidates, batches and tokens. Keep
weights in NF4 and computation in FP32; score six fixed-width pairs in batches of three.
Select weighted rank fusion only on development data; require quality and resource gates before
the explicit heldout comparison. Retain actual scores, runtime identities,
source hashes, memory and latency samples in reviewable reports.

Factories share one lazy runtime and a nonblocking inference lock. Busy or failed
models return first-stage results. Failures disable reranking until restart.
The default `off` path allocates no additional model memory. Model preparation
is an explicit operator action; serving remains offline with safe local weights.

## Alternatives and consequences

Always-on reranking would spend scarce resources even without measured gains.
An external inference service changes the privacy/deployment boundary. Loading
one model per app factory duplicates weights in the shared serving process.
The chosen option preserves the existing process, database, embedding and HTTP
schemas. Existing score fields describe first-stage similarity while result
order reflects weighted reciprocal rank fusion. A fixed bounded model operation has no
hard timeout. A benchmark on short synthetic excerpts cannot establish maximum
generation-context capacity or production relevance.

Rollback sets `RERANKER_PROFILE=off` and restarts; no data migration is needed.
Historical `hybrid`/`legacy` modes bypass reranking. See
[runtime behavior, limits, benchmark and reproduction](../local-reranker.md),
`backend/tests/test_local_reranker.py`, and the linked evaluation artifacts.
