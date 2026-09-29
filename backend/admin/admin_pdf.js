const API_BASE =
    (window.location.protocol === 'file:')
        ? 'http://localhost:8000'
        : '';

const $ = (s) =>
    document.querySelector(s);


let selectedFile = null;

let currentPage = 1;

let totalPages = 1;

const PER_PAGE = 10;


/* =========================================================
   API
========================================================= */

async function api(url, options = {}) {

    const res = await fetch(
        `${API_BASE}${url}`,
        {
            credentials: 'same-origin',
            ...options
        }
    );

    if (!res.ok) {

        let msg = `خطای ${res.status}`;

        try {

            const j = await res.json();

            msg =
                j.error ||
                j.message ||
                msg;

        } catch (_) {}

        throw new Error(msg);
    }

    return res.json();
}


/* =========================================================
   HELPERS
========================================================= */

function esc(str) {

    return String(str).replace(
        /[&<>"']/g,
        c => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        }[c])
    );
}


function formatBytes(bytes) {

    if (bytes < 1024)
        return `${bytes} B`;

    if (bytes < 1024 * 1024)
        return `${(bytes / 1024).toFixed(1)} KB`;

    if (bytes < 1024 * 1024 * 1024)
        return `${(bytes / 1024 / 1024).toFixed(1)} MB`;

    return `${(
        bytes /
        1024 /
        1024 /
        1024
    ).toFixed(1)} GB`;
}


/* =========================================================
   RESULT
========================================================= */

function clearResult() {

    const r = $('#result');

    r.className = 'result';

    r.innerHTML = '';
}


function showResult(
    type,
    title,
    bullets = []
) {

    const r = $('#result');

    r.className =
        'result show ' + type;

    let html =
        `<div>${esc(title)}</div>`;

    if (bullets.length) {

        html +=
            '<ul>' +
            bullets
                .map(
                    b =>
                        `<li>${esc(b)}</li>`
                )
                .join('') +
            '</ul>';
    }

    r.innerHTML = html;
}


/* =========================================================
   FILE PICKING
========================================================= */

const dropzone =
    $('#dropzone');

const fileInput =
    $('#fileInput');


dropzone.addEventListener(
    'click',
    () => fileInput.click()
);


dropzone.addEventListener(
    'dragover',
    e => {

        e.preventDefault();

        dropzone.classList.add(
            'dragover'
        );
    }
);


dropzone.addEventListener(
    'dragleave',
    () => {

        dropzone.classList.remove(
            'dragover'
        );
    }
);


dropzone.addEventListener(
    'drop',
    e => {

        e.preventDefault();

        dropzone.classList.remove(
            'dragover'
        );

        if (
            e.dataTransfer.files.length
        ) {

            handleFile(
                e.dataTransfer.files[0]
            );
        }
    }
);


fileInput.addEventListener(
    'change',
    e => {

        if (e.target.files.length) {

            handleFile(
                e.target.files[0]
            );
        }
    }
);


/* =========================================================
   HANDLE PDF
========================================================= */

function handleFile(file) {

    clearResult();

    if (
        !file.name
            .toLowerCase()
            .endsWith('.pdf')
    ) {

        showResult(
            'err',
            'فقط فایل PDF پذیرفته می‌شود.'
        );

        return;
    }


    selectedFile = file;


    $('#fileInfo').innerHTML = `

        <b>${esc(file.name)}</b>

        <br>

        حجم فایل:
        <b>${formatBytes(file.size)}</b>

    `;

    $('#fileInfo')
        .classList.add('show');


    /*
     * Browser cannot reliably extract the PDF text here
     * without an additional PDF.js dependency.
     *
     * We therefore show the browser's built-in PDF viewer.
     */

    const url =
        URL.createObjectURL(file);


    $('#pdfPreview').innerHTML = `

        <iframe
            src="${url}"
            width="100%"
            height="400"
            style="
                border:0;
                border-radius:8px;
            "
        ></iframe>

    `;

    $('#pdfPreview')
        .classList.add('show');


    $('#uploadBtn')
        .disabled = false;
}


/* =========================================================
   UPLOAD
========================================================= */

$('#uploadBtn')
    .addEventListener(
        'click',
        async () => {

            if (!selectedFile)
                return;


            const btn =
                $('#uploadBtn');


            btn.disabled = true;


            const original =
                btn.textContent;


            btn.innerHTML =
                'در حال استخراج و بردارسازی... ' +
                '<span class="spinner"></span>';


            clearResult();


            const fd =
                new FormData();


            fd.append(
                'file',
                selectedFile,
                selectedFile.name
            );


            fd.append(
                'replace',
                $('#replaceDupes').checked
                    ? 'true'
                    : 'false'
            );


            try {

                const r =
                    await api(
                        '/admin/api/pdf/upload',
                        {
                            method: 'POST',
                            body: fd
                        }
                    );


                showResult(
                    'ok',

                    'PDF با موفقیت پردازش شد.',

                    [

                        `نام فایل: ${r.filename}`,

                        `تعداد صفحات: ${r.page_count}`,

                        `تعداد chunk: ${r.chunk_count}`,

                        `chunkهای بردارسازی‌شده: ${r.embedded_chunks}`,

                        `ابعاد embedding: ${r.embedding_dim}`,

                        `زمان پردازش: ${r.duration_sec} ثانیه`

                    ]
                );


                loadStats();

                currentPage = 1;

                loadDocuments();

            } catch (e) {

                showResult(
                    'err',
                    'خطا: ' + e.message
                );

            } finally {

                btn.disabled = false;

                btn.textContent =
                    original;
            }
        }
    );


/* =========================================================
   CLEAR
========================================================= */

$('#clearBtn')
    .addEventListener(
        'click',
        () => {

            selectedFile = null;

            fileInput.value = '';

            $('#fileInfo')
                .classList.remove('show');

            $('#pdfPreview')
                .classList.remove('show');

            $('#pdfPreview')
                .innerHTML = '';

            $('#uploadBtn')
                .disabled = true;

            clearResult();
        }
    );


/* =========================================================
   STATS
========================================================= */

async function loadStats() {

    try {

        const data =
            await api(
                '/admin/api/pdf/stats'
            );


        $('#statDocuments')
            .textContent =
                data.documents;


        $('#statChunks')
            .textContent =
                data.chunks;


        $('#statEmbedded')
            .textContent =
                data.embedded_chunks;


        $('#statDim')
            .textContent =
                data.dim ?? '–';

    } catch (e) {

        console.error(e);
    }
}


/* =========================================================
   DOCUMENTS
========================================================= */

async function loadDocuments() {

    const container =
        $('#docsContainer');


    const q =
        $('#searchInput')
            .value
            .trim();


    try {

        const data =
            await api(

                `/admin/api/pdf/documents` +
                `?page=${currentPage}` +
                `&per_page=${PER_PAGE}` +
                `&q=${encodeURIComponent(q)}`
            );


        totalPages =
            Math.max(
                1,
                Math.ceil(
                    data.total /
                    PER_PAGE
                )
            );


        if (!data.items.length) {

            container.innerHTML =
                '<div class="empty">' +
                'فایل PDF یافت نشد.' +
                '</div>';

        } else {

            container.innerHTML = `

                <table>

                    <thead>

                        <tr>

                            <th>#</th>

                            <th>نام فایل</th>

                            <th>صفحات</th>

                            <th>Chunk</th>

                            <th>Embedding</th>

                            <th>وضعیت</th>

                            <th></th>

                        </tr>

                    </thead>


                    <tbody>

                        ${data.items.map(d => `

                            <tr>

                                <td>
                                    ${d.id}
                                </td>


                                <td class="filename">

                                    ${esc(
                                        d.filename
                                    )}

                                    <br>

                                    <small>
                                        ${formatBytes(
                                            d.file_size
                                        )}
                                    </small>

                                </td>


                                <td>
                                    ${d.page_count}
                                </td>


                                <td>
                                    ${d.chunk_count}
                                </td>


                                <td>

                                    ${d.embedded_chunk_count}

                                    /
                                    ${d.chunk_count}

                                    <br>

                                    <small>
                                        dim=${d.embedding_dim ?? '–'}
                                    </small>

                                </td>


                                <td>

                                    <span
                                        class="badge ${d.status}"
                                    >

                                        ${statusText(
                                            d.status
                                        )}

                                    </span>

                                </td>


                                <td>

                                    <button
                                        class="btn-danger"
                                        onclick="deletePdf(${d.id})"
                                    >
                                        حذف
                                    </button>

                                </td>

                            </tr>

                        `).join('')}

                    </tbody>

                </table>

            `;
        }


        $('#pageInfo')
            .textContent =
                `صفحه ${currentPage} از ${totalPages}`;


        $('#prevBtn')
            .disabled =
                currentPage <= 1;


        $('#nextBtn')
            .disabled =
                currentPage >= totalPages;


    } catch (e) {

        container.innerHTML = `

            <div class="empty">

                خطا در بارگذاری:
                ${esc(e.message)}

            </div>

        `;
    }
}


function statusText(status) {

    switch (status) {

        case 'completed':
            return 'تکمیل شده';

        case 'processing':
            return 'در حال پردازش';

        case 'failed':
            return 'ناموفق';

        default:
            return status;
    }
}


/* =========================================================
   DELETE
========================================================= */

async function deletePdf(id) {

    if (
        !confirm(
            `فایل PDF شماره ${id} حذف شود؟`
        )
    ) {

        return;
    }


    try {

        await api(
            `/admin/api/pdf/documents/${id}`,
            {
                method: 'DELETE'
            }
        );


        loadDocuments();

        loadStats();


    } catch (e) {

        alert(
            'خطا در حذف: ' +
            e.message
        );
    }
}


/* =========================================================
   PAGINATION
========================================================= */

$('#prevBtn')
    .addEventListener(
        'click',
        () => {

            if (currentPage > 1) {

                currentPage--;

                loadDocuments();
            }
        }
    );


$('#nextBtn')
    .addEventListener(
        'click',
        () => {

            if (
                currentPage <
                totalPages
            ) {

                currentPage++;

                loadDocuments();
            }
        }
    );


/* =========================================================
   SEARCH
========================================================= */

$('#searchBtn')
    .addEventListener(
        'click',
        () => {

            currentPage = 1;

            loadDocuments();
        }
    );


$('#searchInput')
    .addEventListener(
        'keypress',
        e => {

            if (e.key === 'Enter') {

                currentPage = 1;

                loadDocuments();
            }
        }
    );




loadStats();

loadDocuments();