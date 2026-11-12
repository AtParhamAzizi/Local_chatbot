"""Allowlisted, bounded import adapters that emit text and explicit source locations."""

import csv
import hashlib
import io
import json
import re
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from xml.etree import ElementTree

from werkzeug.exceptions import RequestEntityTooLarge

from persian_text import normalize_persian
from request_validation import InvalidRequest, MAX_ANSWER_CHARS, MAX_IMPORT_ROWS, MAX_MESSAGE_CHARS, text


MAX_IMPORT_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 500000
MAX_IMPORT_CHUNKS = 256
MAX_ZIP_ENTRIES = 256
MAX_ZIP_BYTES = 16 * 1024 * 1024
MAX_XML_BYTES = 4 * 1024 * 1024
EXTENSIONS = {'.txt': 'text', '.md': 'markdown', '.csv': 'csv', '.docx': 'docx'}
WORD_NAMESPACES = {'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
                   'http://purl.oclc.org/ooxml/wordprocessingml/main'}
MAIN_CONTENT_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'


@dataclass(frozen=True)
class ImportUnit:
    content: str
    location: dict


@dataclass(frozen=True)
class PreparedImport:
    metadata: dict
    units: tuple = ()
    records: tuple = ()

    @property
    def document_hash(self):
        canonical = json.dumps({'format': self.metadata['format'], 'version': 1,
                                'units': [(unit.content, unit.location) for unit in self.units]},
                               ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        return hashlib.sha256(b'controlled-import-v1\0' + canonical).hexdigest()


def clean_filename(filename):
    filename = text(255, 1)(filename, 'filename').replace('\\', '/').rsplit('/', 1)[-1]
    if filename in {'', '.', '..'} or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in filename):
        raise InvalidRequest('Use a nonempty filename without control characters.')
    return filename


def decode_text(raw):
    try:
        value = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise InvalidRequest('Use UTF-8 encoding, optionally with a UTF-8 BOM.') from None
    if any((ord(character) < 32 and character not in '\n\r\t') or 127 <= ord(character) <= 159 for character in value):
        raise InvalidRequest('Binary data and control characters are not accepted as text.')
    return value.replace('\r\n', '\n').replace('\r', '\n')


def _text_units(value, *, markdown=False, guard=None):
    units, pending, start, headings, fence = [], [], 1, [], None

    def flush():
        if pending:
            location = {'line_start': start, 'line_end': start + len(pending) - 1}
            if markdown:
                location['headings'] = [title for level, title in headings]
            content = '\n'.join(pending)
            if normalize_persian(content):
                units.append(ImportUnit(content, location))
                if len(units) > MAX_IMPORT_CHUNKS:
                    raise RequestEntityTooLarge()
            pending.clear()

    for number, line in enumerate(value.split('\n'), 1):
        if guard is not None and number % 256 == 1:
            guard()
        if markdown:
            marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
            if marker:
                run = marker[1]
                if fence is None:
                    flush()
                    fence = run
                elif run[0] == fence[0] and len(run) >= len(fence) and not marker[2].strip():
                    fence = None
                if not pending:
                    start = number
                pending.append(line)
                if fence is None:
                    flush()
                continue
            if fence is None:
                if re.match(r'^\s*<[/!?A-Za-z]', line) or (number == 1 and line.strip() == '---'):
                    raise InvalidRequest('Markdown imports accept text, headings and fenced code; remove raw HTML/front matter.')
                heading = re.match(r'^ {0,3}(#{1,6})[ \t]+(.+)$', line)
                if heading:
                    flush()
                    if len(heading[2].strip()) > 240:
                        raise InvalidRequest('Markdown heading titles must contain at most 240 characters.')
                    level = len(heading[1])
                    while headings and headings[-1][0] >= level:
                        headings.pop()
                    headings.append((level, heading[2].strip()))
        if not line.strip() and fence is None:
            flush()
            continue
        if not pending:
            start = number
        pending.append(line)
    flush()
    if fence is not None:
        raise InvalidRequest('Close every Markdown fenced code block before importing.')
    return units


def _csv_records(value, guard):
    records = []
    state = 'start'
    for index, character in enumerate(value):
        if guard is not None and index % 4096 == 0:
            guard()
        if state == 'quoted':
            if character == '"':
                state = 'closed'
        elif character == '"':
            if state == 'start':
                state = 'quoted'
            elif state == 'closed':
                state = 'quoted'
            else:
                raise InvalidRequest('Quotes inside unquoted CSV fields must be escaped in a quoted field.')
        elif character in ',\n':
            state = 'start'
        elif state == 'closed':
            raise InvalidRequest('Do not append text after a closing CSV quote.')
        else:
            state = 'unquoted'
    if state == 'quoted':
        raise InvalidRequest('Close every quoted CSV field.')
    try:
        reader = csv.reader(io.StringIO(value, newline=''), strict=True)
        if next(reader, None) != ['question', 'answer']:
            raise InvalidRequest('CSV requires exactly the header question,answer in that order.')
        previous_line = reader.line_num
        for record, fields in enumerate(reader, 1):
            if guard is not None:
                guard()
            if record > MAX_IMPORT_ROWS:
                raise RequestEntityTooLarge()
            if len(fields) != 2:
                raise InvalidRequest(f'CSV record {record} must have exactly two fields.')
            question = text(MAX_MESSAGE_CHARS, 1)(fields[0], 'question')
            answer = text(MAX_ANSWER_CHARS, 1)(fields[1], 'answer')
            if not normalize_persian(question):
                raise InvalidRequest(f'CSV record {record} must contain searchable question text.')
            records.append({'question': question, 'answer': answer,
                            'location': {'record': record, 'line_start': previous_line + 1, 'line_end': reader.line_num}})
            previous_line = reader.line_num
    except csv.Error:
        raise InvalidRequest('Malformed CSV quoting or oversized field.') from None
    if not records:
        raise InvalidRequest('CSV must contain at least one question/answer record.')
    return records


def _xml(raw):
    value = decode_text(raw)
    if re.search(r'<!\s*(DOCTYPE|ENTITY)', value, re.IGNORECASE):
        raise InvalidRequest('DOCX XML declarations and entities are not supported.')
    declaration = re.match(r'\s*<\?xml[^?]*encoding=[\'"]([^\'"]+)', value, re.IGNORECASE)
    if declaration and declaration[1].lower() not in {'utf-8', 'utf8'}:
        raise InvalidRequest('DOCX XML parts must use UTF-8 encoding.')
    parser = ElementTree.XMLPullParser(events=('start', 'end'))
    root, depth, count = None, 0, 0
    try:
        for start in range(0, len(value), 16384):
            parser.feed(value[start:start + 16384])
            for event, node in parser.read_events():
                if event == 'start':
                    if root is None:
                        root = node
                    depth += 1
                    count += 1
                    if depth > 64 or count > 50000:
                        raise RequestEntityTooLarge()
                else:
                    depth -= 1
        parser.close()
    except (ElementTree.ParseError, ValueError):
        raise InvalidRequest('Malformed DOCX XML.') from None
    return root


def _docx_units(raw, guard):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ZIP_ENTRIES or sum(entry.file_size for entry in entries) > MAX_ZIP_BYTES:
                raise RequestEntityTooLarge()
            seen, parts = set(), {}
            for entry in entries:
                if guard is not None:
                    guard()
                name = entry.filename
                path = PurePosixPath(name)
                if (name.casefold() in seen or '\\' in name or path.is_absolute() or '..' in path.parts
                        or ':' in name or entry.flag_bits & 1):
                    raise InvalidRequest('DOCX contains an ambiguous, unsafe or encrypted package entry.')
                seen.add(name.casefold())
                if entry.file_size > max(1024 * 1024, entry.compress_size * 200):
                    raise RequestEntityTooLarge()
                if any(part in name.lower() for part in ('vbaproject', 'activex/', 'embeddings/')):
                    raise InvalidRequest('Macros, active content and embedded files are not supported.')
                if name.endswith(('.xml', '.rels')):
                    if entry.file_size > MAX_XML_BYTES:
                        raise RequestEntityTooLarge()
                    with archive.open(entry) as stream:
                        content = stream.read(MAX_XML_BYTES + 1)
                    if len(content) > MAX_XML_BYTES:
                        raise RequestEntityTooLarge()
                    parts[name] = _xml(content)
                elif not entry.is_dir():
                    with archive.open(entry) as stream:
                        while stream.read(65536):
                            if guard is not None:
                                guard()
            if not {'[Content_Types].xml', '_rels/.rels', 'word/document.xml'} <= parts.keys():
                raise InvalidRequest('Use a DOCX package with a conventional word/document.xml main part.')
            types = parts['[Content_Types].xml']
            if types.tag != '{http://schemas.openxmlformats.org/package/2006/content-types}Types':
                raise InvalidRequest('Invalid DOCX content types root.')
            if not any(node.get('PartName') == '/word/document.xml' and node.get('ContentType') == MAIN_CONTENT_TYPE
                       for node in types):
                raise InvalidRequest('The DOCX main part must be a non-macro Word document.')
            if any(any(marker in node.get('ContentType', '').lower() for marker in ('macro', 'vba', 'activex', 'oleobject'))
                   for node in types):
                raise InvalidRequest('Active DOCX content types are not supported.')
            office_links = [node for node in parts['_rels/.rels'] if node.get('Type', '').endswith('/officeDocument')]
            if (len(office_links) != 1 or office_links[0].get('Target', '').lstrip('/') != 'word/document.xml'
                    or office_links[0].get('TargetMode') == 'External'):
                raise InvalidRequest('The DOCX main document relationship is invalid.')
            for name, root in parts.items():
                if name.endswith('.rels'):
                    if root.tag != '{http://schemas.openxmlformats.org/package/2006/relationships}Relationships':
                        raise InvalidRequest('Invalid DOCX relationships root.')
                    for relation in root:
                        if relation.get('TargetMode') == 'External' and not relation.get('Type', '').endswith('/hyperlink'):
                            raise InvalidRequest('External DOCX content is not supported.')
            root = parts['word/document.xml']
            namespace = root.tag.partition('}')[0].removeprefix('{')
            if namespace not in WORD_NAMESPACES or root.tag != '{' + namespace + '}document':
                raise InvalidRequest('Invalid WordprocessingML document root.')
            prefix = '{' + namespace + '}'
            unsupported = {'drawing', 'object', 'pict', 'altChunk', 'ins', 'del', 'moveFrom', 'moveTo',
                           'fldSimple', 'fldChar', 'instrText', 'vanish', 'webHidden', 'txbxContent',
                           'footnoteReference', 'endnoteReference', 'headerReference', 'footerReference'}
            unsupported_tags = {prefix + tag for tag in unsupported}
            unsupported_tags.add('{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent')
            if any(node.tag in unsupported_tags for name, part in parts.items()
                   if name.startswith('word/') and name.endswith('.xml') for node in part.iter()):
                raise InvalidRequest('Export DOCX drawings, fields, hidden text or tracked changes to a reviewed text/PDF file.')
            body = root.find(prefix + 'body')
            if body is None:
                raise InvalidRequest('DOCX has no document body.')
            units = []
            for number, paragraph in enumerate(body.iter(prefix + 'p'), 1):
                if guard is not None and number % 256 == 1:
                    guard()
                content = ''.join(node.text or '' if node.tag == prefix + 't' else
                                  '\t' if node.tag == prefix + 'tab' else '\n' if node.tag in {prefix + 'br', prefix + 'cr'}
                                  else '' for node in paragraph.iter())
                if normalize_persian(content):
                    units.append(ImportUnit(content, {'part': 'word/document.xml', 'paragraph': number}))
                    if len(units) > MAX_IMPORT_CHUNKS:
                        raise RequestEntityTooLarge()
            return units
    except (zipfile.BadZipFile, NotImplementedError, OSError, EOFError, zlib.error):
        raise InvalidRequest('Malformed or unsupported DOCX ZIP package.') from None


def prepare_import(raw, filename, *, guard=None):
    filename = clean_filename(filename)
    format_name = EXTENSIONS.get(PurePosixPath(filename).suffix.lower())
    if format_name is None:
        raise InvalidRequest('Enabled import extensions are .txt, .md, .csv and .docx.')
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        raise InvalidRequest('The import file must contain bytes.')
    if len(raw) > MAX_IMPORT_BYTES:
        raise RequestEntityTooLarge()
    metadata = {'version': 1, 'format': format_name, 'filename': filename,
                'sha256': hashlib.sha256(raw).hexdigest(), 'importer': format_name + '-v1',
                'encoding': 'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8'}
    if format_name == 'docx':
        metadata['encoding'] = 'ooxml-utf-8'
        units = _docx_units(raw, guard)
    else:
        value = decode_text(raw)
        if len(value) > MAX_TEXT_CHARS:
            raise RequestEntityTooLarge()
        if format_name == 'csv':
            return PreparedImport(metadata, records=tuple(_csv_records(value, guard)))
        units = _text_units(value, markdown=format_name == 'markdown', guard=guard)
    if sum(len(unit.content) for unit in units) > MAX_TEXT_CHARS:
        raise RequestEntityTooLarge()
    if not units:
        raise InvalidRequest('The import contains no searchable text.')
    return PreparedImport(metadata, units=tuple(units))


def import_chunks(prepared, *, guard=None):
    chunks = []
    for unit in prepared.units:
        start = 0
        while start < len(unit.content):
            if guard is not None:
                guard()
            end = min(start + 900, len(unit.content))
            location = {**unit.location, 'character_start': start, 'character_end': end}
            if 'line_start' in location:
                location['line_start'] = unit.location['line_start'] + unit.content[:start].count('\n')
                location['line_end'] = unit.location['line_start'] + unit.content[:end - 1].count('\n')
            content = unit.content[start:end]
            if normalize_persian(content):
                chunks.append({'chunk_index': len(chunks), 'page_start': None, 'page_end': None,
                               'content': content, 'source_metadata': {**prepared.metadata, 'location': location}})
            if len(chunks) > MAX_IMPORT_CHUNKS:
                raise RequestEntityTooLarge()
            if end == len(unit.content):
                break
            start = end - 160
    return chunks
