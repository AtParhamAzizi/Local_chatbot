# Local Persian PDF OCR (F030)

OCR is optional and disabled by default. It runs locally during PDF preview and
publication when embedded text is missing or not useful. No external OCR service,
Python OCR package, automatic model download or database migration is required.
Tesseract and its Persian `fas` traineddata must already be installed locally.

## Install and enable

On Debian/Ubuntu/WSL, provision the optional system dependencies:

```sh
sudo apt-get update
sudo apt-get install --no-install-recommends tesseract-ocr tesseract-ocr-fas
tesseract --list-langs
export PDF_OCR_ENABLED=1
```

On native Windows, install a trusted Tesseract 5 distribution with Persian
language data. Set these variables in the serving process's PowerShell
environment, adjusting the existing installation path:

```powershell
$env:PDF_OCR_ENABLED = '1'
$env:PDF_OCR_EXECUTABLE = 'C:/Program Files/Tesseract-OCR/tesseract.exe'
$env:PDF_OCR_TESSDATA = 'C:/Program Files/Tesseract-OCR/tessdata'
& $env:PDF_OCR_EXECUTABLE --list-langs
```

Restart serving after changing settings. `fas` must be listed.
The feature does not start OCR or inspect language files on application import
or factory creation; availability errors appear only when OCR is needed.
An installed WSL engine does not automatically enable the native Windows server.

For Compose, set `INSTALL_OCR=1` and `PDF_OCR_ENABLED=1` in `.env`, then rebuild
and recreate the chatbot container. `INSTALL_OCR` only installs system packages;
the runtime flag separately enables fallback. No weights are downloaded at runtime.
Native launchers read process environment variables rather than `.env`.

| Setting | Default / accepted values |
| --- | --- |
| `PDF_OCR_ENABLED` | `0`; boolean `0/1/false/true` |
| `PDF_OCR_EXECUTABLE` | `tesseract`; executable name on PATH or full trusted path, without shell arguments |
| `PDF_OCR_TESSDATA` | Unset; optional local data directory, relative paths resolve under `backend/` |
| `PDF_OCR_DPI` | `150`; integer 96-300 |
| `PDF_OCR_TIMEOUT_SECONDS` | `30`; integer 1-60, includes waiting for OCR capacity |
| `PDF_OCR_MIN_CONFIDENCE` | `75`; integer 1-100, character-weighted mean word confidence |
| `PDF_OCR_MIN_TEXT_CHARS` | `32`; integer 1-4096, minimum embedded/OCR alphabetic characters |

Useful text must also contain at least three distinct letters and no more than
2% Unicode replacement characters. This is a heuristic: long incorrect text layers
or headers can still look useful. Inspect samples, correct/re-export faulty text
layers or increase the minimum before retrying such documents. Existing useful
text remains unchanged. OCR detects separate text regions (Tesseract sparse-text
segmentation mode 11), using `fas` alone to avoid English substitutions for Persian
words. Reading order can vary across columns or tables; inspect
samples for layout errors. This feature is not a layout/table reconstruction system.

## Review before publishing

Choose the PDF and run **بررسی و پیش‌نمایش بدون انتشار**. Preview shows each
inspected page's original number, embedded/OCR method, confidence, minimum word
confidence and a bounded excerpt. The original PDF viewer remains available.
OCR replaces the weak embedded text of that page; it is not appended to it.

Any OCR mean below the configured threshold, any word below 50, or text that
fails the usefulness check marks the page for review and blocks the entire file.
Missing language data, timeouts, oversized images and extraction failures also
block publication with a page-specific remedy. Blank pages processed with OCR
are uncertain too: remove intentional blank pages or correct the source rather
than silently omitting content. Re-scan an upright, clear page, correct the text
layer with a local editor, or remove an intentionally empty page, then preview
the revised file again. There is no override that publishes uncertain OCR.

Confidence is an engine score, not a calibrated correctness probability. Even
high-confidence samples need administrator inspection for important knowledge.
Recognition is sensitive to scan resolution and typography; higher DPI does not
guarantee better recognition. Compare the samples with the source and adjust
`PDF_OCR_DPI` within its bounds if lines are missing or distorted.
At most 20 issue messages appear, but the extraction report includes all inspected
pages (up to the existing 200-page PDF cap), including their individual review
flags. Preview stores neither images nor text and performs no embeddings/writes
to searchable knowledge. Normal audit metadata may be written.

Publication re-extracts and rechecks confidence, including for direct API clients
and checkpoint retries. An uncertain or interrupted attempt writes no partial
knowledge or replacement. Successful chunks retain original `page_start/page_end`
for retrieval citations. Successful job results persist per-page method/confidence
without copying excerpts into job metadata; no new tables or migrations are needed.

## Capacity and cancellation

One shared OCR slot limits concurrent native processes across worker imports and
previews. Each process uses one OpenMP thread and local `fas` data. Files must
fit the existing upload/page/extracted-character/chunk quotas and these OCR limits:

- At most 20 pages requiring OCR per import; split longer scanned documents.
- At most 16 million grayscale pixels per page, checked before raster allocation.
- At most 2 MiB of TSV output is read/accepted per page.
- Default 30 seconds per OCR page including capacity waits; 120 seconds total OCR
  budget per import, checked between calls. Split documents when the budget expires.

Child-process and slot waits check cancellation/deadlines at most every 250 ms.
Cancellation or timeout kills and reaps the owned child and removes its private
temporary image/output directory. Rasterization, process teardown and database
guards are native/blocking operations, so there is no unconditional wall-clock
cancellation guarantee. This extends the existing extraction work unit with one
bounded raster operation; publication fencing still prevents canceled work from
becoming searchable. Preview has no durable job to cancel; closing the browser
does not promise immediate HTTP extraction cancellation, but OCR time budgets apply.
An operating-system kill or power loss bypasses Python cleanup; after stopping
the service, remove orphaned `chatbot-ocr-*` directories in its temporary folder
and terminate any orphaned Tesseract process before restarting.

## Verify

Offline behavior checks require no OCR engine:

```text
python -B backend/tests/run_tests.py --pattern test_ocr.py -v
python -B backend/tests/browser_smoke.py --channel chrome --grep 'OCR|preflight|background'
```

For actual language recognition, install the lightweight test requirements plus
PyMuPDF, Tesseract with `fas`, and Arial (Windows) or DejaVu Sans (Linux):

```text
python -B backend/tests/ocr_smoke.py
```

The acceptance script rasterizes a synthetic public two-page Persian document,
checks it has no embedded text, runs real OCR/preview/publication into temporary
SQLite, and verifies the real PDF retrieval scorer returns Persian text with
page provenance. A second fixture deliberately has an uncertain blank scanned
page; preview flags it and direct publication leaves prior searchable rows intact.
Embedding vectors are deterministic test adapters; this is not a model-quality
benchmark or a real MySQL locking test. Network access and deployment services
are forbidden. Windows can explicitly test an already prepared WSL engine using
`--wsl-distribution Ubuntu`; that option is only an acceptance harness.
Use `--font /path/to/local/font.ttf` to check another installed Persian font.

The independent CI OCR job provisions these system dependencies on Ubuntu and
runs this acceptance script. Ordinary offline discovery still uses OCR/PDF doubles.

Local acceptance on 2026-11-09 used Tesseract 5.5.0 in Ubuntu WSL with the CI's
DejaVu Sans font at the default 150 DPI. Both Persian pages passed paragraph-term
checks, published as two chunks, and retrieved with page 1 provenance (mean word
confidence 87.72/89.59). The deliberately uncertain second page blocked publication
and left the previously searchable document intact. This used the isolated SQLite
and deterministic embedding adapters described above.
