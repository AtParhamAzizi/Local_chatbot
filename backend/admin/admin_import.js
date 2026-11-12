'use strict';

const sourceFile = document.getElementById('sourceFile');
const replaceExisting = document.getElementById('replaceExisting');
const previewButton = document.getElementById('previewButton');
const publishButton = document.getElementById('publishButton');
const cancelButton = document.getElementById('cancelButton');
const retryButton = document.getElementById('retryButton');
const statusText = document.getElementById('status');
const previewText = document.getElementById('preview');
const resultText = document.getElementById('result');
let activeJob = null;
let previewVersion = 0;
let running = false;

async function requestJson(path, options = {}) {
    const response = await adminFetch(path, { credentials: 'same-origin', ...options });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
}

function uploadBody() {
    const file = sourceFile.files[0];
    if (!file) throw new Error('فایل را انتخاب کنید.');
    if (!/\.(txt|md|csv|docx)$/i.test(file.name)) throw new Error('قالب فایل پشتیبانی نمی‌شود.');
    if (file.size > 20 * 1024 * 1024) throw new Error('حجم فایل بیش از ۲۰ مگابایت است.');
    const body = new FormData();
    body.append('file', file);
    body.append('replace', replaceExisting.checked ? '1' : '0');
    return body;
}

function resetPreview() {
    previewVersion += 1;
    publishButton.disabled = true;
    previewText.textContent = '';
}

function setRunning(value) {
    running = value;
    sourceFile.disabled = value;
    replaceExisting.disabled = value;
    previewButton.disabled = value;
    publishButton.disabled = true;
    cancelButton.disabled = !value;
}

function displayJob(job) {
    statusText.textContent = `${job.filename || ''}: ${job.status}`;
    resultText.textContent = JSON.stringify(job.progress || job.result || job.error || job, null, 2);
    retryButton.disabled = !job.retryable;
}

async function pollJob() {
    const jobId = activeJob;
    try {
        const job = await requestJson(`/admin/api/import/jobs/${jobId}`);
        if (jobId !== activeJob) return;
        displayJob(job);
        if (['queued', 'running'].includes(job.status)) {
            window.setTimeout(pollJob, 1000);
        } else {
            setRunning(false);
            resultText.textContent = JSON.stringify(job.result || job.error || job, null, 2);
            resetPreview();
        }
    } catch (error) {
        statusText.textContent = error.message;
        if (running && jobId === activeJob) window.setTimeout(pollJob, 3000);
    }
}

sourceFile.addEventListener('change', resetPreview);
replaceExisting.addEventListener('change', resetPreview);
previewButton.addEventListener('click', async () => {
    const version = previewVersion;
    previewButton.disabled = true;
    publishButton.disabled = true;
    try {
        const report = await requestJson('/admin/api/import/preflight', { method: 'POST', body: uploadBody() });
        if (version !== previewVersion) return;
        previewText.textContent = JSON.stringify(report, null, 2);
        publishButton.disabled = !report.can_publish;
        statusText.textContent = report.can_publish ? 'پیش‌نمایش و اطلاعات منبع را بررسی و سپس ورود را آغاز کنید.' : 'اشکالات فایل را اصلاح کنید.';
    } catch (error) {
        statusText.textContent = error.message;
    } finally {
        previewButton.disabled = running;
    }
});

document.getElementById('importForm').addEventListener('submit', async event => {
    event.preventDefault();
    if (publishButton.disabled || running) return;
    try {
        const body = uploadBody();
        setRunning(true);
        retryButton.disabled = true;
        const job = await requestJson('/admin/api/import/jobs', { method: 'POST', body });
        activeJob = job.job_id;
        pollJob();
    } catch (error) {
        setRunning(false);
        statusText.textContent = error.message;
    }
});

cancelButton.addEventListener('click', async () => {
    cancelButton.disabled = true;
    try {
        displayJob(await requestJson(`/admin/api/import/jobs/${activeJob}`, { method: 'DELETE' }));
    } catch (error) {
        statusText.textContent = error.message;
        cancelButton.disabled = !running;
    }
});

retryButton.addEventListener('click', async () => {
    retryButton.disabled = true;
    try {
        setRunning(true);
        await requestJson(`/admin/api/import/jobs/${activeJob}/retry`, { method: 'POST' });
        pollJob();
    } catch (error) {
        setRunning(false);
        retryButton.disabled = false;
        statusText.textContent = error.message;
    }
});
