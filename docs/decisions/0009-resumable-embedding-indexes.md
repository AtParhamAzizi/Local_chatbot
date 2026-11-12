# 0009: Resumable embedding generations with atomic publication

Date: 2026-11-12. Status: implemented. Extends
[0008](0008-embedding-compatibility-profiles.md), replacing its manual-only refresh
and index rollback procedure while retaining exact compatibility identities.

## Problem

Changing the live query embedder immediately excludes existing knowledge, even
when its vector dimension is unchanged. Updating stored vectors in place also
destroys the compatible index before the replacement has been validated.

## Decision

Keep profile-keyed shadow vectors in MySQL and persist active, previous and
requested profile pointers. Explicit serving initialization restores the active
runtime, then starts one database-elected background builder. Independent model
handles preserve serving resources during candidate loading and inference.

Persist bounded batches keyed by source ID and source-text hash. Reconciliation
resumes committed work and catches concurrent source changes. Before cutover,
lock publication state/source ranges, validate full coverage and vector contents,
and atomically change pointers. Ingestion shares the state lock and fences
obsolete staged profiles. Query profiles remain pinned across both searches;
retained vectors let requests already in flight complete. Rollback first catches
the previous generation up to current source text, then uses the same validation.

## Alternatives and consequences

In-place replacement loses the old index and creates partial coverage. Pausing
chat for bulk re-import avoids mixing but causes a retrieval outage. A dedicated
vector database/process could support larger corpora but adds deployment and
consistency boundaries beyond the existing NumPy/MySQL system.

This approach adds embedding RAM and per-generation storage, retains old vectors
without automatic garbage collection, and briefly blocks knowledge writers during
full validation. Source-text joins suppress stale/orphaned vectors. Artifacts must
remain immutable; a different executable dependency/normalization environment
requires a parallel deployment to preserve the old runtime across restart.

## Migration, recovery and verification

Additive schema version 10 preserves original knowledge and vectors. Initial
materialization re-embeds legacy text rather than guessing provenance. Saved
batches survive crashes, errors leave the serving pointer intact, and stale
requests cannot publish. See [operations and limits](../embedding-index-migration.md)
and the offline and disposable-MySQL migration regressions. Local SQL/model
doubles verify control flow and availability; actual model quality is a separate
acceptance measurement.
