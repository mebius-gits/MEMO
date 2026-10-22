// Client for the local speech service (SenseVoice STT, Kokoro TTS).
// Recordings are converted to 16 kHz mono WAV here, so the service needs no audio codecs.
const RATE = 16000;

function encodeWav(samples) {
  const view = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const text = (offset, value) => [...value].forEach((char, i) => view.setUint8(offset + i, char.charCodeAt(0)));
  text(0, 'RIFF'); view.setUint32(4, 36 + samples.length * 2, true); text(8, 'WAVE');
  text(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, RATE, true); view.setUint32(28, RATE * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, 'data'); view.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, i) => view.setInt16(44 + i * 2, Math.max(-1, Math.min(1, sample)) * 0x7fff, true));
  return new Blob([view], { type: 'audio/wav' });
}

async function toWav(blob) {
  const context = new AudioContext();
  const decoded = await context.decodeAudioData(await blob.arrayBuffer()).finally(() => context.close());
  const offline = new OfflineAudioContext(1, Math.max(1, Math.ceil(decoded.duration * RATE)), RATE);
  const source = offline.createBufferSource();
  source.buffer = decoded; source.connect(offline.destination); source.start();
  return encodeWav((await offline.startRendering()).getChannelData(0));
}

// Split into sentences so playback can start after the first one is synthesised.
const sentences = text => (text.match(/[^。！？!?\n]+[。！？!?]*/g) || []).map(part => part.trim().slice(0, 500)).filter(Boolean);

export function createSpeech(base, onTalking = () => {}) {
  const audio = new Audio();
  let token = 0;
  async function post(path, body, headers) {
    let response;
    try { response = await fetch(`${base}${path}`, { method: 'POST', headers, body, signal: AbortSignal.timeout(60000) }); }
    catch { throw new Error('無法連接語音服務。請確認 speech 容器已啟動。'); }
    if (!response.ok) throw new Error(`語音服務回應錯誤（${response.status}）。`);
    return response;
  }
  const synthesize = text => post('/tts', JSON.stringify({ text }), { 'Content-Type': 'application/json' })
    .then(async response => URL.createObjectURL(await response.blob()))
    .catch(() => null);
  function stop() { token++; audio.pause(); audio.removeAttribute('src'); onTalking(false); }
  return {
    stop,
    // Speak sentence by sentence, prefetching the next while the current one plays; a new call interrupts.
    async speak(text) {
      stop();
      const mine = token, parts = sentences(text);
      let next = parts.length ? synthesize(parts[0]) : null;
      for (let i = 0; i < parts.length; i++) {
        const url = await next;
        next = i + 1 < parts.length ? synthesize(parts[i + 1]) : null;
        if (mine !== token) { if (url) URL.revokeObjectURL(url); next?.then(u => u && URL.revokeObjectURL(u)); return; }
        if (!url) continue;
        audio.src = url; onTalking(true);
        try {
          await audio.play();
          await new Promise(resolve => { audio.onended = resolve; audio.onpause = resolve; });
        } catch { next?.then(u => u && URL.revokeObjectURL(u)); break; /* Autoplay blocked until the page gets a click. */ }
        finally { URL.revokeObjectURL(url); }
      }
      if (mine === token) onTalking(false);
    },
    async record() {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
      const recorder = new MediaRecorder(stream), chunks = [];
      recorder.ondataavailable = event => chunks.push(event.data);
      recorder.start();
      return {
        stop: () => new Promise((resolve, reject) => {
          recorder.onstop = () => { stream.getTracks().forEach(track => track.stop()); toWav(new Blob(chunks, { type: recorder.mimeType })).then(resolve, reject); };
          recorder.stop();
        }),
      };
    },
    async transcribe(wav) { return (await post('/stt', wav, { 'Content-Type': 'audio/wav' })).json(); },
  };
}
