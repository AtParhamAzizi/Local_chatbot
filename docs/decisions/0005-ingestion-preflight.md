# 0005: Transient ingestion preflight using shared preparation

Date: 2026-11-09

Status: Implemented

## Context

F029 requires administrators to inspect duplicates, extracted content, chunk
counts and likely local processing work before publication. A separate parser
could disagree with publication, while a temporary searchable import would expose
unreviewed content. Preview must obey existing authentication and upload quotas.

## Decision

Add two authenticated POST preflight endpoints in API contract 1.3. They use the
same preparation methods as JSON/PDF publication, then query existing hashes.
They do not embed, load models, submit jobs, retain payloads or write knowledge.
Normal audit metadata is permitted. Reports are no-store and bound samples/issues.
Content problems are successful reports with actionable record/field/page issues;
transport and authorization errors keep the standard sanitized error envelope.

Preview is an advisory snapshot. Publishing sends the original bytes and validates
again, including current duplicates. Admin pages require a successful preview and
invalidate it on file/option changes; API publication remains independently safe
without a preview token. Work estimates count embedding inputs/batches/characters
instead of claiming unmeasured local timing, memory or monetary cost.

## Alternatives and consequences

A persisted approval token could bind publication to a retained snapshot, but
adds storage/expiry and concurrency policy without eliminating duplicate races.
Reusing the ingestion queue would create durable jobs for inspection and could
load models. Independent browser parsing cannot reproduce the PDF extraction or
server limits. Shared synchronous preparation avoids those changes, at the cost
of repeating extraction during publication and consuming an upload rate attempt.
Preview reads may become stale. Samples are excerpts, not a full content review.

## Migration and verification

No schema migration is required. Legacy publication endpoints retain their request
and response contracts. Reverting the additive endpoints also requires reverting
the admin preview controls and API contract revision.

Offline service/route/API/permission tests verify shared preparation, duplicate and
cost calculations, locations, bounds and no model or publication calls. Chrome
journeys verify preview-only interaction, safe text rendering, invalid input,
changed selections and publication/cancellation/retry compatibility. The disposable
MySQL regression compares knowledge and job rows before/after real PDF/JSON
previews; local execution requires a running Docker daemon.
