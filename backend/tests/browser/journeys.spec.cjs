const { test: base, expect } = require(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const { installAudio } = require('./audio.cjs');

const uiURL = process.env.BROWSER_UI_URL;
const adminURL = process.env.BROWSER_ADMIN_URL;
const fixtureHeaders = { 'X-Browser-Fixture': process.env.BROWSER_SMOKE_TOKEN };
const password = 'browser-tests-only-password';
const reply = '\u067e\u0627\u0633\u062e \u0622\u0632\u0645\u0627\u06cc\u0634\u06cc';
const followUp = '\u0633\u0648\u0627\u0644 \u0628\u0639\u062f\u06cc';

const test = base.extend({
    browserSafety: [async ({ context }, use, testInfo) => {
        const errors = [];
        const blocked = [];
        context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
        await context.route('**/*', route => {
            const target = new URL(route.request().url());
            if ([uiURL, adminURL].includes(target.origin)) return route.continue();
            blocked.push(target.href);
            return route.abort('blockedbyclient');
        });
        const reset = await context.request.post(`${uiURL}/__browser__/reset`, {
            headers: fixtureHeaders, data: {},
        });
        expect(reset.status()).toBe(200);
        await use();
        await testInfo.attach('browser-errors', {
            body: JSON.stringify({ errors, blocked }, null, 2), contentType: 'application/json',
        });
        expect(errors, 'Uncaught errors in the page or an iframe').toEqual([]);
        expect(blocked, 'Browser requests must stay inside this disposable fixture').toEqual([]);
    }, { auto: true }],
});

async function resetFixture(context, options) {
    const response = await context.request.post(`${uiURL}/__browser__/reset`, {
        headers: fixtureHeaders, data: options,
    });
    expect(response.status()).toBe(200);
}

async function fixtureState(context) {
    const response = await context.request.get(`${uiURL}/__browser__/state`, { headers: fixtureHeaders });
    expect(response.status()).toBe(200);
    return response.json();
}

async function openChat(page) {
    await page.goto(uiURL);
    await expect(page.locator('#connectionStatus')).toHaveClass(/connected/);
    await expect(page.locator('#messageInput')).toBeEnabled();
}

async function sendChat(page, message, keyboard = false) {
    await page.locator('#messageInput').fill(message);
    const response = page.waitForResponse(candidate => candidate.url() === `${uiURL}/chat`);
    if (keyboard) await page.locator('#messageInput').press('Enter');
    else await page.locator('#sendButton').click();
    expect((await response).status()).toBe(200);
    await expect(page.locator('.user-message .message-text').last()).toHaveText(message);
    await expect(page.locator('.bot-message .message-text').last()).toHaveText(reply);
    await expect(page.locator('.feedback-buttons').last()).toBeVisible();
    await expect(page.locator('#typingIndicator')).toHaveCount(0);
    await expect(page.locator('#messageInput')).toBeEnabled();
}

async function login(page, username = 'browser-admin') {
    await page.goto(`${adminURL}/login`);
    await page.locator('#username').fill(username);
    await page.locator('#password').fill(password);
    await page.locator('#submitButton').click();
    await expect(page).toHaveURL(`${adminURL}/`);
    await expect(page.locator('#logoutButton')).toBeVisible();
}

function preflightReport(kind) {
    return { kind, filename: `upload.${kind}`, valid: true, can_publish: true, replace: false,
        counts: { records: kind === 'json' ? 4 : null, pages: kind === 'pdf' ? 3 : null,
            duplicates_in_file: 1, duplicates_existing: 2, expected_chunks: 3, inserts: 1, updates: 0, skipped: 3 },
        cost: { embedding_inputs: 3, embedding_batches: 1, input_characters: 40, batch_size: 8 },
        samples: kind === 'json' ? [{ record: 1, question: '<img src=x onerror="window.sampleExecuted=true">', answer: 'sample answer' }]
            : [{ page: 3, text: '<script>window.sampleExecuted=true</script> extracted sample' }],
        issues: [], issues_truncated: false };
}

async function mockPreflight(page, kind, report = preflightReport(kind)) {
    const prefix = kind === 'json' ? '/admin/api/upload' : '/admin/api/pdf/upload';
    await page.route(`${adminURL}${prefix}/preflight`, route => {
        expect(route.request().headers()['x-csrf-token']).toBeTruthy();
        return route.fulfill({ json: report });
    });
}

test('controlled imports preview every enabled extension with safe provenance and invalidate changed options', async ({ page }) => {
    await login(page);
    await page.goto(`${adminURL}/admin/admin_import.html`);
    const sample = '<script>window.sampleExecuted=true</script>';
    let publications = 0;
    await page.route(`${adminURL}/admin/api/import/jobs`, route => {
        publications++;
        return route.fulfill({ status: 500, json: { error: 'Unexpected publication' } });
    });
    await page.route(`${adminURL}/admin/api/import/preflight`, route => {
        expect(route.request().headers()['x-csrf-token']).toBeTruthy();
        return route.fulfill({ json: { ...preflightReport('import'),
            source_metadata: { filename: 'source', sha256: 'a'.repeat(64) },
            samples: [{ text: sample, source_metadata: { location: { line_start: 1 } } }] } });
    });
    for (const extension of ['txt', 'md', 'csv', 'docx']) {
        await page.locator('#sourceFile').setInputFiles({ name: `guide.${extension}`, buffer: Buffer.from('content') });
        await expect(page.locator('#publishButton')).toBeDisabled();
        await page.locator('#previewButton').click();
        await expect(page.locator('#publishButton')).toBeEnabled();
        await expect(page.locator('#preview')).toContainText('line_start');
        await expect(page.locator('#preview')).toContainText(sample);
        await expect(page.locator('#preview script')).toHaveCount(0);
        expect(await page.evaluate(() => window.sampleExecuted)).toBeUndefined();
        await page.locator('#replaceExisting').setChecked(!await page.locator('#replaceExisting').isChecked());
        await expect(page.locator('#publishButton')).toBeDisabled();
        await expect(page.locator('#preview')).toBeEmpty();
    }
    expect(publications).toBe(0);
});

test('controlled import background job can be canceled and retried with saved provenance', async ({ page }) => {
    await login(page);
    await page.goto(`${adminURL}/admin/admin_import.html`);
    const jobId = 'a'.repeat(32);
    let status = 'running';
    await page.route(`${adminURL}/admin/api/import/preflight`, route => route.fulfill({ json: preflightReport('import') }));
    await page.route(`${adminURL}/admin/api/import/jobs`, route => {
        expect(route.request().method()).toBe('POST');
        expect(route.request().headers()['x-csrf-token']).toBeTruthy();
        return route.fulfill({ status: 202, json: { job_id: jobId, kind: 'import', status: 'queued' } });
    });
    await page.route(`${adminURL}/admin/api/import/jobs/${jobId}`, route => {
        if (route.request().method() === 'DELETE') {
            expect(route.request().headers()['x-csrf-token']).toBeTruthy();
            status = 'canceled';
        }
        return route.fulfill({ json: { job_id: jobId, kind: 'import', filename: 'guide.txt', status,
            retryable: status === 'canceled', result: status === 'completed' ? { source_metadata: { filename: 'guide.txt', format: 'text' } } : null } });
    });
    await page.route(`${adminURL}/admin/api/import/jobs/${jobId}/retry`, route => {
        expect(route.request().headers()['x-csrf-token']).toBeTruthy();
        status = 'completed';
        return route.fulfill({ status: 202, json: { job_id: jobId, status: 'queued' } });
    });
    await page.locator('#sourceFile').setInputFiles({ name: 'guide.txt', buffer: Buffer.from('content') });
    await page.locator('#previewButton').click();
    await expect(page.locator('#publishButton')).toBeEnabled();
    await page.locator('#publishButton').click();
    await expect(page.locator('#sourceFile')).toBeDisabled();
    await expect(page.locator('#status')).toContainText('running');
    await page.locator('#cancelButton').click();
    await expect(page.locator('#sourceFile')).toBeEnabled();
    await expect(page.locator('#retryButton')).toBeEnabled();
    await page.locator('#retryButton').click();
    await expect(page.locator('#status')).toContainText('completed');
    await expect(page.locator('#result')).toContainText('source_metadata');
    await expect(page.locator('#retryButton')).toBeDisabled();
});

for (const kind of ['json', 'pdf']) {
    test(`preflight ${kind} preview is read-only, safe to render and invalidated by changed options or file`, async ({ page }) => {
        await login(page);
        await mockPreflight(page, kind);
        let publications = 0;
        page.on('request', request => {
            if (/\/upload(?:\/jobs)?$/.test(new URL(request.url()).pathname) && request.method() === 'POST') publications++;
        });
        if (kind === 'pdf') await page.addInitScript(() => { URL.createObjectURL = () => 'about:blank'; });
        await page.goto(`${adminURL}/admin/${kind === 'json' ? 'admin' : 'admin_pdf'}.html`);
        await page.locator('#fileInput').setInputFiles({ name: `upload.${kind}`, buffer: Buffer.from('original content'),
            mimeType: kind === 'json' ? 'application/json' : 'application/pdf' });
        await expect(page.locator('#uploadBtn')).toBeDisabled();
        await page.locator('#preflightBtn').click();
        await expect(page.locator('#uploadBtn')).toBeEnabled();
        await expect(page.locator('#preflightReport')).toContainText('sample');
        await expect(page.locator('#preflightReport')).toContainText('40');
        await expect(page.locator('#preflightReport img, #preflightReport script')).toHaveCount(0);
        expect(await page.evaluate(() => window.sampleExecuted)).toBeUndefined();
        await page.locator('#replaceDupes').check();
        await expect(page.locator('#uploadBtn')).toBeDisabled();
        await expect(page.locator('#preflightReport')).toHaveCount(0);
        await page.locator('#preflightBtn').click();
        await expect(page.locator('#uploadBtn')).toBeEnabled();
        await page.locator('#fileInput').setInputFiles({ name: `different.${kind}`, buffer: Buffer.from('different content') });
        await expect(page.locator('#uploadBtn')).toBeDisabled();
        await expect(page.locator('#preflightReport')).toHaveCount(0);
        await page.locator('#clearBtn').click();
        await expect(page.locator('#preflightBtn')).toBeDisabled();
        expect(publications).toBe(0);
    });

    test(`preflight ${kind} validation shows record or page errors and prevents publication`, async ({ page }) => {
        await login(page);
        const report = { ...preflightReport(kind), valid: false, can_publish: false, counts: null, cost: null,
            issues: [{ severity: 'error', message: 'Fix this source value.', ...(kind === 'json' ? { record: 7, field: 'answer' } : { page: 7 }) }] };
        await mockPreflight(page, kind, report);
        if (kind === 'pdf') await page.addInitScript(() => { URL.createObjectURL = () => 'about:blank'; });
        await page.goto(`${adminURL}/admin/${kind === 'json' ? 'admin' : 'admin_pdf'}.html`);
        await page.locator('#fileInput').setInputFiles({ name: `invalid.${kind}`, buffer: Buffer.from('invalid content') });
        await page.locator('#preflightBtn').click();
        await expect(page.locator('#preflightReport')).toContainText('Fix this source value.');
        await expect(page.locator('#preflightReport')).toContainText('7');
        if (kind === 'json') await expect(page.locator('#preflightReport')).toContainText('answer');
        await expect(page.locator('#uploadBtn')).toBeDisabled();
    });
}

for (const confidence of [98, 54]) {
    test(`OCR preflight exposes page confidence ${confidence} and blocks uncertain extraction`, async ({ page }) => {
        await login(page);
        const uncertain = confidence < 75;
        const report = { ...preflightReport('pdf'), valid: !uncertain, can_publish: !uncertain,
            extraction: { ocr_enabled: true, minimum_confidence: 75, minimum_word_confidence: 50,
                pages: [{ page: 4, method: 'ocr', confidence, min_confidence: confidence,
                    needs_review: uncertain, text_sample: '<img src=x onerror="window.ocrExecuted=true"> نمونه فارسی برای بازبینی' }] },
            issues: uncertain ? [{ severity: 'error', page: 4, message: 'Review and re-scan page 4.' }] : [] };
        await mockPreflight(page, 'pdf', report);
        await page.addInitScript(() => { URL.createObjectURL = () => 'about:blank'; });
        await page.goto(`${adminURL}/admin/admin_pdf.html`);
        await page.locator('#fileInput').setInputFiles({ name: 'scan.pdf', buffer: Buffer.from('scan fixture') });
        await page.locator('#preflightBtn').click();
        await expect(page.locator('#preflightReport')).toContainText('صفحه 4');
        await expect(page.locator('#preflightReport')).toContainText(`${confidence}٪`);
        await expect(page.locator('#preflightReport')).toContainText('نمونه فارسی برای بازبینی');
        await expect(page.locator('#preflightReport img')).toHaveCount(0);
        expect(await page.evaluate(() => window.ocrExecuted)).toBeUndefined();
        if (uncertain) {
            await expect(page.locator('#uploadBtn')).toBeDisabled();
            await expect(page.locator('#preflightReport')).toContainText('نیازمند بازبینی');
        } else await expect(page.locator('#uploadBtn')).toBeEnabled();
    });
}

test('preflight response for a previous selection cannot enable publication', async ({ page }) => {
    await login(page);
    let finishPreview;
    let started = false;
    await page.route(`${adminURL}/admin/api/upload/preflight`, async route => {
        started = true;
        await new Promise(resolve => { finishPreview = resolve; });
        await route.fulfill({ json: preflightReport('json') });
    });
    await page.goto(`${adminURL}/admin/admin.html`);
    await page.locator('#fileInput').setInputFiles({ name: 'first.json', buffer: Buffer.from('[]') });
    await page.locator('#preflightBtn').click();
    await expect.poll(() => started).toBe(true);
    await page.locator('#fileInput').setInputFiles({ name: 'second.json', buffer: Buffer.from('[]') });
    const response = page.waitForResponse(`${adminURL}/admin/api/upload/preflight`);
    finishPreview();
    await response;
    await expect(page.locator('#uploadBtn')).toBeDisabled();
    await expect(page.locator('#preflightReport')).toHaveCount(0);
});

test('preflight controls stay disabled while publication submission is pending', async ({ page }) => {
    await login(page);
    await mockPreflight(page, 'json');
    let finishSubmission;
    let submitted = false;
    await page.route(`${adminURL}/admin/api/upload/jobs`, async route => {
        submitted = true;
        await new Promise(resolve => { finishSubmission = resolve; });
        await route.fulfill({ status: 503, json: { code: 'service_unavailable', error: 'Queue is busy.' } });
    });
    await page.goto(`${adminURL}/admin/admin.html`);
    await page.locator('#fileInput').setInputFiles({ name: 'first.json', buffer: Buffer.from('[]') });
    await page.locator('#preflightBtn').click();
    await expect(page.locator('#uploadBtn')).toBeEnabled();
    await page.locator('#uploadBtn').click();
    await expect.poll(() => submitted).toBe(true);
    await page.locator('#replaceDupes').check();
    await page.locator('#fileInput').setInputFiles({ name: 'second.json', buffer: Buffer.from('[]') });
    await expect(page.locator('#preflightBtn')).toBeDisabled();
    await expect(page.locator('#uploadBtn')).toBeDisabled();
    finishSubmission();
    await expect(page.locator('#result')).toContainText('Queue is busy.');
    await expect(page.locator('#preflightBtn')).toBeEnabled();
    await expect(page.locator('#uploadBtn')).toBeDisabled();
});

for (const [kind, failed] of [['json', false], ['pdf', false], ['json', true]]) {
    test(`background ${kind} upload ${failed ? 'failure' : 'completion'} restores controls`, async ({ page }) => {
        await login(page);
        const prefix = kind === 'json' ? '/admin/api/upload/jobs' : '/admin/api/pdf/upload/jobs';
        await mockPreflight(page, kind);
        const jobId = 'a'.repeat(32);
        let polls = 0;
        await page.route(`${adminURL}${prefix}**`, async route => {
            if (route.request().method() === 'POST') {
                expect(route.request().headers()['x-csrf-token']).toBeTruthy();
                return route.fulfill({ status: 202, headers: { Location: `${prefix}/${jobId}` },
                    json: { job_id: jobId, kind, status: 'queued' } });
            }
            polls += 1;
            const status = polls === 1 ? 'running' : failed ? 'failed' : 'completed';
            return route.fulfill({ json: { job_id: jobId, kind, status,
                error: failed ? { code: 'invalid_request', error: 'The request is invalid.' } : null,
                result: kind === 'json'
                    ? { received: 1, inserted: 1, updated: 0, skipped: 0, embedded: 1, errors: [], duration_sec: 0.01 }
                    : { filename: 'upload.pdf', page_count: 1, chunk_count: 1, embedded_chunks: 1, embedding_dim: 3, duration_sec: 0.01 },
            } });
        });
        if (kind === 'pdf') {
            await page.addInitScript(() => { URL.createObjectURL = () => 'about:blank'; });
        }
        await page.goto(`${adminURL}/admin/${kind === 'json' ? 'admin' : 'admin_pdf'}.html`);
        await page.locator('#fileInput').setInputFiles({
            name: `upload.${kind}`, mimeType: kind === 'json' ? 'application/json' : 'application/pdf',
            buffer: Buffer.from(kind === 'json' ? '[{"question":"question","answer":"answer"}]' : '%PDF-1.4\n%%EOF'),
        });
        await page.locator('#preflightBtn').click();
        await expect(page.locator('#uploadBtn')).toBeEnabled();
        await page.locator('#uploadBtn').click();
        await expect(page.locator('#uploadBtn')).toBeDisabled();
        await expect(page.locator('#ingestionProgress')).toContainText(jobId);
        await expect(page.locator('#result')).toHaveClass(failed ? /err/ : /ok/);
        await expect(page.locator('#uploadBtn')).toBeEnabled();
        if (failed) await expect(page.locator('#result')).toContainText('The request is invalid.');
        expect(polls).toBe(2);
    });
}

for (const kind of ['json', 'pdf']) {
    test(`background ${kind} progress, running cancellation, reload and checkpoint retry`, async ({ page }) => {
        await login(page);
        const prefix = kind === 'json' ? '/admin/api/upload/jobs' : '/admin/api/pdf/upload/jobs';
        await mockPreflight(page, kind);
        const jobId = 'b'.repeat(32);
        let status = 'running';
        let retried = false;
        let stage = 0;
        let submissions = 0;
        let retries = 0;
        const stages = ['extraction', 'chunking', 'embedding', 'publishing'];
        const seen = [];
        await page.route(`${adminURL}${prefix}**`, async route => {
            const request = route.request();
            if (request.method() === 'POST') {
                expect(request.headers()['x-csrf-token']).toBeTruthy();
                if (request.url().endsWith('/retry')) {
                    retries++;
                    retried = true;
                } else submissions++;
                status = 'running';
                return route.fulfill({ status: 202, headers: { Location: `${prefix}/${jobId}` },
                    json: { job_id: jobId, kind, status: 'queued' } });
            }
            if (request.method() === 'DELETE') {
                expect(request.headers()['x-csrf-token']).toBeTruthy();
                status = 'canceled';
            }
            const current = retried ? stages[Math.min(stage++, 3)] : 'embedding';
            if (retried) seen.push(current);
            if (stage > 4) status = 'completed';
            return route.fulfill({ json: { job_id: jobId, kind, status, retryable: status === 'canceled',
                checkpoint: 'upload', progress: { stage: current, source: kind, completed: 1, total: 2 },
                result: kind === 'json'
                    ? { received: 1, inserted: 1, updated: 0, skipped: 0, embedded: 1, errors: [], duration_sec: 1 }
                    : { filename: 'upload.pdf', page_count: 1, chunk_count: 1, embedded_chunks: 1, embedding_dim: 3, duration_sec: 1 },
            } });
        });
        if (kind === 'pdf') await page.addInitScript(() => { URL.createObjectURL = () => 'about:blank'; });
        await page.goto(`${adminURL}/admin/${kind === 'json' ? 'admin' : 'admin_pdf'}.html`);
        await page.locator('#fileInput').setInputFiles({ name: `upload.${kind}`,
            mimeType: kind === 'json' ? 'application/json' : 'application/pdf',
            buffer: Buffer.from(kind === 'json' ? '[{"question":"q","answer":"a"}]' : '%PDF-1.4\n%%EOF'),
        });
        await page.locator('#preflightBtn').click();
        await expect(page.locator('#uploadBtn')).toBeEnabled();
        await page.locator('#uploadBtn').click();
        await expect(page.locator('#ingestionProgress progress')).toHaveAttribute('value', '1');
        await expect(page.locator('#ingestionProgress')).toContainText('1/2');
        await page.locator('#ingestionProgress').getByRole('button', { name: 'لغو', exact: true }).click();
        await expect(page.locator('#ingestionProgress')).toContainText('لغو شد');
        await page.reload();
        await expect(page.locator('#ingestionProgress')).toContainText(jobId);
        await page.locator('#ingestionProgress').getByRole('button', { name: 'تلاش دوباره' }).click();
        await expect(page.locator('#ingestionProgress')).toContainText('تکمیل شد', { timeout: 15000 });
        expect(submissions).toBe(1);
        expect(retries).toBe(1);
        expect(new Set(seen)).toEqual(new Set(stages));
    });
}

for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
    test(`RTL chat, keyboard and recommendations at ${viewport.width}px`, async ({ page, context }) => {
        await page.setViewportSize(viewport);
        await installAudio(context);
        await openChat(page);
        await expect(page.locator('html')).toHaveAttribute('lang', 'fa');
        await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
        await expect(page.locator('#messageInput')).toHaveCSS('direction', 'rtl');
        const message = '\u0633\u0644\u0627\u0645 <b>\u0646\u0645\u0648\u0646\u0647</b> \u06f1\u06f2\u06f3';
        await sendChat(page, message, true);
        await expect(page.locator('.user-message .message-text').last()).toHaveCSS('direction', 'rtl');
        await expect(page.locator('.bot-message .message-text').last()).toHaveCSS('direction', 'rtl');
        await expect(page.locator('.user-message b')).toHaveCount(0);
        const userBounds = await page.locator('.user-message').last().boundingBox();
        const botBounds = await page.locator('.bot-message').last().boundingBox();
        expect(userBounds.x + userBounds.width).toBeGreaterThan(botBounds.x + botBounds.width);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await expect(page.locator('.recommended-question')).toHaveText(followUp);
        await page.locator('.recommended-question').click();
        await expect(page.locator('#messageInput')).toHaveValue(followUp);
        await sendChat(page, followUp);
        expect((await fixtureState(context)).messages).toHaveLength(2);
    });
}

test('quick questions and pending chat restore usable controls', async ({ page, context }) => {
    await installAudio(context);
    await openChat(page);
    await page.locator('.quick-question-btn').first().click();
    const question = await page.locator('#messageInput').inputValue();
    expect(question.length).toBeGreaterThan(0);
    expect((await fixtureState(context)).messages).toHaveLength(0);
    let releaseRequest;
    const gate = new Promise(resolve => { releaseRequest = resolve; });
    await page.route(`${uiURL}/chat`, async route => { await gate; await route.continue(); });
    try {
        await page.locator('#sendButton').click();
        await expect(page.locator('#typingIndicator')).toBeVisible();
        await expect(page.locator('#sendButton')).toBeDisabled();
        await expect(page.locator('#messageInput')).toBeDisabled();
        await expect(page.locator('#voiceButton')).toBeDisabled();
    } finally {
        releaseRequest();
    }
    await expect(page.locator('.feedback-buttons')).toBeVisible();
    await expect(page.locator('#typingIndicator')).toHaveCount(0);
    await expect(page.locator('#messageInput')).toBeEnabled();
    await expect(page.locator('#voiceButton')).toBeEnabled();
    expect((await fixtureState(context)).messages[0].user_message).toBe(question);
});

test('feedback failure leaves rating unselected and retry persists the choice', async ({ page, context }) => {
    await resetFixture(context, { feedback_failure: true });
    await installAudio(context);
    await openChat(page);
    await sendChat(page, '\u0628\u0627\u0632\u062e\u0648\u0631\u062f');
    const like = page.locator('.like-btn');
    const dislike = page.locator('.dislike-btn');
    const status = page.locator('.feedback-status');
    await like.click();
    await expect(status).toHaveClass(/error/);
    await expect(status).toHaveText('\u062b\u0628\u062a \u0628\u0627\u0632\u062e\u0648\u0631\u062f \u0646\u0627\u0645\u0648\u0641\u0642 \u0628\u0648\u062f. \u062f\u0648\u0628\u0627\u0631\u0647 \u062a\u0644\u0627\u0634 \u06a9\u0646\u06cc\u062f.');
    await expect(like).toBeEnabled();
    await expect(dislike).toBeEnabled();
    await expect(like).toHaveAttribute('aria-pressed', 'false');
    expect((await fixtureState(context)).messages[0].feedback).toBeNull();
    await like.click();
    await expect(like).toHaveAttribute('aria-pressed', 'true');
    await expect(status).not.toHaveClass(/error/);
    expect((await fixtureState(context)).messages[0].feedback).toBe('like');
    await dislike.click();
    await expect(dislike).toHaveAttribute('aria-pressed', 'true');
    await expect(like).toHaveAttribute('aria-pressed', 'false');
    const state = await fixtureState(context);
    expect(state.messages[0].feedback).toBe('dislike');
    expect(state.feedback_attempts).toBe(3);
});

test('login rejects wrong credentials, supports password visibility and revokes logout', async ({ page, context }) => {
    await page.goto(`${adminURL}/`);
    await expect(page).toHaveURL(`${adminURL}/login`);
    await page.locator('#username').fill('browser-admin');
    await page.locator('#password').fill('wrong-password');
    await page.locator('#passwordToggle').click();
    await expect(page.locator('#password')).toHaveAttribute('type', 'text');
    await page.locator('#passwordToggle').click();
    await expect(page.locator('#password')).toHaveAttribute('type', 'password');
    const rejected = page.waitForResponse(response => response.url() === `${adminURL}/login` && response.request().method() === 'POST');
    await page.locator('#submitButton').click();
    expect((await rejected).status()).toBe(401);
    await expect(page.locator('#loginError')).toHaveText('Invalid username or password.');
    await expect(page.locator('#submitButton')).toBeEnabled();
    expect((await fixtureState(context)).sessions).toBe(0);
    await login(page);
    expect((await fixtureState(context)).sessions).toBe(1);
    const cookies = await context.cookies(adminURL);
    await page.locator('#logoutButton').click();
    await expect(page).toHaveURL(`${adminURL}/login`);
    expect((await fixtureState(context)).sessions).toBe(0);
    await context.addCookies(cookies);
    expect((await context.request.get(`${adminURL}/admin/api/session`)).status()).toBe(401);
    await page.goto(`${adminURL}/`);
    await expect(page).toHaveURL(`${adminURL}/login`);
});

for (const [username, allowed] of [
    ['browser-admin', ['admin', 'admin_pdf', 'admin_import', 'adminmodel', 'adminchat', 'adminlogger', 'controller', 'admin-user-control']],
    ['browser-pdf', ['admin_pdf']],
    ['browser-empty', []],
]) {
    test(`permission-based navigation for ${username}`, async ({ page, context }) => {
        await login(page, username);
        const visible = page.locator('.menu-item[data-page]:visible');
        await expect(visible).toHaveCount(allowed.length);
        expect(await visible.evaluateAll(items => items.map(item => item.dataset.page)))
            .toEqual(allowed.map(stem => `admin/${stem}.html`));
        if (!allowed.length) {
            await expect(page.locator('#pageFrame')).toHaveAttribute('src', 'about:blank');
        } else {
            const initial = allowed[0];
            await expect(page.frameLocator('#pageFrame').locator('.theme-toggle')).toBeVisible();
            await expect(page.locator('.menu-item.active')).toHaveAttribute('data-page', `admin/${initial}.html`);
            if (username === 'browser-admin') {
                await page.locator('[data-page="admin/admin_pdf.html"]').click();
                await expect(page.frameLocator('#pageFrame').locator('#fileInput')).toBeAttached();
                await expect(page.locator('.menu-item.active')).toHaveAttribute('data-page', 'admin/admin_pdf.html');
            }
        }
        if (username !== 'browser-admin') {
            expect((await context.request.get(`${adminURL}/admin/admin-user-control.html`)).status()).toBe(403);
            expect((await context.request.get(`${adminURL}/admin/api/admin-users`)).status()).toBe(403);
        }
    });
}

test('chat theme persists after reload and synchronizes between same-origin tabs', async ({ page, context }) => {
    await openChat(page);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    const darkColor = await page.locator('body').evaluate(element => getComputedStyle(element).color);
    await page.locator('.theme-toggle').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    expect(await page.locator('body').evaluate(element => getComputedStyle(element).color)).not.toBe(darkColor);
    await expect(page.locator('.theme-toggle')).toHaveAttribute('aria-pressed', 'true');
    expect(await page.evaluate(() => localStorage.getItem('liquid-theme'))).toBe('light');
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    const sibling = await context.newPage();
    await sibling.goto(uiURL);
    await expect(sibling.locator('html')).toHaveAttribute('data-theme', 'light');
    await sibling.locator('.theme-toggle').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await expect(page.locator('.theme-toggle')).toHaveAttribute('aria-pressed', 'false');
});

test('admin theme persists through login, iframe changes and reload without crossing origins', async ({ page, context }) => {
    await openChat(page);
    await page.locator('.theme-toggle').click();
    await page.goto(`${adminURL}/login`);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.locator('.theme-toggle').click();
    await login(page);
    const embedded = page.frameLocator('#pageFrame');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await expect(embedded.locator('html')).toHaveAttribute('data-theme', 'light');
    await embedded.locator('.theme-toggle').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.locator('[data-page="admin/admin_pdf.html"]').click();
    await expect(embedded.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await expect(embedded.locator('html')).toHaveAttribute('data-theme', 'dark');
    const userTab = await context.newPage();
    await userTab.goto(uiURL);
    await expect(userTab.locator('html')).toHaveAttribute('data-theme', 'light');
});

for (const [mode, detail] of [
    ['denied', '\u0627\u062c\u0627\u0632\u0647 \u062f\u0633\u062a\u0631\u0633\u06cc'],
    ['missing', '\u0645\u06cc\u06a9\u0631\u0648\u0641\u0648\u0646\u06cc \u067e\u06cc\u062f\u0627 \u0646\u0634\u062f'],
    ['worklet-error', 'Mock worklet failure'],
    ['socket-error', '\u0627\u062a\u0635\u0627\u0644 \u0632\u0646\u062f\u0647'],
]) {
    test(`microphone ${mode} preserves the draft and restores text chat`, async ({ page, context }) => {
        await installAudio(context, mode);
        await openChat(page);
        const draft = '\u067e\u06cc\u0634\u200c\u0646\u0648\u06cc\u0633';
        await page.locator('#messageInput').fill(draft);
        await expect(page.locator('#voiceButton')).toBeEnabled();
        await page.locator('#voiceButton').click();
        await expect(page.locator('#voiceStatus')).toHaveClass(/error/);
        await expect(page.locator('#voiceStatus')).toContainText(detail);
        await expect(page.locator('#messageInput')).toHaveValue(draft);
        await expect(page.locator('#messageInput')).toBeEnabled();
        await expect(page.locator('#sendButton')).toBeEnabled();
        await expect(page.locator('#voiceButton')).toHaveAttribute('aria-pressed', 'false');
        await expect(page.locator('#voiceButton')).toBeEnabled();
        expect(await page.evaluate(() => window.__audioMock.closed)).toBe(1);
        expect(await page.evaluate(() => window.__audioMock.stopped)).toBe(mode === 'denied' || mode === 'missing' ? 0 : 1);
        await sendChat(page, draft);
    });
}

test('unavailable voice backend disables only the microphone', async ({ page, context }) => {
    await resetFixture(context, { voice_unavailable: true });
    await installAudio(context);
    await openChat(page);
    await expect(page.locator('#voiceStatus')).toHaveClass(/error/);
    await expect(page.locator('#voiceButton')).toBeDisabled();
    await sendChat(page, '\u0645\u062a\u0646 \u0628\u062f\u0648\u0646 \u0645\u06cc\u06a9\u0631\u0648\u0641\u0648\u0646');
    expect(await page.evaluate(() => window.__audioMock.requests)).toBe(0);
});

test('unsupported audio APIs leave text chat available', async ({ page, context }) => {
    await installAudio(context, 'unsupported');
    await openChat(page);
    await expect(page.locator('#voiceStatus')).toHaveClass(/error/);
    await expect(page.locator('#voiceButton')).toBeDisabled();
    await sendChat(page, '\u0645\u0631\u0648\u0631\u06af\u0631 \u0628\u062f\u0648\u0646 \u0635\u0648\u062a');
    expect(await page.evaluate(() => window.__audioMock.requests)).toBe(0);
});

test('microphone disconnect retains partial transcript and releases capture', async ({ page, context }) => {
    await installAudio(context);
    await openChat(page);
    await page.locator('#messageInput').fill('draft');
    await expect(page.locator('#voiceButton')).toBeEnabled();
    await page.locator('#voiceButton').click();
    await expect(page.locator('#voiceButton')).toHaveAttribute('aria-pressed', 'true');
    await page.evaluate(() => {
        window.__audioMock.sockets[0].message({ type: 'transcript', segment_id: 1, text: 'partial', is_final: false });
        window.__audioMock.disconnectMicrophone();
    });
    await expect(page.locator('#voiceStatus')).toHaveClass(/error/);
    await expect(page.locator('#messageInput')).toHaveValue('draft partial');
    await expect(page.locator('#messageInput')).toBeEnabled();
    expect((await fixtureState(context)).messages).toHaveLength(0);
    expect(await page.evaluate(() => window.__audioMock.stopped)).toBe(1);
    expect(await page.evaluate(() => window.__audioMock.closed)).toBe(1);
    await sendChat(page, 'draft partial');
});

test('mock recording stop sends the final transcript exactly once and frees audio', async ({ page, context }) => {
    await installAudio(context);
    await openChat(page);
    await expect(page.locator('#voiceButton')).toBeEnabled();
    await page.locator('#voiceButton').click();
    await expect(page.locator('#voiceButton')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#messageInput')).toBeDisabled();
    await page.locator('#voiceButton').click();
    await expect(page.locator('.feedback-buttons')).toBeVisible();
    await expect(page.locator('#voiceButton')).toHaveAttribute('aria-pressed', 'false');
    const state = await fixtureState(context);
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].user_message).toBe('\u06af\u0641\u062a\u0627\u0631 \u0622\u0632\u0645\u0627\u06cc\u0634\u06cc');
    expect(await page.evaluate(() => window.__audioMock.stopped)).toBe(1);
    expect(await page.evaluate(() => window.__audioMock.closed)).toBe(1);
    await expect.poll(async () => (await fixtureState(context)).speech_requests).toEqual([reply]);
    await expect.poll(() => page.evaluate(() => window.__audioMock.played)).toBe(1);
});

test('speech then silence flushes capture, sends once and reads the answer locally', async ({ page, context }) => {
    await installAudio(context);
    await openChat(page);
    await expect(page.locator('#voiceButton')).toBeEnabled();
    await expect(page.locator('#voiceReplies')).toBeChecked();
    await page.locator('#voiceButton').click();
    await expect(page.locator('#voiceButton')).toHaveAttribute('aria-pressed', 'true');
    await page.evaluate(() => {
        window.__audioMock.sockets[0].message({type: 'transcript', segment_id: 1, text: 'partial', is_final: false});
    });
    await expect(page.locator('#messageInput')).toHaveValue('partial');
    await page.evaluate(() => {
        for (let i = 0; i < 3; i++) window.__audioMock.packet(0.1);
        for (let i = 0; i < 16; i++) window.__audioMock.packet(0);
    });
    await expect.poll(async () => (await fixtureState(context)).speech_requests).toEqual([reply]);
    const state = await fixtureState(context);
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].user_message).toBe('\u06af\u0641\u062a\u0627\u0631 \u0622\u0632\u0645\u0627\u06cc\u0634\u06cc');
    await expect.poll(() => page.evaluate(() => window.__audioMock.played)).toBe(1);
    expect(await page.evaluate(() => window.__audioMock.stopped)).toBe(1);
    expect(await page.evaluate(() => window.__audioMock.closed)).toBe(1);
});

test('short pauses and initial silence do not submit an unfinished recording', async ({ page, context }) => {
    await installAudio(context);
    await openChat(page);
    await expect(page.locator('#voiceButton')).toBeEnabled();
    await page.locator('#voiceButton').click();
    await expect(page.locator('#voiceButton')).toHaveAttribute('aria-pressed', 'true');
    await page.evaluate(() => {
        for (let i = 0; i < 30; i++) window.__audioMock.packet(0);
        for (let i = 0; i < 3; i++) window.__audioMock.packet(0.1);
        for (let i = 0; i < 10; i++) window.__audioMock.packet(0);
        window.__audioMock.packet(0.1);
    });
    await expect(page.locator('#voiceButton')).toHaveAttribute('aria-pressed', 'true');
    expect((await fixtureState(context)).messages).toHaveLength(0);
});

test('voice playback can be disabled while recording and text is still submitted', async ({ page, context }) => {
    await installAudio(context);
    await openChat(page);
    await expect(page.locator('#voiceButton')).toBeEnabled();
    await page.locator('#voiceReplies').uncheck();
    await page.locator('#voiceButton').click();
    await expect(page.locator('#voiceButton')).toHaveAttribute('aria-pressed', 'true');
    await page.locator('#voiceButton').click();
    await expect(page.locator('.feedback-buttons')).toBeVisible();
    expect((await fixtureState(context)).speech_requests).toEqual([]);
    expect(await page.evaluate(() => window.__audioMock.played)).toBe(0);
});

test('TTS failure keeps the chatbot answer and text input available', async ({ page, context }) => {
    await resetFixture(context, {speech_unavailable: true});
    await installAudio(context);
    await openChat(page);
    await expect(page.locator('#voiceButton')).toBeEnabled();
    await page.locator('#voiceButton').click();
    await expect(page.locator('#voiceButton')).toHaveAttribute('aria-pressed', 'true');
    await page.locator('#voiceButton').click();
    await expect(page.locator('.feedback-buttons')).toBeVisible();
    await expect(page.locator('#voiceStatus')).toHaveClass(/error/);
    await expect(page.locator('#messageInput')).toBeEnabled();
    await expect(page.locator('#voiceButton')).toBeEnabled();
    expect((await fixtureState(context)).messages).toHaveLength(1);
    expect(await page.evaluate(() => window.__audioMock.played)).toBe(0);
});

test('starting another recording stops the previous answer playback', async ({ page, context }) => {
    await installAudio(context, 'keep-playing');
    await openChat(page);
    await expect(page.locator('#voiceButton')).toBeEnabled();
    await page.locator('#voiceButton').click();
    await expect(page.locator('#voiceButton')).toHaveAttribute('aria-pressed', 'true');
    await page.locator('#voiceButton').click();
    await expect(page.locator('#voicePlayer')).toBeVisible();
    await expect(page.locator('#voiceStopButton')).toBeVisible();
    await page.locator('#voiceButton').click();
    await expect(page.locator('#voiceButton')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#voicePlayer')).toBeHidden();
    await expect(page.locator('#voiceStopButton')).toBeHidden();
    expect((await fixtureState(context)).messages).toHaveLength(1);
});

test('autoplay denial leaves an audio control for manual playback', async ({ page, context }) => {
    await installAudio(context, 'autoplay-denied');
    await openChat(page);
    await expect(page.locator('#voiceButton')).toBeEnabled();
    await page.locator('#voiceButton').click();
    await expect(page.locator('#voiceButton')).toHaveAttribute('aria-pressed', 'true');
    await page.locator('#voiceButton').click();
    await expect(page.locator('#voicePlayer')).toBeVisible();
    await expect(page.locator('#voiceStatus')).toContainText('دکمه پخش');
    await page.locator('#voicePlayer').evaluate(audio => audio.play());
    await expect(page.locator('#voiceStopButton')).toBeHidden();
    expect(await page.evaluate(() => window.__audioMock.played)).toBe(2);
});
