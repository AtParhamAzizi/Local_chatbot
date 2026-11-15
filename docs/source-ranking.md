# Comparable ranking across source types (F039)

[F040 optional local reranking](local-reranker.md) can reorder a bounded set of
these admitted candidates. The first-stage formula and default remain unchanged.

The default `RETRIEVAL_STRATEGY=fusion` recomputes scores for Q&A, PDFs and
controlled documents from common evidence. It ignores the collections' local
`score` fields. Previously, legacy Q&A cosine and PDF overlap scores differed
by construction; F038 gave them the same formula but separate BM25 statistics,
different candidate caps and Q&A-first ties. Those values were still not on a
common lexical scale.

## Calibration and fusion

Both searches first enforce complete query/source/parent embedding profiles,
dimensions, valid finite nonzero vectors and completed document publication.
The default service requests every eligible candidate with `k=None`. The shared
scorer builds one BM25 snapshot over Q&A `question + newline + answer` and
document content. PDF and controlled chunks share the document ID namespace;
Q&A IDs have a separate namespace, so equal numeric IDs do not collide.

Document frequencies, average length, tokenizer and calibration parameters are
shared across the entire snapshot. The selected fixed calibration is:

```text
lexical = BM25 / (BM25 + 16)
score   = 0.8 * cosine + 0.2 * lexical
```

This is score-scale calibration: lexical evidence has one bounded scale and
both source types use the same evidence formula. It is not fitted probability
calibration. There are no source-specific coefficients or source quotas. Cosine
uses compatible embeddings; BM25 retains F038's Persian word tokenizer,
`k1=1.2`, `b=0.75`, unfiltered words and unique query terms.

Only after recalibration does the service apply `score >= 0.35`, sort descending
and retain six hits. An absent collection or a collection of weak candidates
does not earn a reserved result slot. Equal scores use the SHA-256 of normalized
lexical text, then the SHA-256 of the namespaced source identity. This provides
repeatable ties without a fixed Q&A/PDF preference. Exact duplicate evidence can
still occupy multiple slots; evidence deduplication is a separate concern.

The existing HTTP `score`, source `score`, and generation-context similarity
remain ranking values with the same shared formula; they are not probabilities.
No response fields, schema, stored embeddings or model files change.

## Snapshot and runtime costs

The existing two-view LRU now normally holds combined-corpus snapshots, keyed by
policy version, full profile and sorted eligible `(namespaced ID, original text)`
pairs. Input/SQL order does not invalidate a snapshot. Committed edits/deletions,
publication and profile cutover/rollback select a new view; failed publication
continues to expose the old data. Validation precedes indexing. Readers retain
immutable views during rebuilds.

Serving still scans all eligible SQL rows/vectors. Removing collection caps
retains all candidate dictionaries until shared ranking, and exact tie ordering
hashes source text. Cache view count is bounded, memory bytes are not. F038's
historical timing numbers do not measure this new merge; no F039 end-to-end
latency or representative large-corpus RAM claim is made.

## Development selection and frozen evaluation

Recorded on November 15, 2026 using the unchanged F037 v1 synthetic corpus:
30 sources, 30 development questions and 30 heldout questions. Each split has
24 answerable and six unanswerable cases. Measurements reuse cached actual
local Qwen3-Embedding-0.6B vectors with full profile/dataset checks, without
loading a model, starting an application or contacting a database/network.
Independent human/domain review remains pending. F038 already used this heldout
set for its own acceptance; this is a regression comparison, not a new independent
generalization benchmark.

Development compares lexical weights `0.05/0.1/0.15/0.2/0.25/0.3/0.4/0.5` with
scales `1/2/4/8/16/32` (48 combinations). Selection prioritizes passing acceptance,
then preserving recall@6 for every source kind, then nDCG@6, recall@6 and MRR@6;
ties prefer smaller lexical weight and scale. The selected weight 0.2 and scale 16
are frozen before F039 heldout replay. The
[development record](evaluations/f039-development.json) includes every candidate,
rankings, source fingerprints, model identity and dataset/vector checksums.

The acceptance budget was fixed after development exploration, before heldout
access: no overall or per-kind recall@6 regression, no increase in unanswerable
false positives, at most 0.01 absolute nDCG loss and 0.005 absolute MRR loss.
This permits the small development change needed for a common scale; it does
not claim a relevance improvement. The frozen
[heldout report](evaluations/f039-heldout.json) passes all gates.

| Metric | F038 development | Fusion development | F038 heldout | Fusion heldout |
| --- | ---: | ---: | ---: | ---: |
| Recall@6 | 0.958333 | 0.958333 | 0.958333 | 0.958333 |
| MRR@6 | 0.864583 | 0.862500 | 0.895833 | 0.895833 |
| nDCG@6 | 0.863962 | 0.858954 | 0.888111 | 0.888111 |
| Unanswerable false positives | 1.000000 | 1.000000 | 1.000000 | 1.000000 |

Source recall below counts only relevant evidence of that specific kind;
retrieving a relevant Q&A does not give credit for a missing relevant PDF in the
same question. This differs from the older query-level source-kind slices.

| Relevant source kind | Development questions | Development recall@6 before / after | Heldout questions | Heldout recall@6 before / after |
| --- | ---: | ---: | ---: | ---: |
| Q&A | 10 | 1.00 / 1.00 | 10 | 0.90 / 0.90 |
| PDF | 8 | 1.00 / 1.00 | 8 | 1.00 / 1.00 |
| Controlled document | 10 | 0.90 / 0.90 | 10 | 1.00 / 1.00 |

Balance here means source-independent scoring and preserved evidence retrieval
for each kind, not equal result counts. Paired deterministic tests additionally
place six relevant targets per kind against 20 opposing-kind distractors: each
kind wins 6/6, and swapping source labels preserves the ordering. Tests also
change collection-local scores by orders of magnitude without changing the
result, verify common-scale arithmetic, overlapping table IDs, deterministic
ties, no forced source quota, weak-match exclusion and evaluator/production parity.
Controlled tests establish ranking mechanics, not real-model accuracy.
The unchanged 100% unanswerable false-positive rate remains an abstention limit.

## Replay and rollback

Prepare independent profiled vectors using the existing F038 preparation command
if the ignored local cache is unavailable. From the repository root:

```text
python -B backend/hybrid_eval.py prepare --model-path backend/model/Qwen3-Embedding-0.6B --output backend/.runtime/f039/development-vectors.json
python -B backend/source_fusion_eval.py freeze --vectors backend/.runtime/f039/development-vectors.json --output backend/.runtime/f039/development.json
python -B backend/hybrid_eval.py prepare --split heldout --model-path backend/model/Qwen3-Embedding-0.6B --output backend/.runtime/f039/heldout-vectors.json
python -B backend/source_fusion_eval.py evaluate --split heldout --vectors backend/.runtime/f039/heldout-vectors.json --selection backend/.runtime/f039/development.json --output backend/.runtime/f039/heldout.json
```

`freeze --split heldout` is rejected before vector access. Selection is restricted
to development. Frozen evaluation rejects changed code, dataset or model identity
and unaccepted selections. Failed quality/balance acceptance writes its report and
returns nonzero. Do not change calibration in response to heldout results;
further tuning requires development data and a fresh independent holdout.
Committed F037/F038 artifacts remain unchanged references.

Set `RETRIEVAL_STRATEGY=hybrid` and restart for the frozen F038 policy, or `legacy`
for the earlier cosine/overlap policy. Both intentionally retain historical
score comparison behavior; they are diagnostic rollback options. Restore `fusion`
and restart to use comparable default ranking. Compose and `.env.example` default
to `fusion`. See [operations](operations.md#hybrid-retrieval-and-rollback-f038)
and [ADR 0012](decisions/0012-comparable-source-ranking.md).
