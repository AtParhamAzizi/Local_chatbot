/* =====================================================
   API CONFIGURATION
===================================================== */

const API_BASE =
    (window.location.protocol === 'file:')
        ? 'http://localhost:8000'
        : '';

const API_URL =
    `${API_BASE}/admin/api/logs`;


/* =====================================================
   PAGINATION
===================================================== */

let currentPage = 1;

const pageSize = 25;


/* =====================================================
   DATA
===================================================== */

let allLogs = [];


/* =====================================================
   DOM ELEMENTS
===================================================== */

const tableBody =
    document.getElementById("logsTableBody");

const searchInput =
    document.getElementById("searchInput");

const levelFilter =
    document.getElementById("levelFilter");

const warningFilter =
    document.getElementById("warningFilter");

const refreshBtn =
    document.getElementById("refreshBtn");

const prevBtn =
    document.getElementById("prevBtn");

const nextBtn =
    document.getElementById("nextBtn");

const pageInfo =
    document.getElementById("pageInfo");

const totalLogs =
    document.getElementById("totalLogs");

const infoLogs =
    document.getElementById("infoLogs");

const failedLogs =
    document.getElementById("failedLogs");

const warningLogs =
    document.getElementById("warningLogs");


/* =====================================================
   LOAD LOGS
===================================================== */

async function loadLogs() {

    tableBody.innerHTML = `
        <tr>
            <td
                colspan="6"
                class="loading"
            >
                در حال دریافت لاگ‌ها...
            </td>
        </tr>
    `;


    try {

        const response =
            await fetch(API_URL, {
                method: "GET",

                headers: {
                    "Accept":
                        "application/json"
                }
            });


        if (!response.ok) {

            throw new Error(
                `HTTP ${response.status}`
            );
        }


        const data =
            await response.json();


        /*
         Expected API:

         {
             "success": true,
             "logs": [
                 {
                     "id": 1,
                     "_level": "database.py",
                     "_from": "get_conn",
                     "message": "Database connected",
                     "warninglevel": 1,
                     "created_at": "2026-09-14 02:30:00"
                 }
             ]
         }
        */


        if (Array.isArray(data)) {

            allLogs = data;

        } else {

            allLogs =
                Array.isArray(data.logs)
                    ? data.logs
                    : [];
        }


        currentPage = 1;


        updateStats();

        renderLogs();

    } catch (error) {

        console.error(
            "Failed to load admin logs:",
            error
        );


        tableBody.innerHTML = `
            <tr>
                <td
                    colspan="6"
                    class="empty"
                >
                    دریافت لاگ‌ها با خطا مواجه شد.
                </td>
            </tr>
        `;
    }
}


/* =====================================================
   STATISTICS
===================================================== */

function updateStats() {

    let information = 0;

    let failed = 0;

    let warning = 0;


    for (const log of allLogs) {

        const level =
            Number(log.warninglevel);


        if (level === 1) {

            information++;

        } else if (level === 2) {

            warning++;

        } else if (level === 3) {

            failed++;
        }
    }


    totalLogs.textContent =
        allLogs.length;

    infoLogs.textContent =
        information;

    failedLogs.textContent =
        failed;

    warningLogs.textContent =
        warning;
}


/* =====================================================
   FILTER
===================================================== */

function getFilteredLogs() {

    const search =
        searchInput.value
            .trim()
            .toLowerCase();


    const selectedFile =
        levelFilter.value
            .trim()
            .toLowerCase();


    const selectedWarning =
        warningFilter.value;


    return allLogs.filter(log => {


        /*
         -----------------------------------------------
         Filter by Python file
         -----------------------------------------------
        */

        const pythonFile =
            String(
                log._level ?? ""
            )
            .trim()
            .toLowerCase()
            .replace(/\.py$/, "");


        if (
            selectedFile &&
            pythonFile !== selectedFile
        ) {

            return false;
        }


        /*
         -----------------------------------------------
         Filter by warninglevel
         -----------------------------------------------
        */

        const warningLevel =
            String(
                log.warninglevel ?? ""
            );


        if (
            selectedWarning &&
            warningLevel !== selectedWarning
        ) {

            return false;
        }


        /*
         -----------------------------------------------
         Search
         -----------------------------------------------
        */

        if (search) {

            const searchable = [

                log.id,

                log._level,

                log._from,

                log.message,

                log.warninglevel,

                log.created_at

            ]
                .filter(
                    value =>
                        value !== null &&
                        value !== undefined
                )
                .join(" ")
                .toLowerCase();


            if (
                !searchable.includes(search)
            ) {

                return false;
            }
        }


        return true;
    });
}


/* =====================================================
   RENDER TABLE
===================================================== */

function renderLogs() {

    const filteredLogs =
        getFilteredLogs();


    const totalPages =
        Math.max(
            1,
            Math.ceil(
                filteredLogs.length /
                pageSize
            )
        );


    if (currentPage > totalPages) {

        currentPage =
            totalPages;
    }


    const startIndex =
        (currentPage - 1) *
        pageSize;


    const endIndex =
        startIndex +
        pageSize;


    const pageLogs =
        filteredLogs.slice(
            startIndex,
            endIndex
        );


    if (pageLogs.length === 0) {

        tableBody.innerHTML = `
            <tr>
                <td
                    colspan="6"
                    class="empty"
                >
                    هیچ لاگی پیدا نشد.
                </td>
            </tr>
        `;

    } else {

        tableBody.innerHTML =
            pageLogs
                .map(
                    log =>
                        createLogRow(log)
                )
                .join("");
    }


    pageInfo.textContent =
        `page ${currentPage} of ${totalPages}`;


    prevBtn.disabled =
        currentPage <= 1;


    nextBtn.disabled =
        currentPage >= totalPages;
}


/* =====================================================
   CREATE TABLE ROW
===================================================== */

function createLogRow(log) {

    const id =
        escapeHtml(
            log.id ?? "-"
        );


    /*
     * _level = Python file name
     */

    const pythonFile =
        escapeHtml(
            log._level ?? "-"
        );


    /*
     * _from = function name
     */

    const functionName =
        escapeHtml(
            log._from ?? "-"
        );


    const message =
        escapeHtml(
            log.message ?? "-"
        );


    const warningLevel =
        Number(
            log.warninglevel
        );


    const status =
        getStatus(warningLevel);


    const createdAt =
        formatDate(
            log.created_at
        );


    return `
        <tr>

            <td>
                ${id}
            </td>


            <td>

                <span class="python-file">
                    ${pythonFile}.py
                </span>

            </td>


            <td>

                <span class="function-name">
                    ${functionName}()
                </span>

            </td>


            <td
                class="message"
                title="${message}"
            >
                ${message}
            </td>


            <td>

                <span
                    class="status ${status.className}"
                >
                    ${status.text}
                </span>

            </td>


            <td>

                <span class="date">
                    ${createdAt}
                </span>

            </td>

        </tr>
    `;
}


/* =====================================================
   WARNING LEVEL
===================================================== */

function getStatus(level) {

    if (level === 1) {

        return {
            text: "information",
            className:
                "status-information"
        };
    }


    if (level === 2) {

        return {
            text: "warning",
            className:
                "status-warning"
        };
    }


    if (level === 3) {

        return {
            text: "Failed",
            className:
                "status-failed"
        };
    }


    return {
        text: "unknown",
        className:
            "status-unknown"
    };
}


/* =====================================================
   DATE FORMAT
===================================================== */

function formatDate(value) {

    if (!value) {

        return "-";
    }


    /*
     * MySQL normally returns:
     *
     * 2026-09-14 02:30:15
     *
     * Convert it to:
     *
     * 2026-09-14T02:30:15
     */

    const normalized =
        String(value)
            .replace(" ", "T");


    const date =
        new Date(normalized);


    if (
        Number.isNaN(
            date.getTime()
        )
    ) {

        return escapeHtml(value);
    }


    return date.toLocaleString(
        "fa-IR",
        {
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit"
        }
    );
}


/* =====================================================
   HTML ESCAPE
===================================================== */

function escapeHtml(value) {

    return String(value)

        .replaceAll(
            "&",
            "&amp;"
        )

        .replaceAll(
            "<",
            "&lt;"
        )

        .replaceAll(
            ">",
            "&gt;"
        )

        .replaceAll(
            '"',
            "&quot;"
        )

        .replaceAll(
            "'",
            "&#039;"
        );
}


/* =====================================================
   SEARCH
===================================================== */

searchInput.addEventListener(
    "input",
    () => {

        currentPage = 1;

        renderLogs();
    }
);


/* =====================================================
   PYTHON FILE FILTER
===================================================== */

levelFilter.addEventListener(
    "change",
    () => {

        currentPage = 1;

        renderLogs();
    }
);


/* =====================================================
   WARNING LEVEL FILTER
===================================================== */

warningFilter.addEventListener(
    "change",
    () => {

        currentPage = 1;

        renderLogs();
    }
);


/* =====================================================
   REFRESH
===================================================== */

refreshBtn.addEventListener(
    "click",
    loadLogs
);


/* =====================================================
   PREVIOUS PAGE
===================================================== */

prevBtn.addEventListener(
    "click",
    () => {

        if (currentPage > 1) {

            currentPage--;

            renderLogs();
        }
    }
);


/* =====================================================
   NEXT PAGE
===================================================== */

nextBtn.addEventListener(
    "click",
    () => {

        const filteredLogs =
            getFilteredLogs();


        const totalPages =
            Math.max(
                1,
                Math.ceil(
                    filteredLogs.length /
                    pageSize
                )
            );


        if (
            currentPage <
            totalPages
        ) {

            currentPage++;

            renderLogs();
        }
    }
);


/* =====================================================
   INITIAL LOAD
===================================================== */

loadLogs();
