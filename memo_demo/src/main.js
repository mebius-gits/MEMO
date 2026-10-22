import './style.css';
import { createMemoScene } from './scene.js';

const $ = selector => document.querySelector(selector);
const ICONS = {
  thermo: '<path d="M10 14.5V5a2 2 0 0 1 4 0v9.5a4 4 0 1 1-4 0z"/>',
  drop: '<path d="M12 3c3 4 6 7 6 10.5a6 6 0 0 1-12 0C6 10 9 7 12 3z"/>',
  wind: '<path d="M3 9h11a3 3 0 1 0-3-3M3 14h15a3 3 0 1 1-3 3M3 19h7"/>',
  chat: '<path d="M4 5h16v11H9l-5 4z"/>',
  sliders: '<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
  replay: '<path d="M4 12a8 8 0 1 0 3-6.2M4 4v5h5"/>',
  pause: '<path d="M9 5v14M15 5v14"/>',
  play: '<path d="M7 4l13 8-13 8z"/>',
  send: '<path d="M12 19V5M5 12l7-7 7 7"/>',
};
const icon = name => `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;
const AVATAR = '<svg viewBox="-6 -6 192 222" aria-hidden="true"><rect width="180" height="206" rx="22" fill="#69CDB4" stroke="#0F2F2A" stroke-width="10"/><rect x="18" y="18" width="144" height="104" rx="10" fill="#C4F0DB" stroke="#0F2F2A" stroke-width="8"/><circle cx="66" cy="64" r="10" fill="#12332C"/><circle cx="114" cy="64" r="10" fill="#12332C"/><path d="M68 88 Q90 106 112 88" fill="none" stroke="#12332C" stroke-width="8" stroke-linecap="round"/><rect x="40" y="150" width="34" height="12" rx="4" fill="#F4D03F"/><circle cx="140" cy="158" r="12" fill="#E4574E"/></svg>';
for (const el of document.querySelectorAll('[data-icon]')) el.innerHTML = icon(el.dataset.icon);
for (const [id, name] of [['reset-camera', 'replay'], ['pause-motion', 'pause'], ['send-message', 'send']]) $(`#${id}`).innerHTML = icon(name);
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

function selectTab(name) {
  for (const tab of ['chat', 'params']) {
    const active = tab === name;
    $(`#tab-${tab}`).setAttribute('aria-selected', String(active)); $(`#tab-${tab}`).tabIndex = active ? 0 : -1; $(`#panel-${tab}`).hidden = !active;
  }
  if (name === 'chat') { $('#chat-badge').hidden = true; $('#panel-chat').scrollTop = $('#panel-chat').scrollHeight; }
}
$('#tab-chat').onclick = () => selectTab('chat');
$('#tab-params').onclick = () => selectTab('params');
$('.tabs').onkeydown = event => { if (['ArrowLeft', 'ArrowRight'].includes(event.key)) { const next = $('#panel-chat').hidden ? 'chat' : 'params'; selectTab(next); $(`#tab-${next}`).focus(); } };

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
  try { response = await fetch(`${base}${path}`, { ...options, signal: AbortSignal.timeout(path.endsWith('/interaction') ? 45000 : 12000), headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } }); }
  catch { throw new Error('無法連接後端。請檢查網址、HTTPS、CORS 或網路連線。'); }
  if (!response.ok) {
    const error = new Error(response.status === 404 ? '找不到這個裝置或 API，請確認後端已更新。' : `後端回應錯誤（${response.status}），請稍後再試。`);
    error.status = response.status; throw error;
  }
  return response.json();
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
  element.setAttribute('d', path); element.setAttribute('fill', 'none'); element.setAttribute('stroke', key === 'temperature_c' ? '#D9A400' : key === 'humidity_pct' ? '#3D86E0' : '#2F8F7B'); element.setAttribute('stroke-width', '2'); element.setAttribute('stroke-linejoin', 'round'); element.setAttribute('vector-effect', 'non-scaling-stroke'); svg.appendChild(element);
}
function renderReadings() {
  const latest = readings[0];
  const fresh = freshness(latest);
  $('#freshness').textContent = fresh; $('#freshness').classList.toggle('live', fresh === '剛剛更新' && connected);
  for (const [key, id] of [['temperature_c', 'temperature-value'], ['humidity_pct', 'humidity-value'], ['co2_ppm', 'co2-value']]) {
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
    await syncCompanion(selected, currentGeneration);
  } catch (error) { if (currentGeneration !== generation) return; connection(false, '後端連線中斷'); $('#freshness').classList.remove('live'); $('#freshness').textContent = '連線中斷'; if (showErrors) toast(error.message); }
  finally { refreshing = false; }
}
const missingApi = () => { if (!apiBase) toast('尚未設定 VITE_API_URL，請設定後重新啟動 Vite。'); return !apiBase; };

function message(text, kind) {
  const row = document.createElement('div'); row.className = `message ${kind}`;
  if (kind.startsWith('memo')) { const avatar = document.createElement('span'); avatar.className = 'avatar'; avatar.innerHTML = AVATAR; row.appendChild(avatar); }
  const content = document.createElement('div'), p = document.createElement('p'); p.textContent = text; content.appendChild(p);
  row.appendChild(content); $('#messages').appendChild(row); $('#panel-chat').scrollTop = $('#panel-chat').scrollHeight;
  if (kind === 'memo' && $('#panel-chat').hidden) $('#chat-badge').hidden = false;
  return row;
}
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
const moodLabels = { neutral: '安靜陪伴', friendly: '溫柔陪伴', happy: '開心', concerned: '有點關心', confused: '還想多了解' };
const moodFaces = { neutral: '◡', friendly: '◡', happy: '▽', concerned: '︵', confused: '?' };
const chips = () => document.querySelectorAll('.chip');
async function chat(text) {
  if (chatting) return;
  if (missingApi()) return;
  const selected = activeDevice, currentGeneration = generation;
  chatting = true; $('#send-message').disabled = true; chips().forEach(chip => { chip.disabled = true; });
  message(text, 'user'); $('#chat-input').value = '';
  const waiting = message('讓我看看你空間裡的資料…', 'memo waiting');
  try {
    const reply = await request(`/api/v1/devices/${encodeURIComponent(selected)}/interaction`, { method: 'POST', body: JSON.stringify({ session_id: sessionId, request_id: crypto.randomUUID(), user_message: text, personality: personality() }) });
    waiting.remove();
    if (generation !== currentGeneration) { message('裝置已切換，已略過上一個裝置的回覆。', 'memo'); return; }
    message(reply.text, 'memo');
    showEmotion(reply.emotion);
  } catch (error) { waiting.remove(); message(error.message, 'memo'); }
  finally { chatting = false; $('#send-message').disabled = false; chips().forEach(chip => { chip.disabled = false; }); }
}
$('#chat-form').onsubmit = event => { event.preventDefault(); const text = $('#chat-input').value.trim(); if (text) { selectTab('chat'); chat(text); } };
chips().forEach(chip => { chip.onclick = () => { selectTab('chat'); chat(chip.dataset.prompt); }; });
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
    $('#messages').replaceChildren();
    for (const turn of turns) { message(turn.user_text, 'user'); message(turn.response.text, 'memo'); }
    if (!turns.length) message('我在這裡。空間有值得留意的變化時，我會輕輕告訴你。', 'memo');
    conversationScope = scope;
  }
  const tasks = await request(`/api/v1/devices/${encodeURIComponent(device)}/notifications`);
  if (generation !== currentGeneration || activeDevice !== device) return;
  for (const task of tasks) {
    if (!seenNotifications.has(task.id)) {
      message(task.response.text, 'memo');
      showEmotion(task.response.emotion); seenNotifications.add(task.id);
    }
    try { await request(`/api/v1/devices/${encodeURIComponent(device)}/notifications/ack`, { method: 'POST', body: JSON.stringify({ task_id: task.id }) }); }
    catch (error) { if (error.status !== 409) throw error; }
  }
}
message('嗨，我是 MEMO。\n我就在這裡，陪你看看今天的空間。', 'memo');
if (apiBase) {
  try { apiBase = normalizeUrl(apiBase); refresh(); }
  catch (error) { apiBase = ''; connection(false, 'VITE_API_URL 無效'); toast(error.message); }
} else connection(false, '未設定 VITE_API_URL');
syncDisturbance();
const interval = setInterval(() => { if (!document.hidden) { refresh(); syncDisturbance(); } }, 5000);
window.addEventListener('pagehide', () => { clearInterval(interval); companion?.dispose(); });
