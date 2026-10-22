import './style.css';
import { createMemoScene } from './scene.js';

const $ = selector => document.querySelector(selector);
const storage = { get(key) { try { return localStorage.getItem(key); } catch { return null; } }, set(key, value) { try { localStorage.setItem(key, value); } catch { /* Private browsers may block storage. */ } } };
let apiBase = storage.get('memo.apiBase') || '', activeDevice = '', readings = [], devices = [];
let refreshing = false, chatting = false, generation = 0, toastTimer, connected = false;
let companion;
try { companion = createMemoScene($('#scene')); } catch (error) { $('#webgl-fallback').hidden = false; console.warn('3D unavailable', error.message); }
if (companion?.paused) { $('#pause-motion').textContent = '▷'; $('#pause-motion').setAttribute('aria-label', '恢復動態'); }
$('#reset-camera').onclick = () => companion?.reset();
$('#pause-motion').onclick = () => { const paused = companion?.toggleMotion(); $('#pause-motion').textContent = paused ? '▷' : 'Ⅱ'; $('#pause-motion').setAttribute('aria-label', paused ? '恢復動態' : '暫停動態'); };

function toast(message) { clearTimeout(toastTimer); $('#toast').textContent = message; $('#toast').hidden = false; toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 5500); }
function connection(ok, label) { connected = ok; $('#connection-dot').classList.toggle('online', ok); $('#connection-label').textContent = label; }
function normalizeUrl(value) {
  const url = new URL(value.trim());
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== '/') throw new Error('請填完整的 HTTP(S) 來源網址，不包含路徑、帳密或查詢參數。');
  if (location.protocol === 'https:' && url.protocol === 'http:') throw new Error('此網站使用 HTTPS，請使用 HTTPS 後端網址。');
  return url.origin;
}
async function request(path, options = {}, base = apiBase) {
  if (!base) throw new Error('請先連接你的 MEMO 後端。');
  let response;
  try { response = await fetch(`${base}${path}`, { ...options, signal: AbortSignal.timeout(12000), headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } }); }
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
  values.forEach((value, index) => { if (value === null) { drawing = false; return; } path += `${drawing ? 'L' : 'M'}${index / (values.length - 1) * 96 + 2},${26 - (value - min) / span * 18} `; drawing = true; });
  const element = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  element.setAttribute('d', path); element.setAttribute('fill', 'none'); element.setAttribute('stroke', key === 'temperature_c' ? '#bcaa79' : key === 'humidity_pct' ? '#8da9b2' : '#9baa77'); element.setAttribute('stroke-width', '1.5'); svg.appendChild(element);
}
function renderReadings() {
  const latest = readings[0];
  const fresh = freshness(latest);
  $('#freshness').textContent = fresh; $('#freshness').classList.toggle('live', fresh === '剛剛更新' && connected);
  for (const [key, id] of [['temperature_c', 'temperature-value'], ['humidity_pct', 'humidity-value'], ['co2_ppm', 'co2-value']]) {
    const value = latest?.[key]; $(`#${id}`).textContent = value == null ? '—' : key === 'temperature_c' ? Number(value).toFixed(1) : Math.round(value).toLocaleString(); drawSparkline(key);
  }
  if (latest) {
    const time = new Date(latest.observed_at).toLocaleString('zh-TW', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    $('#data-note').textContent = `${activeDevice === 'memo-web-demo' ? '模擬裝置 · ' : ''}${fresh === '剛剛更新' ? '最近讀值' : '歷史讀值，非即時狀態'} · ${time}`;
  } else $('#data-note').textContent = activeDevice ? '這個裝置還沒有讀值。資料到達後會自動更新。' : '連接你的後端，看看空間裡的小變化。';
}
async function refresh(showErrors = false) {
  if (!apiBase || refreshing) return;
  refreshing = true; const currentGeneration = generation;
  try {
    const result = await request('/api/v1/devices?limit=500');
    if (currentGeneration !== generation) return;
    devices = result;
    if (!devices.some(d => d.id === activeDevice)) activeDevice = devices.find(d => d.id === 'room01')?.id || devices[0]?.id || '';
    $('#device').replaceChildren();
    if (!devices.length) $('#device').add(new Option('尚無裝置 · 可試用模擬工具', ''));
    for (const device of devices) $('#device').add(new Option(`${device.name} · ${device.id}`, device.id));
    $('#device').value = activeDevice;
    const selected = activeDevice;
    const resultReadings = selected ? await request(`/api/v1/devices/${encodeURIComponent(selected)}/readings?limit=24`) : [];
    if (currentGeneration !== generation || selected !== activeDevice) return;
    readings = resultReadings; connection(true, '已連接後端'); renderReadings();
  } catch (error) { if (currentGeneration !== generation) return; connection(false, '後端連線中斷'); $('#freshness').classList.remove('live'); $('#freshness').textContent = '連線中斷'; $('#data-note').textContent = '連線中斷，畫面保留最後一次讀值。'; if (showErrors) toast(error.message); }
  finally { refreshing = false; }
}
$('#device').onchange = () => { activeDevice = $('#device').value; generation++; readings = []; renderReadings(); refresh(true); };
$('#refresh-data').onclick = () => apiBase ? refresh(true) : $('#settings-button').click();
$('#settings-button').onclick = () => { $('#api-url').value = apiBase; $('#settings-error').textContent = ''; $('#settings-dialog').showModal(); };
$('#close-settings').onclick = () => $('#settings-dialog').close();
$('#settings-form').onsubmit = async event => {
  event.preventDefault(); $('#settings-error').textContent = ''; $('#connect-button').disabled = true;
  try {
    const base = normalizeUrl($('#api-url').value);
    const health = await request('/health/ready', {}, base);
    if (health.status !== 'ready') throw new Error('後端尚未就緒。');
    apiBase = base; generation++; activeDevice = ''; readings = []; storage.set('memo.apiBase', base);
    $('#settings-dialog').close(); await refresh(true); toast('已保存連線設定。');
  } catch (error) { $('#settings-error').textContent = error.message; }
  finally { $('#connect-button').disabled = false; }
};
$('#disconnect').onclick = () => { apiBase = ''; activeDevice = ''; readings = []; generation++; storage.set('memo.apiBase', ''); connection(false, '尚未連接後端'); $('#device').replaceChildren(new Option('尚未連接裝置', '')); renderReadings(); $('#settings-dialog').close(); };

function message(text, kind, caption = '') {
  const row = document.createElement('div'); row.className = `message ${kind}`;
  if (kind === 'memo') { const avatar = document.createElement('span'); avatar.className = 'avatar'; avatar.textContent = 'm'; row.appendChild(avatar); }
  const content = document.createElement('div'), p = document.createElement('p'); p.textContent = text; content.appendChild(p);
  if (caption) { const small = document.createElement('small'); small.textContent = caption; content.appendChild(small); }
  row.appendChild(content); $('#messages').appendChild(row); $('#messages').scrollTop = $('#messages').scrollHeight;
  return row;
}
const moodLabels = { neutral: '安靜陪伴', friendly: '溫柔陪伴', happy: '開心', concerned: '有點關心', confused: '還想多了解' };
async function chat(text) {
  if (chatting) return;
  if (!apiBase) { $('#settings-button').click(); return; }
  if (!activeDevice) { toast('請先選擇裝置，或送出一筆模擬讀值。'); return; }
  const selected = activeDevice, currentGeneration = generation;
  chatting = true; $('#send-message').disabled = true; $('#ask-summary').disabled = true;
  message(text, 'user'); $('#chat-input').value = '';
  const waiting = message('讓我看看你空間裡的資料…', 'memo');
  try {
    const reply = await request(`/api/v1/devices/${encodeURIComponent(selected)}/interaction`, { method: 'POST', body: JSON.stringify({ user_message: text, personality: { tone: $('#tone').value } }) });
    waiting.remove();
    if (generation !== currentGeneration) { message('裝置已切換，已略過上一個裝置的回覆。', 'memo'); return; }
    message(reply.text, 'memo', `${reply.source === 'template' ? '模板回覆' : '模型回覆'} · 後端資料庫 · ${selected}`);
    const label = moodLabels[reply.emotion.label] || moodLabels.neutral;
    $('#mood-label').textContent = label; $('#mood-face').textContent = reply.emotion.label === 'confused' ? '?' : '◡';
    companion?.setEmotion(reply.emotion.label, reply.emotion.intensity);
  } catch (error) { waiting.remove(); message(error.message, 'memo', '未取得回覆'); }
  finally { chatting = false; $('#send-message').disabled = false; $('#ask-summary').disabled = false; }
}
$('#chat-form').onsubmit = event => { event.preventDefault(); const text = $('#chat-input').value.trim(); if (text) chat(text); };
$('#ask-summary').onclick = () => chat('MEMO，現在的環境如何？');
$('#simulation-toggle').onclick = () => { const expanded = $('#simulation-body').hidden; $('#simulation-body').hidden = !expanded; $('#simulation-toggle').setAttribute('aria-expanded', String(expanded)); $('#simulation-arrow').textContent = expanded ? '−' : '＋'; };
for (const [name, unit] of [['temperature', '°C'], ['humidity', '%'], ['co2', 'ppm']]) $(`#sim-${name}`).oninput = e => { $(`#sim-${name}-output`).textContent = `${e.target.value} ${unit}`; };
$('#publish-simulation').onclick = async () => {
  if (!apiBase) { $('#settings-button').click(); return; }
  const button = $('#publish-simulation'); button.disabled = true;
  const startGeneration = generation;
  try {
    try { await request('/api/v1/devices', { method: 'POST', body: JSON.stringify({ id: 'memo-web-demo', name: 'MEMO 網頁模擬', location: '獨立示範空間' }) }); }
    catch (error) { if (error.status !== 409) throw error; }
    if (startGeneration !== generation) return;
    await request('/api/v1/telemetry', { method: 'POST', body: JSON.stringify({ schema_version: 1, message_id: `web-${crypto.randomUUID()}`, device_id: 'memo-web-demo', observed_at: new Date().toISOString(), sequence: 1, quality: 'ok', metrics: { temperature_c: Number($('#sim-temperature').value), humidity_pct: Number($('#sim-humidity').value), co2_ppm: Number($('#sim-co2').value) } }) });
    if (startGeneration !== generation) return;
    activeDevice = 'memo-web-demo'; generation++; await refresh();
    toast('模擬讀值已寫入後端。'); await chat('MEMO，請摘要剛剛的模擬環境資料。');
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; }
};
if (apiBase) refresh();
const interval = setInterval(() => { if (!document.hidden) refresh(); }, 5000);
window.addEventListener('pagehide', () => { clearInterval(interval); companion?.dispose(); });
