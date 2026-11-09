"""Local OCR confidence, subprocess cleanup and pre-publication review boundaries."""

import importlib
import io
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import numpy as np
from werkzeug.exceptions import BadRequest

from core_support import OfflineCoreFixture


PERSIAN = 'راهنمای خدمات مشتریان برای دریافت پاسخ با مرکز پشتیبانی تماس بگیرید'
HEADER = 'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'


def tsv(words):
    return (HEADER + ''.join(f'5\t1\t1\t1\t1\t{index}\t0\t0\t10\t10\t{confidence}\t{word}\n'
                             for index, (word, confidence) in enumerate(words, 1))).encode()


class LocalOcrTests(OfflineCoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.module = importlib.import_module('services.ocr')

    def test_tsv_confidence_is_character_weighted_and_retains_the_least_certain_word(self):
        result = self.module.parse_tsv(tsv([('سلام', 96), ('دوست', 40)]))
        self.assertEqual(result.text, 'سلام دوست')
        self.assertEqual((result.confidence, result.min_confidence), (68, 40))
        self.assertIsNone(self.module.parse_tsv(HEADER.encode()).confidence)
        for raw in (b'invalid', b'\xff', tsv([('word', 'nan')]), tsv([('word', -1)]),
                    tsv([('word', 101)]), b'x' * (self.module.MAX_TSV_BYTES + 1)):
            with self.subTest(raw=raw[:50]), self.assertRaises(self.module.OcrError):
                self.module.parse_tsv(raw)

    def test_useful_text_rejects_empty_garbled_and_tiny_text_but_accepts_persian(self):
        for value in ('', '12345678', 'xx' * 40, 'عنوان', '\ufffd' * 20 + PERSIAN):
            self.assertFalse(self.module.useful_text(value))
        self.assertTrue(self.module.useful_text(PERSIAN))

    def test_local_command_uses_fixed_languages_no_shell_and_removes_temporary_images(self):
        process = Mock()
        process.wait.return_value = 0
        process.poll.return_value = 0
        commands = []

        def start(command, **options):
            commands.append((command, options))
            Path(command[2] + '.tsv').write_bytes(tsv([('سلام', 99), ('دوستان', 98)]))
            return process

        engine = self.module.LocalOcr(enabled=True, process_factory=start)
        engine._render = Mock()
        engine.extract(Mock())
        command, options = commands[0]
        self.assertEqual(command[-1], 'tsv')
        self.assertEqual(command[command.index('-l') + 1], 'fas')
        self.assertEqual(command[command.index('--psm') + 1], '11')
        self.assertNotIn('shell', options)
        self.assertEqual(options['env']['OMP_THREAD_LIMIT'], '1')
        self.assertFalse(Path(command[1]).parent.exists())
        process.kill.assert_not_called()

    def test_timeout_kills_and_reaps_process_releases_capacity_and_cleans_files(self):
        current = [0.0]
        process = Mock()
        process.poll.return_value = None

        def waiting(timeout=None):
            if timeout is not None:
                current[0] += 1
                raise subprocess.TimeoutExpired('local-ocr', timeout)
            return -1

        process.wait.side_effect = waiting
        engine = self.module.LocalOcr(enabled=True, clock=lambda: current[0], process_factory=Mock(return_value=process))
        engine.timeout = 1
        engine._render = Mock()
        with self.assertRaises(self.module.OcrError) as failure:
            engine.extract(Mock())
        self.assertEqual(failure.exception.code, 'ocr_timeout')
        process.kill.assert_called_once()
        process.wait.assert_any_call()
        filename = engine._render.call_args.args[1]
        self.assertFalse(filename.parent.exists())
        self.assertTrue(self.module.OCR_SLOT.acquire(blocking=False))
        self.module.OCR_SLOT.release()

    def test_cancellation_during_ocr_kills_child_and_never_waits_for_another_page(self):
        process = Mock()
        process.wait.side_effect = lambda timeout=None: -1 if timeout is None else self.waiting(timeout)
        process.poll.return_value = None
        engine = self.module.LocalOcr(enabled=True, process_factory=Mock(return_value=process))
        engine._render = Mock()
        checks = []

        def guard():
            checks.append(True)
            if len(checks) == 5:
                raise RuntimeError('Canceled')

        with self.assertRaisesRegex(RuntimeError, 'Canceled'):
            engine.extract(Mock(), guard=guard)
        process.kill.assert_called_once()
        self.assertFalse(engine._render.call_args.args[1].parent.exists())

    def waiting(self, timeout):
        raise subprocess.TimeoutExpired('local-ocr', timeout)

    def test_missing_engine_output_and_failed_child_return_only_actionable_codes(self):
        for factory, code in ((Mock(side_effect=FileNotFoundError('private binary path')), 'ocr_unavailable'),
                              (Mock(return_value=Mock(wait=Mock(return_value=1), poll=Mock(return_value=1))), 'ocr_failed'),
                              (Mock(return_value=Mock(wait=Mock(return_value=0), poll=Mock(return_value=0))), 'ocr_failed')):
            engine = self.module.LocalOcr(enabled=True, process_factory=factory)
            engine._render = Mock()
            with self.assertRaises(self.module.OcrError) as failure:
                engine.extract(Mock())
            self.assertEqual(failure.exception.code, code)
            self.assertNotIn('private', str(failure.exception))
            self.assertFalse(engine._render.call_args.args[1].parent.exists())

    def test_image_dimensions_are_checked_before_allocating_a_pixmap(self):
        engine = self.module.LocalOcr(enabled=True)
        for width, height in ((1e7, 1e7), (float('nan'), 10), (0, 100)):
            page = Mock(rect=SimpleNamespace(width=width, height=height))
            with self.assertRaises(self.module.OcrError) as failure:
                engine._render(page, 'unused.png')
            self.assertEqual(failure.exception.code, 'ocr_image_limit')
            page.get_pixmap.assert_not_called()


class OcrIngestionTests(OfflineCoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.module = importlib.import_module('services.ingestion')
        self.ocr_module = importlib.import_module('services.ocr')
        self.engine = SimpleNamespace(enabled=True, minimum_text=32, minimum_confidence=75,
                                      extract=Mock(return_value=self.ocr_module.OcrText(PERSIAN, 98, 94)))
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.return_value = None
        self.embed = Mock(side_effect=lambda texts: np.ones((len(texts), 2), dtype=np.float32))
        self.service = self.module.IngestionService(connect=Mock(return_value=self.connection), embed=self.embed,
                                                   open_pdf=Mock(), hash_document=lambda raw: 'hash', audit=Mock(),
                                                   ocr=self.engine)

    def document(self, texts):
        document = Mock(page_count=len(texts))
        document.load_page.side_effect = [Mock(get_text=Mock(return_value=value)) for value in texts]
        self.service.open_pdf.return_value = document
        return document

    def preview(self, texts):
        document = self.document(texts)
        report = self.service.preflight(b'scan', 'persian.pdf', kind='pdf')
        document.close.assert_called_once()
        return report

    def test_mixed_pages_only_ocr_missing_useful_text_and_preserve_original_page_numbers(self):
        report = self.preview([PERSIAN, '', '\ufffd' * 35])
        self.assertTrue(report['can_publish'])
        self.assertEqual(self.engine.extract.call_count, 2)
        self.assertEqual([page['method'] for page in report['extraction']['pages']], ['embedded', 'ocr', 'ocr'])
        self.assertEqual([sample['page'] for sample in report['samples']], [1, 2, 3])
        self.assertEqual(report['cost']['ocr_pages'], 2)
        self.embed.assert_not_called()
        self.connection.commit.assert_not_called()

    def test_confident_scan_publishes_vectors_with_page_provenance_and_durable_confidence(self):
        document = self.document(['', ''])
        result = self.service.ingest_pdf(b'scan', 'persian.pdf')
        pages = result['extraction']['pages']
        self.assertEqual([(page['page'], page['method'], page['confidence']) for page in pages], [(1, 'ocr', 98), (2, 'ocr', 98)])
        self.assertTrue(all('text_sample' not in page for page in pages))
        chunks = [call.args[1] for call in self.cursor.execute.call_args_list if 'INSERT INTO pdf_chunks' in call.args[0]]
        self.assertEqual([chunk[2:5] for chunk in chunks], [(1, 1, PERSIAN), (2, 2, PERSIAN)])
        self.connection.commit.assert_called_once()
        document.close.assert_called_once()

    def test_uncertain_mean_or_word_confidence_blocks_the_entire_import_before_embedding_or_database(self):
        for mean, minimum in ((74, 65), (99, 49), (None, None)):
            self.engine.extract.return_value = self.ocr_module.OcrText(PERSIAN, mean, minimum)
            report = self.preview([PERSIAN, ''])
            self.assertFalse(report['can_publish'])
            self.assertFalse(report['valid'])
            self.assertEqual(report['issues'][0]['page'], 2)
            self.assertTrue(report['extraction']['pages'][1]['needs_review'])
            self.assertEqual(report['extraction']['pages'][1]['text_sample'], PERSIAN)
            self.document([PERSIAN, ''])
            with self.assertRaises(BadRequest):
                self.service.ingest_pdf(b'scan', 'persian.pdf', replace=True)
        self.embed.assert_not_called()
        self.service.connect.assert_not_called()

    def test_ocr_disabled_preserves_embedded_behavior_and_does_not_start_engine(self):
        self.engine.enabled = False
        report = self.preview([PERSIAN])
        self.assertTrue(report['can_publish'])
        self.assertNotIn('extraction', report)
        report = self.preview([''])
        self.assertFalse(report['can_publish'])
        self.engine.extract.assert_not_called()

    def test_engine_failures_and_no_text_have_page_review_locations_without_publication(self):
        for code in ('ocr_timeout', 'ocr_unavailable', 'ocr_image_limit'):
            self.engine.extract.side_effect = self.ocr_module.OcrError(code)
            report = self.preview([''])
            self.assertEqual(report['extraction']['pages'][0]['reason'], code)
            self.assertEqual(report['issues'][0]['page'], 1)
            self.assertFalse(report['can_publish'])
        self.engine.extract.side_effect = None
        self.engine.extract.return_value = self.ocr_module.OcrText('', None, None)
        report = self.preview([''])
        self.assertFalse(report['can_publish'])
        self.embed.assert_not_called()
        self.service.connect.assert_not_called()

    def test_per_file_ocr_page_limit_is_actionable_and_never_publishes_partial_pages(self):
        report = self.preview([''] * 21)
        self.assertEqual(self.engine.extract.call_count, 20)
        self.assertEqual(report['issues'][0]['page'], 21)
        self.assertEqual(report['extraction']['pages'][-1]['reason'], 'ocr_page_limit')
        self.assertFalse(report['can_publish'])
        self.embed.assert_not_called()
        self.service.connect.assert_not_called()
