"""Synthetic public Persian scans and an isolated SQLite PDF publication adapter."""

import sqlite3
from contextlib import closing
from pathlib import Path


PERSIAN_PAGES = (
    'راهنمای خدمات مشتریان\nبرای دریافت پاسخ با مرکز پشتیبانی تماس بگیرید\nخدمات پشتیبانی در تمام روزهای هفته ارائه می شود',
    'راهنمای ثبت درخواست\nلطفا نام و شماره تماس خود را وارد کنید\nکارشناسان پشتیبانی درخواست شما را بررسی می کنند',
)


def scanned_pdf(*, uncertain=False, font_path=None):
    import pymupdf

    candidates = (font_path,) if font_path else ('C:/Windows/Fonts/arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    font = next((Path(candidate) for candidate in candidates if Path(candidate).is_file()), None)
    if font is None:
        raise RuntimeError('Install Arial or the DejaVu Sans font for the synthetic Persian scan.')
    with pymupdf.open() as source, pymupdf.open() as scanned:
        for index, paragraph in enumerate(PERSIAN_PAGES):
            page = source.new_page(width=595, height=842)
            if not uncertain or index != 1:
                css = (f'@font-face {{font-family: sample; src: url({font.name});}} '
                       'body {font-family: sample; font-size: 14px; line-height: 1.8; color: black;}')
                page.insert_htmlbox(pymupdf.Rect(40, 60, 555, 780),
                                    '<div dir="rtl">' + '<br>'.join(paragraph.splitlines()) + '</div>',
                                    css=css, archive=pymupdf.Archive(str(font.parent)))
            image = page.get_pixmap(dpi=200, alpha=False).tobytes('png')
            target = scanned.new_page(width=595, height=842)
            target.insert_image(target.rect, stream=image)
        return scanned.tobytes()


class PdfDatabase:
    def __init__(self, filename):
        self.filename = str(filename)
        with closing(sqlite3.connect(self.filename)) as connection:
            connection.executescript('''
                CREATE TABLE pdf_documents (
                    id INTEGER PRIMARY KEY, filename TEXT, document_hash TEXT UNIQUE, file_size INTEGER,
                    page_count INTEGER, chunk_count INTEGER, embedded_chunk_count INTEGER,
                    embedding_dim INTEGER, status TEXT, embedding_profile TEXT
                );
                CREATE TABLE pdf_chunks (
                    id INTEGER PRIMARY KEY, document_id INTEGER, chunk_index INTEGER,
                    page_start INTEGER, page_end INTEGER, content TEXT, embedding BLOB, embedding_dim INTEGER,
                    embedding_profile TEXT,
                    FOREIGN KEY(document_id) REFERENCES pdf_documents(id) ON DELETE CASCADE,
                    UNIQUE(document_id, chunk_index)
                );
            ''')

    def connect(self):
        return PdfConnection(self.filename)


class PdfConnection:
    def __init__(self, filename):
        self.raw = sqlite3.connect(filename, isolation_level=None)
        self.raw.row_factory = sqlite3.Row
        self.raw.execute('PRAGMA foreign_keys=ON')

    def cursor(self):
        return PdfCursor(self.raw.cursor())

    def begin(self):
        self.raw.execute('BEGIN')

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


class PdfCursor:
    def __init__(self, cursor):
        self.raw = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.raw.close()

    @property
    def lastrowid(self):
        return self.raw.lastrowid

    def execute(self, statement, parameters=()):
        self.raw.execute(statement.replace('%s', '?'), parameters)

    def fetchone(self):
        row = self.raw.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self):
        return [dict(row) for row in self.raw.fetchall()]
