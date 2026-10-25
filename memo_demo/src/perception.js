// What MEMO makes of the camera, as plain functions with no DOM or camera: where its eyes
// look, which expression the person shows, whether they wave, which face the screen shows,
// how good a photo is, and which chat messages mean "look at this". Fed with MediaPipe
// results by vision.js; tested on its own in tests/perception.test.mjs.

// ---- Eyes --------------------------------------------------------------------------------

// Pupils move up to maxX / maxY screen pixels; small head moves inside the dead zone are
// ignored, and positions snap to `step` pixels so the face redraws only when it shows.
export const GAZE = { maxX: 40, maxY: 28, deadZone: .06, gain: 2.2, rate: 6, step: 4 };

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
const snap = value => Math.round(value / GAZE.step) * GAZE.step;

export function createGaze() {
  let x = 0, y = 0, targetX = 0, targetY = 0;
  return {
    // centre: the face's centre in the raw camera frame (0..1), or null when no one is seen.
    target(centre) {
      if (!centre) { targetX = targetY = 0; return; }
      // The raw frame is what the camera sees: a face on its left is on the person's right,
      // which is MEMO's screen-right as the person looks at it. Hence 0.5 - x.
      const dx = (.5 - centre.x) * GAZE.gain, dy = (centre.y - .5) * GAZE.gain;
      const dead = value => Math.abs(value) < GAZE.deadZone ? 0 : value - Math.sign(value) * GAZE.deadZone;
      targetX = clamp(dead(dx), -1, 1); targetY = clamp(dead(dy), -1, 1);
    },
    // Eases toward the target; returns the pupil offset in screen pixels.
    update(seconds) {
      const k = 1 - Math.exp(-GAZE.rate * seconds);
      x += (targetX - x) * k; y += (targetY - y) * k;
      return { x: snap(x * GAZE.maxX), y: snap(y * GAZE.maxY) };
    },
  };
}

// ---- Expressions -------------------------------------------------------------------------

const mean = (...values) => values.reduce((sum, value) => sum + (value ?? 0), 0) / values.length;
// Checked in this order; the first that clears its threshold wins. Scores use blendshapes
// measured against the person's own resting face (see createExpressionReader).
export const EXPRESSIONS = {
  surprised: { score: b => Math.min(b.browInnerUp ?? 0, Math.max(b.jawOpen ?? 0, mean(b.eyeWideLeft, b.eyeWideRight))), enter: .45, exit: .25, hold: 400 },
  smiling: { score: b => mean(b.mouthSmileLeft, b.mouthSmileRight), enter: .5, exit: .3, hold: 1000 },
  frowning: { score: b => mean(b.browDownLeft, b.browDownRight), enter: .45, exit: .25, hold: 1500 },
  tired: { score: b => mean(b.eyeBlinkLeft, b.eyeBlinkRight), enter: .45, exit: .3, hold: 20000 },
};
const NEUTRAL_HOLD = 2000;
// A yawn (mouth wide open, eyes not wide) for 1.5 s counts as tired for the next 30 s.
const YAWN = { jaw: .6, hold: 1500, lasts: 30000 };
// How MEMO answers each expression on its screen: not copying a frown, but concern.
export const MIRROR = { smiling: 'happy', surprised: 'surprised', frowning: 'concerned', tired: 'sleepy' };

export function createExpressionReader() {
  const baseline = {};
  let samples = 0, state = 'neutral', candidate = 'neutral', since = 0, last = null, yawnSince = null, tiredUntil = 0;
  function relative(blendshapes, t) {
    const seconds = last === null ? 0 : (t - last) / 1000;
    const adjusted = {};
    for (const [name, value] of Object.entries(blendshapes)) {
      const base = baseline[name];
      // The resting face: averaged over the first frames, then followed slowly while the
      // person looks neutral, never learning an expression as "resting".
      if (base === undefined) baseline[name] = value;
      else if (samples < 20) baseline[name] = base + (value - base) / (samples + 1);
      else if (state === 'neutral' && value < base + .15) baseline[name] = base + (value - base) * Math.min(1, seconds / 20);
      const rest = Math.min(baseline[name], .8);
      adjusted[name] = clamp((value - rest) / (1 - rest), 0, 1);
    }
    samples++;
    return adjusted;
  }
  return {
    get state() { return state; },
    // blendshapes: { name: score 0..1 } for one frame; t in ms. Returns the steady label.
    update(blendshapes, t) {
      const b = relative(blendshapes, t);
      last = t;
      const yawning = (b.jawOpen ?? 0) > YAWN.jaw && mean(b.eyeWideLeft, b.eyeWideRight) < .2;
      yawnSince = yawning ? yawnSince ?? t : null;
      if (yawnSince !== null && t - yawnSince >= YAWN.hold) tiredUntil = t + YAWN.lasts;
      let want = Object.keys(EXPRESSIONS).find(name => EXPRESSIONS[name].score(b) >= EXPRESSIONS[name].enter);
      if (!want && state !== 'neutral' && EXPRESSIONS[state].score(b) >= EXPRESSIONS[state].exit) want = state;
      if ((!want || want === 'tired' || want === 'frowning') && t < tiredUntil) want = 'tired';
      want ||= 'neutral';
      if (want === state) { candidate = want; return state; }
      if (candidate !== want) { candidate = want; since = t; }
      const hold = want === 'tired' && t < tiredUntil ? 0 : want === 'neutral' ? NEUTRAL_HOLD : EXPRESSIONS[want].hold;
      if (t - since >= hold) state = want;
      return state;
    },
    // No face: forget the moment, keep the learned resting face.
    lost() { state = candidate = 'neutral'; yawnSince = null; tiredUntil = 0; last = null; },
  };
}

// ---- Waving ------------------------------------------------------------------------------

const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
// An open palm: at least three fingertips farther from the wrist than their middle joints.
export function openPalm(hand) {
  const fingers = [[8, 6], [12, 10], [16, 14], [20, 18]];
  return fingers.filter(([tip, joint]) => distance(hand[tip], hand[0]) > distance(hand[joint], hand[0]) * 1.1).length >= 3;
}

// A wave: an open palm raised above the chin, swinging side to side at least `reversals`
// times within `window` ms, each swing wider than `amplitude` of the frame.
export function createWaveDetector({ window = 1500, reversals = 3, amplitude = .05, cooldown = 8000 } = {}) {
  let path = [], lastWave = -Infinity;
  return {
    // hand: 21 MediaPipe landmarks (x, y in 0..1) or null; chinY: the chin's y, if known.
    update(hand, chinY, t) {
      if (!hand || !openPalm(hand) || hand[0].y > (chinY ?? .6)) { path = []; return false; }
      const palm = [0, 5, 9, 13, 17].reduce((sum, i) => sum + hand[i].x, 0) / 5;
      path.push({ x: palm, t });
      path = path.filter(point => t - point.t <= window);
      let turns = 0, direction = 0, extreme = path[0].x;
      for (const { x } of path.slice(1)) {
        const moved = x - extreme;
        if (direction === 0) { if (Math.abs(moved) >= amplitude) { direction = Math.sign(moved); extreme = x; } }
        else if (Math.sign(moved) === direction) extreme = x;
        else if (Math.abs(moved) >= amplitude) { turns++; direction = -direction; extreme = x; }
      }
      if (turns < reversals || t - lastWave < cooldown) return false;
      lastWave = t; path = [];
      return true;
    },
  };
}

// ---- MEMO's screen -----------------------------------------------------------------------

// Page states and MEMO's own alerts always show; a reply's expression holds for a while;
// only then does MEMO answer the person's expression.
export const OWN_FACES = new Set(['alert', 'error', 'thinking', 'listening', 'looking']);
export const REPLY_HOLD = 8000;
export function screenFace({ base, holdUntil = 0, now, busy = false, mirror = null }) {
  if (OWN_FACES.has(base) || busy || now < holdUntil || !mirror) return base;
  return mirror;
}

// ---- Photos ------------------------------------------------------------------------------

// The pixel rectangle of a region given as fractions of the frame; the whole frame for none.
export function cropRect(roi, width, height) {
  if (!roi) return { sx: 0, sy: 0, sw: width, sh: height };
  const sx = Math.round(clamp(roi.x, 0, 1) * width), sy = Math.round(clamp(roi.y, 0, 1) * height);
  return { sx, sy, sw: Math.max(1, Math.min(width - sx, Math.round(roi.w * width))), sh: Math.max(1, Math.min(height - sy, Math.round(roi.h * height))) };
}

// Brightness and contrast (mean and spread of grey, 0-255) and sharpness (variance of the
// Laplacian) of an RGBA image, usually a small copy of the photo. Recorded with each photo.
export function measureQuality(rgba, width, height) {
  const grey = new Float32Array(width * height);
  let sum = 0;
  for (let i = 0; i < grey.length; i++) { grey[i] = .299 * rgba[i * 4] + .587 * rgba[i * 4 + 1] + .114 * rgba[i * 4 + 2]; sum += grey[i]; }
  const brightness = sum / grey.length;
  let spread = 0;
  for (const value of grey) spread += (value - brightness) ** 2;
  let lap = 0, lapSquares = 0, count = 0;
  for (let y = 1; y < height - 1; y++) for (let x = 1; x < width - 1; x++) {
    const i = y * width + x;
    const value = grey[i - 1] + grey[i + 1] + grey[i - width] + grey[i + width] - 4 * grey[i];
    lap += value; lapSquares += value * value; count++;
  }
  const round = value => Math.round(value * 100) / 100;
  return {
    brightness: round(brightness),
    contrast: round(Math.sqrt(spread / grey.length)),
    sharpness: round(count ? lapSquares / count - (lap / count) ** 2 : 0),
  };
}

// ---- Asking MEMO to look -----------------------------------------------------------------

// Chat phrasings that mean "look at this through the camera"; the same list as the backend's
// memo.domain.vision.LOOK_REQUEST. The model's {"type":"look"} covers everything else.
export const LOOK_REQUEST = /手上|手裡|手中|拿著|拿的(是|東西)|這(個|是|張|本|瓶|罐|盒|些|上面)?是什麼|這是啥|翻譯(一下)?(這|它|上面|看看)|上面寫(的|著)?(是)?什麼|寫(的|著)?是什麼|(看|瞧)(一下|看)?(這個|這|我手|我拿|我現在)|你看得到(我|這)|幫我看(一下|看)?(這|我)/;
export const wantsLook = text => LOOK_REQUEST.test(text || '');

// What the voice reads: quoted foreign text (「Fragile」意思是易碎品) is shown, not spoken,
// since the Chinese voice cannot pronounce it; the Chinese translation is.
export function spokenText(text) {
  const cjk = value => (value.match(/[㐀-鿿]/g) || []).length / Math.max(1, value.replace(/\s/g, '').length);
  const spoken = text.replace(/「([^」]*)」/g, (quoted, inner) => cjk(inner) < .3 ? '' : quoted).replace(/^[\s，、：:]+/, '').trim();
  return spoken || text;
}

// ---- Watching for change -----------------------------------------------------------------

// How different two grey copies of the frame are (0-255), ignoring an overall change in
// brightness, so a camera adjusting its exposure is not a change.
export function greyDiff(a, b) {
  if (!a || !b || a.length !== b.length) return 255;
  let meanA = 0, meanB = 0;
  for (let i = 0; i < a.length; i++) { meanA += a[i]; meanB += b[i]; }
  meanA /= a.length; meanB /= b.length;
  let sum = 0;
  for (let i = 0; i < a.length; i++) sum += Math.abs((a[i] - meanA) - (b[i] - meanB));
  return sum / a.length;
}

// Watches one region (or the whole frame) for a change that stays. Each update compares the
// frame with the reference (the last settled scene) and with the previous frame:
//   'idle'     nothing changed, or the change went away (a hand came and went)
//   'onset'    the first frame that differs: a good moment for a photo of what is happening
//   'changing' still different and still moving
//   'settled'  different from the reference and still for `settle` updates in a row
// After acting on 'settled', rebase() makes the current scene the new reference.
export function createChangeWatch({ threshold = 12, still = 5, settle = 3 } = {}) {
  let reference = null, previous = null, steady = 0, changing = false;
  return {
    update(frame) {
      if (!reference) { reference = previous = frame; return 'idle'; }
      const change = greyDiff(frame, reference), motion = greyDiff(frame, previous);
      previous = frame;
      if (change < threshold) { changing = false; steady = 0; return 'idle'; }
      if (!changing) { changing = true; steady = 0; return 'onset'; }
      steady = motion < still ? steady + 1 : 0;
      return steady >= settle ? 'settled' : 'changing';
    },
    rebase(frame) { reference = previous = frame; steady = 0; changing = false; },
  };
}

// 「現在這邊怎樣？」: a photo is taken at once (no countdown) and MEMO reports what it sees
// with the readings. The same list as the backend's memo.domain.observation.REPORT_NOW.
export const REPORT_NOW = /現在(這邊|這裡|房間|家裡)?(怎樣|怎麼樣|如何|的?情況|的?狀況)|這(邊|裡)(現在)?(怎樣|怎麼樣|的?情況|的?狀況)|看一下(現在|四周|周圍|房間|這邊)|幫我看看(四周|周圍|房間|這邊)/;
export const wantsReport = text => REPORT_NOW.test(text || '');
