"""Geometry, content preservation and ingestion limits without a PDF dependency."""

import importlib
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import sleep
from types import SimpleNamespace
from unittest.mock import Mock, patch

from werkzeug.exceptions import RequestEntityTooLarge

from core_support import OfflineCoreFixture


def line(text, x=40, y=100, width=210, size=11, bold=False):
    return {'bbox': (x, y, x + width, y + size),
            'spans': [{'text': text, 'size': size, 'flags': 16 if bold else 0}]}


def pdf_page(lines, *, tables=()):
    return SimpleNamespace(
        get_text=Mock(return_value={'width': 600, 'height': 800,
                                   'blocks': [{'type': 0, 'lines': lines}]}),
        find_tables=Mock(return_value=SimpleNamespace(tables=tables)),
    )


class PdfLayoutTests(OfflineCoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.layout = importlib.import_module('services.pdf_layout')

    def ordered(self, lines):
        result = self.layout.extract_page(pdf_page(lines))
        self.layout.finish_document([result])
        return result

    def test_columns_read_to_completion_with_spanning_sections_and_titles(self):
        lines = [line('Document title', y=70, width=520, size=20)]
        for y, left, right in ((120, 'First left', 'First right'), (145, 'Last left', 'Last right'),
                               (340, 'Lower left start', 'Lower right start'), (365, 'Lower left end', 'Lower right end')):
            lines.extend((line(right, 330, y), line(left, 40, y)))
        lines.append(line('Second section', y=280, width=520, size=16))
        result = self.ordered(lines)
        self.assertEqual([b.text for b in result.blocks], ['Document title', 'First left', 'Last left',
                         'First right', 'Last right', 'Second section', 'Lower left start', 'Lower left end',
                         'Lower right start', 'Lower right end'])
        self.assertIn('# Document title', result.text)
        self.assertIn('## Second section', result.text)

    def test_persian_columns_read_right_first_without_reversing_unicode_or_numbers(self):
        right = ['راهنمای خدمات ۱۲۳', 'پایان ستون راست']
        left = ['درخواست مشتریان 456', 'پایان ستون چپ']
        result = self.ordered([line(text, x, y) for x, texts in ((40, left), (330, right))
                               for text, y in zip(texts, (100, 120))])
        self.assertEqual([b.text for b in result.blocks], right + left)

    def test_a_single_pdf_span_can_cross_cells_and_columns_without_merging_their_words(self):
        page = pdf_page([])
        chars = []
        for text, start in (('پشتیبانی ۱۲۳', 240), ('ثبت نام 456', 500)):
            chars.extend({'c': char, 'bbox': (start + i * 4, 140, start + i * 4 + 4, 152)}
                         for i, char in enumerate(text))
        page.get_text.return_value['blocks'] = [{'lines': [
            {'bbox': (240, 140, 550, 152), 'spans': [{'size': 11, 'flags': 0, 'chars': chars}]}]}]
        result = self.layout.extract_page(page)
        self.assertEqual([block.text for block in result.blocks], ['پشتیبانی ۱۲۳', 'ثبت نام 456'])
        self.assertEqual(result.source_chars, len('پشتیبانی ۱۲۳ثبت نام 456') + 1)

    def test_three_unequal_columns_and_a_short_single_column_are_not_interleaved(self):
        result = self.ordered([line(f'{x}:{y}', x, y, width) for x, width in ((40, 130), (210, 140), (395, 165))
                               for y in (100, 120, 140)])
        self.assertEqual([b.text for b in result.blocks], [f'{x}:{y}' for x in (40, 210, 395) for y in (100, 120, 140)])
        result = self.ordered([line(f'Line {y}', 40, y, 80) for y in (100, 120, 140, 160)])
        self.assertEqual([b.text for b in result.blocks], [f'Line {y}' for y in (100, 120, 140, 160)])

    def test_recurring_margins_and_changing_page_numbers_do_not_delete_body_or_unique_notes(self):
        pages = []
        for index in range(3):
            lines = [line('Support handbook', y=20, size=9), line(f'Page {index + 1} of 3', y=770, size=9),
                     line('Support handbook', y=100, width=500), line('Useful body text ' * 10, y=130, width=500)]
            if index == 1:
                lines.append(line('Unique legal note', y=750, size=9))
            pages.append(self.layout.extract_page(pdf_page(lines)))
        self.layout.finish_document(pages)
        for page in pages:
            self.assertEqual(page.text.count('Support handbook'), 1)
            self.assertNotIn('Page ', page.text)
        self.assertIn('Unique legal note', pages[1].text)
        single = self.layout.extract_page(pdf_page([line('Unique top title', y=20)]))
        self.layout.finish_document([single])
        self.assertIn('Unique top title', single.text)

    def test_pages_containing_only_repeated_furniture_do_not_resurrect_removed_text(self):
        pages = [self.layout.extract_page(pdf_page([line('Recurring footer', y=770, size=9)])) for _ in range(2)]
        self.layout.finish_document(pages)
        self.assertEqual([page.text for page in pages], ['', ''])

    def test_tables_keep_cells_rows_pipes_and_blanks_without_duplicate_text(self):
        table = SimpleNamespace(bbox=(40, 100, 560, 200), extract=lambda: [
            ['Item', 'Cost'], ['A | B', '10\nUSD'], [None, '20 USD']])
        page = pdf_page([line('Item', y=110), line('Cost', x=330, y=110), line('After table', y=240)], tables=[table])
        result = self.layout.extract_page(page)
        self.layout.extract_tables(page, result)
        self.layout.finish_document([result])
        self.assertIn('| Item | Cost |\n| A \\| B | 10 USD |\n|  | 20 USD |', result.text)
        self.assertEqual(result.text.count('Item'), 1)
        self.assertIn('After table', result.text)
        page.find_tables.assert_called_once_with(strategy='lines_strict')

    def test_table_detection_failure_keeps_text_and_extraction_omits_image_payloads(self):
        page = pdf_page([line('Readable text remains')])
        page.find_tables.side_effect = RuntimeError('Unrecognized vector path')
        result = self.layout.extract_page(page)
        self.layout.extract_tables(page, result)
        self.assertIn('Readable text remains', result.text)
        self.assertEqual(page.get_text.call_args.kwargs['flags'] & 4, 0)

    def test_concurrent_recognition_cannot_mix_shared_table_scratch_state(self):
        start = Barrier(2)
        scratch = {}

        def extract(value):
            page = pdf_page([line('table text')])
            result = self.layout.extract_page(page)

            def find(**options):
                scratch['value'] = value
                sleep(.01)
                return SimpleNamespace(tables=[SimpleNamespace(
                    bbox=(40, 100, 560, 200), extract=lambda: [['Label', 'Value'], ['Page', scratch['value']]])])

            page.find_tables = find
            start.wait(timeout=5)
            self.layout.extract_tables(page, result)
            return result.text

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(extract, ('First', 'Second')))
        self.assertIn('| Page | First |', results[0])
        self.assertIn('| Page | Second |', results[1])

    def test_bold_and_large_headings_remain_separate_from_paragraphs(self):
        result = self.ordered([line('Body words ' * 20, y=150, width=520),
                               line('Bold section', y=100, bold=True), line('Title', y=60, size=20)])
        self.assertEqual([b.kind for b in result.blocks], ['heading', 'heading', 'paragraph'])
        self.assertIn('## Bold section\n\nBody words', result.text)

    def test_document_passes_check_cancellation_between_pages(self):
        pages = [self.layout.extract_page(pdf_page([line('content')])) for _ in range(3)]
        guard = Mock(side_effect=[None, RuntimeError('canceled')])
        with self.assertRaisesRegex(RuntimeError, 'canceled'):
            self.layout.finish_document(pages, guard=guard)
        self.assertEqual(guard.call_count, 2)


class LayoutIngestionTests(OfflineCoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.module = importlib.import_module('services.ingestion')
        self.embed = Mock(side_effect=AssertionError('Preparation cannot embed'))
        self.connect = Mock(side_effect=AssertionError('Preparation cannot connect'))
        self.open_pdf = Mock()
        self.service = self.module.IngestionService(open_pdf=self.open_pdf, embed=self.embed, connect=self.connect,
                                                   ocr=SimpleNamespace(enabled=False), audit=Mock())

    def document(self, pages):
        document = Mock(page_count=len(pages), load_page=Mock(side_effect=pages))
        self.open_pdf.return_value = document
        return document

    def test_source_limit_precedes_table_detection_or_recurring_header_removal(self):
        page = pdf_page([line('x' * 120, y=20)])
        document = self.document([page, page])
        with patch.object(self.module, 'MAX_PDF_TEXT_CHARS', 100), self.assertRaises(RequestEntityTooLarge):
            self.service._prepare_pdf(b'pdf')
        page.find_tables.assert_not_called()
        document.close.assert_called_once()
        self.embed.assert_not_called()
        self.connect.assert_not_called()

    def test_structural_markup_is_also_bounded_before_embedding(self):
        table = SimpleNamespace(bbox=(40, 100, 560, 200), extract=lambda: [['first', 'second'], ['third', 'fourth']])
        page = pdf_page([line('small')], tables=[table])
        document = self.document([page])
        with patch.object(self.module, 'MAX_PDF_TEXT_CHARS', 20), self.assertRaises(RequestEntityTooLarge):
            self.service._prepare_pdf(b'pdf')
        document.close.assert_called_once()

    def test_chunks_keep_structure_order_and_original_pages_including_blank_pages(self):
        first = pdf_page([line('Column title', y=60, width=520, size=20),
                          line('Right first', x=330), line('Left first'),
                          line('Right last', x=330, y=125), line('Left last', y=125)])
        document = self.document([first, pdf_page([]), pdf_page([line('Last page paragraph')])])
        count, chunks, empty = self.service._prepare_pdf(b'pdf')
        self.assertEqual((count, empty), (3, [2]))
        self.assertEqual([chunk['page_start'] for chunk in chunks], [1, 3])
        self.assertTrue(all(chunk['page_start'] == chunk['page_end'] for chunk in chunks))
        text = chunks[0]['content']
        self.assertIn('# Column title', text)
        self.assertLess(text.index('Left last'), text.index('Right first'))
        document.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
