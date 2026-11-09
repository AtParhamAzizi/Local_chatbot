# 0004: Atomic ingestion publication and upload checkpoints

Date: 2026-11-09

Status: Implemented

## Context

F026 persisted queued uploads but knowledge writes still used autocommit. A
crash could leave partial searchable JSON, remove a prior PDF during replacement,
or publish knowledge without recording job success. Retrying those outcomes
could replay an import. Running cancellation requires a publication fence even
when a shared native embedding call cannot be interrupted safely.

## Decision

F027 uses the checksum-verified saved upload as its durable retry checkpoint.
Failed/canceled jobs retain it for 24 hours. Retrying keeps the job ID, revalidates
and re-embeds, and receives a fresh attempt token even within the same pool.
Completed jobs cannot be retried. Interrupted running jobs fail predictably and
require explicit retry; invalid inputs are not automatically replayed forever.

All knowledge writes and successful job completion, result and payload deletion
share one InnoDB transaction. The job row is locked only at the final ownership
check/finish, allowing cancellation during publication to win and force rollback.
Readers see only complete imports; previous PDFs stay searchable until commit.
Uncertain commit acknowledgements are resolved by the durable job result.

Progress persists counts for extraction, chunking, embedding and publishing.
Cancellation durably fences publication immediately and checks ownership at
work-unit boundaries. See [operations](../operations.md#durable-ingestion-jobs-f026)
for the precise cooperative bound; it is not a hard wall-clock guarantee for
native model/PDF or database calls. The browser remembers its job link across
reload and offers cancellation and same-ID checkpoint retries.

## Alternatives and consequences

Persisting model vectors at every micro-batch would avoid recomputation but
requires durable model identity, intermediate schemas, storage quotas and garbage
collection. Replaying from the upload avoids reusing vectors after model changes.
Killing threads in shared models is unsafe; process-isolated models would be
needed for strict wall-clock compute termination and would change model memory
requirements. Such a requirement should revisit this boundary explicitly.

The explicit transaction is necessary for safe cancellation/retry and also keeps
prior PDF replacements intact. Transaction size is bounded by existing 256-row/
chunk quotas; database transaction/binlog capacity still requires deployment
sizing. Failed/canceled upload storage can exceed active queue capacity during
its 24-hour retry window. Cleanup uses bounded sweeps and requires live serving.
The initial submission still has no transport idempotency key.

Migration 6 adds nullable progress without changing old knowledge. Operators
must drain/stop F026 workers before upgrading. Old started jobs without progress
are denied checkpoint retry because their partial autocommit writes cannot be
attributed safely. Migration rollback requires the normal restored database and
matching release; do not edit released migrations.

## Verification and follow-up

Offline job tests exercise durable progress, cancellation between batches, stale
attempts, expiry/capacity, invisible transaction writes and interrupted
publication rollback. API/permission tests cover retry authorization and CSRF.
Chrome journeys exercise stage counts, running cancellation, reload and retry
for JSON/PDF. The disposable MySQL suite adds later-row failure, canceled
publication and retained-PDF tests; Docker was unavailable for local execution.
Real model latency/hung-call behavior has not been measured here.

## Related records

[0001](0001-shared-serving-process.md) retains shared model ownership;
[0003](0003-explicit-migrations-and-local-models.md) governs migration/deployment.
