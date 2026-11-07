// AURA SPATIAL viewer: one three.js scene holding any number of objects (compare side by side). Every object is made of
// pieces you can take apart: explode it, grab a piece and pull it out (hand, mouse or touch), fling it away, call it back,
// put everything back together step by step. Every change is reversible (undo stack + "restore all"). Looks: normal or HOLOGRAM.
// Renders only when something changes (battery), pauses when hidden, disposes everything it loaded.
import { loadThree } from './lib.js';
import { buildModel, buildProcedural, buildMolecule, buildMacromolecule, buildImage } from './builders.js';

const VIEWS = { front: [0, 0.12, 1], back: [0, 0.12, -1], left: [-1, 0.12, 0], right: [1, 0.12, 0], top: [0, 1, 0.0001], bottom: [0, -1, 0.0001], iso: [0.85, 0.55, 1] };
const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ').trim();
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

const HOLO_VERT = `
#include <common>
#include <clipping_planes_pars_vertex>
varying vec3 vN; varying vec3 vV; varying vec3 vW;
void main() {
  vec4 p = vec4(position, 1.0); vec3 n = normal;
  #ifdef USE_INSTANCING
    p = instanceMatrix * p; n = mat3(instanceMatrix) * n;
  #endif
  vec4 w = modelMatrix * p; vW = w.xyz;
  vN = normalize(mat3(modelMatrix) * n);
  vec4 mvPosition = viewMatrix * w; vV = normalize(cameraPosition - w.xyz);
  gl_Position = projectionMatrix * mvPosition;
  #include <clipping_planes_vertex>
}`;
const HOLO_FRAG = `
#include <common>
#include <clipping_planes_pars_fragment>
uniform vec3 uColor; uniform float uTime; uniform float uOpacity; uniform float uGlow;
varying vec3 vN; varying vec3 vV; varying vec3 vW;
void main() {
  #include <clipping_planes_fragment>
  float f = pow(1.0 - abs(dot(normalize(vN), normalize(vV))), 2.2);
  float scan = smoothstep(0.92, 1.0, sin(vW.y * 90.0 - uTime * 3.0));
  float band = 0.5 + 0.5 * sin(vW.y * 18.0 + uTime * 1.5);
  vec3 c = uColor * (0.10 + 1.35 * f + 0.10 * band) + uColor * 0.9 * scan + vec3(uGlow) * 0.35;
  gl_FragColor = vec4(c, clamp((0.08 + 0.85 * f + 0.25 * scan) * uOpacity + uGlow * 0.3, 0.0, 1.0));
}`;

export class Viewer {
  static async create(host, handlers = {}) {
    const L = await loadThree();
    return new Viewer(L, host, handlers);
  }

  constructor(L, host, handlers) {
    const { THREE } = L;
    this.L = L; this.T = THREE; this.host = host; this.h = handlers;
    this.objects = []; this.parts = []; this.tweens = []; this.notes = []; this.timeline = []; this.history = [];
    this.state = this.freshState();
    this.dirty = 2; this.clock = new THREE.Clock(); this.t = 0;

    const r = this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
    const phone = Math.min(screen.width, screen.height) < 600;
    r.setPixelRatio(Math.min(devicePixelRatio || 1, phone ? 2 : 1.75));
    r.outputColorSpace = THREE.SRGBColorSpace;
    r.toneMapping = THREE.ACESFilmicToneMapping; r.toneMappingExposure = 1.05;
    r.localClippingEnabled = true;
    r.domElement.className = 'sp-canvas';
    host.append(r.domElement);
    this.css = new L.CSS2DRenderer();
    this.css.domElement.className = 'sp-labels';
    host.append(this.css.domElement);

    const scene = this.scene = new THREE.Scene();
    const pmrem = new THREE.PMREMGenerator(r);
    scene.environment = pmrem.fromScene(new L.RoomEnvironment(), 0.04).texture;
    pmrem.dispose();
    scene.add(new THREE.HemisphereLight(0xcfefff, 0x0b1a26, 0.5));
    const key = new THREE.DirectionalLight(0xffffff, 1.4); key.position.set(3, 5, 4); scene.add(key);
    const rim = new THREE.DirectionalLight(0x3ad6ff, 1.1); rim.position.set(-4, 2, -5); scene.add(rim);

    this.camera = new THREE.PerspectiveCamera(38, 1, 0.01, 200);
    this.camera.position.set(2.4, 1.5, 2.9);
    const c = this.controls = new L.OrbitControls(this.camera, r.domElement);
    c.enableDamping = true; c.dampingFactor = 0.08; c.rotateSpeed = 0.7; c.zoomSpeed = 0.9; c.minDistance = 0.05; c.maxDistance = 40;
    c.autoRotateSpeed = 1.6;
    c.addEventListener('change', () => { this.dirty = 2; });
    this.world = new THREE.Group(); scene.add(this.world);
    this.clip = [];
    this.platform = this.makePlatform(); scene.add(this.platform);
    this.scanRing = this.makeRing(); this.scanRing.visible = false; scene.add(this.scanRing);
    this.bindPointer();
    this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(host);
    this.resize();
    this.onVis = () => { this.running = !document.hidden; this.dirty = 2; };
    document.addEventListener('visibilitychange', this.onVis);
    this.running = true;
    r.setAnimationLoop(() => this.tick());
  }

  freshState() {
    return { selection: null, hover: null, highlights: new Map(), hidden: new Set(), isolated: null, xray: false, wireframe: false, opacity: new Map(),
      colors: new Map(), explode: 0, section: null, spin: false, labels: false, playing: new Set(), holo: false, inspecting: null };
  }

  // ---------------------------------------------------------------- scene furniture
  makePlatform() {
    const T = this.T, g = new T.Group();
    const mat = (o) => new T.LineBasicMaterial({ color: 0x3ad6ff, transparent: true, opacity: o, depthWrite: false });
    for (const [r, o] of [[1.25, 0.5], [1.0, 0.18], [0.7, 0.12], [1.45, 0.08]]) {
      const pts = []; for (let i = 0; i <= 128; i++) { const a = (i / 128) * Math.PI * 2; pts.push(new T.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r)); }
      g.add(new T.Line(new T.BufferGeometry().setFromPoints(pts), mat(o)));
    }
    const ticks = [];
    for (let i = 0; i < 72; i++) { const a = (i / 72) * Math.PI * 2, r0 = i % 6 ? 1.25 : 1.18; ticks.push(new T.Vector3(Math.cos(a) * r0, 0, Math.sin(a) * r0), new T.Vector3(Math.cos(a) * 1.3, 0, Math.sin(a) * 1.3)); }
    g.add(new T.LineSegments(new T.BufferGeometry().setFromPoints(ticks), mat(0.35)));
    const disc = new T.Mesh(new T.CircleGeometry(1.25, 96), new T.MeshBasicMaterial({ color: 0x3ad6ff, transparent: true, opacity: 0.05, depthWrite: false }));
    disc.rotation.x = -Math.PI / 2; g.add(disc);
    return g;
  }
  makeRing() {
    const T = this.T, pts = [];
    for (let i = 0; i <= 160; i++) { const a = (i / 160) * Math.PI * 2; pts.push(new T.Vector3(Math.cos(a), 0, Math.sin(a))); }
    const g = new T.Group();
    g.add(new T.Line(new T.BufferGeometry().setFromPoints(pts), new T.LineBasicMaterial({ color: 0x9ef3ff, transparent: true, opacity: 0.95, depthTest: false })));
    const disc = new T.Mesh(new T.RingGeometry(0.0, 1, 96), new T.MeshBasicMaterial({ color: 0x3ad6ff, transparent: true, opacity: 0.12, side: T.DoubleSide, depthWrite: false, blending: T.AdditiveBlending }));
    disc.rotation.x = -Math.PI / 2; g.add(disc);
    g.renderOrder = 10;
    return g;
  }

  resize() {
    const w = this.host.clientWidth || 1, hh = this.host.clientHeight || 1;
    this.renderer.setSize(w, hh, false); this.css.setSize(w, hh);
    this.camera.aspect = w / hh;
    this.camera.setViewOffset(w, hh, 0, Math.round(Math.min(48, hh * 0.05)), w, hh);
    this.camera.updateProjectionMatrix(); this.dirty = 2;
    const portrait = w < hh;
    if (this.wasPortrait !== undefined && portrait !== this.wasPortrait && this.objects.length) this.view(this.state.view || 'iso');
    this.wasPortrait = portrait;
  }

  // ---------------------------------------------------------------- mouse / touch: tap = select, drag a selected piece = pull it out,
  // long-press any piece = grab it, hover (mouse) = glow + name
  bindPointer() {
    const el = this.renderer.domElement;
    let down = null, press = null, lastMove = 0;
    el.addEventListener('pointerdown', (e) => {
      down = { x: e.clientX, y: e.clientY, t: performance.now(), id: e.pointerId };
      const part = this.pickAt(e.clientX, e.clientY);
      const startGrab = () => {
        if (!down || !this.grabStart(part, this.ndcOf(down.x, down.y))) return;
        this.controls.enabled = false; down.grab = true;
        try { el.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
        this.h.onGrab?.(part, true);
      };
      if (part && part.name === this.state.selection) startGrab();                                     // drag the selected piece = move it
      else if (part) press = setTimeout(() => { if (down && !down.moved) { this.select(part.name, { from: 'press' }); startGrab(); } }, 420);
    });
    el.addEventListener('pointermove', (e) => {
      if (down) {
        if (!down.grab && Math.hypot(e.clientX - down.x, e.clientY - down.y) > 6) { down.moved = true; clearTimeout(press); }
        if (down.grab) { this.grabMove(this.ndcOf(e.clientX, e.clientY)); this.lastPtr = { x: e.clientX, y: e.clientY, t: performance.now(), px: this.lastPtr?.x, py: this.lastPtr?.y, pt: this.lastPtr?.t }; }
        return;
      }
      if (e.pointerType === 'mouse' && performance.now() - lastMove > 50) {     // hover glow
        lastMove = performance.now();
        this.hover(this.pickAt(e.clientX, e.clientY));
        el.style.cursor = this.state.hover ? (this.state.hover === this.state.selection ? 'grab' : 'pointer') : '';
      }
    });
    const up = (e) => {
      clearTimeout(press);
      if (!down) return;
      if (down.grab) {
        const lp = this.lastPtr;
        const speed = lp && lp.pt && performance.now() - lp.t < 90 ? Math.hypot(lp.x - lp.px, lp.y - lp.py) / Math.max(8, lp.t - lp.pt) : 0;
        this.grabEnd(speed > 2.2);
        this.controls.enabled = true;
        this.lastPtr = null;
        this.h.onGrab?.(null, false);
      } else if (!down.moved && performance.now() - down.t < 450) {
        const part = this.pickAt(e.clientX, e.clientY);
        this.select(part ? part.name : null, { from: 'tap' });
      }
      down = null;
    };
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', up);
    el.addEventListener('pointerleave', () => { if (!down) this.hover(null); });
  }
  ndcOf(x, y) { const r = this.renderer.domElement.getBoundingClientRect(); return { x: ((x - r.left) / r.width) * 2 - 1, y: -((y - r.top) / r.height) * 2 + 1 }; }

  // ---------------------------------------------------------------- loading
  async add(scene, { mode = 'replace', onProgress, inside = null } = {}) {
    const L = this.L;
    let built;
    if (scene.kind === 'model') built = await buildModel(L, scene, onProgress);
    else if (scene.kind === 'procedural') built = await buildProcedural(L, scene.spec);
    else if (scene.kind === 'molecule') built = buildMolecule(L, scene.spec);
    else if (scene.kind === 'macromolecule') built = buildMacromolecule(L, scene.spec);
    else if (scene.kind === 'image') built = await buildImage(L, scene);
    else throw new Error('Nothing to show.');
    if (mode === 'replace') { this.clear(); this.history = []; }
    const T = this.T, holder = new T.Group(), pivot = new T.Group();
    holder.add(built.root); pivot.add(holder); this.world.add(pivot);
    pivot.updateMatrixWorld(true);
    const box = new T.Box3().setFromObject(built.root, true), size = box.getSize(new T.Vector3()), center = box.getCenter(new T.Vector3());
    const radius = Math.max(1e-6, size.length() / 2), s = 1 / radius;
    holder.scale.setScalar(s); holder.position.copy(center).multiplyScalar(-s);
    const obj = { index: this.objects.length, scene, built, pivot, holder, radius, scale: s, units: built.units, home: pivot.quaternion.clone(),
      flat: !!built.flat, mixer: null, actions: new Map(), triangles: 0, split: built.split, insideOf: inside };
    this.objects.push(obj);
    this.layout();
    if (inside != null && this.objects[inside]) {          // fit it within the host's shell
      const host = this.objects[inside];
      pivot.quaternion.copy(host.pivot.quaternion);
      pivot.updateMatrixWorld(true);
      const hb = new T.Box3(); this.parts.filter((p) => p.obj === inside).forEach((p) => p.meshes.forEach((m) => hb.expandByObject(m, true)));
      const ib = new T.Box3().setFromObject(holder, true);
      const hs = hb.getSize(new T.Vector3()), is = ib.getSize(new T.Vector3());
      const fit = Math.min(hs.x / Math.max(1e-6, is.x), hs.y / Math.max(1e-6, is.y), hs.z / Math.max(1e-6, is.z)) * 0.88;
      pivot.scale.setScalar(fit);
      pivot.position.copy(hb.getCenter(new T.Vector3())).sub(ib.getCenter(new T.Vector3()).sub(pivot.position).multiplyScalar(fit));
      obj.home = pivot.quaternion.clone();
    }
    pivot.updateMatrixWorld(true);
    this.registerParts(obj);
    if (built.clips?.length) {
      obj.mixer = new T.AnimationMixer(built.root);
      built.clips.forEach((clip, i) => obj.actions.set(clip.name || `Animation ${i + 1}`, obj.mixer.clipAction(clip)));
    }
    if (this.state.holo) this.holo(true, { record: false });
    this.refresh();
    if (inside == null) this.view('iso', { instant: mode === 'replace' });
    this.scanReveal(obj);
    this.dirty = 2;
    return obj;
  }

  layout() {
    const side = this.objects.filter((o) => o.insideOf == null), n = side.length;
    side.forEach((o, i) => { o.pivot.position.set((i - (n - 1) / 2) * 2.5, 0, 0); });
    this.platform.scale.setScalar(Math.max(1, n * 1.05));
    this.platform.position.set(0, -1.02, 0);
    this.objects.forEach((o) => o.pivot.updateMatrixWorld(true));
  }

  /** The laser-scan reveal: a glowing ring sweeps up the object and it materialises behind it. */
  scanReveal(obj) {
    if (document.hidden) return;                       // nobody's watching: no reveal (and no half-clipped model when they come back)
    const T = this.T, box = new T.Box3().setFromObject(obj.pivot, true);
    const y0 = box.min.y - 0.02, y1 = box.max.y + 0.02, c = box.getCenter(new T.Vector3()), r = Math.max(box.max.x - box.min.x, box.max.z - box.min.z) * 0.62;
    const plane = new T.Plane(new T.Vector3(0, -1, 0), y0);
    plane.userData = { scan: true };
    this.clip.push(plane); this.markMaterials();
    this.scanRing.visible = true; this.scanRing.scale.set(r, 1, r); this.scanRing.position.set(c.x, y0, c.z);
    this.tween(1500, (k) => {
      const y = y0 + (y1 - y0) * ease(k);
      plane.constant = y; this.scanRing.position.y = y;
      this.scanRing.children[0].material.opacity = 0.95 * (1 - k * 0.6);
      if (k >= 1) { const i = this.clip.indexOf(plane); if (i >= 0) this.clip.splice(i, 1); this.scanRing.visible = false; this.markMaterials(); }
    });
  }
  markMaterials() { this.parts.forEach((p) => p.meshes.forEach((m) => [].concat(m.material).forEach((mat) => { mat.needsUpdate = true; }))); this.dirty = 2; }

  registerParts(obj) {
    const T = this.T, pivot = obj.pivot;
    const taken = new Set(this.parts.map((p) => p.lname));
    const list = obj.built.parts.length ? obj.built.parts : [{ name: 'Whole object', meshes: [] }];
    const inv = new T.Matrix4().copy(pivot.matrixWorld).invert();
    for (const bp of list) {
      let name = bp.name;
      if (taken.has(norm(name))) name = `${name} (${obj.index + 1})`;
      taken.add(norm(name));
      const part = { name, lname: norm(name), obj: obj.index, group: bp.group || '', desc: bp.desc || '', meshes: bp.meshes, shape: bp.shape,
        ex: 0, exShown: 0, off: new T.Vector3(), offShown: new T.Vector3(), ai: false };
      const box = new T.Box3();
      part.meshes.forEach((m) => box.expandByObject(m, true));
      const wc = box.isEmpty() ? pivot.getWorldPosition(new T.Vector3()) : box.getCenter(new T.Vector3());
      part.center = wc;
      part.size = box.isEmpty() ? 0 : box.getSize(new T.Vector3()).length();
      const cP = wc.clone().applyMatrix4(inv);                     // centre in the object's own space (object radius = 1)
      const len = cP.length();
      part.dirP = len < 1e-4 ? new T.Vector3(0, 0.12, 0) : cP.clone().normalize().multiplyScalar(0.25 + len * 1.1);
      if (obj.insideOf != null) part.dirP.multiplyScalar(0.7 / Math.max(0.05, pivot.scale.x));   // inside parts spread as far as the shell's
      for (const m of part.meshes) {
        m.userData.part = part;
        const cloned = [].concat(m.material).map((mat) => {
          const cm = mat.clone();
          cm.userData.orig = { opacity: cm.opacity, transparent: cm.transparent, depthWrite: cm.depthWrite, color: cm.color ? cm.color.clone() : null,
            emissive: cm.emissive ? cm.emissive.clone() : null, emissiveIntensity: cm.emissiveIntensity ?? 0, wireframe: !!cm.wireframe };
          cm.clippingPlanes = this.clip;
          return cm;
        });
        m.material = Array.isArray(m.material) ? cloned : cloned[0];
        m.userData.real = m.material;
        obj.triangles += ((m.geometry.index ? m.geometry.index.count : m.geometry.attributes.position?.count || 0) / 3) * (m.count || 1);
        // pivot-space vectors -> this mesh's parent space (constant: only the pivot ever rotates)
        const rel = new T.Matrix4().copy(m.parent.matrixWorld).invert().multiply(pivot.matrixWorld);
        m.userData.toLocal = new T.Matrix3().setFromMatrix4(rel);
        m.userData.home = { pos: m.position.clone(), quat: m.quaternion.clone(), scale: m.scale.clone() };
      }
      this.parts.push(part);
    }
  }

  clear() {
    for (const o of this.objects) {
      o.mixer?.stopAllAction();
      this.world.remove(o.pivot);
      o.pivot.traverse((n) => {
        if (n.geometry) n.geometry.dispose();
        const mats = [];
        if (n.userData.real) mats.push(...[].concat(n.userData.real));
        if (n.userData.holo) mats.push(n.userData.holo);
        mats.forEach((m) => { for (const k in m) { if (m[k] && m[k].isTexture) m[k].dispose(); } m.dispose(); });
      });
    }
    this.notes.forEach((n) => { n.obj.parent?.remove(n.obj); n.obj.element.remove(); }); this.notes = [];
    this.labelObjs?.forEach((l) => { l.parent?.remove(l); l.element.remove(); }); this.labelObjs = null;
    if (this.measureObj) { this.scene.remove(this.measureObj); this.measureObj = null; }
    this.objects = []; this.parts = []; this.timeline = [];
    const holo = this.state.holo;
    this.state = this.freshState(); this.state.holo = holo;
    this.clip.length = 0; this.controls.autoRotate = false; this.scanRing.visible = false;
    this.dirty = 2;
    this.h.onSelect?.(null, 'clear');
  }

  removeObject(i) {
    const o = this.objects[i];
    if (!o) return false;
    const keep = this.objects.filter((x) => x !== o).map((x) => x.scene);
    this.clear();
    keep.reduce((p, s, k) => p.then(() => this.add(s, { mode: k ? 'add' : 'replace' })), Promise.resolve());
    return true;
  }

  // ---------------------------------------------------------------- undo (every change is reversible)
  snapshot() {
    const s = this.state;
    return { hidden: [...s.hidden], isolated: s.isolated ? [...s.isolated] : null, xray: s.xray, wireframe: s.wireframe, opacity: [...s.opacity], colors: [...s.colors],
      highlights: [...s.highlights], explode: s.explode, section: s.section ? { ...s.section } : null, labels: s.labels, holo: s.holo, spin: s.spin,
      selection: s.selection, inspecting: s.inspecting, parts: this.parts.map((p) => [p.ex, p.off.clone()]),
      cam: [this.camera.position.clone(), this.controls.target.clone()] };
  }
  checkpoint() { this.history.push(this.snapshot()); if (this.history.length > 80) this.history.shift(); }
  canUndo() { return this.history.length > 0; }
  undo() {
    const snap = this.history.pop();
    if (!snap) return false;
    const s = this.state;
    s.hidden = new Set(snap.hidden); s.isolated = snap.isolated; s.xray = snap.xray; s.wireframe = snap.wireframe; s.opacity = new Map(snap.opacity);
    s.colors = new Map(snap.colors); s.highlights = new Map(snap.highlights); s.explode = snap.explode; s.labels = snap.labels; s.selection = snap.selection;
    s.inspecting = snap.inspecting;
    this.parts.forEach((p, i) => { if (snap.parts[i]) { p.ex = snap.parts[i][0]; p.off.copy(snap.parts[i][1]); } });
    if (s.holo !== snap.holo) this.holo(snap.holo, { record: false });
    this.spin(snap.spin);
    this.section(snap.section?.axis || null, snap.section ? snap.section.value : null);
    this.timeline = [];
    this.moveCamera(snap.cam[0], snap.cam[1]);
    this.refresh();
    this.h.onSelect?.(s.selection ? this.byName(s.selection) : null, 'undo');
    return true;
  }
  /** Everything back to how it arrived: shown, solid, assembled, every piece home. */
  restoreAll() {
    this.checkpoint();
    const holo = this.state.holo;
    this.state = this.freshState(); this.state.holo = holo;
    this.parts.forEach((p) => { p.ex = 0; p.off.set(0, 0, 0); });
    this.timeline = [];
    this.section(null, null); this.controls.autoRotate = false;
    this.objects.forEach((o) => { o.pivot.quaternion.copy(o.home); o.mixer?.stopAllAction(); });
    this.refresh(); this.view('iso');
    this.h.onSelect?.(null, 'restore');
  }
  reset() { this.restoreAll(); }
  /** What's currently changed (the state bar - tap one to reverse just that). */
  changes() {
    const s = this.state, out = [];
    if (s.isolated) out.push({ id: 'isolate', label: `ISOLATED: ${s.isolated.length > 1 ? s.isolated.length + ' parts' : s.isolated[0]}` });
    if (s.hidden.size) out.push({ id: 'hidden', label: `${s.hidden.size} HIDDEN` });
    if (s.xray) out.push({ id: 'xray', label: 'X-RAY' });
    if (s.wireframe) out.push({ id: 'wireframe', label: 'WIREFRAME' });
    if (s.opacity.size) out.push({ id: 'opacity', label: 'SEE-THROUGH' });
    if (s.colors.size || s.highlights.size) out.push({ id: 'colors', label: 'COLOURED' });
    if (s.explode > 0.01 || this.parts.some((p) => p.ex > 0.01)) out.push({ id: 'explode', label: 'EXPLODED' });
    const moved = this.parts.filter((p) => p.off.lengthSq() > 1e-6).length;
    if (moved) out.push({ id: 'moved', label: `${moved} MOVED` });
    if (s.section) out.push({ id: 'section', label: `CUT ${s.section.axis.toUpperCase()}` });
    if (s.spin) out.push({ id: 'spin', label: 'SPINNING' });
    if (s.playing.size) out.push({ id: 'motion', label: 'MOTION' });
    if (s.inspecting) out.push({ id: 'inspect', label: `INSPECTING ${s.inspecting}` });
    if (s.labels) out.push({ id: 'labels', label: 'LABELS' });
    if (this.notes.length || this.measureObj) out.push({ id: 'notes', label: 'NOTES' });
    if (s.holo) out.push({ id: 'holo', label: 'HOLOGRAM' });
    return out;
  }
  /** Reverse one change from the state bar. */
  clearChange(id) {
    this.checkpoint();
    const s = this.state;
    if (id === 'isolate') s.isolated = null;
    if (id === 'hidden') s.hidden.clear();
    if (id === 'xray') s.xray = false;
    if (id === 'wireframe') s.wireframe = false;
    if (id === 'opacity') s.opacity.clear();
    if (id === 'colors') { s.colors.clear(); s.highlights.clear(); }
    if (id === 'explode') { s.explode = 0; this.parts.forEach((p) => { p.ex = 0; }); this.timeline = []; }
    if (id === 'moved') this.parts.forEach((p) => p.off.set(0, 0, 0));
    if (id === 'section') this.section(null, null);
    if (id === 'spin') this.spin(false);
    if (id === 'motion') this.animate(null, false);
    if (id === 'inspect') { s.inspecting = null; s.xray = false; this.spin(false); }
    if (id === 'labels') s.labels = false;
    if (id === 'notes') this.clearNotes();
    if (id === 'holo') this.holo(false, { record: false });
    this.refresh();
    if (['isolate', 'explode', 'inspect', 'hidden'].includes(id)) this.view(s.view || 'iso');
  }

  // ---------------------------------------------------------------- finding parts by words
  find(targets, { objIndex } = {}) {
    const out = new Set();
    const pool = objIndex != null ? this.parts.filter((p) => p.obj === objIndex) : this.parts;
    for (const raw of [].concat(targets || [])) {
      const t = norm(raw);
      if (!t) continue;
      if (/^(it|this|that|selection|selected|the selected part)$/.test(t)) { if (this.state.selection) out.add(this.byName(this.state.selection)); continue; }
      if (/^(all|everything|every part|all parts|the whole thing)$/.test(t)) { pool.forEach((p) => out.add(p)); continue; }
      let hit = pool.filter((p) => p.lname === t);
      if (!hit.length) hit = pool.filter((p) => p.lname.startsWith(t) || t.startsWith(p.lname));
      if (!hit.length) hit = pool.filter((p) => p.lname.includes(t) || norm(p.group) === t);
      if (!hit.length) {
        const words = t.split(' ').filter((w) => w.length > 2);
        const scored = pool.map((p) => [p, words.filter((w) => p.lname.includes(w) || norm(p.group).includes(w)).length / Math.max(1, words.length)]).filter(([, s]) => s >= 0.5);
        const best = Math.max(0, ...scored.map(([, s]) => s));
        hit = scored.filter(([, s]) => s === best).map(([p]) => p);
      }
      hit.forEach((p) => out.add(p));
    }
    out.delete(undefined);
    return [...out];
  }
  byName(name) { const n = norm(name); return this.parts.find((p) => p.lname === n); }

  /** AI-identified names for pieces ("Piece 4" -> "Crankshaft"); state that refers to the old name follows it. */
  rename(oldName, newName, desc) {
    const p = this.byName(oldName);
    if (!p || !newName) return null;
    let name = String(newName).slice(0, 60), k = 2;
    while (this.parts.some((q) => q !== p && q.lname === norm(name))) name = `${newName} ${k++}`;
    const o = p.lname, n = norm(name), s = this.state;
    for (const m of [s.opacity, s.colors, s.highlights]) if (m.has(o)) { m.set(n, m.get(o)); m.delete(o); }
    if (s.hidden.has(o)) { s.hidden.delete(o); s.hidden.add(n); }
    if (s.isolated) s.isolated = s.isolated.map((x) => (norm(x) === o ? name : x));
    if (s.selection === p.name) s.selection = name;
    if (s.inspecting === p.name) s.inspecting = name;
    p.name = name; p.lname = n; p.ai = true;
    if (desc) p.desc = desc;
    return p;
  }

  // ---------------------------------------------------------------- visual state -> materials
  refresh() {
    const s = this.state, sel = s.selection ? this.byName(s.selection) : null, hov = s.hover ? this.byName(s.hover) : null;
    const iso = s.isolated ? new Set(s.isolated.map(norm)) : null;
    for (const p of this.parts) {
      const hidden = s.hidden.has(p.lname) || (iso && !iso.has(p.lname));
      const faded = s.xray && sel !== p && !s.highlights.has(p.lname);
      const op = s.opacity.get(p.lname);
      const hl = sel === p ? '#3ad6ff' : s.highlights.get(p.lname) || (hov === p ? '#9ef3ff' : null);
      const color = s.colors.get(p.lname);
      for (const m of p.meshes) {
        m.visible = !hidden;
        if (s.holo && m.material.uniforms) {
          const u = m.material.uniforms;
          u.uColor.value.set(color || (sel === p ? '#ffb547' : hov === p ? '#ffffff' : s.highlights.get(p.lname) || '#3ad6ff'));
          u.uOpacity.value = faded ? 0.25 : Math.min(1, op ?? 1);
          u.uGlow.value = sel === p ? 0.5 : hov === p ? 0.25 : 0;
          continue;
        }
        for (const mat of [].concat(m.material)) {
          const o = mat.userData.orig;
          if (!o) continue;
          let opacity = o.opacity;
          if (faded) opacity = Math.min(opacity, 0.13);
          if (op != null) opacity = Math.min(o.opacity, op);
          const transparent = o.transparent || opacity < 1, wire = s.wireframe || o.wireframe;
          const recompile = mat.transparent !== transparent || mat.wireframe !== wire;
          mat.opacity = opacity; mat.transparent = transparent; mat.depthWrite = opacity >= 1 ? o.depthWrite : false; mat.wireframe = wire;
          if (mat.color && o.color) mat.color.copy(color ? new this.T.Color(color) : o.color);
          if (mat.emissive) {
            if (hl) { mat.emissive.set(hl); mat.emissiveIntensity = hov === p && sel !== p ? 0.28 : 0.55; } else { mat.emissive.copy(o.emissive); mat.emissiveIntensity = o.emissiveIntensity; }
          } else if (hl && mat.color) mat.color.lerp(new this.T.Color(hl), 0.5);
          if (recompile) mat.needsUpdate = true;
        }
      }
    }
    this.updateLabels();
    this.dirty = 2;
    this.h.onChange?.(this.context());
  }

  /** HOLOGRAM look: every piece becomes glowing light (fresnel edges + scan lines) - the Iron Man workshop. */
  holo(on = !this.state.holo, { record = true } = {}) {
    if (record) this.checkpoint();
    this.state.holo = !!on;
    for (const p of this.parts) for (const m of p.meshes) {
      if (on) {
        if (!m.userData.holo) {
          m.userData.holo = new this.T.ShaderMaterial({ vertexShader: HOLO_VERT, fragmentShader: HOLO_FRAG, transparent: true, depthWrite: false,
            blending: this.T.AdditiveBlending, side: this.T.DoubleSide, clipping: true,
            uniforms: { uColor: { value: new this.T.Color('#3ad6ff') }, uTime: { value: 0 }, uOpacity: { value: 1 }, uGlow: { value: 0 } } });
          m.userData.holo.clippingPlanes = this.clip;
        }
        m.material = m.userData.holo;
      } else m.material = m.userData.real;
    }
    this.platform.visible = true;
    this.refresh();
  }

  // ---------------------------------------------------------------- operations (all generic; the screen calls checkpoint() first)
  select(name, { from } = {}) {
    const p = name ? this.find(name)[0] : null;
    this.state.selection = p ? p.name : null;
    this.refresh();
    this.h.onSelect?.(p || null, from);
    return p;
  }
  hover(part) {
    const n = part ? part.name : null;
    if (n === this.state.hover) return;
    this.state.hover = n; this.refresh();
    this.h.onHover?.(part);
  }
  highlight(names, color = '#ffb547') { const ps = this.find(names); ps.forEach((p) => this.state.highlights.set(p.lname, color)); this.refresh(); return ps; }
  unhighlight() { this.state.highlights.clear(); this.refresh(); }
  hide(names) {
    const ps = names?.length ? this.find(names) : (this.state.selection ? this.find(this.state.selection) : []);
    ps.forEach((p) => this.state.hidden.add(p.lname));
    if (this.state.isolated) this.state.isolated = this.state.isolated.filter((n) => !ps.some((p) => p.lname === norm(n)));
    this.refresh(); return ps;
  }
  show(names) {
    const ps = names?.length ? this.find(names) : this.parts.filter((p) => this.state.hidden.has(p.lname));
    ps.forEach((p) => this.state.hidden.delete(p.lname));
    if (this.state.isolated) this.state.isolated.push(...ps.map((p) => p.name).filter((n) => !this.state.isolated.includes(n)));
    this.refresh(); return ps;
  }
  isHidden(p) { const s = this.state; return s.hidden.has(p.lname) || !!(s.isolated && !s.isolated.map(norm).includes(p.lname)); }
  isolate(names) {
    const ps = names?.length ? this.find(names) : (this.state.selection ? this.find(this.state.selection) : []);
    if (!ps.length) return ps;
    this.state.isolated = ps.map((p) => p.name); ps.forEach((p) => this.state.hidden.delete(p.lname));
    this.refresh(); this.focus(ps.map((p) => p.name), { xray: false }); return ps;
  }
  unisolate() { this.state.isolated = null; this.refresh(); this.view(this.state.view || 'iso'); }
  showAll() { this.state.hidden.clear(); this.state.isolated = null; this.state.opacity.clear(); this.state.xray = false; this.refresh(); }
  opacity(names, v) {
    const ps = names?.length ? this.find(names) : this.parts;
    ps.forEach((p) => (v >= 0.99 ? this.state.opacity.delete(p.lname) : this.state.opacity.set(p.lname, Math.max(0.03, v))));
    this.refresh(); return ps;
  }
  xray(on = !this.state.xray) { this.state.xray = !!on; this.refresh(); }
  wireframe(on = !this.state.wireframe) { this.state.wireframe = !!on; this.refresh(); }
  color(names, c) { const ps = this.find(names); ps.forEach((p) => this.state.colors.set(p.lname, c)); this.refresh(); return ps; }

  /** Explode the whole thing (or just some parts) - every piece flies out along its own direction. */
  explode(a, { targets = null, frame = true } = {}) {
    const v = Math.max(0, Math.min(1, +a || 0));
    const ps = targets?.length ? this.find(targets) : this.parts;
    if (!targets?.length) this.state.explode = v;
    ps.forEach((p) => { p.ex = v; });
    this.timeline = this.timeline.filter((t) => !ps.includes(t.part));
    if (frame && !targets?.length) this.frameExploded(v);
    this.dirty = 2; this.h.onChange?.(this.context());
    return ps;
  }
  frameExploded(v) {
    const T = this.T, box = new T.Box3();
    this.objects.forEach((o) => box.union(new T.Box3().setFromCenterAndSize(o.pivot.position, new T.Vector3(1.16, 1.16, 1.16))));
    const { c, dist } = this.frameFor(box);
    const dir = this.camera.position.clone().sub(this.controls.target).normalize();
    this.moveCamera(c.clone().add(dir.multiplyScalar(dist * (1 + v * 0.75))), c);
  }
  explodeBy(d) { this.explode(this.state.explode + d, { frame: false }); }

  /** Take it apart (or put it together) one piece at a time - outermost first, like an assembly manual played in reverse. */
  sequence(apart = true, stepMs = 0) {
    const order = [...this.parts].sort((a, b) => b.dirP.length() - a.dirP.length() || b.size - a.size);
    if (!apart) order.reverse();
    const step = stepMs || Math.max(60, Math.min(450, 4200 / Math.max(1, order.length)));
    const now = performance.now();
    this.timeline = order.map((p, i) => ({ t: now + i * step, part: p, ex: apart ? 1 : 0, home: !apart }));
    this.state.explode = apart ? 1 : 0;
    if (apart) this.frameExploded(1);
    else setTimeout(() => { if (!this.state.explode) this.view(this.state.view || 'iso'); }, order.length * step + 300);
    this.dirty = 2;
    return order.length;
  }

  /** Put pieces back: everything (or just some), smoothly. */
  reassemble(targets = null) {
    const ps = targets?.length ? this.find(targets) : this.parts;
    ps.forEach((p) => { p.ex = 0; p.off.set(0, 0, 0); });
    if (!targets?.length) { this.state.explode = 0; this.timeline = []; this.view(this.state.view || 'iso'); }
    this.dirty = 2; this.h.onChange?.(this.context());
    return ps;
  }

  /** Move a piece by words: "pull the piston out", "move the lid up". */
  movePart(names, dir = 'out', amount = 0.4) {
    const ps = this.find(names), T = this.T;
    const camDir = (axis) => new T.Vector3().setFromMatrixColumn(this.camera.matrixWorld, axis);
    for (const p of ps) {
      const o = this.objects[p.obj], lin = new T.Matrix3().setFromMatrix4(new T.Matrix4().copy(o.pivot.matrixWorld).invert());
      let w;
      if (dir === 'up') w = new T.Vector3(0, 1, 0); else if (dir === 'down') w = new T.Vector3(0, -1, 0);
      else if (dir === 'left') w = camDir(0).negate(); else if (dir === 'right') w = camDir(0);
      else if (dir === 'toward' || dir === 'forward') w = camDir(2); else if (dir === 'away' || dir === 'back') w = camDir(2).negate();
      else { p.off.add(p.dirP.clone().normalize().multiplyScalar(amount * 1.5)); continue; }
      p.off.add(w.applyMatrix3(lin).normalize().multiplyScalar(amount * 1.5));
    }
    this.dirty = 2; this.h.onChange?.(this.context());
    return ps;
  }

  // ---------------------------------------------------------------- grabbing pieces (hands, mouse, touch)
  grabStart(part, ndc) {
    if (!part) return false;
    const T = this.T, o = this.objects[part.obj];
    const center = this.partCenter(part);
    const n = this.camera.getWorldDirection(new T.Vector3());
    const g = { part, plane: new T.Plane().setFromNormalAndCoplanarPoint(n, center), startOff: part.off.clone(), o,
      inv: new T.Matrix3().setFromMatrix4(new T.Matrix4().copy(o.pivot.matrixWorld).invert()) };
    const hit = this.rayPlane(ndc, g.plane);
    if (!hit) return false;
    g.start = hit;
    this.checkpoint();
    this.grab = g;
    this.state.selection = part.name; this.refresh();
    return true;
  }
  rayPlane(ndc, plane) {
    const T = this.T, ray = new T.Raycaster();
    ray.setFromCamera(new T.Vector2(ndc.x, ndc.y), this.camera);
    return ray.ray.intersectPlane(plane, new T.Vector3());
  }
  /** depth: push away (+) / pull toward you (-), in object radii. */
  grabMove(ndc, depth = 0) {
    const g = this.grab;
    if (!g) return;
    const hit = this.rayPlane(ndc, g.plane);
    if (!hit) return;
    const n = this.camera.getWorldDirection(new this.T.Vector3());
    const w = hit.sub(g.start).addScaledVector(n, depth);
    g.part.off.copy(g.startOff).add(w.applyMatrix3(g.inv));
    g.part.offShown.copy(g.part.off);                      // direct manipulation: no lag
    this.pose(); this.dirty = 2;
  }
  /** thrown = flung away fast: the piece flies off and is set aside ("bring back the …" or the HIDDEN chip returns it). */
  grabEnd(thrown = false) {
    const g = this.grab;
    this.grab = null;
    if (!g) return null;
    const p = g.part;
    if (thrown) {
      const fly = p.off.clone().sub(g.startOff);
      p.off.add(fly.lengthSq() > 1e-6 ? fly.normalize().multiplyScalar(4) : p.dirP.clone().normalize().multiplyScalar(4));
      setTimeout(() => {
        this.state.hidden.add(p.lname); p.off.copy(g.startOff); p.offShown.copy(g.startOff);
        if (this.state.selection === p.name) this.state.selection = null;
        this.pose(); this.refresh(); this.h.onThrow?.(p); this.h.onSelect?.(null, 'throw');
      }, 380);
    }
    this.h.onChange?.(this.context());
    return p;
  }

  // ---------------------------------------------------------------- camera
  section(axis, v) {
    const keep = this.clip.filter((pl) => pl.userData?.scan);
    this.clip.length = 0; this.clip.push(...keep);
    if (axis && v != null) {
      const n = { x: [-1, 0, 0], y: [0, -1, 0], z: [0, 0, -1] }[axis] || [-1, 0, 0];
      this.clip.push(new this.T.Plane(new this.T.Vector3(...n), +v || 0));
      this.state.section = { axis, value: +v || 0 };
    } else this.state.section = null;
    this.markMaterials(); this.h.onChange?.(this.context());
  }
  rotate(axis = 'y', degrees = 90, objIndex) {
    const T = this.T, ax = new T.Vector3(...({ x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] }[axis] || [0, 1, 0]));
    const total = ((+degrees || 0) * Math.PI) / 180;
    const objs = objIndex != null ? this.objects.filter((o) => o.index === objIndex || o.insideOf === objIndex) : this.objects;
    let done = 0;
    this.tween(600, (k) => { const step = ease(k) * total - done; done += step; const q = new T.Quaternion().setFromAxisAngle(ax, step); objs.forEach((o) => o.pivot.quaternion.premultiply(q)); });
  }
  spin(on = !this.state.spin) { this.state.spin = !!on; this.controls.autoRotate = !!on; this.dirty = 2; }
  frameFor(box) {
    const T = this.T, c = box.getCenter(new T.Vector3()), r = Math.max(0.05, box.getSize(new T.Vector3()).length() / 2);
    const dist = (r / Math.sin((this.camera.fov * Math.PI) / 360)) * (this.camera.aspect < 1 ? 1.35 / Math.max(0.55, this.camera.aspect) : 1.08);
    return { c, dist };
  }
  visibleBox(parts) {
    const box = new this.T.Box3();
    (parts || this.parts).forEach((p) => p.meshes.forEach((m) => { if (m.visible) box.expandByObject(m, true); }));
    if (box.isEmpty()) this.objects.forEach((o) => box.expandByObject(o.pivot, true));
    return box;
  }
  moveCamera(pos, target, instant) {
    const p0 = this.camera.position.clone(), t0 = this.controls.target.clone();
    if (instant) { this.camera.position.copy(pos); this.controls.target.copy(target); this.controls.update(); this.dirty = 2; return; }
    this.tween(750, (k) => { const e = ease(k); this.camera.position.lerpVectors(p0, pos, e); this.controls.target.lerpVectors(t0, target, e); this.controls.update(); });
  }
  view(preset = 'iso', { instant } = {}) {
    const T = this.T, { c, dist } = this.frameFor(this.visibleBox());
    const d = new T.Vector3(...(VIEWS[preset] || VIEWS.iso)).normalize();
    this.state.view = preset;
    this.moveCamera(c.clone().add(d.multiplyScalar(dist)), c, instant);
  }
  zoom(factor = 1.5) {
    const off = this.camera.position.clone().sub(this.controls.target).divideScalar(Math.max(0.1, +factor || 1));
    this.moveCamera(this.controls.target.clone().add(off), this.controls.target.clone());
  }
  focus(names, { xray = true } = {}) {
    const ps = this.find(names);
    if (!ps.length) return ps;
    const { c, dist } = this.frameFor(this.visibleBox(ps));
    const dir = this.camera.position.clone().sub(this.controls.target).normalize();
    const pos = c.clone().add(dir.multiplyScalar(Math.max(dist * 1.5, 0.9)));
    this.moveCamera(pos, c);
    if (xray) {                                              // hidden inside something else? see through the rest
      const T = this.T, ray = new T.Raycaster(pos, c.clone().sub(pos).normalize());
      const blockers = this.parts.filter((p) => !ps.includes(p)).flatMap((p) => p.meshes.filter((m) => m.visible));
      const hit = ray.intersectObjects([...blockers, ...ps.flatMap((p) => p.meshes)], false)[0];
      if (hit && !ps.includes(hit.object.userData.part) && !this.state.xray) { this.state.xray = true; this.refresh(); }
    }
    return ps;
  }
  /** INSPECT: one piece, everything else ghosted, the camera orbiting it. */
  inspect(name) {
    const p = this.find(name)[0];
    if (!p) return null;
    this.state.selection = p.name; this.state.inspecting = p.name; this.state.xray = true;
    this.refresh();
    const { c, dist } = this.frameFor(this.visibleBox([p]));
    const dir = this.camera.position.clone().sub(this.controls.target).normalize();
    this.moveCamera(c.clone().add(dir.multiplyScalar(Math.max(dist * 1.35, 0.4))), c);
    setTimeout(() => { if (this.state.inspecting) this.spin(true); }, 780);
    this.h.onSelect?.(p, 'inspect');
    return p;
  }

  animations() {
    const out = [];
    this.objects.forEach((o) => {
      o.actions.forEach((_a, name) => out.push({ name, obj: o.index, authored: true }));
      (o.built.anims || []).forEach((a) => out.push({ name: a.name, obj: o.index, authored: false, spec: a }));
    });
    return out;
  }
  animate(name, on = true) {
    const all = this.animations();
    let list = !name || /^(auto|all|it)$/i.test(name) ? all : all.filter((a) => norm(a.name).includes(norm(name)) || norm(name).includes(norm(a.name)));
    if (!list.length && all.length === 1) list = all;
    for (const a of list) {
      const key = `${a.obj}:${a.name}`;
      if (a.authored) { const act = this.objects[a.obj].actions.get(a.name); if (on) act.reset().play(); else act.stop(); }
      if (on) this.state.playing.add(key); else this.state.playing.delete(key);
    }
    if (!on && !name) { this.state.playing.clear(); this.objects.forEach((o) => o.mixer?.stopAllAction()); this.pose(); }
    this.dirty = 2; this.h.onChange?.(this.context());
    return list;
  }

  // ---------------------------------------------------------------- labels, notes, measuring, copies
  labels(on = !this.state.labels) { this.state.labels = !!on; this.updateLabels(); this.h.onChange?.(this.context()); }
  updateLabels() {
    if (!this.labelObjs) this.labelObjs = [];
    this.labelObjs.forEach((l) => { l.parent?.remove(l); l.element.remove(); }); this.labelObjs = [];
    this.dirty = 2;
    const sel = this.state.selection && this.byName(this.state.selection);
    if (!(this.state.labels || sel) || !this.parts.length) return;
    let ps = this.state.labels ? this.parts.filter((p) => p.meshes.some((m) => m.visible)) : [];
    const named = ps.filter((p) => !/^(piece|section) \d+$/i.test(p.name));
    if (named.length >= 3) ps = named;                    // unnamed pieces stay quiet once real / AI names exist
    if (ps.length > 40) ps = [...ps].sort((a, b) => b.size - a.size).slice(0, 40);
    if (sel && !ps.includes(sel)) ps.push(sel);
    for (const p of ps) {
      const div = document.createElement('div');
      div.className = 'sp-tag' + (p === sel ? ' on' : '') + (p.ai ? ' ai' : '');
      div.textContent = p.name;
      div.onclick = () => this.select(p.name, { from: 'label' });
      const o = new this.L.CSS2DObject(div);
      o.userData.part = p;
      this.scene.add(o); this.labelObjs.push(o);
    }
    this.placeLabels();
  }
  placeLabels() {
    for (const l of this.labelObjs || []) l.position.copy(this.partCenter(l.userData.part));
    for (const n of this.notes) n.obj.position.copy(this.partCenter(n.part));
  }
  declutter() {
    const sel = this.state.selection && this.byName(this.state.selection);
    const ls = [...this.labelObjs].sort((a, b) => (b.userData.part === sel) - (a.userData.part === sel) || b.userData.part.size - a.userData.part.size);
    const shown = [];
    for (const l of ls) {
      l.element.style.visibility = '';
      const r = l.element.getBoundingClientRect();
      if (!r.width) continue;
      const hit = shown.some((s) => r.left < s.right + 4 && r.right > s.left - 4 && r.top < s.bottom + 2 && r.bottom > s.top - 2);
      if (hit && l.userData.part !== sel) l.element.style.visibility = 'hidden'; else shown.push(r);
    }
  }
  partCenter(p) {
    const box = new this.T.Box3();
    p.meshes.forEach((m) => box.expandByObject(m, true));
    return box.isEmpty() ? p.center.clone() : box.getCenter(new this.T.Vector3());
  }
  annotate(target, text) {
    const p = this.find(target)[0] || (this.state.selection && this.byName(this.state.selection));
    if (!p) return null;
    const div = document.createElement('div'); div.className = 'sp-note'; div.textContent = String(text || p.name).slice(0, 120);
    const obj = new this.L.CSS2DObject(div); this.scene.add(obj);
    this.notes.push({ part: p, obj }); this.placeLabels(); this.dirty = 2;
    return p;
  }
  clearNotes() {
    this.notes.forEach((n) => { n.obj.parent?.remove(n.obj); n.obj.element.remove(); }); this.notes = [];
    if (this.measureObj) { this.measureObj.traverse((x) => x.element?.remove()); this.scene.remove(this.measureObj); this.measureObj = null; }
    this.dirty = 2;
  }
  measure(a, b) {
    const pa = this.find(a)[0], pb = this.find(b)[0];
    if (!pa || !pb) return null;
    const T = this.T, ca = this.partCenter(pa), cb = this.partCenter(pb);
    const o = this.objects[pa.obj], d = ca.distanceTo(cb) / o.scale;
    if (this.measureObj) { this.measureObj.traverse((x) => x.element?.remove()); this.scene.remove(this.measureObj); }
    const g = new T.Group();
    g.add(new T.Line(new T.BufferGeometry().setFromPoints([ca, cb]), new T.LineDashedMaterial({ color: 0xffb547, dashSize: 0.03, gapSize: 0.02, depthTest: false })));
    g.children[0].computeLineDistances();
    const div = document.createElement('div'); div.className = 'sp-note measure';
    const units = o.units === 'model units' ? 'model units (real scale unknown)' : o.units;
    div.textContent = `${d < 10 ? d.toFixed(2) : Math.round(d)} ${units}`;
    const lab = new this.L.CSS2DObject(div); lab.position.copy(ca.clone().lerp(cb, 0.5)); g.add(lab);
    this.scene.add(g); this.measureObj = g; this.dirty = 2;
    return { distance: d, units };
  }
  duplicate(target) {
    const p = this.find(target)[0];
    if (!p) return null;
    const T = this.T, o = this.objects[p.obj];
    const meshes = p.meshes.map((m) => {
      const c = m.clone();
      const real = [].concat(m.userData.real).map((x) => x.clone());
      c.material = Array.isArray(m.userData.real) ? real : real[0];
      c.userData = {};
      m.parent.add(c); return c;
    });
    const shift = new T.Vector3(1.2, 0, 0);
    meshes.forEach((c) => { const pw = c.getWorldPosition(new T.Vector3()).add(shift); c.position.copy(c.parent.worldToLocal(pw)); });
    o.pivot.updateMatrixWorld(true);
    const saved = o.built; o.built = { ...saved, parts: [{ name: `${p.name} (copy)`, meshes, group: p.group, desc: p.desc }] };
    this.registerParts(o); o.built = saved;
    if (this.state.holo) this.holo(true, { record: false });
    this.refresh(); return p;
  }

  // ---------------------------------------------------------------- picking + gesture-level control
  pickAt(clientX, clientY) { const n = this.ndcOf(clientX, clientY); return this.pickNdc(n.x, n.y); }
  pickNdc(x, y) {
    const T = this.T, ray = new T.Raycaster();
    ray.setFromCamera(new T.Vector2(x, y), this.camera);
    const meshes = this.parts.flatMap((p) => p.meshes.filter((m) => m.visible));
    const hits = ray.intersectObjects(meshes, false).filter((h) => !this.clip.length || this.clip.every((pl) => pl.distanceToPoint(h.point) >= 0));
    const solid = hits.find((h) => { const p = h.object.userData.part; return p && !(this.state.xray && this.state.selection !== p.name) && (this.state.opacity.get(p.lname) ?? 1) > 0.3; });
    return (solid || hits[0])?.object.userData.part || null;
  }
  orbitBy(dx, dy) {
    const T = this.T, off = this.camera.position.clone().sub(this.controls.target), sph = new T.Spherical().setFromVector3(off);
    sph.theta -= dx; sph.phi = Math.max(0.05, Math.min(Math.PI - 0.05, sph.phi - dy));
    off.setFromSpherical(sph); this.camera.position.copy(this.controls.target).add(off); this.controls.update(); this.dirty = 2;
  }
  panBy(dx, dy) {
    const T = this.T, dist = this.camera.position.distanceTo(this.controls.target);
    const right = new T.Vector3().setFromMatrixColumn(this.camera.matrix, 0), up = new T.Vector3().setFromMatrixColumn(this.camera.matrix, 1);
    const move = right.multiplyScalar(-dx * dist).add(up.multiplyScalar(dy * dist));
    this.camera.position.add(move); this.controls.target.add(move); this.controls.update(); this.dirty = 2;
  }
  dollyBy(f) {
    const off = this.camera.position.clone().sub(this.controls.target);
    const len = Math.max(this.controls.minDistance, Math.min(this.controls.maxDistance, off.length() / f));
    this.camera.position.copy(this.controls.target).add(off.setLength(len)); this.controls.update(); this.dirty = 2;
  }
  rollBy(rad) {
    const T = this.T, axis = this.camera.position.clone().sub(this.controls.target).normalize(), q = new T.Quaternion().setFromAxisAngle(axis, rad);
    this.objects.forEach((o) => o.pivot.quaternion.premultiply(q)); this.dirty = 2;
  }

  /** A picture of the current view with the biggest visible pieces numbered - for AURA's AI to look at and name them. */
  numberedShot(max = 24) {
    const ps = this.parts.filter((p) => !this.isHidden(p) && p.size > 0).sort((a, b) => b.size - a.size).slice(0, max);
    this.renderer.render(this.scene, this.camera);
    const src = this.renderer.domElement, W = Math.min(1280, src.width), H = Math.round((src.height / src.width) * W);
    const cv = document.createElement('canvas'); cv.width = W; cv.height = H;
    const g = cv.getContext('2d');
    g.fillStyle = '#05080c'; g.fillRect(0, 0, W, H);
    g.drawImage(src, 0, 0, W, H);
    const items = [];
    ps.forEach((p) => {
      const v = this.partCenter(p).project(this.camera);
      if (v.z > 1 || Math.abs(v.x) > 1 || Math.abs(v.y) > 1) return;
      const n = items.length + 1, x = (v.x * 0.5 + 0.5) * W, y = (-v.y * 0.5 + 0.5) * H;
      g.fillStyle = '#ffd400'; g.strokeStyle = '#000'; g.lineWidth = 3;
      g.beginPath(); g.arc(x, y, 13, 0, Math.PI * 2); g.fill(); g.stroke();
      g.fillStyle = '#000'; g.font = 'bold 15px sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(String(n), x, y + 1);
      items.push({ n, name: p.name });
    });
    return { image: cv.toDataURL('image/jpeg', 0.82).split(',')[1], items };
  }

  // ---------------------------------------------------------------- AI DISSECT: cut a one-piece model into its real, named parts
  /** How much real structure an object has: named parts (not 'Piece N' / 'Section N'). */
  structure(objIndex) {
    const ps = this.parts.filter((p) => p.obj === objIndex);
    const named = ps.filter((p) => !/^(piece|section|main body|whole object)( \d+)?$/i.test(p.name) && !/ · (piece|section) \d+$/i.test(p.name));
    return { total: ps.length, named: named.length, dissected: !!this.objects[objIndex]?.dissected };
  }
  /** A straight-on front picture of one object (and the camera it was taken with) for the vision AI to mark parts on. */
  frontShot(objIndex) {
    const T = this.T, o = this.objects[objIndex];
    const ps = this.parts.filter((p) => p.obj === objIndex);
    ps.forEach((p) => { p.ex = p.exShown = 0; p.off.set(0, 0, 0); p.offShown.set(0, 0, 0); });
    this.pose();
    const box = new T.Box3(); ps.forEach((p) => p.meshes.forEach((m) => box.expandByObject(m, true)));
    const c = box.getCenter(new T.Vector3()), size = box.getSize(new T.Vector3());
    const W = 1024, H = 1024;
    const cam = new T.OrthographicCamera(-1, 1, 1, -1, 0.01, 100);
    const half = Math.max(size.x, size.y) * 0.55;
    cam.left = -half; cam.right = half; cam.top = half; cam.bottom = -half;
    const dir = new T.Vector3(0, 0, 1).applyQuaternion(o.pivot.quaternion);
    cam.position.copy(c).addScaledVector(dir, Math.max(size.z, 1) * 3);
    cam.up.set(0, 1, 0).applyQuaternion(o.pivot.quaternion);
    cam.lookAt(c); cam.updateProjectionMatrix(); cam.updateMatrixWorld(true);
    const hidden = [];
    this.parts.forEach((p) => { if (p.obj !== objIndex) p.meshes.forEach((m) => { if (m.visible) { m.visible = false; hidden.push(m); } }); });
    const plat = this.platform.visible; this.platform.visible = false;
    const size0 = this.renderer.getSize(new T.Vector2()), pr = this.renderer.getPixelRatio();
    this.renderer.setPixelRatio(1); this.renderer.setSize(W, H, false);
    this.renderer.render(this.scene, cam);
    const cv = document.createElement('canvas'); cv.width = W; cv.height = H;
    const g = cv.getContext('2d'); g.fillStyle = '#11161c'; g.fillRect(0, 0, W, H); g.drawImage(this.renderer.domElement, 0, 0, W, H);
    this.renderer.setPixelRatio(pr); this.renderer.setSize(size0.x, size0.y, false);
    hidden.forEach((m) => { m.visible = true; }); this.platform.visible = plat;
    this.resize(); this.dirty = 2;
    return { image: cv.toDataURL('image/jpeg', 0.85).split(',')[1], cam };
  }
  /** Cut the object's real geometry by the AI's boxes: every triangle goes to the most specific box it falls in (or the nearest). */
  dissectWith(objIndex, aiParts, cam) {
    const T = this.T, o = this.objects[objIndex];
    if (!o || !aiParts?.length) return 0;
    if (this.state.holo) this.holo(false, { record: false });
    const old = this.parts.filter((p) => p.obj === objIndex);
    const boxes = aiParts.map((a) => ({ ...a, area: (a.box[2] - a.box[0]) * (a.box[3] - a.box[1]), cy: (a.box[0] + a.box[2]) / 2, cx: (a.box[1] + a.box[3]) / 2 }));
    const byName = new Map();
    const v = new T.Vector3(), va = new T.Vector3(), vb = new T.Vector3(), vc = new T.Vector3();
    for (const p of old) {
      for (const m of p.meshes) {
        if (m.isInstancedMesh || !m.geometry?.attributes?.position) { (byName.get(p.name) || byName.set(p.name, { meshes: [], desc: p.desc }).get(p.name)).meshes.push(m); continue; }
        m.material = m.userData.real;
        m.updateMatrixWorld(true);
        const geo = m.geometry, pos = geo.attributes.position, idx = geo.index, n = (idx ? idx.count : pos.count) / 3;
        const vi = idx ? (i) => idx.getX(i) : (i) => i;
        const label = new Int32Array(n);
        for (let t = 0; t < n; t++) {
          va.fromBufferAttribute(pos, vi(t * 3)); vb.fromBufferAttribute(pos, vi(t * 3 + 1)); vc.fromBufferAttribute(pos, vi(t * 3 + 2));
          v.copy(va).add(vb).add(vc).divideScalar(3).applyMatrix4(m.matrixWorld).project(cam);
          const x = (v.x * 0.5 + 0.5) * 1000, y = (1 - (v.y * 0.5 + 0.5)) * 1000;
          let best = -1, bestA = Infinity, near = 0, nearD = Infinity;
          boxes.forEach((b, i) => {
            if (y >= b.box[0] && y <= b.box[2] && x >= b.box[1] && x <= b.box[3] && b.area < bestA) { best = i; bestA = b.area; }
            const d = (b.cx - x) ** 2 + (b.cy - y) ** 2; if (d < nearD) { nearD = d; near = i; }
          });
          label[t] = best >= 0 ? best : near;
        }
        const groups = new Map();
        for (let t = 0; t < n; t++) { const k = label[t]; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(t); }
        const attrs = Object.entries(geo.attributes);
        for (const [k, tris] of groups) {
          const out = new T.BufferGeometry();
          for (const [name, attr] of attrs) {
            const sz = attr.itemSize, arr = new Float32Array(tris.length * 3 * sz);
            let q = 0;
            for (const t of tris) for (let c = 0; c < 3; c++) { const src = vi(t * 3 + c); for (let j = 0; j < sz; j++) arr[q++] = attr.getComponent(src, j); }
            out.setAttribute(name, new T.BufferAttribute(arr, sz, attr.normalized));
          }
          if (!out.attributes.normal) out.computeVertexNormals();
          const mesh = new T.Mesh(out, Array.isArray(m.material) ? m.material[0] : m.material);
          mesh.position.copy(m.userData.home?.pos || m.position); mesh.quaternion.copy(m.userData.home?.quat || m.quaternion); mesh.scale.copy(m.userData.home?.scale || m.scale);
          m.parent.add(mesh);
          const nm = boxes[k].name;
          (byName.get(nm) || byName.set(nm, { meshes: [], desc: boxes[k].desc }).get(nm)).meshes.push(mesh);
        }
        m.parent.remove(m); geo.dispose();
      }
    }
    // swap the object's parts for the dissected ones
    const s = this.state;
    for (const p of old) { s.hidden.delete(p.lname); s.opacity.delete(p.lname); s.colors.delete(p.lname); s.highlights.delete(p.lname); }
    if (old.some((p) => p.name === s.selection)) s.selection = null;
    this.parts = this.parts.filter((p) => p.obj !== objIndex);
    o.pivot.updateMatrixWorld(true);
    const saved = o.built;
    o.built = { ...saved, parts: [...byName.entries()].map(([name, x]) => ({ name, meshes: x.meshes, group: 'AI-dissected', desc: (x.desc ? x.desc + ' · ' : '') + 'cut from the real model by AURA (boundaries approximate)' })) };
    this.registerParts(o);
    o.built = { ...saved, parts: o.built.parts };
    o.dissected = true; o.split = { method: 'dissected', pieces: byName.size };
    this.labelObjs?.forEach((l) => { l.parent?.remove(l); l.element.remove(); }); this.labelObjs = [];
    this.refresh();
    return byName.size;
  }
  /** The host shell goes see-through so what's inside shows. */
  ghost(objIndex, v = 0.28) { this.parts.filter((p) => p.obj === objIndex).forEach((p) => this.state.opacity.set(p.lname, v)); this.refresh(); }

  // ---------------------------------------------------------------- the AI's view of the scene
  viewName() {
    const off = this.camera.position.clone().sub(this.controls.target).normalize();
    let best = 'iso', score = -2;
    for (const [k, v] of Object.entries(VIEWS)) { const d = off.dot(new this.T.Vector3(...v).normalize()); if (d > score) { score = d; best = k; } }
    return best === 'front' ? 'anterior / front' : best === 'back' ? 'posterior / back' : best;
  }
  context() {
    const cur = [...this.objects].reverse().find((o) => o.insideOf == null) || this.objects[this.objects.length - 1];
    const s = this.state;
    return {
      objects: this.objects.map((o) => ({ index: o.index, name: o.scene.display_name, kind: o.scene.kind, source: o.scene.source?.label, pieces: o.split?.method || 'model parts', inside_of: o.insideOf })),
      subject: cur?.scene.display_name || null, kind: cur?.scene.kind, source: cur?.scene.source ? `${cur.scene.source.label} - ${cur.scene.source.title || ''}` : null,
      level: cur?.scene.level_label, parts: this.parts.map((p) => p.name), selection: s.selection, hovered: s.hover,
      hidden: this.parts.filter((p) => s.hidden.has(p.lname)).map((p) => p.name), isolated: s.isolated, xray: s.xray, wireframe: s.wireframe,
      explode: Math.round(s.explode * 100) / 100, moved: this.parts.filter((p) => p.off.lengthSq() > 1e-6).map((p) => p.name),
      section: s.section, view: this.objects.length ? this.viewName() : null, hologram: s.holo, inspecting: s.inspecting,
      animations: this.animations().map((a) => `${a.name}${a.authored ? ' (from the model)' : ' (illustrative)'}`), playing: [...s.playing], spin: s.spin,
    };
  }

  // ---------------------------------------------------------------- frame loop
  tween(ms, fn) { this.tweens.push({ t0: performance.now(), ms, fn }); this.dirty = 2; }

  tick() {
    if (!this.running) return;
    const dt = Math.min(0.05, this.clock.getDelta()); this.t += dt;
    let active = false;
    const now = performance.now();
    if (this.tweens.length) {
      this.tweens = this.tweens.filter((tw) => { const k = Math.min(1, (now - tw.t0) / tw.ms); tw.fn(k); return k < 1; });
      active = true;
    }
    if (this.timeline.length) {                               // step-by-step (dis)assembly
      this.timeline = this.timeline.filter((ev) => { if (ev.t > now) return true; ev.part.ex = ev.ex; if (ev.home) ev.part.off.set(0, 0, 0); return false; });
      if (!this.timeline.length) this.h.onChange?.(this.context());
      active = true;
    }
    const s = this.state;
    let moving = s.playing.size > 0;
    const k = Math.min(1, dt * 7);
    for (const p of this.parts) {
      if (Math.abs(p.exShown - p.ex) > 0.001) { p.exShown += (p.ex - p.exShown) * k; if (Math.abs(p.exShown - p.ex) < 0.002) p.exShown = p.ex; moving = true; }
      if (!this.grab || this.grab.part !== p) {
        if (p.offShown.distanceToSquared(p.off) > 1e-8) { p.offShown.lerp(p.off, k); if (p.offShown.distanceToSquared(p.off) < 1e-7) p.offShown.copy(p.off); moving = true; }
      }
    }
    if (moving) { this.pose(); active = true; }
    for (const o of this.objects) if (o.mixer && s.playing.size) { o.mixer.update(dt); active = true; }
    if (s.selection && !s.holo) {
      const p = this.byName(s.selection);
      p?.meshes.forEach((m) => [].concat(m.material).forEach((mat) => { if (mat.emissive) mat.emissiveIntensity = 0.4 + 0.25 * Math.sin(this.t * 4); }));
      active = true;
    }
    if (s.holo) {
      for (const p of this.parts) for (const m of p.meshes) if (m.material.uniforms) m.material.uniforms.uTime.value = this.t;
      active = true;
    }
    if (this.controls.autoRotate) active = true;
    this.controls.update();
    this.h.onFrame?.(dt);
    if (active || this.dirty > 0) {
      if (this.labelObjs?.length || this.notes.length) this.placeLabels();
      this.renderer.render(this.scene, this.camera);
      this.css.render(this.scene, this.camera);
      if ((this.labelObjs?.length || 0) > 1) this.declutter();
      this.dirty = Math.max(0, this.dirty - 1);
    }
  }

  /** Every mesh's pose = home + its explode offset + where it was pulled to + illustrative motion. */
  pose() {
    const s = this.state, T = this.T, v = new T.Vector3(), lv = new T.Vector3();
    const anims = [];
    this.objects.forEach((o) => (o.built.anims || []).forEach((a) => { if (s.playing.has(`${o.index}:${a.name}`)) anims.push({ o, a, set: new Set(a.parts.map(norm)) }); }));
    for (const p of this.parts) {
      v.copy(p.dirP).multiplyScalar(p.exShown).add(p.offShown);
      for (const m of p.meshes) {
        const hm = m.userData.home;
        if (!hm) continue;
        m.position.copy(hm.pos).add(lv.copy(v).applyMatrix3(m.userData.toLocal));
        m.quaternion.copy(hm.quat); m.scale.copy(hm.scale);
        for (const { o, a, set } of anims) {
          if (o.index !== p.obj || (set.size && !set.has(p.lname) && ![...set].some((n) => p.lname.includes(n)))) continue;
          const ax = new T.Vector3(...a.axis).normalize(), w = this.t * a.speed;
          if (a.type === 'rotate') m.quaternion.multiply(new T.Quaternion().setFromAxisAngle(ax, w * Math.PI));
          else if (a.type === 'oscillate') m.position.addScaledVector(ax, Math.sin(w * Math.PI * 2) * (a.amount || 1) * 0.1);
          else if (a.type === 'pulse') m.scale.multiplyScalar(1 + Math.max(0, Math.sin(w * Math.PI * 2)) * Math.min(0.5, (a.amount || 8) / 100));
          else if (a.type === 'orbit') m.position.applyAxisAngle(ax, w * 0.5);
        }
      }
    }
    this.dirty = 2;
  }

  dispose() {
    this.renderer.setAnimationLoop(null);
    document.removeEventListener('visibilitychange', this.onVis);
    this.ro.disconnect();
    this.clear();
    this.controls.dispose();
    this.renderer.dispose();
    this.scene.environment?.dispose();
    this.renderer.domElement.remove(); this.css.domElement.remove();
  }
}
