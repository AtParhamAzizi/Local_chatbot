"""Versioned, generated public layout corpus with independently specified truth.

No binaries, private documents, downloads or model weights. Coordinates describe
the intended reading order independently of the PDF's content-stream ordering.
"""

from dataclasses import dataclass, field


@dataclass
class ExpectedPage:
    anchors: list
    structures: list = field(default_factory=list)
    absent: list = field(default_factory=list)


@dataclass
class Case:
    name: str
    raw: bytes
    pages: list


def corpus():
    import pymupdf

    cases = []

    def new_page(document):
        return document.new_page(width=600, height=800)

    def text(page, x, y, value, size=11, bold=False):
        page.insert_text((x, y), value, fontsize=size, fontname='hebo' if bold else 'helv')

    def columns(page, groups, *, top=130, positions=None):
        positions = positions or [40, 330]
        # Deliberately interleave content-stream order across columns.
        for row in range(len(groups[0])):
            for x, group in reversed(list(zip(positions, groups))):
                text(page, x, top + row * 22, group[row])

    def table(page, x, y, width, rows):
        height, count = 30, len(rows[0])
        for index in range(len(rows) + 1):
            page.draw_line((x, y + index * height), (x + width, y + index * height))
        for index in range(count + 1):
            page.draw_line((x + index * width / count, y), (x + index * width / count, y + len(rows) * height))
        for row_index, row in enumerate(rows):
            for cell_index, cell in enumerate(row):
                if cell:
                    text(page, x + cell_index * width / count + 6, y + row_index * height + 20, cell)

    with pymupdf.open() as document:
        page = new_page(document)
        anchors = ['Welcome to the regional service centre.', 'Please bring your account reference.',
                   'The team will confirm your appointment.', 'Keep this receipt for your records.']
        for index, value in enumerate(anchors):
            text(page, 40, 100 + index * 24, value)
        cases.append(Case('single-column-control', document.tobytes(), [ExpectedPage(anchors)]))

    for name, positions in [('two-columns', [40, 330]), ('three-columns', [40, 225, 410])]:
        with pymupdf.open() as document:
            page = new_page(document)
            groups = [[f'{label} opening instructions.', f'{label} supporting details.', f'{label} closing request.']
                      for label in ('West', 'Central', 'East')[:len(positions)]]
            columns(page, groups, positions=positions)
            cases.append(Case(name, document.tobytes(), [ExpectedPage(sum(groups, []))]))

    with pymupdf.open() as document:
        page = new_page(document)
        title = 'Service manual and regional support procedures'
        section = 'Part two: follow-up requests and required documents'
        text(page, 40, 90, title, 20)
        upper = [['First office receives the request.', 'First office checks the account.', 'First office issues a receipt.'],
                 ['Second office reviews the receipt.', 'Second office confirms the date.', 'Second office closes the case.']]
        lower = [['Later requests need a reference.', 'Later requests include the date.', 'Later requests finish here.'],
                 ['Final review starts with records.', 'Final review checks the response.', 'Final review completes the file.']]
        columns(page, upper)
        text(page, 40, 290, section, 16)
        columns(page, lower, top=340)
        cases.append(Case('spanning-headings-and-bands', document.tobytes(), [ExpectedPage(
            [title] + sum(upper, []) + [section] + sum(lower, []), ['# ' + title, '## ' + section])]))

    with pymupdf.open() as document:
        page = new_page(document)
        right = ['راهنمای خدمات مشتریان', 'درخواست پشتیبانی مشتریان', 'پایان ستون راست ۱۲۳']
        left = ['راهنمای ثبت درخواست', 'بررسی مدارک مشتریان', 'پایان ستون چپ 456']
        for x, group in ((40, left), (330, right)):
            page.insert_htmlbox((x, 100, x + 230, 240), '<div dir="rtl">' + '<br>'.join(group) + '</div>',
                                css='body {font-size: 12px; line-height: 1.8;}')
        cases.append(Case('persian-rtl-columns', document.tobytes(), [ExpectedPage(right + left)]))

    with pymupdf.open() as document:
        page = new_page(document)
        rows = [['Service', 'Region', 'Fee'], ['Delivery', 'North', '120'], ['Collection', 'South', '80']]
        text(page, 40, 100, 'Current service charges are listed below.')
        table(page, 40, 140, 510, rows)
        text(page, 40, 280, 'Contact the local office for exemptions.')
        cases.append(Case('ruled-table', document.tobytes(), [ExpectedPage(
            ['Current service charges'] + sum(rows, []) + ['Contact the local office'],
            ['| ' + ' | '.join(row) + ' |' for row in rows])]))

    with pymupdf.open() as document:
        page = new_page(document)
        rows = [['راهنمای درخواست', 'خدمات مشتریان'], ['ثبت نام 456', 'پشتیبانی ۱۲۳']]
        table(page, 40, 100, 520, [['', ''], ['', '']])
        for row_index, row in enumerate(rows):
            for column, value in enumerate(reversed(row)):
                x, y = 40 + column * 260, 100 + row_index * 30
                page.insert_htmlbox((x + 2, y + 1, x + 258, y + 29), '<div dir="rtl">' + value + '</div>',
                                    css='body {font-size: 11px;}')
        cases.append(Case('persian-rtl-table', document.tobytes(), [ExpectedPage(sum(rows, []),
            ['| ' + ' | '.join(row) + ' |' for row in rows])]))

    with pymupdf.open() as document:
        page = new_page(document)
        rows = [['Type', 'Days'], ['Basic', '3'], ['Express', '1']]
        text(page, 40, 110, 'Available delivery plans.')
        table(page, 40, 140, 220, rows)
        text(page, 40, 270, 'Choose the plan that suits you.')
        right = ['Applications begin at reception.', 'Provide your delivery address.', 'Confirm the contact number.',
                 'An adviser verifies the request.', 'An adviser sends confirmation.']
        for index, value in enumerate(right):
            text(page, 330, 110 + index * 35, value)
        cases.append(Case('table-beside-prose', document.tobytes(), [ExpectedPage(
            ['Available delivery plans.'] + sum(rows, []) + ['Choose the plan'] + right,
            ['| ' + ' | '.join(row) + ' |' for row in rows])]))

    with pymupdf.open() as document:
        pages = []
        for index in range(3):
            page = new_page(document)
            text(page, 40, 25, 'Regional support handbook', 9)
            text(page, 280, 780, f'Page {index + 1} of 3', 9)
            heading = f'Office procedure {index + 1}'
            body = f'Procedure {index + 1} requires a current account reference and the completed request form.'
            text(page, 40, 100, heading, 16)
            text(page, 40, 140, body)
            # Same text in the body is substantive and must survive suppression.
            text(page, 40, 190, 'Regional support handbook')
            anchors = [heading, body, 'Regional support handbook']
            if index == 1:
                text(page, 40, 750, 'Unique legal note applies to this office only.', 9)
                anchors.append('Unique legal note applies to this office only.')
            pages.append(ExpectedPage(anchors, ['## ' + heading], [f'Page {index + 1} of 3']))
        cases.append(Case('recurring-margins-and-unique-note', document.tobytes(), pages))

    with pymupdf.open() as document:
        page = new_page(document)
        text(page, 40, 110, 'Proof of address', bold=True)
        body = 'Bring a recent utility statement showing your name and your current home address.'
        text(page, 40, 140, body)
        text(page, 40, 190, 'Accepted documents', bold=True)
        last = 'A current bank statement or an official council letter can also be accepted.'
        text(page, 40, 220, last)
        cases.append(Case('bold-section-headings', document.tobytes(), [ExpectedPage(
            ['Proof of address', body, 'Accepted documents', last], ['## Proof of address', '## Accepted documents'])]))

    with pymupdf.open() as document:
        page = new_page(document)
        # A borderless list is a conservative text-preservation control.
        anchors = ['Appointment checklist', 'Bring your account reference.', 'Bring a signed consent form.',
                   'Ask the adviser about the next steps.']
        text(page, 40, 90, anchors[0], 18)
        for index, value in enumerate(anchors[1:]):
            text(page, 60, 140 + index * 25, value)
        cases.append(Case('indented-list-control', document.tobytes(), [ExpectedPage(anchors, ['# ' + anchors[0]])]))
    return cases
