# 0006: Optional local Persian OCR with blocking confidence review

Date: 2026-11-09

Status: Implemented

## Context

Scanned Persian PDFs have no useful embedded text. F029 can preview text PDFs,
but accepting OCR without confidence review could publish incorrect knowledge.
Both preflight and publication must use identical extraction, respect cancellation
and resource bounds, and retain the original PDF page numbers for citations.

## Decision

Add a disabled-by-default local Tesseract `fas` adapter behind ingestion's
existing PDF preparation boundary. A typed server setting enables it; ordinary
imports/factories have no OCR executable or data requirement. A subprocess gives
word confidence through TSV and permits cancellation/timeout cleanup. Rendered
grayscale dimensions, output reads, OCR page count, per-page wait/runtime and
per-import budget are bounded. One shared slot and one OpenMP thread control
concurrent OCR resource use. No cloud fallback or runtime download exists.

Useful embedded text bypasses OCR. OCR replaces weak page text while preserving
original page numbers. Low mean confidence, any very uncertain word or unusable
output blocks the entire file before embeddings/writes; preflight exposes page
confidence, excerpts and actionable review issues. Administrators correct/re-scan
the source and preview again. There is no unchecked approval/bypass flag. Existing
atomic publication and attempt fencing remain authoritative for retries/cancellation.

Successful job JSON retains per-page method/confidence, excluding excerpts.
Existing chunk page fields preserve searchable provenance without a migration.
API contract 1.4 documents optional extraction metadata. This extends the
preparation behavior in [0005](0005-ingestion-preflight.md).

## Alternatives and consequences

PyMuPDF's integrated OCR does not provide the word-confidence review interface
used here. A Python neural OCR stack adds model/dependency/platform requirements;
a hosted API conflicts with local processing. Tesseract is an optional separately
provisioned system dependency. Confidence is heuristic and can miss high-confidence
mistakes; the fallback usefulness heuristic can miss long faulty text layers.

Per-file review blocks good pages alongside uncertain ones, avoiding searchable
partial documents. Explicit per-page approvals/edits would require a durable,
content-bound review workflow and schema; that is outside this change. Native
raster/SQL/process teardown retain work-unit rather than hard-second bounds.

## Verification and rollback

Offline regressions cover confidence parsing, Persian text, page metadata,
pre-publication rejection, disabled behavior, resource limits, child cleanup and
cancellation. API and Chrome tests cover metadata and review rendering. The local
OCR acceptance harness uses real recognition on a synthetic Persian scan with
isolated SQLite publication/retrieval and deterministic embedding adapters; the
separate CI job provisions the engine and language data. It does not certify
accuracy on every customer scan or real MySQL concurrency.

Disable `PDF_OCR_ENABLED` and restart to restore embedded-only extraction. Existing
published OCR knowledge remains searchable with original page provenance. To
remove or correct it, use the existing authorized document deletion/replacement
workflow. No schema rollback is needed.
