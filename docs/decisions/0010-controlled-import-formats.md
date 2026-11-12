# 0010: Controlled import adapters and source provenance

Date: 2026-11-12

Status: Implemented

## Context

Knowledge imports previously accepted strict JSON Q&A and bounded PDF text.
Enabling arbitrary extensions would leave encoding, duplicate identity, extraction
limits and source locations undefined. New files also need the existing durable
job, atomic publication and embedding compatibility guarantees.

## Decision

Enable only `.txt`, `.md`, `.csv` and `.docx` through the functional adapters in
`services/importers.py`. Each produces a validated `PreparedImport` with explicit
format/version/encoding, original-byte checksum, bounded basename and source
units or Q&A records with locations. Preparation is shared by preview and worker.
No generic parser fallback, network fetch, archive extraction or new dependency
is introduced. DOCX accepts a documented textual OOXML subset; unsupported
visual/active/ambiguous content is rejected for review and conversion.

CSV reuses Q&A normalization/deduplication. Other formats reuse the physical
document/chunk tables and the current embedding-index source inventory. Nullable
JSON source metadata publishes with content/vectors; generic documents have
NULL chunk pages and a distinct retrieval source type. Existing legacy sources
remain unlabelled. The same provenance reaches context and chat sources.

New routes and the dashboard page require the administrator role. Existing
collection grants govern listing/deleting the resulting knowledge. Session,
origin, CSRF, admission, job capacity and upload-checkpoint policies are reused.
The API contract advances to 1.5.0 and schema to 14 through forward migrations.

## Alternatives and consequences

A universal conversion library could support more formats but would expand
dependencies, subprocess/network behavior and extraction semantics before their
validation contracts exist. A second document/index storage stack would duplicate
publication and migration logic. Keeping the historical table names avoids that
duplication; operators must understand that their document list now includes
non-PDF sources with no page count. DOCX table geometry, visual content and
pagination are outside this adapter's contract.

Semantic document identity ignores container/encoding transport differences;
the exact upload checksum remains available for evidence. Location/layout changes
can create a new document, and cross-format copies remain separate sources.
Successful raw bytes retain the existing deletion policy. Operators retain their
source originals separately if required. Schema rollback uses a tested backup
and matching release, not narrowing columns after new writes.

## Verification and follow-up

[Format tests](../../backend/tests/test_controlled_imports.py) exercise every
enabled adapter's malformed-file, duplicate, encoding and metadata behavior,
publication rollback and retrieval with deterministic embeddings. Job/API/policy
tests cover durable publication, permissions and admission. Index scenarios
preserve imported evidence through cutover and rollback. Browser journeys verify
safe previews and job controls. Local disposable MySQL checks passed format
and index scenarios twice, plus HTTP job/chat provenance and schema upgrades.
No real embedding-quality
claim is made. See [format policy and commands](../import-formats.md).

## Related records

Depends on [atomic ingestion](0004-ingestion-publication-and-retries.md),
[shared preview preparation](0005-ingestion-preflight.md),
[normalization](0007-shared-persian-normalization.md),
[embedding profiles](0008-embedding-compatibility-profiles.md) and
[background index migration](0009-resumable-embedding-indexes.md).
