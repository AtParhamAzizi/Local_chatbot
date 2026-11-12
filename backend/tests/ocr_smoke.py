"""Run actual local Persian OCR, publication and retrieval on a synthetic scan."""

import argparse
import hashlib
import importlib
import json
import os
import socket
import subprocess
import sys
import types
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tesseract', default='tesseract')
    parser.add_argument('--tessdata')
    parser.add_argument('--font', help='Optional local font file for the synthetic Persian scan.')
    parser.add_argument('--wsl-distribution', help='Windows acceptance only: run Tesseract from this installed WSL distribution.')
    options = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import numpy as np
    from embedding_fixtures import profile, with_profile
    import pymupdf
    from werkzeug.exceptions import BadRequest

    from ocr_fixture import PdfDatabase, scanned_pdf
    from services.ingestion import IngestionService
    from services.ocr import LocalOcr

    def forbidden(*args, **kwargs):
        raise AssertionError('This fixture must not use network or deployment services')

    environment = {name: value for name, value in os.environ.items()
                   if name.upper() in {'PATH', 'SYSTEMROOT', 'SYSTEMDRIVE', 'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA'}}
    environment.update({'APP_ENV': 'test', 'PDF_OCR_ENABLED': '1', 'PDF_OCR_EXECUTABLE': options.tesseract,
                        'PDF_OCR_MIN_CONFIDENCE': '75', 'PDF_OCR_MIN_TEXT_CHARS': '32'})
    if options.tessdata:
        environment['PDF_OCR_TESSDATA'] = options.tessdata
    with TemporaryDirectory(prefix='ocr-acceptance-') as directory, \
            patch.dict(os.environ, environment, clear=True), \
            patch.object(socket, 'create_connection', forbidden), \
            patch.object(socket, 'getaddrinfo', forbidden), patch.object(socket.socket, 'connect', forbidden):
        database = PdfDatabase(Path(directory) / 'knowledge.sqlite')
        def process_factory(command, **settings):
            if options.wsl_distribution:
                if os.name != 'nt':
                    raise RuntimeError('--wsl-distribution is only supported on Windows')
                converted = list(command)
                for index in (1, 2):
                    target = Path(converted[index])
                    converted[index] = '/mnt/' + target.drive[0].lower() + '/' + '/'.join(target.parts[1:])
                command = [str(Path(os.environ['SYSTEMROOT']) / 'System32/wsl.exe'), '-d', options.wsl_distribution,
                           '--', 'env', 'OMP_THREAD_LIMIT=1', *converted]
            return subprocess.Popen(command, **settings)

        engine = LocalOcr(process_factory=process_factory)
        embed = Mock(side_effect=lambda texts: np.tile(np.array([1, 0], dtype=np.float32), (len(texts), 1)))
        service = IngestionService(connect=database.connect, embed=with_profile(embed), open_pdf=pymupdf.open,
                                   hash_document=lambda raw: hashlib.sha256(raw).hexdigest(), audit=lambda *args, **kwargs: None,
                                   ocr=engine)
        raw = scanned_pdf(font_path=options.font)
        with pymupdf.open(stream=raw, filetype='pdf') as document:
            assert all(not page.get_text().strip() for page in document), 'Fixture must be image-only'
        preview = service.preflight(raw, 'persian-scan.pdf', kind='pdf')
        assert preview['can_publish'], json.dumps(preview['extraction'], ensure_ascii=True)
        for page, words in zip(preview['extraction']['pages'], (('مشتریان', 'پشتیبانی', 'هفته'), ('درخواست', 'تماس', 'کارشناسان'))):
            assert all(word in page['text_sample'] for word in words), json.dumps(page, ensure_ascii=True)
        embed.assert_not_called()
        result = service.ingest_pdf(raw, 'persian-scan.pdf')
        fake_database = types.ModuleType('database')
        fake_database.get_conn = database.connect
        fake_database.logger = types.SimpleNamespace(operation=lambda function: function)
        with patch.dict(sys.modules, {'database': fake_database}):
            scoring = importlib.import_module('searchForAnswer')
            hits = scoring._search_pdf(np.array([1, 0], dtype=np.float32), query='مشتریان', profile=profile(2))
        assert hits and hits[0]['page_start'] == 1 and 'مشتریان' in hits[0]['content'], 'OCR knowledge must be searchable with page provenance'
        before = [(hit['content'], hit['page_start']) for hit in hits]
        embed.reset_mock()
        uncertain = scanned_pdf(uncertain=True, font_path=options.font)
        review = service.preflight(uncertain, 'uncertain-scan.pdf', kind='pdf')
        assert not review['can_publish'] and review['extraction']['pages'][1]['needs_review']
        try:
            service.ingest_pdf(uncertain, 'uncertain-scan.pdf')
        except BadRequest:
            pass
        else:
            raise AssertionError('Uncertain OCR must block publication')
        embed.assert_not_called()
        assert before == [(hit['content'], hit['page_start']) for hit in scoring._search_pdf(np.array([1, 0], dtype=np.float32), query='مشتریان', profile=profile(2))]
        print(json.dumps({'case': 'synthetic Persian image-only PDF', 'pages': result['page_count'],
                          'chunks': result['chunk_count'], 'search_hit_page': hits[0]['page_start'],
                          'review_pages': [page['page'] for page in review['extraction']['pages'] if page['needs_review']],
                          'confidence': [page['confidence'] for page in result['extraction']['pages']],
                          'embeddings': 'deterministic test adapter; no real text model'}, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
