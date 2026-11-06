"""Bounded QA/PDF ingestion without Flask request or response objects."""

import re
import time

import numpy as np
from werkzeug.exceptions import RequestEntityTooLarge

from request_validation import (
    MAX_ANSWER_CHARS, MAX_IMPORT_ROWS, MAX_MESSAGE_CHARS, MAX_PDF_CHUNKS,
    MAX_PDF_PAGES, MAX_PDF_TEXT_CHARS, InvalidRequest, parse_json, text, validate_object,
)
from services import adapters


PDF_CHUNK_SIZE = 900
PDF_CHUNK_OVERLAP = 160


class IngestionError(Exception):
    def __init__(self, payload, status):
        super().__init__(payload.get('error', 'Ingestion failed'))
        self.payload = payload
        self.status = status


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

    def __init__(self, *, connect=adapters.connect_database, embed=adapters.embed_texts,
                 open_pdf=adapters.open_pdf, hash_question=adapters.hash_question,
                 hash_document=adapters.hash_document, audit=adapters.audit,
                 clock=time.time, splitter=split_text):
        self.connect = connect
        self.embed = embed
        self.open_pdf = open_pdf
        self.hash_question = hash_question
        self.hash_document = hash_document
        self.audit = audit
        self.clock = clock
        self.split_text = splitter

    def ingest_qa(self, raw, *, replace=False):
        """Import UTF-8 JSON bytes; reject every invalid row before embedding/writes."""
        self.audit('Received a knowledge-base JSON upload')
        started = self.clock()

        payload = parse_json(raw)
        if not isinstance(payload, list) or not payload:
            raise InvalidRequest('The uploaded JSON must be a non-empty array of question/answer objects.')
        if len(payload) > MAX_IMPORT_ROWS:
            raise RequestEntityTooLarge()
        rows, seen = [], set()
        for item in payload:
            item = validate_object(item, {
                'question': text(MAX_MESSAGE_CHARS, 1), 'answer': text(MAX_ANSWER_CHARS, 1),
            }, required=('question', 'answer'))
            question, answer = item['question'], item['answer']
            question_hash = self.hash_question(question)
            if question_hash in seen:
                continue
            seen.add(question_hash)
            rows.append((question, answer, question_hash))

        try:
            vectors = self.embed([row[0] for row in rows])
        except Exception as error:
            raise IngestionError({'error': 'Request failed.'}, 500) from None
        dim = int(vectors.shape[1])
        conn = self.connect()
        inserted = updated = skipped = 0
        try:
            with conn.cursor() as cur:
                for (question, answer, question_hash), vec in zip(rows, vectors):
                    blob = vec.tobytes()
                    cur.execute("SELECT id FROM qa_documents WHERE question_hash = %s", (question_hash,))
                    existing = cur.fetchone()
                    if existing:
                        if replace:
                            cur.execute(
                                "UPDATE qa_documents "
                                "   SET answer=%s, embedding=%s, embedding_dim=%s "
                                " WHERE id=%s",
                                (answer, blob, dim, existing['id'])
                            )
                            self.audit(f"Updated knowledge-base record id={existing['id']}, embedding_dim={dim}")
                            updated += 1
                        else:
                            skipped += 1
                        continue
                    cur.execute(
                        "INSERT INTO qa_documents "
                        "  (question, answer, question_hash, embedding, embedding_dim) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (question, answer, question_hash, blob, dim)
                    )
                    self.audit(f'Inserted knowledge-base record id={cur.lastrowid}, embedding_dim={dim}')
                    inserted += 1
        finally:
            conn.close()
        self.audit(f'JSON import completed: inserted={inserted}, updated={updated}, skipped={skipped}')
        return {
            'received':     len(payload),
            'inserted':     inserted,
            'updated':      updated,
            'skipped':      skipped,
            'embedded':     inserted + updated,
            'duration_sec': round(self.clock() - started, 2),
            'errors':       [],
        }

    def ingest_pdf(self, raw, filename, *, replace=False):
        """Import bounded PDF bytes; preserve page metadata and legacy failure state."""
        started = self.clock()


        try:
            pdf = self.open_pdf(stream=raw, filetype='pdf')
        except Exception as error:
            raise IngestionError({
                'error': 'Request failed.'
            }, 400) from None
        pdf_page_count = pdf.page_count
        if pdf_page_count > MAX_PDF_PAGES:
            pdf.close()
            raise RequestEntityTooLarge()
        if pdf.page_count == 0:
            pdf.close()
            raise IngestionError({
                'error': 'فایل PDF هیچ صفحه‌ای ندارد'
            }, 400) from None
        pages = []
        total_text_chars = 0
        try:
            for page_number in range(pdf.page_count):
                page = pdf.load_page(page_number)
                text = page.get_text("text", sort=True)
                total_text_chars += len(text)
                if total_text_chars > MAX_PDF_TEXT_CHARS:
                    raise RequestEntityTooLarge()
                text = text.strip()
                if text:
                    pages.append({
                        'page': page_number + 1,
                        'text': text
                    })
        except RequestEntityTooLarge:
            raise
        except Exception as error:
            raise IngestionError({'error': 'Unable to extract PDF text.'}, 400) from None
        finally:
            pdf.close()
        if not pages:
            raise IngestionError({
                'error': (
                    'هیچ متن قابل استخراجی از PDF پیدا نشد. '
                    'ممکن است PDF اسکن‌شده باشد.'
                )
            }, 400) from None

        chunks = []
        chunk_index = 0
        for page in pages:
            page_chunks = self.split_text(
                page['text'],
                chunk_size=PDF_CHUNK_SIZE,
                overlap=PDF_CHUNK_OVERLAP
            )
            for content in page_chunks:
                if len(chunks) >= MAX_PDF_CHUNKS:
                    raise RequestEntityTooLarge()
                chunks.append({
                    'chunk_index': chunk_index,
                    'page_start': page['page'],
                    'page_end': page['page'],
                    'content': content
                })
                chunk_index += 1
        if not chunks:
            raise IngestionError({
                'error': 'هیچ chunk قابل استفاده‌ای از PDF تولید نشد'
            }, 400) from None
        document_hash = self.hash_document(raw)
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
        try:
            texts = [
                chunk['content']
                for chunk in chunks
            ]
            vectors = self.embed(texts)
            if vectors is None:
                raise RuntimeError(
                    'embed_texts هیچ برداری برنگرداند'
                )
            vectors = np.asarray(vectors)
            if vectors.ndim != 2:
                raise RuntimeError(
                    f'ابعاد embedding نامعتبر است: {vectors.shape}'
                )
            if vectors.shape[0] != len(chunks):
                raise RuntimeError(
                    'تعداد embeddingها با تعداد chunkها برابر نیست'
                )
            dim = int(vectors.shape[1])
        except Exception as error:
            raise IngestionError({
                'error': 'Request failed.'
            }, 500) from None
        conn = self.connect()
        document_id = None
        try:
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
                        status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        filename,
                        document_hash,
                        len(raw),
                        pdf_page_count,
                        len(chunks),
                        0,
                        dim,
                        'processing'
                    )
                )
                document_id = cur.lastrowid

                for chunk, vector in zip(chunks, vectors):
                    blob = np.asarray(
                        vector,
                        dtype=np.float32
                    ).tobytes()
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
                            embedding_dim
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            document_id,
                            chunk['chunk_index'],
                            chunk['page_start'],
                            chunk['page_end'],
                            chunk['content'],
                            blob,
                            dim
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
            conn.commit()
        except Exception as error:
            conn.rollback()
            try:
                with conn.cursor() as cur:
                    if document_id:
                        cur.execute(
                            """
                            UPDATE pdf_documents
                            SET
                                status = 'failed',
                                error_message = %s
                            WHERE id = %s
                            """,
                            (
                                'pdf_processing_failed',
                                document_id
                            )
                        )
                conn.commit()
            except Exception:
                pass
            raise IngestionError({
                'error': 'Request failed.'
            }, 500) from None
        finally:
            conn.close()

        return {
            'success': True,
            'document_id': document_id,
            'filename': filename,
            'file_size': len(raw),
            'page_count': pdf_page_count,
            'chunk_count': len(chunks),
            'embedded_chunks': len(chunks),
            'embedding_dim': dim,
            'duration_sec': round(
                self.clock() - started,
                2
            )
        }
