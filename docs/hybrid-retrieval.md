# Indexed Persian hybrid retrieval (F038)

The default `hybrid` strategy combines local embedding cosine with indexed BM25
for both Q&A and PDF/controlled documents. `RETRIEVAL_STRATEGY=legacy` restores
the prior Q&A cosine and document overlap policy after restart. No schema,
knowledge or model changes are needed to switch strategies.

## Policy and index lifecycle

`services/lexical_index.py` uses inverted term postings with BM25 `k1=1.2`,
`b=0.75`. It shares `fa-v1` normalization (Persian letter/digit/half-space
equivalence), Unicode casefolding and word tokens of length at least two.
Repeated query terms count once. The selected policy retains stop words and
negation and performs no stemming. Numbers remain discriminating.

Q&A lexical text is `question + newline + answer`; its embedding remains the
question embedding. PDF and controlled documents use chunk content. Each
collection has its own document frequencies and length normalization. Both use:

```text
score = 0.8 * cosine + 0.2 * BM25 / (BM25 + 8)
```

All eligible sources are scored before the existing five-Q&A/eight-document
candidate limits; lexical evidence can rescue a source outside the dense top
candidates. The service retains the 0.35 threshold and six-result final limit.
Scores are ranking values, not probabilities. Ties within each collection use
ascending source ID; production's stable final merge keeps Q&A first on ties.
Offline corpus IDs provide deterministic ties; parity tests exercise actual
production scorers with both policies.

Only sources with matching complete embedding profiles, dimensions and valid,
nonzero finite vectors enter postings. Documents also require completed status
and compatible parent profiles. The exact eligible source ID/text tuples,
collection, profile identity and tokenizer mode/version key each cached snapshot.
Committed import/replacement/deletion and embedding cutover/rollback automatically
select matching views. Failed publication preserves the old corpus and view.

The process holds a two-view LRU, normally Q&A plus documents. Only complete
indexes publish, builds are serialized, and existing readers retain their
immutable snapshot. Cache hits avoid document tokenization and queries visit
only matching postings. First use, source changes, eviction and restart pay a
cold build. View count is bounded; RAM bytes are not. Readers can retain evicted
views. Dense SQL fetch/cosine and exact snapshot comparison still scan the corpus.
There is no persistent lexical index, vector ANN or new dependency.

## Development-only selection

Recorded on 2026-11-12 using the unchanged F037 v1 corpus: 30 sources, separate
30-question development and heldout intent families, each with 24 answerable and
six unanswerable questions. Sources/labels were agent-reviewed; independent human
and domain review remains pending. All measurements below use actual local
`Qwen3-Embedding-0.6B` embeddings, not deterministic test doubles or the F037
lexical reference. Reports pin the dataset, vector artifact, model profile and
artifact revision. Model preparation blocked normal Python network/DNS access.

Development tried 36 candidates: three tokenizers, lexical weights
`0.1/0.2/0.3/0.4`, saturation scales `1/4/8`. Selection maximized nDCG@6, then
recall@6 and MRR@6, then minimized unanswerable false positives, weight, scale
and tokenizer name. Heldout questions were not loaded for selection. The complete
[development selection](evaluations/f038-development-selection.json) was frozen
before heldout vector preparation/evaluation and pins the lexical source hash.

| Development method (best candidate per tokenizer) | Recall@6 | MRR@6 | nDCG@6 |
| --- | ---: | ---: | ---: |
| Legacy cosine / document overlap | 0.937500 | 0.760417 | 0.778596 |
| Word BM25 + cosine (selected) | 0.958333 | 0.864583 | 0.863962 |
| Filtered word BM25 + cosine | 0.916667 | 0.819444 | 0.815035 |
| Character-trigram BM25 + cosine | 0.916667 | 0.833333 | 0.827199 |

Words won this comparison; Persian compatibility was evaluated rather than
inferred from a tokenizer's name. This limited comparison does not establish
superiority over every Persian analyzer or deployment corpus.

## Frozen heldout results

The [full heldout report](evaluations/f038-heldout.json) includes rankings,
per-question/tag/source-kind metrics, profile identities, selection/vector
checksums and latency measurements. No policy was adjusted after heldout access.
The frozen run was repeated with identical quality metrics.

| Heldout metric | Legacy | Hybrid |
| --- | ---: | ---: |
| Recall@1 | 0.729167 | 0.729167 |
| Recall@3 | 0.895833 | 0.895833 |
| Recall@6 | 0.895833 | 0.958333 |
| MRR@6 | 0.895833 | 0.895833 |
| nDCG@6 | 0.856406 | 0.888111 |
| Unanswerable false-positive rate | 1.000000 | 1.000000 |
| Hard-negative-at-1 rate | 0.100000 | 0.100000 |

The preregistered gates require nDCG@6 gain at least 0.01, no recall@6 or MRR@6
regression, and no increase in unanswerable false positives. All passed. The
unchanged false-positive rate is a material limitation: this feature improves
retrieval ranking/coverage on these excerpts and does not solve abstention,
answer generation or broad production quality. Retune only on development;
heldout-driven tuning requires a newly versioned, reviewed heldout set.

## Latency measurements and budgets

The frozen selection also records latency budgets: hybrid warm p95 must be at
most `max(legacy_p95 * 1.25, legacy_p95 + 5ms)`; cold latency must be at most
`max(legacy_p95 * 2, 500ms)`. Scales are 30 and 3,000 source rows. Each strategy
has one cold query followed by three passes over all 30 heldout queries (90 warm
samples). Runs use cached real vectors and the same scoring mirror for both
policies, including the actual lexical index/cache implementation.

Recorded replay: Windows 11 build 26300, AMD Ryzen 5 3500U, CPython 3.13.14,
NumPy 2.5.4. Embedding preparation used Torch 2.7.1+cu118 on CPU, four threads,
Transformers 5.20.2; timing itself loads no model.

| Sources | Legacy warm p95 ms | Hybrid warm p95 ms | Hybrid cold ms | Warm budget ms | Cold budget ms |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 30 | 1.8172 | 0.4461 | 2.1346 | 6.8172 | 500 |
| 3,000 | 180.3302 | 36.2197 | 209.9359 | 225.41275 | 500 |

Both scales passed. These are in-process scoring/indexing measurements: **model
inference, database I/O and HTTP are excluded**. The scale fixture duplicates
the 30 source texts/vectors under unique IDs; it is not a representative corpus
of 3,000 unique documents. Cold builds cost more than legacy scoring; warm
document scoring saves repeated normalization/tokenization. Timings are hardware
and load dependent. Measure end-to-end latency and RAM on real deployment data;
these measurements do not imply a full-request speedup.

Evidence applies to the recorded embedding profile and corpus. Re-evaluate on
development and freeze a new candidate when changing the model, normalization or
corpus; do not assume the present quality gains transfer automatically.

## Repeat the evaluation

From the repository root, use an environment with the application embedding
dependencies and trusted, complete local model artifacts for `prepare`:

```text
python -B backend/hybrid_eval.py prepare --model-path backend/model/Qwen3-Embedding-0.6B --output backend/.runtime/f038/development-vectors.json
python -B backend/hybrid_eval.py select --vectors backend/.runtime/f038/development-vectors.json --output backend/.runtime/f038/development-selection.json
```

Preparation records complete profiled vectors in ignored runtime files and does
not access the serving DB, ingest data, change model selection or start the app.
It uses the independent production loader, CPU inference and eight-input batches.
`select --split heldout` is rejected before dataset/model access. Inspect and
freeze a development selection before preparing/evaluating heldout:

```text
python -B backend/hybrid_eval.py prepare --split heldout --model-path backend/model/Qwen3-Embedding-0.6B --output backend/.runtime/f038/heldout-vectors.json
python -B backend/hybrid_eval.py evaluate --split heldout --vectors backend/.runtime/f038/heldout-vectors.json --selection docs/evaluations/f038-development-selection.json --output backend/.runtime/f038/heldout-evaluation.json
```

`select`/`evaluate` need only NumPy plus the standard library once vectors exist;
they validate exact dataset coverage, complete profile, dimensions and finite
nonzero vectors. The last command replays the shipped frozen policy and fails
if its dataset/profile or frozen lexical source hash differs. It writes the report and returns nonzero when
quality or latency acceptance fails. A repeat's timing can vary; no automatic
threshold adjustment is made. Preserve new reports separately from committed
selection/evidence; never overwrite F037 artifacts. The older
`retrieval_eval.py --ranker local` remains the frozen pre-F038 reference.

For regression commands, see [testing](../backend/tests/TESTING.md#indexed-hybrid-retrieval-f038).
For deployment rollback, see [operations](operations.md#hybrid-retrieval-and-rollback-f038).
The durable choice and tradeoffs are in [ADR 0011](decisions/0011-indexed-persian-hybrid.md).
