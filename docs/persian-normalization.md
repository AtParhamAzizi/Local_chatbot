# Shared Persian normalization (F033)

`backend/persian_text.py` defines policy `fa-v1`. It creates a separate normalized
copy for Q&A identity, query/document embeddings and lexical comparison. Stored
question/answer text, extracted PDF chunk text, generator context, chat messages,
filenames and page citations retain their originals. Existing edge trimming and
PDF extraction/chunk cleanup still apply before that storage.

## Policy

| Input | Comparison/model copy |
| --- | --- |
| Arabic `ي`, `ى`, `ك` | Persian `ی`, `ی`, `ک` |
| Persian `۰۱۲۳۴۵۶۷۸۹`, Arabic-Indic `٠١٢٣٤٥٦٧٨٩` | ASCII `0123456789` |
| Unicode compatibility forms, including shaped PDF letters | NFKC forms, then the same Persian mapping |
| Whitespace, NBSP, tabs, line breaks | One space, with edges trimmed |
| ZWNJ half-space (`U+200C`), zero-width space (`U+200B`) | The same space boundary |
| Tatweel and optional Arabic marks `U+064B`–`U+0652` | Removed from the copy |
| ZWJ, word joiner, BOM, soft hyphen and selected bidi format controls | Removed from the copy |

For example, `ثبت‌نام كاربري ١٢٣؟` and `ثبت نام کاربری ۱۲۳؟` both use
`ثبت نام کاربری 123؟` as their comparison/model input. Both source spellings
remain available wherever they are stored or previewed. The normalizer is
idempotent, including combining sequences exposed by removing format controls.

This is not a morphological or spelling corrector. `می‌رود` and `می رود` share
a boundary, while `میرود` stays distinct. Different numbers, punctuation,
Latin letter case and hamza/alef letters remain distinct in hashes/model inputs.
Lexical terms add Unicode casefolding and word tokenization, preserving the
previous unique-term overlap calculation while separating Persian punctuation.
Credentials, session identities and file byte hashes never use this policy.

## Where it applies

- Q&A preparation hashes the normalized question and keeps the first original
  question/answer and source record number for within-file duplicates.
- Ingestion normalizes only each bounded embedding batch. Retrieval normalizes
  its query before invoking the embedding adapter. The public embedding function
  also normalizes before tokenization, including direct callers.
- PDF lexical scoring normalizes both query and original chunk text. Retrieval
  results still contain original questions, answers, PDF text and source metadata.
- Preflight shows original samples and counts normalized duplicates. Its
  `input_characters` estimate counts the actual normalized embedding inputs.
- Questions that become empty after normalization are invalid before any
  embedding or knowledge access. Format-only queries return no hits without
  model/search work. Format-only PDF chunks are omitted.

## Existing knowledge and replacement

New Q&A rows use SHA-256 of normalized UTF-8 in the existing unique
`question_hash` field. Existing keys and text are not rewritten at startup,
preview or import. When indexed normalized keys are missing, preview/publication
compare existing original questions in descending batches of 256. The scan keeps
only requested matches, uses decreasing IDs so new appends cannot extend it, and
checks cancellation between publication batches. A miss may scan all Q&A rows.

An indexed canonical-key match takes precedence. Otherwise the newest matching
legacy ID is selected. Historical duplicates are preserved and count as one
existing class for each unique input question. Without replacement, matched
records are skipped with their text, hashes, vectors and IDs unchanged. Explicit
replacement updates only the selected row's answer/vector, preserving its original
question, hash and ID. It does not merge or delete other aliases. The original
publication transaction and unique constraint handle new writes.

F033 itself needs no schema migration; F034 added profiles in versions 7–9.
Version 10 adds [resumable index rebuilding](embedding-index-migration.md);
serving now requires version 14, including [controlled import provenance](import-formats.md).
Original embedding columns remain unchanged while F035 freshly embeds source
text into separate profile-keyed generations. Replacement imports also remain
available; keep the Q&A answer unchanged when refreshing only a base vector.
[F034 embedding profiles](embedding-profiles.md) record the normalization version
and exclude unknown/incompatible vectors until their source is freshly embedded. See
[ADR 0007](decisions/0007-shared-persian-normalization.md).

## Validation

Run with the lightweight offline test dependencies:

```text
python -B backend/tests/run_tests.py --pattern test_persian_text.py -v
```

Authored equivalence/negative cases verify keys, terms, idempotence and the public
tokenizer boundary. An isolated SQLite store checks preview/import/deduplication,
legacy keys, non-destructive skips, replacement and original QA/PDF retrieval
with page provenance. Embedding doubles hash their exact inputs; equivalent
queries must return identical ranked hits and scores without hiding normalization
inside the doubles. A real Flask chat check verifies that history and generation
receive the original query/context.

The real-PyMuPDF `pdf_layout_eval.py` separately checks normalization of model
copies while storing the original structured chunks. The disposable MySQL suite
adds legacy-key preview/import/replacement/chat coverage; local execution was
blocked by the unavailable Docker daemon. These checks establish preprocessing
consistency and original preservation, not accuracy of real embedding/generation
models. The full corpus-scan cost and missing-space/spelling ambiguity remain
explicit limits.
