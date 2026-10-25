// One camera stream for the whole page: MEMO's eyes, expressions and waving read it, and
// photos (asked for in chat, or by look tasks) are taken from it. Nothing leaves the page
// except a photo MEMO was asked to take, and that one is never stored by the backend.
import { cropRect, measureQuality } from './perception.js';

const PHOTO_SIDE = 1280, PHOTO_LIMIT = 2 * 1024 * 1024, QUALITY_SIDE = 256;

export function createCamera({ onChange = () => {} } = {}) {
  const video = Object.assign(document.createElement('video'), { muted: true, playsInline: true, autoplay: true });
  video.setAttribute('aria-hidden', 'true');
  let stream = null, capture = null, state = 'off', starting = null;
  const set = value => { state = value; onChange(value); };

  async function start() {
    if (stream) return true;
    if (starting) return starting;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) { set('insecure'); return false; }
    starting = (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' }, audio: false });
      } catch (error) {
        set(error.name === 'NotAllowedError' || error.name === 'SecurityError' ? 'denied' : 'unavailable');
        return false;
      } finally { starting = null; }
      const [track] = stream.getVideoTracks();
      // The camera unplugged or taken by another app ends the track.
      track.addEventListener('ended', () => { release(); set('unavailable'); });
      // ImageCapture reads the track itself, so photos still work while the tab is hidden.
      capture = 'ImageCapture' in window ? new ImageCapture(track) : null;
      video.srcObject = stream;
      await video.play().catch(() => {});
      set('on');
      return true;
    })();
    return starting;
  }
  function release() {
    stream?.getTracks().forEach(track => track.stop());
    stream = capture = null; video.srcObject = null;
  }
  function stop() { release(); set('off'); }

  async function frame() {
    if (!stream) throw new Error('鏡頭沒有開。');
    if (capture) { try { return await capture.grabFrame(); } catch { /* fall back to the video element */ } }
    if (video.readyState < 2) await new Promise(resolve => video.addEventListener('loadeddata', resolve, { once: true }));
    return video;
  }

  // A JPEG of the frame (or the part `roi` names, as fractions of the raw frame) with its
  // measured quality. Poor photos are still sent and recorded, never silently retaken.
  async function photo({ roi = null, maxSide = PHOTO_SIDE } = {}) {
    const source = await frame(), takenAt = new Date();
    const width = source.videoWidth || source.width, height = source.videoHeight || source.height;
    const { sx, sy, sw, sh } = cropRect(roi, width, height);
    const scale = Math.min(1, maxSide / Math.max(sw, sh));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(sw * scale); canvas.height = Math.round(sh * scale);
    canvas.getContext('2d').drawImage(source, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
    const small = document.createElement('canvas'), shrink = Math.min(1, QUALITY_SIDE / Math.max(canvas.width, canvas.height));
    small.width = Math.max(3, Math.round(canvas.width * shrink)); small.height = Math.max(3, Math.round(canvas.height * shrink));
    const context = small.getContext('2d', { willReadFrequently: true });
    context.drawImage(canvas, 0, 0, small.width, small.height);
    const quality = { ...measureQuality(context.getImageData(0, 0, small.width, small.height).data, small.width, small.height), width: canvas.width, height: canvas.height };
    source.close?.();
    for (const level of [.85, .7, .5]) {
      const image = canvas.toDataURL('image/jpeg', level);
      if (image.length * .75 <= PHOTO_LIMIT) return { image, quality, takenAt };
    }
    throw new Error('照片太大了。');
  }

  // A tiny grey copy of the frame (or of `roi`) for watching changes; never leaves the page.
  const sampler = document.createElement('canvas');
  async function sample({ roi = null, width = 64 } = {}) {
    const source = await frame();
    const fullWidth = source.videoWidth || source.width, fullHeight = source.videoHeight || source.height;
    const { sx, sy, sw, sh } = cropRect(roi, fullWidth, fullHeight);
    sampler.width = width; sampler.height = Math.max(4, Math.round(width * sh / sw));
    const context = sampler.getContext('2d', { willReadFrequently: true });
    context.drawImage(source, sx, sy, sw, sh, 0, 0, sampler.width, sampler.height);
    source.close?.();
    const rgba = context.getImageData(0, 0, sampler.width, sampler.height).data;
    const grey = new Float32Array(sampler.width * sampler.height);
    for (let i = 0; i < grey.length; i++) grey[i] = .299 * rgba[i * 4] + .587 * rgba[i * 4 + 1] + .114 * rgba[i * 4 + 2];
    return grey;
  }

  return {
    video, start, stop, photo, sample,
    get state() { return state; },
    get active() { return Boolean(stream); },
  };
}
