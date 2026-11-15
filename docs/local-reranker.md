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
the embedding model. CUDA NF4 with FP32 computation, PyTorch SDPA attention,
batches of at most three pairs, and 512 total tokens
per query/passage pair and 128 query tokens bound model work. Query and passage
text are capped before tokenization. Document truncation always preserves the
decision suffix. `use_cache=False` and `logits_to_keep=1` avoid generation KV
caches and full-sequence vocabulary logits.

Left padding fixes the tensor width at 512. Two batches cover the six admitted
candidates, amortizing dispatch/dequantization while preserving room for the
resident generator on the measured Pascal GPU. The
unquantized layers also use FP32; the generator keeps its separate NF4/FP16
configuration. These limits are a measured hardware choice, not automatic model
or device selection.

The development-selected weighted reciprocal rank fusion sorts the candidates:

```text
rerank_score = 0.75 / (60 + model_rank) + 0.25 / (60 + first_stage_rank)
```

Ranks start at one. This avoids averaging saturated model probabilities with
the first-stage scale. All six admitted candidates remain eligible context;
this profile improves their order rather than expanding recall. Requests for
more than six retain the untouched first-stage tail. Ties retain the
source-independent first-stage ordering. No source quotas
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
# Match the benchmark's four PyTorch CPU threads; set before launching Python.
$env:OMP_NUM_THREADS = '4'
# Optional override; relative paths resolve from backend/.
$env:RERANKER_MODEL_PATH = 'model/Qwen3-Reranker-0.6B'
```

Then start the application normally. Compose passes `RERANKER_PROFILE` from
`.env`; use its CUDA override and recreate the application container after
changing the setting. `off` immediately restores the lower-resource path after
restart. For containers, set `OMP_NUM_THREADS=4` in the application service's
environment override to reproduce the measured thread count. No CPU reranker
profile is enabled by this benchmark.

## Measured profile and decision

The November 15, 2026 measurements justify enabling `gtx1050-4gb` as an explicit
single-user quality option when roughly five seconds of additional retrieval
latency is acceptable. Keep `off` for small machines or interactive workloads
that cannot afford this cost. The recorded host is a GeForce GTX 1050 with
4096 MiB VRAM, AMD Ryzen 5 3500U (four cores/eight logical processors), about
14 GiB system RAM, and Windows 11 build 26300. PyTorch uses four CPU threads,
torch 2.7.1+cu118 and transformers 5.20.2. The reranker runs in the NF4/FP32
configuration above alongside the idle NF4/FP16 Qwen3.5-2B generator.

| Quality metric | Development, F039 → reranked | Heldout, F039 → reranked |
| --- | --- | --- |
| nDCG@6 | 0.8590 → 0.8775 | 0.8881 → 0.9381 |
| MRR@6 | 0.8625 → 0.8625 | 0.8958 → 0.9583 |
| Recall@1 | 0.6667 → 0.6875 | 0.7292 → 0.8333 |
| Recall@6 | 0.9583 → 0.9583 | 0.9583 → 0.9583 |
| Hard negative at rank 1 rate | 0.1667 → 0.1000 | 0.1000 → 0.0667 |

| Resource metric | Development | Heldout |
| --- | --- | --- |
| Reranker cold load | 9.74 s | 10.03 s |
| Warm p50 / p95, 60 requests | 3.75 / 4.56 s | 3.83 / 4.62 s |
| Six full 512-token pairs, stress latency | 3.07 s | 3.02 s |
| Additional peak GPU reservation | 1018 MiB | 1018 MiB |
| Additional process peak bound | 972.2 MiB | 956.5 MiB |
| Combined GPU reservation during generation | 2720 MiB | 2722 MiB |
| Combined process peak | 2954.9 MiB | 2954.4 MiB |
| Serial generation with both models resident | 16 tokens, passed | 16 tokens, passed |

Heldout relevant-source recall@6 remains Q&A 0.90, PDF 1.00 and controlled
documents 1.00. Their recall@1 changes from 0.80/0.875/0.40 to 0.80/1.00/0.60.
Development controlled-document recall@1 decreases from 0.60 to 0.55 while Q&A
increases from 0.70 to 0.80; the improvement is an aggregate result, not a claim
that every source type improves on every split. All six unanswerable questions
in each split still return candidates, so false-positive rate remains 1.00.

Both records pass all fixed quality and resource gates. The model's local
content identity is
`sha256:ef80e8f4eb863ffb3e8e9b03598812af7ebd0613adc4614998d257d3eae67815`.
The complete measurements, scores, selection alternatives, runtime identities
and replayable rankings are committed in
[development](evaluations/f040-development.json) and
[heldout](evaluations/f040-heldout.json). Default configuration remains `off`;
these results support the named optional profile within the protocol's scope.

## Benchmark protocol

`backend/reranker_eval.py` uses the unchanged F037 Persian corpus: 30 sources,
30 development and 30 heldout questions, each with 24 answerable and six
unanswerable cases. Actual cached embedding vectors construct the same F039
candidate lists; the local reranker actually scores those lists. All network
socket operations are blocked, and no database/application is started.

Development compares 50 fusion configurations: reciprocal rank fusion with
constants 1, 5 and 60, raw probability averaging, and clipped/rescaled log-odds
averaging, each with model weights 0.1, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.75,
0.9 and 1.0. Selection prioritizes passing quality gates, then nDCG@6, recall@6,
MRR@6, smaller weight, smaller rank constant and method name. The shipped
strategy must match the development choice. Before heldout scoring, the frozen
development record must pass and match the policy/source fingerprints, dataset,
embedding profile and local model/runtime identity. Heldout never selects a
weight. Committed candidate scores, first-stage values and rankings permit
metric replay without weights or cached vectors.

Acceptance requires at least 0.01 absolute nDCG@6 improvement, no recall@6 or
MRR@6 loss, no relevant-source-kind recall loss and no false-positive increase.
The single-user quality profile budgets warm p95 at 5 seconds, model cold load
at 60 seconds, additional GPU reservation at 2048 MiB, and a conservative
additional process peak working-set bound of 4096 MiB. The combined models must
stay within 3840 MiB GPU reservation and 8192 MiB process peak working set.
These gates and the development policy are fixed before heldout scoring.

The default NF4/FP16 generator loads first, matching application startup. Its idle
GPU reservation and current process working set form the resource baseline;
unused allocator buffers are released before capturing it. This order avoids a
large second-model mapping after warming the reranker on Windows systems with
limited paging space. It does not change the host paging configuration.

One query warms the reranker; two complete passes yield 60 timed samples per split.
CUDA synchronization surrounds every sample, including tokenization and artifact
checks. Cold load includes local content hashing and model/tokenizer loading,
but excludes Python/PyTorch import. Nearest-rank percentiles and the individual
samples are retained. Peak process working set and PyTorch GPU allocation and
reservation include a separate stress request of six pairs at the full 512-token
bound; its latency is reported separately from corpus p95. GPU increments subtract
the idle generator baseline. The process bound subtracts baseline current working
set from the absolute historical peak, which can include generator loading and
therefore conservatively overstates the reranker's additional peak. Absolute
peaks are retained too. The embedding cache avoids loading the CPU embedder;
these figures exclude SQL, embedding and complete chat latency.

A serial coexistence smoke test retains both the reranker and default local
Qwen3.5-2B generator in their measured configurations. It reranks the
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
