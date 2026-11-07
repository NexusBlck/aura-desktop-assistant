// AURA Vision (phone) - a full-screen camera HUD.
//   Live:   on-device object detection + tracking, counts, recognising things you told AURA to remember.
//   Always: INTEL - the vision AI keeps observing in the background whenever the scene changes and pins
//           labels on everything it notices (beyond what the live detector knows).
//   Modes:  SCAN · DETECTIVE · ASK · SENTINEL · MED · READ · TRANSLATE · STUDY · SOLVE · FOOD · COMPARE ·
//           DOC · FIND · MEMORY (+ RECALL and the vision timeline)
//   Extras: visor filters, motion trails, rough distances, voice commands, precision detector.
//   Camera: front/back (button or double-tap), pinch / zoom chips, freeze, torch (when the phone allows),
//           photos from your library.
// Analyses run on the PC (vision_lab.py); detection and recognition run on the phone.
import { el, h, icon, toast, confirm } from '../../ui/components.js';
import { speechChunks } from '../../core/format.js';
import * as audio from '../../platform/audio.js';
import * as haptics from '../../platform/haptics.js';
import * as storage from '../../platform/storage.js';
import * as engine from '../../vision/engine.js';
import { renderFields } from '../../vision/renderers.js';

const MODES = [
  ['scan', 'SCAN', 'Tap anything to identify it · hold it to remember it'],
  ['detective', 'DETECTIVE', 'Clues → deductions → conclusion, pinned on the image'],
  ['ask', 'ASK', 'Ask anything about what you see - by voice or text'],
  ['sentinel', 'SENTINEL', 'Tell AURA what to watch for - it alerts you the moment it happens'],
  ['med', 'MED', 'Drugs · ECG · X-ray · lab reports · skin (educational)'],
  ['read', 'READ', 'Reads text - then add dates, tasks, numbers, links'],
  ['translate', 'TRANSLATE', 'Translates the text in view'],
  ['study', 'STUDY', 'Textbook, slide, ECG, X-ray → tutor · flashcards · quiz'],
  ['solve', 'SOLVE', 'Step-by-step solutions'],
  ['food', 'FOOD', 'Calories and macros for what you\'re eating'],
  ['compare', 'COMPARE', 'Shoot A, then B - AURA compares them'],
  ['doc', 'DOC', 'Scan pages → a searchable PDF on your PC'],
  ['find', 'FIND', 'Tell AURA what to look for - it alerts you when it sees it'],
  ['memory', 'MEMORY', 'Aim at something and teach AURA to recognise it'],
];
const SHUTTER = { scan: 'ID', detective: 'CASE', ask: 'ASK', sentinel: 'ARM', med: 'MED', read: 'READ', translate: 'TRANS', study: 'STUDY',
  solve: 'SOLVE', food: 'FOOD', compare: 'A', doc: '+PAGE', find: 'FIND', memory: 'LEARN' };
const MED_SUB = [['drug', 'DRUG'], ['ecg', 'ECG'], ['xray', 'X-RAY'], ['lab', 'LAB'], ['lesion', 'SKIN']];
const VISORS = [['', 'NORMAL'], ['night', 'NIGHT'], ['thermal', 'THERMAL'], ['xray', 'X-RAY'], ['tactical', 'TACTICAL']];
const LANGS = ['English', 'Urdu', 'Arabic', 'French', 'Spanish', 'Chinese', 'German'];
const STUDY_SUB = [['explain', 'TUTOR'], ['flashcards', 'FLASHCARDS'], ['quiz', 'QUIZ']];

export function open(ctx) {
  // ------------------------------------------------------------------ state
  const S = {
    facing: storage.get('vx.facing', 'environment'), stream: null, track: null, caps: {},
    zoom: 1, hwZoom: false, torch: false, frozen: false, grid: storage.get('vx.grid', false),
    mode: storage.get('vx.mode', 'scan'), studyMode: 'explain', lang: storage.get('vx.lang', 'English'),
    memory: [], find: null, tracks: [], centerMem: null, busy: false, alive: true,
    lastDet: 0, lastRec: 0, detBusy: false, recBusy: false, fps: 0, frames: 0, fpsAt: performance.now(),
    announced: {}, foundState: false, lastImage: null, lastAnswer: '', engineState: 'loading',
    medKind: 'drug', visor: storage.get('vx.visor', 0), precision: storage.get('vx.precision', true),
    intel: storage.get('vx.intel', true), intelObjs: [], intelRegion: null, intelSummary: '', intelBusy: false, lastIntel: 0, intelBackoff: 0,
    intelOpen: false, intelHaz: false,                                   // INTEL stays a small chip until you tap it
    sig: null, sigAt: 0, intelSig: null, changedAt: 0,
    sentinel: { armed: false, conditions: storage.get('vx.watch', []), lastAlert: {}, busy: false, last: 0, sig: null },
    compareA: null, docPages: [], pins: null,
  };
  const tracker = new engine.Tracker();

  // ------------------------------------------------------------------ DOM
  const video = h('video', { playsinline: true, muted: true, autoplay: true });
  const freeze = h('canvas', { class: 'vx-freeze' });
  const ov = h('canvas', { class: 'vx-ov' });
  const gridEl = h('div', { class: 'vx-grid', hidden: !S.grid });
  const reticle = h('div', { class: 'vx-reticle', html: `<svg viewBox="0 0 100 100">
    <g class="r1"><circle cx="50" cy="50" r="46" fill="none" stroke="#3ad6ff" stroke-width=".8" stroke-dasharray="4 7" opacity=".8"/></g>
    <g class="r2"><path d="M50 8 A42 42 0 0 1 92 50" fill="none" stroke="#9ef3ff" stroke-width="1.6"/><path d="M50 92 A42 42 0 0 1 8 50" fill="none" stroke="#3ad6ff" stroke-width="1.6" opacity=".7"/></g>
    <path d="M50 38v8M50 54v8M38 50h8M54 50h8" stroke="#9ef3ff" stroke-width="1.4"/><circle cx="50" cy="50" r="1.6" fill="#9ef3ff"/></svg>` });
  const tele = el('div', 'vx-tele');
  // INTEL: a small chip ("◇ INTEL · 6") - tap it to open the details + the violet labels on the view, tap again to close
  const ticker = h('div', { class: 'vx-ticker', hidden: true });
  const intelChip = h('button', { class: 'vx-intel-chip', hidden: true, 'aria-label': 'INTEL - tap to open' });
  const intelClose = h('button', { class: 'vx-intel-x', 'aria-label': 'Close INTEL' }, '×');
  const toggleIntel = (e) => { e.stopPropagation(); e.preventDefault(); haptics.tap(); S.intelOpen = !S.intelOpen; paintIntel(); };
  for (const n of [intelChip, intelClose, ticker]) {
    ['pointerdown', 'touchstart', 'mousedown'].forEach((ev) => n.addEventListener(ev, (e) => e.stopPropagation(), { passive: ev === 'touchstart' }));
  }
  intelChip.addEventListener('click', toggleIntel);
  intelClose.addEventListener('click', toggleIntel);
  function paintIntel() {
    const n = S.intelObjs.length;
    intelChip.hidden = !S.intel || (!n && !S.intelSummary && !S.intelBusy);
    intelChip.textContent = `◇ INTEL${S.intelBusy && !n ? '…' : ` · ${n}`}${S.intelHaz ? ' ⚠' : ''} ${S.intelOpen ? '▴' : '▾'}`;
    intelChip.classList.toggle('open', S.intelOpen);
    intelChip.classList.toggle('haz', S.intelHaz);
    ticker.hidden = !S.intel || !S.intelOpen || !S.intelSummary;
  }
  const banner = h('div', { hidden: true });
  const counts = h('div', { class: 'vx-counts' });
  const hint = el('div', 'vx-hint');
  const flash = el('div', 'vx-flash');
  const foundFx = h('div', { class: 'vx-found', hidden: true });
  const bootText = el('div'), bootBar = el('i');
  const boot = h('div', { class: 'vx-boot' }, h('div', {}, el('b', null, 'AURA VISION'), bootText, h('div', { class: 'bar' }, bootBar)));

  const btn = (ic, label, fn, cls = '') => { const b = h('button', { class: 'vx-btn ' + cls, 'aria-label': label, title: label }, icon(ic)); b.onclick = (e) => { e.stopPropagation(); haptics.tap(); fn(b); }; return b; };
  const closeBtn = btn('back', 'Close Vision', () => close());
  const flipBtn = btn('camera', 'Switch camera (or double-tap)', () => flip());
  const torchBtn = btn('power', 'Flashlight', () => setTorch(!S.torch));
  torchBtn.hidden = true;
  const freezeBtn = btn('stop', 'Freeze frame', () => toggleFreeze());
  const gridBtn = btn('more', 'Grid', () => { S.grid = !S.grid; gridEl.hidden = !S.grid; storage.set('vx.grid', S.grid); gridBtn.classList.toggle('on', S.grid); }, S.grid ? 'on' : '');
  const intelBtn = btn('core', 'INTEL - always-on AI observation', () => {
    S.intel = !S.intel; storage.set('vx.intel', S.intel); intelBtn.classList.toggle('on', S.intel);
    if (!S.intel) { S.intelObjs = []; S.intelOpen = false; }
    paintIntel();
    showBanner(S.intel ? 'INTEL ON - AURA KEEPS OBSERVING' : 'INTEL OFF', 'cyan', 1500);
  }, S.intel ? 'on' : '');
  const visorBtn = btn('eye', 'Visor filter', () => {
    S.visor = (S.visor + 1) % VISORS.length; storage.set('vx.visor', S.visor); applyVisor();
    showBanner(`VISOR: ${VISORS[S.visor][1]}`, 'cyan', 1000);
  });
  const voiceBtn = btn('mic', 'Voice command', () => voiceCommand());
  const zoomBox = h('div', { class: 'vx-zoom' });
  const sub = h('div', { class: 'vx-sub' });
  const modesBar = h('div', { class: 'vx-modes', role: 'tablist' });
  const shutter = h('button', { class: 'vx-shutter', 'aria-label': 'Analyse' }, el('span'));
  const leftBtn = h('button', { class: 'vx-thumb', 'aria-label': 'Photo library' }, icon('image'));
  const rightBtn = h('button', { class: 'vx-thumb', 'aria-label': 'What AURA remembers' }, icon('eye'));
  const fileIn = h('input', { type: 'file', accept: 'image/*', hidden: true });

  const root = h('div', { class: 'vx', role: 'dialog', 'aria-label': 'AURA Vision' },
    video, freeze, ov, gridEl, h('div', { class: 'vx-scan' }), h('div', { class: 'vx-vign' }),
    h('div', { class: 'vx-frame' }, el('i'), el('i'), el('i'), el('i')), reticle, foundFx, flash,
    h('div', { class: 'vx-top' }, closeBtn, tele, voiceBtn, flipBtn), intelChip, ticker, banner, counts,
    h('div', { class: 'vx-side' }, intelBtn, visorBtn, torchBtn, freezeBtn, gridBtn), zoomBox, hint,
    h('div', { class: 'vx-bottom' }, sub, modesBar, h('div', { class: 'vx-row' }, leftBtn, shutter, rightBtn)), boot, fileIn);
  document.body.append(root);
  let sheetEl = null;

  // ------------------------------------------------------------------ helpers
  const say = (text) => {
    if (!text || !ctx.store.get('settings').voice) return;
    audio.stop();
    audio.playSequence(speechChunks(text).map((p) => () => ctx.api.tts(p).catch(() => null)));
  };
  let bannerTimer = null;
  function showBanner(text, cls = '', ms = 3500) {
    banner.className = 'vx-banner ' + cls; banner.textContent = text; banner.hidden = false;
    clearTimeout(bannerTimer); if (ms) bannerTimer = setTimeout(() => { banner.hidden = true; }, ms);
  }
  function beep(freq = 880, ms = 120) {
    try {
      const ac = beep.ac || (beep.ac = new (window.AudioContext || window.webkitAudioContext)());
      const o = ac.createOscillator(), g = ac.createGain();
      o.frequency.value = freq; o.type = 'sine'; g.gain.value = 0.08;
      o.connect(g); g.connect(ac.destination); o.start(); o.stop(ac.currentTime + ms / 1000);
    } catch (e) { /* no audio */ }
  }
  const src = () => (S.frozen ? freeze : video);
  const srcW = () => (S.frozen ? freeze.width : video.videoWidth);
  const srcH = () => (S.frozen ? freeze.height : video.videoHeight);

  // screen <-> source mapping (object-fit: cover, digital zoom about the centre, mirrored front camera)
  function view() {
    const W = root.clientWidth, H = root.clientHeight, vw = srcW() || 1, vh = srcH() || 1;
    const z = S.hwZoom ? 1 : S.zoom;
    const s = Math.max(W / vw, H / vh) * z;
    return { W, H, s, ox: (W - vw * s) / 2, oy: (H - vh * s) / 2, mirror: S.frozen ? S.freezeMirror : S.facing === 'user' };
  }
  const toScreen = (v, x, y) => ({ x: v.mirror ? v.W - (v.ox + x * v.s) : v.ox + x * v.s, y: v.oy + y * v.s });
  function toSource(v, sx, sy) { if (v.mirror) sx = v.W - sx; return { x: (sx - v.ox) / v.s, y: (sy - v.oy) / v.s }; }
  function visibleRegion() {
    const v = view(), a = toSource(v, 0, 0), b = toSource(v, v.W, v.H);
    const x = Math.max(0, Math.min(a.x, b.x)), y = Math.max(0, Math.min(a.y, b.y));
    return [x, y, Math.min(srcW(), Math.max(a.x, b.x)) - x, Math.min(srcH(), Math.max(a.y, b.y)) - y];
  }
  function centerRegion(frac = 0.42) {
    const [x, y, w, hh] = visibleRegion(); const side = Math.min(w, hh) * frac;
    return [x + w / 2 - side / 2, y + hh * 0.46 - side / 2, side, side];
  }
  function applyVideoTransform() {
    // the preview is mirrored for the front camera (like a selfie); analysis always uses the real image
    const z = S.hwZoom ? 1 : S.zoom, m = S.facing === 'user' ? -1 : 1;
    video.style.transform = `scale(${m * z}, ${z})`;
    freeze.style.transform = `scale(${(S.freezeMirror ? -1 : 1) * z}, ${z})`;
    applyVisor();
  }

  function applyVisor() {
    // looks only - what AURA analyses is always the real image
    const f = { night: 'grayscale(1) sepia(1) hue-rotate(55deg) saturate(5) brightness(1.5) contrast(1.15)',
      thermal: 'invert(1) hue-rotate(180deg) saturate(7) contrast(1.35)', xray: 'grayscale(1) invert(1) contrast(1.7) brightness(1.1)',
      tactical: 'grayscale(1) contrast(1.7) sepia(1) hue-rotate(150deg) saturate(3.2) brightness(1.05)' }[VISORS[S.visor][0]] || '';
    video.style.filter = f; freeze.style.filter = f;
    visorBtn.classList.toggle('on', S.visor > 0);
  }

  /** The current view (or a region of it) as a JPEG: { b64, blob, url }. */
  function capture(region = null, maxSide = 1280) {
    const [x, y, w, hh] = region || visibleRegion();
    const sc = Math.min(1, maxSide / Math.max(w, hh));
    const c = document.createElement('canvas');
    c.width = Math.max(1, Math.round(w * sc)); c.height = Math.max(1, Math.round(hh * sc));
    const g = c.getContext('2d');
    g.drawImage(src(), x, y, w, hh, 0, 0, c.width, c.height);          // never mirrored - text stays readable
    const url = c.toDataURL('image/jpeg', 0.86);
    const b64 = url.split(',')[1];
    const bin = atob(b64), arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    return { b64, url, blob: new Blob([arr], { type: 'image/jpeg' }) };
  }
  function shutterFx() { flash.classList.remove('go'); void flash.offsetWidth; flash.classList.add('go'); haptics.tap(); }

  // ------------------------------------------------------------------ camera
  async function startCamera() {
    if (S.stream) S.stream.getTracks().forEach((t) => t.stop());
    try {
      S.stream = await navigator.mediaDevices.getUserMedia({ audio: false,
        video: { facingMode: { ideal: S.facing }, width: { ideal: 1920 }, height: { ideal: 1080 } } });
    } catch (e) {
      showBanner(e && e.name === 'NotAllowedError' ? 'CAMERA BLOCKED - ALLOW IT IN IPHONE SETTINGS' : 'CAMERA UNAVAILABLE - USE A PHOTO', '', 0);
      return;
    }
    video.srcObject = S.stream;
    S.track = S.stream.getVideoTracks()[0];
    try { await video.play(); } catch (e) { /* autoplay with muted+playsinline is allowed */ }
    S.caps = (S.track.getCapabilities && S.track.getCapabilities()) || {};
    torchBtn.hidden = !S.caps.torch; S.torch = false; torchBtn.classList.remove('on');
    S.hwZoom = !!(S.caps.zoom && S.caps.zoom.max > 1);
    S.zoom = 1;
    buildZoom(); applyVideoTransform();
    storage.set('vx.facing', S.facing);
  }
  function flip() {
    S.facing = S.facing === 'user' ? 'environment' : 'user';
    if (S.frozen) toggleFreeze(false);
    startCamera();
    showBanner(S.facing === 'user' ? 'FRONT CAMERA' : 'REAR CAMERA', 'cyan', 1200);
  }
  async function setTorch(on) {
    try { await S.track.applyConstraints({ advanced: [{ torch: on }] }); S.torch = on; torchBtn.classList.toggle('on', on); }
    catch (e) { showBanner('FLASHLIGHT NOT AVAILABLE', '', 1500); }
  }
  async function setZoom(z) {
    if (S.hwZoom) {
      z = Math.max(S.caps.zoom.min || 1, Math.min(S.caps.zoom.max, z));
      try { await S.track.applyConstraints({ advanced: [{ zoom: z }] }); } catch (e) { /* keep last */ }
    } else z = Math.max(1, Math.min(6, z));
    S.zoom = z; applyVideoTransform();
    zoomBox.querySelectorAll('button').forEach((b) => b.classList.toggle('on', Math.abs(+b.dataset.z - z) < 0.05));
  }
  function buildZoom() {
    zoomBox.innerHTML = '';
    const levels = S.hwZoom ? [1, 2, 3, 5].filter((z) => z <= S.caps.zoom.max) : [1, 2, 3, 4];
    for (const z of levels) {
      const b = h('button', { 'data-z': z, class: z === 1 ? 'on' : '' }, `${z}×`);
      b.onclick = (e) => { e.stopPropagation(); haptics.tap(); setZoom(z); };
      zoomBox.append(b);
    }
  }
  function toggleFreeze(force) {
    const on = force ?? !S.frozen;
    if (on && video.videoWidth) {
      freeze.width = video.videoWidth; freeze.height = video.videoHeight;
      freeze.getContext('2d').drawImage(video, 0, 0);
      S.freezeMirror = S.facing === 'user';
    }
    S.frozen = on;
    if (!on) S.pins = null;
    applyVideoTransform();
    root.classList.toggle('frozen', on);
    freezeBtn.classList.toggle('on', on);
    if (on) { showBanner('FRAME FROZEN', 'cyan', 1200); detectOnce(); }
  }
  async function detectOnce() {
    if (!engine.ready()) return;
    const dets = await engine.detect(freeze);
    S.tracks = dets.map((d, i) => ({ id: 1000 + i, ...d, mem: null }));
    recognise(true);
  }

  // gestures: double-tap flips, pinch zooms, tap identifies, hold remembers
  let lastTap = 0, pinch = null, holdTimer = null, moved = false;
  root.addEventListener('touchstart', (e) => {
    if (e.target.closest('button, .vx-sheet, input')) return;
    moved = false;
    if (e.touches.length === 2) {
      const d = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
      pinch = { d, z: S.zoom }; clearTimeout(holdTimer); return;
    }
    const t = e.touches[0];
    holdTimer = setTimeout(() => { if (!moved) { haptics.success(); rememberAt(t.clientX, t.clientY); } }, 650);
  }, { passive: true });
  root.addEventListener('touchmove', (e) => {
    moved = true; clearTimeout(holdTimer);
    if (pinch && e.touches.length === 2) {
      const d = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
      setZoom(pinch.z * (d / pinch.d));
    }
  }, { passive: true });
  root.addEventListener('touchend', (e) => {
    clearTimeout(holdTimer);
    if (pinch) { if (e.touches.length < 2) pinch = null; return; }
    if (moved || e.target.closest('button, .vx-sheet, input')) return;
    const t = e.changedTouches[0], now = Date.now();
    if (now - lastTap < 300) { lastTap = 0; clearTimeout(tapTimer); flip(); return; }
    lastTap = now;
    tapTimer = setTimeout(() => tapAt(t.clientX, t.clientY), 300);
  });
  let tapTimer = null;
  root.addEventListener('click', (e) => {     // desktop / mouse
    if (e.target.closest('button, .vx-sheet, input') || 'ontouchstart' in window) return;
    tapAt(e.clientX, e.clientY);
  });

  function trackAt(sx, sy) {
    const v = view();
    return S.tracks.filter((t) => {
      const a = toScreen(v, t.box[0], t.box[1]), b = toScreen(v, t.box[0] + t.box[2], t.box[1] + t.box[3]);
      return sx >= Math.min(a.x, b.x) && sx <= Math.max(a.x, b.x) && sy >= Math.min(a.y, b.y) && sy <= Math.max(a.y, b.y);
    }).sort((p, q) => p.box[2] * p.box[3] - q.box[2] * q.box[3])[0] || null;
  }
  function regionAt(sx, sy) {
    const v = view(), p = toSource(v, sx, sy), side = Math.min(srcW(), srcH()) * 0.3;
    return [Math.max(0, p.x - side / 2), Math.max(0, p.y - side / 2), side, side];
  }
  function tapAt(sx, sy) {
    if (sheetEl) return;
    const t = trackAt(sx, sy);
    if (S.mode === 'memory') { rememberAt(sx, sy); return; }
    identify(t ? t.box : regionAt(sx, sy), t ? t.label : '');
  }
  function rememberAt(sx, sy) {
    const t = trackAt(sx, sy);
    rememberFlow(t ? { trackId: t.id, box: t.box, label: t.label } : { box: regionAt(sx, sy) });
  }

  // ------------------------------------------------------------------ live loop
  function loop() {
    if (!S.alive) return;
    requestAnimationFrame(loop);
    const now = performance.now();
    if (!S.frozen && engine.ready() && video.videoWidth && !S.detBusy && now - S.lastDet > 100) {
      S.detBusy = true; S.lastDet = now;
      engine.detect(video).then((dets) => {
        S.tracks = tracker.update(dets).map((t) => t);
        S.frames++;
        if (now - S.fpsAt > 1000) { S.fps = Math.round((S.frames * 1000) / (now - S.fpsAt)); S.frames = 0; S.fpsAt = now; }
      }).catch(() => {}).finally(() => { S.detBusy = false; });
    }
    if (!S.frozen && now - S.lastRec > 850) { S.lastRec = now; recognise(); }
    if (!S.frozen && video.videoWidth && now - S.sigAt > 400) {
      S.sigAt = now;
      const sig = engine.sceneSignature(video);
      if (engine.sceneDiff(sig, S.sig) > 0.06) S.changedAt = now;
      S.sig = sig;
      scheduleIntel(now); scheduleSentinel(now);
    }
    draw();
  }

  // INTEL: whenever the view changes and then settles, the vision AI takes a careful look (rate-limited)
  function scheduleIntel(now) {
    if (!S.intel || S.intelBusy || S.busy || sheetEl || now < S.intelBackoff) return;
    const settled = now - S.changedAt > 700;
    const changedSinceLast = engine.sceneDiff(S.sig, S.intelSig) > 0.08;
    if (!settled || (!changedSinceLast && now - S.lastIntel < 45000) || now - S.lastIntel < 9000) return;
    S.intelBusy = true; S.lastIntel = now; S.intelSig = S.sig; paintIntel();
    const region = visibleRegion(), img = capture(region, 1024), thumb = capture(region, 200).b64;
    ctx.api.lab('observe', { image: img.b64, log: true, thumb, focus: S.find ? S.find.name : '' }).then((r) => {
      if (r.error) { S.intelBackoff = performance.now() + 30000; return; }
      S.intelRegion = region;
      S.intelObjs = (r.objects || []).filter((o) => o.label).slice(0, 10);
      S.intelSummary = r.summary || '';
      const extra = [...(r.hazards || []).map((x) => '⚠ ' + x), ...(r.notable || []).slice(0, 3).map((x) => '◆ ' + x), ...(r.text || []).slice(0, 2).map((x) => '“' + x + '”')];
      S.intelHaz = !!(r.hazards || []).length;
      ticker.innerHTML = '';
      ticker.append(intelClose, h('b', {}, 'INTEL ▸ '), S.intelSummary, ...extra.map((x) => h('span', { class: x.startsWith('⚠') ? 'haz' : '' }, x)),
        ...(S.intelObjs.length ? [h('span', { class: 'objs' }, S.intelObjs.map((o) => o.label).join(' · '))] : []));
      paintIntel();
      if ((r.hazards || []).length) { showBanner('⚠ ' + r.hazards[0].toUpperCase().slice(0, 70), '', 5000); beep(440, 200); say('Careful: ' + r.hazards[0]); }
      if (S.find && S.find.type === 'class' && !S.foundState && S.intelObjs.some((o) => o.label.toLowerCase().includes(S.find.name.toLowerCase()))) {
        showBanner(`INTEL: ${S.find.name.toUpperCase()} MAY BE IN VIEW`, '', 4000); beep(880, 100);
      }
    }).catch(() => { S.intelBackoff = performance.now() + 20000; }).finally(() => { S.intelBusy = false; paintIntel(); });
  }

  // SENTINEL: watch for what you asked, on every real change (and at least every 20 s)
  function scheduleSentinel(now) {
    const W = S.sentinel;
    if (!W.armed || W.busy || !W.conditions.length) return;
    const changed = engine.sceneDiff(S.sig, W.sig) > 0.05;
    if ((!changed && now - W.last < 20000) || now - W.last < 6000) return;
    W.busy = true; W.last = now; W.sig = S.sig;
    const img = capture(visibleRegion(), 900), thumb = capture(visibleRegion(), 200).b64;
    ctx.api.lab('watch', { image: img.b64, conditions: W.conditions, thumb }).then((r) => {
      for (const t of r.triggered || []) {
        if (Date.now() - (W.lastAlert[t.condition] || 0) < 60000) continue;
        W.lastAlert[t.condition] = Date.now();
        root.classList.add('alert'); setTimeout(() => root.classList.remove('alert'), 4000);
        showBanner(`🚨 SENTINEL: ${String(t.condition).toUpperCase()}`, 'red', 8000);
        beep(988, 150); setTimeout(() => beep(740, 150), 180); setTimeout(() => beep(988, 220), 360); haptics.warning();
        say(`Alert. ${t.condition}. ${t.evidence || ''}`);
      }
      if (r.description) hint.textContent = `SENTINEL ▸ ${r.description}`;
    }).catch(() => {}).finally(() => { W.busy = false; });
  }

  function recognise(force = false) {
    if (S.recBusy || !engine.canRecognise() || !S.memory.length || (!video.videoWidth && !S.frozen)) return;
    S.recBusy = true;
    try {
      const cands = S.tracks.filter((t) => t.label !== 'person').sort((a, b) => b.box[2] * b.box[3] - a.box[2] * a.box[3]).slice(0, 4);
      for (const t of cands) {
        const m = engine.match(engine.embed(src(), t.box), S.memory);
        t.mem = m ? { id: m.item.id, name: m.item.name, score: m.score } : (t.mem && t.mem.score > 0.86 ? t.mem : null);
        if (m) announce(m.item, m.score);
      }
      const cm = engine.match(engine.embed(src(), centerRegion()), S.memory, 0.82);
      S.centerMem = cm ? { id: cm.item.id, name: cm.item.name, score: cm.score } : null;
      if (cm) announce(cm.item, cm.score);
    } catch (e) { console.warn('recognise', e); }
    S.recBusy = false;
    checkFind();
  }

  function announce(item, score) {
    const now = Date.now();
    if (S.find && S.find.type === 'mem' && S.find.id === item.id) return;     // FIND handles its own alert
    if (now - (S.announced[item.id] || 0) < 25000) return;
    S.announced[item.id] = now;
    showBanner(`★ ${item.name.toUpperCase()} · ${Math.round(score * 100)}%`);
    haptics.success(); beep(1046, 90);
    say(`That's your ${item.name}.`);
    ctx.api.lab('memory/seen', { id: item.id, scene: capture(visibleRegion(), 360).b64 }).catch(() => {});
  }

  function checkFind() {
    if (!S.find) { if (S.foundState) { S.foundState = false; root.classList.remove('found'); } return; }
    let hit = null;
    if (S.find.type === 'class') hit = S.tracks.find((t) => t.label === S.find.label);
    else hit = S.tracks.find((t) => t.mem && t.mem.id === S.find.id) || (S.centerMem && S.centerMem.id === S.find.id ? { box: centerRegion() } : null);
    if (hit && !S.foundState) {
      S.foundState = true; root.classList.add('found');
      foundFx.hidden = false; foundFx.style.animation = 'none'; void foundFx.offsetWidth; foundFx.style.animation = '';
      const v = view(), c = toScreen(v, hit.box[0] + hit.box[2] / 2, hit.box[1] + hit.box[3] / 2);
      const dir = c.x < v.W * 0.38 ? 'TO YOUR LEFT' : c.x > v.W * 0.62 ? 'TO YOUR RIGHT' : 'STRAIGHT AHEAD';
      showBanner(`TARGET ACQUIRED: ${S.find.name.toUpperCase()} · ${dir}`, '', 5000);
      beep(880, 110); setTimeout(() => beep(1320, 140), 150); haptics.success();
      say(`Found ${S.find.type === 'mem' ? 'your ' : 'a '}${S.find.name}, ${dir.toLowerCase()}.`);
      if (S.find.type === 'mem') ctx.api.lab('memory/seen', { id: S.find.id, scene: capture(visibleRegion(), 360).b64 }).catch(() => {});
    } else if (!hit && S.foundState) { S.foundState = false; root.classList.remove('found'); }
  }

  function draw() {
    const v = view(), dpr = window.devicePixelRatio || 1;
    if (ov.width !== Math.round(v.W * dpr) || ov.height !== Math.round(v.H * dpr)) { ov.width = Math.round(v.W * dpr); ov.height = Math.round(v.H * dpr); }
    const g = ov.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, v.W, v.H);
    const showBoxes = ['scan', 'find', 'memory', 'sentinel', 'detective'].includes(S.mode) || S.frozen;
    const tally = {};
    for (const t of S.tracks) {
      const name = t.mem ? `★ ${t.mem.name}` : t.label;
      tally[name] = (tally[name] || 0) + 1;
      if (!showBoxes) continue;
      const a = toScreen(v, t.box[0], t.box[1]), b = toScreen(v, t.box[0] + t.box[2], t.box[1] + t.box[3]);
      const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y), w = Math.abs(b.x - a.x), hh = Math.abs(b.y - a.y);
      const target = S.find && ((S.find.type === 'class' && S.find.label === t.label) || (S.find.type === 'mem' && t.mem && t.mem.id === S.find.id));
      const col = t.mem || target ? '#ffb547' : '#3ad6ff';
      const L = Math.min(18, w / 3, hh / 3);
      g.strokeStyle = col; g.lineWidth = target ? 3 : 2; g.shadowColor = col; g.shadowBlur = target ? 16 : 8;
      g.beginPath();
      g.moveTo(x, y + L); g.lineTo(x, y); g.lineTo(x + L, y);
      g.moveTo(x + w - L, y); g.lineTo(x + w, y); g.lineTo(x + w, y + L);
      g.moveTo(x + w, y + hh - L); g.lineTo(x + w, y + hh); g.lineTo(x + w - L, y + hh);
      g.moveTo(x + L, y + hh); g.lineTo(x, y + hh); g.lineTo(x, y + hh - L);
      g.stroke();
      g.shadowBlur = 0; g.lineWidth = 1; g.globalAlpha = 0.25; g.strokeRect(x, y, w, hh); g.globalAlpha = 1;
      if (t.trail && t.trail.length > 3) {               // motion trail
        g.beginPath();
        t.trail.forEach(([cx, cy], i) => { const p = toScreen(v, cx, cy); i ? g.lineTo(p.x, p.y) : g.moveTo(p.x, p.y); });
        g.strokeStyle = col; g.globalAlpha = 0.45; g.lineWidth = 2; g.setLineDash([2, 4]); g.stroke(); g.setLineDash([]); g.globalAlpha = 1;
      }
      const dist = engine.distance(t.label, t.box, srcW(), srcH());
      const label = (t.mem ? `★ ${t.mem.name.toUpperCase()} ${Math.round(t.mem.score * 100)}%` : `${t.label.toUpperCase()} ${Math.round(t.score * 100)}%`)
        + (dist ? ` · ≈${dist < 10 ? dist.toFixed(1) : Math.round(dist)}M` : '');
      g.font = '600 11px "Share Tech Mono", monospace';
      const tw = g.measureText(label).width + 12;
      const ly = y > 22 ? y - 20 : y + 4;
      g.fillStyle = t.mem || target ? 'rgba(40,22,0,.85)' : 'rgba(0,14,22,.8)'; g.fillRect(x, ly, tw, 17);
      g.fillStyle = col; g.fillRect(x, ly, 2, 17);
      g.fillText(label, x + 7, ly + 12.5);
    }
    // INTEL tags (what the vision AI noticed) - violet, dashed
    if (S.intel && S.intelOpen && S.intelObjs.length && S.intelRegion && !sheetEl) {        // only while INTEL is opened
      const [rx, ry, rw, rh] = S.intelRegion;
      g.font = '600 10px "Share Tech Mono", monospace';
      for (const o of S.intelObjs) {
        const a = toScreen(v, rx + o.x * rw, ry + o.y * rh), b = toScreen(v, rx + (o.x + o.w) * rw, ry + (o.y + o.h) * rh);
        const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y), w = Math.abs(b.x - a.x), hh = Math.abs(b.y - a.y);
        if (w < 6 || hh < 6) continue;
        g.strokeStyle = 'rgba(167,139,250,.85)'; g.lineWidth = 1.2; g.setLineDash([5, 4]); g.strokeRect(x, y, w, hh); g.setLineDash([]);
        const txt = `◇ ${o.label}`.toUpperCase().slice(0, 34), tw = g.measureText(txt).width + 10, ty = Math.min(v.H - 16, y + hh + 2);
        g.fillStyle = 'rgba(24,14,48,.82)'; g.fillRect(x, ty, tw, 15); g.fillStyle = '#c4b5fd'; g.fillText(txt, x + 5, ty + 11);
        if (o.detail && w > 90) { const d = String(o.detail).slice(0, 40); g.fillStyle = 'rgba(196,181,253,.8)'; g.fillText(d, x + 5, ty + 25); }
      }
    }
    // DETECTIVE evidence pins
    if (S.pins && S.frozen && S.pins.region) {
      const [rx, ry, rw, rh] = S.pins.region;
      for (const c of S.pins.clues) {
        const p = toScreen(v, rx + (+c.x || 0.5) * rw, ry + (+c.y || 0.5) * rh);
        g.beginPath(); g.arc(p.x, p.y, 13, 0, Math.PI * 2); g.fillStyle = 'rgba(255,181,71,.9)'; g.shadowColor = '#ffb547'; g.shadowBlur = 14; g.fill(); g.shadowBlur = 0;
        g.fillStyle = '#1a0f00'; g.font = '900 12px "Orbitron", sans-serif'; g.textAlign = 'center'; g.fillText(String(c.n), p.x, p.y + 4.5); g.textAlign = 'left';
      }
    }
    if (S.centerMem && showBoxes) {
      const r = centerRegion(), a = toScreen(v, r[0], r[1]);
      g.font = '700 12px "Orbitron", sans-serif'; g.fillStyle = '#ffb547'; g.shadowColor = '#ffb547'; g.shadowBlur = 10;
      g.fillText(`★ ${S.centerMem.name.toUpperCase()}`, Math.min(a.x, v.W - 160), Math.max(90, a.y - 8)); g.shadowBlur = 0;
    }
    // telemetry + counts (cheap, but not every frame)
    if (!draw.at || performance.now() - draw.at > 250) {
      draw.at = performance.now();
      const mode = MODES.find((m) => m[0] === S.mode)[1];
      tele.innerHTML = '';
      tele.append(el('b', null, `VISION ▸ ${mode}${S.sentinel.armed ? ' · 🛡 ARMED' : ''}`), el('br'),
        `CAM ${S.facing === 'user' ? 'FRONT' : 'REAR'} · ${S.zoom.toFixed(1)}× · OBJ ${S.tracks.length} · ${S.engineState === 'ready' ? S.fps + ' FPS' : S.engineState.toUpperCase()}`
        + `${engine.isPrecise() ? ' · HD' : ''} · MEM ${S.memory.length}${S.intel ? ' · INTEL' + (S.intelBusy ? '…' : '') : ''}`);
      counts.innerHTML = '';
      if (S.mode === 'scan' || S.mode === 'find') {
        Object.entries(tally).sort((a, b) => b[1] - a[1]).slice(0, 8).forEach(([k, n]) => counts.append(h('span', { class: k.startsWith('★') ? 'mem' : '' }, `${n}× ${k.toUpperCase()}`)));
        if (S.find) counts.prepend(h('span', { class: 'mem' }, `◎ SEARCHING: ${S.find.name.toUpperCase()}`));
      }
    }
  }

  // ------------------------------------------------------------------ modes
  function setMode(m) {
    S.mode = m; storage.set('vx.mode', m);
    modesBar.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.m === m));
    shutter.firstChild.textContent = SHUTTER[m];
    hint.textContent = MODES.find((x) => x[0] === m)[2];
    rightBtn.setAttribute('aria-label', 'What AURA remembers');
    sub.innerHTML = '';
    if (m === 'study') {
      for (const [k, l] of STUDY_SUB) {
        const b = h('button', { class: S.studyMode === k ? 'on' : '' }, l);
        b.onclick = (e) => { e.stopPropagation(); S.studyMode = k; sub.querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b)); };
        sub.append(b);
      }
    } else if (m === 'translate') {
      for (const l of LANGS) {
        const b = h('button', { class: S.lang === l ? 'on' : '' }, l.slice(0, 3).toUpperCase());
        b.onclick = (e) => { e.stopPropagation(); S.lang = l; storage.set('vx.lang', l); sub.querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b)); };
        sub.append(b);
      }
    } else if (m === 'med') {
      for (const [k, l] of MED_SUB) {
        const b = h('button', { class: S.medKind === k ? 'on' : '' }, l);
        b.onclick = (e) => { e.stopPropagation(); S.medKind = k; sub.querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b)); };
        sub.append(b);
      }
    } else if (m === 'sentinel') {
      for (const c of S.sentinel.conditions) {
        const b = h('button', { class: 'on' }, `◉ ${c.slice(0, 26)} ✕`);
        b.onclick = (e) => { e.stopPropagation(); S.sentinel.conditions = S.sentinel.conditions.filter((x) => x !== c); storage.set('vx.watch', S.sentinel.conditions); setMode('sentinel'); };
        sub.append(b);
      }
      const add = h('button', {}, '+ WATCH FOR…');
      add.onclick = (e) => { e.stopPropagation(); sentinelSheet(); };
      sub.append(add);
      shutter.firstChild.textContent = S.sentinel.armed ? 'DISARM' : 'ARM';
    } else if (m === 'compare') {
      shutter.firstChild.textContent = S.compareA ? 'B' : 'A';
      if (S.compareA) { const b = h('button', { class: 'on' }, 'A CAPTURED ✕'); b.onclick = (e) => { e.stopPropagation(); S.compareA = null; setMode('compare'); }; sub.append(b); }
    } else if (m === 'doc') {
      const n = h('button', { class: S.docPages.length ? 'on' : '' }, `${S.docPages.length} PAGE${S.docPages.length === 1 ? '' : 'S'}`);
      sub.append(n);
      if (S.docPages.length) {
        const fin = h('button', { class: 'on' }, '✓ MAKE PDF'); fin.onclick = (e) => { e.stopPropagation(); finishDoc(); }; sub.append(fin);
        const clr = h('button', {}, 'CLEAR'); clr.onclick = (e) => { e.stopPropagation(); S.docPages = []; setMode('doc'); }; sub.append(clr);
      }
    } else if (m === 'find' && S.find) {
      const b = h('button', { class: 'on' }, `◎ ${S.find.name.toUpperCase()} ✕`);
      b.onclick = (e) => { e.stopPropagation(); S.find = null; setMode('find'); };
      sub.append(b);
    }
    const target = modesBar.querySelector(`[data-m="${m}"]`);
    if (target) target.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' });
  }
  for (const [m, label] of MODES) {
    const b = h('button', { 'data-m': m, role: 'tab' }, label);
    b.onclick = (e) => { e.stopPropagation(); haptics.tap(); setMode(m); };
    modesBar.append(b);
  }

  shutter.onclick = (e) => {
    e.stopPropagation();
    if (S.busy || sheetEl) return;
    ({
      scan: () => { const t = trackAt(root.clientWidth / 2, root.clientHeight * 0.46); identify(t ? t.box : centerRegion(0.6), t ? t.label : ''); },
      ask: () => askSheet(),
      detective: () => detectiveCase(),
      sentinel: () => {
        if (!S.sentinel.conditions.length) { sentinelSheet(); return; }
        S.sentinel.armed = !S.sentinel.armed; S.sentinel.lastAlert = {}; S.sentinel.last = 0;
        showBanner(S.sentinel.armed ? `🛡 SENTINEL ARMED · WATCHING FOR ${S.sentinel.conditions.length} THING${S.sentinel.conditions.length > 1 ? 'S' : ''}` : 'SENTINEL OFF', S.sentinel.armed ? 'cyan' : '', 3000);
        say(S.sentinel.armed ? `Sentinel armed. I'll alert you if ${S.sentinel.conditions.join(', or if ')}.` : 'Sentinel off.');
        setMode('sentinel');
      },
      med: () => analyse('med', () => ctx.api.lab('med', { image: shot().b64, kind: S.medKind }), (r) => {
        if (r.not_found) { openResult('NOTHING TO READ', [h('p', {}, r.not_found)], { speak: r.not_found }); return; }
        const title = { drug: r.name, ecg: r.impression, xray: r.study, lab: 'Lab report', lesion: 'Skin / wound' }[S.medKind] || 'Clinical lens';
        openResult(title || 'Clinical lens', renderFields(r, ['disclaimer', 'name', 'study']), { speak: r.impression || r.pattern || r.description || r.how_it_works || '',
          title: `MED ${S.medKind}: ${title || ''}`, text: JSON.stringify(r, null, 2) });
      }),
      compare: () => {
        if (!S.compareA) { S.compareA = shot(); showBanner('A CAPTURED · NOW AIM AT B', 'cyan', 2500); setMode('compare'); return; }
        const a = S.compareA, b = shot(); S.compareA = null; setMode('compare');
        analyse('compare', () => ctx.api.lab('compare', { a: a.b64, b: b.b64 }), (r) => {
          openResult(r.verdict ? 'VERDICT' : 'Comparison', [
            h('div', { class: 'vx-pair' }, h('div', {}, h('img', { src: a.url, alt: 'A' }), el('span', null, 'A · ' + (r.a || ''))), h('div', {}, h('img', { src: b.url, alt: 'B' }), el('span', null, 'B · ' + (r.b || '')))),
            h('p', { style: 'font-size:1.05rem' }, r.verdict || ''), r.pick ? el('div', 'lbl', `PICK: ${String(r.pick).toUpperCase()}`) : null,
            ...renderFields({ differences: r.differences, similarities: r.similarities })],
          { speak: r.verdict || '', title: 'Comparison', text: `A: ${r.a}\nB: ${r.b}\nVerdict: ${r.verdict}\nDifferences: ${(r.differences || []).join('; ')}` });
        });
      },
      doc: () => {
        if (S.docPages.length >= 30) { showBanner('30 PAGES MAX - MAKE THE PDF', '', 2500); return; }
        S.docPages.push(shot().b64); showBanner(`PAGE ${S.docPages.length} CAPTURED`, 'cyan', 1200); setMode('doc');
      },
      read: () => analyse('read', () => ctx.api.lab('read', { image: shot().b64, mode: 'read' }), showRead),
      translate: () => analyse('translate', () => ctx.api.lab('read', { image: shot().b64, mode: 'translate', lang: S.lang }), showTranslate),
      study: () => analyse('study', () => ctx.api.lab('study', { image: shot().b64, mode: S.studyMode }), showStudy),
      solve: () => analyse('solve', () => ctx.api.lab('study', { image: shot().b64, mode: 'solve' }), showSolve),
      food: () => analyse('food', () => ctx.api.lab('study', { image: shot().b64, mode: 'food' }), showFood),
      find: () => findSheet(),
      memory: () => rememberFlow({ box: centerRegion() }),
    })[S.mode]();
  };
  leftBtn.onclick = (e) => { e.stopPropagation(); fileIn.click(); };
  rightBtn.onclick = (e) => { e.stopPropagation(); memorySheet(); };
  fileIn.onchange = async () => {
    const f = fileIn.files && fileIn.files[0]; fileIn.value = '';
    if (!f) return;
    const img = new Image(); img.src = URL.createObjectURL(f);
    try { await img.decode(); } catch (e) { toast('Vision', "That photo couldn't be opened.", 'error'); return; }
    freeze.width = img.naturalWidth; freeze.height = img.naturalHeight;
    freeze.getContext('2d').drawImage(img, 0, 0);
    S.freezeMirror = false;
    S.frozen = true; root.classList.add('frozen'); freezeBtn.classList.add('on'); applyVideoTransform();
    leftBtn.style.backgroundImage = `url(${img.src})`; leftBtn.innerHTML = '';
    showBanner('PHOTO LOADED - PICK A MODE, TAP THE BUTTON', 'cyan', 3000);
    detectOnce();
  };

  // the frame used for an analysis: the frozen frame, or a fresh capture with a flash
  function shot() { shutterFx(); const c = capture(); S.lastImage = c; return c; }

  async function analyse(kind, call, render) {
    S.busy = true; root.classList.add('busy');
    showBanner('ANALYSING…', 'cyan', 0);
    try {
      const r = await call();
      banner.hidden = true;
      if (r.error) { showBanner(r.error.toUpperCase().slice(0, 80), '', 4000); return; }
      render(r);
      haptics.success();
    } catch (e) {
      showBanner(e.message.toUpperCase().slice(0, 80), '', 4000);
    } finally { S.busy = false; root.classList.remove('busy'); }
  }

  async function identify(box, hintLabel) {
    if (S.busy) return;
    shutterFx();
    S.lastImage = capture();
    const crop = capture(box, 768);
    await analyse('identify', () => ctx.api.vision('identify', crop.blob, hintLabel), (info) => {
      const rows = (info.details || []).map((d) => h('div', { class: 'kv' }, el('span', null, String(d.label).toUpperCase()), el('b', null, d.value)));
      openResult(String(info.name || 'Object'), [
        info.category ? el('div', 'lbl', String(info.category).toUpperCase() + (info.confidence ? ' · ' + String(info.confidence).toUpperCase() + ' CONFIDENCE' : '')) : null,
        info.summary ? h('p', {}, info.summary) : null, ...rows,
        info.fact ? h('p', { style: 'color:var(--violet);margin-top:10px' }, info.fact) : null,
      ], { speak: [info.summary, info.fact].filter(Boolean).join(' '), title: info.name, text: [info.summary, ...(info.details || []).map((d) => `${d.label}: ${d.value}`), info.fact].filter(Boolean).join('\n'),
        extra: [['REMEMBER THIS', () => { closeSheet(); rememberFlow({ box, presetName: info.name }); }]] });
    });
  }

  // ------------------------------------------------------------------ result sheets
  function closeSheet() { if (sheetEl) { sheetEl.remove(); sheetEl = null; } }
  function openResult(title, nodes, { speak = '', title: saveTitle = title, text = '', extra = [] } = {}) {
    closeSheet();
    const body = h('div', { class: 'vx-sb' }, h('h3', {}, title), ...nodes.filter(Boolean));
    const acts = h('div', { class: 'vx-acts' });
    const add = (label, fn) => { const b = h('button', {}, label); b.onclick = async (e) => { e.stopPropagation(); haptics.tap(); await fn(b); }; acts.append(b); };
    if (speak) add('▶ SPEAK', () => say(speak));
    if (text) add('COPY', async () => { try { await navigator.clipboard.writeText(text); showBanner('COPIED', 'cyan', 1200); } catch (e) { showBanner('COPY NOT ALLOWED', '', 1500); } });
    if (text) add('SAVE TO NOTES', async () => { await ctx.api.appendNote(saveTitle, text); showBanner('SAVED TO NOTES', 'cyan', 1500); });
    if (S.lastImage) add('SAVE TO PC', async () => { await ctx.api.lab('save', { image: S.lastImage.b64, title: saveTitle, text }); showBanner('SAVED TO DOCUMENTS › AURA VISION', 'cyan', 2000); });
    add('ASK MORE', () => { S.lastAnswer = `${title}: ${text}`.slice(0, 1500); closeSheet(); askSheet(true); });
    for (const [l, fn] of extra) add(l, fn);
    add('✕ CLOSE', () => closeSheet());
    sheetEl = h('div', { class: 'vx-sheet' }, h('div', { class: 'grab' }), body, acts);
    let y0 = null;
    sheetEl.addEventListener('touchstart', (e) => { if (e.target.closest('.grab, h3')) y0 = e.touches[0].clientY; }, { passive: true });
    sheetEl.addEventListener('touchend', (e) => { if (y0 != null && e.changedTouches[0].clientY - y0 > 80) closeSheet(); y0 = null; });
    root.append(sheetEl);
    if (speak) say(speak);
  }

  function chips(list, onPick) {
    const box = h('div');
    list.forEach((x) => { const b = h('button', { class: 'vx-chipbtn' }, x.label || x); b.onclick = (e) => { e.stopPropagation(); onPick(x); }; box.append(b); });
    return box;
  }

  function showRead(r) {
    const items = [];
    (r.dates || []).forEach((d) => items.push({ label: `📅 ${d.event || 'Event'} - ${d.text}`, go: async () => {
      const res = await ctx.api.addCalendar(`${d.event || 'Event'} ${d.text}`); showBanner((res.message || res.error || 'DONE').toUpperCase().slice(0, 80), res.error ? '' : 'cyan', 3500); } }));
    (r.todos || []).forEach((t) => items.push({ label: `✓ ${t}`, go: async () => { await ctx.api.addTask(t); showBanner('ADDED TO YOUR TASKS', 'cyan', 1500); } }));
    (r.phones || []).forEach((p) => items.push({ label: `☎ ${p}`, go: () => { location.href = 'tel:' + p.replace(/[^\d+]/g, ''); } }));
    (r.emails || []).forEach((m) => items.push({ label: `✉ ${m}`, go: () => { location.href = 'mailto:' + m; } }));
    (r.links || []).forEach((l) => items.push({ label: `↗ ${l}`, go: () => window.open(/^https?:/.test(l) ? l : 'https://' + l, '_blank', 'noopener') }));
    openResult(r.summary || 'Text', [
      items.length ? el('div', 'lbl', 'FOUND IN IT - TAP TO ACT') : null, items.length ? chips(items, (x) => x.go()) : null,
      el('div', 'lbl', 'TEXT'), h('pre', {}, r.text || '(no text found)'),
    ], { speak: r.summary || '', title: r.summary || 'Scanned text', text: r.text || '' });
  }
  function showTranslate(r) {
    openResult(`${r.language || 'Text'} → ${S.lang}`, [el('div', 'lbl', S.lang.toUpperCase()), h('p', { style: 'font-size:1.08rem' }, r.translation || r.text || ''),
      el('div', 'lbl', 'ORIGINAL'), h('pre', {}, r.text || '')], { speak: r.translation || '', title: 'Translation', text: `${r.translation || ''}\n\n${r.text || ''}` });
  }
  function showStudy(r) {
    if (S.studyMode === 'flashcards' && r.cards) {
      const cards = r.cards.map((c) => { const card = h('div', { class: 'vx-card' }, h('div', { class: 'in' }, h('div', { class: 'face' }, c.q), h('div', { class: 'face back' }, c.a)));
        card.onclick = () => { card.classList.toggle('flip'); haptics.tap(); }; return card; });
      openResult(r.title || 'Flashcards', [el('div', 'lbl', `${r.cards.length} CARDS · TAP TO FLIP`), ...cards],
        { title: `Flashcards: ${r.title || ''}`, text: r.cards.map((c) => `Q: ${c.q}\nA: ${c.a}`).join('\n\n') });
      return;
    }
    if (S.studyMode === 'quiz' && r.questions) {
      let score = 0, answered = 0;
      const scoreEl = el('div', 'lbl', `SCORE 0 / ${r.questions.length}`);
      const qs = r.questions.map((q, qi) => {
        const box = h('div', { class: 'vx-q' }, h('p', {}, h('b', {}, `${qi + 1}. `), q.q));
        (q.options || []).forEach((o, oi) => {
          const b = h('button', { class: 'opt' }, `${'ABCD'[oi] || oi + 1}. ${o}`);
          b.onclick = () => {
            if (box.classList.contains('done')) return;
            box.classList.add('done'); answered++;
            const right = oi === +q.answer;
            if (right) { score++; haptics.success(); } else haptics.warning();
            b.classList.add(right ? 'right' : 'wrong');
            box.querySelectorAll('.opt')[+q.answer]?.classList.add('right');
            scoreEl.textContent = `SCORE ${score} / ${r.questions.length}` + (answered === r.questions.length ? (score === answered ? ' · PERFECT' : '') : '');
          };
          box.append(b);
        });
        box.append(el('div', 'why', q.why || ''));
        return box;
      });
      openResult(r.title || 'Quiz', [scoreEl, ...qs], { title: `Quiz: ${r.title || ''}`,
        text: r.questions.map((q, i) => `${i + 1}. ${q.q}\n${(q.options || []).map((o, j) => `  ${'ABCD'[j]}. ${o}`).join('\n')}\nAnswer: ${'ABCD'[+q.answer]} - ${q.why || ''}`).join('\n\n') });
      return;
    }
    const kp = (r.key_points || []).map((k) => h('div', { class: 'kv' }, el('span', null, '◆'), el('b', { style: 'text-align:left;flex:1;color:var(--text)' }, k)));
    openResult(r.title || 'Explanation', [h('p', {}, r.explanation || r.text || ''),
      kp.length ? el('div', 'lbl', 'REMEMBER') : null, ...kp,
      r.clinical ? el('div', 'lbl', 'CLINICAL') : null, r.clinical ? h('p', {}, r.clinical) : null,
      r.mnemonic ? el('div', 'lbl', 'MNEMONIC') : null, r.mnemonic ? h('p', { style: 'color:var(--amber)' }, r.mnemonic) : null],
    { speak: r.explanation || r.text || '', title: r.title || 'Study', text: [r.explanation || r.text, ...(r.key_points || []).map((k) => '- ' + k), r.clinical, r.mnemonic].filter(Boolean).join('\n') });
  }
  function showSolve(r) {
    openResult(r.problem || 'Solution', [...(r.steps || []).map((s, i) => h('p', {}, h('b', { style: 'color:var(--cyan)' }, `${i + 1}. `), s)),
      el('div', 'lbl', 'ANSWER'), h('p', { style: 'font-size:1.15rem;color:var(--amber)' }, r.answer || r.text || '')],
    { speak: `The answer is ${r.answer || ''}.`, title: r.problem || 'Solution', text: [...(r.steps || []), 'Answer: ' + (r.answer || '')].join('\n') });
  }
  function showFood(r) {
    const m = (label, v, unit) => h('div', {}, label, h('b', {}, v == null ? '--' : `${Math.round(v)}${unit}`));
    openResult(`${r.total_kcal != null ? Math.round(r.total_kcal) + ' KCAL' : 'Nutrition'}`, [
      h('div', { class: 'vx-macro' }, m('PROTEIN', r.protein_g, 'g'), m('CARBS', r.carbs_g, 'g'), m('FAT', r.fat_g, 'g'), m('SUGAR', r.sugar_g, 'g')),
      ...(r.items || []).map((i) => h('div', { class: 'kv' }, el('span', null, `${i.name} · ${i.portion || ''}`), el('b', null, `${Math.round(i.kcal || 0)} kcal`))),
      r.verdict ? h('p', { style: 'margin-top:10px' }, r.verdict) : null, r.tip ? h('p', { style: 'color:var(--green)' }, r.tip) : null],
    { speak: `About ${Math.round(r.total_kcal || 0)} calories. ${r.verdict || ''}`, title: 'Food log',
      text: `${(r.items || []).map((i) => `${i.name} (${i.portion || ''}) ${Math.round(i.kcal || 0)} kcal`).join(', ')} - total ${Math.round(r.total_kcal || 0)} kcal, P ${Math.round(r.protein_g || 0)}g C ${Math.round(r.carbs_g || 0)}g F ${Math.round(r.fat_g || 0)}g` });
  }

  // ------------------------------------------------------------------ DETECTIVE
  async function detectiveCase(question = '') {
    if (!S.frozen) toggleFreeze(true);            // pins need a still frame
    const region = visibleRegion();
    S.lastImage = capture(region);
    shutterFx();
    await analyse('detective', () => ctx.api.lab('detective', { image: S.lastImage.b64, question }), (r) => {
      S.pins = { region, clues: r.clues || [] };
      const conf = { high: 'var(--green)', medium: 'var(--amber)', low: 'var(--red)' };
      openResult('CASE FILE', [
        el('div', 'lbl', 'CLUES (PINNED ON THE IMAGE)'),
        ...(r.clues || []).map((c) => h('p', {}, h('b', { style: 'color:var(--amber)' }, `${c.n}. `), c.clue)),
        el('div', 'lbl', 'DEDUCTIONS'),
        ...(r.deductions || []).map((d) => h('div', { class: 'vx-ded' }, h('p', {}, h('b', {}, d.claim)), h('p', { style: 'color:var(--text-dim);font-size:.9rem' }, 'because ' + (d.because || '')),
          h('span', { style: `color:${conf[d.confidence] || 'var(--text-dim)'};font-family:var(--f-head);font-size:.56rem;letter-spacing:2px` }, `${String(d.confidence || '').toUpperCase()} CONFIDENCE`))),
        el('div', 'lbl', 'CONCLUSION'), h('p', { style: 'font-size:1.05rem;color:var(--cyan-bright)' }, r.conclusion || ''),
        (r.check_next || []).length ? el('div', 'lbl', 'CHECK NEXT') : null, ...(r.check_next || []).map((x) => h('p', {}, '→ ' + x)),
      ], { speak: r.conclusion || '', title: 'Case file', text: [...(r.clues || []).map((c) => `${c.n}. ${c.clue}`), ...(r.deductions || []).map((d) => `- ${d.claim} (because ${d.because}; ${d.confidence})`), 'Conclusion: ' + (r.conclusion || '')].join('\n') });
    });
  }

  // ------------------------------------------------------------------ SENTINEL setup
  function sentinelSheet() {
    closeSheet();
    const input = h('input', { placeholder: 'e.g. someone opens the door', enterkeyhint: 'done', 'aria-label': 'Watch for' });
    const addIt = (c) => {
      c = (c || input.value).trim(); if (!c) return;
      if (!S.sentinel.conditions.includes(c)) S.sentinel.conditions.push(c);
      storage.set('vx.watch', S.sentinel.conditions); input.value = '';
      closeSheet(); S.sentinel.armed = true; S.sentinel.last = 0; setMode('sentinel');
      showBanner(`🛡 ARMED · WATCHING: ${c.toUpperCase().slice(0, 50)}`, 'cyan', 3000);
      say(`Watching. I'll alert you if ${c}.`);
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') addIt(); });
    const ideas = ['someone enters the room', 'the door opens', 'the baby wakes up', 'a person is near my laptop', 'the pot boils over', 'my phone screen lights up', 'the cat jumps on the table', 'it starts raining'];
    sheetEl = h('div', { class: 'vx-sheet' }, h('div', { class: 'grab' }), h('div', { class: 'vx-sb' }, h('h3', {}, 'SENTINEL · WHAT SHOULD I WATCH FOR?'),
      h('p', { style: 'font-size:.9rem;color:var(--text-dim)' }, 'Prop the phone up facing what matters. AURA checks every time the scene changes and alerts you (sound + voice + a notification on your phone).'),
      h('div', { class: 'vx-input' }, input, h('button', { class: 'vx-btn on', 'aria-label': 'Watch', onclick: (e) => { e.stopPropagation(); addIt(); } }, icon('check'))),
      el('div', 'lbl', 'IDEAS'), chips(ideas, (c) => addIt(c))),
    h('div', { class: 'vx-acts' }, h('button', { onclick: (e) => { e.stopPropagation(); closeSheet(); } }, '✕ CLOSE')));
    root.append(sheetEl);
    setTimeout(() => input.focus(), 200);
  }

  // ------------------------------------------------------------------ DOC → PDF
  function finishDoc() {
    closeSheet();
    const input = h('input', { placeholder: 'Title - e.g. Pathology notes week 6', value: '', 'aria-label': 'Title' });
    const go = h('button', {}, `✓ MAKE PDF (${S.docPages.length})`);
    go.onclick = async (e) => {
      e.stopPropagation();
      const title = input.value.trim() || 'Scanned document', pages = S.docPages.slice();
      closeSheet();
      await analyse('doc', () => ctx.api.lab('doc', { pages, title }), (r) => {
        S.docPages = []; setMode('doc');
        openResult(`PDF READY · ${r.pages} PAGE${r.pages === 1 ? '' : 'S'}`, [h('p', {}, r.name), el('div', 'lbl', 'SAVED IN DOCUMENTS › AURA UPLOADS (SEARCHABLE)'), h('pre', {}, (r.text || '').slice(0, 3000))],
          { title, text: r.text || '', extra: [['OPEN IN FILES', () => { closeSheet(); ctx.openTool('files', { kind: 'uploads', path: '', title: 'ADDED FROM PHONE' }); }]] });
      });
    };
    sheetEl = h('div', { class: 'vx-sheet' }, h('div', { class: 'grab' }), h('div', { class: 'vx-sb' }, h('h3', {}, 'NAME THE DOCUMENT'), h('div', { class: 'vx-input' }, input),
      h('p', { style: 'font-size:.85rem;color:var(--text-dim)' }, 'AURA reads every page too, so later you can say "bring up everything I have on …" and it finds this.')),
    h('div', { class: 'vx-acts' }, go, h('button', { onclick: (e) => { e.stopPropagation(); closeSheet(); } }, '✕ CANCEL')));
    root.append(sheetEl);
    setTimeout(() => input.focus(), 200);
  }

  // ------------------------------------------------------------------ voice commands inside Vision
  let vrec = null;
  async function voiceCommand() {
    if (vrec) { vrec.stop(); return; }
    try { vrec = await audio.record({ maxSeconds: 10 }); } catch (e) { showBanner('MICROPHONE BLOCKED', '', 2000); return; }
    voiceBtn.classList.add('on'); showBanner('LISTENING…', 'cyan', 0);
    const blob = await vrec.done; vrec = null; voiceBtn.classList.remove('on');
    if (!blob) { showBanner("DIDN'T CATCH THAT", '', 1500); return; }
    let t = '';
    try { t = (await ctx.api.transcribe(blob)).text || ''; } catch (e) { showBanner(e.message.toUpperCase(), '', 2500); return; }
    banner.hidden = true;
    if (!t) { showBanner("DIDN'T CATCH THAT", '', 1500); return; }
    runVoice(t);
  }
  function runVoice(t) {
    const q = t.trim(), l = q.toLowerCase();
    let m;
    showBanner(`“${q.slice(0, 60)}”`, 'cyan', 2500);
    if ((m = l.match(/\b(?:find|look for|search for|where'?s)\s+(?:my |the |a )?(.+?)[.?!]*$/))) {
      const name = m[1]; const mem = S.memory.find((x) => name.includes(x.name.toLowerCase()) || x.name.toLowerCase().includes(name));
      if (mem) { S.find = { type: 'mem', id: mem.id, name: mem.name }; setMode('find'); say(`Looking for your ${mem.name}.`); return; }
      const cls = engine.CLASSES.find((c) => name.includes(c) || c.includes(name));
      if (cls) { S.find = { type: 'class', label: cls, name: cls }; setMode('find'); say(`Looking for ${cls}.`); return; }
    }
    if ((m = l.match(/\bwhere (?:did i (?:leave|put)|is|are) (?:my )?(.+?)[.?!]*$/))) { recall(q); return; }
    if ((m = l.match(/\bremember (?:this|it|that)(?: as)? (.+?)[.!]*$/))) { rememberFlow({ box: centerRegion(), presetName: m[1] }); return; }
    if ((m = l.match(/\b(?:alert|tell|warn|notify) me (?:when|if) (.+?)[.!]*$/))) {
      if (!S.sentinel.conditions.includes(m[1])) S.sentinel.conditions.push(m[1]);
      storage.set('vx.watch', S.sentinel.conditions); S.sentinel.armed = true; S.sentinel.last = 0; setMode('sentinel'); say(`Watching. I'll alert you if ${m[1]}.`); return;
    }
    if ((m = l.match(/\btranslate(?: (?:this|it))?(?: (?:to|into) (\w+))?/))) { if (m[1]) { const L = LANGS.find((x) => x.toLowerCase() === m[1]); if (L) S.lang = L; } setMode('translate'); shutter.click(); return; }
    if (/\b(read (this|it)|what does (this|it) say)\b/.test(l)) { setMode('read'); shutter.click(); return; }
    if (/\b(detective|investigate|what happened here|analy[sz]e the scene)\b/.test(l)) { setMode('detective'); detectiveCase(); return; }
    if (/\b(calories|nutrition|how healthy)\b/.test(l)) { setMode('food'); shutter.click(); return; }
    if (/\b(solve|answer this question)\b/.test(l)) { setMode('solve'); shutter.click(); return; }
    if (/\b(flip|switch camera|selfie|front camera|back camera|rear camera)\b/.test(l)) { flip(); return; }
    if (/\b(night vision|thermal|x-?ray vision|tactical)\b/.test(l)) { S.visor = l.includes('night') ? 1 : l.includes('thermal') ? 2 : l.includes('ray') ? 3 : 4; applyVisor(); return; }
    // anything else: a question about what's in front of the camera
    S.lastImage = shot();
    analyse('ask', () => ctx.api.lab('ask', { image: S.lastImage.b64, question: q }), (r) => {
      S.lastAnswer = r.answer || r.text || '';
      openResult(q.length > 40 ? q.slice(0, 40) + '…' : q, [h('p', {}, S.lastAnswer)], { speak: S.lastAnswer, title: q, text: `Q: ${q}\nA: ${S.lastAnswer}` });
    });
  }

  // ------------------------------------------------------------------ RECALL + timeline
  async function recall(question) {
    await analyse('recall', () => ctx.api.lab('recall', { question }), (r) => {
      const it = r.item;
      openResult('RECALL', [h('p', { style: 'font-size:1.05rem' }, r.answer || ''),
        it && it.scene ? el('div', 'lbl', `${it.name.toUpperCase()} · LAST SEEN ${it.last_seen ? new Date(it.last_seen).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }) : ''}`) : null,
        it && it.scene ? h('img', { src: `data:image/jpeg;base64,${it.scene}`, alt: 'Where it was last seen', style: 'width:100%;border:1px solid var(--amber)' }) : null],
      { speak: r.answer || '', title: 'Recall', text: r.answer || '' });
    });
  }
  async function timelineSheet() {
    closeSheet();
    const list = h('div', {}, el('div', 'lbl', 'LOADING…'));
    sheetEl = h('div', { class: 'vx-sheet' }, h('div', { class: 'grab' }), h('div', { class: 'vx-sb' }, h('h3', {}, 'VISION TIMELINE'), list),
      h('div', { class: 'vx-acts' }, h('button', { onclick: (e) => { e.stopPropagation(); closeSheet(); } }, '✕ CLOSE')));
    root.append(sheetEl);
    try {
      const { items } = await ctx.api.lab('timeline', { limit: 80 });
      list.innerHTML = '';
      if (!items.length) list.append(h('p', { style: 'color:var(--text-dim)' }, 'Nothing yet - with INTEL on, AURA keeps a log of what it saw.'));
      for (const e of items) list.append(h('div', { class: 'vx-mem' }, e.thumb ? h('img', { src: `data:image/jpeg;base64,${e.thumb}`, alt: '' }) : h('div'),
        h('div', {}, h('b', { style: e.kind === 'alert' ? 'color:var(--red)' : '' }, e.summary), el('div', 'meta', new Date(e.t).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }))), h('span')));
    } catch (err) { list.innerHTML = ''; list.append(h('p', {}, err.message)); }
  }

  // ------------------------------------------------------------------ ASK
  function askSheet(followUp = false) {
    closeSheet();
    const input = h('input', { placeholder: followUp ? 'Ask a follow-up…' : 'What do you want to know?', enterkeyhint: 'send', 'aria-label': 'Question' });
    const mic = h('button', { class: 'vx-btn', 'aria-label': 'Ask by voice' }, icon('mic'));
    const go = async (q) => {
      q = (q || input.value).trim(); if (!q) return;
      closeSheet();
      const img = followUp && S.lastImage ? S.lastImage : shot();
      const question = followUp && S.lastAnswer ? `${q}\n(Earlier you told me: ${S.lastAnswer})` : q;
      await analyse('ask', () => ctx.api.lab('ask', { image: img.b64, question }), (r) => {
        S.lastAnswer = r.answer || r.text || '';
        openResult(q.length > 40 ? q.slice(0, 40) + '…' : q, [h('p', {}, r.answer || r.text || ''),
          (r.follow_ups || []).length ? el('div', 'lbl', 'ASK NEXT') : null,
          (r.follow_ups || []).length ? chips(r.follow_ups, (f) => { closeSheet(); goFollow(f); }) : null],
        { speak: r.answer || r.text || '', title: q, text: `Q: ${q}\nA: ${r.answer || r.text || ''}` });
      });
    };
    const goFollow = (f) => { askSheet(true); setTimeout(() => sheetEl && sheetEl.querySelector('input') && (sheetEl.querySelector('input').value = f, sheetEl.querySelector('.vx-input button:last-child').click()), 50); };
    const send = h('button', { class: 'vx-btn on', 'aria-label': 'Ask' }, icon('send'));
    send.onclick = (e) => { e.stopPropagation(); go(); };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    let rec = null;
    mic.onclick = async (e) => {
      e.stopPropagation();
      if (rec) { rec.stop(); return; }
      try { rec = await audio.record({ maxSeconds: 12 }); } catch (err) { showBanner('MICROPHONE BLOCKED', '', 2000); return; }
      mic.classList.add('on'); input.placeholder = 'Listening…';
      const blob = await rec.done; rec = null; mic.classList.remove('on');
      if (!blob) { input.placeholder = 'Didn\'t catch that - type it'; return; }
      input.placeholder = 'Transcribing…';
      try { const t = (await ctx.api.transcribe(blob)).text; if (t) { input.value = t; go(t); } else input.placeholder = 'Didn\'t catch that - type it'; }
      catch (err) { input.placeholder = err.message; }
    };
    const quick = followUp ? [] : ['What is this?', 'Is this safe?', 'How do I use this?', "What's wrong here?", 'Explain this like a doctor', 'How much does this cost?'];
    sheetEl = h('div', { class: 'vx-sheet' }, h('div', { class: 'grab' }), h('div', { class: 'vx-sb' },
      h('h3', {}, followUp ? 'FOLLOW-UP' : 'ASK AURA ABOUT THIS'), quick.length ? chips(quick, (q) => go(q)) : null,
      h('div', { class: 'vx-input' }, input, mic, send)), h('div', { class: 'vx-acts' }, h('button', { onclick: (e) => { e.stopPropagation(); closeSheet(); } }, '✕ CLOSE')));
    root.append(sheetEl);
    setTimeout(() => input.focus(), 200);
  }

  // ------------------------------------------------------------------ FIND
  function findSheet() {
    closeSheet();
    const input = h('input', { placeholder: 'e.g. keys, bottle, laptop, cup…', enterkeyhint: 'search', 'aria-label': 'What to find' });
    const setFind = (f) => {
      S.find = f; S.foundState = false; closeSheet(); setMode('find');
      showBanner(`SEARCHING FOR ${f.name.toUpperCase()} - SWEEP THE ROOM`, 'cyan', 3500);
      say(`Looking for ${f.type === 'mem' ? 'your ' : ''}${f.name}. Sweep the room slowly.`);
      if (f.type === 'mem' && !engine.canRecognise()) showBanner('RECOGNITION CORE OFFLINE - CAN ONLY FIND COMMON OBJECTS', '', 4000);
    };
    const go = () => {
      const q = input.value.trim().toLowerCase(); if (!q) return;
      const mem = S.memory.find((m) => m.name.toLowerCase().includes(q) || q.includes(m.name.toLowerCase()));
      if (mem) return setFind({ type: 'mem', id: mem.id, name: mem.name });
      const alias = { phone: 'cell phone', mobile: 'cell phone', tv: 'tv', television: 'tv', glass: 'wine glass', bag: 'handbag', plant: 'potted plant', sofa: 'couch', table: 'dining table', ball: 'sports ball', mug: 'cup' };
      const cls = engine.CLASSES.find((c) => c === (alias[q] || q)) || engine.CLASSES.find((c) => c.includes(q) || q.includes(c));
      if (cls) return setFind({ type: 'class', label: cls, name: cls });
      showBanner(`I CAN'T SPOT "${q.toUpperCase()}" YET - TEACH ME IN MEMORY MODE`, '', 4000);
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    const memChips = S.memory.map((m) => ({ label: `★ ${m.name}`, f: { type: 'mem', id: m.id, name: m.name } }));
    const common = ['cell phone', 'bottle', 'cup', 'laptop', 'book', 'backpack', 'remote', 'scissors', 'keyboard', 'mouse', 'chair', 'person']
      .map((c) => ({ label: c, f: { type: 'class', label: c, name: c } }));
    sheetEl = h('div', { class: 'vx-sheet' }, h('div', { class: 'grab' }), h('div', { class: 'vx-sb' }, h('h3', {}, 'WHAT SHOULD I FIND?'),
      h('div', { class: 'vx-input' }, input, h('button', { class: 'vx-btn on', onclick: (e) => { e.stopPropagation(); go(); }, 'aria-label': 'Find' }, icon('eye'))),
      memChips.length ? el('div', 'lbl', 'YOUR THINGS') : null, memChips.length ? chips(memChips, (x) => setFind(x.f)) : null,
      el('div', 'lbl', 'EVERYDAY OBJECTS'), chips(common, (x) => setFind(x.f)),
      h('p', { style: 'font-size:.85rem;color:var(--text-dim);margin-top:10px' }, 'Tip: teach AURA your own things (keys, ID card, stethoscope…) in MEMORY mode - then it can find exactly yours.')),
    h('div', { class: 'vx-acts' }, h('button', { onclick: (e) => { e.stopPropagation(); closeSheet(); } }, '✕ CLOSE')));
    root.append(sheetEl);
    setTimeout(() => input.focus(), 200);
  }

  // ------------------------------------------------------------------ MEMORY
  async function loadMemory() {
    try { S.memory = (await ctx.api.memory()).items || []; } catch (e) { /* PC offline - keep what we have */ }
  }

  function rememberFlow({ trackId = null, box, label = '', presetName = '', addTo = null }) {
    closeSheet();
    if (!engine.canRecognise()) { showBanner(S.engineState === 'loading' ? 'RECOGNITION CORE STILL LOADING…' : 'RECOGNITION CORE OFFLINE', '', 3000); return; }
    const preview = capture(box, 320);
    const name = h('input', { placeholder: 'Name it - e.g. My car keys', value: addTo ? addTo.name : presetName, maxlength: '60', 'aria-label': 'Name' });
    const note = h('input', { placeholder: 'Note (optional) - e.g. the blue keychain', maxlength: '300', 'aria-label': 'Note' });
    const status = el('div', 'lbl', 'AURA WILL STUDY IT FROM A FEW ANGLES');
    const learn = h('button', {}, addTo ? 'TEACH MORE ANGLES' : '◆ LEARN IT');
    learn.onclick = async (e) => {
      e.stopPropagation();
      if (!addTo && !name.value.trim()) { name.focus(); return; }
      learn.disabled = true;
      const embeds = [];
      for (let i = 0; i < 8; i++) {
        let b = box;
        if (trackId) { const t = S.tracks.find((x) => x.id === trackId); if (t) b = t.box; }
        const v = engine.embed(src(), b);
        if (v) embeds.push(v);
        status.textContent = `LEARNING ${embeds.length}/8 - MOVE AROUND IT SLIGHTLY`;
        haptics.tap();
        await new Promise((r) => setTimeout(r, 260));
      }
      if (embeds.length < 3) { status.textContent = "COULDN'T SEE IT CLEARLY - TRY AGAIN"; learn.disabled = false; return; }
      try {
        if (addTo) await ctx.api.lab('memory/views', { id: addTo.id, embeddings: embeds });
        else await ctx.api.lab('memory/add', { name: name.value.trim(), note: note.value.trim(), thumb: preview.b64, embeddings: embeds, model: engine.EMBED_MODEL });
        await loadMemory();
        closeSheet(); haptics.success(); beep(660, 80); setTimeout(() => beep(990, 120), 100);
        const n = addTo ? addTo.name : name.value.trim();
        showBanner(`★ MEMORISED: ${n.toUpperCase()}`, '', 3000);
        say(addTo ? `Got more angles of your ${n}.` : `Got it. I'll recognise your ${n} from now on.`);
        S.announced = {};
      } catch (err) { status.textContent = err.message; learn.disabled = false; }
    };
    sheetEl = h('div', { class: 'vx-sheet' }, h('div', { class: 'grab' }), h('div', { class: 'vx-sb' },
      h('h3', {}, addTo ? `MORE ANGLES: ${addTo.name.toUpperCase()}` : 'REMEMBER THIS'),
      h('img', { src: preview.url, alt: '', style: 'width:96px;height:96px;object-fit:cover;border:1px solid var(--amber);display:block;margin-bottom:10px' }),
      addTo ? null : h('div', { class: 'vx-input' }, name), addTo ? null : h('div', { class: 'vx-input' }, note), status),
    h('div', { class: 'vx-acts' }, learn, h('button', { onclick: (e) => { e.stopPropagation(); closeSheet(); } }, '✕ CANCEL')));
    root.append(sheetEl);
    if (!addTo) setTimeout(() => name.focus(), 200);
  }

  function memorySheet() {
    closeSheet();
    const list = h('div');
    const render = () => {
      list.innerHTML = '';
      if (!S.memory.length) { list.append(h('p', { style: 'color:var(--text-dim)' }, 'Nothing yet. Aim at something you own (keys, wallet, ID card, stethoscope…), switch to MEMORY and tap LEARN - or hold your finger on it in SCAN.')); return; }
      for (const m of S.memory) {
        const acts = h('div', { style: 'display:flex;flex-direction:column;gap:4px' });
        const mk = (l, fn) => { const b = h('button', { class: 'vx-chipbtn', style: 'margin:0;font-family:var(--f-head);font-size:.56rem;letter-spacing:1.4px;min-height:32px' }, l); b.onclick = (e) => { e.stopPropagation(); fn(); }; acts.append(b); };
        mk('FIND', () => { S.find = { type: 'mem', id: m.id, name: m.name }; closeSheet(); setMode('find'); showBanner(`SEARCHING FOR ${m.name.toUpperCase()}`, 'cyan', 3000); });
        mk('+ ANGLES', () => rememberFlow({ box: centerRegion(), addTo: m }));
        if (m.last_scene) mk('LAST SEEN', () => { closeSheet(); openResult(`${m.name} · last seen`, [h('img', { src: `data:image/jpeg;base64,${m.last_scene}`, alt: '', style: 'width:100%;border:1px solid var(--amber)' }),
          el('div', 'lbl', m.last_seen ? new Date(m.last_seen).toLocaleString([], { weekday: 'long', hour: 'numeric', minute: '2-digit' }) : '')], {}); });
        mk('DELETE', async () => { if (await confirm('FORGET IT?', `AURA will stop recognising "${m.name}".`, 'FORGET', true)) { await ctx.api.lab('memory/delete', { id: m.id }); await loadMemory(); render(); } });
        list.append(h('div', { class: 'vx-mem' }, h('img', { src: m.thumb ? `data:image/jpeg;base64,${m.thumb}` : '', alt: '' }),
          h('div', {}, h('b', {}, m.name), el('div', 'meta', m.note || ''), el('div', 'meta', `${(m.embeddings || []).length} views · seen ${m.seen || 0}×${m.last_seen ? ' · last ' + new Date(m.last_seen).toLocaleString([], { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' }) : ''}`)), acts));
      }
    };
    render();
    const rq = h('input', { placeholder: 'Ask: where did I leave my keys?', enterkeyhint: 'search', 'aria-label': 'Recall' });
    rq.addEventListener('keydown', (e) => { if (e.key === 'Enter' && rq.value.trim()) { const q = rq.value.trim(); closeSheet(); recall(q); } });
    sheetEl = h('div', { class: 'vx-sheet' }, h('div', { class: 'grab' }), h('div', { class: 'vx-sb' }, h('h3', {}, `AURA REMEMBERS · ${S.memory.length}`),
      el('div', 'lbl', 'RECALL'), h('div', { class: 'vx-input' }, rq), el('div', 'lbl', 'YOUR THINGS'), list),
      h('div', { class: 'vx-acts' }, h('button', { onclick: (e) => { e.stopPropagation(); closeSheet(); setMode('memory'); } }, '+ TEACH SOMETHING NEW'),
        h('button', { onclick: (e) => { e.stopPropagation(); timelineSheet(); } }, 'TIMELINE'),
        h('button', { onclick: (e) => { e.stopPropagation(); closeSheet(); } }, '✕ CLOSE')));
    root.append(sheetEl);
  }

  // ------------------------------------------------------------------ lifecycle
  function close() {
    S.alive = false;
    if (S.stream) S.stream.getTracks().forEach((t) => t.stop());
    audio.stop();
    root.remove();
    document.removeEventListener('visibilitychange', onVis);
  }
  function onVis() {
    if (document.visibilityState === 'hidden') { if (S.stream) S.stream.getTracks().forEach((t) => t.stop()); }
    else if (S.alive && !S.frozen) startCamera();
  }
  document.addEventListener('visibilitychange', onVis);

  setMode(S.mode);
  startCamera();
  loadMemory();
  requestAnimationFrame(loop);
  engine.load((stage, f) => { bootText.textContent = stage; bootBar.style.width = `${Math.round(f * 100)}%`; })
    .then((r) => {
      S.engineState = 'ready';
      boot.classList.add('gone'); setTimeout(() => boot.remove(), 600);
      showBanner(r.embedder ? 'VISION CORE ONLINE · RECOGNITION ARMED' : 'VISION CORE ONLINE · RECOGNITION OFFLINE', 'cyan', 2200);
      if (S.precision) engine.upgrade((ok) => { if (ok && S.alive) showBanner('PRECISION DETECTOR ONLINE · HD', 'cyan', 2000); });
    })
    .catch((e) => {
      S.engineState = 'offline';
      boot.classList.add('gone'); setTimeout(() => boot.remove(), 600);
      showBanner('LIVE DETECTION OFFLINE - ANALYSIS MODES STILL WORK', '', 4000);
      console.warn(e);
    });
}
