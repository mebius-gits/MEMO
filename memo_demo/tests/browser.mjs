import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import assert from 'node:assert/strict';

mkdirSync('test-results', { recursive: true });
const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--enable-unsafe-swiftshader'],
});
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('http://localhost:5173', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#scene canvas');
  await page.waitForTimeout(1500);
  assert.equal(await page.locator('#webgl-fallback').isVisible(), false);
  await page.getByRole('button', { name: '連線設定' }).click();
  await page.locator('#api-url').fill(process.env.MEMO_TEST_API || 'http://localhost:8000');
  await page.locator('#connect-button').click();
  await page.waitForFunction(() => document.querySelector('#connection-label').textContent === '已連接後端');
  await page.locator('#simulation-toggle').click();
  await page.locator('#publish-simulation').click();
  await page.waitForFunction(() => document.querySelector('#mood-label').textContent === '溫柔陪伴');
  assert.equal(await page.locator('#temperature-value').textContent(), '26.0');
  assert.ok((await page.locator('#messages').textContent()).includes('後端儲存的近期讀值'));
  await page.locator('#simulation-toggle').click();
  await page.locator('#pause-motion').click();
  await page.screenshot({ path: 'test-results/desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(600);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: 'test-results/mobile.png', fullPage: true });
  await page.getByRole('button', { name: '連線設定' }).click();
  await page.locator('#api-url').fill('http://127.0.0.1:1');
  await page.locator('#connect-button').click();
  await page.waitForFunction(() => document.querySelector('#settings-error').textContent.includes('無法連接'));
  await page.locator('#disconnect').click();
  assert.equal(await page.locator('#temperature-value').textContent(), '—');
  assert.equal(await page.locator('#connection-label').textContent(), '尚未連接後端');
  assert.deepEqual(errors, []);
  console.log('PASS: 3D renderer, real backend connection, simulation persistence, response emotion, mobile layout, connection failure, disconnect');
} finally { await browser.close(); }
