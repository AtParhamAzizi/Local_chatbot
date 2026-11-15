# Optional local reranker (F040)

`RERANKER_PROFILE=off` is the default and the path for small machines. It keeps
the F039 ranking, imports no model dependencies and allocates no reranker RAM or
VRAM. `RERANKER_PROFILE=gtx1050-4gb` opts into the benchmarked configuration below.
Restart after changing this setting or the model directory. The feature applies
only with `RETRIEVAL_STRATEGY=fusion`; historical `hybrid` and `legacy` modes keep
their original behavior. No database migration or embedding rebuild is needed.

## Ranking and resource bounds

The first stage continues to validate complete embedding profiles and calibrate
all eligible source types together. It admits scores of at least 0.35, then
passes at most six candidates to `Qwen/Qwen3-Reranker-0.6B`. The model sees the
normalized query and Q&A question/answer or document passage, with the same
English relevance instruction for every source type. The final-token `yes` /
`no` softmax supplies a relevance score. The decision prompt follows the
[official model card](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B).

The cross-encoder is a separate local causal model; it does not reuse or change
the embedding model. CUDA NF4 with FP32 linear computation and FP16 unquantized
layers, eager attention, batches of one, 512 total tokens
per query/passage pair and 128 query tokens bound model work. Query and passage
text are capped before tokenization. Document truncation always preserves the
decision suffix. `use_cache=False` and `logits_to_keep=1` avoid generation KV
caches and full-sequence vocabulary logits.

The development-selected mixture sorts the bounded candidates, and returns six
hits. Ties retain the source-independent first-stage ordering. No source quotas
or source-specific weights are introduced. HTTP `score` and context similarity
continue to expose the original calibrated first-stage value; their values need
not descend after reranking. The internal `rerank_score` controls order and is
not an HTTP field. Admission and abstention retain the first-stage threshold;
reranking cannot restore excluded or incompatible sources.

Application factories share one lazy runtime. Imports and factory creation do
not load weights. A nonblocking lock allows one model operation at a time;
concurrent requests use their original first-stage results immediately. Missing
files, CUDA absence, OOM, changed artifacts, invalid output or inference failures
return the original first six hits. A fixed warning contains no question,
passage, path or exception details. A failed runtime stays disabled until process
restart, preventing repeated expensive load attempts. There is no hard inference
deadline: the token/candidate bounds limit work, and the benchmark establishes
the expected latency for the named profile.

## Local preparation and configuration

Weights are an optional operator download, excluded from Git and mounted by the
existing read-only model directory in Compose. Serving and evaluation never
download models, execute remote model code, or load pickle weights. The explicit
preparation command pins upstream revision
`e61197ed45024b0ed8a2d74b80b4d909f1255473`:

```powershell
.\.venv\Scripts\python.exe -B backend/reranker_prepare.py
```

If a large streaming transfer stalls, add `--chunked`. This resumes contiguous
partial batches using up to eight parallel 4 MiB ranges, and verifies the final
weights against the pinned upstream LFS SHA-256 before publishing the file.
The complete weights occupy 1,191,588,280 bytes; allow additional disk space for
download caches. Keep the full tokenizer/config snapshot beside them. See the
upstream Apache-2.0 license before distributing model files.

```powershell
$env:RERANKER_PROFILE = 'gtx1050-4gb'
# Optional override; relative paths resolve from backend/.
$env:RERANKER_MODEL_PATH = 'model/Qwen3-Reranker-0.6B'
```

Then start the application normally. Compose passes `RERANKER_PROFILE` from
`.env`; use its CUDA override and recreate the application container after
changing the setting. `off` immediately restores the lower-resource path after
restart. No CPU reranker profile is enabled by this benchmark.

## Benchmark protocol

`backend/reranker_eval.py` uses the unchanged F037 Persian corpus: 30 sources,
30 development and 30 heldout questions, each with 24 answerable and six
unanswerable cases. Actual cached embedding vectors construct the same F039
candidate lists; the local reranker actually scores those lists. All network
socket operations are blocked, and no database/application is started.

Development compares model weights 0.25, 0.5, 0.75 and 1.0, prioritizing passing
quality gates, then nDCG@6, recall@6, MRR@6, and smaller weight. The shipped
weight must match the development choice. Before heldout scoring, the frozen
development record must pass and match the policy/source fingerprints, dataset,
embedding profile and local model/runtime identity. Heldout never selects a
weight. Committed candidate scores, first-stage values and rankings permit
metric replay without weights or cached vectors.

Acceptance requires at least 0.01 absolute nDCG@6 improvement, no recall@6 or
MRR@6 loss, no relevant-source-kind recall loss and no false-positive increase.
The single-user quality profile budgets warm p95 at 5 seconds, model cold load
at 60 seconds, reranker GPU reservation at 2048 MiB, and reranker process peak
working set at 4096 MiB. These gates were fixed before heldout scoring.

One query warms the model; two complete passes yield 60 timed samples per split.
CUDA synchronization surrounds every sample, including tokenization and artifact
checks. Cold load includes local content hashing and model/tokenizer loading,
but excludes Python/PyTorch import. Nearest-rank percentiles and the individual
samples are retained. Peak process working set and PyTorch GPU allocation and
reservation include a separate stress request of six pairs at the full 512-token
bound; its latency is reported separately from corpus p95. The embedding cache avoids loading the CPU embedder;
these figures exclude SQL, embedding and complete chat latency.

A separate serial coexistence smoke test retains the reranker while loading the
default local Qwen3.5-2B generator in double-quantized NF4/FP16. It reranks the
full token/candidate stress list, then generates up to 16 tokens and requires GPU reservation no
greater than 3840 MiB. This checks basic coexistence on a 4 GiB GPU; it does not
establish capacity for maximum chat context, 512 generated tokens, larger
generators, speech models, simultaneous generation or multiple serving workers.
An inference OOM still follows the first-stage fallback.

```powershell
.\.venv\Scripts\python.exe -B backend/reranker_eval.py freeze --vectors backend/.runtime/f038/development-vectors.json --output docs/evaluations/f040-development.json
.\.venv\Scripts\python.exe -B backend/reranker_eval.py evaluate --split heldout --vectors backend/.runtime/f038/heldout-vectors.json --selection docs/evaluations/f040-development.json --output docs/evaluations/f040-heldout.json
```

Create missing embedding caches with the F038 preparation commands in
[hybrid retrieval](hybrid-retrieval.md), using the same full local embedding
snapshot. A different dataset/profile/model or a code change requires a new
development freeze before heldout evaluation.

This small synthetic benchmark supports an explicit local quality option.
Independent human/domain review and representative customer-corpus validation
remain pending. F038/F039 already used these heldout questions, so this is a
regression comparison rather than independent generalization evidence. The
first-stage unanswerable false-positive problem remains visible in the report;
reranking alone does not provide calibrated abstention.
