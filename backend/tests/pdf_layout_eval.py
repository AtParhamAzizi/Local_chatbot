"""Compare F031 with the frozen pre-F031 extraction on real generated PDFs."""

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import unicodedata
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch


def normalized(text):
    # Matching only: accommodate shaped Persian glyphs without changing storage.
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', text)).strip()


def baseline_text(page):
    # Frozen pre-F031 extraction/cleanup, without chunk overlap duplication.
    text = page.get_text('text', sort=True).replace('\u00ad', '')
    text = re.sub(r'(?<=\w)-\n(?=\w)', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def score(texts, expected):
    result = dict(content_errors=0, structure_errors=0, reading_order_errors=0,
                  content_checks=0, structure_checks=0, reading_order_checks=0, reading_order_evaluated=0)
    for text, page in zip(texts, expected):
        value = normalized(text)
        positions = []
        for anchor in page.anchors:
            needle = normalized(anchor)
            result['content_checks'] += 1
            # Missing or duplicate body anchors are extraction errors.
            count = value.count(needle)
            result['content_errors'] += int(count != 1)
            positions.append(value.find(needle) if count == 1 else -1)
        for absent in page.absent:
            result['content_checks'] += 1
            result['content_errors'] += int(normalized(absent) in value)
        for structure in page.structures:
            result['structure_checks'] += 1
            result['structure_errors'] += int(normalized(structure) not in value)
        # Every ordered pair, not only adjacent items; missing anchors are
        # charged to content instead of improving this metric silently.
        for index, first in enumerate(positions):
            for second in positions[index + 1:]:
                result['reading_order_checks'] += 1
                result['reading_order_evaluated'] += int(first >= 0 and second >= 0)
                result['reading_order_errors'] += int(first >= 0 and second >= 0 and first > second)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Optional JSON result file, including per-case diagnostics.')
    options = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import numpy as np
    import pymupdf

    from ocr_fixture import PdfDatabase
    from pdf_layout_fixture import corpus
    from services.ingestion import IngestionService

    def forbidden(*args, **kwargs):
        raise AssertionError('Layout evaluation cannot use deployment services or networks')

    environment = {name: value for name, value in os.environ.items()
                   if name.upper() in {'PATH', 'SYSTEMROOT', 'SYSTEMDRIVE', 'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA'}}
    environment.update(APP_ENV='test', PDF_OCR_ENABLED='0')
    results = []
    with TemporaryDirectory(prefix='pdf-layout-eval-') as directory, \
            patch.dict(os.environ, environment, clear=True), \
            patch.object(socket, 'create_connection', forbidden), patch.object(socket, 'getaddrinfo', forbidden), \
            patch.object(socket.socket, 'connect', forbidden):
        database = PdfDatabase(Path(directory) / 'knowledge.sqlite')
        embed = Mock(side_effect=lambda texts: np.ones((len(texts), 2), dtype=np.float32))
        service = IngestionService(connect=database.connect, embed=embed, open_pdf=pymupdf.open,
                                   hash_document=lambda raw: hashlib.sha256(raw).hexdigest(),
                                   audit=lambda *args, **kwargs: None, ocr=SimpleNamespace(enabled=False))
        for case in corpus():
            with pymupdf.open(stream=case.raw, filetype='pdf') as document:
                baseline = [baseline_text(page) for page in document]
            count, chunks, empty = service._prepare_pdf(case.raw)
            assert count == len(case.pages) and not empty, case.name
            # Fixtures fit one chunk/page: inspect actual model/persistence text.
            assert len(chunks) == count, (case.name, 'Fixture exceeded one chunk per page')
            actual = [chunk['content'] for chunk in chunks]
            preview = service.preflight(case.raw, case.name + '.pdf', kind='pdf')
            assert preview['can_publish'], preview
            embed.assert_not_called()
            expected_indexes = sorted({0, len(chunks) // 2, len(chunks) - 1})
            assert [sample['text'] for sample in preview['samples']] == [actual[index][:240] for index in expected_indexes]
            result = service.ingest_pdf(case.raw, case.name + '.pdf')
            assert result['chunk_count'] == len(chunks)
            embedded = [value for call in embed.call_args_list for value in call.args[0]]
            assert embedded == actual, 'Embedded text differs from preparation'
            embed.reset_mock()
            connection = database.connect()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT page_start, page_end, content FROM pdf_chunks WHERE document_id=%s ORDER BY chunk_index',
                                   (result['document_id'],))
                    stored = cursor.fetchall()
            finally:
                connection.close()
            assert [row['content'] for row in stored] == actual
            assert [(row['page_start'], row['page_end']) for row in stored] == [(n, n) for n in range(1, count + 1)]
            before, after = score(baseline, case.pages), score(actual, case.pages)
            results.append(dict(case=case.name, pages=count, baseline=before, layout=after,
                                baseline_text=baseline, layout_text=actual))
    totals = {kind: {key: sum(case[kind][key] for case in results) for key in results[0][kind]}
              for kind in ('baseline', 'layout')}
    report = dict(corpus_version=1, pymupdf=pymupdf.VersionBind, documents=len(results),
                  pages=sum(case['pages'] for case in results), totals=totals, cases=results)
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'cases'}, indent=2))
    for case in results:
        print(case['case'], 'baseline:', case['baseline'], 'layout:', case['layout'])
    assert totals['layout']['content_errors'] == 0, 'Content loss or duplicate extraction'
    assert totals['layout']['structure_errors'] < totals['baseline']['structure_errors'], 'Structure did not improve'
    assert totals['layout']['reading_order_errors'] < totals['baseline']['reading_order_errors'], 'Reading order did not improve'
    assert totals['layout']['reading_order_errors'] == 0, 'Reading order regressed on a corpus case'
    assert all(case['layout']['content_errors'] + case['layout']['structure_errors'] <=
               case['baseline']['content_errors'] + case['baseline']['structure_errors'] for case in results)
    print('PASS: fewer extraction/structure and reading-order errors; preview, embedding and storage agree.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
