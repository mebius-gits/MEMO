import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';

export function createMemoScene(container) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.35;
  container.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, 1, .1, 60);
  const home = new THREE.Vector3(4.6, 3.5, 8.2);
  camera.position.copy(home);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(0, 1.55, 0);
  controls.enableDamping = true;
  controls.enablePan = false;
  controls.minDistance = 6;
  controls.maxDistance = 12;
  controls.minPolarAngle = .65;
  controls.maxPolarAngle = 1.6;
  controls.minAzimuthAngle = -1.1;
  controls.maxAzimuthAngle = 1.1;
  scene.add(new THREE.HemisphereLight(0xf6ffe3, 0x6a846a, 3));
  const sun = new THREE.DirectionalLight(0xfff6dc, 4.2);
  sun.position.set(-3, 7, 5);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -5, right: 5, top: 6, bottom: -5, near: .1, far: 20 });
  sun.shadow.normalBias = .035;
  sun.shadow.bias = -.0002;
  sun.shadow.radius = 5;
  scene.add(sun);
  const fill = new THREE.DirectionalLight(0xb2e6d1, 1.4);
  fill.position.set(4, 3, -3);
  scene.add(fill);
  const material = (color, roughness = .58) => new THREE.MeshStandardMaterial({ color, roughness });
  const mint = material(0x54b9a1);
  const darkMint = material(0x287765);
  const trim = material(0x226753);
  const yellow = material(0xf5cf56);
  const group = new THREE.Group();
  scene.add(group);
  function mesh(geometry, mat, x, y, z, parent = group) {
    const object = new THREE.Mesh(geometry, mat);
    object.position.set(x, y, z);
    object.castShadow = true;
    object.receiveShadow = true;
    parent.add(object);
    return object;
  }
  const box = (w, h, d, radius, mat, x, y, z, parent) => mesh(new RoundedBoxGeometry(w, h, d, 5, radius), mat, x, y, z, parent);
  const body = new THREE.Group();
  group.add(body);
  box(2.08, 2.62, 1.18, .22, darkMint, 0, 1.98, -.025, body);
  box(2.01, 2.56, 1.17, .2, mint, 0, 2, .025, body);
  box(1.74, 1.48, .13, .13, trim, 0, 2.47, .619, body);
  const canvas = document.createElement('canvas');
  canvas.width = 1024; canvas.height = 840;
  const ctx = canvas.getContext('2d');
  const faceTexture = new THREE.CanvasTexture(canvas);
  faceTexture.colorSpace = THREE.SRGBColorSpace;
  const faceMat = new THREE.MeshBasicMaterial({ map: faceTexture, transparent: true });
  const face = mesh(new THREE.PlaneGeometry(1.61, 1.35), faceMat, 0, 2.47, .698, body);
  face.castShadow = false;
  box(1.04, .1, .04, .025, trim, -.18, 1.51, .632, body);
  function cylinder(radius, depth, mat, x, y, z) {
    const object = mesh(new THREE.CylinderGeometry(radius, radius, depth, 48), mat, x, y, z, body);
    object.rotation.x = Math.PI / 2;
    return object;
  }
  function cross(size, depth, mat, z) {
    const a = size / 2, b = size / 6;
    const shape = new THREE.Shape();
    shape.moveTo(-b, a); shape.lineTo(b, a); shape.lineTo(b, b); shape.lineTo(a, b);
    shape.lineTo(a, -b); shape.lineTo(b, -b); shape.lineTo(b, -a); shape.lineTo(-b, -a);
    shape.lineTo(-b, -b); shape.lineTo(-a, -b); shape.lineTo(-a, b); shape.lineTo(-b, b);
    shape.closePath();
    mesh(new THREE.ExtrudeGeometry(shape, { depth, bevelEnabled: true, bevelSegments: 3, steps: 1, bevelSize: .015, bevelThickness: .015 }), mat, -.56, 1.16, z, body);
  }
  cross(.53, .05, material(0x8c9042), .625);
  cross(.44, .06, yellow, .69);
  cylinder(.128, .06, trim, .67, 1.24, .64);
  cylinder(.105, .07, material(0x88b94c), .67, 1.24, .69);
  cylinder(.15, .06, trim, .51, .91, .64);
  cylinder(.128, .08, material(0xe77966), .51, .91, .70);
  const triangle = new THREE.Shape();
  triangle.moveTo(0, .15); triangle.lineTo(-.145, -.12); triangle.lineTo(.145, -.12); triangle.closePath();
  mesh(new THREE.ExtrudeGeometry(triangle, { depth: .065, bevelEnabled: true, bevelSize: .018, bevelThickness: .015, bevelSegments: 3 }), material(0x7cbecb), .22, 1.22, .66, body);
  box(.22, .055, .04, .02, trim, -.53, .82, .64, body);
  box(.22, .055, .04, .02, trim, -.20, .82, .64, body);
  const badge = document.createElement('canvas'); badge.width = 400; badge.height = 100;
  const bc = badge.getContext('2d'); bc.fillStyle = '#286f5c'; bc.font = '600 40px sans-serif'; bc.textAlign = 'center'; bc.fillText('M E M O', 200, 62);
  const badgeTexture = new THREE.CanvasTexture(badge); badgeTexture.colorSpace = THREE.SRGBColorSpace;
  const logo = mesh(new THREE.PlaneGeometry(.57, .14), new THREE.MeshBasicMaterial({ map: badgeTexture, transparent: true }), 0, .73, .641, body);
  logo.castShadow = false;
  for (let row = 0; row < 3; row++) for (let col = 0; col < 2; col++) {
    const dot = mesh(new THREE.CircleGeometry(.036, 16), trim, 1.045, 2.5 - row * .16, -.13 + col * .24, body);
    dot.rotation.y = Math.PI / 2;
  }
  function limb(points, radius, mat) {
    const curve = new THREE.CatmullRomCurve3(points.map(p => new THREE.Vector3(...p)));
    return mesh(new THREE.TubeGeometry(curve, 24, radius, 12, false), mat, 0, 0, 0);
  }
  for (const sign of [-1, 1]) {
    limb([[sign * .53, .73, .2], [sign * .59, .33, .57], [sign * .67, .24, 1.04]], .105, darkMint);
    const shoe = box(.36, .28, .65, .12, darkMint, sign * .67, .27, 1.13);
    shoe.rotation.y = sign * -.15;
    box(.33, .37, .24, .1, darkMint, sign * .67, .36, 1.35);
    limb([[sign * 1.01, 1.47, .05], [sign * 1.22, .88, .29], [sign * 1.36, .29, .80]], .077, mint);
    const hand = mesh(new THREE.SphereGeometry(.15, 24, 20), mint, sign * 1.36, .24, .85);
    hand.scale.set(.82, .5, 1.2);
    for (let i = 0; i < 3; i++) {
      limb([[sign * (1.28 + i * .07), .24, .89], [sign * (1.3 + i * .07), .2, 1.01]], .027, mint);
    }
  }
  const floor = mesh(new THREE.PlaneGeometry(200, 200), new THREE.ShadowMaterial({ opacity: .12 }), 0, .006, 0, scene);
  floor.rotation.x = -Math.PI / 2; floor.castShadow = false;
  const platform = mesh(new THREE.CylinderGeometry(2.15, 2.18, .1, 96), material(0xd8e0c9, .95), 0, .06, .12, scene);
  platform.castShadow = false;
  let mood = 'neutral', intensity = .4, paused = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let lastFace = '', frameId;
  function drawFace(blink) {
    const key = `${mood}-${blink}`;
    if (lastFace === key) return;
    lastFace = key;
    ctx.clearRect(0, 0, 1024, 840);
    ctx.fillStyle = '#cbe9ca'; ctx.beginPath(); ctx.roundRect(0, 0, 1024, 840, 70); ctx.fill();
    ctx.strokeStyle = '#254e3d'; ctx.fillStyle = '#254e3d'; ctx.lineWidth = 26; ctx.lineCap = 'round';
    for (const x of [320, 700]) {
      ctx.beginPath();
      if (blink || mood === 'happy') {
        ctx.moveTo(x - 32, 350); ctx.quadraticCurveTo(x, mood === 'happy' ? 300 : 353, x + 32, 350); ctx.stroke();
      } else { ctx.ellipse(x, 350, 35, mood === 'confused' && x === 700 ? 32 : 43, 0, 0, Math.PI * 2); ctx.fill(); }
    }
    if (mood === 'concerned') {
      ctx.lineWidth = 17; ctx.beginPath(); ctx.moveTo(270, 264); ctx.lineTo(350, 242); ctx.moveTo(672, 242); ctx.lineTo(752, 264); ctx.stroke();
    }
    ctx.lineWidth = 24; ctx.beginPath();
    if (mood === 'confused') { ctx.arc(512, 505, 35, 0, Math.PI * 2); }
    else if (mood === 'concerned') { ctx.moveTo(435, 525); ctx.quadraticCurveTo(512, 470, 585, 525); }
    else { ctx.moveTo(365, 505); ctx.quadraticCurveTo(512, mood === 'happy' ? 680 : 625, 660, 505); }
    ctx.stroke();
    if (mood === 'friendly' || mood === 'happy') {
      ctx.fillStyle = '#e9b9a19c';
      for (const x of [225, 805]) { ctx.beginPath(); ctx.ellipse(x, 460, 47, 22, 0, 0, Math.PI * 2); ctx.fill(); }
    }
    faceTexture.needsUpdate = true;
  }
  let elapsed = 0, previous = performance.now();
  function animate(now) {
    const delta = Math.min((now - previous) / 1000, .05); previous = now;
    if (!paused && !document.hidden) elapsed += delta;
    body.position.y = paused ? 0 : Math.sin(elapsed * 1.7) * .016;
    body.rotation.z = mood === 'confused' ? -.065 : Math.sin(elapsed * .7) * .006 * intensity;
    drawFace(!paused && elapsed % 5 > 4.8);
    controls.update(); renderer.render(scene, camera);
    frameId = requestAnimationFrame(animate);
  }
  const observer = new ResizeObserver(() => {
    const { width, height } = container.getBoundingClientRect();
    if (!width || !height) return;
    renderer.setSize(width, height);
    camera.aspect = width / height; camera.updateProjectionMatrix();
  }); observer.observe(container);
  renderer.domElement.addEventListener('webglcontextlost', e => { e.preventDefault(); document.querySelector('#webgl-fallback').hidden = false; });
  frameId = requestAnimationFrame(animate);
  return {
    setEmotion(label, amount) { mood = label; intensity = amount; },
    toggleMotion() { paused = !paused; return paused; },
    get paused() { return paused; },
    reset() { camera.position.copy(home); controls.target.set(0, 1.55, 0); controls.update(); },
    dispose() { cancelAnimationFrame(frameId); observer.disconnect(); controls.dispose(); renderer.dispose(); }
  };
}
