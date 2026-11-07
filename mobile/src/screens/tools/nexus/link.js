// LINK - your PC in your hand. A live view of the PC's screen, and three ways to drive it:
//   TOUCH     tap the picture where you want to click (hold = right-click, two fingers = scroll)
//   TRACKPAD  the screen becomes a laptop trackpad (tap = click, two-finger tap = right-click, two-finger drag = scroll)
//   AIR       point and wave the phone - the gyroscope moves the mouse
// plus a keyboard, shortcut keys, media keys, clipboard both ways, and locking the PC.
// Whoever is at the PC sees a notice when the phone connects (nexus.py).
import { h, el, icon, toast, button, openSheet, confirm } from '../../../ui/components.js';
import * as haptics from '../../../platform/haptics.js';
import * as storage from '../../../platform/storage.js';
import { requestMotion, onMotion, keepAwake } from '../../../platform/sensors.js';
import { stage, chips } from './common.js';

const QUALITY = { eco: [720, 40, 700], hd: [1100, 55, 350], max: [1600, 70, 200] };      // width, jpeg quality, min ms between frames
const KEYS = [['esc', 'ESC'], ['tab', 'TAB'], ['backspace', '⌫'], ['enter', '↵'], ['left', '←'], ['up', '↑'], ['down', '↓'], ['right', '→'],
  ['win', '⊞'], ['alt+tab', 'ALT⇥'], ['ctrl+c', 'COPY'], ['ctrl+v', 'PASTE'], ['ctrl+z', 'UNDO'], ['ctrl+s', 'SAVE'], ['alt+f4', 'CLOSE'], ['win+d', 'DESKTOP'],
  ['f5', 'F5'], ['f11', 'FULL'], ['pageup', 'PG↑'], ['pagedown', 'PG↓']];
const MEDIA = [['prevtrack', '⏮'], ['playpause', '⏯'], ['nexttrack', '⏭'], ['volumedown', '🔉'], ['volumemute', '🔇'], ['volumeup', '🔊']];

export function open(ctx) {
  stage('LINK', 'SYS-06', (body, page, onCleanup) => {
    const S = { mode: storage.get('nx.link.mode', 'touch'), q: storage.get('nx.link.q', 'hd'), alive: true, live: true,
      fps: 0, frames: 0, fpsAt: performance.now(), url: null, pending: [], sending: false, dx: 0, dy: 0, sx: 0, sy: 0, air: false };

    const img = h('img', { class: 'nx-screen', alt: 'Your PC screen', draggable: 'false' });
    const pad = h('div', { class: 'nx-pad' }, el('span', null, 'TRACKPAD'));
    const airPad = h('div', { class: 'nx-air' }, el('b', null, 'AIR MOUSE'), el('span', null, 'Hold the big button and move the phone like a laser pointer.'));
    const airHold = h('button', { class: 'nx-air-hold' }, 'HOLD TO AIM');
    const airBtns = h('div', { class: 'row-btns' });
    airPad.append(airHold, airBtns);
    const tele = el('div', 'nx-link-tele', 'CONNECTING…');
    const screenBox = h('div', { class: 'nx-screen-box' }, img, tele);
    const modeChips = chips([['touch', 'TOUCH'], ['trackpad', 'TRACKPAD'], ['air', 'AIR']], S.mode, (m) => setMode(m));
    const qChips = chips([['eco', 'ECO'], ['hd', 'HD'], ['max', 'MAX']], S.q, (q) => { S.q = q; storage.set('nx.link.q', q); });
    const kbd = h('input', { class: 'input nx-kbd', placeholder: 'Type on your PC… (Enter sends)', autocapitalize: 'off', autocomplete: 'off', enterkeyhint: 'send' });
    const keys = h('div', { class: 'nx-keys' }, KEYS.map(([k, label]) => keyBtn(k, label)));
    const media = h('div', { class: 'nx-keys media' }, MEDIA.map(([k, label]) => keyBtn(k, label)));
    const tools = h('div', { class: 'row-btns' },
      button('PC → HERE (CLIPBOARD)', pullClip), button('HERE → PC', pushClip),
      button('LOCK PC', async () => { if (await confirm('Lock your PC?', 'Windows will lock - you\'ll need your PIN/password at the PC to get back in.', 'LOCK', true)) send({ type: 'lock' }); }, 'danger'));
    const pauseBtn = button('PAUSE VIEW', () => { S.live = !S.live; pauseBtn.textContent = S.live ? 'PAUSE VIEW' : 'RESUME VIEW'; if (S.live) loop(); });

    body.append(h('div', { class: 'nx-scroll nx-linkpage' }, screenBox, h('div', { class: 'nx-link-bar' }, modeChips.node, qChips.node), pad, airPad,
      h('div', { class: 'nx-row' }, kbd, button('SEND', typeIt, 'solid')), keys, el('div', 'nx-label', 'MEDIA'), media, el('div', 'nx-label', 'TOOLS'), tools, pauseBtn));

    function keyBtn(k, label) {
      const b = h('button', { class: 'nx-key' }, label);
      b.onclick = () => { haptics.tap(); send({ type: 'key', keys: k }); };
      return b;
    }

    // ---------------------------------------------------------------- sending input (batched, one request at a time)
    function send(ev) { S.pending.push(ev); flush(); }
    async function flush() {
      if (S.sending) return;
      if (S.dx || S.dy) { S.pending.unshift({ type: 'move_rel', dx: Math.round(S.dx), dy: Math.round(S.dy) }); S.dx = 0; S.dy = 0; }
      if (S.sx || S.sy) { S.pending.push({ type: 'scroll', dy: Math.round(S.sy), dx: Math.round(S.sx) }); S.sx = 0; S.sy = 0; }
      if (!S.pending.length) return;
      const events = S.pending.splice(0, 40);
      S.sending = true;
      try {
        const r = await ctx.api.nexus('input', { events }, 8000);
        if (r.error && r.error !== 'fail-safe') toast('LINK', r.error, 'error', 3000);
      } catch (e) { toast('LINK', e.message, 'error', 2500); }
      S.sending = false;
      if (S.pending.length || S.dx || S.dy || S.sx || S.sy) setTimeout(flush, 16);
    }
    const nudge = () => { if (!S.sending) setTimeout(flush, 30); };

    async function typeIt() {
      if (!kbd.value) { send({ type: 'key', keys: 'enter' }); return; }
      send({ type: 'type', text: kbd.value }); kbd.value = '';
    }
    kbd.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); typeIt(); } };

    async function pullClip() {
      const r = await ctx.api.nexus('clipboard');
      openSheet('PC CLIPBOARD', (b, sheet) => {
        const ta = h('textarea', { class: 'textarea', readonly: true }, r.text || '');
        b.append(ta, h('div', { class: 'row-btns', style: 'margin-top:10px' }, button('COPY TO PHONE', async () => {
          try { await navigator.clipboard.writeText(r.text || ''); toast('Copied', 'Now on your phone\'s clipboard.', 'info', 2500); sheet.close(); } catch (e) { ta.select(); toast('Copy', 'Select the text and copy it.', 'info'); }
        }, 'solid')));
      });
    }
    function pushClip() {
      openSheet('SEND TO PC CLIPBOARD', (b, sheet) => {
        const ta = h('textarea', { class: 'textarea', placeholder: 'Paste or type here…' });
        b.append(ta, h('div', { class: 'row-btns', style: 'margin-top:10px' },
          button('PASTE FROM PHONE', async () => { try { ta.value = await navigator.clipboard.readText(); } catch (e) { toast('Paste', 'Long-press the box and choose Paste.', 'info'); } }),
          button('SEND', async () => { await ctx.api.nexus('clipboard', { text: ta.value }); toast('Sent', 'It\'s on your PC\'s clipboard - Ctrl+V to paste.', 'info', 3000); sheet.close(); }, 'solid')));
      });
    }

    // ---------------------------------------------------------------- the live screen
    async function loop() {
      while (S.alive && S.live) {
        if (document.visibilityState !== 'visible') { await wait(500); continue; }
        const [w, q, gap] = QUALITY[S.q] || QUALITY.hd;
        const t0 = performance.now();
        try {
          const res = await ctx.api.screen(w, q);
          const blob = await res.blob();
          const url = URL.createObjectURL(blob);
          img.onload = () => { if (S.url) URL.revokeObjectURL(S.url); S.url = url; };
          img.src = url;
          S.frames++;
          const now = performance.now();
          if (now - S.fpsAt > 1000) { S.fps = (S.frames * 1000) / (now - S.fpsAt); S.frames = 0; S.fpsAt = now; }
          tele.textContent = `LIVE · ${S.fps.toFixed(1)} FPS · ${Math.round(blob.size / 1024)} KB · ${res.headers.get('X-Screen-Size') || ''}`;
          tele.classList.remove('err');
        } catch (e) {
          tele.textContent = e.status === 0 ? 'PC UNREACHABLE - RETRYING' : 'NO SCREEN - ' + e.message.toUpperCase();
          tele.classList.add('err');
          await wait(1500);
        }
        await wait(Math.max(0, gap - (performance.now() - t0)));
      }
    }
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));

    // ---------------------------------------------------------------- TOUCH: tap the picture
    const norm = (t) => { const r = img.getBoundingClientRect(); return { x: (t.clientX - r.left) / r.width, y: (t.clientY - r.top) / r.height }; };
    let touch = null;
    img.addEventListener('touchstart', (e) => {
      if (S.mode !== 'touch') return;
      e.preventDefault();
      const t = e.touches[0];
      touch = { n: e.touches.length, x: t.clientX, y: t.clientY, at: Date.now(), moved: false, p: norm(t) };
      touch.hold = setTimeout(() => { if (touch && !touch.moved && touch.n === 1) { haptics.warning(); send({ type: 'click', button: 'right', ...touch.p }); touch.done = true; } }, 550);
    }, { passive: false });
    img.addEventListener('touchmove', (e) => {
      if (S.mode !== 'touch' || !touch) return;
      e.preventDefault();
      const t = e.touches[0], dy = t.clientY - touch.y, dx = t.clientX - touch.x;
      if (Math.hypot(dx, dy) > 8) touch.moved = true;
      if (e.touches.length >= 2) { S.sy += dy * 4; S.sx -= dx * 2; touch.x = t.clientX; touch.y = t.clientY; nudge(); }
    }, { passive: false });
    img.addEventListener('touchend', (e) => {
      if (S.mode !== 'touch' || !touch) return;
      e.preventDefault();
      clearTimeout(touch.hold);
      if (!touch.moved && !touch.done && touch.n === 1) {
        const dbl = S.lastTap && Date.now() - S.lastTap < 300;
        haptics.tap();
        send({ type: 'click', ...touch.p, double: !!dbl });
        S.lastTap = dbl ? 0 : Date.now();
      }
      touch = null;
    }, { passive: false });

    // ---------------------------------------------------------------- TRACKPAD
    let tp = null;
    pad.addEventListener('touchstart', (e) => {
      e.preventDefault();
      const t = e.touches[0];
      if (!tp) tp = { at: Date.now(), maxN: 0, moved: 0 };
      tp.maxN = Math.max(tp.maxN, e.touches.length); tp.x = t.clientX; tp.y = t.clientY;
    }, { passive: false });
    pad.addEventListener('touchmove', (e) => {
      if (!tp) return;
      e.preventDefault();
      const t = e.touches[0], dx = t.clientX - tp.x, dy = t.clientY - tp.y;
      tp.x = t.clientX; tp.y = t.clientY; tp.moved += Math.abs(dx) + Math.abs(dy);
      if (e.touches.length >= 2) { S.sy += -dy * 5; S.sx += dx * 3; } else {
        const speed = Math.hypot(dx, dy), accel = 1.4 + Math.min(2.6, speed / 8);        // faster swipe = further
        S.dx += dx * accel; S.dy += dy * accel;
      }
      nudge();
    }, { passive: false });
    pad.addEventListener('touchend', (e) => {
      e.preventDefault();
      if (!tp || e.touches.length) return;
      if (tp.moved < 10 && Date.now() - tp.at < 300) { haptics.tap(); send({ type: 'click', button: tp.maxN >= 2 ? 'right' : 'left' }); }
      tp = null;
    }, { passive: false });

    // ---------------------------------------------------------------- AIR mouse (gyroscope)
    let stopAir = () => {};
    for (const [label, ev] of [['CLICK', { type: 'click' }], ['RIGHT', { type: 'click', button: 'right' }], ['DOUBLE', { type: 'click', double: true }]]) {
      airBtns.append(button(label, () => send(ev), label === 'CLICK' ? 'solid' : ''));
    }
    const holdOn = (e) => { e.preventDefault(); S.air = true; airHold.classList.add('on'); haptics.tap(); };
    const holdOff = () => { S.air = false; airHold.classList.remove('on'); };
    airHold.addEventListener('touchstart', holdOn, { passive: false });
    airHold.addEventListener('touchend', holdOff);
    airHold.addEventListener('touchcancel', holdOff);
    async function startAir() {
      if (!(await requestMotion())) { toast('AIR', 'Motion access is needed - allow it in Settings → Safari → Motion & Orientation Access.', 'error'); return; }
      stopAir();
      stopAir = onMotion((m) => {
        if (!S.air) return;
        // rotation rate (°/s): turning left/right -> x, tilting up/down -> y
        const k = 0.9 * (m.dt > 1 ? m.dt / 1000 : m.dt) * 60;
        S.dx += -m.ra * k * 0.55; S.dy += -m.rb * k * 0.55;
        if (Math.abs(S.dx) + Math.abs(S.dy) > 2) nudge();
      });
    }

    function setMode(m) {
      S.mode = m; storage.set('nx.link.mode', m); modeChips.set(m);
      pad.hidden = m !== 'trackpad'; airPad.hidden = m !== 'air';
      screenBox.classList.toggle('touch', m === 'touch');
      if (m === 'air') startAir(); else { stopAir(); stopAir = () => {}; }
    }

    setMode(S.mode);
    loop();
    const release = keepAwake();
    onCleanup(() => { S.alive = false; stopAir(); release(); if (S.url) URL.revokeObjectURL(S.url); });
  });
}
