# Persian retrieval evaluation (F037)

`evaluation/persian-retrieval/v1/` contains 30 synthetic Persian source units and
60 authored questions for the fictional سپهر service. These are Q&A, PDF-page
and Markdown-line excerpts with logical filenames/coordinates, not physical
documents or customer data. Each split has 24 answerable and six unanswerable
questions, including direct wording, Arabic/Persian letters and digits,
half-spaces, terminology substitutions, near-matches and multiple sources.

## Review, splits and versioning

The [manifest](../evaluation/persian-retrieval/v1/manifest.json) records version
1.0.0, canonical JSON SHA-256 identities, counts, provenance, the repository's MIT license
and review status. Each question has an expected answer or missing-information
rationale, exact evidence quotes, graded relevant sources and reviewed hard
negatives. Unlisted sources are grade zero; grade one means useful partial
evidence; grade two means complete evidence for that question.

[Review notes](../evaluation/persian-retrieval/v1/REVIEW.md) record the agent
source-grounding review and corrections. Independent human/domain review is
pending. This small diagnostic seed does not establish representative Persian
customer quality or deployment readiness. A documented “no” is answerable;
missing information is unanswerable.

Development and heldout have six disjoint intent families each; related variants
stay together. Both search the complete same corpus. Tune on development;
default runs do not open heldout questions. Explicit heldout evaluation is for a
frozen candidate: record its version and exposure in your experiment record.
The public split is an operational holdout, not secret. If results influence
tuning, replace it with new intent families in a new version. Structural checks
and replay of the fixed lexical reference are not candidate tuning.

Freeze released sources, questions, labels, splits and baselines. Corrections
require a new version directory, updated checksums/review and new recorded
baselines. Hashes cover UTF-8 canonical JSON (sorted keys, compact separators,
no NaN), making indentation and Windows/Linux file newlines immaterial.
Updating a checksum alone is not review or permission to reuse an exposed split.

## Repeatable commands

Run from the repository root. The reference evaluator needs only Python's
standard library and the shared Persian normalization module.

```text
python -B backend/retrieval_eval.py validate
python -B backend/retrieval_eval.py check-baseline
python -B backend/retrieval_eval.py run --output backend/.runtime/retrieval-eval/development.json
```

`validate` checks both splits for family/ID leakage and normalized duplicates,
exact evidence membership, consistent labels, provenance, counts and checksums.
Normal runs open only the selected split. JSON duplicate keys, nonfinite scores,
incomplete rankings, unknown/repeated sources, ascending scores and mismatched
identities fail. Report output cannot overwrite the versioned dataset directory.

After freezing a candidate, select heldout explicitly:

```text
python -B backend/retrieval_eval.py check-baseline --split heldout
python -B backend/retrieval_eval.py run --split heldout --output backend/.runtime/retrieval-eval/heldout.json
```

The lexical reference uses shared normalized unique-term overlap divided by query
term count, excludes zero-overlap sources and returns at most six hits. It indexes
Q&A questions and document text, matching production embedding inputs, without
synonym expansion or stop-word tuning. Ties use ascending source IDs. Saved
reports include all returned rankings, per-question metrics and tag/source-kind
slices, plus dataset/system identities. No timestamps are added, so repeated
runs are identical. Reference reports also pin the normalization source hash.

## Actual local embeddings

With the application CPU dependencies installed and a complete, trusted,
immutable local model directory:

```text
python -B backend/retrieval_eval.py run --ranker local --model-path backend/model/Qwen3-Embedding-0.6B --compare evaluation/persian-retrieval/v1/baselines/lexical-development.json --output backend/.runtime/retrieval-eval/local-development.json
```

`--model-id` defaults to catalog entry `qwen3.5-2b`. An independent runtime
uses local-only artifacts, source batches of eight and individual queries.
Every batch/profile is validated; reports record the full embedding profile,
artifact revision and scoring settings. Normal Python network/DNS connections
are blocked during loading/inference. This requires no configured database,
ingestion, serving startup or model-selection writes.

The frozen scoring mirror uses cosine for Q&A, `0.82 * cosine + 0.18 * overlap`
for documents, five Q&A/eight document candidates, threshold 0.35 and six final
hits. Parity tests compare against actual search functions and
`RetrievalService` with profiled vectors. Source-ID tie breaking replaces
unspecified database row ordering for tied scores. This measures retrieval on
authored excerpts, not extraction, authorization, generation or latency.
Real-weight runs remain manual; none were performed for this implementation.

## Score a candidate retriever

Export UTF-8 JSON with `format_version: 1`, the exact `dataset` identity from a
generated report's `run.dataset`, and `system` metadata with nonempty `name`,
`version` and `normalization`. Record configuration, code/artifact revision
and embedding profile in `system` as appropriate. `rankings` must map **every**
selected question ID to descending scored hits, including empty lists for
abstentions. One illustrative entry is:

```json
{"dev-signup-1": [{"source_id": "signup-main", "score": 0.91}]}
```

Use stable corpus IDs, preserving a mapping when loading an isolated candidate
index. Export results after the candidate's own acceptance threshold. Never
insert label-derived expected sources into predictions. Both supplied rankers
exclude relevance labels/expected answers from scoring inputs.

```text
python -B backend/retrieval_eval.py score --predictions candidate.json --compare evaluation/persian-retrieval/v1/baselines/lexical-development.json --output backend/.runtime/retrieval-eval/comparison.json
```

Comparison requires the same corpus, questions, split, version, checksums, metric
version and cutoffs. Baseline metrics are recomputed from its saved rankings;
editing aggregate scores cannot conceal a regression. Deltas are observed
differences, not significance tests or automatic deployment gates.

## Metrics and baseline

Ranking metrics are macro-averaged over answerable questions only; missing
relevant evidence scores zero. Unanswerable cases do not inflate them.

- Recall@1/3/6: retrieved distinct positive sources divided by **all** grade-one
  and grade-two sources for the question.
- MRR@6: reciprocal rank of the first positive source in the first six, or zero.
- nDCG@6: sum of `(2^grade - 1) / log2(rank + 1)`, divided by the ideal ordering
  of all positive sources, truncated to six.
- Answerable abstention rate: empty lists among answerable questions.
- Unanswerable false-positive rate: nonempty lists among unanswerable questions;
  this measures retrieval acceptance, not answer-generation correctness.
- Hard-negative@1 rate: a reviewed wrong-scope source ranked first, over all
  questions. Other irrelevant hits still reduce recall/MRR/nDCG.

Missing denominators yield JSON `null`. Per-question MRR/nDCG and summary metrics
are rounded to ten decimals for cross-platform replay. Relevant-source-kind
slices overlap when a question has evidence from multiple kinds.

| Fixed lexical v1 | Recall@1 | Recall@3 | Recall@6 | MRR@6 | nDCG@6 | Unanswerable false positives |
| --- | --- | --- | --- | --- | --- | --- |
| Development | 0.583333 | 0.729167 | 0.875000 | 0.746528 | 0.748243 | 1.000000 |
| Heldout | 0.479167 | 0.687500 | 0.833333 | 0.669444 | 0.697331 | 1.000000 |

The weak baseline returns hits for all unknown cases because of shared
service/topic words. No ranker settings or labels were tuned after recording
heldout results. These are reference measurements, not production-model results.

## Verification

`python -B backend/tests/run_tests.py --pattern test_retrieval_eval.py --repeat 2`
checks hand-calculated metrics, perfect/empty/wrong rankings, denominator handling,
tampered/incomplete predictions, label/hash integrity, split leakage, baseline
drift, default heldout isolation, CLI replay and production-score parity.
The existing offline CI matrix runs these with deterministic profiled embeddings
and forbids real model, database and network access.
