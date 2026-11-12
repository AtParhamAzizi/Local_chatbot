"""Bounded QA/PDF ingestion without Flask request or response objects."""

import re
import time
from contextlib import ExitStack, contextmanager

from werkzeug.exceptions import RequestEntityTooLarge

from request_validation import (
    MAX_ANSWER_CHARS, MAX_IMPORT_ROWS, MAX_MESSAGE_CHARS, MAX_PDF_CHUNKS,
    MAX_PDF_PAGES, MAX_PDF_TEXT_CHARS, InvalidRequest, parse_json, text,
)
from services import adapters
from services.embedding_batches import staged_embeddings
from services.ocr import LocalOcr, MAX_OCR_PAGES, MIN_WORD_CONFIDENCE, OCR_BUDGET_SECONDS, OcrError, useful_text
from services.pdf_layout import LayoutPage, extract_page, extract_tables, finish_document
from settings import setting_value
from persian_text import normalize_persian


PDF_CHUNK_SIZE = 900
PDF_CHUNK_OVERLAP = 160


class IngestionError(Exception):
    def __init__(self, payload, status):
        super().__init__(payload.get('error', 'Ingestion failed'))
        self.payload = payload
        self.status = status


class PreparationInvalid(InvalidRequest):
    def __init__(self, issues, *, truncated=False):
        super().__init__(issues[0]['message'])
        self.issues = issues
        self.truncated = truncated


class PreparationLimit(RequestEntityTooLarge):
    def __init__(self, message, **location):
        super().__init__()
        self.issues = [{'message': message, **location}]


def split_text(text, chunk_size=1200, overlap=200):
    # Remove layout noise introduced by PDF extraction before embedding.
    text = text.replace('\u00ad', '')
    text = re.sub(r'(?<=\w)-\n(?=\w)', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    if not text:
        return []
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    chunks = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + chunk_size, text_len)
        # Try to find a natural breaking point.
        if end < text_len:
            candidates = [
                text.rfind("\n\n", start, end),
                text.rfind("\n", start, end),
                text.rfind(". ", start, end),
                text.rfind("؟ ", start, end),
                text.rfind("! ", start, end),
            ]
            best = max(candidates)
            if best > start + chunk_size * 0.5:
                end = best + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= text_len:
            break
        start = max(0, end - overlap)
    return chunks


class IngestionService:
    """Validate, embed and persist uploads using explicitly replaceable adapters."""

    def __init__(self, *, connect=adapters.connect_database, embed=adapters.embed_batch,
                 open_pdf=adapters.open_pdf, hash_question=adapters.hash_question,
                 hash_document=adapters.hash_document, audit=adapters.audit,
                 clock=time.time, splitter=split_text, batch_size=None, progress=None, ocr=None):
        self.connect = connect
        self.embed = embed
        self.open_pdf = open_pdf
        self.hash_question = hash_question
        self.hash_document = hash_document
        self.audit = audit
        self.clock = clock
        self.split_text = splitter
        self.batch_size = setting_value('EMBEDDING_BATCH_SIZE') if batch_size is None else batch_size
        if isinstance(self.batch_size, bool) or not isinstance(self.batch_size, int) or not 1 <= self.batch_size <= 64:
            raise ValueError('batch_size must be an integer from 1 to 64')
        self.progress = progress
        self.ocr = LocalOcr() if ocr is None else ocr

    def _report_embedding(self, source, completed, total):
        self.audit(f'{source} embedding progress: completed={completed}, total={total}')
        if self.progress is not None:
            self.progress({'stage': 'embedding', 'source': source, 'completed': completed, 'total': total})

    def _report(self, stage, source, completed, total, progress, guard):
        if guard is not None:
            guard()
        if progress is not None:
            progress({'stage': stage, 'source': source, 'completed': completed, 'total': total})

    @contextmanager
    def _publication(self, result, publication):
        if publication is not None:
            with publication(result) as connection:
                yield connection
            return
        connection = self.connect()
        try:
            connection.begin()
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _pdf_publication(self, result, publication):
        try:
            with self._publication(result, publication) as connection:
                yield connection
        except Exception:
            raise IngestionError({'error': 'Request failed.'}, 500) from None

    @contextmanager
    def _embeddings(self, records, *, text_for, source, publication_guard=None, progress=None):
        def report(completed, total):
            if publication_guard is not None:
                publication_guard()
            self._report_embedding(source, completed, total)
            if progress is not None:
                progress({'stage': 'embedding', 'source': source, 'completed': completed, 'total': total})

        with ExitStack() as resources:
            try:
                result = resources.enter_context(staged_embeddings(
                    records, embed=self.embed, batch_size=self.batch_size,
                    text_for=lambda record: normalize_persian(text_for(record)),
                    report=report,
                ))
            except Exception:
                self.audit(f'{source} embedding failed', warninglevel=3)
                raise IngestionError({'error': 'Request failed.'}, 500) from None
            yield result

    def _prepare_qa(self, raw, *, publication_guard=None, progress=None):
        self._report('extraction', 'json', 0, 1, progress, publication_guard)
        payload = parse_json(raw)
        if not isinstance(payload, list) or not payload:
            raise InvalidRequest('The uploaded JSON must be a non-empty array of question/answer objects.')
        if len(payload) > MAX_IMPORT_ROWS:
            raise PreparationLimit(f'Split the file into arrays of at most {MAX_IMPORT_ROWS} records.')
        self._report('extraction', 'json', 1, 1, progress, publication_guard)
        self._report('chunking', 'json', 0, len(payload), progress, publication_guard)
        rows, seen, issues, record_numbers = [], set(), [], []
        issue_count = 0
        for record_number, item in enumerate(payload, 1):
            if publication_guard is not None:
                publication_guard()
            record_issues = []
            if not isinstance(item, dict):
                record_issues.append({'message': 'Use an object with question and answer strings.'})
            else:
                if item.keys() - {'question', 'answer'}:
                    record_issues.append({'message': 'Remove fields other than question and answer.'})
                validated = {}
                for field, maximum in (('question', MAX_MESSAGE_CHARS), ('answer', MAX_ANSWER_CHARS)):
                    try:
                        validated[field] = text(maximum, 1)(item.get(field), field)
                        if field == 'question' and not normalize_persian(validated[field]):
                            raise InvalidRequest('The question must contain searchable text, not only formatting characters.')
                    except InvalidRequest as error:
                        record_issues.append({'field': field, 'message': error.description})
                item = validated
            for issue in record_issues:
                issue_count += 1
                if len(issues) < 20:
                    issues.append({'record': record_number, **issue})
            if record_issues:
                continue
            question, answer = item['question'], item['answer']
            question_hash = self.hash_question(normalize_persian(question))
            if question_hash in seen:
                continue
            seen.add(question_hash)
            rows.append((question, answer, question_hash))
            record_numbers.append(record_number)
        self._report('chunking', 'json', len(payload), len(payload), progress, publication_guard)
        if issues:
            raise PreparationInvalid(issues, truncated=issue_count > len(issues))
        return len(payload), rows, record_numbers

    def ingest_qa(self, raw, *, replace=False, publication_guard=None, progress=None, publication=None):
        """Import UTF-8 JSON bytes; reject every invalid row before embedding/writes."""
        self.audit('Received a knowledge-base JSON upload')
        started = self.clock()
        received, rows, _ = self._prepare_qa(raw, publication_guard=publication_guard, progress=progress)

        with self._embeddings(rows, text_for=lambda row: row[0], source='json',
                              publication_guard=publication_guard, progress=progress) as (vectors, profile):
            if publication_guard is not None:
                publication_guard()
            result = {'received': received, 'errors': []}
            with self._publication(result, publication) as conn:
                if self.embed is adapters.embed_batch:
                    adapters.guard_embedding_publication(conn, profile)
                inserted, updated, skipped = self._persist_qa(rows, vectors, profile, replace, conn, progress, publication_guard)
                result.update(inserted=inserted, updated=updated, skipped=skipped,
                              embedded=inserted + updated, duration_sec=round(self.clock() - started, 2))
        self.audit(f'JSON import completed: inserted={inserted}, updated={updated}, skipped={skipped}')
        return result

    def _persist_qa(self, rows, vectors, profile, replace, conn, progress, guard):
        dim, metadata = profile.dimension, profile.to_json()
        inserted = updated = skipped = 0
        legacy_matches = None
        self._report('publishing', 'json', 0, len(rows), progress, guard)
        with conn.cursor() as cur:
            for index, ((question, answer, question_hash), blob) in enumerate(zip(rows, vectors)):
                self._report('publishing', 'json', index, len(rows), progress, guard)
                cur.execute("SELECT id FROM qa_documents WHERE question_hash = %s", (question_hash,))
                existing = cur.fetchone()
                if existing is None:
                    if legacy_matches is None:
                        legacy_matches = self._legacy_qa_matches(cur, {row[2] for row in rows}, guard)
                    existing = legacy_matches.get(question_hash)
                if existing:
                    if replace:
                        cur.execute(
                            "UPDATE qa_documents "
                            "   SET answer=%s, embedding=%s, embedding_dim=%s, embedding_profile=%s "
                            " WHERE id=%s",
                            (answer, blob, dim, metadata, existing['id'])
                        )
                        self.audit(f"Updated knowledge-base record id={existing['id']}, embedding_dim={dim}")
                        updated += 1
                    else:
                        skipped += 1
                    continue
                cur.execute(
                    "INSERT INTO qa_documents "
                    "  (question, answer, question_hash, embedding, embedding_dim, embedding_profile) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (question, answer, question_hash, blob, dim, metadata)
                )
                self.audit(f'Inserted knowledge-base record id={cur.lastrowid}, embedding_dim={dim}')
                inserted += 1
        self._report('publishing', 'json', len(rows), len(rows), progress, guard)
        return inserted, updated, skipped

    def _legacy_qa_matches(self, cursor, keys, guard=None):
        """Match pre-F033 hashes without rewriting original questions or keys.

        Read at most 256 rows per batch, walking backwards so concurrent appends
        cannot extend the scan. Prefer the newest legacy alias deterministically;
        an indexed canonical-key match takes precedence at the caller.
        """
        matches = {}
        upper = (1 << 63) - 1
        while keys - matches.keys():
            if guard is not None:
                guard()
            cursor.execute('SELECT id, question FROM qa_documents WHERE id <= %s ORDER BY id DESC LIMIT 256', (upper,))
            records = cursor.fetchall()
            if not records:
                break
            for record in records:
                key = self.hash_question(normalize_persian(record['question']))
                if key in keys and key not in matches:
                    matches[key] = {'id': record['id']}
            upper = records[-1]['id'] - 1
            if len(records) < 256:
                break
        return matches

    def _prepare_pdf(self, raw, *, publication_guard=None, progress=None, extraction=None):
        extraction = {} if extraction is None else extraction
        if self.ocr.enabled:
            extraction.update(ocr_enabled=True, minimum_confidence=self.ocr.minimum_confidence,
                              minimum_word_confidence=MIN_WORD_CONFIDENCE, pages=[])
        ocr_deadline = time.monotonic() + OCR_BUDGET_SECONDS
        ocr_count = 0
        review_issues = []
        if publication_guard is not None:
            publication_guard()
        try:
            pdf = self.open_pdf(stream=raw, filetype='pdf')
        except Exception:
            raise PreparationInvalid([{'message': 'Open the PDF locally, remove password protection, and export a readable PDF.'}]) from None
        pdf_page_count = pdf.page_count
        if pdf_page_count > MAX_PDF_PAGES:
            pdf.close()
            raise PreparationLimit(f'Split the PDF into files of at most {MAX_PDF_PAGES} pages.')
        if pdf.page_count == 0:
            pdf.close()
            raise IngestionError({
                'error': 'فایل PDF هیچ صفحه‌ای ندارد'
            }, 400) from None
        pages, empty_pages, layouts = [], [], []
        total_text_chars = 0
        try:
            self._report('extraction', 'pdf', 0, pdf.page_count, progress, publication_guard)
            for page_number in range(pdf.page_count):
                if publication_guard is not None:
                    publication_guard()
                try:
                    page = pdf.load_page(page_number)
                    layout = extract_page(page)
                    text = layout.plain_text
                except Exception:
                    raise PreparationInvalid([{'page': page_number + 1,
                                               'message': 'Text extraction failed. Re-export this page as a readable PDF.'}]) from None
                total_text_chars += layout.source_chars
                if total_text_chars > MAX_PDF_TEXT_CHARS:
                    raise PreparationLimit(f'Extracted text exceeds {MAX_PDF_TEXT_CHARS} characters. Split the PDF before this page.',
                                           page=page_number + 1)
                if self.ocr.enabled:
                    metadata = {'page': page_number + 1, 'method': 'embedded', 'confidence': None,
                                'min_confidence': None, 'needs_review': False, 'text_sample': text[:240]}
                    extraction['pages'].append(metadata)
                    if not useful_text(text, self.ocr.minimum_text):
                        metadata['method'] = 'ocr'
                        ocr_count += 1
                        original_size = len(text)
                        try:
                            if ocr_count > MAX_OCR_PAGES:
                                raise OcrError('ocr_page_limit')
                            recognized = self.ocr.extract(page, guard=publication_guard, deadline=ocr_deadline)
                            text = recognized.text
                            metadata.update(confidence=recognized.confidence, min_confidence=recognized.min_confidence,
                                            text_sample=text[:240])
                            if (not useful_text(text, self.ocr.minimum_text) or recognized.confidence is None
                                    or recognized.confidence < self.ocr.minimum_confidence
                                    or recognized.min_confidence is None or recognized.min_confidence < MIN_WORD_CONFIDENCE):
                                metadata['needs_review'] = True
                                review_issues.append({'page': page_number + 1,
                                                      'message': 'Uncertain OCR. Review the extracted sample against this page, correct or re-scan the source, and preview again. Publication is blocked.'})
                        except OcrError as error:
                            metadata.update(needs_review=True, text_sample='', reason=error.code)
                            review_issues.append({'page': page_number + 1, 'message': str(error)})
                            text = ''
                        total_text_chars += len(text) - original_size
                        if total_text_chars > MAX_PDF_TEXT_CHARS:
                            raise PreparationLimit(f'Extracted text exceeds {MAX_PDF_TEXT_CHARS} characters. Split the PDF before this page.',
                                                   page=page_number + 1)
                        # OCR currently exposes plain text, without reliable cell geometry.
                        layout = LayoutPage(0, 0, plain_text=text, source_chars=len(text))
                if layout.width:
                    extract_tables(page, layout)
                layouts.append(layout)
                self._report('extraction', 'pdf', page_number + 1, pdf.page_count, progress, publication_guard)
        except (RequestEntityTooLarge, PreparationInvalid):
            raise
        except Exception as error:
            raise IngestionError({'error': 'Unable to extract PDF text.'}, 400) from None
        finally:
            pdf.close()
        if review_issues:
            raise PreparationInvalid(review_issues[:20], truncated=len(review_issues) > 20)
        finish_document(layouts, guard=publication_guard)
        structured_chars = 0
        for page_number, layout in enumerate(layouts, 1):
            if publication_guard is not None:
                publication_guard()
            text = layout.text.strip()
            structured_chars += len(text)
            if structured_chars > MAX_PDF_TEXT_CHARS:
                raise PreparationLimit(f'Extracted text exceeds {MAX_PDF_TEXT_CHARS} characters. Split the PDF before this page.',
                                       page=page_number)
            if extraction and extraction['pages'][page_number - 1]['method'] == 'embedded':
                extraction['pages'][page_number - 1]['text_sample'] = text[:240]
            if text:
                pages.append({'page': page_number, 'text': text})
            else:
                empty_pages.append(page_number)
        if not pages:
            raise PreparationInvalid([{'page': page_number, 'message': 'No extractable text. Apply OCR to this page before importing.'}
                                      for page_number in empty_pages[:20]], truncated=len(empty_pages) > 20)

        chunks = []
        chunk_index = 0
        self._report('chunking', 'pdf', 0, len(pages), progress, publication_guard)
        for page_index, page in enumerate(pages):
            if publication_guard is not None:
                publication_guard()
            page_chunks = self.split_text(
                page['text'],
                chunk_size=PDF_CHUNK_SIZE,
                overlap=PDF_CHUNK_OVERLAP
            )
            for content in page_chunks:
                if not normalize_persian(content):
                    continue
                if len(chunks) >= MAX_PDF_CHUNKS:
                    raise PreparationLimit(f'Chunk count exceeds {MAX_PDF_CHUNKS}. Split the PDF before this page.', page=page['page'])
                chunks.append({
                    'chunk_index': chunk_index,
                    'page_start': page['page'],
                    'page_end': page['page'],
                    'content': content
                })
                chunk_index += 1
            self._report('chunking', 'pdf', page_index + 1, len(pages), progress, publication_guard)
        if not chunks:
            raise IngestionError({
                'error': 'هیچ chunk قابل استفاده‌ای از PDF تولید نشد'
            }, 400) from None
        return pdf_page_count, chunks, empty_pages

    def ingest_pdf(self, raw, filename, *, replace=False, publication_guard=None, progress=None, publication=None):
        """Import bounded PDF bytes and publish the complete document atomically."""
        started = self.clock()
        extraction = {}
        pdf_page_count, chunks, _ = self._prepare_pdf(raw, publication_guard=publication_guard, progress=progress,
                                                       extraction=extraction)
        if publication_guard is not None:
            publication_guard()
        document_hash = self.hash_document(raw)
        if publication_guard is not None:
            publication_guard()
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT id, filename, status FROM pdf_documents WHERE document_hash = %s',
                    (document_hash,),
                )
                existing = cur.fetchone()
        finally:
            conn.close()
        if existing and not replace:
            raise IngestionError({
                'success': False, 'duplicate': True, 'message': 'This PDF has already been uploaded.',
                'document_id': existing['id'], 'filename': existing['filename'], 'status': existing['status'],
            }, 409) from None
        with self._embeddings(chunks, text_for=lambda chunk: chunk['content'], source='pdf',
                              publication_guard=publication_guard, progress=progress) as (vectors, profile):
            if publication_guard is not None:
                publication_guard()
            result = {
                'success': True,
                'filename': filename,
                'file_size': len(raw),
                'page_count': pdf_page_count,
                'chunk_count': len(chunks),
                'embedded_chunks': len(chunks),
                'embedding_dim': profile.dimension,
                'duration_sec': round(self.clock() - started, 2),
            }
            if extraction:
                result['extraction'] = {**extraction, 'pages': [
                    {key: value for key, value in page.items() if key != 'text_sample'} for page in extraction['pages']
                ]}
            with self._pdf_publication(result, publication) as conn:
                if self.embed is adapters.embed_batch:
                    adapters.guard_embedding_publication(conn, profile)
                result['document_id'] = self._persist_pdf(
                    raw, filename, document_hash, pdf_page_count, chunks, existing, replace, vectors, profile,
                    conn, progress, publication_guard,
                )
        return result

    def preflight(self, raw, filename, *, kind, replace=False):
        """Inspect the complete bounded upload without embedding, jobs or publication."""
        report = {
            'kind': kind, 'filename': filename, 'valid': False, 'can_publish': False,
            'replace': replace, 'counts': None, 'cost': None, 'samples': [],
            'issues': [], 'issues_truncated': False,
        }
        extraction = {}
        try:
            if kind == 'json':
                received, rows, record_numbers = self._prepare_qa(raw)
                connection = self.connect()
                try:
                    with connection.cursor() as cursor:
                        placeholders = ','.join(['%s'] * len(rows))
                        cursor.execute(f'SELECT question_hash FROM qa_documents WHERE question_hash IN ({placeholders})',
                                       tuple(row[2] for row in rows))
                        keys = {row[2] for row in rows}
                        found = {record['question_hash'] for record in cursor.fetchall()} & keys
                        found.update(self._legacy_qa_matches(cursor, keys - found))
                        existing = len(found)
                finally:
                    connection.close()
                inputs = [row[0] for row in rows]
                report['counts'] = {
                    'records': received, 'pages': None, 'duplicates_in_file': received - len(rows),
                    'duplicates_existing': existing, 'expected_chunks': len(rows),
                    'inserts': len(rows) - existing, 'updates': existing if replace else 0,
                    'skipped': received - len(rows) + (0 if replace else existing),
                }
                sample_indexes = sorted({0, len(rows) // 2, len(rows) - 1})
                report['samples'] = [{'record': record_numbers[index], 'question': rows[index][0][:240],
                                      'answer': rows[index][1][:240]} for index in sample_indexes]
            elif kind == 'pdf':
                page_count, chunks, empty_pages = self._prepare_pdf(raw, extraction=extraction)
                if extraction:
                    report['extraction'] = extraction
                connection = self.connect()
                try:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT id FROM pdf_documents WHERE document_hash = %s', (self.hash_document(raw),))
                        existing = bool(cursor.fetchone())
                finally:
                    connection.close()
                inputs = [chunk['content'] for chunk in chunks] if not existing or replace else []
                report['counts'] = {
                    'records': None, 'pages': page_count, 'duplicates_in_file': 0,
                    'duplicates_existing': int(existing), 'expected_chunks': len(chunks),
                    'inserts': int(not existing), 'updates': int(existing and replace),
                    'skipped': int(existing and not replace),
                }
                sample_indexes = sorted({0, len(chunks) // 2, len(chunks) - 1})
                report['samples'] = [{'page': chunks[index]['page_start'], 'text': chunks[index]['content'][:240]}
                                     for index in sample_indexes]
                report['issues'] = [{'severity': 'warning', 'page': page_number,
                                     'message': 'No extractable text on this page; it will be skipped. Apply OCR if it contains knowledge.'}
                                    for page_number in empty_pages[:20]]
                report['issues_truncated'] = len(empty_pages) > 20
                if existing and not replace:
                    report['issues'].insert(0, {'severity': 'error', 'message': 'This PDF already exists. Enable replacement or select a different PDF.'})
                    report['issues_truncated'] = report['issues_truncated'] or len(report['issues']) > 20
                    report['issues'] = report['issues'][:20]
            else:
                raise ValueError('Unsupported ingestion kind')
        except (InvalidRequest, RequestEntityTooLarge, IngestionError) as error:
            if extraction:
                report['extraction'] = extraction
            issues = getattr(error, 'issues', None)
            if issues is None:
                message = error.description if isinstance(error, InvalidRequest) else 'No usable PDF content. Export a PDF with extractable text.'
                issues = [{'message': message}]
            report['issues'] = [{'severity': 'error', **issue} for issue in issues]
            report['issues_truncated'] = getattr(error, 'truncated', False)
            return report
        report['valid'] = True
        report['can_publish'] = not any(issue['severity'] == 'error' for issue in report['issues'])
        report['cost'] = {
            'embedding_inputs': len(inputs), 'embedding_batches': (len(inputs) + self.batch_size - 1) // self.batch_size,
            'input_characters': sum(len(normalize_persian(value)) for value in inputs), 'batch_size': self.batch_size,
        }
        if extraction:
            report['cost']['ocr_pages'] = sum(page['method'] == 'ocr' for page in extraction['pages'])
        return report

    def _persist_pdf(self, raw, filename, document_hash, page_count, chunks, existing, replace, vectors, profile,
                     conn, progress, guard):
        dim, metadata = profile.dimension, profile.to_json()
        document_id = None
        self._report('publishing', 'pdf', 0, len(chunks), progress, guard)
        with conn.cursor() as cur:
            if existing and replace:
                document_id = existing['id']
                cur.execute(
                    """
                    DELETE FROM pdf_documents
                    WHERE id = %s
                    """,
                    (document_id,)
                )
            cur.execute(
                """
                INSERT INTO pdf_documents
                (
                    filename,
                    document_hash,
                    file_size,
                    page_count,
                    chunk_count,
                    embedded_chunk_count,
                    embedding_dim,
                    status,
                    embedding_profile
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    filename,
                    document_hash,
                    len(raw),
                    page_count,
                    len(chunks),
                    0,
                    dim,
                    'processing',
                    metadata
                )
            )
            document_id = cur.lastrowid

            for index, (chunk, blob) in enumerate(zip(chunks, vectors)):
                self._report('publishing', 'pdf', index, len(chunks), progress, guard)
                cur.execute(
                    """
                    INSERT INTO pdf_chunks
                    (
                        document_id,
                        chunk_index,
                        page_start,
                        page_end,
                        content,
                        embedding,
                        embedding_dim,
                        embedding_profile
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        document_id,
                        chunk['chunk_index'],
                        chunk['page_start'],
                        chunk['page_end'],
                        chunk['content'],
                        blob,
                        dim,
                        metadata
                    )
                )
            # ---------------------------------------------
            # Update status
            # ---------------------------------------------
            cur.execute(
                """
                UPDATE pdf_documents
                SET
                    embedded_chunk_count = %s,
                    status = 'completed'
                WHERE id = %s
                """,
                (
                    len(chunks),
                    document_id
                )
            )
        self._report('publishing', 'pdf', len(chunks), len(chunks), progress, guard)
        return document_id
