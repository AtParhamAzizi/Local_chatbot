# 0007: Shared Persian comparison copies with preserved originals

Date: 2026-11-11

Status: Implemented

## Context

Lexical retrieval previously folded Arabic yeh/kaf only, embeddings received raw
writing variants, and Q&A identity used SHA-256 of edge-trimmed text. Equivalent
digits, half-spaces and presentation forms could therefore produce different
keys and model inputs. Existing questions, answers and PDF evidence must retain
their original writing; existing unique hashes cannot simply be recomputed when
several historical rows collapse to the same normalized class.

## Decision

Use the standard-library `persian_text.py` policy `fa-v1` for comparison/model
copies. New Q&A hashes, ingestion/query embedding inputs and lexical terms share
that policy. Source storage, generation context, chat history, page metadata,
filenames and PDF byte identity stay original. Lexical terms additionally keep
their established casefold behavior. The detailed transformations and intentional
distinctions are specified in [the policy](../persian-normalization.md).

Keep the existing schema and unique key. Indexed canonical keys are preferred;
misses trigger a descending keyset scan of original questions in batches of 256.
Only the requested input classes (at most 256) are retained in memory. Scans
recognize pre-F033 hashes without modifying their text, keys, IDs or vectors.
The newest matching legacy alias is selected deterministically; multiple aliases
are retained. Replacement continues to update answer/vector only. New inserts
use normalized keys and the existing publication transaction/unique constraint.
Preview performs the same read-only comparison and counts matching input classes.

## Alternatives and consequences

Rehashing every existing row can violate uniqueness or require a destructive
merge. An alias/index schema would permit fast lookups while retaining duplicate
evidence but adds migration/backfill/recovery work. Read-through legacy matching
fits this local brute-force corpus and requires no serving DDL or startup writes.
The tradeoff is a full metadata scan for misses, with bounded batch memory and
cancellation checks. A persisted, versioned normalization index becomes appropriate
if corpus size makes this expensive; do not introduce a stale process cache.

The policy treats ZWNJ as an explicit word boundary rather than guessing missing
spaces or applying stemming. Punctuation, case and hamza/alef distinctions remain
in keys/model copies. Broad identity/security normalization is outside its scope.
Compatibility folding and optional-vowel removal apply only to derived copies.

Older vectors remain byte-for-byte unchanged until explicit replacement/import.
Queries consistently use the new policy, but real retrieval quality against
older vectors requires re-embedding. Future changes to the policy/model identity
need explicit version/compatibility handling (F034). Exact rollback to previous
hash/embedding semantics requires the matching old release and pre-change backup;
do not mass-rewrite keys in place when legacy aliases exist.

## Verification and follow-up

`test_persian_text.py` covers authored equivalence groups, distinct-word/number
controls, idempotence, the tokenizer boundary, SQLite-backed ingestion/preview,
legacy aliases, replacement, original context/history and QA/PDF ranking/page
metadata. Model doubles hash the exact adapter inputs, avoiding a normalizing
double that could mask a missing production boundary. The real-PDF layout
evaluation also passes with canonical model copies and original stored content.

A real MySQL regression was added. Its local runner could not provision because
the Docker daemon was unavailable; offline SQL evidence does not certify MySQL
locking. Real embedding quality is not measured by these deterministic tests.

## Related records

Extends [0004](0004-ingestion-publication-and-retries.md) publication and
[0005](0005-ingestion-preflight.md) shared preparation without changing their
transaction or authorization boundaries.
