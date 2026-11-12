# API contract v1 (F022)

The checked-in `openapi.json` describes the supported chatbot UI and administrator
HTTP APIs, their request/response JSON Schemas, parameters, media types, permissions,
and voice WebSocket frames. Its `info.version` is the contract revision, currently
`1.5.0`. Both applications advertise `API-Version: 1` on successes, errors,
redirects, and HEAD responses. This is a response marker, not a negotiation header;
clients do not send it. Allowed UI origins can read it through CORS.

Revision 1.5.0 adds administrator-only controlled text/Markdown/CSV/DOCX import
preview/jobs, `kind=import`, `type=document` chat sources and optional source
provenance. The exact enabled formats, encoding rules, quotas and duplicate
semantics are defined in [import-formats.md](../import-formats.md).

The UI and admin are separate origins (development ports 5000 and 8000). Each
OpenAPI operation names its service and development server; substitute the actual
deployment HTTPS origin. The shared serving process does not combine the two
origins. Standalone `backend/STT/STT.py` and `backend/TTS/app.py` are demonstrations
with different routes; their `/voice`, `/stream`, `/api/status` and
`/api/synthesize` paths are outside this supported integration contract.

## Versioning and compatibility

- Existing unversioned paths are **v1** and remain supported throughout 1.x
  releases. No route is deprecated by this change. `/api/v1` is not an alias.
- Patch revisions document corrections or fix behavior to match the contract.
  Minor revisions may add endpoints or optional response properties. Update the
  contract, examples and tests together. Clients must ignore unknown response
  properties and JSON object ordering. Checked-in schemas deliberately reject
  undocumented output fields so CI detects drift before publication.
- Removing/renaming paths or fields, changing field types/nullability, making an
  optional input required, narrowing accepted inputs, changing pagination meaning,
  authentication requirements, status codes, or machine error-code meanings is a
  breaking change. Adding values to a documented closed enum is also breaking.
  New integrations must not infer behavior from human-readable error text.
- Breaking changes require a new major, a migration guide and parallel routes:
  future UI endpoints use `/api/v2/...`; future admin endpoints use
  `/admin/api/v2/...`. They are not implemented/reserved aliases today. A future
  major must retain v1 in parallel until a published deprecation period of at
  least 180 days has elapsed. Announce affected paths, replacement schemas and an
  exact removal date; add standard `Deprecation`, `Sunset` and migration `Link`
  headers then. No such headers or removal deadline apply to v1 today.
- Security fixes can restrict unsafe behavior immediately and must include release
  notes and regression tests. Historical unsafe behavior (client-supplied chat
  ownership, GET logout, missing CSRF) is not a compatibility promise.
- New collections should follow the documented page envelope. Existing response
  wrappers and date formats below stay unchanged in v1. New token authentication,
  cursors, idempotency keys or bulk operations require an explicit contract first.

## Requests and transport

### Scanned Persian PDF OCR (1.4, F030)

When the server's `PDF_OCR_ENABLED` is true, PDF preflight and publication share
the same optional local OCR fallback. No request field enables OCR or bypasses
confidence checks. The existing multipart/auth/CSRF/permission contracts remain.
An optional `extraction` property appears on PDF preview and successful import
results, including durable job results. It contains `ocr_enabled: true`,
`minimum_confidence`, `minimum_word_confidence` and `pages` (at most 200).
Each page has one-based `page`, `method: embedded|ocr`, nullable `confidence`
and `min_confidence` (0-100), and `needs_review`. Preview also includes a
`text_sample` of at most 240 characters. Successful job/import results omit
samples. Engine failures additionally identify a fixed `reason` code.

Useful embedded text bypasses OCR. For OCR, a mean below the configured threshold,
any word below 50, or unusable text blocks the whole import. Invalid previews
remain HTTP 200 with `can_publish=false`, actionable page issues and extraction
samples; direct publication fails validation before embeddings or knowledge writes.
Correct/re-scan the source and preview again. No uncertain page becomes searchable.
Confidence is an engine heuristic, not a probability of correctness. Successful
chunks and retrieved citations retain their original PDF page numbers.

Successful previews add `cost.ocr_pages` to the existing work estimate. OCR itself
has already run during preview and runs again on publication/retry. The optional
path obeys image, time, concurrency and per-file OCR limits in [OCR operations](../ocr.md).
No cloud requests or automatic language downloads occur.

### Ingestion preflight (1.3, F029)

POST `/admin/api/upload/preflight` or `/admin/api/pdf/upload/preflight` with the
same multipart `file`, `replace` and CSRF proof as an import. The respective
`json_upload`/`pdf_upload` permission is required. Preview shares the upload
rate budget (six attempts per client per five minutes), including publication
and retries; it does not consume queue capacity. It extracts and validates the
whole bounded file synchronously without embeddings, text-generation/embedding model loading, retained
upload bytes, jobs or searchable writes. Request audit metadata may be written.

HTTP 200 includes `valid`, `can_publish`, `counts`, `cost`, `samples`, `issues`
and `issues_truncated`, plus kind/filename/replace. Content errors use
`valid=false`, `can_publish=false`, null counts/cost and actionable issues.
Each issue has severity/message and, when applicable, a one-based `record`
and `field` or `page`. At most 20 issues are returned; fix those and preview
again if truncated. File-level syntax/shape errors have no record location.
Transport, multipart, extension, auth and byte-limit errors retain standard
HTTP errors. A structurally valid existing PDF has `valid=true` but
`can_publish=false` unless replacement is enabled. Mixed text/scanned PDFs
warn about pages with no extracted text; an entirely scanned PDF is invalid.

Counts distinguish `duplicates_in_file` (JSON questions; first occurrence wins)
from `duplicates_existing` (unique question matches or one exact-byte PDF).
`expected_chunks` is the prepared unique-question/PDF-chunk count. `inserts`,
`updates` and `skipped` count JSON records or PDF documents; JSON skipped includes
within-file duplicates. `records` is null for PDF and `pages` is null for JSON.
Existing questions still incur embedding work even when skipped, matching the
publication pipeline. A blocked duplicate PDF incurs zero embedding work.
Cost reports `embedding_inputs`, `embedding_batches`, `input_characters` and
configured `batch_size`; these are local workload estimates, not token, RAM,
currency or timing guarantees. Tokenizer truncation and hardware affect actual cost.

Samples select first/middle/last prepared records or chunks (up to three) and
truncate each text field to 240 characters. JSON samples retain original record
numbers after deduplication; PDF samples identify the source page. A preview
does not approve a stored upload: publish the original file separately. The
admin pages enable publication only after preview, invalidate it when the file
or replace option changes, and ignore responses for old selections. Publication
always revalidates and rechecks duplicates; concurrent imports can change counts.
All reports are `Cache-Control: no-store`.

### Background uploads and safe retries (1.2, F026/F027)

Use `POST /admin/api/upload/jobs` for JSON and `POST /admin/api/pdf/upload/jobs`
for PDF with the same bounded multipart fields and authorization as the legacy
upload paths. **202** returns `{job_id, kind, status: "queued"}`, a relative
`Location` and `Retry-After: 1`. Extraction, content validation and embedding
run after submission. Poll `GET Location` for `queued|running|completed|failed|canceled`,
UTC timestamps, `result` (the legacy import result on completion), and `error`
(an allowlisted `{code, error}` on failure). Polling returns 200 even when the
job failed; inspect `status`. Unknown/malformed/wrong-kind IDs return 404.

Status also includes `progress: {stage, source, completed, total}` or null,
`checkpoint: 'upload'` or null, and boolean `retryable`. Progress stages are
`extraction`, `chunking`, `embedding`, `publishing`; stage totals are independent.
`DELETE Location` cancels queued or running jobs (200); cancellation is idempotent,
and completed/failed jobs return 409. `POST Location/retry` with no body returns
202 and requeues failed/canceled work from its saved upload within 24 hours.
Queued retries are idempotent; running/completed/expired/missing-checkpoint jobs
return 409. Retries retain the same job ID and obey admission limits. Mutations require CSRF. JSON jobs
require `json_upload`, PDF jobs `pdf_upload`, including reads and cancellation;
any account with that grant may manage its kind's jobs. Unknown query fields
are rejected. Queue admission returns 503 when full/busy. There is no implicit
deduplication of submissions; keep the returned ID rather than retry a confirmed
submission. A lost submission response has no idempotency guarantee.

State and queued payloads persist in MySQL. After service restart, queued jobs
continue; interrupted running jobs fail with `ingestion_interrupted` and can be
retried from the upload checkpoint. Publication and job completion share one
transaction, preventing searchable partial data and replay after a committed
success. Completed jobs stay terminal. Failed/canceled bytes expire after 24
hours; successful bytes are deleted at commit. See the
[cancellation work-unit bound](../operations.md#durable-ingestion-jobs-f026),
retention and pre-F027 migration caveat. Legacy `/admin/api/upload` and
`/admin/api/pdf/upload` retain synchronous response schemas while using the same
worker queue; they wait at most 240 seconds, with 503 on timeout and work still queued/running.

Send UTF-8 JSON objects with `Content-Type: application/json` for JSON mutations.
JSON-compatible `application/*+json` types are also accepted. Wrong media types
return 415. Malformed UTF-8/JSON, duplicate object keys, non-finite numbers, unknown
fields, wrong types, and missing required fields return 400. Booleans are not
integers. Strings are trimmed except passwords; maximum lengths apply before
trimming and minimum lengths after trimming. Limits count Unicode characters.
Valid surrogate-pair encodings are accepted; isolated Unicode surrogates are not.

Ordinary bodies are capped at 65536 bytes and query strings at 8192 bytes. Uploads
allow 65536 bytes of multipart overhead beyond their file cap, at most eight form
parts, and 128 KiB of in-memory form fields. JSON file imports allow one nonempty
`.json` file up to 20 MiB containing 1-256 `{question, answer}` objects, question
length 1-4096 and answer length 1-16000. All rows are checked before model work.
PDF imports allow one nonempty `.pdf` file up to 50 MiB, 1-200 pages, at most
500000 extracted characters and 256 chunks. Extension checks are case-insensitive.
Scanned/no-text PDFs require the optional OCR path; unusable OCR still fails validation. Both upload routes accept optional form
`replace=true|false|1|0|yes|no|on|off` (default `false`) and `csrf_token`; values are
case-sensitive. Duplicate files/fields and unknown form fields are rejected.
Missing/invalid extensions return 400; size/page/text/chunk limits return 413.

Query-schema routes reject duplicate and unknown query parameters. Integers in
queries are unsigned decimal strings, without signs, fractions or whitespace.
Positive path IDs are bounded by 2147483647; chat-message IDs are bounded by
9223372036854775807. JSON IDs remain numbers in v1; clients must preserve 64-bit
integer precision rather than round IDs. List, session, log and message endpoints
do not return embedding bytes, password hashes, session tokens or ownership hashes.
Authorized model-catalog responses retain model paths for the administration UI.

Originless native HTTP calls are supported. A supplied browser `Origin` must be
explicitly allowed. Admin browser calls must also be same-origin; admin has no
CORS access. User CORS permits GET/HEAD/POST/OPTIONS and Content-Type, but does not
advertise credentialed cross-origin cookies; use same-origin browsers for feedback.
The voice WebSocket always requires an allowed UI `Origin`. Automatic HEAD and
OPTIONS remain available where Flask registers them, subject to the same guards;
HEAD responses have no body. Request size, origin, authentication, CSRF and admission
guards can reject a request before payload validation. Error precedence is not a
client contract.

## Authentication and authorization

There is no bearer token or API-key login. Use a cookie jar and the existing form
flow for administrator integrations:

1. GET `/login` on the admin origin; retain the cookie and extract the hidden
   `csrf_token` from its HTML form.
2. POST `/login` as a URL-encoded or multipart form with `username`, `password`
   and that token (or `X-CSRF-Token`). Success redirects 302 to `/` and rotates the
   session. Invalid credentials return HTML 401; login throttling returns HTML
   429. Both failures expose the machine code in `X-Error-Code`.
3. GET `/admin/api/session` with the new cookie. It returns
   `{user, csrf_token, allowed_pages}`; use this session-bound token on mutations.
4. Send `X-CSRF-Token` for every admin JSON mutation and DELETE, including POST
   `/logout`. Form uploads may instead include `csrf_token`. Never place a token
   in the query string. Logout revokes the session and redirects 302 to `/login`.

Production uses Secure/HttpOnly/host-only `__Host-local_chatbot_admin` cookies and
requires HTTPS. Explicit development uses `local_chatbot_admin_dev`. Absolute
and idle lifetimes are configurable; account security changes revoke sessions,
and changing the signed-in account clears its cookie. Unauthenticated admin APIs
and logout return JSON 401; unauthenticated HTML pages redirect to `/login`.
An authenticated user lacking permission, or a mutation lacking valid CSRF, gets
403. GET `/logout` is not supported and cannot revoke a session.

OpenAPI's `x-permission` names the required grant (`json_upload`, `pdf_upload`,
`models`, `chats`, `system_reports`, `controller`), `authenticated`, or
`administrator`. Active administrators can perform all registered operations;
account management specifically requires `is_admin`, even for wildcard-granted
non-admins. Unknown controller actions are denied with 403. `x-csrf-required`
also applies to login, although it has no authenticated-session requirement.

Chat needs no administrator login. GET `/` or POST `/chat` establishes an anonymous
ownership cookie when chat retention is enabled. Production uses
`__Host-local_chatbot_chat`; development uses `local_chatbot_chat_dev`. Feedback
requires that cookie and an owned `message_id`; missing/invalid cookies return 401
and unknown, foreign or legacy-unowned messages all return 404. `user_id` in chat
requests is accepted only for compatibility and ignored. Retention can disable
feedback (403) or all chat history (`message_id: null`, `feedback_enabled: false`);
answers still work. Public grouping IDs and IP addresses are not credentials.

## Payloads and pagination

All required fields, nested shapes and nullability are in `openapi.json`.
Representative chat exchange:

```json
{"message":"How do I contact support?","user_id":"legacy-client-id"}
```

```json
{
  "message_id": 12,
  "feedback_enabled": true,
  "reply": "Contact support at 1649.",
  "recommended_questions": [],
  "score": 0.9,
  "sources": [{"type":"qa","filename":null,"pages":null,"score":0.9}]
}
```

No retrieval hits means `score: null`, empty sources/recommendations and a fallback
reply. PDF sources include `[page_start, page_end]` instead of null pages.
POST `/tts/synthesize` accepts only `{text}` (1-5000 characters); success is
`audio/wav`, never a JSON envelope. `/health` is liveness, not dependency readiness.
Voice/TTS health may start lazy loading and return 503 until ready.

| Collection | Parameters and defaults | Response and ordering |
| --- | --- | --- |
| `/admin/api/documents` | `page=1` (1-10000), `per_page=10` (1-100), optional `q` (up to 256) | `{total,page,per_page,items}`; ID descending |
| `/admin/api/pdf/documents` | Same | Same envelope; ID descending |
| `/admin/api/chat/sessions` | `limit=300` (1-1000), optional `q` (up to 256) | `{sessions,count}`; last activity descending; no offset/cursor |
| `/admin/api/chat/messages` | Required `user_id` (1-64), optional `ip` (up to 45) | `{messages,count}`; all matching rows, created_at then ID ascending |
| `/admin/api/admin-users` | None | `{users,current_user_id}`; all users, username order |
| `/admin/api/logs` | None | `{success:true,logs}`; all rows, created_at then ID descending |
| `/admin/api/models` | None | `{active_model,models}`; catalog order, not pagination |

Page numbers are 1-based, not offsets. `total` is the filtered total, not the
current page size. An out-of-range page returns empty `items` with the requested
page/per_page and unchanged total, not 404. An empty collection returns zero and
an empty array. `count` on conversation responses is the number of returned rows,
not the full matching total. Lists without pagination do not accept page/cursor
semantics; adding bounded pagination to those responses requires a new contract.
QA search matches question/answer; PDF search matches filename; conversation
search matches user ID, IP, message and reply. SQL LIKE wildcards retain their
existing meaning. Concurrent writes can move records between pages; no snapshot
or cursor consistency is promised. Last-activity ties have no defined order.

Empty/absent `ip` in conversation reads/deletes means all groups for that user;
nonempty `ip` selects a group. DELETE document/message responses use
`{ok:true,deleted:ID}`; DELETE conversation uses `{ok:true,deleted:row_count}`.
These meanings are intentionally distinct in v1. Missing resources return 404.
Imports and account creation have their existing success wrappers: JSON import
counts, PDF `{success:true,document_id,...}`, and account creation HTTP 201
`{ok:true,id}`. Duplicate PDF imports return the normalized 409 error only, not
internal duplicate/document details.

Account timestamps and conversation timestamps use ISO-style database-local
strings with a space separator (or null). PDF-list and audit-log timestamps keep
Flask's HTTP-date strings (RFC 1123, or null). v1 does not promise a shared timezone
or a single date format across legacy collections. Model catalog order, generated
reply text, scores and operational timing values are not deterministic content.
POST controller actions return the same status shape with HTTP 200 for immediate
toggles and 202 for accepted model work; poll status and its `busy`/`errors` maps.
An accepted action can later fail with `model_action_failed` in `errors`; that is
an asynchronous result code, not a top-level HTTP error code.

## Errors, retries and privacy

JSON failures use `{code, error}` plus optional allowlisted `status` and
`retry_after`. `X-Error-Code` repeats the code and `Cache-Control: no-store` applies.
No SQL, traceback, exception description, nested details or secret values are
part of this envelope. HTML login 401/429 and bodyless HEAD responses are the
exceptions described above. Unknown 4xx statuses use `request_failed`; unknown
5xx statuses use `internal_error`. The status is still the original HTTP status.

| HTTP | Codes |
| --- | --- |
| 400 | `invalid_request` |
| 401 | `authentication_required` |
| 403 | `forbidden` |
| 404 | `not_found` |
| 405 | `method_not_allowed` (preserves `Allow`) |
| 409 | `conflict` |
| 413 | `payload_too_large` |
| 415 | `unsupported_media_type` |
| 429 | `rate_limited` |
| 500 | `internal_error`, `transcription_failed` |
| 503 | `service_unavailable`, `voice_loading`, `voice_unavailable`, `voice_model_failed`, `tts_loading`, `tts_unavailable` |

429 returns integer seconds in `Retry-After` and JSON `retry_after` when applicable;
honor that delay. Admission can depend on client IP and concurrent work, so opening
a new cookie jar does not bypass it. Do not automatically retry mutations after a
timeout or 5xx: a write may have committed and there is no idempotency-key guarantee.
Feedback overwrites an existing rating and document deletion remains 404 after
the first successful deletion. Readiness polling may retry after a delay; 503 does
not promise that a missing dependency will recover automatically. Successful chat,
feedback, TTS, controller feature and all admin responses are no-store.

## Voice protocol and browser compatibility

When socket dependencies are installed, GET `/voice-stream` upgrades to WebSocket
after origin and admission checks. Send mono 16 kHz signed little-endian PCM16
binary frames of at most 16000 bytes and finally `{"type":"stop"}`. The server
sends `ready`, zero or more `transcript` frames with segment IDs/final flags, then
`done`. Stop flushes pending transcription; `done` does not submit a chat message.
The client explicitly submits final text through `/chat`. Frames are documented
under OpenAPI `x-websocket` and the `Voice*` component schemas. Client stop frames
may have extra keys for legacy compatibility; HTTP JSON requests may not.
Failures after upgrade use `{type:"error",code,message,retry_after?}` (not HTTP
`error`); unready models, busy admission, transcription failure, and recording
limits use the documented codes. HTTP origin/admission failures use the normal
HTTP envelope before upgrade. Without socket dependencies the route is absent
and returns 404. Maximum wall/audio duration is 120 seconds; 30 seconds without
audio ends the session. The connection closes after completion or error.

Legacy browser entry points are retained:

| Service | Entry point | Promise |
| --- | --- | --- |
| UI | `/`, `/index.html` | Chat page; `/index.html` redirects 302 to `/` |
| UI | `/controller/features`, `/controller/features.js` | Existing JSON feature names and JavaScript helper |
| UI | `/voice/health`, `/voice/audio-worklet.js`, `/voice-stream` | Integrated voice paths; socket route conditional as above |
| Admin | `/`, `/login`, `/logout`, `/admin/api/session` | Existing dashboard/form/cookie flow and POST-only logout |
| Admin | `/login.css`, `/login.js`, `/login-theme.js` | Public login assets |
| Admin | `/admin-csrf.js`, `/theme.js`, `/dashboard.js`, `/dashboard.css` | Authenticated shared assets |
| Admin | `/admin/<filename>`, `/controller` | Existing policy-checked admin pages/controller |
| Admin | `/admin/api/chat/logs?id=ID` DELETE | Deletes a retained chat message, not an audit-log entry |

All existing `/admin/api/*` paths, wrappers and methods in OpenAPI remain v1.
This does not promise arbitrary filesystem/static aliases or nonexistent settings
pages. The frozen `backend/tests/admin_route_contract.py` inventory and F022 tests
guard legacy routes, including auth/CSRF behavior and ignored chat `user_id`.

## Verification and changes

Run `python -B backend/tests/run_tests.py --pattern test_api_contracts.py`, then the
full offline suite. Tests exercise real Flask routing, guards, serialization,
ingestion services, pagination SQL parameters, and WebSocket framing against the
checked-in schemas, using isolated database/model/PDF adapters. Successes, errors,
authentication, CSRF and legacy entry points are checked; no live DB, models,
network or microphone is needed. The small test assertion helper supports only
the JSON Schema keywords used here and fails on unknown keywords or external
references; it is not a general OpenAPI/JSON Schema validator. Existing MySQL and
browser suites separately exercise persistence and browser journeys.

Before changing an API, classify compatibility using this policy, update the
contract version and documentation, and extend contract tests for populated,
empty, invalid and permission-denied cases. Do not silently edit the legacy route
baseline to make a removal pass. Integration clients should pin a supported major,
check `API-Version`, preserve cookies/CSRF proofs, and branch on machine codes.
