const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

async function main() {
    const browser = await chromium.launch({
        channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome',
        headless: true,
        args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'],
    });
    try {
        const context = await browser.newContext({ permissions: ['microphone'] });
        const violations = [];
        const scriptErrors = [];
        await context.exposeBinding('recordPolicyViolation', ({ frame }, violation) => {
            violations.push({ url: frame.url(), ...violation });
        });
        await context.addInitScript(() => {
            document.addEventListener('securitypolicyviolation', event => {
                void window.recordPolicyViolation({
                    directive: event.effectiveDirective,
                    disposition: event.disposition,
                    blockedURI: event.blockedURI,
                });
            });
        });
        context.on('page', page => page.on('pageerror', error => scriptErrors.push(error.message)));
        const page = await context.newPage();
        const response = await page.goto('http://localhost:5000/');
        assert.equal(response.headers()['x-content-type-options'], 'nosniff');
        assert.equal(response.headers()['content-security-policy'], "frame-ancestors 'none'");
        await page.waitForFunction(() => window.__CTL_FEATURES__?.voice === true);
        await page.locator('#privacyNotice summary').click();
        await page.locator('#privacyNotice .privacy-details').waitFor({ state: 'visible' });
        assert.match(await page.locator('#playbackPrivacy').innerText(), /سرویس اینترنتی/);
        assert.equal(await page.locator('#voiceReplies').isChecked(), false);
        const speech = await page.evaluate(() => {
            const calls = [];
            window.SpeechSynthesisUtterance = class TestUtterance {
                constructor(text) { this.text = text; }
            };
            Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
                getVoices: () => [{ lang: 'fa-IR', localService: false }],
                cancel: () => calls.push('cancel'),
                speak: () => calls.push('speak'),
            } });
            speakVoiceReply('test reply');
            const beforeOptIn = [...calls];
            const checkbox = document.getElementById('voiceReplies');
            checkbox.checked = true;
            speakVoiceReply('test reply');
            checkbox.checked = false;
            checkbox.dispatchEvent(new Event('change'));
            return { beforeOptIn, afterOptIn: calls };
        });
        assert.deepEqual(speech.beforeOptIn, []);
        assert.deepEqual(speech.afterOptIn, ['cancel', 'speak', 'cancel']);
        await page.locator('#privacyNotice summary').click();
        const chatResponse = page.waitForResponse(response => response.url().endsWith('/chat') && response.status() === 200);
        await page.locator('.quick-question-btn').first().click();
        await page.locator('#sendButton').click();
        const chat = await (await chatResponse).json();
        assert.equal(chat.message_id, 1);
        assert.equal(typeof chat.reply, 'string');
        const feedback = await page.evaluate(async () => {
            const response = await fetch('/feedback', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message_id: 1, feedback: 'like' }),
            });
            return response.status;
        });
        assert.equal(feedback, 200);

        const audio = await page.evaluate(async () => {
            const microphone = await navigator.mediaDevices.getUserMedia({ audio: true });
            const context = new AudioContext();
            await context.audioWorklet.addModule('/voice/audio-worklet.js');
            const recorder = new AudioWorkletNode(context, 'pcm-recorder');
            const source = context.createMediaStreamSource(microphone);
            source.connect(recorder);
            recorder.connect(context.destination);
            source.disconnect();
            recorder.disconnect();
            microphone.getTracks().forEach(track => track.stop());
            await context.close();
            return 'worklet-loaded';
        });
        assert.equal(audio, 'worklet-loaded');
        const voice = await page.evaluate(() => new Promise((resolve, reject) => {
            const socket = new WebSocket('ws://localhost:5000/voice-stream');
            const received = [];
            const deadline = setTimeout(() => { socket.close(); reject(new Error('Voice timeout')); }, 10000);
            socket.onerror = () => { clearTimeout(deadline); reject(new Error('WebSocket failed')); };
            socket.onmessage = event => {
                const message = JSON.parse(event.data);
                received.push(message.type);
                if (message.type === 'ready') socket.send(JSON.stringify({ type: 'stop' }));
                if (message.type === 'done') {
                    clearTimeout(deadline);
                    socket.close();
                    resolve(received);
                }
            };
        }));
        assert.deepEqual(voice, ['ready', 'done']);

        await page.goto('http://localhost:8000/login');
        await page.locator('#username').fill('browser-test');
        await page.locator('#password').fill('browser-test-password');
        await Promise.all([page.waitForURL('http://localhost:8000/'), page.locator('#submitButton').click()]);
        const embedded = page.frameLocator('#pageFrame');
        await embedded.locator('#fileInput').waitFor({ state: 'attached' });
        assert.equal(await embedded.locator('body').evaluate(() => typeof window.adminFetch), 'function');
        const mutation = await embedded.locator('body').evaluate(async () => {
            const response = await window.adminFetch('/admin/api/models', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model_id: 'test' }),
            });
            return response.status;
        });
        assert.equal(mutation, 200);
        await page.locator('[data-page="admin/admin_pdf.html"]').click();
        await page.waitForFunction(() => {
            const frame = document.getElementById('pageFrame');
            return frame.contentWindow.location.pathname === '/admin/admin_pdf.html' &&
                typeof frame.contentWindow.handleFile === 'function';
        });
        await embedded.locator('#fileInput').setInputFiles({
            name: 'preview.pdf', mimeType: 'application/pdf',
            buffer: Buffer.from('%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF'),
        });
        const preview = embedded.locator('iframe');
        await preview.waitFor({ state: 'attached' });
        assert.match(await preview.getAttribute('src'), /^blob:http:\/\/localhost:8000\//);
        for (const stem of ['adminmodel', 'adminchat', 'adminlogger', 'controller', 'admin-user-control']) {
            await page.locator(`[data-page="admin/${stem}.html"]`).click();
            await page.waitForFunction(expected => {
                const frame = document.getElementById('pageFrame');
                return frame.contentWindow.location.pathname === `/admin/${expected}.html` &&
                    typeof frame.contentWindow.adminFetch === 'function';
            }, stem);
        }
        await Promise.all([page.waitForURL('http://localhost:8000/login'), page.locator('#logoutButton').click()]);
        assert.deepEqual(scriptErrors, []);
        assert.deepEqual(violations.filter(violation => violation.disposition === 'enforce'), []);
        assert(violations.some(violation => violation.directive === 'script-src-elem' && violation.blockedURI === 'inline'));
        assert(violations.some(violation => violation.directive === 'script-src-attr' && violation.blockedURI === 'inline'));
        const report = [...new Set(violations.map(({ directive, disposition, blockedURI }) =>
            `${disposition}: ${directive} (${blockedURI})`))];
        process.stdout.write(JSON.stringify({
            result: 'passed', flows: ['privacy-notice', 'playback-opt-in', 'chat', 'feedback', 'microphone', 'audio-worklet', 'voice-websocket',
                'login', 'admin-iframe', 'csrf-mutation', 'blob-pdf-preview', 'admin-pages', 'logout'],
            cspViolations: report,
        }, null, 2) + '\n');
        await context.close();
    } finally {
        await browser.close();
    }
}

main().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });
