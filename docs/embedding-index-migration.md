# Resumable embedding-index migration (F035)

Embedding selection now builds a shadow index while requests continue using the
saved serving profile. Index storage starts at schema **10**; current serving
requires schema **14**, including [controlled import provenance](import-formats.md).
Follow the [backup and upgrade procedure](../backend/migrations/README.md).
Startup performs no DDL. No worker starts during imports or factory assembly.

## Select, observe and recover

Prepare the candidate in a **separate immutable local directory**. Keep the
serving and previous snapshots available. Change the catalog embedding path
through deployment configuration and restart, or use the existing authenticated,
CSRF-protected model selection operation for a catalog entry with another path.
Both current generator entries use the same embedding path by default, so changing
only the generator normally keeps the same embedding profile.

The serving process restores the database's active profile before accepting
requests. A changed configured model/path queues the candidate. Candidate weights
load in the background independently of the serving model; load failure leaves
retrieval on the current index. Model selection's existing success response means
the selection was saved, not that an embedding migration has finished.

Use the same environment/credentials as the server:

```powershell
python -B backend/reembed.py status
python -B backend/reembed.py retry
python -B backend/reembed.py rollback
```

`status` reads state and per-profile status, committed vector counts, validation
time and fixed failure codes. `active_id` identifies the serving profile;
`previous_id` identifies its rollback target; `requested_id` may remain NULL while
candidate loading is pending. `request_token` remains present until a successful
cutover. `error_code=index_build_failed` requires investigation and retry. These
commands never load models or perform re-embedding themselves; the running
server's elected worker processes requests.

Committed micro-batches are durable checkpoints. Process death or restart resumes
from missing or changed sources without repeating a completed prefix. On an
ordinary build failure, fix the underlying weights, memory, disk or DB issue and
run `retry`. Retry preserves good vectors and reissues the request. If artifact
bytes/profile intentionally changed after a failed candidate was identified,
select that catalog entry again to capture a new profile instead of assigning
the changed artifacts to its old identity.

`rollback` queues the previous profile and catches up additions/edits before
validating and switching. The current index stays available throughout rollback.
The active and previous pointers switch in one transaction; vectors for earlier
profiles remain stored. Rollback does not undo knowledge edits or change the
generator. A saved rollback survives restart; startup does not silently override
it with an unchanged configured selection.

## Consistency and availability

`embedding_indexes` stores complete immutable profiles and local model locators;
`embedding_index_vectors` stores `(profile, source kind, source ID, source SHA-256,
float32 bytes)`; `embedding_index_state` owns active/previous/request pointers.
Only completed PDF documents participate. Original Q&A answers, question hashes,
PDF text, filenames, page citations and existing embedding columns are preserved.
The initial serving generation is materialized before any candidate can replace
it, including freshly embedding sources whose original vectors have NULL profiles.
Unknown legacy vectors are never relabeled.

Each query captures one serving model/profile before inference and uses that
identity for both Q&A and PDF searches. Shadow vectors join live source rows only
when their text hash matches. Matching base vectors supply immediate visibility
for new active-profile uploads. Every scored vector has the query's exact profile;
retained generations let already-embedded queries finish after cutover.

The worker uses `EMBEDDING_BATCH_SIZE` (1–64, default 8). Each batch must have the
expected complete profile, shape, finite values and nonzero vectors. Source text
is reread for missing/hash-changed rows. Before switching, one repeatable-read
transaction locks the state and source ranges, checks complete live coverage and
decodes/validates every vector. It commits the new active and previous pointers
together. Source churn forces another catch-up pass. No inference occurs under
these publication locks. Normal retrieval uses nonlocking reads and remains
available while validation briefly blocks knowledge writes.

Ingestion locks the same state row before writing and rejects a batch staged
under an obsolete profile. Its knowledge/job transaction rolls back; retry that
upload's existing durable ingestion checkpoint. No partially published upload or
incompatible mixed index is exposed. A database-scoped named lock elects one
builder; request tokens prevent superseded builds from switching the index.

## Capacity and limits

Budget CPU RAM for both serving and candidate embedding weights during a build,
and for the previous runtime retained for quick rollback. Model calls serialize
per runtime; inference and validation still compete for CPU/DB resources. This
is availability continuity, not a latency SLA. Shadow storage grows by about
`source count * dimension * 4` bytes per profile plus keys and database overhead.
Old vectors are retained deliberately; automatic generation garbage collection
is not implemented. Deleted sources cannot be retrieved because searches join
live rows, though orphaned shadow bytes remain until operator cleanup.

Final validation scans all source vectors and locks source ranges; allow time
for knowledge writes to resume on large corpora. Continuous source churn can
delay migration. Status counts during building include checkpoint rows for
sources subsequently deleted; successful validation records the exact live count.

Keep serving/previous files immutable and keep their runtime dependencies
compatible. This process cannot execute an older normalization implementation or
older Python/model-library environment after replacing it in place. If a restart
cannot reproduce the saved serving profile, startup fails closed. Use a parallel
deployment with the old environment serving traffic for dependency/normalization
upgrades requiring different executable code, and retain that deployment for
rollback. Replacing the only serving snapshot in place defeats online recovery.

## Verification

`test_index_migration.py` exercises production orchestration, persistence and
retrieval using translated SQL in SQLite: equal-dimension migrations, continuous
queries, resume after failure/process death, stale-source reconciliation,
superseded requests, corrupt/profile-mismatched vectors, upload fencing, new
upload visibility, legacy preservation and rollback catch-up. The disposable
MySQL suite repeats those scenarios and adds concurrent validation/retrieval,
staged-upload locking and injected cutover-update rollback. Run it using
`python -B backend/tests/run_mysql_tests.py --repeat 2`; it never accepts an
existing deployment database. Model weights are doubled; retrieval-quality and
resource measurements with actual candidate models remain operator acceptance.
