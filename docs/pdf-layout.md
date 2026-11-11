# PDF layout extraction and evaluation (F031)

Embedded-text PDFs now retain column order, heading boundaries and ruled table
cells through ingestion. Both preflight and publication use the same preparation
in `backend/services/ingestion.py` and `backend/services/pdf_layout.py`.

## Behavior

Character positions split lines/spans that the PDF extractor joined across a
gutter or cell. Supported whitespace gutters separate columns; spanning material
divides the page into vertical bands. Predominantly Persian/Arabic text reads
rightmost columns first. Character sequences, mixed Latin text and numbers keep
their original logical order. Short large/bold text becomes `#` / `##` headings;
paragraph and line boundaries are retained for the existing splitter.

Ruled tables become pipe-delimited rows with explicit cell boundaries, empty
cells and escaped literal pipes. The extractor uses the original characters in
each detected cell, avoiding the geometry-based character reversal that can
affect Persian table extraction. RTL tables emit rightmost cells first. It does
not assume the first row is a heading or add a fabricated Markdown header.
Cell line breaks become spaces. Source text covered by a detected table is
replaced once. Recognition failure preserves the positioned text. Recognition
and cell copying share a process lock because older supported PyMuPDF releases
use shared recognizer scratch state.

Short margin lines must recur on at least two pages and 60% of all pages, occupy
the top 7% or bottom 8%, and use no larger than 110% of the body font size to be
removed. Numeric English/Persian page counters can change between pages. Body
occurrences of the same text, unique margin notes and single-page titles remain.

The source text quota is enforced before suppression/table detection; the final
structured text is also capped. No raster images are copied during text
extraction. OCR eligibility is evaluated on original text; OCR output still uses
its existing confidence/review path. Extraction closes the PDF on failure or
cancellation, and document passes check the publication guard between pages.
Chunks retain their original one-based page numbers and use the existing atomic
publication transaction. No schema, public API or dependency change is required.

To update an existing document, preview the same original PDF and use the
existing replacement option. Existing searchable chunks are not rewritten at
startup. Reverting the implementation affects subsequent imports; existing
published content requires explicit replacement to change.

## Reproducible acceptance evaluation

Install the lightweight `backend/tests/requirements.txt` and `PyMuPDF>=1.24,<2`
in a test environment, then run from the repository root:

```text
python -B backend/tests/pdf_layout_eval.py --output REPORT.json
```

`pdf_layout_fixture.py` generates corpus version 1: 11 public synthetic documents
with 13 pages. It covers single-column prose, two/three columns, spanning headings
and column bands, Persian RTL columns with Latin/Persian numbers, English and
Persian ruled tables, a table beside prose, recurring headers/changing counters,
unique notes, bold sections and an indented list. Expected content, order and
structure are specified independently from extraction. No customer documents or
binary fixtures are committed; MuPDF's bundled fonts render the Persian samples.

The baseline freezes the former `get_text('text', sort=True)` and whitespace /
soft-hyphen cleanup. Each fixture page fits one production chunk, so the new
measurements use actual prepared chunk content without overlap artifacts. The
evaluator also checks preflight samples, every embedded input against the shared
Persian-normalized copy (F033), persisted original chunk text and page numbers
using a fresh SQLite database and deterministic
embedding adapter. Network calls and inherited deployment configuration are
blocked; no real models or MySQL services are used.

Metrics count missing/duplicated content anchors or unwanted page counters,
missing heading/table structures, and inverted pairs of content anchors.
Only uniquely found anchors participate in order comparisons; missing or
duplicated anchors count as content errors, with the number of comparable pairs
reported separately. NFKC/whitespace normalization is for scoring only, so
presentation-form glyphs can match the authored Persian truth without altering
stored content. These are corpus checks, not estimates of customer-document
accuracy or downstream model quality.

Measured on November 11, 2026, Windows x64, Python 3.13.14, PyMuPDF 1.28.2:

| Metric | Flattened baseline | Layout-aware |
| --- | ---: | ---: |
| Content errors / 88 checks | 16 | 0 |
| Structure errors / 16 checks | 16 | 0 |
| Reading-order inversions | 41 | 0 |
| Comparable ordered pairs / 326 specified | 298 | 326 |

The [recorded JSON](evaluations/pdf-layout-v1.json) contains per-document scores
and both extracted outputs. Acceptance requires zero content/order errors, fewer
structure/order errors than baseline, and no per-document content/structure
regression. CI runs the evaluator in the provisioned OCR job, outside default
offline discovery. Offline geometry regressions cover additional negative and
resource/concurrency boundaries; see [test instructions](../backend/tests/TESTING.md).

Local validation also passed all 399 offline tests twice and the source checks.
The direct Windows suite encountered an existing temporary Git-directory lock
in the bytecode hygiene test. Its focused rerun passed; the complete repeated
run passed after initializing `tempfile.gettempdir()` before entering the test
runner, keeping temporary repositories in the system temp directory. No tests
were skipped or changed for that workaround.

## Limits

The corpus is synthetic and small. Borderless tables are retained as positioned
text, without guaranteed row/cell recognition. Unusual fonts, sparse/unequal
columns without supported gutters, sidebars, vertical/rotated writing, merged
cells and ambiguous heading styles can still need correction at the source.
OCR remains plain text, without layout reconstruction. Repeated section text in
the outer margin can be indistinguishable from running furniture.

The existing 900-character chunks / 160-character overlap can still split long
tables or sections; headings are not repeated on every continuation chunk. This
feature preserves structure through extraction and ordinary chunking; tokenizer
budgets and structural chunk continuation belong to F032. Native PDF/table calls
retain the existing page-unit cancellation limitation.
