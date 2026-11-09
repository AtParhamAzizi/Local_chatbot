(function () {
    let tokenRequest = null;

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
        while (true) {
            if (onStatus) onStatus(job);
            if (job.status === 'completed') return job.result;
            if (job.status === 'failed' || job.status === 'canceled') {
                throw new Error(job.error?.error || 'Ingestion canceled.');
            }
            await new Promise(resolve => setTimeout(resolve, 1000));
            response = await window.adminFetch(location, { cache: 'no-store' });
            job = await response.json();
            if (!response.ok) throw new Error(job.error || `HTTP ${response.status}`);
        }
    };
})();
