import './style.css';
import { createMemoScene } from './scene.js';
import { createSpeech } from './speech.js';
import { createCamera } from './camera.js';
import { startTracking } from './vision.js';
import { createLookRunner } from './looks.js';
import { references } from './references.js';
import { MIRROR, OWN_FACES, REPLY_HOLD, createExpressionReader, createGaze, createWaveDetector, screenFace, spokenText, wantsLook, wantsReport } from './perception.js';

const $ = selector => document.querySelector(selector);
const ICONS = {
  thermo: '<path d="M10 14.5V5a2 2 0 0 1 4 0v9.5a4 4 0 1 1-4 0z"/>',
  drop: '<path d="M12 3c3 4 6 7 6 10.5a6 6 0 0 1-12 0C6 10 9 7 12 3z"/>',
  wind: '<path d="M3 9h11a3 3 0 1 0-3-3M3 14h15a3 3 0 1 1-3 3M3 19h7"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  replay: '<path d="M4 12a8 8 0 1 0 3-6.2M4 4v5h5"/>',
  pause: '<path d="M9 5v14M15 5v14"/>',
  play: '<path d="M7 4l13 8-13 8z"/>',
  send: '<path d="M12 19V5M5 12l7-7 7 7"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  speaker: '<path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>',
  mute: '<path d="M4 9v6h4l5 4V5L8 9zM17 9l4 6M21 9l-4 6"/>',
  image: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="M21 16l-5-5-9 8"/>',
  newChat: '<path d="M4 5h16v11H9l-5 4z"/><path d="M12 8v5M9.5 10.5h5"/>',
  camera: '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
};
const icon = name => `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;
for (const el of document.querySelectorAll('[data-icon]')) el.innerHTML = icon(el.dataset.icon);
for (const [id, name] of [['reset-camera', 'replay'], ['pause-motion', 'pause'], ['send-message', 'send'], ['mic', 'mic'], ['attach', 'image'], ['new-chat', 'newChat'], ['camera-toggle', 'camera']]) $(`#${id}`).innerHTML = icon(name);
const storage = { get(key) { try { return localStorage.getItem(key); } catch { return null; } }, set(key, value) { try { localStorage.setItem(key, value); } catch { /* Private browsers may block storage. */ } } };
// Backend origin comes only from VITE_API_URL (read when Vite starts or builds).
// The page follows the device Node-RED is sending for; room01 until Node-RED reports otherwise.
let apiBase = (import.meta.env.VITE_API_URL || '').trim(), activeDevice = 'room01', readings = [];
let refreshing = false, chatting = false, generation = 0, toastTimer, connected = false;
const sessionId = storage.get('memo.sessionId') || crypto.randomUUID();
storage.set('memo.sessionId', sessionId);
let conversationScope = '';
const seenNotifications = new Set();
let companion;
try { companion = createMemoScene($('#scene')); } catch (error) { $('#webgl-fallback').hidden = false; console.warn('3D unavailable', error.message); }
if (import.meta.env.DEV) window.memoScene = companion;
function motionButton(paused) { const label = paused ? '恢復動態' : '暫停動態'; $('#pause-motion').innerHTML = icon(paused ? 'play' : 'pause'); $('#pause-motion').setAttribute('aria-label', label); $('#pause-motion').title = label; }
if (companion?.paused) motionButton(true);
$('#reset-camera').onclick = () => companion?.reset();
$('#pause-motion').onclick = () => motionButton(companion?.toggleMotion());

// Voice: replies and proactive notices are read aloud through the local speech service (VITE_SPEECH_URL).
const speechBase = (import.meta.env.VITE_SPEECH_URL || '').trim().replace(/\/$/, '');
let talkingNow = false;
const speech = speechBase ? createSpeech(speechBase, value => { talkingNow = value; companion?.setTalking(value); if (!value) lastReplyAt = performance.now(); }) : null;
let voiceOn = storage.get('memo.voice') !== 'off';
function voiceButton() {
  const label = voiceOn ? '關閉語音朗讀' : '開啟語音朗讀';
  $('#voice-toggle').innerHTML = icon(voiceOn ? 'speaker' : 'mute'); $('#voice-toggle').setAttribute('aria-pressed', String(voiceOn));
  $('#voice-toggle').setAttribute('aria-label', label); $('#voice-toggle').title = label;
}
$('#voice-toggle').onclick = () => { voiceOn = !voiceOn; storage.set('memo.voice', voiceOn ? 'on' : 'off'); if (!voiceOn) speech?.stop(); voiceButton(); };
$('#voice-toggle').hidden = $('#mic').hidden = !speech;
voiceButton();

// Speech bubble pinned above BMO's head; follows the 3D anchor as the camera moves.
const bubbleEl = $('#bubble'), bubbleState = { queue: [], shownAt: 0, timer: 0, x: NaN, y: NaN };
function placeBubble() {
  const stage = $('.stage').getBoundingClientRect(), point = companion?.anchor();
  const x = point ? point.x : stage.width * .62, y = point ? point.y : stage.height * .3;
  if (!(Math.abs(x - bubbleState.x) <= .5 && Math.abs(y - bubbleState.y) <= .5)) {
    bubbleState.x = x; bubbleState.y = y;
    const width = bubbleEl.offsetWidth;
    // Keep the bubble on stage: shift left near the right edge, never above the top.
    bubbleEl.style.left = `${Math.max(16, Math.min(x, stage.width - width - 16))}px`;
    bubbleEl.style.bottom = `${Math.max(16, stage.height - y)}px`;
    bubbleEl.style.maxHeight = `${Math.max(120, y - 16)}px`;
  }
  requestAnimationFrame(placeBubble);
}
function say(text, { quote = '', waiting = false, voice = false, sources = [], spoken = text, report = false } = {}) {
  // Anything new on screen interrupts what MEMO was saying; only replies and notices are spoken.
  if (voice && voiceOn) speech?.speak(spoken); else speech?.stop();
  $('#bubble-quote').textContent = quote; $('#bubble-quote').hidden = !quote;
  // An answer to an assignment (watch, report, what happened) is marked as a report.
  $('#bubble-tag').hidden = !report;
  $('#bubble-text').textContent = text;
  // Web answers show where they came from; the links open the source page.
  const links = sources.filter((item, index) => sources.findIndex(other => other.source === item.source) === index).map(item => {
    const link = document.createElement('a');
    Object.assign(link, { href: item.url, target: '_blank', rel: 'noopener noreferrer', textContent: item.source, title: item.title });
    return link;
  });
  $('#bubble-sources').replaceChildren(...(links.length ? ['來源：', ...links] : [])); $('#bubble-sources').hidden = !links.length;
  bubbleEl.classList.toggle('waiting', waiting);
  bubbleEl.classList.remove('pop'); void bubbleEl.offsetWidth; bubbleEl.classList.add('pop');
  bubbleEl.scrollTop = 0; bubbleState.shownAt = performance.now(); bubbleState.x = NaN;
}
// Proactive notices queue so back-to-back ones each stay readable and never cut into a chat reply.
function announce(text, emotion) { bubbleState.queue.push({ text, emotion }); drainBubble(); }
function drainBubble() {
  if (bubbleState.timer || !bubbleState.queue.length) return;
  const wait = chatting || talkingNow ? 1500 : Math.max(0, bubbleState.shownAt + 6000 - performance.now());
  bubbleState.timer = setTimeout(() => {
    bubbleState.timer = 0;
    if (!chatting && !talkingNow) { const next = bubbleState.queue.shift(); say(next.text, { voice: true }); showEmotion(next.emotion); }
    drainBubble();
  }, wait);
}
requestAnimationFrame(placeBubble);

function toast(message) { clearTimeout(toastTimer); $('#toast').textContent = message; $('#toast').hidden = false; toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 5500); }
function connection(ok, label) { connected = ok; $('#connection-dot').classList.toggle('online', ok); $('#connection-label').textContent = label; }
function normalizeUrl(value) {
  const url = new URL(value.trim());
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== '/') throw new Error('請填完整的 HTTP(S) 來源網址，不包含路徑、帳密或查詢參數。');
  if (location.protocol === 'https:' && url.protocol === 'http:') throw new Error('此網站使用 HTTPS，VITE_API_URL 須為 HTTPS。');
  return url.origin;
}
async function request(path, options = {}, base = apiBase) {
  if (!base) throw new Error('尚未設定 VITE_API_URL。');
  let response;
  try { response = await fetch(`${base}${path}`, { ...options, signal: AbortSignal.timeout(/\/(interaction|digest|captures)$/.test(path) ? 45000 : 12000), headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } }); }
  catch { throw new Error('無法連接後端。請檢查網址、HTTPS、CORS 或網路連線。'); }
  if (!response.ok) {
    const error = new Error(response.status === 404 ? '找不到這個裝置或 API，請確認後端已更新。' : `後端回應錯誤（${response.status}），請稍後再試。`);
    error.status = response.status; throw error;
  }
  return response.status === 204 ? null : response.json();
}
function freshness(reading) {
  if (!reading) return '等待資料';
  if (reading.quality !== 'ok') return '品質待確認';
  const age = Date.now() - Date.parse(reading.observed_at);
  if (!Number.isFinite(age) || age < 0) return '時間待確認';
  return age <= 30000 ? '剛剛更新' : '資料已過期';
}
function drawSparkline(key) {
  const svg = $(`[data-metric="${key}"] .sparkline`);
  svg.replaceChildren();
  const values = readings.slice(0, 24).reverse().map(r => r.quality === 'ok' && r[key] != null ? r[key] : null);
  const valid = values.filter(v => v !== null);
  if (valid.length < 2) return;
  const min = Math.min(...valid), span = Math.max(...valid) - min || 1;
  let path = '', drawing = false;
  values.forEach((value, index) => { if (value === null) { drawing = false; return; } path += `${drawing ? 'L' : 'M'}${index / (values.length - 1) * 100},${24 - (value - min) / span * 20} `; drawing = true; });
  const element = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  element.setAttribute('d', path); element.setAttribute('fill', 'none'); element.setAttribute('stroke', { temperature_c: '#D9A400', humidity_pct: '#3D86E0', co2_ppm: '#2F8F7B', illuminance_lux: '#C2711A' }[key]); element.setAttribute('stroke-width', '2'); element.setAttribute('stroke-linejoin', 'round'); element.setAttribute('vector-effect', 'non-scaling-stroke'); svg.appendChild(element);
}
function renderReadings() {
  const latest = readings[0];
  const fresh = freshness(latest);
  $('#freshness').textContent = fresh; $('#freshness').classList.toggle('live', fresh === '剛剛更新' && connected);
  for (const [key, id] of [['temperature_c', 'temperature-value'], ['humidity_pct', 'humidity-value'], ['co2_ppm', 'co2-value'], ['illuminance_lux', 'lux-value']]) {
    const value = latest?.[key]; $(`#${id}`).textContent = value == null ? '—' : key === 'temperature_c' ? Number(value).toFixed(1) : Math.round(value).toLocaleString(); drawSparkline(key);
  }
}
async function refresh(showErrors = false) {
  if (!apiBase || refreshing) return;
  refreshing = true; const currentGeneration = generation;
  try {
    const selected = activeDevice;
    let resultReadings;
    try { resultReadings = await request(`/api/v1/devices/${encodeURIComponent(selected)}/readings?limit=24`); }
    catch (error) {
      // The device registers itself on its first sample; until then there is simply nothing to show.
      if (error.status !== 404) throw error;
      if (currentGeneration === generation) { readings = []; connection(true, '已連接後端'); renderReadings(); }
      return;
    }
    if (currentGeneration !== generation || selected !== activeDevice) return;
    readings = resultReadings; connection(true, '已連接後端'); renderReadings();
    await syncLamp(selected);
    await syncCompanion(selected, currentGeneration);
    if (performance.now() - looksSyncedAt > 15000) syncLooks(selected).catch(() => {});
    if (performance.now() - observationSyncedAt > 15000) syncObservation(selected).catch(() => {});
  } catch (error) { if (currentGeneration !== generation) return; connection(false, '後端連線中斷'); $('#freshness').classList.remove('live'); $('#freshness').textContent = '連線中斷'; if (showErrors) toast(error.message); }
  finally { refreshing = false; }
}
const missingApi = () => { if (!apiBase) toast('尚未設定 VITE_API_URL，請設定後重新啟動 Vite。'); return !apiBase; };

// Persona presets: tone drives the fallback templates; the description is what the model plays.
const PERSONAS = {
  companion: { tone: 'gentle', text: '溫柔安靜的桌上小夥伴，說話輕柔，先關心你的感受。' },
  duty: { tone: 'concise', text: '冷靜的值班員，簡短直接，先講現況，再提醒該留意什麼。' },
  friend: { tone: 'lively', text: '愛吐槽的損友，語氣輕鬆幽默，但該提醒的一定講到。' },
  coach: { tone: 'lively', text: '有精神的教練，常給你打氣，督促你照顧好自己。' },
  custom: { tone: 'gentle', text: '' },
};
const persona = { id: 'companion', custom: '' };
try { Object.assign(persona, JSON.parse(storage.get('memo.persona')) || {}); } catch { /* Ignore an unreadable saved persona. */ }
if (!PERSONAS[persona.id]) persona.id = 'companion';
const personaText = () => persona.id === 'custom' ? persona.custom : PERSONAS[persona.id].text;
const personality = () => ({ tone: PERSONAS[persona.id].tone, persona: personaText().trim() });
function renderPersona() { $(`input[name="persona"][value="${persona.id}"]`).checked = true; $('#persona-text').value = personaText(); storage.set('memo.persona', JSON.stringify(persona)); }
document.querySelectorAll('input[name="persona"]').forEach(input => { input.onchange = () => { persona.id = input.value; renderPersona(); }; });
// Editing a preset's description turns it into the custom persona.
$('#persona-text').oninput = event => { persona.custom = event.target.value; if (persona.id !== 'custom') { persona.id = 'custom'; $('input[name="persona"][value="custom"]').checked = true; } storage.set('memo.persona', JSON.stringify(persona)); };
renderPersona();
// Desk lamp: switched only here (MEMO can see it but not change it); the backend keeps the
// mode, so other open pages follow within a refresh.
const LAMP_LABELS = { off: '關燈', dim: '暗光', warm: '溫暖光', day: '白天光' };
let lampMode = 'day', lampChanging = false;
function showLamp(mode) {
  lampMode = LAMP_LABELS[mode] ? mode : 'day';
  $(`input[name="lamp"][value="${lampMode}"]`).checked = true;
  $('.stage').dataset.lamp = lampMode; $('#lamp-state').textContent = LAMP_LABELS[lampMode];
  companion?.setLamp(lampMode);
}
async function syncLamp(device) {
  if (lampChanging) return;
  const lamp = await request(`/api/v1/devices/${encodeURIComponent(device)}/lamp`);
  if (device === activeDevice && !lampChanging) showLamp(lamp.mode);
}
document.querySelectorAll('input[name="lamp"]').forEach(input => {
  input.onchange = async () => {
    const previous = lampMode;
    showLamp(input.value);
    if (!apiBase) return;
    lampChanging = true;
    try { showLamp((await request(`/api/v1/devices/${encodeURIComponent(activeDevice)}/lamp`, { method: 'PUT', body: JSON.stringify({ mode: input.value }) })).mode); }
    catch (error) { showLamp(previous); toast(error.message); }
    finally { lampChanging = false; }
  };
});
// thinking and listening are page states; the rest come from MEMO's replies.
const moodLabels = { neutral: '安靜陪伴', friendly: '溫柔陪伴', happy: '開心', concerned: '有點擔心', confused: '有點疑惑', surprised: '嚇一跳', proud: '專業模式', sleepy: '有點睏', alert: '警戒中', error: '出錯了', thinking: '思考中', listening: '在聽你說', looking: '拍照中' };
const moodFaces = { neutral: '◡', friendly: '◡', happy: '▽', concerned: '︵', confused: '?', surprised: '!', proud: '✦', sleepy: 'z', alert: '!', error: '×', thinking: '…', listening: '♪', looking: '◉' };
// Images: one picture per message, shrunk here to a JPEG of at most 1024 px so phone photos
// upload quickly. The backend shows it to the model and keeps only a note that it was sent.
const IMAGE_SIDE = 1024, IMAGE_LIMIT = 2 * 1024 * 1024;
let attachment = null;
async function shrinkImage(file) {
  if (!file.type.startsWith('image/')) throw new Error('只能上傳圖片。');
  const bitmap = await createImageBitmap(file).catch(() => { throw new Error('這張圖片無法讀取，請換一張 JPEG 或 PNG。'); });
  const scale = Math.min(1, IMAGE_SIDE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(bitmap.width * scale); canvas.height = Math.round(bitmap.height * scale);
  const context = canvas.getContext('2d');
  context.fillStyle = '#fff'; context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(bitmap, 0, 0, canvas.width, canvas.height); bitmap.close();
  for (const quality of [.85, .7, .5]) {
    const url = canvas.toDataURL('image/jpeg', quality);
    if (url.length * .75 <= IMAGE_LIMIT) return url;
  }
  throw new Error('圖片太大了，請換一張小一點的。');
}
function setAttachment(url) {
  attachment = url;
  $('#attachment').hidden = !url;
  if (url) $('#attachment-preview').src = url; else $('#attachment-preview').removeAttribute('src');
}
async function attachFile(file) {
  try { setAttachment(await shrinkImage(file)); $('#chat-input').focus(); }
  catch (error) { toast(error.message); }
}
$('#attach').onclick = () => $('#image-input').click();
$('#image-input').onchange = event => { const [file] = event.target.files; event.target.value = ''; if (file) attachFile(file); };
$('#attachment-remove').onclick = () => setAttachment(null);
// Pasting a screenshot into the message box attaches it.
$('#chat-input').addEventListener('paste', event => {
  const file = [...(event.clipboardData?.files || [])].find(item => item.type.startsWith('image/'));
  if (file) { event.preventDefault(); attachFile(file); }
});
// "MEMO 我手上拿的是什麼": with the camera on, MEMO takes a photo right away (the person is
// already showing it) and asks with it. When the model asks to look (look_requested), the
// same message is sent again with a photo, also right away.
async function snap() {
  snapping = true; showEmotion({ label: 'looking', intensity: .5 }); flashPhoto();
  try { return (await camera.photo()).image; } finally { snapping = false; }
}
async function chat(text, { photo = false } = {}) {
  if (chatting) return;
  if (missingApi()) return;
  const selected = activeDevice, currentGeneration = generation;
  let image = attachment, lookAgain = false;
  chatting = true; $('#send-message').disabled = true;
  $('#chat-input').value = ''; setAttachment(null);
  try {
    if (!image && camera.active && !photo && wantsReport(text)) {
      // 「現在這邊怎樣」: MEMO looks around the whole room right away.
      flashPhoto(); image = (await camera.photo({ maxSide: 1024 })).image; photo = true;
    } else if (!image && camera.active && (photo || wantsLook(text))) {
      image = await snap(); photo = true;
    }
    if (image) text ||= '幫我看看這張圖';
    const quote = image ? `${text}（${photo ? '拍照' : '附圖'}）` : text;
    say('讓我想想…', { quote, waiting: true });
    showEmotion({ label: 'thinking', intensity: .5 });
    const reply = await request(`/api/v1/devices/${encodeURIComponent(selected)}/interaction`, { method: 'POST', body: JSON.stringify({ session_id: sessionId, request_id: crypto.randomUUID(), user_message: text, personality: personality(), camera: camera.active ? 'on' : 'off', ...(image ? { image } : {}) }) });
    if (generation !== currentGeneration) { say('裝置已切換，已略過上一個裝置的回覆。'); return; }
    const hint = reply.look_requested && !camera.active ? '先按畫面右上角的鏡頭按鈕打開鏡頭，我才看得到喔。' : '';
    say(hint ? `${reply.text}\n${hint}` : reply.text, { quote, voice: true, sources: reply.sources || [], spoken: image ? spokenText(reply.text) : reply.text, report: reply.mode === 'assignment' });
    showEmotion(reply.emotion);
    lookAgain = reply.look_requested && camera.active;
    if (reply.actions?.some(action => action.includes('持續盯著'))) offerRoi(selected);
    else if (reply.actions?.length) syncLooks(selected).catch(() => {}); // set up or deleted by voice
  } catch (error) { say(error.message, { quote: text }); showEmotion({ label: 'error', intensity: .5 }); }
  finally { chatting = false; $('#send-message').disabled = false; lastReplyAt = performance.now(); }
  if (lookAgain) chat(text, { photo: true });
}
// Mic: click to start. A pause after speaking (or a second click, or 15 s) stops it and sends the
// transcript as a chat message; silence from the start cancels quietly.
let recording = null, recordTimer = 0;
function micState(active) { const label = active ? '停止並送出' : '按一下開始說話'; $('#mic').setAttribute('aria-pressed', String(active)); $('#mic').setAttribute('aria-label', label); $('#mic').title = label; }
micState(false);
$('#mic').onclick = async () => {
  if (chatting || !speech) return;
  if (!recording) {
    try {
      speech.stop();
      recording = await speech.record({ onEnd: reason => { if (!recording) return; if (reason === 'silence') $('#mic').click(); else cancelRecording(); } });
    } catch { toast('無法使用麥克風，請允許瀏覽器存取麥克風。'); return; }
    micState(true); showEmotion({ label: 'listening', intensity: .5 });
    recordTimer = setTimeout(() => { if (recording) $('#mic').click(); }, 15000);
    return;
  }
  clearTimeout(recordTimer);
  const current = recording; recording = null; micState(false); $('#mic').disabled = true;
  try {
    say('聽聽你說了什麼…', { waiting: true }); showEmotion({ label: 'thinking', intensity: .5 });
    const { text } = await speech.transcribe(await current.stop());
    if (text.trim()) chat(text.trim()); else { say('我沒聽清楚，可以再說一次嗎？'); showEmotion({ label: 'confused', intensity: .4 }); }
  } catch (error) { say(error.message); showEmotion({ label: 'error', intensity: .5 }); }
  finally { $('#mic').disabled = false; }
};
function cancelRecording() {
  clearTimeout(recordTimer);
  recording?.cancel(); recording = null; micState(false);
  say('我沒聽到聲音，想說話時再按一次麥克風。'); showEmotion({ label: 'neutral', intensity: .3 });
}
// Start over: the conversation and its summaries are cleared; what MEMO remembers about the
// person (name, likes, projects) stays, since it belongs to the same browser session.
$('#new-chat').onclick = async () => {
  if (missingApi()) return;
  if (chatting) { toast('等 MEMO 回完這句，再重新開始。'); return; }
  if (!confirm('要清除這次的對話，重新開始嗎？\nMEMO 記得的「關於你的事」會保留。')) return;
  try {
    await request(`/api/v1/devices/${encodeURIComponent(activeDevice)}/memory/conversation?session_id=${sessionId}`, { method: 'DELETE' });
    speech?.stop(); bubbleState.queue.length = 0;
    say('好，我們重新開始吧！想聊什麼都可以。'); showEmotion({ label: 'friendly', intensity: .5 });
    toast('已清除這次的對話。');
  } catch (error) { toast(error.message); }
};
$('#chat-form').onsubmit = event => { event.preventDefault(); const text = $('#chat-input').value.trim(); if (text || attachment) chat(text); };
// Node-RED keeps sending drifting readings; this panel only adds or clears disturbance offsets there.
const nodeRedBase = (import.meta.env.VITE_NODE_RED_URL || '').trim().replace(/\/$/, '');
const offsetInputs = [...document.querySelectorAll('[data-offset]')];
let offsetsEdited = false, nodeRed = null;
function showOffset(input) { const value = Number(input.value); $(`#${input.id}-output`).textContent = `${value > 0 ? '+' : ''}${value} ${input.dataset.unit}`; }
offsetInputs.forEach(input => { input.oninput = () => { offsetsEdited = true; showOffset(input); }; });
async function disturbance(body) {
  if (!nodeRedBase) throw new Error('尚未設定 VITE_NODE_RED_URL。');
  // text/plain keeps the POST a simple CORS request, so Node-RED needs no preflight handling.
  const options = body ? { method: 'POST', headers: { 'Content-Type': 'text/plain' }, body: JSON.stringify(body) } : {};
  let response;
  try { response = await fetch(`${nodeRedBase}/memo/disturbance`, { ...options, signal: AbortSignal.timeout(8000) }); }
  catch { throw new Error('無法連接 Node-RED。請確認容器已啟動，且 CORS_ORIGINS 包含本網站。'); }
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || `Node-RED 回應錯誤（${response.status}）。`);
  return result;
}
function renderDisturbance(state) {
  nodeRed = state;
  const toggle = $('#node-red-toggle');
  toggle.disabled = !state; toggle.setAttribute('aria-pressed', String(Boolean(state?.enabled)));
  toggle.textContent = !state ? (nodeRedBase ? '未連線' : '未設定') : state.enabled ? '傳送中' : '已暫停';
  toggle.title = state ? (state.enabled ? '暫停 Node-RED 傳送' : '恢復 Node-RED 傳送') : '';
  $('#apply-disturbance').disabled = $('#reset-disturbance').disabled = !state;
  if (state && state.device_id !== activeDevice) { activeDevice = state.device_id; generation++; readings = []; renderReadings(); refresh(); }
  if (state && !offsetsEdited) offsetInputs.forEach(input => { input.value = state.anomaly_offset[input.dataset.offset] || 0; showOffset(input); });
}
async function syncDisturbance() {
  if (!nodeRedBase) return renderDisturbance(null);
  try { renderDisturbance(await disturbance()); } catch { renderDisturbance(null); }
}
async function changeDisturbance(body, note) {
  try {
    const state = await disturbance(body);
    offsetsEdited = false; renderDisturbance(state); toast(note);
  } catch (error) { toast(error.message); }
}
$('#apply-disturbance').onclick = () => changeDisturbance({ offsets: Object.fromEntries(offsetInputs.map(input => [input.dataset.offset, Number(input.value)])) }, '已套用干擾，數值會在幾筆資料內逐步變化。');
$('#reset-disturbance').onclick = () => changeDisturbance({ reset: true }, '已恢復正常，數值會逐步回到平常範圍。');
$('#node-red-toggle').onclick = () => changeDisturbance({ enabled: !nodeRed?.enabled }, nodeRed?.enabled ? '已暫停 Node-RED 傳送。' : '已恢復 Node-RED 傳送。');
// MEMO's screen: page states and its own replies come first (a reply's expression holds for
// a while); otherwise MEMO answers the person's expression. The label beside it stays MEMO's.
const face = { base: { label: 'neutral', intensity: .4 }, holdUntil: 0, mirror: null, mirrorAt: -Infinity, shown: '' };
function applyFace() {
  const label = screenFace({ base: face.base.label, holdUntil: face.holdUntil, now: performance.now(), busy: talkingNow, mirror: face.mirror });
  const intensity = label === face.base.label ? face.base.intensity : .5;
  if (`${label}:${intensity}` === face.shown) return;
  face.shown = `${label}:${intensity}`; companion?.setEmotion(label, intensity);
}
function setMirror(label, now) {
  // At most one change every 1.5 s, so MEMO does not flicker with every twitch.
  if (label === face.mirror || now - face.mirrorAt < 1500) return;
  face.mirror = label; face.mirrorAt = now; applyFace();
}
function showEmotion(emotion) {
  $('#mood-label').textContent = moodLabels[emotion.label] || moodLabels.neutral;
  $('#mood-face').textContent = moodFaces[emotion.label] || moodFaces.neutral;
  face.base = emotion; face.holdUntil = performance.now() + REPLY_HOLD; applyFace();
}
async function syncCompanion(device, currentGeneration) {
  const scope = `${apiBase}/${device}/${sessionId}`;
  if (conversationScope !== scope) {
    const turns = await request(`/api/v1/devices/${encodeURIComponent(device)}/memory/conversation?session_id=${sessionId}`);
    if (generation !== currentGeneration || activeDevice !== device) return;
    const last = turns.at(-1);
    if (!chatting) say(last ? last.response.text : '我在這裡。空間有值得留意的變化時，我會輕輕告訴你。', { quote: last?.user_text });
    conversationScope = scope;
    // Back after a while: one or two sentences on what MEMO said while nobody was here.
    const lastSeen = Number(storage.get('memo.lastSeen')) || 0;
    if (lastSeen && Date.now() - lastSeen > 120000) {
      try {
        const digest = await request(`/api/v1/devices/${encodeURIComponent(device)}/digest`, { method: 'POST', body: JSON.stringify({ since: new Date(lastSeen).toISOString() }) });
        if (digest.text && generation === currentGeneration) announce(digest.text, digest.emotion);
      } catch { /* A digest is a courtesy; never block the page on it. */ }
    }
  }
  await deliverNotifications(device, currentGeneration);
  storage.set('memo.lastSeen', String(Date.now()));
}
// Ready notices are shown and spoken, then acknowledged. Look tasks call this right after a
// photo leads to a notice, also while the tab is hidden, so MEMO speaks within seconds.
async function deliverNotifications(device, currentGeneration = generation) {
  const tasks = await request(`/api/v1/devices/${encodeURIComponent(device)}/notifications`);
  if (generation !== currentGeneration || activeDevice !== device) return;
  for (const task of tasks) {
    if (!seenNotifications.has(task.id)) {
      announce(task.response.text, task.response.emotion); seenNotifications.add(task.id);
    }
    try { await request(`/api/v1/devices/${encodeURIComponent(device)}/notifications/ack`, { method: 'POST', body: JSON.stringify({ task_id: task.id }) }); }
    catch (error) { if (error.status !== 409) throw error; }
  }
}
// Camera: MEMO's eyes follow the person, its screen answers their expression, it waves back
// while idle, and it takes photos when asked or when a look task is due. Off until turned on.
let snapping = false, lastReplyAt = 0, lastTyping = 0, tracking = null, looksSyncedAt = -Infinity, runner = null;
let cameraWanted = storage.get('memo.camera') === 'on';
const CAMERA_STATES = { on: '觀察中', off: '鏡頭關著', denied: '鏡頭權限被拒絕', unavailable: '找不到可用的鏡頭', insecure: '鏡頭需要 localhost 或 HTTPS' };
const baseTitle = document.title;
const camera = createCamera({ onChange: renderCamera });
$('#camera-preview').appendChild(camera.video);
const gaze = createGaze(), expressions = createExpressionReader(), waves = createWaveDetector();
function updateTitle() { document.title = camera.active && document.hidden ? `📷 ${baseTitle}` : baseTitle; }
function renderCamera(state) {
  const on = state === 'on', label = on ? '關閉鏡頭（停止觀察）' : '打開鏡頭：MEMO 開始觀察這裡，眼睛也會跟著你';
  $('#camera-toggle').setAttribute('aria-pressed', String(on)); $('#camera-toggle').setAttribute('aria-label', label); $('#camera-toggle').title = label;
  $('#camera-chip').hidden = state === 'off';
  $('#camera-chip').classList.toggle('on', on);
  $('#camera-chip-label').textContent = CAMERA_STATES[state] || CAMERA_STATES.off;
  $('#camera-preview').hidden = !on;
  $('#observe-state').textContent = on ? '觀察中' : '鏡頭沒開';
  $('#observe-state').classList.toggle('live', on);
  updateTitle();
}
renderCamera('off');
// MEMO waves back only when it is doing nothing else and the person is not typing.
function idle() {
  const now = performance.now();
  return !chatting && !talkingNow && !recording && !snapping && !OWN_FACES.has(face.base.label)
    && now - lastReplyAt > 3000 && now - lastTyping > 2000 && !companion?.waving;
}
function waveBack() { if (companion?.paused) setMirror('happy', performance.now()); else companion?.wave(); }
async function cameraOn({ quiet = false } = {}) {
  cameraWanted = true; storage.set('memo.camera', 'on');
  if (!(await camera.start())) { if (!quiet) toast(CAMERA_STATES[camera.state]); return false; }
  syncObservation(activeDevice).catch(() => {});
  tracking ||= startTracking(camera.video, {
    onFace(reading, now) {
      runner?.face(Boolean(reading));
      if (!reading) { gaze.target(null); expressions.lost(); setMirror(null, now); return; }
      gaze.target(reading.centre);
      setMirror(MIRROR[expressions.update(reading.blendshapes, now)] || null, now);
    },
    onHand(hand, chinY, now) { if (waves.update(hand, chinY, now) && idle()) waveBack(); },
    wantHands: idle,
  }).catch(error => {
    tracking = null; console.warn('face tracking unavailable', error.message);
    toast('臉部追蹤載入失敗；拍照仍然可以用。');
  });
  return true;
}
function cameraOff() {
  cameraWanted = false; storage.set('memo.camera', 'off');
  camera.stop(); gaze.target(null); expressions.lost(); face.mirror = null; applyFace();
  syncObservation(activeDevice).catch(() => {});
}
$('#camera-toggle').onclick = () => (camera.active ? cameraOff() : cameraOn());
$('#chat-input').addEventListener('keydown', () => { lastTyping = performance.now(); });
// Observation goes on in the background: the camera stays on until it is turned off.
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && cameraWanted && !camera.active) cameraOn({ quiet: true });
  updateTitle();
});
let followed = performance.now();
requestAnimationFrame(function follow(now) {
  const offset = gaze.update(Math.min(.1, (now - followed) / 1000)); followed = now;
  companion?.setGaze(companion.paused ? { x: 0, y: 0 } : offset);
  applyFace();
  requestAnimationFrame(follow);
});
function flashPhoto() {
  if (!camera.active) return;
  $('#camera-chip').classList.add('flash'); $('#camera-chip-label').textContent = '剛看了一眼';
  setTimeout(() => { $('#camera-chip').classList.remove('flash'); renderCamera(camera.state); }, 2500);
}
function startLooks() {
  runner = createLookRunner({
    camera, request, device: () => activeDevice, onPhoto: flashPhoto,
    onNotice: () => deliverNotifications(activeDevice).catch(() => {}),
    onJobs: () => syncLooks(activeDevice).catch(() => {}),
    onObserved: () => syncObservation(activeDevice).catch(() => {}),
  });
  runner.start();
}

// 日常紀錄 section: what MEMO saw last, and today's log (what the camera saw, what tasks
// found, the room's events, marked when MEMO said it out loud). MEMO only looks again when
// the scene changes, someone comes or goes, or on its 15-minute patrol, and a look the model
// could not read leaves what it saw before in place, so the latest try is shown when it failed.
const SOURCE_WORDS = { observe: '鏡頭', look: '任務', environment: '環境' };
let observationSyncedAt = -Infinity;
async function syncObservation(device) {
  observationSyncedAt = performance.now();
  const base = `/api/v1/devices/${encodeURIComponent(device)}`;
  const midnight = new Date().setHours(0, 0, 0, 0), hours = Math.max(1, Math.ceil((Date.now() - midnight) / 3600000));
  const [state, log, [tried]] = await Promise.all([
    request(`${base}/observation`), request(`${base}/observation/log?hours=${hours}&limit=40`), request(`${base}/vision/observations?job_id=observe&limit=1`)]);
  if (device !== activeDevice) return;
  const now = state.now, today = log.filter(entry => new Date(entry.at) >= midnight);
  const parts = now ? [now.summary || (now.people != null && `${now.people} 人`), now.lighting && `光線${now.lighting}`].filter(Boolean) : [];
  $('#observe-seen').textContent = now ? `${now.seen_at} 看到：${parts.join('・')}` : 'MEMO 正在看第一眼…';
  // The latest look is newer than what MEMO saw last whenever it is not a 'seen' one.
  const failed = tried && tried.result !== 'seen';
  $('#observe-tried').textContent = failed ? `${clockOf(tried.captured_at)} ${now ? '有再看一次' : '看了一眼'}，但沒看清楚（${REASON_WORDS[tried.reason] || '看不出來'}）` : '';
  $('#observe-tried').hidden = !failed;
  $('#observe-seen').hidden = !now && failed;
  $('#observe-now').hidden = !now && !camera.active;
  $('#observe-log').replaceChildren(...today.map(entry => element('li', { className: entry.spoken ? 'spoken' : '' },
    element('time', { textContent: clockOf(entry.at) }),
    element('span', { className: 'tag', textContent: SOURCE_WORDS[entry.source] || entry.source }),
    element('span', { textContent: entry.text }))));
  $('#observe-empty').hidden = today.length > 0 || !$('#observe-now').hidden;
  $('#observe-clear').hidden = !today.some(entry => entry.source !== 'environment') && !now;
}
$('#observe-clear').onclick = async () => {
  if (!confirm('要清除鏡頭的觀察紀錄嗎？MEMO 記得的現場也會一起清掉；環境事件會保留。')) return;
  try { await request(`/api/v1/devices/${encodeURIComponent(activeDevice)}/observation/log`, { method: 'DELETE' }); await syncObservation(activeDevice); }
  catch (error) { toast(error.message); }
};

// Tasks: reminders, and look tasks with their latest record and history (answer, photo
// quality, and the scene: 人、事、時、地、物、動作). Photos themselves are never kept.
const SHORT_KINDS = ['once', 'followup'];
const PURPOSE_WORDS = { look: '拍照', remind: '提醒', summary: '摘要', followup: '追蹤' };
// memo.domain.vision.TRIGGER_LABELS
const TRIGGER_WORDS = { light: '燈光變化', co2_high: 'CO₂ 偏高', co2_rising: 'CO₂ 上升', temperature_high: '太熱', temperature_low: '太冷', temperature_rising: '變熱', humidity_high: '太潮濕', humidity_low: '太乾' };
const spanOf = job => job.seconds ? `${job.seconds} 秒` : job.minutes % 60 === 0 ? `${job.minutes / 60} 小時` : `${job.minutes} 分鐘`;
function untilWords(iso) {
  const minutes = Math.round((new Date(iso) - Date.now()) / 60000);
  return minutes <= 0 ? '快到了' : minutes < 60 ? `還有 ${minutes} 分鐘` : `還有 ${Math.round(minutes / 60)} 小時`;
}
// A card's title is the thing itself; when and how it runs goes on one line under it.
function taskTitle(job) {
  if (job.purpose === 'followup') return `${job.message}之後的變化`;
  if (job.purpose === 'summary') return '環境摘要';
  return job.look?.watch && job.look.mode === 'check' ? job.look.question : job.message;
}
function taskMeta(job) {
  const look = job.look || {}, at = job.next_run_at && clockOf(job.next_run_at);
  const on = (look.on || []).map(trigger => TRIGGER_WORDS[trigger] || trigger).join('、');
  if (job.kind === 'event') return `${on}時看`;
  const when = job.kind === 'daily' ? `每天 ${job.time_of_day}`
    : SHORT_KINDS.includes(job.kind) ? (at ? `${at}（${untilWords(job.next_run_at)}）` : '')
    : look.watch ? `${look.mode === 'check' ? '盯著在不在' : '持續盯著'}・每 ${spanOf(job)}確認`
    : `每 ${spanOf(job)}${at ? `・下次 ${at}` : ''}`;
  return [when, on && `${on}時也看`].filter(Boolean).join('・');
}
const RESULT_WORDS = { yes: '是', no: '否', unknown: '看不出來', skipped: '沒拍到' };
const REASON_WORDS = { camera_off: '鏡頭沒開', permission_denied: '鏡頭權限被拒絕', camera_unavailable: '找不到鏡頭', no_capture: '網頁沒有回應', no_confirmation: '沒拍到確認照', quiet_hours: '勿擾時段', vision_unavailable: '模型不支援看圖', model_not_configured: '沒有設定模型', model_timeout: '模型逾時', model_unavailable: '模型連不上', invalid_model_output: '模型回覆無法解讀', vision_busy: '正在分析上一張', analysis_interrupted: '分析中斷' };
const QUALITY_WORDS = { dark: '太暗', overexposed: '過亮', low_contrast: '對比太低', blurry: '模糊' };
const element = (tag, props = {}, ...children) => { const node = Object.assign(document.createElement(tag), props); node.append(...children.filter(child => child != null && child !== false)); return node; };
const clockOf = iso => new Date(iso).toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit', hour12: false });
function sceneLine(scene) {
  if (!scene) return '';
  const people = scene.people || {}, who = [people.count != null ? `${people.count} 人` : '', people.description].filter(Boolean).join('，');
  return [scene.place && `地點：${scene.place}`, who && `人：${who}`, scene.activity && `事：${scene.activity}`, scene.objects?.length && `物：${scene.objects.join('、')}`, scene.actions?.length && `動作：${scene.actions.join('、')}`, scene.lighting && `光線：${scene.lighting}`].filter(Boolean).join('；');
}
function recordLine(record) {
  const answer = record.mode === 'change' ? { yes: '有變化', no: '沒變化' }[record.result] || RESULT_WORDS[record.result] : RESULT_WORDS[record.result];
  const flags = (record.quality?.flags || []).map(flag => QUALITY_WORDS[flag]);
  const unverified = record.result === 'yes' && record.reason && record.reason !== 'quiet_hours' && '畫面偵測到，模型沒確認';
  const sensors = record.sensors || {}, light = [sensors.light_changed && '光線剛變過', sensors.dark && '房間很暗'];
  return [`${clockOf(record.captured_at)} ${answer}`, flags.length && `照片${flags.join('、')}`, ...light, record.reason && (REASON_WORDS[record.reason] || record.reason), unverified, record.decision === 'baseline' && '設為基準', record.detail].filter(Boolean).join('・');
}
async function syncLooks(device) {
  looksSyncedAt = performance.now();
  const base = `/api/v1/devices/${encodeURIComponent(device)}`;
  const jobs = await request(`${base}/schedules`);
  const records = jobs.some(job => job.purpose === 'look') ? await request(`${base}/vision/observations?limit=80`) : [];
  if (device === activeDevice) renderLooks(jobs, records);
  return jobs.filter(job => job.purpose === 'look');
}
// A new watched task: framing the thing makes small changes count and keeps people walking
// by out of it, so MEMO offers the region editor right away.
async function offerRoi(device) {
  const jobs = await syncLooks(device).catch(() => []);
  const job = jobs.filter(item => item.look?.watch && !item.look.roi).sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  if (!job || device !== activeDevice) return;
  if (!camera.active) { toast('打開鏡頭後，可以在「任務」按「框選範圍」框出要盯著的東西。'); return; }
  toast('框出要盯著的東西，小小的變化也看得出來；不框就看整個畫面。');
  openRoi(job);
}
function renderLooks(jobs, records) {
  const active = jobs.filter(job => job.status === 'active').length;
  $('#looks-state').textContent = `${active} 個任務進行中`; $('#looks-state').hidden = !active;
  $('#looks-empty').hidden = jobs.length > 0;
  const card = job => {
    const button = (text, action, extra = '') => element('button', { type: 'button', className: `btn small ${extra}`.trim(), textContent: text, onclick: () => lookAction(job, action) });
    const paused = job.status === 'paused', look = job.purpose === 'look';
    const mine = look ? records.filter(record => record.job_id === job.id && record.decision !== 'confirm') : [];
    // Watching is a real choice only for a task that repeats every 5 minutes or more; more
    // often than that it is always watched.
    const watchChoice = look && job.kind === 'interval' && job.look.mode !== 'report' && !job.seconds && job.minutes >= 5;
    return element('li', { className: `look${paused ? ' paused' : ''}` },
      element('div', { className: 'look-head' },
        element('span', { className: `kind kind-${job.purpose}`, textContent: PURPOSE_WORDS[job.purpose] || job.purpose }),
        element('b', { textContent: taskTitle(job), title: job.description }),
        paused && element('span', { className: 'tag', textContent: '已暫停' })),
      element('p', { className: 'task-meta', textContent: taskMeta(job) }),
      mine[0] && element('p', { className: 'look-last', textContent: recordLine(mine[0]) }),
      mine.length > 1 && element('details', {}, element('summary', { textContent: `紀錄（${mine.length}）` }),
        element('ol', {}, ...mine.slice(0, 12).map(record => element('li', {}, element('span', { textContent: recordLine(record) }), sceneLine(record.scene) && element('small', { textContent: sceneLine(record.scene) }))))),
      element('div', { className: 'look-actions' },
        look && button('框選範圍', 'roi'),
        watchChoice && button(job.look.watch ? '停止盯著' : '持續盯著', 'watch'),
        button(paused ? '恢復' : '暫停', 'toggle'),
        button('刪除', 'delete', 'quiet danger')));
  };
  const byTime = (a, b) => (a.next_run_at || '9').localeCompare(b.next_run_at || '9');
  for (const [group, mine] of [['short', jobs.filter(job => SHORT_KINDS.includes(job.kind))], ['timed', jobs.filter(job => !SHORT_KINDS.includes(job.kind))]]) {
    $(`#tasks-${group}`).hidden = !mine.length;
    $(`#tasks-${group} .count`).textContent = mine.length;
    $(`#looks-${group}`).replaceChildren(...mine.sort(byTime).map(card));
  }
}
async function lookAction(job, action) {
  const path = `/api/v1/devices/${encodeURIComponent(activeDevice)}/schedules/${job.id}`;
  try {
    if (action === 'roi') { openRoi(job); return; }
    if (action === 'watch') await request(`${path}/look`, { method: 'PUT', body: JSON.stringify({ watch: !job.look.watch }) });
    if (action === 'toggle') await request(path, { method: 'PATCH', body: JSON.stringify({ status: job.status === 'paused' ? 'active' : 'paused' }) });
    if (action === 'delete') {
      const records = job.purpose === 'look' ? '\n這個任務的拍照紀錄也會一起刪除。' : '';
      if (!confirm(`要刪除「${job.description}」嗎？${records}`)) return;
      await request(path, { method: 'DELETE' });
      if (job.purpose === 'look') await references.remove(job.id);
    }
    await syncLooks(activeDevice);
  } catch (error) { toast(error.message); }
}
// Region editor: drag on the live preview. The preview is mirrored like a selfie, while
// regions are stored in the raw camera frame, so x is flipped on the way in and out.
let roiJob = null, roiRect = null, roiStart = null;
const roiBox = $('#roi-box');
function drawRoi() {
  roiBox.hidden = !roiRect;
  if (roiRect) Object.assign(roiBox.style, { left: `${roiRect.x * 100}%`, top: `${roiRect.y * 100}%`, width: `${roiRect.w * 100}%`, height: `${roiRect.h * 100}%` });
}
function openRoi(job) {
  if (!camera.active) { toast('先打開鏡頭，才能框選範圍。'); return; }
  roiJob = job;
  const roi = job.look?.roi;
  roiRect = roi ? { x: 1 - roi.x - roi.w, y: roi.y, w: roi.w, h: roi.h } : null;
  $('#roi-stage').prepend(camera.video); drawRoi(); $('#roi-dialog').showModal();
}
$('#roi-dialog').addEventListener('close', () => { $('#camera-preview').appendChild(camera.video); roiJob = null; });
const roiPoint = event => { const box = $('#roi-stage').getBoundingClientRect(); return { x: Math.min(1, Math.max(0, (event.clientX - box.left) / box.width)), y: Math.min(1, Math.max(0, (event.clientY - box.top) / box.height)) }; };
$('#roi-stage').addEventListener('pointerdown', event => { roiStart = roiPoint(event); $('#roi-stage').setPointerCapture(event.pointerId); });
$('#roi-stage').addEventListener('pointermove', event => {
  if (!roiStart) return;
  const point = roiPoint(event);
  roiRect = { x: Math.min(roiStart.x, point.x), y: Math.min(roiStart.y, point.y), w: Math.abs(point.x - roiStart.x), h: Math.abs(point.y - roiStart.y) };
  drawRoi();
});
$('#roi-stage').addEventListener('pointerup', () => { roiStart = null; if (roiRect && (roiRect.w < .05 || roiRect.h < .05)) { roiRect = null; drawRoi(); } });
async function saveRoi(roi) {
  try {
    const job = roiJob;
    await request(`/api/v1/devices/${encodeURIComponent(activeDevice)}/schedules/${job.id}/look`, { method: 'PUT', body: JSON.stringify({ roi }) });
    // A change task compares within its region, so a new region starts a new reference.
    if (job.look?.mode === 'change') { await references.remove(job.id); runner?.forget(job.id); }
    $('#roi-dialog').close(); toast(roi ? '已更新拍照範圍。' : '改回拍整個畫面。');
    syncLooks(activeDevice).catch(() => {});
  } catch (error) { toast(error.message); }
}
const round = value => Math.round(value * 1000) / 1000;
$('#roi-save').onclick = () => saveRoi(roiRect ? { x: round(Math.max(0, 1 - roiRect.x - roiRect.w)), y: round(roiRect.y), w: round(roiRect.w), h: round(roiRect.h) } : null);
$('#roi-full').onclick = () => saveRoi(null);
$('#roi-cancel').onclick = () => $('#roi-dialog').close();

say('嗨，我是 MEMO。\n我就在這裡，陪你看看今天的空間。');
if (apiBase) {
  try { apiBase = normalizeUrl(apiBase); refresh(); startLooks(); }
  catch (error) { apiBase = ''; connection(false, 'VITE_API_URL 無效'); toast(error.message); }
} else connection(false, '未設定 VITE_API_URL');
syncDisturbance();
if (cameraWanted) cameraOn({ quiet: true });
const interval = setInterval(() => { if (!document.hidden) { refresh(); syncDisturbance(); } }, 5000);
window.addEventListener('pagehide', () => { clearInterval(interval); companion?.dispose(); });
