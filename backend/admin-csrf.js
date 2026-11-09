(function () {
    let tokenRequest = null;
    const ingestionWatchers = new Map();
    let ingestionActive = false;
    let reviewedFile = null;
    let reviewedReplace = false;
    let preflightRevision = 0;

    window.adminCanPublish = (file, replace) => !ingestionActive && file !== null &&
        file === reviewedFile && replace === reviewedReplace;

    window.adminResetPreflight = function (file) {
        preflightRevision += 1;
        reviewedFile = null;
        document.getElementById('preflightReport')?.remove();
        document.getElementById('preflightBtn').disabled = !file || ingestionActive;
        document.getElementById('uploadBtn').disabled = true;
    };

    async function previewUpload(pdf) {
        const file = selectedFile;
        if (!file || ingestionActive) return;
        window.adminResetPreflight(file);
        const revision = preflightRevision;
        const replace = document.getElementById('replaceDupes').checked;
        const button = document.getElementById('preflightBtn');
        button.disabled = true;
        const panel = document.createElement('div');
        panel.id = 'preflightReport';
        panel.setAttribute('role', 'status');
        panel.setAttribute('aria-live', 'polite');
        panel.style.whiteSpace = 'pre-wrap';
        panel.style.overflowWrap = 'anywhere';
        panel.textContent = 'در حال بررسی فایل؛ دانش قابل جستجو تغییر نمی‌کند…';
        document.getElementById('result').before(panel);
        const body = new FormData();
        body.append('file', file, file.name);
        body.append('replace', String(replace));
        try {
            const response = await window.adminFetch(`/admin/api/${pdf ? 'pdf/' : ''}upload/preflight`, { method: 'POST', body });
            const report = await response.json();
            if (revision !== preflightRevision) return;
            if (!response.ok) throw new Error(report.error || `HTTP ${response.status}`);
            const lines = [report.can_publish ? 'بررسی کامل شد؛ برای انتشار دکمه شروع را بزنید.' : 'پیش از انتشار، خطاهای زیر را اصلاح کنید.'];
            if (report.counts) {
                const counts = report.counts;
                lines.push(`رکوردها: ${counts.records ?? '—'} | صفحات: ${counts.pages ?? '—'}`,
                    `تکراری در فایل: ${counts.duplicates_in_file} | موجود در دانش: ${counts.duplicates_existing}`,
                    `قطعه‌های مورد انتظار: ${counts.expected_chunks}`,
                    `درج: ${counts.inserts} | جایگزینی: ${counts.updates} | نادیده گرفته‌شده: ${counts.skipped}`);
            }
            if (report.cost) {
                lines.push(`برآورد پردازش: ${report.cost.embedding_inputs} متن، ${report.cost.embedding_batches} دسته، ${report.cost.input_characters} نویسه`,
                    'زمان و مصرف منابع به مدل و سخت‌افزار بستگی دارد. این بررسی بردارسازی انجام نمی‌دهد.');
                if (report.cost.ocr_pages !== undefined) lines.push(`صفحات پردازش‌شده با OCR محلی: ${report.cost.ocr_pages}`);
            }
            if (report.extraction) {
                lines.push(`استخراج محلی OCR فعال است. حداقل اطمینان میانگین: ${report.extraction.minimum_confidence}٪؛ حداقل اطمینان هر واژه: ${report.extraction.minimum_word_confidence}٪.`);
                for (const extracted of report.extraction.pages) {
                    const method = extracted.method === 'ocr' ? 'OCR محلی' : 'متن PDF';
                    lines.push(`صفحه ${extracted.page} — ${method} — اطمینان: ${extracted.confidence ?? '—'}٪ — کمترین اطمینان واژه: ${extracted.min_confidence ?? '—'}٪${extracted.needs_review ? ' — نیازمند بازبینی؛ انتشار متوقف است' : ''}\n${extracted.text_sample || ''}`);
                }
            }
            for (const issue of report.issues) {
                const location = issue.record ? `رکورد ${issue.record}` : issue.page ? `صفحه ${issue.page}` : 'فایل';
                lines.push(`${issue.severity === 'warning' ? 'هشدار' : 'خطا'} — ${location}${issue.field ? ` / ${issue.field}` : ''}: ${issue.message}`);
            }
            if (report.issues_truncated) lines.push('فقط ۲۰ مورد اول نمایش داده شد. پس از اصلاح، دوباره بررسی کنید.');
            lines.push('نمونه استخراج (ابتدا، میانه و انتها؛ حداکثر ۲۴۰ نویسه از هر فیلد):');
            for (const sample of report.samples) {
                lines.push(sample.record ? `رکورد ${sample.record}: ${sample.question}\n${sample.answer}` : `صفحه ${sample.page}: ${sample.text}`);
            }
            lines.push('این برآورد موقتی است؛ هنگام انتشار، فایل و موارد تکراری دوباره بررسی می‌شوند.');
            panel.textContent = lines.join('\n\n');
            if (report.can_publish) {
                reviewedFile = file;
                reviewedReplace = replace;
            }
            document.getElementById('uploadBtn').disabled = !window.adminCanPublish(selectedFile, replace);
        } catch (error) {
            if (revision === preflightRevision) panel.textContent = error.message;
        } finally {
            if (revision === preflightRevision) button.disabled = ingestionActive;
        }
    }

    function getToken() {
        if (!tokenRequest) {
            tokenRequest = fetch('/admin/api/session', {
                credentials: 'same-origin',
                cache: 'no-store'
            }).then(async response => {
                if (!response.ok) {
                    const error = new Error('Unable to verify your session. Reload the page and sign in again.');
                    error.status = response.status;
                    throw error;
                }
                const payload = await response.json();
                if (typeof payload.csrf_token !== 'string' || !payload.csrf_token) {
                    throw new Error('Unable to obtain CSRF proof. Reload the page and try again.');
                }
                return payload.csrf_token;
            }).catch(error => {
                tokenRequest = null;
                throw error;
            });
        }
        return tokenRequest;
    }

    window.adminFetch = async function (url, options = {}) {
        const target = new URL(url, window.location.href);
        if (target.origin !== window.location.origin) {
            throw new Error('Administrator requests must use the same origin.');
        }
        const settings = { credentials: 'same-origin', ...options };
        const method = (options.method || 'GET').toUpperCase();
        if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
            const headers = new Headers(options.headers);
            headers.set('X-CSRF-Token', await getToken());
            settings.headers = headers;
        }
        const response = await fetch(target.href, settings);
        if (response.status === 401 || response.status === 403) {
            tokenRequest = null;
        }
        return response;
    };

    window.adminIngestion = async function (url, body, onStatus) {
        ingestionActive = true;
        document.getElementById('preflightBtn').disabled = true;
        try {
            const response = await window.adminFetch(url, { method: 'POST', body });
            const job = await response.json();
            if (!response.ok) throw new Error(job.error || `HTTP ${response.status}`);
            const location = response.headers.get('Location');
            if (!location) throw new Error('Missing ingestion job location.');
            sessionStorage.setItem(location.includes('/pdf/') ? 'pdf-ingestion-job' : 'qa-ingestion-job', location);
            return await window.adminWatchIngestion(location, onStatus);
        } finally {
            ingestionActive = false;
            document.getElementById('preflightBtn').disabled = !selectedFile;
        }
    };

    window.adminWatchIngestion = function (location, onStatus) {
        if (ingestionWatchers.has(location)) return ingestionWatchers.get(location);
        const pending = watchIngestion(location, onStatus).finally(() => ingestionWatchers.delete(location));
        ingestionWatchers.set(location, pending);
        return pending;
    };

    async function watchIngestion(location, onStatus) {
        while (true) {
            const response = await window.adminFetch(location, { cache: 'no-store' });
            const job = await response.json();
            if (!response.ok) throw new Error(job.error || `HTTP ${response.status}`);
            if (onStatus) onStatus(job, location);
            if (job.status === 'completed') {
                sessionStorage.removeItem(location.includes('/pdf/') ? 'pdf-ingestion-job' : 'qa-ingestion-job');
                window.dispatchEvent(new Event('ingestion-completed'));
                return job.result;
            }
            if (job.status === 'failed' || job.status === 'canceled') {
                throw new Error(job.error?.error || 'Ingestion canceled.');
            }
            await new Promise(resolve => setTimeout(resolve, 1000));
        }
    }

    window.adminIngestionStatus = function (job, location) {
        let panel = document.getElementById('ingestionProgress');
        if (!panel) {
            panel = document.createElement('div');
            panel.id = 'ingestionProgress';
            panel.setAttribute('role', 'status');
            panel.setAttribute('aria-live', 'polite');
            document.querySelector('#result').before(panel);
        }
        panel.replaceChildren();
        const label = document.createElement('p');
        const stages = { extraction: 'استخراج', chunking: 'قطعه‌بندی', embedding: 'بردارسازی', publishing: 'انتشار' };
        const states = { queued: 'در صف', running: 'در حال پردازش', completed: 'تکمیل شد', failed: 'ناموفق', canceled: 'لغو شد' };
        const progress = job.progress;
        label.textContent = `${states[job.status] || job.status}${progress ? ` — ${stages[progress.stage] || progress.stage}: ${progress.completed}/${progress.total}` : ''}`;
        panel.append(label);
        const identity = document.createElement('code');
        identity.textContent = job.job_id;
        panel.append(identity);
        const active = ['queued', 'running'].includes(job.status);
        ingestionActive = active;
        const upload = document.querySelector('#uploadBtn');
        if (active) upload.disabled = true;
        else if (typeof selectedFile !== 'undefined') upload.disabled = !window.adminCanPublish(selectedFile, document.getElementById('replaceDupes').checked);
        document.getElementById('preflightBtn').disabled = active || !selectedFile;
        if (progress) {
            const meter = document.createElement('progress');
            meter.max = progress.total || 1;
            meter.value = progress.completed;
            meter.setAttribute('aria-label', stages[progress.stage] || progress.stage);
            panel.append(meter);
        }
        const action = (text, method, target) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.textContent = text;
            button.onclick = async () => {
                button.disabled = true;
                try {
                    const response = await window.adminFetch(target, { method });
                    const current = await response.json();
                    if (!response.ok) throw new Error(current.error || `HTTP ${response.status}`);
                    window.adminIngestionStatus(current, location);
                    if (method === 'POST') await window.adminWatchIngestion(location, window.adminIngestionStatus);
                } catch (error) {
                    label.textContent = error.message;
                    button.disabled = false;
                }
            };
            panel.append(button);
        };
        if (active) action('لغو', 'DELETE', location);
        if (job.retryable) action('تلاش دوباره', 'POST', `${location}/retry`);
    };

    document.addEventListener('DOMContentLoaded', () => {
        const pdf = window.location.pathname.endsWith('/admin_pdf.html');
        if (!pdf && !window.location.pathname.endsWith('/admin.html')) return;
        document.getElementById('preflightBtn').addEventListener('click', () => previewUpload(pdf));
        document.getElementById('replaceDupes').addEventListener('change', () => window.adminResetPreflight(selectedFile));
        const location = sessionStorage.getItem(pdf ? 'pdf-ingestion-job' : 'qa-ingestion-job');
        if (location && /^\/admin\/api\/(pdf\/)?upload\/jobs\/[0-9a-f]{32}$/.test(location)) {
            window.adminWatchIngestion(location, window.adminIngestionStatus).catch(() => {});
        }
    });
})();
