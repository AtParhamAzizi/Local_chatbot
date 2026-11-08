# 0003: Explicit migrations and prepared local text-model profiles

Date recorded: 2026-11-08

Status: Implemented; recorded from the current code

## Context and decision

Startup should not silently change persistent schema or resolve/download a
different text-model dependency stack. [schema_migrations.py](../../backend/schema_migrations.py)
and immutable ordered [migration files](../../backend/migrations/README.md)
provide explicit, locked and checksummed upgrades. Startup checks the ledger
read-only. Existing databases need stopped writers and a restore-tested backup;
backup identity is recorded, but the tool cannot certify restore validity.
Serving uses restricted DML credentials, separate from schema migration grants.

[model_registry.py](../../backend/model_registry.py) selects from two Qwen entries
using persisted/environment preference and config-presence fallback. The
embedder/generator load prepared local files, and selection does not re-embed
knowledge. [scripts/dependencies.py](../../scripts/dependencies.py) installs native
hash-locked CPU/cu118 profiles only into fresh environments and verifies their
inventory. The [dependency guide](../../requirements/README.md) records supported
targets and recreation/rollback limits. Whisper and optional TTS have separate
first-use download/cache behavior; this is not a promise that every speech path
is offline without preparation.

## Alternatives and consequences

Startup DDL would require persistent schema privileges and make interruptions
harder to audit. MySQL DDL commits implicitly, so recovery uses inspected resumable
steps or a tested restore into a clean replacement database, not a fictitious
transactional rollback. There is no automatic downgrade.

Unbounded dependency resolution or runtime text-model downloads would make a
restart depend on a changing external stack. The selected approach requires
up-front model storage, explicit package upgrades and separate speech caches.
Config presence cannot establish complete weights, memory capacity or answer
quality. Changing the embedder requires re-ingestion even when dimensions match.
ARM64 CPU containers are outside the recorded x86-64 lock matrix.

## Verification and reconsideration

[test_schema_migrations.py](../../backend/tests/test_schema_migrations.py),
[test_dependency_profiles.py](../../backend/tests/test_dependency_profiles.py)
and the [disposable MySQL tests](../../backend/tests/mysql/test_schema.py) cover
ledger/recovery and profile boundaries. Real GPU/model qualification remains
separate. A new model catalog, vector store, dependency target or migration
strategy needs its own compatibility, recovery and validation evidence.
