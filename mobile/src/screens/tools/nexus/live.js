// LIVE - a hands-free, real-time conversation with AURA. The mic stays open; whenever you finish
// saying something it goes to the PC (Whisper + AI, with the camera's current view when it's on),
// AURA answers out loud, and anything you asked to be DONE is passed to AURA's main brain.
import { h, el, icon, toast } from '../../../ui/components.js';
import * as audio from '../../../platform/audio.js';
import * as haptics from '../../../platform/haptics.js';
import * as storage from '../../../platform/storage.js';
import { keepAwake } from '../../../platform/sensors.js';
import { stage, toB64, playReply, camera, grab, stopStream, micError, camError } from './common.js';

const STATES = { starting: 'INITIALISING', listening: 'LISTENING', hearing: 'HEARING YOU', thinking: 'THINKING', speaking: 'SPEAKING', muted: 'MUTED' };

export function open(ctx) {
  stage('LIVE', 'SYS-01', (body, page, onCleanup) => {
    const S = { state: 'starting', level: 0, cam: storage.get('nx.live.cam', false), facing: 'environment', stream: null,
      mic: null, history: [], alive: true, muted: false };

    const video = h('video', { class: 'nx-live-video', playsinline: true, muted: true, autoplay: true, hidden: true });
    const orb = h('canvas', { class: 'nx-orb-canvas' });
    const status = el('div', 'nx-live-status', STATES.starting);
    const you = el('div', 'nx-cap you');
    const aura = el('div', 'nx-cap aura', 'Say anything. Ask about what you see, or tell me to do something.');
    const btn = (ic, label, fn) => { const b = h('button', { class: 'nx-round', 'aria-label': label }, icon(ic), el('span', null, label)); b.onclick = () => { haptics.tap(); fn(b); }; return b; };
    const camBtn = btn('camera', 'EYES', () => setCam(!S.cam));
    const flipBtn = btn('back', 'FLIP', () => { S.facing = S.facing === 'user' ? 'environment' : 'user'; if (S.cam) setCam(true); });
    const muteBtn = btn('mic', 'MUTE', () => setMute(!S.muted));
    const endBtn = btn('stop', 'END', () => page.close());
    endBtn.classList.add('danger');
    const stageEl = h('div', { class: 'nx-live' }, video, h('div', { class: 'nx-live-shade' }), orb, status,
      h('div', { class: 'nx-caps' }, you, aura), h('div', { class: 'nx-live-ctl' }, camBtn, flipBtn, muteBtn, endBtn));
    body.append(stageEl);
    orb.onclick = () => { if (S.state === 'speaking') { audio.stop(); } };

    const setState = (s) => { S.state = s; status.textContent = STATES[s] || s; stageEl.dataset.state = s; };

    // ---------------------------------------------------------------- the orb
    let raf = 0, t0 = performance.now();
    function draw() {
      if (!S.alive) return;
      const dpr = window.devicePixelRatio || 1, W = orb.clientWidth, H = orb.clientHeight;
      if (orb.width !== W * dpr) { orb.width = W * dpr; orb.height = H * dpr; }
      const g = orb.getContext('2d');
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, W, H);
      const t = (performance.now() - t0) / 1000, cx = W / 2, cy = H / 2, R = Math.min(W, H) * 0.24;
      const col = { listening: [58, 214, 255], hearing: [158, 243, 255], thinking: [167, 139, 250], speaking: [255, 181, 71] }[S.state] || [71, 112, 127];
      const energy = S.state === 'hearing' ? S.level : S.state === 'speaking' ? 0.35 + 0.25 * Math.sin(t * 9) * Math.sin(t * 3.3) : S.state === 'thinking' ? 0.2 : S.level * 0.6;
      const glow = g.createRadialGradient(cx, cy, R * 0.2, cx, cy, R * 2.2);
      glow.addColorStop(0, `rgba(${col},${0.35 + energy * 0.4})`); glow.addColorStop(1, `rgba(${col},0)`);
      g.fillStyle = glow; g.beginPath(); g.arc(cx, cy, R * 2.2, 0, Math.PI * 2); g.fill();
      for (let ring = 0; ring < 4; ring++) {
        g.beginPath();
        const n = 96;
        for (let i = 0; i <= n; i++) {
          const a = (i / n) * Math.PI * 2;
          const wob = Math.sin(a * (3 + ring) + t * (1.4 + ring * 0.7)) * Math.cos(a * 2 - t * 1.1);
          const r = R * (0.72 + ring * 0.13) + wob * R * (0.05 + energy * 0.35) * (1 - ring * 0.15);
          const x = cx + r * Math.cos(a), y = cy + r * Math.sin(a);
          i ? g.lineTo(x, y) : g.moveTo(x, y);
        }
        g.strokeStyle = `rgba(${col},${0.9 - ring * 0.18})`; g.lineWidth = ring ? 1 : 2;
        g.shadowColor = `rgb(${col})`; g.shadowBlur = ring ? 0 : 14;
        g.stroke();
      }
      g.shadowBlur = 0;
      if (S.state === 'thinking') {          // orbiting sparks
        for (let i = 0; i < 3; i++) {
          const a = t * 2.4 + (i * Math.PI * 2) / 3;
          g.fillStyle = `rgba(${col},.95)`; g.beginPath(); g.arc(cx + R * 1.25 * Math.cos(a), cy + R * 1.25 * Math.sin(a), 3.5, 0, Math.PI * 2); g.fill();
        }
      }
      raf = requestAnimationFrame(draw);
    }
    raf = requestAnimationFrame(draw);

    // ---------------------------------------------------------------- camera
    async function setCam(on) {
      stopStream(S.stream); S.stream = null;
      S.cam = on; storage.set('nx.live.cam', on);
      camBtn.classList.toggle('on', on); video.hidden = !on; stageEl.classList.toggle('cam', on);
      if (!on) return;
      try {
        S.stream = await camera(S.facing);
        video.srcObject = S.stream;
        video.classList.toggle('mirror', S.facing === 'user');
        await video.play().catch(() => {});
      } catch (e) { camError(e); S.cam = false; camBtn.classList.remove('on'); video.hidden = true; stageEl.classList.remove('cam'); }
    }

    function setMute(m) {
      S.muted = m; muteBtn.classList.toggle('on', m);
      if (!S.mic) return;
      if (m) { S.mic.pause(); setState('muted'); } else { S.mic.resume(); setState('listening'); }
    }

    // ---------------------------------------------------------------- one turn
    async function waitQuiet() {           // AURA's main brain may also be answering (after a command)
      for (let i = 0; i < 300 && S.alive && audio.isPlaying(); i++) await new Promise((r) => setTimeout(r, 200));
    }

    async function onUtterance(blob) {
      if (!S.alive || S.state === 'thinking' || S.state === 'speaking') return;
      S.mic.pause();
      setState('thinking');
      try {
        const res = await ctx.api.nexus('live', {
          audio: await toB64(blob), mime: 'audio/wav', image: S.cam ? grab(video, 768) : '', history: S.history.slice(-8),
        }, 60000);
        if (!S.alive) return;
        if (!res.heard) { resume(); return; }
        you.textContent = res.heard;
        aura.textContent = res.answer || '';
        S.history.push({ role: 'user', text: res.heard }, { role: 'aura', text: res.answer || '' });
        if (res.command) {
          haptics.success();
          ctx.send(res.command);
          aura.append(h('div', { class: 'nx-cap-act' }, '⚡ ', res.command));
        }
        setState('speaking');
        if (ctx.store.get('settings').voice) await playReply(res);
        await waitQuiet();
        if (res.end) { page.close(); return; }
      } catch (e) {
        toast('LIVE', e.message, 'error', 3500);
      }
      resume();
    }
    function resume() {
      if (!S.alive) return;
      if (S.muted) { setState('muted'); return; }
      S.mic.resume(); setState('listening');
    }

    // ---------------------------------------------------------------- start
    (async () => {
      try {
        S.mic = await audio.listen({
          onUtterance, maxSeconds: 20,
          onLevel: (l) => { S.level = S.level * 0.6 + l * 0.4; },
          onSpeech: (on) => { if (S.state === 'listening' || S.state === 'hearing') setState(on ? 'hearing' : 'listening'); },
          onBlocked: (b) => { if (S.state === 'listening' || S.state === 'hearing') status.textContent = b ? 'TAP THE SCREEN SO I CAN HEAR YOU' : STATES[S.state]; },
        });
        if (!S.alive) { S.mic.stop(); return; }
        setState('listening');
        if (S.cam) setCam(true);
      } catch (e) { micError(e); setState('muted'); }
    })();

    const release = keepAwake();
    onCleanup(() => {
      S.alive = false; cancelAnimationFrame(raf); release();
      if (S.mic) S.mic.stop();
      stopStream(S.stream); audio.stop();
    });
  });
}
