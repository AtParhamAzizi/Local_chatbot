"""Optional local Persian OCR with bounded images, subprocesses and confidence."""

import csv
import math
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from settings import setting_value


MAX_IMAGE_PIXELS = 16000000
MAX_TSV_BYTES = 2 * 1024 * 1024
MAX_OCR_PAGES = 20
OCR_BUDGET_SECONDS = 120
MIN_WORD_CONFIDENCE = 50
OCR_SLOT = threading.BoundedSemaphore(1)
OCR_MESSAGES = {
    'ocr_unavailable': 'Install local Tesseract 5 with fas traineddata; check PDF_OCR_EXECUTABLE and PDF_OCR_TESSDATA.',
    'ocr_failed': 'Local OCR failed. Check fas language data and re-export this page as a clear upright scan.',
    'ocr_timeout': 'OCR exceeded its time budget. Split the PDF or simplify this page and preview again.',
    'ocr_image_limit': 'This page exceeds the OCR image limit. Resize the page or reduce PDF_OCR_DPI.',
    'ocr_output_limit': 'OCR output exceeds the page limit. Split or simplify this page.',
    'ocr_page_limit': f'This file requires OCR on more than {MAX_OCR_PAGES} pages. Split the PDF and preview again.',
}


class OcrError(Exception):
    def __init__(self, code):
        super().__init__(OCR_MESSAGES[code])
        self.code = code


@dataclass(frozen=True)
class OcrText:
    text: str
    confidence: float | None
    min_confidence: float | None


def useful_text(value, minimum=32):
    letters = [character for character in value if character.isalpha() and character != '\ufffd']
    return (len(letters) >= minimum and len(set(letters)) >= 3
            and value.count('\ufffd') <= len(value) * 0.02)


def parse_tsv(raw):
    if len(raw) > MAX_TSV_BYTES:
        raise OcrError('ocr_output_limit')
    try:
        rows = csv.DictReader(raw.decode('utf-8-sig').splitlines(), delimiter='\t', quoting=csv.QUOTE_NONE)
        if not {'level', 'page_num', 'block_num', 'par_num', 'line_num', 'conf', 'text'} <= set(rows.fieldnames or ()):
            raise ValueError()
        lines, confidences, weights = [], [], []
        last_line = None
        for row in rows:
            if row['level'] != '5' or not row['text'].strip():
                continue
            confidence = float(row['conf'])
            if not math.isfinite(confidence) or not 0 <= confidence <= 100:
                raise ValueError()
            value = row['text'].strip()
            identity = tuple(row[field] for field in ('page_num', 'block_num', 'par_num', 'line_num'))
            if identity != last_line:
                lines.append([])
                last_line = identity
            lines[-1].append(value)
            confidences.append(confidence)
            weights.append(len(value))
        value = '\n'.join(' '.join(line) for line in lines)
        if len(value) > 500000:
            raise OcrError('ocr_output_limit')
        mean = sum(confidence * weight for confidence, weight in zip(confidences, weights)) / sum(weights) if weights else None
        return OcrText(value, round(mean, 2) if mean is not None else None, min(confidences) if confidences else None)
    except (UnicodeError, ValueError, TypeError, KeyError, AttributeError, csv.Error):
        raise OcrError('ocr_failed') from None


class LocalOcr:
    def __init__(self, *, enabled=None, clock=time.monotonic, process_factory=subprocess.Popen):
        self.enabled = setting_value('PDF_OCR_ENABLED') if enabled is None else enabled
        self.minimum_text = setting_value('PDF_OCR_MIN_TEXT_CHARS')
        self.minimum_confidence = setting_value('PDF_OCR_MIN_CONFIDENCE')
        self.dpi = setting_value('PDF_OCR_DPI')
        self.timeout = setting_value('PDF_OCR_TIMEOUT_SECONDS')
        self.executable = setting_value('PDF_OCR_EXECUTABLE')
        self.tessdata = setting_value('PDF_OCR_TESSDATA')
        self.clock = clock
        self.process_factory = process_factory

    def _render(self, page, filename):
        import fitz

        width, height = page.rect.width, page.rect.height
        if not all(math.isfinite(value) and value > 0 for value in (width, height)):
            raise OcrError('ocr_image_limit')
        pixels = (math.ceil(width * self.dpi / 72) + 1) * (math.ceil(height * self.dpi / 72) + 1)
        if pixels > MAX_IMAGE_PIXELS:
            raise OcrError('ocr_image_limit')
        page.get_pixmap(dpi=self.dpi, colorspace=fitz.csGRAY, alpha=False).save(str(filename))

    def extract(self, page, *, guard=None, deadline=None):
        deadline = min(deadline if deadline is not None else math.inf, self.clock() + self.timeout)

        def check():
            if guard is not None:
                guard()
            if self.clock() >= deadline:
                raise OcrError('ocr_timeout')

        acquired = False
        try:
            while not acquired:
                check()
                acquired = OCR_SLOT.acquire(timeout=0.25)
            with TemporaryDirectory(prefix='chatbot-ocr-') as directory:
                image = Path(directory) / 'page.png'
                output = Path(directory) / 'text'
                check()
                try:
                    self._render(page, image)
                except (OSError, ValueError, RuntimeError):
                    raise OcrError('ocr_failed') from None
                check()
                command = [self.executable, str(image), str(output), '-l', 'fas', '--psm', '11', '--dpi', str(self.dpi)]
                if self.tessdata:
                    command.extend(['--tessdata-dir', self.tessdata])
                command.append('tsv')
                environment = {**os.environ, 'OMP_THREAD_LIMIT': '1'}
                process = None
                try:
                    process = self.process_factory(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                   stderr=subprocess.DEVNULL, env=environment,
                                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    while True:
                        check()
                        try:
                            status = process.wait(timeout=min(0.25, max(0.001, deadline - self.clock())))
                            break
                        except subprocess.TimeoutExpired:
                            continue
                    check()
                    if status != 0:
                        raise OcrError('ocr_failed')
                except OSError:
                    raise OcrError('ocr_unavailable') from None
                finally:
                    if process is not None and process.poll() is None:
                        process.kill()
                        process.wait()
                extracted = output.with_suffix('.tsv')
                try:
                    with extracted.open('rb') as stream:
                        raw = stream.read(MAX_TSV_BYTES + 1)
                except OSError:
                    raise OcrError('ocr_failed') from None
                return parse_tsv(raw)
        finally:
            if acquired:
                OCR_SLOT.release()
