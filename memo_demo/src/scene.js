import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';

// Cartoon handheld companion: stepped toon shading, inverted-hull ink outlines, flat decal buttons.
const INK = 0x1d2b2f;
const COLORS = { body: 0x66c7b8, arm: 0x5fb0b4, leg: 0x4f8f99, slot: 0x2c5d59, blue: 0x3a4fb3, yellow: 0xf6d23b, cyan: 0x86d8ec, green: 0x4cc45b, red: 0xe9455a };
const SCREEN = '#CDEFDB', ALERT_SCREEN = '#FBDCCF', FACE = '#1D2B2F';
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

  const hemi = new THREE.HemisphereLight(0xffffff, 0xb9d6d2, 1.6);
  scene.add(hemi);
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

  // Desk lamp beside MEMO, its shade tipped toward MEMO. Each mode sets the
  // room light, the lamp's own light and glow; the scene eases toward them over ~a second.
  const LAMP_MODES = {
    day: { sky: 0xffffff, ground: 0xb9d6d2, hemi: 1.6, keyColor: 0xffffff, key: 1.9, bulb: 0xfffbea, light: 0xfff6e0, lamp: 1.2, glow: .28 },
    // Soft peach rather than orange: a saturated warm light turns MEMO's mint body olive.
    warm: { sky: 0xfff1e0, ground: 0xd9c5ae, hemi: 1.4, keyColor: 0xffe6c8, key: 1.5, bulb: 0xffd9a0, light: 0xffcf9c, lamp: 3.5, glow: .5 },
    dim: { sky: 0x8a8f99, ground: 0x3a3a3c, hemi: .6, keyColor: 0xffd0a0, key: .35, bulb: 0xffc27a, light: 0xffbf85, lamp: 3.5, glow: .65 },
    off: { sky: 0x64727e, ground: 0x1d2327, hemi: .4, keyColor: 0x8fa6bd, key: .22, bulb: 0x77746c, light: 0x000000, lamp: 0, glow: 0 },
  };
  const lamp = new THREE.Group();
  lamp.scale.setScalar(.7);
  // Stand the lamp at (x, z) on the floor, turned so its shade leans toward MEMO.
  function placeLamp(x, z) { lamp.position.set(x, 0, z); lamp.rotation.y = Math.atan2(z, -x); }
  placeLamp(-1.95, -1.9);
  scene.add(lamp);
  const lampBody = toon(COLORS.yellow), lampStem = toon(COLORS.slot);
  mesh(new THREE.CylinderGeometry(.42, .5, .14, 40), lampBody, lamp, 0, .07, 0, limbOutline);
  const stemCurve = new THREE.CatmullRomCurve3([[0, .12, 0], [0, 1.25, 0], [.2, 2.05, 0], [.7, 2.42, 0]].map(p => new THREE.Vector3(...p)));
  mesh(new THREE.TubeGeometry(stemCurve, 40, .05, 12, false), lampStem, lamp, 0, 0, 0, limbOutline);
  mesh(new THREE.SphereGeometry(.09, 16, 12), lampStem, lamp, 0, 1.25, 0, limbOutline);
  const lampHead = new THREE.Group();
  lampHead.position.set(.82, 2.4, 0);
  lampHead.rotation.z = .62;
  lamp.add(lampHead);
  const shadeMaterial = toon(COLORS.yellow); shadeMaterial.side = THREE.DoubleSide;
  mesh(new THREE.CylinderGeometry(.13, .42, .5, 40, 1, true), shadeMaterial, lampHead, 0, 0, 0, outlineMaterial(.022));
  mesh(new THREE.SphereGeometry(.14, 20, 12, 0, Math.PI * 2, 0, Math.PI / 2), lampBody, lampHead, 0, .22, 0, limbOutline);
  const bulbMaterial = new THREE.MeshBasicMaterial({ color: LAMP_MODES.day.bulb });
  mesh(new THREE.SphereGeometry(.15, 20, 16), bulbMaterial, lampHead, 0, -.2, 0);
  const lampLight = new THREE.PointLight(LAMP_MODES.day.light, LAMP_MODES.day.lamp, 10, 1.2);
  lampLight.position.set(0, -.45, 0);
  lampHead.add(lampLight);
  const halo = document.createElement('canvas');
  halo.width = halo.height = 128;
  const hctx = halo.getContext('2d'), haloShade = hctx.createRadialGradient(64, 64, 4, 64, 64, 64);
  haloShade.addColorStop(0, 'rgba(255,236,200,1)'); haloShade.addColorStop(.35, 'rgba(255,200,130,.45)'); haloShade.addColorStop(1, 'rgba(255,180,110,0)');
  hctx.fillStyle = haloShade; hctx.fillRect(0, 0, 128, 128);
  const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(halo), color: LAMP_MODES.day.bulb, transparent: true, opacity: LAMP_MODES.day.glow, blending: THREE.AdditiveBlending, depthWrite: false }));
  glow.position.set(0, -.3, 0); glow.scale.setScalar(1.5);
  lampHead.add(glow);
  let lampTarget = LAMP_MODES.day;
  const scratch = new THREE.Color();
  function easeLamp(rate) {
    const t = paused ? 1 : Math.min(1, rate);
    const toward = (color, hex) => color.lerp(scratch.setHex(hex), t);
    toward(hemi.color, lampTarget.sky); toward(hemi.groundColor, lampTarget.ground); toward(key.color, lampTarget.keyColor);
    toward(bulbMaterial.color, lampTarget.bulb); toward(lampLight.color, lampTarget.light); toward(glow.material.color, lampTarget.bulb);
    hemi.intensity += (lampTarget.hemi - hemi.intensity) * t;
    key.intensity += (lampTarget.key - key.intensity) * t;
    lampLight.intensity += (lampTarget.lamp - lampLight.intensity) * t;
    glow.material.opacity += (lampTarget.glow - glow.material.opacity) * t;
  }

  const headAnchor = new THREE.Vector3(W / 2 - .1, H + .3, FRONT);
  let mood = 'neutral', intensity = .4, talking = false, paused = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let lastFace = '', frameId;
  // The screen shows either a face (eyes, mouth, optional brows/blush/sweat) or, for a few
  // states, a full-screen symbol instead of the face — never both.
  const EXPRESSIONS = {
    neutral: { eyes: 'dot', mouth: 'smile' },
    friendly: { eyes: 'dot', mouth: 'grin', blush: true },
    happy: { eyes: 'arc', mouth: 'open', blush: true },
    concerned: { eyes: 'dot', brows: 'worried', mouth: 'frown', sweat: true },
    surprised: { eyes: 'wide', brows: 'up', mouth: 'o' },
    proud: { eyes: 'lidded', mouth: 'smirk' },
    sleepy: { eyes: 'closed', mouth: 'small' },
    confused: { screen: 'question' },
    thinking: { screen: 'loading' },
    listening: { screen: 'wave' },
    alert: { screen: 'alert' },
    error: { screen: 'error' },
  };
  const ANIMATED = new Set(['loading', 'wave']);
  function symbol(kind, frame) {
    const bold = size => `900 ${size}px "Arial Black", "Helvetica Neue", sans-serif`;
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    if (kind === 'question') { ctx.fillStyle = FACE; ctx.font = bold(470); ctx.fillText('?', 512, 385); }
    else if (kind === 'alert') { ctx.fillStyle = '#D9363E'; ctx.font = bold(470); ctx.fillText('!', 512, 385); }
    else if (kind === 'error') {
      ctx.fillStyle = '#D9363E'; ctx.font = bold(190); ctx.fillText('ERROR!', 512, 375);
    } else if (kind === 'loading') {
      // Eight dots in a ring; the bright one walks round, older ones fade.
      for (let i = 0; i < 8; i++) {
        const angle = (i / 8) * Math.PI * 2 - Math.PI / 2, age = (frame - i + 8) % 8;
        ctx.globalAlpha = Math.max(.15, 1 - age * .12);
        ctx.fillStyle = FACE; ctx.beginPath();
        ctx.arc(512 + Math.cos(angle) * 170, 363 + Math.sin(angle) * 170, 34, 0, Math.PI * 2); ctx.fill();
      }
      ctx.globalAlpha = 1;
    } else if (kind === 'wave') {
      // Level bars, like a recorder listening.
      ctx.fillStyle = FACE;
      for (let i = 0; i < 7; i++) {
        const height = 70 + Math.abs(Math.sin(frame * .55 + i * .9)) * 300;
        ctx.beginPath(); ctx.roundRect(512 + (i - 3) * 104 - 28, 363 - height / 2, 56, height, 28); ctx.fill();
      }
    }
  }
  function line(points, width = 20) {
    ctx.beginPath(); ctx.lineWidth = width;
    points.forEach(([x, y, cx, cy], i) => {
      if (!i) ctx.moveTo(x, y); else if (cx === undefined) ctx.lineTo(x, y); else ctx.quadraticCurveTo(cx, cy, x, y);
    });
    ctx.stroke();
  }
  function drawFace(blink, mouthOpen) {
    const look = EXPRESSIONS[mood] || EXPRESSIONS.neutral;
    // Animated symbols redraw ~10 times a second; faces only when something changes.
    const frame = ANIMATED.has(look.screen) && !paused ? Math.floor(performance.now() / 100) : 0;
    const faceKey = look.screen ? `${look.screen}-${frame}` : `${mood}-${blink}-${mouthOpen}`;
    if (lastFace === faceKey) return;
    lastFace = faceKey;
    ctx.clearRect(0, 0, 1024, 726);
    ctx.fillStyle = look.screen === 'alert' || look.screen === 'error' ? ALERT_SCREEN : SCREEN;
    ctx.strokeStyle = FACE; ctx.lineWidth = 26;
    ctx.beginPath(); ctx.roundRect(14, 14, 996, 698, 64); ctx.fill(); ctx.stroke();
    ctx.fillStyle = FACE; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    if (look.screen) { symbol(look.screen, frame); faceTexture.needsUpdate = true; return; }
    const eyeY = 300;
    [330, 694].forEach((x, side) => {
      const eyes = blink && look.eyes !== 'closed' ? 'blink' : look.eyes;
      ctx.beginPath();
      if (eyes === 'blink') line([[x - 30, eyeY + 6], [x + 30, eyeY + 6]]);
      else if (eyes === 'arc') line([[x - 36, eyeY + 16], [x + 36, eyeY + 16, x, eyeY - 38]]);
      else if (eyes === 'closed') line([[x - 34, eyeY], [x + 34, eyeY, x, eyeY + 30]]);
      else if (eyes === 'lidded') { ctx.ellipse(x, eyeY + 8, 30, 30, 0, 0, Math.PI); ctx.fill(); line([[x - 40, eyeY + 4], [x + 40, eyeY + 4]], 16); }
      else if (eyes === 'squint' && side === 1) line([[x - 32, eyeY + 4], [x + 32, eyeY - 4]]);
      else if (eyes === 'wide') { ctx.ellipse(x, eyeY, 40, 52, 0, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = SCREEN; ctx.beginPath(); ctx.arc(x + 12, eyeY - 16, 11, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = FACE; }
      else if (eyes === 'up') { ctx.ellipse(x + 14, eyeY - 18, 28, 38, 0, 0, Math.PI * 2); ctx.fill(); }
      else { ctx.ellipse(x, eyeY, 30, 42, 0, 0, Math.PI * 2); ctx.fill(); }
    });
    ctx.lineWidth = 16;
    if (look.brows === 'worried') { line([[280, 232], [360, 208]], 16); line([[664, 208], [744, 232]], 16); }
    else if (look.brows === 'stern') { line([[280, 206], [365, 236]], 16); line([[659, 236], [744, 206]], 16); }
    else if (look.brows === 'raised') line([[650, 200], [740, 214, 700, 176]], 16);
    else if (look.brows === 'up') { line([[285, 210], [375, 210, 330, 180]], 16); line([[649, 210], [739, 210, 694, 180]], 16); }
    if (look.blush) {
      ctx.fillStyle = 'rgba(240, 140, 150, .45)';
      for (const x of [230, 794]) { ctx.beginPath(); ctx.ellipse(x, 400, 48, 24, 0, 0, Math.PI * 2); ctx.fill(); }
      ctx.fillStyle = FACE;
    }
    if (look.sweat) {
      ctx.fillStyle = '#6EB8E8'; ctx.beginPath();
      ctx.moveTo(812, 196); ctx.quadraticCurveTo(780, 250, 790, 268); ctx.arc(812, 262, 22, Math.PI * .9, Math.PI * 2.1, true); ctx.quadraticCurveTo(844, 250, 812, 196); ctx.fill();
      ctx.fillStyle = FACE;
    }
    const mouth = mouthOpen ? 'talk' : look.mouth;
    ctx.lineWidth = 14;
    if (mouth === 'talk') {
      // Speaking: a small open mouth that flaps while audio plays.
      ctx.beginPath(); ctx.ellipse(512, 455, 52, 34, 0, 0, Math.PI * 2); ctx.fillStyle = '#2E6B3C'; ctx.fill(); ctx.stroke();
    } else if (mouth === 'open') {
      const shape = new Path2D('M420 420 L604 420 C604 560 420 560 420 420 Z');
      ctx.fillStyle = '#2E6B3C'; ctx.fill(shape);
      ctx.save(); ctx.clip(shape); ctx.fillStyle = '#7CD06D'; ctx.beginPath(); ctx.ellipse(512, 535, 70, 46, 0, 0, Math.PI * 2); ctx.fill(); ctx.restore();
      ctx.stroke(shape);
    } else if (mouth === 'o') { ctx.beginPath(); ctx.ellipse(512, 470, 36, 44, 0, 0, Math.PI * 2); ctx.fillStyle = '#2E6B3C'; ctx.fill(); ctx.stroke(); }
    else if (mouth === 'wavy') line([[440, 470], [485, 455, 462, 440], [530, 470, 508, 488], [580, 455, 555, 440]], 14);
    else if (mouth === 'frown') line([[452, 478], [572, 478, 512, 428]], 14);
    else if (mouth === 'flat') line([[450, 465], [574, 465]], 14);
    else if (mouth === 'side') line([[470, 468], [570, 452, 530, 476]], 14);
    else if (mouth === 'smirk') line([[440, 450], [590, 430, 530, 500]], 14);
    else if (mouth === 'small') line([[485, 462], [539, 462, 512, 482]], 14);
    else { const wide = mouth === 'grin' ? 110 : 82; line([[512 - wide, 420], [512 + wide, 420, 512, mouth === 'grin' ? 520 : 495]], 14); }
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
    // Confused tilts its head; alert trembles slightly; otherwise a gentle sway.
    body.rotation.z = mood === 'confused' ? -.07 : mood === 'alert' && !paused ? Math.sin(elapsed * 26) * .012 : Math.sin(elapsed * .7) * .008 * intensity;
    feet.forEach((foot, i) => { foot.rotation.x = paused ? 0 : Math.sin(elapsed * 2.2 + i * 1.4) * .12 * (mood === 'happy' ? 2 : 1); });
    drawFace(!paused && elapsed % 5 > 4.82, talking && !paused && Math.floor(performance.now() / 140) % 2 === 1);
    easeLamp(delta * 3.5);
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
    // Portrait stages have no room on the left; the lamp moves to MEMO's right side.
    if (camera.aspect < 1) placeLamp(2.25, .35); else placeLamp(-1.95, -1.9);
    camera.updateProjectionMatrix();
  }); observer.observe(container);
  renderer.domElement.addEventListener('webglcontextlost', e => { e.preventDefault(); document.querySelector('#webgl-fallback').hidden = false; });
  frameId = requestAnimationFrame(animate);
  return {
    setEmotion(label, amount) { mood = label; intensity = amount; },
    setTalking(value) { talking = value; },
    // Desk lamp: off | dim | warm | day.
    setLamp(mode) { lampTarget = LAMP_MODES[mode] || LAMP_MODES.day; },
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
