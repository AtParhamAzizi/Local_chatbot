"""Controlled formats validate before work and retain evidence through publication and retrieval."""

import hashlib
import importlib
import io
import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.sax.saxutils import escape

import numpy as np
from werkzeug.exceptions import RequestEntityTooLarge

from core_support import OfflineCoreFixture
from embedding_fixtures import with_profile
from test_persian_text import KnowledgeDatabase


WORD_NAMESPACE = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
SAMPLE = 'راهنمای ثبت درخواست\nپاسخ اصلی با کد ۱۲۳'


def docx_bytes(paragraphs=(SAMPLE,), *, document=None, extra=None, bom=False):
    main = document or ('<w:document xmlns:w="' + WORD_NAMESPACE + '"><w:body>'
                        + ''.join('<w:p><w:r><w:t>' + escape(value) + '</w:t></w:r></w:p>' for value in paragraphs)
                        + '</w:body></w:document>')
    parts = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
        'wordprocessingml.document.main+xml"/></Types>',
        '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>',
        'word/document.xml': ('\ufeff' if bom else '') + main,
    }
    parts.update(extra or {})
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content.encode('utf-8') if isinstance(content, str) else content)
    return output.getvalue()


def samples():
    return {'text.txt': SAMPLE.encode('utf-8'), 'guide.md': ('# راهنما\n\n' + SAMPLE).encode('utf-8'),
            'questions.csv': ('question,answer\nپرسش,پاسخ اصلی\n').encode('utf-8'), 'guide.docx': docx_bytes()}


class ControlledImportTests(OfflineCoreFixture, unittest.TestCase):
    def make_database(self, directory):
        return KnowledgeDatabase(Path(directory) / 'knowledge.sqlite')

    def setUp(self):
        super().setUp()
        self.importers = importlib.import_module('services.importers')
        self.ingestion = importlib.import_module('services.ingestion')
        directory = self.resources.enter_context(TemporaryDirectory())
        self.knowledge = self.make_database(directory)
        self.embed = Mock(side_effect=lambda values: np.ones((len(values), 3), dtype=np.float32))
        self.profiled_embed = with_profile(self.embed)
        self.service = self.ingestion.IngestionService(
            connect=self.knowledge.connect, embed=self.profiled_embed, audit=Mock(),
            hash_question=self.database.hash_question, batch_size=2, ocr=SimpleNamespace(enabled=False))

    def prepare(self, raw, filename):
        return self.importers.prepare_import(raw, filename)

    def test_each_enabled_format_has_explicit_encoding_hash_filename_and_location(self):
        for filename, raw in samples().items():
            with self.subTest(filename=filename):
                prepared = self.prepare(raw, '../private/' + filename)
                self.assertEqual(prepared.metadata['filename'], filename)
                self.assertEqual(prepared.metadata['sha256'], hashlib.sha256(raw).hexdigest())
                self.assertEqual(prepared.metadata['version'], 1)
                if prepared.records:
                    self.assertEqual(prepared.records[0]['location'], {'record': 1, 'line_start': 2, 'line_end': 2})
                else:
                    chunks = self.importers.import_chunks(prepared)
                    self.assertEqual(chunks[0]['source_metadata']['sha256'], prepared.metadata['sha256'])
                    self.assertIsNone(chunks[0]['page_start'])
                    location = chunks[0]['source_metadata']['location']
                    self.assertTrue('paragraph' in location or 'line_start' in location)
                self.assertEqual(prepared.metadata['encoding'], 'ooxml-utf-8' if filename.endswith('.docx') else 'utf-8')

    def test_each_enabled_format_rejects_malformed_before_database_or_embedding(self):
        malformed = {'bad.txt': b'hello\x00binary', 'bad.md': b'# heading\n```python\nunclosed',
                     'bad.csv': b'question,answer\n"unclosed,answer', 'bad.docx': b'PK\x03\x04truncated'}
        for filename, raw in malformed.items():
            with self.subTest(filename=filename), patch.object(self.service, 'connect') as connect:
                with self.assertRaises(self.importers.InvalidRequest):
                    self.service.ingest_import(raw, filename)
                connect.assert_not_called()
        self.embed.assert_not_called()

    def test_cancellation_during_preparation_is_not_misreported_as_file_corruption(self):
        for filename, raw in samples().items():
            with self.subTest(filename=filename), self.assertRaisesRegex(RuntimeError, 'ownership lost'):
                self.importers.prepare_import(raw, filename, guard=Mock(side_effect=RuntimeError('ownership lost')))

    def test_each_text_format_accepts_utf8_bom_and_rejects_invalid_or_guessed_encodings(self):
        for filename, raw in samples().items():
            if filename.endswith('.docx'):
                continue
            with self.subTest(filename=filename):
                prepared = self.prepare(b'\xef\xbb\xbf' + raw, filename)
                self.assertEqual(prepared.metadata['encoding'], 'utf-8-sig')
                for invalid in (b'\xff\xfe' + raw, raw + b'\xff', raw.decode().encode('utf-16')):
                    with self.assertRaises(self.importers.InvalidRequest):
                        self.prepare(invalid, filename)
                with self.assertRaises(self.importers.InvalidRequest):
                    self.prepare(raw + '\u0085'.encode(), filename)
        self.assertEqual(self.prepare(docx_bytes(bom=True), 'bom.docx').units[0].content, SAMPLE)
        for invalid in (b'<w:document>\xff</w:document>', '<document/>'.encode('utf-16')):
            with self.assertRaises(self.importers.InvalidRequest):
                self.prepare(docx_bytes(extra={'word/document.xml': invalid}), 'bad.docx')

    def test_each_document_duplicate_is_detected_before_embedding_across_names_and_encodings(self):
        for filename, raw in samples().items():
            if filename.endswith('.csv'):
                continue
            with self.subTest(filename=filename):
                self.service.ingest_import(raw, filename)
                self.embed.reset_mock()
                variant = docx_bytes(bom=True) if filename.endswith('.docx') else b'\xef\xbb\xbf' + raw.replace(b'\n', b'\r\n')
                preview = self.service.preflight(variant, 'renamed' + Path(filename).suffix, kind='import')
                self.assertTrue(preview['valid'])
                self.assertFalse(preview['can_publish'])
                self.assertEqual(preview['cost']['embedding_inputs'], 0)
                with self.assertRaises(self.ingestion.IngestionError) as error:
                    self.service.ingest_import(variant, 'renamed' + Path(filename).suffix)
                self.assertEqual(error.exception.status, 409)
                self.embed.assert_not_called()

    def test_each_enabled_format_publishes_source_metadata_that_reaches_context(self):
        for filename, raw in samples().items():
            with self.subTest(filename=filename):
                preview = self.service.preflight(raw, filename, kind='import')
                self.assertTrue(preview['can_publish'])
                self.assertIn('source_metadata', preview['samples'][0])
                result = self.service.ingest_import(raw, filename)
                self.assertEqual(result['source_metadata'], preview['source_metadata'])
                from api_contract_support import ApiContract
                contract = ApiContract()
                schema = 'ControlledQaImport' if filename.endswith('.csv') else 'ControlledDocumentImport'
                contract.assert_schema(result, {'$ref': '#/components/schemas/' + schema})
        with patch.object(self.scoring, 'get_conn', side_effect=self.knowledge.connect):
            batch = self.profiled_embed(['query'])
            vectors, profile = batch.vectors, batch.profile
            hits = self.scoring._search_qa(vectors[0], profile=profile) + self.scoring._search_pdf(vectors[0], profile=profile)
        self.assertEqual(len(hits), 5)
        self.assertEqual({hit['source_metadata']['format'] for hit in hits}, {'text', 'markdown', 'csv', 'docx'})
        for hit in hits:
            metadata = hit['source_metadata']
            context = self.scoring.build_context([hit])
            self.assertIn(metadata['filename'], context)
            self.assertIn(metadata['sha256'], context)
            self.assertIn('location', context)
            if hit['source_type'] == 'document':
                self.assertIsNone(hit['page_start'])

    def test_csv_deduplication_matches_existing_qa_preserves_first_source_and_replaces_atomically(self):
        raw = 'question,answer\nكيف علي,first\nکیف علی,second\n'.encode()
        preview = self.service.preflight(raw, 'first.csv', kind='import')
        self.assertEqual(preview['counts']['duplicates_in_file'], 1)
        first = self.service.ingest_import(raw, 'first.csv')
        self.assertEqual((first['received'], first['inserted'], first['skipped']), (2, 1, 1))
        stored = self.knowledge.rows()
        self.assertEqual(json.loads(stored[0]['source_metadata'])['location']['record'], 1)
        duplicate = b'\xef\xbb\xbf' + raw
        self.assertEqual(self.service.ingest_import(duplicate, 'second.csv')['skipped'], 2)
        self.assertEqual(self.knowledge.rows(), stored)
        self.assertEqual(self.service.ingest_import(duplicate, 'second.csv', replace=True)['updated'], 1)
        self.assertEqual(json.loads(self.knowledge.rows()[0]['source_metadata'])['filename'], 'second.csv')
        self.service.ingest_qa(json.dumps([{'question': 'کیف علی', 'answer': 'JSON replacement'}]).encode(), replace=True)
        self.assertIsNone(self.knowledge.rows()[0]['source_metadata'])

    def test_csv_multiline_escaped_fields_keep_physical_and_logical_record_locations(self):
        raw = 'question,answer\r\n"پرسش\r\nدوم","say ""yes"", please"\r\nnext,answer\r\n'.encode()
        records = self.prepare(raw, 'questions.csv').records
        self.assertEqual(records[0]['answer'], 'say "yes", please')
        self.assertEqual(records[0]['question'], 'پرسش\nدوم')
        self.assertEqual(records[0]['location'], {'record': 1, 'line_start': 2, 'line_end': 3})
        self.assertEqual(records[1]['location']['line_start'], 4)

    def test_csv_rejects_ambiguous_headers_fields_quoting_and_blank_answers(self):
        for raw in (b'answer,question\na,b', b'Question,answer\na,b', b'question,answer,extra\na,b,c',
                    b'question,answer\na', b'question,answer\na,', b'question,answer\na,b,c',
                    b'question,answer\na"b,c', b'question,answer\n"a"x,b', b'question,answer\n',
                    'question,answer\n\u200c,answer'.encode()):
            with self.subTest(raw=raw), self.assertRaises(self.importers.InvalidRequest):
                self.prepare(raw, 'bad.csv')

    def test_markdown_preserves_headings_code_and_line_provenance_without_following_links(self):
        raw = b'# First\n\n[local label](https://example.invalid)\n\n## Next\n```text\n<safe in code>\n```\n'
        prepared = self.prepare(raw, 'guide.md')
        self.assertEqual(prepared.units[1].location, {'line_start': 3, 'line_end': 3, 'headings': ['First']})
        self.assertIn('<safe in code>', prepared.units[-1].content)
        self.assertEqual(prepared.units[-1].location['headings'], ['First', 'Next'])
        skipped_levels = self.prepare(b'### Deep\n\n## Parent\nparagraph', 'levels.md')
        self.assertEqual(skipped_levels.units[-1].location['headings'], ['Parent'])
        for raw in (b'<script>bad</script>', b'---\nconfig: value\n---', b'~~~\nunclosed'):
            with self.assertRaises(self.importers.InvalidRequest):
                self.prepare(raw, 'bad.md')

    def test_docx_rejects_unsafe_or_unsupported_packages_and_entities(self):
        cases = [docx_bytes(extra={'../outside.xml': '<root/>'}),
                 docx_bytes(extra={'word/vbaProject.bin': b'code'}),
                 docx_bytes(extra={'word/embeddings/file.bin': b'code'}),
                 docx_bytes(extra={'word/document.xml': '<!DOCTYPE x [<!ENTITY value "unsafe">]><x>&value;</x>'}),
                 docx_bytes(extra={'word/document.xml': '<broken>'}),
                 docx_bytes(extra={'_rels/.rels': '<wrong/>'})]
        for element in ('drawing', 'ins', 'fldSimple', 'vanish', 'footnoteReference'):
            cases.append(docx_bytes(document='<w:document xmlns:w="' + WORD_NAMESPACE
                                    + '"><w:body><w:' + element + '/></w:body></w:document>'))
        external = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        external += '<Relationship Id="rId1" Type="test/attachedTemplate" Target="https://example.invalid" TargetMode="External"/></Relationships>'
        cases.append(docx_bytes(extra={'word/_rels/document.xml.rels': external}))
        for raw in cases:
            with self.subTest(length=len(raw)), self.assertRaises(self.importers.InvalidRequest):
                self.prepare(raw, 'bad.docx')

    def test_bounds_and_empty_files_are_enforced_before_embedding(self):
        for filename, raw in {'empty.txt': b'\xef\xbb\xbf', 'empty.md': b' \n', 'empty.csv': b'question,answer',
                              'empty.docx': docx_bytes(paragraphs=())}.items():
            with self.subTest(filename=filename), self.assertRaises(self.importers.InvalidRequest):
                self.service.ingest_import(raw, filename)
        oversized = [('large.txt', b'a' * 500001), ('large.csv', b'question,answer\n' + b'q,a\n' * 257),
                     ('many.md', b'paragraph\n\n' * 257), ('many.docx', docx_bytes(paragraphs=('paragraph',) * 257))]
        for filename, raw in oversized:
            with self.subTest(filename=filename), self.assertRaises(RequestEntityTooLarge):
                self.service.ingest_import(raw, filename)
        self.embed.assert_not_called()

    def test_docx_reads_runs_tables_tabs_and_breaks_with_paragraph_positions(self):
        document = '<w:document xmlns:w="' + WORD_NAMESPACE + '"><w:body><w:p/>'
        document += '<w:p><w:r><w:t>first</w:t><w:tab/><w:t>second</w:t><w:br/><w:t>third</w:t></w:r></w:p>'
        document += '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'
        prepared = self.prepare(docx_bytes(document=document), 'table.docx')
        self.assertEqual([unit.content for unit in prepared.units], ['first\tsecond\nthird', 'cell'])
        self.assertEqual([unit.location['paragraph'] for unit in prepared.units], [2, 3])

    def test_docx_corrupt_members_and_package_expansion_limits_are_rejected(self):
        raw = docx_bytes()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            member = archive.getinfo('word/document.xml')
            offset = member.header_offset + 30 + len(member.filename.encode()) + len(member.extra)
        corrupt = bytearray(raw)
        corrupt[offset + 8] ^= 0xff
        with self.assertRaises(self.importers.InvalidRequest):
            self.prepare(corrupt, 'corrupt.docx')
        with patch.object(self.importers, 'MAX_ZIP_ENTRIES', 2), self.assertRaises(RequestEntityTooLarge):
            self.prepare(raw, 'entries.docx')
        with patch.object(self.importers, 'MAX_XML_BYTES', 32), self.assertRaises(RequestEntityTooLarge):
            self.prepare(raw, 'xml.docx')
        for document in ('<node>' * 65 + '</node>' * 65, '<root>' + '<node/>' * 50000 + '</root>'):
            with self.assertRaises(RequestEntityTooLarge):
                self.prepare(docx_bytes(document=document), 'xml-limit.docx')
        with self.assertRaises(RequestEntityTooLarge):
            self.prepare(docx_bytes(extra={'padding.bin': b'x' * (1024 * 1024 + 1)}), 'bomb.docx')

    def test_all_extensions_are_explicit_and_docx_cannot_be_disguised_text(self):
        for filename in ('data.html', 'data.exe', 'data.doc', 'data.rtf', 'data.markdown', 'data.csv.exe'):
            with self.subTest(filename=filename), self.assertRaises(self.importers.InvalidRequest):
                self.prepare(b'content', filename)
        with self.assertRaises(self.importers.InvalidRequest):
            self.prepare(b'text', 'fake.docx')
        with self.assertRaises(self.importers.InvalidRequest):
            self.prepare(docx_bytes(), 'fake.txt')

    def test_chunk_offsets_reconstruct_original_evidence_and_do_not_cross_paragraphs(self):
        original = 'x' * 899 + '\n' + 'ی' * 1200
        prepared = self.prepare((original + '\n\nsecond paragraph').encode(), 'long.txt')
        chunks = self.importers.import_chunks(prepared)
        self.assertEqual(chunks[0]['source_metadata']['location']['line_end'], 1)
        for chunk in chunks[:-1]:
            location = chunk['source_metadata']['location']
            self.assertEqual(chunk['content'], original[location['character_start']:location['character_end']])
            self.assertLessEqual(len(chunk['content']), 900)
        self.assertEqual(chunks[-1]['source_metadata']['location']['line_start'], 4)
        self.assertEqual(chunks[-1]['content'], 'second paragraph')

    def test_failed_embedding_and_canceled_publication_leave_old_document_and_metadata_intact(self):
        for filename, raw in samples().items():
            with self.subTest(filename=filename):
                self.service.ingest_import(raw, filename)
                before = {table: self.knowledge.rows(table) for table in ('qa_documents', 'pdf_documents', 'pdf_chunks')}
                with patch.object(self.service, 'embed', side_effect=RuntimeError('model failed')):
                    with self.assertRaises(self.ingestion.IngestionError):
                        self.service.ingest_import(raw, filename, replace=True)

                def progress(event):
                    if event['stage'] == 'publishing' and event['completed'] == event['total']:
                        raise RuntimeError('ownership lost before commit')

                with self.assertRaises(RuntimeError):
                    self.service.ingest_import(raw, filename, replace=True, progress=progress)
                for table, rows in before.items():
                    self.assertEqual(self.knowledge.rows(table), rows)


if __name__ == '__main__':
    unittest.main()
