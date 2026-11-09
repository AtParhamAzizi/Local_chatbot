"""Preflight shares publication preparation and never embeds or writes knowledge/jobs."""

import hashlib
import importlib
import io
import json
import unittest
from unittest.mock import MagicMock, Mock, patch

from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from core_support import OfflineCoreFixture
from test_abuse_controls import AbuseEndpointFixture


class PreflightServiceTests(OfflineCoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        service_module = importlib.import_module('services.ingestion')
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchall.return_value = []
        self.cursor.fetchone.return_value = None
        self.cursor.execute.side_effect = self.only_select
        self.embed = Mock(side_effect=AssertionError('Preflight must not embed'))
        self.service = service_module.IngestionService(
            connect=Mock(return_value=self.connection), embed=self.embed, open_pdf=Mock(),
            hash_question=lambda value: hashlib.sha256(value.strip().encode()).hexdigest(),
            hash_document=lambda value: hashlib.sha256(value).hexdigest(), audit=Mock(), batch_size=2,
        )

    def only_select(self, statement, parameters):
        self.assertTrue(statement.startswith('SELECT '), statement)

    def tearDown(self):
        super().tearDown()
        self.embed.assert_not_called()
        self.connection.begin.assert_not_called()
        self.connection.commit.assert_not_called()

    def qa(self, records, **options):
        return self.service.preflight(json.dumps(records).encode(), 'records.json', kind='json', **options)

    def pdf(self, texts, **options):
        document = Mock(page_count=len(texts))
        document.load_page.side_effect = [Mock(get_text=Mock(return_value=value)) for value in texts]
        self.service.open_pdf.return_value = document
        report = self.service.preflight(b'pdf', 'document.pdf', kind='pdf', **options)
        document.close.assert_called_once()
        return report

    def test_json_duplicate_counts_original_record_samples_and_processing_estimate(self):
        records = [{'question': ' first ', 'answer': ' original '},
                   {'question': 'first', 'answer': 'ignored'},
                   {'question': 'middle', 'answer': 'middle answer'},
                   {'question': 'last', 'answer': 'last answer'}]
        self.cursor.fetchall.return_value = [{'question_hash': self.service.hash_question('first')}]
        report = self.qa(records)
        self.assertTrue(report['valid'])
        self.assertTrue(report['can_publish'])
        self.assertEqual(report['counts'], dict(records=4, pages=None, duplicates_in_file=1,
                                               duplicates_existing=1, expected_chunks=3, inserts=2,
                                               updates=0, skipped=2))
        self.assertEqual(report['cost'], dict(embedding_inputs=3, embedding_batches=2,
                                             input_characters=15, batch_size=2))
        self.assertEqual([sample['record'] for sample in report['samples']], [1, 3, 4])
        self.assertEqual(report['samples'][0]['answer'], 'original')
        self.connection.close.assert_called_once()
        report = self.qa(records, replace=True)
        self.assertEqual((report['counts']['updates'], report['counts']['skipped']), (1, 1))
        self.assertEqual(report['cost']['embedding_inputs'], 3)

    def test_json_errors_identify_each_record_and_field_before_any_database_access(self):
        records = [{'question': 'valid', 'answer': 'valid'}, {'question': 'bad', 'answer': 3},
                   None, {'question': ' ', 'answer': 'answer', 'extra': True}]
        report = self.qa(records)
        self.assertFalse(report['valid'])
        self.assertFalse(report['can_publish'])
        self.assertEqual([issue['record'] for issue in report['issues']], [2, 3, 4, 4])
        self.assertEqual(report['issues'][0]['field'], 'answer')
        self.assertIn('string', report['issues'][0]['message'])
        with self.assertRaises(BadRequest):
            self.service.ingest_qa(json.dumps(records).encode())
        self.service.connect.assert_not_called()

    def test_strict_json_syntax_utf8_duplicates_empty_and_row_limit_match_ingestion(self):
        cases = [b'{', b'\xff', b'[]', b'{}', b'[{"question":"a","question":"b","answer":"c"}]',
                 json.dumps([{'question': str(index), 'answer': 'answer'} for index in range(257)]).encode()]
        for raw in cases:
            with self.subTest(raw=raw[:70]):
                report = self.service.preflight(raw, 'records.json', kind='json')
                self.assertFalse(report['can_publish'])
                self.assertTrue(report['issues'][0]['message'])
                with self.assertRaises((BadRequest, RequestEntityTooLarge)):
                    self.service.ingest_qa(raw)
        self.service.connect.assert_not_called()

    def test_reports_and_samples_are_bounded_and_unicode_validated(self):
        report = self.qa([{'question': '', 'answer': ''}] * 50)
        self.assertEqual(len(report['issues']), 20)
        self.assertTrue(report['issues_truncated'])
        report = self.qa([{'question': '\ud800', 'answer': 'answer'}])
        self.assertEqual(report['issues'][0]['field'], 'question')
        self.assertFalse(report['valid'])
        report = self.qa([{'question': str(index) + 'x' * 300, 'answer': 'y' * 400} for index in range(20)])
        self.assertEqual(len(report['samples']), 3)
        self.assertTrue(all(len(sample['question']) == len(sample['answer']) == 240 for sample in report['samples']))

    def test_pdf_uses_publishing_splitter_and_preserves_page_locations_and_empty_page_warning(self):
        texts = ['first page ' * 100, '', 'last page ' * 100]
        report = self.pdf(texts)
        expected = sum(len(self.service.split_text(value, chunk_size=900, overlap=160)) for value in texts)
        self.assertEqual(report['counts']['expected_chunks'], expected)
        self.assertEqual(report['cost']['embedding_inputs'], expected)
        self.assertEqual(report['samples'][0]['page'], 1)
        self.assertEqual(report['samples'][-1]['page'], 3)
        self.assertEqual(report['issues'][0]['page'], 2)
        self.assertEqual(report['issues'][0]['severity'], 'warning')
        self.assertTrue(report['can_publish'])

    def test_pdf_duplicate_blocks_publication_until_replacement_is_reviewed(self):
        self.cursor.fetchone.return_value = {'id': 7}
        report = self.pdf(['paragraph'])
        self.assertTrue(report['valid'])
        self.assertFalse(report['can_publish'])
        self.assertEqual(report['counts']['duplicates_existing'], 1)
        self.assertEqual(report['cost']['embedding_inputs'], 0)
        self.assertIn('replacement', report['issues'][0]['message'])
        report = self.pdf(['paragraph'], replace=True)
        self.assertTrue(report['can_publish'])
        self.assertEqual(report['counts']['updates'], 1)
        self.assertEqual(report['cost']['embedding_inputs'], 1)

    def test_pdf_failed_page_is_actionable_closes_parser_and_does_not_query_database(self):
        document = Mock(page_count=3)
        document.load_page.side_effect = [Mock(get_text=Mock(return_value='paragraph')),
                                         RuntimeError('sensitive parser internals')]
        self.service.open_pdf.return_value = document
        report = self.service.preflight(b'pdf', 'bad.pdf', kind='pdf')
        self.assertFalse(report['valid'])
        self.assertEqual(report['issues'][0]['page'], 2)
        self.assertNotIn('sensitive', str(report))
        document.close.assert_called_once()
        self.assertEqual(document.load_page.call_count, 2)
        self.service.connect.assert_not_called()

    def test_pdf_scan_limits_and_chunk_limits_report_the_responsible_page(self):
        report = self.pdf(['', ''])
        self.assertFalse(report['valid'])
        self.assertEqual([issue['page'] for issue in report['issues']], [1, 2])
        self.assertIn('OCR', report['issues'][0]['message'])
        report = self.pdf(['a' * 250001, 'a' * 250001])
        self.assertEqual(report['issues'][0]['page'], 2)
        self.assertIn('500000', report['issues'][0]['message'])
        self.service.split_text = Mock(side_effect=[['chunk'] * 256, ['overflow']])
        report = self.pdf(['first', 'last'])
        self.assertEqual(report['issues'][0]['page'], 2)
        self.assertIn('256', report['issues'][0]['message'])
        self.service.connect.assert_not_called()

    def test_pdf_page_count_and_unreadable_file_fail_without_extraction_or_query(self):
        document = Mock(page_count=201)
        self.service.open_pdf.return_value = document
        report = self.service.preflight(b'pdf', 'large.pdf', kind='pdf')
        self.assertFalse(report['valid'])
        self.assertIn('200', report['issues'][0]['message'])
        document.load_page.assert_not_called()
        document.close.assert_called_once()
        self.service.open_pdf.side_effect = RuntimeError('private parser error')
        report = self.service.preflight(b'pdf', 'unreadable.pdf', kind='pdf')
        self.assertIn('password', report['issues'][0]['message'])
        self.assertNotIn('private', str(report))
        self.service.connect.assert_not_called()


class PreflightRouteTests(AbuseEndpointFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.headers = self.authenticated_headers()

    def test_preview_uses_service_without_submitting_jobs_and_keeps_report_details(self):
        for kind, prefix in (('json', '/admin/api/upload'), ('pdf', '/admin/api/pdf/upload')):
            with self.subTest(kind=kind), patch.object(self.ingestion, 'preflight') as preview, \
                    patch.object(self.admin_app.extensions['ingestion_jobs'], 'submit') as submit:
                preview.return_value = {'valid': False, 'can_publish': False,
                                        'issues': [{'record': 2, 'field': 'answer', 'message': 'Use a string.'}]}
                response = self.admin_open(prefix + '/preflight', 'POST', headers=self.headers,
                                           data={'file': (io.BytesIO(b'content'), 'file.' + kind), 'replace': 'true'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json, preview.return_value)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                preview.assert_called_once_with(b'content', 'file.' + kind, kind=kind, replace=True)
                submit.assert_not_called()

    def test_preview_enforces_multipart_schema_and_limits_before_service_work(self):
        with patch.object(self.ingestion, 'preflight') as preview:
            for fields in ({}, {'file': (io.BytesIO(b'bad'), 'bad.txt')},
                           {'file': (io.BytesIO(b'[]'), 'data.json'), 'replace': 'invalid'},
                           {'file': (io.BytesIO(b''), 'empty.json')}):
                self.now += 1000
                response = self.admin_open('/admin/api/upload/preflight', 'POST', headers=self.headers, data=fields)
                self.assertIn(response.status_code, (400, 415))
            response = self.admin_open('/admin/api/pdf/upload/preflight?extra=1', 'POST', headers=self.headers,
                                       data={'file': (io.BytesIO(b'pdf'), 'file.pdf')})
            self.assertEqual(response.status_code, 400)
            preview.assert_not_called()

    def test_preview_accepts_uploads_larger_than_ordinary_json_requests_without_jobs(self):
        raw = b' ' * 70000 + b'[]'
        with patch.object(self.ingestion, 'preflight', return_value={'valid': False}) as preview:
            response = self.admin_open('/admin/api/upload/preflight', 'POST', headers=self.headers,
                                       data={'file': (io.BytesIO(raw), 'large.json')})
            self.assertEqual(response.status_code, 200)
            preview.assert_called_once_with(raw, 'large.json', kind='json', replace=False)

    def test_preview_consumes_the_existing_upload_rate_budget(self):
        with patch.object(self.ingestion, 'preflight', return_value={'valid': False}) as preview:
            for index in range(7):
                response = self.admin_open('/admin/api/upload/preflight', 'POST', headers=self.headers,
                                           data={'file': (io.BytesIO(b'[]'), 'records.json')})
                self.assertEqual(response.status_code, 200 if index < 6 else 429)
            self.assertEqual(preview.call_count, 6)
