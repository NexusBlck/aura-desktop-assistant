// AIR CONTROL - drive the whole PC with your hands. Runs in AURA's own window on the PC (it needs the webcam): your camera
// with a glowing hand skeleton, and every gesture becomes real mouse / keyboard input on Windows.
//   ☝/✋ move your hand   -> the cursor follows your index finger (the middle of the camera picture = the whole screen)
//   🤏 pinch + release    -> click            🤏 pinch, move, release -> drag (select text, move windows)
//   🤏🤏 two quick pinches -> double-click     ✊ fist + move up/down  -> scroll
//   🤏🤏 two hands pinch   -> zoom in / out (spread / squeeze)
//   ✋✋ two palms apart    -> task view · together -> show desktop
// PAUSE stops all input instantly; with no hand in view nothing is sent.
import { h, el, toast } from '../../ui/components.js';
import { stage } from './nexus/common.js';
import { startHands, report } from '../../spatial/gestures.js';

const ZONE = { x0: 0.18, x1: 0.82, y0: 0.14, y1: 0.72 };          // the part of the camera picture that maps to the whole screen

export function open(ctx, arg = {}) {
  stage('AIR CONTROL', 'SYS-11', (body, page, onCleanup) => {
    const video = h('video', { playsinline: true, muted: true, autoplay: true, class: 'air-cam' });
    const canvas = h('canvas', { class: 'air-hands' });
    const gesture = el('div', 'air-gesture', 'Starting…');
    const diag = el('div', 'air-diag', '');
    const pauseBtn = h('button', { class: 'air-btn', type: 'button' }, 'PAUSE');
    const zone = h('div', { class: 'air-zone' });
    const guide = h('div', { class: 'air-guide' },
      ...[['☝', 'MOVE', 'cursor follows your finger'], ['🤏', 'PINCH', 'click · hold = drag'], ['✊', 'FIST ↕', 'scroll'],
        ['🤏🤏', 'TWO PINCH', 'zoom'], ['✋✋', 'PALMS', 'apart: task view · together: desktop']]
        .map(([g, t, d]) => h('div', {}, el('b', null, g), el('span', null, t), el('i', null, d))));
    const root = h('div', { class: 'air' }, video, zone, canvas, h('div', { class: 'air-top' }, diag, gesture), guide, h('div', { class: 'air-bar' }, pauseBtn));
    body.append(root);

    let paused = false, hands = null, scrollAcc = 0, zoomAcc = 1, spreadAcc = 0, lastShortcut = 0, down = false;
    pauseBtn.onclick = () => {
      paused = !paused;
      pauseBtn.textContent = paused ? 'RESUME' : 'PAUSE';
      root.classList.toggle('paused', paused);
      if (paused && down) { down = false; post([{ type: 'up' }]); }
      gesture.textContent = paused ? 'PAUSED - no input is sent' : 'Ready';
    };

    const toScreen = (x, y) => ({ x: Math.min(1, Math.max(0, (x - ZONE.x0) / (ZONE.x1 - ZONE.x0))), y: Math.min(1, Math.max(0, (y - ZONE.y0) / (ZONE.y1 - ZONE.y0))) });
    // cursor moves stream (up to 3 in flight, only the newest matters); clicks / drags / scrolls keep their order
    let inflight = 0, pendingMove = null, ordered = Promise.resolve();
    const post = (events) => ctx.api.overdrive('air', { events }).catch((e) => { gesture.textContent = 'Lost the PC: ' + e.message; });
    function pumpMove() {
      if (!pendingMove || inflight >= 3) return;
      const ev = pendingMove; pendingMove = null; inflight++;
      post([ev]).finally(() => { inflight--; pumpMove(); });
    }
    function send(ev) {
      if (paused) return;
      if (ev.type === 'move_abs') { pendingMove = ev; pumpMove(); return; }
      const before = pendingMove; pendingMove = null;
      const batch = before ? [before, ev] : [ev];
      ordered = ordered.then(() => post(batch));
    }
    function flush() { if (pendingMove) pumpMove(); }
    function shortcut(name) {
      const now = performance.now();
      if (now - lastShortcut < 1500) return;
      lastShortcut = now;
      send({ type: 'shortcut', name });
      gesture.textContent = name.replace('_', ' ').toUpperCase();
    }

    (async () => {
      try {
        hands = await startHands(video, canvas, {
          status: (t) => { gesture.textContent = t; },
          gesture: (g) => { if (g && !paused) gesture.textContent = g.replace('HOVER · HOLD STILL = SELECT · PINCH = GRAB', 'MOVE · PINCH = CLICK').replace('GRABBING · MOVE IT · FLICK TO THROW', 'DRAGGING').replace('FIST · MOVE VIEW', 'SCROLLING').replace('TWO HANDS · ZOOM / TWIST', 'ZOOM').replace('PALMS · SPREAD TO EXPLODE', 'TASK VIEW / DESKTOP'); },
          diag: (d) => { diag.textContent = `${d.camera === 'on' ? '●' : '○'} CAMERA  ·  ${d.tracker === 'on' ? '●' : '○'} TRACKER${d.delegate ? ' ' + d.delegate : ''}  ·  ${d.hands} HAND${d.hands === 1 ? '' : 'S'}  ·  ${d.fps} FPS`; diag.classList.toggle('bad', d.camera !== 'on' || d.tracker === 'failed'); },
          hover: (x, y) => { if (x != null) { const p = toScreen(x, y); send({ type: 'move_abs', x: p.x, y: p.y }); } },
          select: () => {},
          dwell: () => {},
          pinchStart: (x, y) => { const p = toScreen(x, y); send({ type: 'move_abs', x: p.x, y: p.y }); send({ type: 'down' }); down = true; return 'grab'; },
          pinchMove: (x, y) => { const p = toScreen(x, y); send({ type: 'move_abs', x: p.x, y: p.y }); },
          pinchEnd: () => { if (down) { send({ type: 'up' }); down = false; } },
          pan: (dx, dy) => {
            scrollAcc += dy;
            if (Math.abs(scrollAcc) > 0.012) { send({ type: 'scroll', dy: Math.round(-scrollAcc * 2600) }); scrollAcc = 0; }
          },
          zoom: (f) => {
            zoomAcc *= f;
            if (zoomAcc > 1.18) { shortcut('zoom_in'); zoomAcc = 1; } else if (zoomAcc < 0.85) { shortcut('zoom_out'); zoomAcc = 1; }
          },
          explode: (d) => {
            spreadAcc += d;
            if (spreadAcc > 0.35) { shortcut('task_view'); spreadAcc = 0; } else if (spreadAcc < -0.35) { shortcut('show_desktop'); spreadAcc = 0; }
          },
          orbit: () => {}, roll: () => {},
        }, null, { timer: true });
      } catch (e) {
        report(`air start: ${e && e.name} ${e && e.message}`);
        gesture.textContent = e && e.name === 'NotAllowedError' ? 'Camera blocked - click the camera icon in the address bar and allow it.' : (e.message || 'Hand tracking could not start.');
        toast('Air Control', gesture.textContent, 'error', 8000);
      }
    })();
    setTimeout(() => guide.classList.add('fold'), 9000);
    onCleanup(() => { if (down) ctx.api.overdrive('air', { events: [{ type: 'up' }] }).catch(() => {}); hands?.stop(); if (arg?.standalone) setTimeout(() => window.close(), 50); });
  });
}
