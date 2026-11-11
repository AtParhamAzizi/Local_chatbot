"""Local PDF geometry -> ordered, structured text; no model or PDF imports.

Keep spans in their extractor-provided Unicode order. Bidi direction controls
column traversal only; reversing characters would corrupt Persian and numbers.
"""

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from threading import Lock


# Supported older PyMuPDF releases share table-recognizer scratch state.
# Copy the cells under the same lock as recognition, across preview/workers.
_TABLE_LOCK = Lock()


@dataclass
class Block:
    bbox: tuple
    text: str
    kind: str = 'paragraph'
    size: float = 11
    bold: bool = False
    group: int = -1
    glyphs: list = field(default_factory=list)


@dataclass
class LayoutPage:
    width: float
    height: float
    blocks: list = field(default_factory=list)
    source_chars: int = 0
    plain_text: str = ''

    @property
    def text(self):
        return render(self.blocks) if self.width else self.plain_text


def _body_size(blocks):
    sizes = Counter()
    for block in blocks:
        if block.kind != 'table':
            sizes[round(block.size)] += len(block.text)
    return sizes.most_common(1)[0][0] if sizes else 11


def _rtl(blocks):
    directions = Counter(unicodedata.bidirectional(char) for block in blocks for char in block.text)
    return directions['R'] + directions['AL'] > directions['L']


def _inside(inner, outer):
    # Use the line centre so table border rounding cannot duplicate cell text.
    x, y = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] <= x <= outer[2] and outer[1] <= y <= outer[3]


def extract_page(page):
    # RAWDICT without PRESERVE_IMAGES (4): retain character geometry, no rasters.
    data = page.get_text('rawdict', flags=64 | 2 | 1, sort=False)
    if isinstance(data, str):
        # Text-only injected adapters remain usable by non-PyMuPDF consumers.
        return LayoutPage(0, 0, source_chars=len(data), plain_text=data)
    blocks = []
    source_chars = 0
    for group, raw in enumerate(data['blocks']):
        if raw.get('type', 0) != 0:
            continue
        for line in raw['lines']:
            fragments, current = [], []
            for span in line['spans']:
                # Also accept dict-style spans from explicit lightweight adapters.
                chars = span.get('chars', [{'c': span.get('text', ''), 'bbox': span.get('bbox', line['bbox'])}])
                for char in chars:
                    bbox = tuple(char['bbox'])
                    if current:
                        previous = current[-1][1]
                        gap = max(bbox[0] - previous[2], previous[0] - bbox[2])
                        if gap > max(12, span['size'] * 1.5):
                            fragments.append(current)
                            current = []
                    current.append((char['c'], bbox, span['size'], span.get('flags', 0)))
                    source_chars += len(char['c'])
            source_chars += 1
            if current:
                fragments.append(current)
            for fragment in fragments:
                value = ''.join(char[0] for char in fragment).strip()
                if not value:
                    continue
                bbox = (min(c[1][0] for c in fragment), min(c[1][1] for c in fragment),
                        max(c[1][2] for c in fragment), max(c[1][3] for c in fragment))
                blocks.append(Block(bbox, value, size=max(c[2] for c in fragment),
                                    bold=any(c[3] & 16 for c in fragment), group=group,
                                    glyphs=[(c[0], c[1]) for c in fragment]))
    result = LayoutPage(data['width'], data['height'], blocks, source_chars)
    # OCR eligibility and source quotas use original text, before any suppression.
    result.plain_text = '\n'.join(block.text for block in blocks)
    return result


def extract_tables(page, layout):
    """Copy ruled tables while the PDF page is live; retain prose on failure.

    Text-based table guessing mistakes aligned newspaper columns for cells, so
    use ruled borders only. Borderless layouts still retain their text/geometry.
    """
    finder = getattr(page, 'find_tables', None)
    if not callable(finder) or not layout.blocks:
        return
    try:
        replacements = []
        with _TABLE_LOCK:
            tables = finder(strategy='lines_strict').tables
            for table in tables:
                rows = table.extract()
                if len(rows) < 2 or max((len(row) for row in rows), default=0) < 2:
                    continue
                source = [block for block in layout.blocks if _inside(block.bbox, table.bbox)]
                # Table.extract() can reorder Persian glyphs by x position.
                # Prefer the original logical spans within the detected cells.
                cell_rows = getattr(table, 'rows', ())
                if len(cell_rows) == len(rows):
                    for row_index, row in enumerate(cell_rows):
                        for column, cell in enumerate(row.cells):
                            if cell is None:
                                continue
                            lines = [''.join(text for text, bbox in block.glyphs if _inside(bbox, cell)).strip()
                                     for block in sorted(source, key=lambda block: block.bbox[1])]
                            if any(lines):
                                rows[row_index][column] = '\n'.join(value for value in lines if value)
                if _rtl(source):
                    rows = [list(reversed(row)) for row in rows]
                # Keep row/cell boundaries and literal pipes, including empty cells.
                text = '\n'.join('| ' + ' | '.join(
                    re.sub(r'\s+', ' ', cell or '').strip().replace('|', '\\|') for cell in row
                ) + ' |' for row in rows)
                replacements.append(Block(tuple(table.bbox), text, kind='table'))
    except Exception:
        # Optional table recognition must never discard otherwise readable text.
        return
    for table in replacements:
        layout.blocks = [block for block in layout.blocks if not _inside(block.bbox, table.bbox)]
        layout.blocks.append(table)


def _column_cut(blocks, width):
    """Find a supported gutter, ignoring wide titles/sections spanning it."""
    narrow = sorted((block for block in blocks if block.kind != 'heading'
                     and block.bbox[2] - block.bbox[0] < width * .65),
                    key=lambda block: block.bbox[0])
    if len(narrow) < 4:
        return None
    end = narrow[0].bbox[2]
    candidates = []
    for block in narrow[1:]:
        if block.bbox[0] - end >= max(12, width * .025):
            candidates.append((block.bbox[0] - end, (block.bbox[0] + end) / 2))
        end = max(end, block.bbox[2])
    for _, cut in sorted(candidates, reverse=True):
        left = [block for block in blocks if block.bbox[2] <= cut]
        right = [block for block in blocks if block.bbox[0] >= cut]
        if len(left) < 2 or len(right) < 2:
            continue
        overlap = min(max(b.bbox[3] for b in left), max(b.bbox[3] for b in right)) - max(
            min(b.bbox[1] for b in left), min(b.bbox[1] for b in right))
        if overlap > 0:
            return cut
    return None


def reading_order(blocks, width, *, rtl=None, depth=0):
    """Traverse columns within vertical bands separated by spanning material."""
    if rtl is None:
        rtl = _rtl(blocks)
    if len(blocks) < 4 or depth >= 12:
        return sorted(blocks, key=lambda b: (round(b.bbox[1], 1), -b.bbox[0] if rtl else b.bbox[0]))
    cut = _column_cut(blocks, width)
    if cut is None:
        return sorted(blocks, key=lambda b: (round(b.bbox[1], 1), -b.bbox[0] if rtl else b.bbox[0]))
    spans = sorted((b for b in blocks if b.bbox[0] < cut < b.bbox[2]), key=lambda b: b.bbox[1])
    remaining = [b for b in blocks if not (b.bbox[0] < cut < b.bbox[2])]
    ordered = []

    def columns(items):
        left = [b for b in items if b.bbox[2] <= cut]
        right = [b for b in items if b.bbox[0] >= cut]
        for column in ((right, left) if rtl else (left, right)):
            if column:
                column_width = max(b.bbox[2] for b in column) - min(b.bbox[0] for b in column)
                ordered.extend(reading_order(column, column_width, rtl=rtl, depth=depth + 1))

    for span in spans:
        before, after = [], []
        for block in remaining:
            target = before if (block.bbox[1] + block.bbox[3]) / 2 < (span.bbox[1] + span.bbox[3]) / 2 else after
            target.append(block)
        columns(before)
        remaining = after
        ordered.append(span)
    columns(remaining)
    return ordered


def _margin_key(block, page, body_size):
    if not page.height or block.kind == 'table' or len(block.text) > 160 or block.size > body_size * 1.1:
        return None
    side = 'top' if block.bbox[3] <= page.height * .07 else (
        'bottom' if block.bbox[1] >= page.height * .92 else None)
    if side is None:
        return None
    text = re.sub(r'\s+', ' ', block.text).strip().casefold()
    # Normalize changing page counters, not arbitrary numbers in titles/dates.
    if re.fullmatch(r'(?:page\s+|صفحه\s+)?\d+(?:\s*(?:/|of|از)\s*\d+)?', text):
        text = '<page-number>'
    return side, text


def finish_document(pages, *, guard=None):
    """Remove only supported recurring margin lines, then order and classify."""
    occurrences = defaultdict(set)
    for index, page in enumerate(pages):
        if guard is not None:
            guard()
        size = _body_size(page.blocks)
        for block in page.blocks:
            key = _margin_key(block, page, size)
            if key:
                occurrences[key].add(index)
    repeated = {key for key, seen in occurrences.items() if len(seen) >= max(2, math.ceil(len(pages) * .6))}
    for page in pages:
        if guard is not None:
            guard()
        size = _body_size(page.blocks)
        page.blocks = [b for b in page.blocks if _margin_key(b, page, size) not in repeated]
        for block in page.blocks:
            if block.kind != 'table' and len(block.text) <= 160 and (
                    block.size >= size * 1.2 or (block.bold and len(block.text) <= 100)):
                block.kind = 'heading'
        page.blocks = reading_order(page.blocks, page.width)


def render(blocks):
    parts = []
    previous = None
    body_size = _body_size(blocks)
    for block in blocks:
        text = block.text
        if block.kind == 'heading':
            text = ('# ' if block.size >= body_size * 1.5 else '## ') + text
        if (previous is not None and block.kind == previous.kind == 'paragraph'
                and block.group == previous.group and block.group >= 0
                and (abs(block.bbox[0] - previous.bbox[0]) < 12 or abs(block.bbox[2] - previous.bbox[2]) < 12)
                and 0 <= block.bbox[1] - previous.bbox[3] <= max(4, block.size * .7)):
            parts[-1] += '\n' + text
        else:
            parts.append(text)
        previous = block
    return '\n\n'.join(parts)
