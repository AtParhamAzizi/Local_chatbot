# Embedding identity and compatibility (F034)

Indexed Q&A rows, PDF documents and PDF chunks carry an `embedding_profile` JSON
value alongside their float32 BLOB/dimension. Equal dimensions alone never make
two vectors compatible. Search requires exact profile equality before cosine or
PDF lexical scoring; PDF chunk and document profiles/dimensions must also agree.
Unknown, malformed, incomplete or unsupported metadata is excluded from search.
Original text, Q&A keys, file hashes and page citations are unchanged.

## Profile contents

`backend/embedding_profile.py` defines immutable, versioned profiles. Canonical
JSON provides a reproducible SHA-256 identity; database JSON key ordering does
not affect compatibility. The stored fields are:

| Field | Meaning |
| --- | --- |
| `version` | Profile schema version, currently 1 |
| `model_id` | Embedding model's catalog identity, independent of generator selection |
| `revision` | SHA-256 identity of the actual local model/tokenizer/configuration/code files |
| `tokenizer` | Tokenizer class, left padding, padding/truncation, maximum length, special-token setting and artifact revision |
| `pooling` | Last non-padding token implementation version and L2 normalization |
| `normalization_version` | Shared Persian preprocessing policy, currently `fa-v1` |
| `dimension` | Loaded model's hidden size, checked against every returned vector |
| `runtime` | Embedding implementation/float32 CPU policy and installed torch, transformers and tokenizers versions |
| `storage_dtype` | Persisted vector representation, currently `float32` |

The revision hashes relative filenames and file content, including weight shards,
JSON configurations/tokenizers, vocabularies, merges, tokenizer models, templates
and custom Python loader code. It does not use an absolute installation path or
trust a repository label as proof of identical weights. Moving an unchanged
snapshot preserves its identity. Changing weights or tokenizer bytes changes it
even when the dimension stays the same. The tokenizer's artifact revision uses
the whole snapshot digest, conservatively invalidating it on model changes too.

Hashing streams files once at model load and adds startup disk I/O proportional
to snapshot size; profiles remain cached with the loaded model. The loader checks
the runtime file inventory and size/timestamps before publishing loaded resources.
Keep model artifacts immutable while loading/loaded; use a separate directory
for a candidate and retain serving/previous snapshots. This is compatibility metadata,
not a signature or protection against an administrator rewriting files/metadata.
Exact runtime-version matching is intentionally conservative: even a dependency
upgrade that would produce equivalent vectors can require re-embedding.
When changing normalization or embedding implementation behavior, bump its
recorded version so previously indexed vectors cannot silently inherit it.

## Boundaries and model changes

`interface.embed_batch()` returns `EmbeddingBatch(vectors, profile)`. It holds
the shared embedding lock through load/inference/profile capture, so selection
or unload cannot relabel in-flight output. `embed_texts()` remains an array-only
convenience for callers that do not index/search vectors. Ingestion/retrieval and
injected embedding adapters must use profiled batches; bare arrays are rejected.
No model loads during factory assembly or preflight.

Each ingestion batch must have the same complete profile, not just the same
dimension. All batches stage before publication. A profile change, missing
metadata, wrong dimension, nonfinite value or zero vector fails before knowledge
writes/replacement. Metadata and vectors commit together in the existing
publication transaction. A model change after staging cannot relabel those
staged vectors. Retrieval passes its query batch's own profile to both searches,
even if a different model becomes active while that request is running.

Selecting a different generator with the same embedding artifacts and processing
configuration preserves compatibility. F035 queues changed embedding selections
for background re-embedding into a separate generation. The current compatible
runtime/index remain available until full validation and atomic cutover. A query
only scores its captured profile, including when cutover happens during the
request. There is no lexical fallback for incompatible PDF vectors. See
[index operations and runtime-upgrade limits](embedding-index-migration.md).

## Upgrade and legacy knowledge

Migrations 7–9 add nullable JSON columns to Q&A, PDF documents and PDF chunks.
They preserve all rows, originals, vectors and timestamps, and never guess a
profile for existing vectors. Version 10 adds shadow generations, durable
checkpoints and active/previous/request state. Serving now requires version 14,
including [controlled import provenance](import-formats.md) in 11–14. Follow the
[migration guide](../backend/migrations/README.md): stop writers, prepare and
restore-test a backup, then run the explicit migration CLI. Startup never adds
columns or backfills identities.

Legacy rows remain visible to authorized document administration. Unknown
vectors stay excluded, while F035 freshly embeds their original source text into
the serving generation in the background. Original text, answers, IDs, profiles
and bytes stay untouched. Existing replacement-import flows remain available;
normal duplicate imports still skip Q&A/reject duplicate PDFs and cannot certify
old vectors. Historical Q&A aliases remain stored as described in the
[normalization policy](persian-normalization.md).

To inspect stored identities without reading BLOBs, an authorized database
operator can query `SELECT id, embedding_dim, embedding_profile FROM qa_documents`
(or `pdf_documents`/`pdf_chunks`). Do not label unknown vectors by copying the
current profile into their columns. Re-embed the source. F035 status/rollback
uses `python -B backend/reembed.py status|rollback`; the previous generation first
catches up with current sources and validates before switching. Schema/release
downgrade still requires the matching older release and a clean restored database.

## Verification

`backend/tests/test_embedding_profiles.py` checks equal-dimension isolation for
Q&A, PDF and combined retrieval against real SQLite publication/search SQL;
model/revision/tokenizer/pooling/normalization/runtime mismatches; legacy and
malformed metadata; document/chunk disagreements; batch switches before writes;
artifact changes/relocation; additive/resumable migrations; loader metadata; and
concurrent selection during inference. Existing normalization tests retain original
text/provenance checks. Embedding doubles supply explicit deterministic profiles.

The disposable MySQL suite adds matching JSON storage, profile-isolated retrieval
and replacement coverage, and verifies migrated legacy profiles stay NULL. These
tests need Docker; they never use a configured application database. Offline
checks establish compatibility enforcement, not real embedding-model accuracy.
