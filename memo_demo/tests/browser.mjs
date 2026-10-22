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
  await page.waitForFunction(() => document.querySelector('#connection-label').textContent === '已連接後端');
  await page.locator('#tab-params').click();
  await page.locator('#publish-simulation').click();
  await page.waitForFunction(() => document.querySelector('#mood-label').textContent === '溫柔陪伴');
  assert.equal(await page.locator('#temperature-value').textContent(), '26.0');
  assert.ok((await page.locator('#messages').textContent()).includes('我有收到空間的新消息'));
  await page.locator('#tab-chat').click();
  await page.locator('#pause-motion').click();
  await page.screenshot({ path: 'test-results/desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(600);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: 'test-results/mobile.png', fullPage: true });
  assert.deepEqual(errors, []);
  console.log('PASS: 3D renderer, real backend connection, simulation persistence, response emotion, mobile layout');
} finally { await browser.close(); }
