import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import assert from 'node:assert/strict';

mkdirSync('test-results', { recursive: true });
const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  // Fake mic + autoplay so the voice round trip runs headless.
  args: ['--enable-unsafe-swiftshader', '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'],
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
  await page.waitForFunction(() => document.querySelector('#temperature-value').textContent !== '—');
  // Disturb Node-RED from the page, confirm it took effect there, then restore.
  const nodeRed = process.env.TEST_NODE_RED_URL || 'http://localhost:1880';
  const offsets = async () => (await (await fetch(`${nodeRed}/memo/disturbance`)).json()).anomaly_offset;
  await page.waitForFunction(() => ['傳送中', '已暫停'].includes(document.querySelector('#node-red-toggle').textContent));
  await page.locator('#dist-co2_ppm').evaluate(input => { input.value = '700'; input.dispatchEvent(new Event('input')); });
  assert.equal(await page.locator('#dist-co2_ppm-output').textContent(), '+700 ppm');
  await page.locator('#apply-disturbance').click();
  await page.waitForFunction(() => document.querySelector('#toast').textContent.startsWith('已套用干擾'));
  assert.equal((await offsets()).co2_ppm, 700);
  await page.locator('#reset-disturbance').click();
  await page.waitForFunction(() => document.querySelector('#dist-co2_ppm-output').textContent === '0 ppm');
  assert.deepEqual(await offsets(), {});
  // Desk lamp: picking a mode updates the backend and the room; restore daylight afterwards.
  const api = process.env.TEST_API_URL || 'http://localhost:8000';
  const lampMode = async () => (await (await fetch(`${api}/api/v1/devices/room01/lamp`)).json()).mode;
  await page.getByText('溫暖', { exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.stage').dataset.lamp === 'warm');
  assert.equal(await lampMode(), 'warm');
  await page.getByText('白天', { exact: true }).click();
  await page.waitForFunction(() => document.querySelector('#lamp-state').textContent === '白天光');
  assert.equal(await lampMode(), 'day');
  // Persona: preset is sent with the chat; editing its description switches to the custom persona.
  await page.getByText('損友', { exact: true }).click();
  assert.ok((await page.locator('#persona-text').inputValue()).startsWith('愛吐槽的損友'));
  await page.locator('#persona-text').fill('講話像老派管家，有禮但會碎念。');
  assert.equal(await page.locator('input[name="persona"]:checked').getAttribute('value'), 'custom');
  const chatRequest = page.waitForRequest(request => request.url().endsWith('/interaction'));
  const spoken = page.waitForResponse(response => response.url().endsWith('/tts'), { timeout: 120000 });
  await page.locator('#chat-input').fill('MEMO，現在的環境如何？');
  await page.locator('#send-message').click();
  assert.deepEqual((await chatRequest).postDataJSON().personality, { tone: 'gentle', persona: '講話像老派管家，有禮但會碎念。' });
  // The reply appears in the speech bubble, quoting the question, placed on stage above BMO.
  await page.waitForFunction(() => !document.querySelector('#bubble').classList.contains('waiting'), null, { timeout: 60000 });
  assert.ok((await page.locator('#bubble-text').textContent()).trim().length > 0);
  assert.equal(await page.locator('#bubble-quote').textContent(), 'MEMO，現在的環境如何？');
  const [bubble, stage] = await Promise.all([page.locator('#bubble').boundingBox(), page.locator('.stage').boundingBox()]);
  assert.ok(bubble.x >= stage.x && bubble.y >= stage.y && bubble.x + bubble.width <= stage.x + stage.width);
  // Voice: the reply is read aloud through the speech service; the mic records, transcribes and replies.
  const tts = await spoken;
  assert.equal(tts.status(), 200);
  assert.equal(tts.headers()['content-type'], 'audio/wav');
  const heard = page.waitForResponse(response => response.url().endsWith('/stt'));
  await page.locator('#mic').click();
  await page.waitForFunction(() => document.querySelector('#mic').getAttribute('aria-pressed') === 'true');
  await page.waitForTimeout(1500);
  await page.locator('#mic').click();
  assert.equal((await heard).status(), 200);
  // Start over: confirm, and the stored conversation for this session is gone.
  page.once('dialog', dialog => dialog.accept());
  const cleared = page.waitForResponse(response => response.request().method() === 'DELETE' && response.url().includes('/memory/conversation'));
  await page.locator('#new-chat').click();
  assert.equal((await cleared).status(), 204);
  await page.waitForFunction(() => document.querySelector('#bubble-text').textContent.startsWith('好，我們重新開始吧'));
  await page.locator('#pause-motion').click();
  await page.screenshot({ path: 'test-results/desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(600);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: 'test-results/mobile.png', fullPage: true });
  assert.deepEqual(errors, []);
  console.log('PASS: 3D renderer, real backend connection, Node-RED disturbance and restore, desk lamp, persona settings, chat reply, voice in/out, new conversation, mobile layout');
} finally { await browser.close(); }
