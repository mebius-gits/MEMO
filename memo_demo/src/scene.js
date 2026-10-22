import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';

// Cartoon handheld companion: stepped toon shading, inverted-hull ink outlines, flat decal buttons.
const INK = 0x1d2b2f;
const COLORS = { body: 0x66c7b8, arm: 0x5fb0b4, leg: 0x4f8f99, slot: 0x2c5d59, blue: 0x3a4fb3, yellow: 0xf6d23b, cyan: 0x86d8ec, green: 0x4cc45b, red: 0xe9455a };
const SCREEN = '#CDEFDB', FACE = '#1D2B2F';
const W = 2, H = 2.5, D = 1.3, FRONT = D / 2, CENTER = H / 2 + .05;

export function createMemoScene(container) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.toneMapping = THREE.NoToneMapping;
  container.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(32, 1, .1, 60);
  const target = new THREE.Vector3(0, 1.45, .5);
  const home = new THREE.Vector3(-5.6, 2.8, 8.2);
  camera.position.copy(home);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.copy(target);
  Object.assign(controls, { enableDamping: true, enablePan: false, minDistance: 6.5, maxDistance: 13, minPolarAngle: .7, maxPolarAngle: 1.55, minAzimuthAngle: -1.2, maxAzimuthAngle: 1.2 });

  scene.add(new THREE.HemisphereLight(0xffffff, 0xb9d6d2, 1.6));
  const key = new THREE.DirectionalLight(0xffffff, 1.9);
  key.position.set(5, 7, 6);
  scene.add(key);

  const gradient = new THREE.DataTexture(new Uint8Array([110, 190, 255]), 3, 1, THREE.RedFormat);
  gradient.minFilter = gradient.magFilter = THREE.NearestFilter;
  gradient.needsUpdate = true;
  const toon = color => new THREE.MeshToonMaterial({ color, gradientMap: gradient });
  const ink = new THREE.MeshBasicMaterial({ color: INK });
  const outlineMaterial = thickness => new THREE.ShaderMaterial({
    uniforms: { thickness: { value: thickness }, color: { value: new THREE.Color(INK) } },
    vertexShader: 'uniform float thickness; void main() { gl_Position = projectionMatrix * modelViewMatrix * vec4(position + normal * thickness, 1.0); }',
    fragmentShader: 'uniform vec3 color; void main() { gl_FragColor = vec4(color, 1.0); }',
    side: THREE.BackSide,
  });
  const bodyOutline = outlineMaterial(.03), limbOutline = outlineMaterial(.022);

  function mesh(geometry, material, parent, x = 0, y = 0, z = 0, outline) {
    const object = new THREE.Mesh(geometry, material);
    object.position.set(x, y, z);
    if (outline) object.add(new THREE.Mesh(geometry, outline));
    parent.add(object);
    return object;
  }

  const root = new THREE.Group();
  scene.add(root);
  const body = new THREE.Group();
  root.add(body);
  mesh(new RoundedBoxGeometry(W, H, D, 6, .14), toon(COLORS.body), body, 0, CENTER, 0, bodyOutline);

  // Front decals: an ink rim behind each raised part; extrusion side walls are ink too, so edges stay outlined at any angle.
  function decal(shapeAt, color, x, y, depth = .05, rim = .028) {
    mesh(new THREE.ShapeGeometry(shapeAt(rim), 32), ink, body, x, CENTER + y, FRONT + .002);
    return mesh(new THREE.ExtrudeGeometry(shapeAt(0), { depth, bevelEnabled: false, curveSegments: 32 }), [toon(color), ink], body, x, CENTER + y, FRONT + .003);
  }
  const circle = r => grow => new THREE.Shape().absarc(0, 0, r + grow, 0, Math.PI * 2);
  const pill = (w, h) => grow => {
    const a = w / 2 + grow, b = h / 2 + grow;
    const s = new THREE.Shape();
    s.moveTo(-a + b, -b); s.lineTo(a - b, -b); s.absarc(a - b, 0, b, -Math.PI / 2, Math.PI / 2); s.lineTo(-a + b, b); s.absarc(-a + b, 0, b, Math.PI / 2, Math.PI * 1.5);
    return s;
  };
  const cross = (size, arm) => grow => {
    const a = size / 2 + grow, b = arm / 2 + grow;
    return new THREE.Shape([[-b, a], [b, a], [b, b], [a, b], [a, -b], [b, -b], [b, -a], [-b, -a], [-b, -b], [-a, -b], [-a, b], [-b, b]].map(([px, py]) => new THREE.Vector2(px, py)));
  };
  const triangle = r => grow => new THREE.Shape([0, 1, 2].map(i => { const angle = Math.PI / 2 + i * Math.PI * 2 / 3, k = r + grow * 2; return new THREE.Vector2(Math.cos(angle) * k, Math.sin(angle) * k * .9); }));

  decal(pill(.98, .085), COLORS.slot, -.17, -.2, .015, .02);
  decal(circle(.06), COLORS.blue, .7, -.18, .03, .022);
  decal(cross(.44, .15), COLORS.yellow, -.5, -.6);
  decal(triangle(.15), COLORS.cyan, .17, -.58);
  decal(circle(.085), COLORS.green, .67, -.55);
  decal(circle(.165), COLORS.red, .4, -.88, .06);
  decal(pill(.25, .065), COLORS.blue, -.58, -1.05, .025, .02);
  decal(pill(.25, .065), COLORS.blue, -.24, -1.05, .025, .02);

  // Screen: canvas-drawn face with its own ink frame.
  const canvas = document.createElement('canvas');
  canvas.width = 1024; canvas.height = 726;
  const ctx = canvas.getContext('2d');
  const faceTexture = new THREE.CanvasTexture(canvas);
  faceTexture.colorSpace = THREE.SRGBColorSpace;
  faceTexture.anisotropy = 4;
  mesh(new THREE.PlaneGeometry(1.56, 1.106), new THREE.MeshBasicMaterial({ map: faceTexture, transparent: true }), body, 0, CENTER + .52, FRONT + .004);

  // Left side: speaker holes and vertical lettering.
  const side = new THREE.Group();
  side.position.set(-W / 2 - .004, CENTER, 0);
  side.rotation.y = -Math.PI / 2;
  body.add(side);
  for (const [z, y] of [[-.2, 1.0], [.16, 1.02], [-.36, .84], [0, .87], [.32, .82], [-.16, .69], [.18, .67]]) mesh(new THREE.CircleGeometry(.045, 20), ink, side, z, y, 0);
  const letters = document.createElement('canvas');
  letters.width = 1024; letters.height = 300;
  const lc = letters.getContext('2d');
  lc.fillStyle = FACE; lc.font = '900 250px "Arial Black", "Helvetica Neue", sans-serif'; lc.textAlign = 'center'; lc.textBaseline = 'middle';
  lc.letterSpacing = '30px'; lc.fillText('MEMO', 512, 160);
  const lettersTexture = new THREE.CanvasTexture(letters);
  lettersTexture.colorSpace = THREE.SRGBColorSpace;
  const lettering = mesh(new THREE.PlaneGeometry(1.5, .44), new THREE.MeshBasicMaterial({ map: lettersTexture, transparent: true }), side, 0, -.14, 0);
  lettering.rotation.z = -Math.PI / 2;

  // Limbs: thin capsules with rounded ends; legs stretch forward on the floor, hands rest beside the body.
  function limb(points, radius, color) {
    const curve = new THREE.CatmullRomCurve3(points.map(p => new THREE.Vector3(...p)));
    const material = toon(color);
    mesh(new THREE.TubeGeometry(curve, 40, radius, 16, false), material, root, 0, 0, 0, limbOutline);
    mesh(new THREE.SphereGeometry(radius, 16, 12), material, root, ...points[0]);
    return material;
  }
  const feet = [];
  for (const sign of [-1, 1]) {
    const legMaterial = limb([[sign * .45, .16, 0], [sign * .47, .11, .7], [sign * .52, .1, 1.45], [sign * .56, .16, 1.88]], .09, COLORS.leg);
    const foot = mesh(new THREE.SphereGeometry(.11, 24, 16), legMaterial, root, sign * .57, .2, 1.92, limbOutline);
    foot.scale.set(1, 1.35, 1.1); feet.push(foot);
    const armMaterial = limb([[sign * (W / 2 - .02), 1.05, .15], [sign * 1.2, .82, .3], [sign * 1.3, .4, .55], [sign * 1.34, .14, .8]], .075, COLORS.arm);
    const hand = mesh(new THREE.SphereGeometry(.12, 24, 16), armMaterial, root, sign * 1.36, .1, .88, limbOutline);
    hand.scale.set(1, .5, 1.2);
    for (const spread of [-.6, 0, .6]) {
      const finger = mesh(new THREE.CapsuleGeometry(.03, .08, 4, 10), armMaterial, root, sign * 1.36 + Math.sin(spread) * .15, .07, .88 + Math.cos(spread) * .15, limbOutline);
      finger.rotation.set(Math.PI / 2, 0, -spread);
    }
  }

  // Soft cartoon contact shadow.
  const blob = document.createElement('canvas');
  blob.width = blob.height = 256;
  const bctx = blob.getContext('2d');
  const shade = bctx.createRadialGradient(128, 128, 10, 128, 128, 128);
  shade.addColorStop(0, 'rgba(29,43,47,.32)'); shade.addColorStop(.6, 'rgba(29,43,47,.14)'); shade.addColorStop(1, 'rgba(29,43,47,0)');
  bctx.fillStyle = shade; bctx.fillRect(0, 0, 256, 256);
  const shadow = mesh(new THREE.PlaneGeometry(3.9, 3.4), new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(blob), transparent: true, depthWrite: false }), scene, 0, .002, .6);
  shadow.rotation.x = -Math.PI / 2;

  const headAnchor = new THREE.Vector3(W / 2 - .1, H + .3, FRONT);
  let mood = 'neutral', intensity = .4, talking = false, paused = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let lastFace = '', frameId;
  function drawFace(blink, mouthOpen) {
    const faceKey = `${mood}-${blink}-${mouthOpen}`;
    if (lastFace === faceKey) return;
    lastFace = faceKey;
    ctx.clearRect(0, 0, 1024, 726);
    ctx.fillStyle = SCREEN; ctx.strokeStyle = FACE; ctx.lineWidth = 26;
    ctx.beginPath(); ctx.roundRect(14, 14, 996, 698, 64); ctx.fill(); ctx.stroke();
    ctx.fillStyle = FACE; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    const eyeY = 300;
    for (const x of [330, 694]) {
      ctx.beginPath(); ctx.lineWidth = 20;
      if (blink) { ctx.moveTo(x - 30, eyeY + 6); ctx.lineTo(x + 30, eyeY + 6); ctx.stroke(); }
      else if (mood === 'happy') { ctx.moveTo(x - 36, eyeY + 16); ctx.quadraticCurveTo(x, eyeY - 38, x + 36, eyeY + 16); ctx.stroke(); }
      else { ctx.ellipse(x, eyeY, 30, mood === 'confused' && x > 512 ? 24 : 42, 0, 0, Math.PI * 2); ctx.fill(); }
    }
    if (mood === 'concerned') {
      ctx.lineWidth = 16; ctx.beginPath();
      ctx.moveTo(280, 228); ctx.lineTo(360, 210); ctx.moveTo(664, 210); ctx.lineTo(744, 228); ctx.stroke();
    }
    if (mood === 'friendly' || mood === 'happy') {
      ctx.fillStyle = 'rgba(240, 140, 150, .45)';
      for (const x of [230, 794]) { ctx.beginPath(); ctx.ellipse(x, 400, 48, 24, 0, 0, Math.PI * 2); ctx.fill(); }
      ctx.fillStyle = FACE;
    }
    ctx.beginPath(); ctx.lineWidth = 14;
    if (mouthOpen) {
      // Speaking: a small open mouth that flaps while audio plays.
      ctx.ellipse(512, 455, 52, 34, 0, 0, Math.PI * 2); ctx.fillStyle = '#2E6B3C'; ctx.fill(); ctx.stroke();
    } else if (mood === 'happy') {
      const mouth = new Path2D('M420 420 L604 420 C604 560 420 560 420 420 Z');
      ctx.fillStyle = '#2E6B3C'; ctx.fill(mouth);
      ctx.save(); ctx.clip(mouth); ctx.fillStyle = '#7CD06D'; ctx.beginPath(); ctx.ellipse(512, 535, 70, 46, 0, 0, Math.PI * 2); ctx.fill(); ctx.restore();
      ctx.stroke(mouth);
    } else if (mood === 'confused') { ctx.arc(530, 455, 28, 0, Math.PI * 2); ctx.stroke(); }
    else if (mood === 'concerned') { ctx.moveTo(452, 470); ctx.quadraticCurveTo(512, 425, 572, 470); ctx.stroke(); }
    else { const wide = mood === 'friendly' ? 110 : 82; ctx.moveTo(512 - wide, 420); ctx.quadraticCurveTo(512, mood === 'friendly' ? 520 : 495, 512 + wide, 420); ctx.stroke(); }
    faceTexture.needsUpdate = true;
  }

  let elapsed = 0, previous = performance.now();
  function animate(now) {
    const delta = Math.min((now - previous) / 1000, .05); previous = now;
    if (!paused && !document.hidden) elapsed += delta;
    const breath = paused ? 0 : Math.sin(elapsed * 1.7);
    const bounce = mood === 'happy' && !paused ? Math.abs(Math.sin(elapsed * 4)) * .05 : 0;
    body.position.y = breath * .018 + bounce;
    body.scale.set(1 - breath * .006, 1 + breath * .008, 1);
    body.rotation.z = mood === 'confused' ? -.07 : Math.sin(elapsed * .7) * .008 * intensity;
    feet.forEach((foot, i) => { foot.rotation.x = paused ? 0 : Math.sin(elapsed * 2.2 + i * 1.4) * .12 * (mood === 'happy' ? 2 : 1); });
    drawFace(!paused && elapsed % 5 > 4.82, talking && !paused && Math.floor(performance.now() / 140) % 2 === 1);
    controls.update(); renderer.render(scene, camera);
    frameId = requestAnimationFrame(animate);
  }
  const observer = new ResizeObserver(() => {
    const { width, height } = container.getBoundingClientRect();
    if (!width || !height) return;
    renderer.setSize(width, height);
    camera.aspect = width / height;
    // Portrait stages (phones) zoom out so BMO and its speech bubble both fit.
    camera.zoom = Math.min(1, camera.aspect / .9);
    camera.updateProjectionMatrix();
  }); observer.observe(container);
  renderer.domElement.addEventListener('webglcontextlost', e => { e.preventDefault(); document.querySelector('#webgl-fallback').hidden = false; });
  frameId = requestAnimationFrame(animate);
  return {
    setEmotion(label, amount) { mood = label; intensity = amount; },
    setTalking(value) { talking = value; },
    toggleMotion() { paused = !paused; return paused; },
    get paused() { return paused; },
    reset() { camera.position.copy(home); controls.target.copy(target); controls.update(); },
    // Screen position (container pixels) just above the top-right corner of the head, for the speech bubble.
    anchor() {
      const { width, height } = container.getBoundingClientRect();
      const point = headAnchor.clone().applyMatrix4(body.matrixWorld).project(camera);
      return { x: (point.x + 1) / 2 * width, y: (1 - point.y) / 2 * height };
    },
    dispose() { cancelAnimationFrame(frameId); observer.disconnect(); controls.dispose(); renderer.dispose(); }
  };
}
