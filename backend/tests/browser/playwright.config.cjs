const path = require('node:path');
const { defineConfig } = require(process.env.PLAYWRIGHT_MODULE || '@playwright/test');

for (const name of ['BROWSER_UI_URL', 'BROWSER_ADMIN_URL']) {
    if (!/^http:\/\/127\.0\.0\.1:[1-9][0-9]*$/.test(process.env[name] || '')) {
        throw new Error('Use python -B backend/tests/browser_smoke.py; only isolated loopback servers are accepted.');
    }
}
if (!/^[0-9a-f]{64}$/.test(process.env.BROWSER_SMOKE_TOKEN || '')) {
    throw new Error('A runner-issued browser fixture token is required.');
}

module.exports = defineConfig({
    testDir: __dirname,
    testMatch: 'journeys.spec.cjs',
    fullyParallel: false,
    workers: 1,
    forbidOnly: true,
    retries: 0,
    metadata: { uiOrigin: process.env.BROWSER_UI_URL, adminOrigin: process.env.BROWSER_ADMIN_URL },
    repeatEach: Number(process.env.BROWSER_REPEAT || 1),
    timeout: 25000,
    expect: { timeout: 6000 },
    outputDir: path.join(process.env.BROWSER_ARTIFACT_DIR, 'results'),
    reporter: [
        ['list'],
        ['json', { outputFile: path.join(process.env.BROWSER_ARTIFACT_DIR, 'report.json') }],
    ],
    use: {
        baseURL: process.env.BROWSER_UI_URL,
        browserName: 'chromium',
        channel: process.env.BROWSER_CHANNEL === 'chromium' ? undefined : process.env.BROWSER_CHANNEL || 'chrome',
        headless: true,
        viewport: { width: 1280, height: 900 },
        serviceWorkers: 'block',
        screenshot: 'only-on-failure',
        trace: 'retain-on-failure',
        launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] },
    },
});
