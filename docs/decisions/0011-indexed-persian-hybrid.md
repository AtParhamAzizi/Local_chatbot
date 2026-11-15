# 0011: Evaluated Persian indexed hybrid retrieval

Date: 2026-11-12

Status: Implemented; default scoring/merge superseded by
[0012](0012-comparable-source-ranking.md). Retained as the `hybrid` rollback strategy.

## Context

Q&A used only cosine similarity; PDF/document scoring retokenized every candidate
to compute unique-term overlap. A deliberate lexical strategy must cover both
collections, respect embedding compatibility/publication and justify Persian
tokenization through development measurements and heldout acceptance.

## Decision

Use immutable in-process inverted BM25 indexes and the shared Persian normalizer.
Compare word tokens, filtered words and character trigrams on F037 development
questions only. Freeze policy before heldout access. The 36-candidate comparison
selected unfiltered word tokens, lexical weight 0.2 and saturation scale 8:
`0.8 * cosine + 0.2 * BM25/(BM25+8)`. Q&A indexes question and answer; documents
index content. BM25 uses `k1=1.2`, `b=0.75`, with separate collection statistics.
Candidate counts, minimum score and final result limit retain existing values.

Build only after full profile/dimension/vector validation. Cache by exact eligible
source tuples, collection, profile and tokenizer mode/version. Serialize builds,
publish complete snapshots and retain immutable old-reader references. A two-view
LRU holds the current Q&A/document views in the ordinary case. Source changes and
embedding migration/rollback naturally choose new views without write hooks.

`RETRIEVAL_STRATEGY=legacy` plus restart restores prior scoring. There is no schema
or model migration and no new dependency/service. The F037 CLI and its committed
baselines remain frozen references; `hybrid_eval.py` records F038 selection and
heldout quality/latency separately.

## Alternatives and consequences

SQL full-text search would introduce server/tokenization configuration and index
maintenance into this feature. A separate search service adds deployment and
publication coordination. The present single-process application already fetches
all compatible vectors; an in-process index reuses that inventory and makes its
tokenizer directly testable. Continuing overlap would leave Q&A without lexical
evidence and documents repeatedly retokenized.

This index accelerates lexical scoring, not dense retrieval. SQL fetch, cosine
scans and corpus snapshot comparison remain linear. The cache bounds views, not
bytes, and cold builds occur on changes/restart/eviction. Concurrent requests may
retain evicted views. Measure memory and full request latency on larger real
corpora before replacing this boundary with persistent indexes or vector ANN.

## Verification and limitations

Actual local Qwen3-Embedding-0.6B CPU vectors produced heldout recall@6
0.8958333333 -> 0.9583333333 and nDCG@6 0.8564058556 -> 0.8881109033. Frozen
quality and scoring/index latency gates passed at 30/3,000 rows. Timings exclude
model inference and DB I/O; the larger benchmark duplicates source text. Both
policies still return false positives for all six unanswerable cases. Sources
are synthetic and agent-reviewed; independent human/domain review is pending.
See [selection, results, budgets and replay](../hybrid-retrieval.md).

[Offline tests](../../backend/tests/test_hybrid_retrieval.py) verify arithmetic,
Persian equivalence, snapshot lifecycle/concurrency, profile rejection, publication
rollback, production/evaluation parity and policy drift. Disposable MySQL repeats
publication scenarios and the existing index migration/HTTP tests.

## Related records

Depends on [normalization](0007-shared-persian-normalization.md),
[embedding profiles](0008-embedding-compatibility-profiles.md),
[embedding index migration](0009-resumable-embedding-indexes.md) and
[controlled source provenance](0010-controlled-import-formats.md).
