import './style.css';
import { createMemoScene } from './scene.js';
import { createSpeech } from './speech.js';

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
};
const icon = name => `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;
for (const el of document.querySelectorAll('[data-icon]')) el.innerHTML = icon(el.dataset.icon);
for (const [id, name] of [['reset-camera', 'replay'], ['pause-motion', 'pause'], ['send-message', 'send'], ['mic', 'mic'], ['attach', 'image'], ['new-chat', 'newChat']]) $(`#${id}`).innerHTML = icon(name);
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
const speech = speechBase ? createSpeech(speechBase, value => { talkingNow = value; companion?.setTalking(value); }) : null;
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
function say(text, { quote = '', waiting = false, voice = false, sources = [] } = {}) {
  // Anything new on screen interrupts what MEMO was saying; only replies and notices are spoken.
  if (voice && voiceOn) speech?.speak(text); else speech?.stop();
  $('#bubble-quote').textContent = quote; $('#bubble-quote').hidden = !quote;
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
  try { response = await fetch(`${base}${path}`, { ...options, signal: AbortSignal.timeout(/\/(interaction|digest)$/.test(path) ? 45000 : 12000), headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } }); }
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
const moodLabels = { neutral: '安靜陪伴', friendly: '溫柔陪伴', happy: '開心', concerned: '有點擔心', confused: '有點疑惑', surprised: '嚇一跳', proud: '專業模式', sleepy: '有點睏', alert: '警戒中', error: '出錯了', thinking: '思考中', listening: '在聽你說' };
const moodFaces = { neutral: '◡', friendly: '◡', happy: '▽', concerned: '︵', confused: '?', surprised: '!', proud: '✦', sleepy: 'z', alert: '!', error: '×', thinking: '…', listening: '♪' };
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
async function chat(text) {
  if (chatting) return;
  if (missingApi()) return;
  const selected = activeDevice, currentGeneration = generation, image = attachment;
  chatting = true; $('#send-message').disabled = true;
  $('#chat-input').value = ''; setAttachment(null);
  if (image) text ||= '幫我看看這張圖';
  const quote = image ? `${text}（附圖）` : text;
  say('讓我想想…', { quote, waiting: true });
  showEmotion({ label: 'thinking', intensity: .5 });
  try {
    const reply = await request(`/api/v1/devices/${encodeURIComponent(selected)}/interaction`, { method: 'POST', body: JSON.stringify({ session_id: sessionId, request_id: crypto.randomUUID(), user_message: text, personality: personality(), ...(image ? { image } : {}) }) });
    if (generation !== currentGeneration) { say('裝置已切換，已略過上一個裝置的回覆。'); return; }
    say(reply.text, { quote, voice: true, sources: reply.sources || [] });
    showEmotion(reply.emotion);
  } catch (error) { say(error.message, { quote }); showEmotion({ label: 'error', intensity: .5 }); }
  finally { chatting = false; $('#send-message').disabled = false; }
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
function showEmotion(emotion) {
  $('#mood-label').textContent = moodLabels[emotion.label] || moodLabels.neutral;
  $('#mood-face').textContent = moodFaces[emotion.label] || moodFaces.neutral;
  companion?.setEmotion(emotion.label, emotion.intensity);
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
  const tasks = await request(`/api/v1/devices/${encodeURIComponent(device)}/notifications`);
  if (generation !== currentGeneration || activeDevice !== device) return;
  for (const task of tasks) {
    if (!seenNotifications.has(task.id)) {
      announce(task.response.text, task.response.emotion); seenNotifications.add(task.id);
    }
    try { await request(`/api/v1/devices/${encodeURIComponent(device)}/notifications/ack`, { method: 'POST', body: JSON.stringify({ task_id: task.id }) }); }
    catch (error) { if (error.status !== 409) throw error; }
  }
  storage.set('memo.lastSeen', String(Date.now()));
}
say('嗨，我是 MEMO。\n我就在這裡，陪你看看今天的空間。');
if (apiBase) {
  try { apiBase = normalizeUrl(apiBase); refresh(); }
  catch (error) { apiBase = ''; connection(false, 'VITE_API_URL 無效'); toast(error.message); }
} else connection(false, '未設定 VITE_API_URL');
syncDisturbance();
const interval = setInterval(() => { if (!document.hidden) { refresh(); syncDisturbance(); } }, 5000);
window.addEventListener('pagehide', () => { clearInterval(interval); companion?.dispose(); });
