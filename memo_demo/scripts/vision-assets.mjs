// Puts what the camera features need under public/mediapipe/, so the page never loads them
// from a CDN: the MediaPipe wasm runtime (from the pinned npm package) and two models,
// downloaded once and checked against their SHA-256. Runs before `npm run dev` and `build`.
import { createHash } from 'node:crypto';
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const target = join(root, 'public', 'mediapipe');
const MODELS = {
  'face_landmarker.task': {
    url: 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task',
    sha256: '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff',
  },
  'hand_landmarker.task': {
    url: 'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task',
    sha256: 'fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1',
  },
};
const sha256 = data => createHash('sha256').update(data).digest('hex');

const wasmSource = join(root, 'node_modules', '@mediapipe', 'tasks-vision', 'wasm');
mkdirSync(join(target, 'wasm'), { recursive: true });
for (const file of readdirSync(wasmSource)) copyFileSync(join(wasmSource, file), join(target, 'wasm', file));

mkdirSync(join(target, 'models'), { recursive: true });
let failed = false;
for (const [name, { url, sha256: expected }] of Object.entries(MODELS)) {
  const path = join(target, 'models', name);
  if (existsSync(path) && sha256(readFileSync(path)) === expected) continue;
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = Buffer.from(await response.arrayBuffer());
    if (sha256(data) !== expected) throw new Error('checksum mismatch');
    writeFileSync(`${path}.part`, data); renameSync(`${path}.part`, path);
    console.log(`vision-assets: downloaded ${name}`);
  } catch (error) {
    // The page still runs: photos work, only eye tracking, expressions and waving are off.
    console.warn(`vision-assets: could not get ${name} (${error.message}); face and hand tracking stay off.`);
    failed = true;
  }
}
if (!failed) console.log('vision-assets: MediaPipe runtime and models ready');
