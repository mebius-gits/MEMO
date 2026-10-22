import { chromium } from 'playwright';
import assert from 'node:assert/strict';

const api = process.env.TEST_API_URL || 'http://localhost:8100';
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true, args: ['--enable-unsafe-swiftshader', '--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows'] });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto('http://localhost:5173');
  await page.locator('#settings-button').click();
  await page.locator('#api-url').fill(api);
  await page.locator('#connect-button').click();
  await page.waitForFunction(() => document.querySelector('#connection-label').textContent === '已連接後端');
  const registration = page.waitForResponse(response => response.url() === `${api}/api/v1/devices` && response.request().method() === 'POST');
  await page.locator('#start-scenario').click();
  const { id: device } = await (await registration).json();
  await page.waitForFunction(id => document.querySelector('#device').value === id, device);
  for (let minute = 1; minute <= 7; minute++) {
    await page.waitForTimeout(55000);
    const tasks = await (await fetch(`${api}/api/v1/devices/${device}/memory/tasks`)).json();
    console.log(JSON.stringify({ minute, tasks: tasks.map(t => [t.rule_id, t.event_status, t.status]) }));
    if ((await page.locator('#scenario-status').textContent()).startsWith('劇本完成')) break;
  }
  const tasks = await (await fetch(`${api}/api/v1/devices/${device}/memory/tasks`)).json();
  for (const rule of ['co2_high', 'co2_rising']) {
    for (const status of ['active', 'resolved']) {
      assert.ok(tasks.some(t => t.rule_id === rule && t.event_status === status && t.status === 'acknowledged'), `${rule} ${status} delivered`);
    }
  }
  const content = await page.locator('#messages').textContent();
  assert.ok(content.includes('主動提醒') && content.includes('主動回應'));
  assert.ok(!content.includes('1400') && !content.includes('800'));
  assert.equal(await page.locator('#mood-label').textContent(), '開心');
  assert.deepEqual(errors, []);
  await page.screenshot({ path: 'test-results/scenario-complete.png', fullPage: true });
  console.log('PASS: complete real-time CO2 scenario, proactive personality, acknowledgement and recovery');
} finally { await browser.close(); }
