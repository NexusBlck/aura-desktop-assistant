// PULSE - a bio-scanner made from the phone's own sensors (educational, not a medical device):
//   HEART   fingertip over the back camera -> the colour changes with each heartbeat (photoplethysmography)
//           -> heart rate, beat-to-beat variability (RMSSD) and a stress estimate
//   BREATH  phone resting on your chest/belly while lying down -> the accelerometer counts breaths
//   REFLEX  tap the moment the screen flashes - reaction time over 5 tries
// Readings are kept on the PC (aura_bio.json) with a history chart.
import { h, el, icon, toast, button, drawSpark } from '../../../ui/components.js';
import * as haptics from '../../../platform/haptics.js';
import { requestMotion, onMotion, keepAwake } from '../../../platform/sensors.js';
import { whenOf } from '../../../core/format.js';
import { stage, camera, stopStream, camError, say } from './common.js';

// ---------------------------------------------------------------- signal helpers
/** Resamples (t ms, v) pairs to an even rate (Hz). */
function resample(samples, hz) {
  if (samples.length < 2) return [];
  const out = [], step = 1000 / hz;
  let j = 0;
  for (let t = samples[0].t; t <= samples[samples.length - 1].t; t += step) {
    while (j < samples.length - 2 && samples[j + 1].t < t) j++;
    const a = samples[j], b = samples[j + 1], k = (t - a.t) / Math.max(1, b.t - a.t);
    out.push(a.v + (b.v - a.v) * k);
  }
  return out;
}
const movingAvg = (x, n) => {
  const out = new Array(x.length); let s = 0;
  for (let i = 0; i < x.length; i++) { s += x[i]; if (i >= n) s -= x[i - n]; out[i] = s / Math.min(i + 1, n); }
  return out;
};
/** Band-pass by difference of moving averages: removes slow drift and fast noise. */
const bandpass = (x, fast, slow) => { const a = movingAvg(x, fast), b = movingAvg(x, slow); return a.map((v, i) => v - b[i]); };
/** The strongest repeating period between minLag and maxLag samples (autocorrelation). */
function bestPeriod(x, minLag, maxLag) {
  const n = x.length, mean = x.reduce((s, v) => s + v, 0) / n, y = x.map((v) => v - mean);
  let e0 = 0; for (const v of y) e0 += v * v;
  const corr = (lag) => { let s = 0; for (let i = 0; i + lag < n; i++) s += y[i] * y[i + lag]; return s / (e0 || 1); };
  let best = 0, bestLag = 0;
  for (let lag = minLag; lag <= Math.min(maxLag, n - 2); lag++) {
    const r = corr(lag);
    if (r > best) { best = r; bestLag = lag; }
  }
  if (bestLag > minLag && bestLag < maxLag) {        // refine between samples (parabola through the peak)
    const a = corr(bestLag - 1), c = corr(bestLag + 1), d = a - 2 * best + c;
    if (d < 0) bestLag += Math.max(-0.5, Math.min(0.5, (0.5 * (a - c)) / d));
  }
  return { lag: bestLag, strength: best };
}

/** The strongest rhythm between lo and hi beats per minute (a frequency spectrum, Hann-windowed).
 *  quality = how far the peak stands out from the rest (peak / median power). */
function spectrum(x, hz, lo = 40, hi = 180, step = 0.5) {
  const n = x.length, mean = x.reduce((s, v) => s + v, 0) / n;
  const y = x.map((v, i) => (v - mean) * (0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (n - 1))));
  const powers = [];
  for (let bpm = lo; bpm <= hi + 1e-9; bpm += step) {
    const w = (2 * Math.PI * bpm) / 60 / hz;
    let re = 0, im = 0;
    for (let i = 0; i < n; i++) { re += y[i] * Math.cos(w * i); im -= y[i] * Math.sin(w * i); }
    powers.push(re * re + im * im);
  }
  let k = 0;
  for (let i = 1; i < powers.length; i++) if (powers[i] > powers[k]) k = i;
  let bpm = lo + k * step;
  if (k > 0 && k < powers.length - 1) {                    // between bins (parabola through the peak)
    const a = powers[k - 1], b = powers[k], c = powers[k + 1], d = a - 2 * b + c;
    if (d < 0) bpm += Math.max(-0.5, Math.min(0.5, (0.5 * (a - c)) / d)) * step;
  }
  // a pulse wave's 2nd harmonic can beat its fundamental - prefer half the rate when that's nearly as strong
  const halfK = Math.round((bpm / 2 - lo) / step);
  if (bpm >= 90 && halfK >= 0 && powers[halfK] > powers[k] * 0.5) bpm /= 2;
  const sorted = powers.slice().sort((p, q) => p - q);
  return { bpm, quality: powers[k] / (sorted[Math.floor(sorted.length / 2)] || 1) };
}

/** Heart rate from fingertip colour samples ({t, r, g}): uses whichever colour carries the clearest pulse
 *  (with the flash on, red saturates and the pulse shows in green). Returns null if there's too little data. */
function pulseRate(samples, hz = 30) {
  let best = null;
  for (const ch of ['r', 'g']) {
    const x = resample(samples.map((s) => ({ t: s.t, v: s[ch] })), hz);
    if (x.length < hz * 5) return null;
    const sig = bandpass(x, 3, Math.round(hz * 1.5)).map((v) => -v);       // drop slow drift (pressure, exposure) and jitter
    const sp = spectrum(sig, hz);
    if (!best || sp.quality > best.quality) best = { ...sp, sig, channel: ch };
  }
  return best;
}

export function open(ctx) {
  stage('PULSE', 'SYS-05', (body, page, onCleanup) => {
    const wrap = h('div', { class: 'nx-scroll' });
    body.append(wrap);
    let stopCurrent = () => {};
    onCleanup(() => stopCurrent());

    function home() {
      stopCurrent(); stopCurrent = () => {};
      wrap.innerHTML = '';
      const card = (ic, name, text, fn) => {
        const b = h('button', { class: 'nx-card' }, h('div', { class: 'nx-card-top' }, h('span', { class: 'nx-card-ic' }, icon(ic))), el('b', null, name), el('p', null, text));
        b.onclick = () => { haptics.tap(); fn(); };
        return b;
      };
      wrap.append(h('div', { class: 'nx-grid' },
        card('heart', 'HEART', 'Heart rate, variability and stress from your fingertip on the camera. ~30 s.', heart),
        card('wave', 'BREATH', 'Breathing rate - lie down with the phone resting on your belly. ~45 s.', breath),
        card('bolt', 'REFLEX', 'Reaction time: tap the instant the screen flashes. 5 tries.', reflex)),
      el('p', 'nx-note', 'For curiosity and training - not a medical device. If you feel unwell, see a doctor.'));
      history();
    }

    async function history() {
      const box = h('div', { class: 'nx-panel' }, el('div', 'nx-label', 'HISTORY'));
      wrap.append(box);
      try {
        const { items } = await ctx.api.nexus('bio');
        const heart = items.filter((x) => x.kind === 'heart').slice(-30), breathI = items.filter((x) => x.kind === 'breath').slice(-30), react = items.filter((x) => x.kind === 'reaction').slice(-30);
        if (!items.length) { box.append(el('p', 'nx-note', 'No readings yet.')); return; }
        const chart = (label, list, unit, max, color) => {
          if (!list.length) return null;
          const c = h('canvas', { class: 'nx-spark' });
          const last = list[list.length - 1];
          const node = h('div', { class: 'nx-hist' }, h('div', { class: 'nx-hist-head' }, el('span', null, label), el('b', null, `${Math.round(last.value)} ${unit}`), el('em', null, whenOf(last.t))), c);
          requestAnimationFrame(() => drawSpark(c, [{ data: list.map((x) => x.value), max, color, fill: color.replace('rgb', 'rgba').replace(')', ',.12)') }]));
          return node;
        };
        box.append(chart('HEART RATE', heart, 'bpm', 160, 'rgb(255,90,106)'), chart('BREATHING', breathI, '/min', 40, 'rgb(58,214,255)'), chart('REACTION', react, 'ms', 600, 'rgb(255,181,71)'));
      } catch (e) { box.append(el('div', 'err', e.message)); }
    }

    function header(title, onBack) {
      const back = h('button', { class: 'nx-link' }, '‹ PULSE');
      back.onclick = onBack || home;
      return [back, h('h2', { class: 'nx-title' }, title)];
    }

    // ---------------------------------------------------------------- HEART (camera PPG)
    async function heart() {
      wrap.innerHTML = '';
      const video = h('video', { playsinline: true, muted: true, autoplay: true, class: 'nx-ppg-video' });
      const wave = h('canvas', { class: 'nx-ppg' });
      const bpmEl = el('div', 'nx-bpm', '--');
      const msg = el('div', 'nx-ppg-msg', 'Cover the back camera (and flash) completely with your fingertip. Press lightly and keep still.');
      const prog = el('i');
      const result = h('div');
      wrap.append(...header('HEART SCAN'), h('div', { class: 'nx-ppg-box' }, video, h('div', { class: 'nx-ppg-ring' }, h('span', { class: 'nx-heart-ic' }, icon('heart')), bpmEl, el('small', null, 'BPM'))),
        wave, h('div', { class: 'bar' }, prog), msg, result);

      let stream;
      try { stream = await camera('environment', { frameRate: { ideal: 30 } }); } catch (e) { camError(e); home(); return; }
      video.srcObject = stream;
      await video.play().catch(() => {});
      const track = stream.getVideoTracks()[0];
      try { await track.applyConstraints({ advanced: [{ torch: true }] }); } catch (e) { /* no torch on this phone/browser - bright light helps */ }

      const c = document.createElement('canvas'); c.width = 40; c.height = 40;
      const g = c.getContext('2d', { willReadFrequently: true });
      const samples = [], DURATION = 30000, GOOD = 15;       // spectrum quality: a real pulse scores 40+, noise ~6
      let start = 0, alive = true, raf = 0, lastBeat = 0, lastEst = 0, liveBpm = 0, prev = null;
      const release = keepAwake();
      const useVfc = typeof video.requestVideoFrameCallback === 'function';     // one sample per real camera frame
      stopCurrent = () => { alive = false; cancelAnimationFrame(raf); try { track.applyConstraints({ advanced: [{ torch: false }] }); } catch (e) { /* ignore */ } stopStream(stream); release(); };

      function next() { if (!alive) return; if (useVfc) video.requestVideoFrameCallback(step); else raf = requestAnimationFrame(step); }
      function step() {
        if (!alive) return;
        next();
        if (!video.videoWidth) return;
        const s = Math.min(video.videoWidth, video.videoHeight) * 0.5;
        g.drawImage(video, (video.videoWidth - s) / 2, (video.videoHeight - s) / 2, s, s, 0, 0, 40, 40);
        const d = g.getImageData(0, 0, 40, 40).data;
        let r = 0, gg = 0, b = 0;
        for (let i = 0; i < d.length; i += 4) { r += d[i]; gg += d[i + 1]; b += d[i + 2]; }
        const n = d.length / 4; r /= n; gg /= n; b /= n;
        if (!useVfc && prev && prev.r === r && prev.g === gg) return;          // same camera frame drawn twice (120 Hz screen)
        prev = { r, g: gg };
        const covered = r > 60 && r >= gg && r > b * 1.3;                        // a fingertip glows red (with or without the flash)
        const now = performance.now();
        if (!covered) {
          if (samples.length) { samples.length = 0; start = 0; liveBpm = 0; bpmEl.textContent = '--'; }
          msg.textContent = 'Cover the camera fully with your fingertip…'; msg.className = 'nx-ppg-msg warn';
          prog.style.width = '0%';
          return;
        }
        if (!start) { start = now; msg.textContent = 'Finding your pulse… keep still, press lightly.'; msg.className = 'nx-ppg-msg'; }
        samples.push({ t: now, r, g: gg });
        prog.style.width = `${Math.min(100, ((now - start) / DURATION) * 100)}%`;
        // live waveform + estimate about once a second, from the last 12 s
        if (now - lastEst > 1000 && now - start > 6000) {
          lastEst = now;
          const est = pulseRate(samples.filter((q) => q.t > now - 12000));
          if (est) {
            const tail = est.sig.slice(-240), max = Math.max(...tail.map(Math.abs), 0.001);
            drawSpark(wave, [{ data: tail.map((v) => v / max + 1), max: 2, color: '#ff5a6a', fill: 'rgba(255,90,106,.08)' }]);
            if (est.quality >= GOOD) {
              liveBpm = Math.round(est.bpm);
              bpmEl.textContent = liveBpm;
              msg.textContent = 'Got it - keep still…'; msg.className = 'nx-ppg-msg';
            } else {
              bpmEl.textContent = '--';
              msg.textContent = 'No clear pulse yet - keep your finger still and press lightly (not hard).'; msg.className = 'nx-ppg-msg warn';
            }
          }
        }
        if (liveBpm && now - lastBeat > 60000 / liveBpm) { lastBeat = now; bpmEl.parentElement.classList.remove('beat'); void bpmEl.offsetWidth; bpmEl.parentElement.classList.add('beat'); }
        if (now - start >= DURATION) { stopCurrent(); finish(); }
      }
      next();

      async function finish() {
        const est = pulseRate(samples);
        if (!est || est.quality < GOOD * 0.8) {
          bpmEl.textContent = '--';
          msg.textContent = "Couldn't find a clear pulse. Try again: cover the camera AND the flash fully, press lightly, rest your hand on something and stay still."; msg.className = 'nx-ppg-msg warn';
          result.replaceChildren(button('TRY AGAIN', heart, 'solid'));
          return;
        }
        const bpm = Math.round(est.bpm), sig = est.sig, period = (30 * 60) / est.bpm;
        // beats: peaks at least 0.6 of a period apart -> intervals -> RMSSD
        const minGap = Math.round(period * 0.6), peaks = [];
        for (let i = 2; i < sig.length - 2; i++) {
          if (sig[i] > 0 && sig[i] >= sig[i - 1] && sig[i] >= sig[i + 1] && sig[i] >= sig[i - 2] && sig[i] >= sig[i + 2]) {
            if (!peaks.length || i - peaks[peaks.length - 1] >= minGap) peaks.push(i);
            else if (sig[i] > sig[peaks[peaks.length - 1]]) peaks[peaks.length - 1] = i;
          }
        }
        const ibi = peaks.slice(1).map((v, i) => ((v - peaks[i]) * 1000) / 30).filter((v) => v > 300 && v < 2000);
        const med = 60000 / est.bpm;
        const clean = ibi.filter((v) => Math.abs(v - med) < med * 0.25);
        let rmssd = 0;
        for (let i = 1; i < clean.length; i++) rmssd += (clean[i] - clean[i - 1]) ** 2;
        rmssd = clean.length > 8 ? Math.sqrt(rmssd / (clean.length - 1)) : 0;
        const stress = !rmssd ? 'unknown' : rmssd > 50 ? 'low' : rmssd > 25 ? 'moderate' : 'elevated';
        const quality = est.quality > 60 ? 'good' : est.quality > 25 ? 'fair' : 'weak';
        bpmEl.textContent = bpm;
        msg.textContent = 'Scan complete.'; msg.className = 'nx-ppg-msg ok';
        haptics.success();
        result.replaceChildren(h('div', { class: 'nx-stats' },
          h('div', {}, el('span', null, 'HEART RATE'), el('b', null, `${bpm}`), el('em', null, 'bpm')),
          h('div', {}, el('span', null, 'HRV (RMSSD)'), el('b', null, rmssd ? `${Math.round(rmssd)}` : '--'), el('em', null, 'ms')),
          h('div', {}, el('span', null, 'STRESS'), el('b', { class: `st-${stress}` }, stress.toUpperCase()), el('em', null, 'estimate')),
          h('div', {}, el('span', null, 'SIGNAL'), el('b', null, quality.toUpperCase()), el('em', null, `${clean.length} beats · ${est.channel === 'g' ? 'green' : 'red'}`))),
        el('p', 'nx-note', bpm < 50 || bpm > 120 ? 'That reading is outside the usual resting range (60-100). Rest a minute and measure again; if it stays like this or you feel unwell, talk to a doctor.' : 'Typical resting heart rate is 60-100 bpm. Higher HRV usually means more relaxed.'),
        h('div', { class: 'row-btns' }, button('AGAIN', heart), button('DONE', home, 'solid')));
        ctx.api.nexus('bio', { kind: 'heart', value: bpm, extra: { rmssd: Math.round(rmssd), stress, quality } }).catch(() => {});
        say(ctx, `Your heart rate is ${bpm} beats per minute${stress !== 'unknown' ? `, and your stress level looks ${stress}` : ''}.`);
      }
    }

    // ---------------------------------------------------------------- BREATH (accelerometer)
    async function breath() {
      if (!(await requestMotion())) { toast('Motion', 'Motion access is needed - allow it in Settings → Safari → Motion & Orientation Access.', 'error'); return; }
      wrap.innerHTML = '';
      const wave = h('canvas', { class: 'nx-ppg' });
      const big = el('div', 'nx-bpm', '--');
      const msg = el('div', 'nx-ppg-msg', 'Lie on your back, rest the phone flat on your belly, breathe normally. Starting in 5…');
      const prog = el('i');
      const result = h('div');
      wrap.append(...header('BREATH SCAN'), h('div', { class: 'nx-breath-orb' }, big, el('small', null, 'BREATHS / MIN')), wave, h('div', { class: 'bar' }, prog), msg, result);
      const samples = [], DURATION = 45000;
      let start = 0, alive = true, countdown = 5;
      const release = keepAwake();
      const cd = setInterval(() => {
        countdown--;
        if (countdown > 0) msg.textContent = `Lie on your back, rest the phone flat on your belly, breathe normally. Starting in ${countdown}…`;
        else { clearInterval(cd); start = performance.now(); msg.textContent = 'Measuring… breathe normally, stay still.'; haptics.tap(); }
      }, 1000);
      const stopMotion = onMotion((m) => {
        if (!alive || !start) return;
        const now = performance.now();
        samples.push({ t: now, v: m.gz });
        prog.style.width = `${Math.min(100, ((now - start) / DURATION) * 100)}%`;
        if (samples.length % 30 === 0 && samples.length > 120) {
          const x = bandpass(resample(samples.slice(-1200), 20), 10, 160);
          const mx = Math.max(...x.map(Math.abs), 0.0005);
          drawSpark(wave, [{ data: x.map((v) => v / mx + 1), max: 2, color: '#3ad6ff', fill: 'rgba(58,214,255,.08)' }]);
          const p = bestPeriod(x, 30, 200);   // 6-40 breaths/min at 20 Hz
          if (p.strength > 0.3) big.textContent = Math.round((60 * 20) / p.lag);
        }
        if (now - start >= DURATION) { stopCurrent(); finish(); }
      });
      stopCurrent = () => { alive = false; clearInterval(cd); stopMotion(); release(); };

      function finish() {
        const x = bandpass(resample(samples, 20), 10, 160);
        const p = bestPeriod(x, 30, 200);
        if (p.strength < 0.25) {
          msg.textContent = "Couldn't pick up your breathing - make sure the phone rests on your belly (not your hand) and try again."; msg.className = 'nx-ppg-msg warn';
          result.replaceChildren(button('TRY AGAIN', breath, 'solid'));
          return;
        }
        const rate = Math.round((60 * 20) / p.lag);
        big.textContent = rate; haptics.success();
        msg.textContent = 'Scan complete.'; msg.className = 'nx-ppg-msg ok';
        result.replaceChildren(el('p', 'nx-note', rate >= 12 && rate <= 20 ? 'Normal adult resting range is 12-20 breaths per minute - you\'re right in it.'
          : 'Normal adult resting range is 12-20 per minute. Measure again after resting; if it stays outside that and you feel unwell, see a doctor.'),
        h('div', { class: 'row-btns' }, button('AGAIN', breath), button('DONE', home, 'solid')));
        ctx.api.nexus('bio', { kind: 'breath', value: rate, extra: { quality: p.strength > 0.5 ? 'good' : 'fair' } }).catch(() => {});
        say(ctx, `You're breathing ${rate} times a minute.`);
      }
    }

    // ---------------------------------------------------------------- REFLEX
    function reflex() {
      wrap.innerHTML = '';
      const pad = h('button', { class: 'nx-reflex' }, el('b', null, 'TAP TO START'), el('span', null, 'Wait for green, then tap as fast as you can.'));
      const res = h('div');
      wrap.append(...header('REFLEX TEST'), pad, res);
      const times = [];
      let state = 'idle', shownAt = 0, timer = 0;
      stopCurrent = () => clearTimeout(timer);
      const show = (cls, a, b) => { pad.className = 'nx-reflex ' + cls; pad.firstChild.textContent = a; pad.lastChild.textContent = b; };
      pad.addEventListener('pointerdown', () => {
        if (state === 'idle' || state === 'result') {
          state = 'wait'; show('wait', 'WAIT…', 'for green');
          timer = setTimeout(() => { state = 'go'; shownAt = performance.now(); show('go', 'TAP!', ''); }, 1200 + Math.random() * 2500);
        } else if (state === 'wait') {
          clearTimeout(timer); state = 'idle'; show('early', 'TOO SOON', 'Tap to try again');
        } else if (state === 'go') {
          const ms = Math.round(performance.now() - shownAt);
          times.push(ms); haptics.tap();
          if (times.length >= 5) {
            const avg = Math.round(times.reduce((s, v) => s + v, 0) / times.length), best = Math.min(...times);
            state = 'done'; show('done', `${avg} ms`, `average · best ${best} ms`);
            res.replaceChildren(el('p', 'nx-note', avg < 250 ? 'Lightning reflexes.' : avg < 320 ? 'Solid - typical is 250-300 ms on a phone.' : 'A bit slow - tiredness, caffeine and focus all change this.'),
              h('div', { class: 'row-btns' }, button('AGAIN', reflex), button('DONE', home, 'solid')));
            ctx.api.nexus('bio', { kind: 'reaction', value: avg, extra: { best } }).catch(() => {});
          } else { state = 'result'; show('', `${ms} ms`, `${times.length}/5 · tap for the next one`); }
        }
      });
    }

    home();
  });
}
