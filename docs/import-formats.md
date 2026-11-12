# Controlled knowledge imports (F036)

Administrators can open **Text / Markdown / CSV / DOCX** in the dashboard or
`/admin/admin_import.html`. Select a source, preview its content and provenance,
then submit a background job. Changing the file or replacement option invalidates
the preview. Content is rendered as text. The worker repeats preparation before
embedding; preview is an advisory snapshot, never permission to bypass validation.

## Enabled adapters

Only these extensions are enabled. MIME declarations do not enable another
format, and changing an extension cannot bypass that adapter's content checks.
Legacy JSON and PDF imports keep their existing endpoints and limits.

| Extension | Content and validation | Source location |
| --- | --- | --- |
| `.txt` | Strict UTF-8, optional UTF-8 BOM; nonempty searchable text; binary controls rejected. Paragraphs are separated by blank lines. | Original line start/end and character offsets within the paragraph |
| `.md` | The same encoding/text policy; headings, paragraphs, lists, links and closed backtick/tilde fenced code remain original Markdown text. Raw HTML blocks and initial YAML front matter are rejected. Linked images, links and includes are never opened. | Original lines, enclosing ATX heading titles and offsets within the source unit |
| `.csv` | Strict UTF-8, optional BOM; exactly `question,answer` as the first header row, in that order; two fields per record. Quoted commas, escaped double quotes and multiline fields are supported. Ambiguous quotes, extra/missing fields, blank questions/answers and format-only questions are rejected. Questions/answers are bounded to 4096/16000 characters. | One-based logical data record plus physical line start/end, including multiline fields |
| `.docx` | A non-macro OOXML ZIP with the conventional `word/document.xml` main part and valid package declarations/relationships. XML parts must be UTF-8, optionally with a BOM. Paragraph/run text, table-cell paragraphs, tabs, line breaks and hyperlink labels are read locally in XML order. | Main-part path, one-based paragraph number (including empty paragraphs) and offsets within that paragraph |

Markdown ATX heading titles are limited to 240 characters each. Text decoding
never guesses another encoding. Convert legacy text/CSV to UTF-8
explicitly. CRLF/CR line endings become LF; spelling, punctuation and original
Persian/Arabic characters remain in stored evidence. The existing `fa-v1`
normalization applies only to embedding/comparison copies and Q&A duplicate keys.
CSV question/answer fields trim surrounding whitespace, matching JSON ingestion.

DOCX support deliberately accepts a textual subset. Drawings/images, embedded
files, macros/active content, external non-hyperlink relationships, alternate
markup, tracked changes, fields, hidden text and referenced headers/footers/
footnotes/endnotes block import. Unsupported markup in other Word XML parts also
blocks publication. Export these documents to reviewed text or the existing PDF
pipeline when their visual/field content matters. Table cells become paragraphs;
the adapter makes no page-layout or table-geometry claim. It never runs Word,
extracts archive paths to disk, follows links, resolves XML entities or invokes
OCR. DOCX page numbers are not fabricated.

## Bounds and publication

All new uploads have a 20 MiB byte limit and a 500000 decoded/extracted character
limit. CSV accepts 1–256 data records. Documents produce at most 256 chunks,
each at most 900 characters with 160-character overlap within a paragraph/source
unit; chunks never cross units. ZIP packages allow at most 256 entries and 16 MiB
total expanded bytes; each XML part is at most 4 MiB. Entries expanding beyond
both 1 MiB and 200 times their compressed size are rejected. XML depth/node
limits are 64/50000 per part. Unsafe paths, duplicate case-insensitive archive
names, encryption, corrupt members and unsupported compression are rejected.

The complete bounded file is validated before any model call or knowledge write.
Invalid content produces a failed job with `invalid_request`; resource overages
use `payload_too_large`. Preflight returns actionable content issues in a 200
report with `valid=false`, without creating jobs or retaining bytes.

Embeddings use the existing profiled batch staging pipeline. One transaction
publishes content, embeddings, source metadata and successful job completion;
failure or cancellation rolls back replacement and preserves old knowledge.
The active-index publication fence also applies. Background index rebuilding
and rollback retain the same source metadata.

CSV shares `qa_documents` with JSON. Within-file normalized question duplicates
keep the first validated occurrence; existing canonical/legacy questions are
skipped unless replacement is selected. Replacement keeps the existing original
question/hash/ID and updates its answer/vector/provenance. Skipping preserves
the old provenance. Later JSON replacement clears CSV provenance because that
answer no longer comes from the CSV. CSV result `skipped` includes within-file
duplicates as well as skipped database matches.

Other new formats share the existing document/chunk collection, physically
named `pdf_documents` / `pdf_chunks`. Document `page_count` is 0, chunk page
columns are NULL, and retrieval uses `source_type=document`. These records use
the existing document listing/deletion/cascade behavior; CSV uses Q&A management.
Existing collection permissions also govern their administration. The new
import endpoints/page themselves require the administrator role; upload grants
or wildcard feature grants alone do not enable them.

Document duplicates use a versioned SHA-256 of format, extracted source units
and locations. Renaming a file, adding a UTF-8 BOM, changing CRLF to LF, or
repacking the same DOCX XML text does not evade duplicate detection. Layout or
location changes can produce a new document. Different formats remain distinct.
A duplicate document fails with conflict unless replacement is selected; its
preflight remains structurally valid but cannot publish. The original-byte hash
is separate from this semantic identity.

## Provenance contract

`source_metadata` is stored in nullable JSON columns on Q&A, documents and
chunks. Old JSON/PDF rows remain NULL. The metadata records:

- `version: 1`, `format: text|markdown|csv|docx` and `importer: <format>-v1`;
- the bounded source basename (client directory prefixes are discarded);
- SHA-256 of the exact uploaded bytes and explicit encoding;
- `location` on each CSV row/document chunk using the format-specific coordinates.

Line, record and paragraph coordinates are one-based. Character offsets are
zero-based, half-open Unicode character positions in the source unit, after
line-ending normalization. Samples, completed job results, retrieval context
and chat `sources[].source_metadata` expose the applicable evidence. CSV chat
sources remain `type=qa`; document sources use `type=document` with `pages=null`.
No original source path is retained. Source filenames/excerpts are evidence,
not trusted instructions. Keep originals under the operator's source policy:
successful job upload bytes are deleted atomically; failed/canceled payloads
follow the existing 24-hour retry checkpoint policy.

## API and upgrade

API contract revision 1.5.0 adds these administrator-only routes:

| Method | Route | Behavior |
| --- | --- | --- |
| POST | `/admin/api/import/preflight` | Multipart `file`, optional `replace`; advisory preparation and provenance |
| POST | `/admin/api/import/jobs` | The same upload fields; 202, durable `kind=import`, Location and Retry-After |
| GET / DELETE | `/admin/api/import/jobs/{job_id}` | Poll / cancel an import job |
| POST | `/admin/api/import/jobs/{job_id}/retry` | Empty body; retry its retained upload checkpoint |

Mutations require existing session, origin, CSRF and shared upload admission
checks. File validation always runs again in the worker. An unsupported extension
is rejected at the upload boundary rather than dispatched to a generic parser.

Stop writers and apply migrations **11–14** using the existing
[backup and explicit upgrade procedure](../backend/migrations/README.md).
Version 11 extends job kinds and permits NULL document chunk page coordinates;
12–14 add nullable provenance columns. All steps are resumable and preserve
existing content, vectors and page values. Serving requires schema **14**.
Restore the pre-upgrade backup with its matching application to roll back the
schema; there is no destructive downgrade. Reprocessing extracted document text
is unnecessary for existing indexes.

## Verification

`python -B backend/tests/run_tests.py --pattern test_controlled_imports.py`
exercises malformed files, duplicate/encoding behavior, exact source locations,
bounded ZIP/XML parsing, metadata roundtrips, retrieval and atomic replacement
for every enabled format with deterministic embeddings and isolated SQLite.
The job, API, authorization, admission and index-migration suites cover shared
integration behavior. The SQLite job scenario doubles progress writes because
SQLite serializes writers across the whole database; it still runs the actual
knowledge/job publication transaction.

`python -B backend/tests/browser_smoke.py --grep 'controlled import' --repeat 2`
checks the real import page, all enabled file selections, safe preview rendering,
option invalidation, job polling/cancel/retry and CSRF with mocked API responses.
It does not certify real parsing or MySQL behavior.

`python -B backend/tests/run_mysql_tests.py --repeat 2` repeats the format
scenarios on disposable MySQL and exercises actual HTTP job publication and
chat provenance. This requires a running Docker Linux engine and never uses a
configured deployment database. Local validation passed the format and index
migration scenarios twice on disposable MySQL, including provenance after
cutover and rollback. HTTP job/chat provenance and schema-upgrade cases also
passed against disposable MySQL.
