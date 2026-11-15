# 0012: Comparable source ranking through shared score calibration

Date: 2026-11-15

Status: Implemented; supersedes 0011's default scoring and final merge.

## Context

Legacy Q&A cosine and document cosine/overlap scores differ by construction.
F038 shares a formula but keeps separate BM25 frequencies/length statistics,
five/eight candidate limits and a Q&A-first merge on equal scores. A mixed-source
ranking must compare evidence on one scale without giving a source type a
formula-derived advantage.

## Decision

Default to `RETRIEVAL_STRATEGY=fusion`. Fetch all profile-compatible valid-vector
candidates and build one lexical snapshot across Q&A and document chunks.
Ignore collection-local scores and compute `0.8*cosine + 0.2*BM25/(BM25+16)`
before the shared 0.35 threshold and six-result limit. This is a fixed scale
calibration, not a probability model. Namespace table-local IDs and break equal
scores by normalized-content and source-identity hashes without type precedence.

Select parameters on development questions and freeze implementation, dataset
and model identities before heldout regression evaluation. Preserve F038 and
legacy strategies as explicit restart-based rollback options. Existing lexical
cache, embedding compatibility, publication guards and HTTP response shape remain.

## Alternatives and consequences

Reciprocal rank fusion avoids score magnitudes, but loses magnitude information
that distinguishes strong and weak evidence. Exploratory development comparisons
did not preserve relevance as well as shared scale calibration. Per-source rank
interleaving can promote weak first-place evidence from an otherwise irrelevant
collection. Source quotas would change the objective from evidence relevance to
result counts. A learned probability calibrator needs more independently judged
data than the current synthetic benchmark provides.

One corpus gives lexical scores a common statistical meaning. Its frequencies
still respond to corpus composition and Q&A/document text length differences;
this choice does not guarantee equal outcome rates on arbitrary corpora. All
candidate dictionaries remain live until final ranking. Dense scans, cold index
builds, source-text hashing and cache memory need deployment measurement.

## Verification and limitations

The 48-candidate development comparison selected weight 0.2 / scale 16. Every
kind's recall@6 is preserved. Development nDCG changes by -0.005008 and MRR by
-0.002083, within budgets fixed before heldout. Heldout recall@6, MRR and nDCG
are unchanged at 0.958333, 0.895833 and 0.888111. Kind-specific heldout recall@6
is Q&A 0.90, PDF 1.00, controlled documents 1.00 for both policies.

Offline source-swap, score-scale and unequal-corpus regressions check mechanics
alongside production/evaluator parity and snapshot lifecycle. Evidence uses
cached actual local embeddings over agent-reviewed synthetic excerpts; independent
review is pending, unanswerable false positives remain 1.0, and this feature has
no new end-to-end latency evidence. See [strategy, records and replay](../source-ranking.md).

## Related records

Supersedes the default scoring/merge in [0011](0011-indexed-persian-hybrid.md);
retains its tokenizer, immutable lexical index and historical evaluation.
