// Face and hand tracking on the camera stream, with MediaPipe running in the page. The
// runtime and models are served from /mediapipe/ (scripts/vision-assets.mjs), loaded only
// when the camera is first turned on. Only landmarks and blendshapes come out of here.
const BASE = `${import.meta.env.BASE_URL}mediapipe/`;
// About 12 face readings a second; slower when inference takes long on this machine.
const FAST = 80, SLOW = 160, TOO_SLOW_MS = 30;

let loading = null;
function load() {
  loading ||= (async () => {
    const { FilesetResolver, FaceLandmarker, HandLandmarker } = await import('@mediapipe/tasks-vision');
    const files = await FilesetResolver.forVisionTasks(`${BASE}wasm`);
    // GPU first; machines without WebGL2 for MediaPipe fall back to the CPU.
    const create = async (Task, model, options) => {
      for (const delegate of ['GPU', 'CPU']) {
        try { return await Task.createFromOptions(files, { baseOptions: { modelAssetPath: `${BASE}models/${model}`, delegate }, runningMode: 'VIDEO', ...options }); }
        catch (error) { if (delegate === 'CPU') throw error; }
      }
    };
    const face = await create(FaceLandmarker, 'face_landmarker.task', { numFaces: 1, outputFaceBlendshapes: true });
    let hand = null;
    return { face, hand: async () => (hand ||= await create(HandLandmarker, 'hand_landmarker.task', { numHands: 1 })) };
  })();
  loading.catch(() => { loading = null; });
  return loading;
}

// Calls onFace({ centre, chinY, blendshapes } | null) for each reading and, while wantHands()
// is true, onHand(landmarks | null, chinY). Pauses by itself while the tab is hidden.
export async function startTracking(video, { onFace, onHand, wantHands = () => false }) {
  const trackers = await load();
  let running = true, last = 0, interval = FAST, chinY = null, turn = 0, handTracker = null, waiting = false, scheduledAt = 0;
  // One pending callback at a time. A frame callback can be lost when the camera restarts
  // (the stream is released in the background); the watchdog then starts a new one.
  const schedule = () => {
    if (!running || waiting) return;
    waiting = true; scheduledAt = performance.now();
    if ('requestVideoFrameCallback' in video) video.requestVideoFrameCallback(tick); else requestAnimationFrame(tick);
  };
  const watchdog = setInterval(() => {
    if (running && waiting && !document.hidden && performance.now() - scheduledAt > 3000) { waiting = false; schedule(); }
  }, 1000);
  async function tick() {
    waiting = false;
    if (!running) return;
    const now = performance.now();
    if (document.hidden || video.readyState < 2 || now - last < interval) { schedule(); return; }
    last = now;
    try {
      const started = performance.now();
      const result = trackers.face.detectForVideo(video, now);
      const points = result.faceLandmarks?.[0];
      if (points) {
        const xs = points.map(p => p.x), ys = points.map(p => p.y);
        chinY = points[152]?.y ?? Math.max(...ys);
        const blendshapes = Object.fromEntries((result.faceBlendshapes?.[0]?.categories || []).map(c => [c.categoryName, c.score]));
        onFace({ centre: { x: (Math.min(...xs) + Math.max(...xs)) / 2, y: (Math.min(...ys) + Math.max(...ys)) / 2 }, chinY, blendshapes }, now);
      } else { chinY = null; onFace(null, now); }
      // Hands every other reading, and only while MEMO could wave back.
      if (wantHands() && turn++ % 2 === 0) {
        handTracker ||= await trackers.hand();
        onHand(handTracker.detectForVideo(video, now + 1).landmarks?.[0] || null, chinY, now);
      }
      interval = performance.now() - started > TOO_SLOW_MS ? SLOW : FAST;
    } catch (error) { console.warn('vision tracking', error.message); interval = SLOW; }
    schedule();
  }
  schedule();
  return { stop() { running = false; clearInterval(watchdog); } };
}
