// Hand control - the Tony Stark workshop. Your hands (front camera / webcam, MediaPipe, on-device) are drawn as glowing
// holograms over the whole view, and they work on the model directly:
//   ✋/☝ HOVER          -> the piece under your index finger lights up; hold still ~0.9 s to select it
//   🤏 PINCH on a piece -> grab it and pull it anywhere (bring your hand closer/further to push/pull it in depth)
//   🤏 + FLICK          -> throw the piece away (it's set aside; "bring it back" / the HIDDEN chip returns it)
//   🤏 PINCH on empty   -> rotate the whole thing
//   ✊ FIST + move       -> move the view
//   🤏🤏 TWO PINCHES     -> zoom (spread) · twist · pan
//   ✋✋ TWO OPEN PALMS  -> spread apart = explode, bring together = reassemble
// Robustness: one-euro smoothing, confidence threshold, pinch hysteresis, gestures must hold a few frames to count,
// cooldowns on one-shot actions, everything releases when a hand leaves the frame.
const TASKS = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14';
const MODEL = 'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task';

const trackers = {};
/** The MediaPipe hand tracker. delegate 'GPU' falls back to 'CPU' if the GPU path can't start. Resolves { lmk, delegate }. */
function landmarker(delegate = 'GPU') {
  if (trackers[delegate]) return trackers[delegate];
  trackers[delegate] = (async () => {
    const url = `${TASKS}/vision_bundle.mjs`;
    const vision = await import(url);
    const files = await vision.FilesetResolver.forVisionTasks(`${TASKS}/wasm`);
    const opts = (d) => ({ baseOptions: { modelAssetPath: MODEL, delegate: d }, runningMode: 'VIDEO', numHands: 2,
      minHandDetectionConfidence: 0.5, minHandPresenceConfidence: 0.5, minTrackingConfidence: 0.5 });
    if (delegate === 'GPU') {
      try { return { lmk: await vision.HandLandmarker.createFromOptions(files, opts('GPU')), delegate: 'GPU' }; } catch (e) { /* fall through to CPU */ }
    }
    return { lmk: await vision.HandLandmarker.createFromOptions(files, opts('CPU')), delegate: 'CPU' };
  })();
  trackers[delegate].catch(() => { delete trackers[delegate]; });
  return trackers[delegate];
}

/** Hand-control problems are sent to the PC's log (logs/phone_errors.log) so they can be fixed. */
export function report(msg) {
  try { fetch('/api/client-log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ error: 'SPATIAL hands: ' + msg, ua: navigator.userAgent }) }); } catch (e) { /* ignore */ }
}

class OneEuro {
  constructor(minCutoff = 1.6, beta = 0.03) { this.min = minCutoff; this.beta = beta; this.x = null; this.dx = 0; this.t = 0; }
  filter(v, t) {
    if (this.x == null) { this.x = v; this.t = t; return v; }
    const dt = Math.max(1e-3, (t - this.t) / 1000); this.t = t;
    const a = (c) => 1 / (1 + 1 / (2 * Math.PI * c * dt));
    const dv = (v - this.x) / dt; this.dx += a(1) * (dv - this.dx);
    this.x += a(this.min + this.beta * Math.abs(this.dx)) * (v - this.x);
    return this.x;
  }
  reset() { this.x = null; this.dx = 0; }
}

const d2 = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

// Measured on real hand landmarks: a PINCH is the thumb tip on the index TIP (not resting along the finger, as in a fist or a
// chin-rest - there the thumb is nearer the index's middle joint); a FIST is all four fingers curled, checked first.
export function classify(lm, prevPinch) {
  const size = d2(lm[0], lm[9]) || 0.1;
  const ratio = (tip, pip) => d2(lm[0], lm[tip]) / (d2(lm[0], lm[pip]) || 1e-6);
  const fingers = [ratio(8, 6), ratio(12, 10), ratio(16, 14), ratio(20, 18)].map((r) => r > 1.12);
  const curled = [ratio(8, 6), ratio(12, 10), ratio(16, 14), ratio(20, 18)].every((r) => r < 1.0);
  const tip = d2(lm[4], lm[8]) / size, dip = d2(lm[4], lm[7]) / size;
  if (curled && !prevPinch) return 'fist';
  const onTip = tip < dip * 0.85 || tip < 0.12;                    // the thumb meets the fingertip itself
  if (prevPinch ? tip < 0.5 : tip < 0.3 && onTip) return 'pinch';
  if (curled) return 'fist';
  const n = fingers.filter(Boolean).length;
  if (n >= 4 && d2(lm[4], lm[5]) / size > 0.5) return 'palm';
  if (fingers[0] && !fingers[2] && !fingers[3]) return 'point';
  return 'hover';
}

/**
 * video: hidden <video> fed by the camera; overlay: full-screen <canvas> over the 3D view (hands are drawn on it);
 * preview: optional small <canvas> for the camera picture. on: { hover(x,y|null), select(x,y), pinchStart(x,y)->'grab'|'orbit',
 * pinchMove(x,y,dx,dy,depth), pinchEnd(flick), orbit(dx,dy), pan(dx,dy), zoom(f), roll(r), explode(d), dwell(k), gesture(text), status(text) }
 * (x, y are 0..1 across the view). Returns { stop() }.
 */
export async function startHands(video, overlay, on, preview, opts = {}) {
  const diag = { camera: 'starting', tracker: 'waiting', delegate: '', fps: 0, hands: 0, errors: 0 };
  const tell = () => on.diag?.({ ...diag });
  on.status?.('Starting the camera…'); tell();
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user', width: { ideal: 960 }, height: { ideal: 720 } }, audio: false });
  } catch (e) {
    diag.camera = 'blocked'; tell(); report(`camera: ${e.name} ${e.message}`);
    throw e;
  }
  video.srcObject = stream; video.muted = true; video.playsInline = true; video.setAttribute('playsinline', ''); video.setAttribute('muted', '');
  try { await video.play(); } catch (e) { report(`video.play: ${e.name} ${e.message}`); }
  diag.camera = 'on'; tell();
  on.status?.('Loading hand tracking (first time ~8 MB)…');
  let tracker;
  try { tracker = await landmarker('GPU'); } catch (e) {
    stream.getTracks().forEach((t) => t.stop()); diag.tracker = 'failed'; tell(); report(`tracker load: ${e.message}`);
    throw new Error("Hand tracking couldn't load (check the connection).");
  }
  let lmk = tracker.lmk;
  diag.tracker = 'on'; diag.delegate = tracker.delegate; tell();
  on.status?.('Raise a hand in front of the camera');
  let fpsCount = 0, fpsT = performance.now(), switching = false;

  const ctx = overlay.getContext('2d'), pctx = preview?.getContext('2d');
  const mk = () => ({ raw: 'none', stable: 'none', count: 0, pinch: false, fx: new OneEuro(), fy: new OneEuro(), x: 0, y: 0, px: null, py: null, fresh: true,
    seen: 0, size: 0.1, still: 0, vel: [], mode: null, size0: 0 });
  const hands = { Left: mk(), Right: mk() };
  let lastT = -1, stopped = false, raf = 0, two = null, palms = null, cooldown = 0, dwell = null, lostSince = 0, lastLm = [];

  // the camera picture is mapped onto the view like a mirror, keeping its aspect (cover) - hands line up with what they touch
  function map() {
    const w = overlay.clientWidth || 1, hh = overlay.clientHeight || 1, vw = video.videoWidth || 640, vh = video.videoHeight || 480;
    const s = Math.max(w / vw, hh / vh);
    return { w, hh, vw, vh, s, ox: (w - vw * s) / 2, oy: (hh - vh * s) / 2 };
  }
  const toView = (p) => { const m = map(); return { x: (m.ox + (1 - p.x) * m.vw * m.s) / m.w, y: (m.oy + p.y * m.vh * m.s) / m.hh }; };

  // timer mode keeps tracking while the window is behind others (AIR CONTROL); animation frames pause in background windows
  const next = () => (opts.timer ? setTimeout(frame, 28) : requestAnimationFrame(frame));
  function frame() {
    if (stopped) return;
    raf = next();
    if (video.readyState < 2 || video.currentTime === lastT) { draw(); return; }
    lastT = video.currentTime;
    const now = performance.now();
    let res;
    try { res = lmk.detectForVideo(video, now); } catch (e) {
      diag.errors++;
      if (diag.errors === 1) report(`detect (${diag.delegate}): ${e.message}`);
      if (diag.errors > 4 && diag.delegate === 'GPU' && !switching) {          // the GPU path runs but fails: switch to the CPU tracker
        switching = true; diag.tracker = 'switching'; tell();
        landmarker('CPU').then((t) => { lmk = t.lmk; diag.delegate = 'CPU'; diag.tracker = 'on'; diag.errors = 0; tell(); })
          .catch((err) => { diag.tracker = 'failed'; tell(); report(`cpu tracker: ${err.message}`); });
      }
      return;
    }
    fpsCount++;
    if (now - fpsT > 1000) { diag.fps = Math.round((fpsCount * 1000) / (now - fpsT)); fpsCount = 0; fpsT = now; diag.hands = (res.landmarks || []).length; tell(); }
    const present = new Set();
    lastLm = [];
    const wrists = [];
    (res.landmarks || []).forEach((lm, i) => {
      if (wrists.some((w) => Math.hypot(w.x - lm[0].x, w.y - lm[0].y) < 0.12)) return;   // a phantom copy of a hand already seen
      wrists.push(lm[0]);
      // left/right guesses are often unsure: use them only to keep each hand in its own slot
      const cat = res.handedness?.[i]?.[0];
      let name = cat && cat.categoryName === 'Left' ? 'Left' : 'Right';
      if (present.has(name)) name = name === 'Left' ? 'Right' : 'Left';
      if (present.has(name)) return;
      present.add(name);
      const h = hands[name];
      h.seen = now;
      const raw = classify(lm, h.pinch);
      if (raw === h.raw) h.count++; else { h.raw = raw; h.count = 1; }
      const need = raw === 'pinch' || h.stable === 'pinch' ? 2 : 3;
      if (h.count >= need && h.stable !== raw) { endPinch(h); h.stable = raw; h.fresh = true; }
      h.pinch = h.stable === 'pinch';
      const v = toView(raw === 'pinch' ? { x: (lm[4].x + lm[8].x) / 2, y: (lm[4].y + lm[8].y) / 2 } : raw === 'fist' ? lm[9] : lm[8]);
      const x = h.fx.filter(v.x, now), y = h.fy.filter(v.y, now);
      h.still = h.fresh ? 0 : Math.hypot(x - h.x, y - h.y);
      h.px = h.fresh ? null : h.x; h.py = h.fresh ? null : h.y;
      h.x = x; h.y = y; h.fresh = false;
      h.size = d2(lm[0], lm[9]);
      h.vel.push({ x, y, t: now }); if (h.vel.length > 6) h.vel.shift();
      lastLm.push({ lm, state: h.stable });
    });
    for (const [name, h] of Object.entries(hands)) {
      if (!present.has(name) && now - h.seen > 250 && h.stable !== 'none') { endPinch(h); h.stable = 'none'; h.raw = 'none'; h.pinch = false; h.fx.reset(); h.fy.reset(); h.fresh = true; h.vel = []; }
    }
    const act = Object.entries(hands).filter(([n, h]) => h.stable !== 'none' && present.has(n)).map(([, h]) => h);
    if (!act.length) {
      if (!lostSince) lostSince = now;
      if (now - lostSince > 1500) { on.status?.('Raise a hand in front of the camera'); on.hover?.(null); dwell = null; }
      two = palms = null;
      draw(); return;
    }
    lostSince = 0;
    const pinching = act.filter((h) => h.stable === 'pinch'), palmsUp = act.filter((h) => h.stable === 'palm');

    if (pinching.length === 2) {
      pinching.forEach((h) => { if (h.mode === 'grab') endPinch(h); h.mode = 'two'; });
      const [a, b] = pinching, dist = Math.hypot(a.x - b.x, a.y - b.y), ang = Math.atan2(b.y - a.y, b.x - a.x), mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      if (two) {
        if (dist > 0.04 && two.dist > 0.04) on.zoom?.(Math.max(0.9, Math.min(1.1, dist / two.dist)));
        let da = ang - two.ang; if (da > Math.PI) da -= 2 * Math.PI; if (da < -Math.PI) da += 2 * Math.PI;
        if (Math.abs(da) < 0.3) on.roll?.(-da);
        on.pan?.((mx - two.mx) * 1.2, (my - two.my) * 1.2);
      }
      two = { dist, ang, mx, my }; palms = null;
      on.gesture?.('TWO HANDS · ZOOM / TWIST'); on.hover?.(null);
      draw(); return;
    }
    two = null;
    if (palmsUp.length === 2) {
      const [a, b] = palmsUp, dist = Math.hypot(a.x - b.x, a.y - b.y);
      if (palms != null) on.explode?.((dist - palms) * 2.4);
      palms = dist;
      on.gesture?.('PALMS · SPREAD TO EXPLODE'); on.hover?.(null);
      draw(); return;
    }
    palms = null;
    const h = act.find((x) => x.stable === 'pinch') || act.find((x) => x.stable === 'fist') || act.find((x) => x.stable === 'point') || act[0];
    if (h.stable === 'pinch') {
      if (!h.mode) { h.mode = on.pinchStart?.(h.x, h.y) || 'orbit'; h.size0 = h.size; }
      if (h.mode === 'grab') {
        const depth = h.size0 ? ((h.size - h.size0) / h.size0) * -2.2 : 0;     // hand closer to the camera = pull the piece toward you
        on.pinchMove?.(h.x, h.y, h.px == null ? 0 : h.x - h.px, h.py == null ? 0 : h.y - h.py, depth);
        on.gesture?.('GRABBING · MOVE IT · FLICK TO THROW');
      } else if (h.mode === 'orbit') {
        if (h.px != null) on.orbit?.((h.x - h.px) * 5, (h.y - h.py) * 4);
        on.gesture?.('PINCH · ROTATE');
      }
      dwell = null;
    } else if (h.stable === 'fist') {
      if (h.px != null) on.pan?.((h.x - h.px) * 1.6, (h.y - h.py) * 1.6);
      on.gesture?.('FIST · MOVE VIEW'); on.hover?.(null); dwell = null;
    } else {                                                        // hover / point / palm: the cursor
      on.hover?.(h.x, h.y);
      if (!dwell || Math.hypot(h.x - dwell.x, h.y - dwell.y) > 0.03) dwell = { x: h.x, y: h.y, t: now };
      const k = Math.min(1, (now - dwell.t) / 900);
      on.dwell?.(k);
      on.gesture?.(k < 1 ? 'HOVER · HOLD STILL = SELECT · PINCH = GRAB' : 'SELECTED');
      if (k >= 1 && now > cooldown) { on.select?.(h.x, h.y); cooldown = now + 1400; dwell = { x: h.x, y: h.y, t: now + 1e6 }; }
    }
    draw();
  }

  function endPinch(h) {
    if (h.mode === 'grab') {
      const v = h.vel, a = v[0], b = v[v.length - 1];
      const speed = a && b && b.t > a.t ? Math.hypot(b.x - a.x, b.y - a.y) / ((b.t - a.t) / 1000) : 0;
      on.pinchEnd?.(speed > 2.0);
    } else if (h.mode === 'orbit') on.pinchEnd?.(false);
    h.mode = null;
  }

  // ---------------------------------------------------------------- holographic hands over the whole view
  const CONN = [[0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10], [10, 11], [11, 12], [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [17, 18], [18, 19], [19, 20], [0, 17]];
  const PALM = [0, 5, 9, 13, 17];
  function draw() {
    const dpr = Math.min(2, devicePixelRatio || 1);
    const w = Math.round(overlay.clientWidth * dpr), hh = Math.round(overlay.clientHeight * dpr);
    if (overlay.width !== w || overlay.height !== hh) { overlay.width = w; overlay.height = hh; }
    ctx.clearRect(0, 0, w, hh);
    const P = (p) => { const v = toView(p); return [v.x * w, v.y * hh]; };
    const t = performance.now() / 1000;
    for (const { lm, state } of lastLm) {
      const col = state === 'pinch' ? '255,181,71' : state === 'fist' ? '167,139,250' : '58,214,255';
      ctx.save();
      ctx.shadowColor = `rgba(${col},0.9)`; ctx.shadowBlur = 18 * dpr;
      ctx.fillStyle = `rgba(${col},0.08)`;
      ctx.beginPath(); PALM.forEach((i, k) => { const [x, y] = P(lm[i]); if (k) ctx.lineTo(x, y); else ctx.moveTo(x, y); }); ctx.closePath(); ctx.fill();
      ctx.strokeStyle = `rgba(${col},0.85)`; ctx.lineWidth = 2.2 * dpr; ctx.lineCap = 'round';
      ctx.beginPath();
      for (const [a, b] of CONN) { const [x1, y1] = P(lm[a]); const [x2, y2] = P(lm[b]); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); }
      ctx.stroke();
      for (let i = 0; i < 21; i++) {
        const [x, y] = P(lm[i]), tip = [4, 8, 12, 16, 20].includes(i);
        ctx.fillStyle = tip ? `rgba(${col},1)` : `rgba(${col},0.6)`;
        ctx.beginPath(); ctx.arc(x, y, (tip ? 4.2 : 2.4) * dpr, 0, Math.PI * 2); ctx.fill();
      }
      if (state === 'pinch') {
        const [x, y] = P({ x: (lm[4].x + lm[8].x) / 2, y: (lm[4].y + lm[8].y) / 2 });
        ctx.strokeStyle = `rgba(${col},0.9)`; ctx.lineWidth = 1.5 * dpr;
        for (let r = 0; r < 2; r++) { ctx.beginPath(); ctx.arc(x, y, (10 + r * 8 + Math.sin(t * 8 + r) * 2) * dpr, 0, Math.PI * 2); ctx.stroke(); }
      } else {
        const [x, y] = P(lm[8]);
        ctx.strokeStyle = `rgba(${col},0.8)`; ctx.lineWidth = 1.2 * dpr;
        ctx.beginPath(); ctx.arc(x, y, 12 * dpr, t * 3, t * 3 + Math.PI * 1.4); ctx.stroke();
      }
      ctx.restore();
    }
    if (pctx && video.readyState >= 2) {
      const pw = Math.round(preview.clientWidth * dpr), ph = Math.round(preview.clientHeight * dpr);
      if (preview.width !== pw) preview.width = pw;
      if (preview.height !== ph) preview.height = ph;
      pctx.save(); pctx.translate(pw, 0); pctx.scale(-1, 1); pctx.drawImage(video, 0, 0, pw, ph); pctx.restore();
    }
  }

  frame();
  return {
    stop() {
      stopped = true; cancelAnimationFrame(raf); clearTimeout(raf);
      stream.getTracks().forEach((t) => t.stop());
      video.srcObject = null;
      ctx.clearRect(0, 0, overlay.width, overlay.height);
    },
  };
}
