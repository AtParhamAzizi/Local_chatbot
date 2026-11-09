(function () {
    let tokenRequest = null;
    const ingestionWatchers = new Map();

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
        let response = await window.adminFetch(url, { method: 'POST', body });
        let job = await response.json();
        if (!response.ok) throw new Error(job.error || `HTTP ${response.status}`);
        const location = response.headers.get('Location');
        if (!location) throw new Error('Missing ingestion job location.');
        sessionStorage.setItem(location.includes('/pdf/') ? 'pdf-ingestion-job' : 'qa-ingestion-job', location);
        return window.adminWatchIngestion(location, onStatus);
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
        const upload = document.querySelector('#uploadBtn');
        if (active) upload.disabled = true;
        else if (typeof selectedFile !== 'undefined') upload.disabled = !selectedFile;
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
        const location = sessionStorage.getItem(pdf ? 'pdf-ingestion-job' : 'qa-ingestion-job');
        if (location && /^\/admin\/api\/(pdf\/)?upload\/jobs\/[0-9a-f]{32}$/.test(location)) {
            window.adminWatchIngestion(location, window.adminIngestionStatus).catch(() => {});
        }
    });
})();
