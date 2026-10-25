// Unit tests for perception.js: no browser, no camera. Run with `npm test`.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  GAZE, MIRROR, REPLY_HOLD, createChangeWatch, createExpressionReader, createGaze, createWaveDetector,
  cropRect, greyDiff, measureQuality, openPalm, screenFace, spokenText, wantsLook, wantsReport,
} from '../src/perception.js';

test('eyes look toward the person and rest when no one is there', () => {
  const gaze = createGaze();
  // A face on the camera's left is on the person's right: MEMO looks to its screen-right.
  gaze.target({ x: .02, y: .5 });
  let offset;
  for (let i = 0; i < 60; i++) offset = gaze.update(1 / 30);
  assert.equal(offset.x, GAZE.maxX);
  assert.equal(offset.y, 0);
  assert.equal(offset.x % GAZE.step, 0);
  gaze.target({ x: .52, y: .51 }); // a small move stays inside the dead zone
  for (let i = 0; i < 90; i++) offset = gaze.update(1 / 30);
  assert.deepEqual(offset, { x: 0, y: 0 });
  gaze.target({ x: .5, y: .95 });
  for (let i = 0; i < 90; i++) offset = gaze.update(1 / 30);
  assert.equal(offset.y, GAZE.maxY); // looking down at someone low in the frame
  gaze.target(null);
  for (let i = 0; i < 90; i++) offset = gaze.update(1 / 30);
  assert.deepEqual(offset, { x: 0, y: 0 });
});

const resting = { mouthSmileLeft: .05, mouthSmileRight: .05, browDownLeft: .05, browDownRight: .05, browInnerUp: .05, jawOpen: .02, eyeWideLeft: .05, eyeWideRight: .05, eyeBlinkLeft: .1, eyeBlinkRight: .1 };
function feed(reader, face, from, to, step = 80) {
  let state;
  for (let t = from; t <= to; t += step) state = reader.update(face, t);
  return state;
}

test('an expression must hold before MEMO answers it', () => {
  const reader = createExpressionReader();
  assert.equal(feed(reader, resting, 0, 2000), 'neutral'); // learns the resting face
  const smile = { ...resting, mouthSmileLeft: .8, mouthSmileRight: .8 };
  assert.equal(feed(reader, smile, 2080, 2600), 'neutral'); // a quick smile is not enough
  assert.equal(feed(reader, smile, 2680, 3400), 'smiling');
  assert.equal(MIRROR.smiling, 'happy');
  // Coming back to neutral takes a moment too, so one odd frame changes nothing.
  assert.equal(feed(reader, resting, 3480, 4400), 'smiling');
  assert.equal(feed(reader, resting, 4480, 6000), 'neutral');
  const surprise = { ...resting, browInnerUp: .8, jawOpen: .6, eyeWideLeft: .7, eyeWideRight: .7 };
  assert.equal(feed(reader, surprise, 6080, 6600), 'surprised');
  const frown = { ...resting, browDownLeft: .7, browDownRight: .7 };
  assert.equal(feed(reader, frown, 8000, 11000), 'frowning');
  assert.equal(MIRROR.frowning, 'concerned');
});

test('a resting face that smiles a little is not "smiling"', () => {
  const reader = createExpressionReader();
  const smiley = { ...resting, mouthSmileLeft: .45, mouthSmileRight: .45 };
  assert.equal(feed(reader, smiley, 0, 5000), 'neutral');
  assert.equal(feed(reader, { ...smiley, mouthSmileLeft: .95, mouthSmileRight: .95 }, 5080, 6600), 'smiling');
});

test('a yawn counts as tired for a while; blinking does not', () => {
  const reader = createExpressionReader();
  feed(reader, resting, 0, 2000);
  for (let t = 2080; t < 6000; t += 400) { reader.update({ ...resting, eyeBlinkLeft: 1, eyeBlinkRight: 1 }, t); reader.update(resting, t + 120); }
  assert.equal(reader.state, 'neutral');
  const yawn = { ...resting, jawOpen: .9, eyeBlinkLeft: .5, eyeBlinkRight: .5 };
  assert.equal(feed(reader, yawn, 6000, 7700), 'tired');
  assert.equal(feed(reader, resting, 7780, 20000), 'tired');
  assert.equal(feed(reader, resting, 20080, 40000), 'neutral');
});

// 21 hand landmarks: an open palm, or a fist, around (x, y).
function hand(x, y, open = true) {
  const points = Array.from({ length: 21 }, () => ({ x, y }));
  points[0] = { x, y: y + .1 };
  for (const [tip, joint] of [[8, 6], [12, 10], [16, 14], [20, 18], [4, 2]]) {
    points[joint] = { x, y: y - .02 };
    points[tip] = { x, y: open ? y - .08 : y + .05 };
  }
  for (const i of [5, 9, 13, 17]) points[i] = { x, y };
  return points;
}

test('waving: an open palm above the chin swinging side to side', () => {
  assert.equal(openPalm(hand(.5, .3)), true);
  assert.equal(openPalm(hand(.5, .3, false)), false);
  const waves = createWaveDetector();
  const swing = (start, open = true, y = .3) => {
    let waved = false;
    for (let i = 0; i < 16; i++) {
      const x = .5 + Math.sin(i * Math.PI / 4) * .08;
      waved = waves.update(hand(x, y, open), .6, start + i * 90) || waved;
    }
    return waved;
  };
  assert.equal(swing(0), true);
  assert.equal(swing(2000), false); // cooldown: one wave back at a time
  assert.equal(swing(20000, false), false); // a fist is not a wave
  assert.equal(swing(30000, true, .7), false); // a hand below the chin is not a wave
  assert.equal(swing(40000), true);
  // Holding still is not a wave.
  const still = createWaveDetector();
  let waved = false;
  for (let i = 0; i < 30; i++) waved = still.update(hand(.5, .3), .6, i * 90) || waved;
  assert.equal(waved, false);
});

test("MEMO's own states and replies come before answering the person", () => {
  assert.equal(screenFace({ base: 'thinking', now: 10, mirror: 'happy' }), 'thinking');
  assert.equal(screenFace({ base: 'alert', now: 10, mirror: 'happy' }), 'alert');
  assert.equal(screenFace({ base: 'proud', holdUntil: REPLY_HOLD, now: 10, mirror: 'happy' }), 'proud');
  assert.equal(screenFace({ base: 'proud', holdUntil: REPLY_HOLD, now: REPLY_HOLD + 1, mirror: 'happy' }), 'happy');
  assert.equal(screenFace({ base: 'neutral', now: 10, busy: true, mirror: 'happy' }), 'neutral');
  assert.equal(screenFace({ base: 'neutral', now: 10, mirror: null }), 'neutral');
});

test('photo regions and quality', () => {
  assert.deepEqual(cropRect(null, 1280, 720), { sx: 0, sy: 0, sw: 1280, sh: 720 });
  assert.deepEqual(cropRect({ x: .5, y: .5, w: .5, h: .5 }, 1280, 720), { sx: 640, sy: 360, sw: 640, sh: 360 });
  assert.deepEqual(cropRect({ x: .9, y: 0, w: .5, h: 1 }, 100, 100), { sx: 90, sy: 0, sw: 10, sh: 100 });
  const image = (width, height, pixel) => { const data = new Uint8ClampedArray(width * height * 4); for (let i = 0; i < width * height; i++) data.set([...pixel(i % width, Math.floor(i / width)), 255], i * 4); return data; };
  const flat = measureQuality(image(32, 32, () => [20, 20, 20]), 32, 32);
  assert.deepEqual(flat, { brightness: 20, contrast: 0, sharpness: 0 }); // dark, flat, blurry
  const checks = measureQuality(image(32, 32, (x, y) => ((x + y) % 2 ? [255, 255, 255] : [0, 0, 0])), 32, 32);
  assert.ok(checks.contrast > 120 && checks.sharpness > 10000 && Math.abs(checks.brightness - 127.5) < 1);
});

test('chat messages that mean "look at this", and what the voice reads', () => {
  for (const text of ['MEMO 我手上拿著什麼', '這是什麼？', '幫我翻譯這個', '上面寫什麼', '你看得到我嗎']) assert.equal(wantsLook(text), true, text);
  for (const text of ['今天天氣如何', '看看明天行程', '']) assert.equal(wantsLook(text), false, text);
  assert.equal(spokenText('「Fragile」意思是易碎品，要小心拿。'), '意思是易碎品，要小心拿。');
  assert.equal(spokenText('這是「早安」兩個字。'), '這是「早安」兩個字。');
  assert.equal(spokenText('「Hello」'), '「Hello」'); // never read nothing
});

// A 16x9 grey frame: a flat background with a bright box at column `box` (or none).
function frame(box = 4, light = 100) {
  const grey = new Float32Array(16 * 9).fill(light);
  if (box !== null) for (let y = 2; y < 6; y++) for (let x = box; x < box + 4; x++) grey[y * 16 + x] = light + 120;
  return grey;
}

test('a change counts once it stays; light and passing hands do not', () => {
  assert.equal(greyDiff(frame(), frame()), 0);
  assert.ok(greyDiff(frame(), frame(4, 160)) < 1); // the camera brightening is no change
  assert.ok(greyDiff(frame(), frame(10)) > 20);
  const watch = createChangeWatch();
  assert.equal(watch.update(frame()), 'idle'); // the first frame is the reference
  assert.equal(watch.update(frame(4, 150)), 'idle');
  // A hand reaches in and leaves: back to the reference, nothing to report.
  assert.equal(watch.update(frame(8)), 'onset');
  assert.equal(watch.update(frame(6)), 'changing');
  assert.equal(watch.update(frame()), 'idle');
  // The box is moved and stays: onset, then settled after three still frames.
  assert.deepEqual([frame(10), frame(10), frame(10), frame(10)].map(f => watch.update(f)), ['onset', 'changing', 'changing', 'settled']);
  watch.rebase(frame(10));
  assert.equal(watch.update(frame(10)), 'idle'); // the new place is the reference now
  // The box taken away entirely.
  assert.deepEqual([frame(null), frame(null), frame(null), frame(null)].map(f => watch.update(f)), ['onset', 'changing', 'changing', 'settled']);
});

test('asking how it is here takes a photo straight away', () => {
  for (const text of ['現在這邊怎樣？', '這裡現在的情況', '幫我看看四周']) assert.equal(wantsReport(text), true, text);
  for (const text of ['我手上拿的是什麼', '今天好累']) assert.equal(wantsReport(text), false, text);
});
