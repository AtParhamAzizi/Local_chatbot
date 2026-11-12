# 0008: Embedding profiles travel with vectors and gate retrieval

Date: 2026-11-11

Status: Implemented

## Context

Dimension-only validation permits unrelated embedding models, revisions or
processing settings to share a search space. Persian preprocessing also changed
in F033, while existing rows have no evidence of their original configuration.
Models can change concurrently with query or multi-batch ingestion work.

## Decision

Store complete versioned JSON profiles alongside Q&A vectors and PDF
documents/chunks using additive nullable migrations 7–9. Profiles record model
identity, content-addressed local revision, tokenizer/pooling configuration,
normalization version, dimension, runtime versions and float32 storage policy.
Unknown historical identity remains NULL. Nothing rewrites originals or assigns
an inferred profile to existing vectors.

The production adapter captures an immutable profile and vectors together under
the embedding lock. Indexed services reject unprofiled arrays and mixed-profile
ingestion batches. Metadata commits with its vectors. Queries carry their own
captured profiles through both searches, which reject every mismatch before
semantic or lexical scoring. A PDF's document/chunk profiles must also agree.

Profiles use canonical JSON comparison in application code because retrieval
already scans the corpus. A separate profile registry, JSON indexes and a global
single-profile constraint are unnecessary for the current bounded application.
Different known profiles may coexist; each query searches only its own subset.

## Consequences and alternatives

Full local artifact hashing adds model-load I/O, and the inference lock serializes
embedding calls with selection/unload. Both make identity reliable at existing
model boundaries. Artifacts remain immutable while loaded. Runtime-version
matching conservatively requires refresh on dependency changes. Generator-only
selection with identical embedding artifacts remains compatible.

Repository names, dimension matching, caller-supplied revision labels and an
independent "current profile" lookup cannot prove which loaded snapshot produced
an in-flight vector. Automatically marking legacy vectors as current would hide
uncertainty. Explicit replacement/re-import creates certified vectors instead.
The existing transactional publication/retry boundaries remain authoritative.

## Upgrade, recovery and verification

Existing installations require stopped writers, a restore-tested backup and the
explicit migration CLI. Search excludes old vectors until re-imported with
replacement enabled. Rollback restores a clean backup with its matching release;
there is no schema downgrade or destructive automatic reindex.

Offline regressions exercise equal-dimension isolation, every identity field,
unknown/corrupt metadata, legacy replacement, profile changes between batches,
in-flight selection, artifact revision and loader capture. Disposable MySQL
regressions check JSON roundtrips, migration preservation and real retrieval SQL.
See [profile operations and checks](../embedding-profiles.md) and the
[migration guide](../../backend/migrations/README.md).

Local validation on November 11, 2026: all 425 guarded offline tests passed
twice in one interpreter; source checks and the real-PyMuPDF layout evaluation
passed. Disposable MySQL execution was attempted but could not provision its
container because Docker's daemon was unavailable. Real model quality was not
evaluated.
