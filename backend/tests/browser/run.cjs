const path = require('node:path');
const entry = require.resolve(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
require(path.join(path.dirname(entry), 'cli.js'));
