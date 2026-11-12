# Architecture decision records

Use an ADR to explain a durable choice and its consequences so a later
contributor can assess a proposed change. [ARCHITECTURE.md](../../ARCHITECTURE.md)
describes current behavior; ADRs explain why a boundary exists and what would
need reconsideration. This directory is not a second configuration registry or
API specification.

## Recorded implementation decisions

These records describe behavior at their stated dates; they do not assert an
unrecorded historical team approval or new feature delivery.

| Record | Decision |
| --- | --- |
| [0001](0001-shared-serving-process.md) | One shared serving worker with explicit initialization and a private optional TTS worker |
| [0002](0002-session-and-operation-boundaries.md) | Separate administrator sessions and anonymous chat ownership, explicit operation policies and CSRF |
| [0003](0003-explicit-migrations-and-local-models.md) | Explicit versioned migrations and locally prepared text models/dependency profiles |
| [0004](0004-ingestion-publication-and-retries.md) | Atomic knowledge/job publication, cooperative cancellation and saved-upload retries |
| [0005](0005-ingestion-preflight.md) | Transient ingestion preview using publication preparation, with advisory cost and duplicate counts |
| [0006](0006-local-persian-ocr.md) | Optional bounded local Persian OCR, original page provenance and blocking review of uncertain extraction |
| [0007](0007-shared-persian-normalization.md) | Shared Persian comparison/model copies with original text and legacy Q&A keys preserved |
| [0008](0008-embedding-compatibility-profiles.md) | Immutable embedding profiles captured with vectors, stored atomically and required for retrieval |

## When and how to add one

Write a record for changes to process topology/concurrency, persistence or schema
strategy, trust/identity/permission boundaries, model/dependency distribution,
public API compatibility or supported deployment platforms. Routine fixes and
wording changes normally need a PR explanation, not another ADR.

1. Copy [template.md](template.md) to the next unused four-digit number and a
   short descriptive filename. Keep real decisions in separate files.
2. Describe the concrete problem, constraints, chosen approach, realistic
   alternatives, tradeoffs, migration/rollback needs and verification evidence.
   Link code and tests; distinguish measured results from assumptions and work
   still proposed. Do not include secrets or private deployment/customer details.
3. Submit the record with the implementation or as a clearly marked proposal.
   A proposal is not implemented behavior and must not be documented as such in
   the README. Use normal code review to decide it; this guide adds no separate
   approval mechanism.
4. Update this index, architecture/operations guidance and any API/migration
   contract affected by the final change. Record the actual validation performed.
5. When changing an established decision, add a new record that supersedes it.
   Mark the old record as superseded and link both directions; preserve its
   original rationale rather than rewriting history.
