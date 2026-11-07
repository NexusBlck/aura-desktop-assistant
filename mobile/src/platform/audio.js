// Microphone recording (push-to-talk) and audio playback (AURA's voice).
//
// iOS rules this works around:
//  * audio may only start playing after a tap - unlock() is called on the first touch, and the same
//    <audio> element is reused for every reply afterwards;
//  * MediaRecorder records audio/mp4 (AAC) on iPhone, webm elsewhere - both go straight to Whisper.

import * as speech from './speech.js';

const player = new Audio();
player.preload = 'auto';
player.setAttribute('playsinline', '');
let unlocked = false;
function silentWav() {
  // 0.05 s of silence, 8 kHz mono 16-bit - a valid file every browser can play
  const n = 400, buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
  const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  str(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); str(8, 'WAVE'); str(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, 8000, true);
  v.setUint32(28, 16000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, 'data'); v.setUint32(40, n * 2, true);
  return URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }));
}

// ONE audio engine for listening, switched on inside a tap. iOS keeps an AudioContext made later (e.g. after
// the "allow microphone?" prompt) paused forever - it then hears nothing - and pauses it again after
// playback, so every tap tries to wake it.
let engine = null;
export function audioEngine() {
  if (!engine) engine = new (window.AudioContext || window.webkitAudioContext)();
  return engine;
}
export function wakeEngine() {
  if (engine && engine.state !== 'running' && engine.state !== 'closed') engine.resume().catch(() => {});
}
document.addEventListener('touchend', () => { audioEngine(); wakeEngine(); }, { passive: true });
document.addEventListener('click', () => { audioEngine(); wakeEngine(); });

export function unlock() {
  if (unlocked) return;
  unlocked = true;
  speech.warm();
  try { audioEngine(); wakeEngine(); } catch (e) { /* no Web Audio */ }
  try {
    player.src = silentWav();
    const p = player.play();
    if (p && p.catch) p.catch(() => { unlocked = false; });
  } catch (e) { unlocked = false; }
}

// ---------------------------------------------------------------- playback queue
let queue = [];
let playing = false;
let stopped = 0;          // bumps on stop() so in-flight fetches are dropped
let onState = () => {};

export function onPlaybackState(fn) { onState = fn; }
export const isPlaying = () => playing;

/** Plays a list of async producers, one after another: each returns a Blob (or null), or
 *  { speak, lang } to use the phone's own voice (phone brain). The next one is fetched while the current one plays. */
export function playSequence(producers) {
  const run = stopped;
  const fetches = producers.map((p, i) => (i < 2 ? p() : null));
  queue.push({ run, producers, fetches });
  if (!playing) next();
}

async function next() {
  const job = queue[0];
  if (!job) { playing = false; onState(false); return; }
  playing = true; onState(true);
  for (let i = 0; i < job.producers.length; i++) {
    if (job.run !== stopped) break;
    if (!job.fetches[i]) job.fetches[i] = job.producers[i]();
    if (i + 1 < job.producers.length && !job.fetches[i + 1]) job.fetches[i + 1] = job.producers[i + 1]();
    let blob = null;
    try { blob = await job.fetches[i]; } catch (e) { blob = null; }
    if (!blob || job.run !== stopped) continue;
    await playBlob(blob);
  }
  queue.shift();
  next();
}

function playBlob(blob) {
  if (blob && blob.speak) return speech.speak(blob.speak, blob.lang);
  return new Promise((resolve) => {
    const url = URL.createObjectURL(blob);
    const done = () => { player.onended = player.onerror = player.onpause = null; URL.revokeObjectURL(url); resolve(); };
    player.onended = done; player.onerror = done; player.onpause = done;
    player.src = url;
    const p = player.play();
    if (p && p.catch) p.catch(done);
  });
}

export function stop() {
  stopped++;
  queue = [];
  speech.cancel();
  try { player.pause(); } catch (e) { /* ignore */ }
  playing = false; onState(false);
}

// ---------------------------------------------------------------- recording
export const canRecord = () => !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);

function pickType() {
  for (const t of ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm', 'audio/ogg']) {
    if (window.MediaRecorder && MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t)) return t;
  }
  return '';
}

/** Starts recording. Resolves to a controller: { stop(), cancel(), done: Promise<Blob|null> }.
 *  Stops by itself after ~1.6 s of silence once you've spoken, or after maxSeconds. */
export async function record({ maxSeconds = 20, onLevel = () => {} } = {}) {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  const type = pickType();
  const rec = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
  const chunks = [];
  let cancelled = false;
  rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };

  // simple voice-activity detection for auto-stop
  let ctx = null, raf = 0;
  try {
    ctx = audioEngine(); wakeEngine();          // the shared engine (iOS: one woken by a tap)
    var vadSrc = ctx.createMediaStreamSource(stream);
    const an = ctx.createAnalyser(); an.fftSize = 1024; vadSrc.connect(an);
    const buf = new Uint8Array(an.fftSize);
    let spoke = false, quietSince = 0;
    const t0 = performance.now();
    const loop = () => {
      an.getByteTimeDomainData(buf);
      let sum = 0; for (let i = 0; i < buf.length; i++) { const v = (buf[i] - 128) / 128; sum += v * v; }
      const rms = Math.sqrt(sum / buf.length);
      onLevel(Math.min(1, rms * 6));
      const now = performance.now();
      if (rms > 0.04) { spoke = true; quietSince = 0; } else if (spoke) { quietSince = quietSince || now; }
      if ((spoke && quietSince && now - quietSince > 1600) || now - t0 > maxSeconds * 1000) { ctrl.stop(); return; }
      if (!spoke && now - t0 > 8000) { ctrl.stop(); return; }      // never said anything
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
  } catch (e) { /* no analyser - manual stop / max time still work */ }

  const done = new Promise((resolve) => {
    rec.onstop = () => {
      cancelAnimationFrame(raf);
      stream.getTracks().forEach((t) => t.stop());
      try { vadSrc && vadSrc.disconnect(); } catch (e) { /* ignore */ }
      resolve(cancelled || !chunks.length ? null : new Blob(chunks, { type: rec.mimeType || type || 'audio/mp4' }));
    };
  });
  const ctrl = {
    stop() { if (rec.state !== 'inactive') rec.stop(); },
    cancel() { cancelled = true; if (rec.state !== 'inactive') rec.stop(); },
    done,
  };
  rec.start();
  setTimeout(() => ctrl.stop(), (maxSeconds + 1) * 1000);
  return ctrl;
}

/** Plays one clip right now (stopping anything else) and resolves when it has finished. */
export async function playOnce(blob) {
  stop();
  const run = stopped;
  playing = true; onState(true);
  await playBlob(blob);
  if (run === stopped) { playing = false; onState(false); if (queue.length) next(); }    // replies that arrived meanwhile
}

// ---------------------------------------------------------------- continuous listening (NEXUS)
// Keeps the microphone open and hands over each thing you say as a 16 kHz WAV clip - with ~0.3 s of
// audio from just before you started, so the first word isn't cut (MediaRecorder can't do that).
//   continuous: false  -> one clip per utterance (voice activity detection)
//   continuous: true   -> back-to-back clips of ~minSeconds..maxSeconds, cut in a pause (lectures)

function encodeWav(chunks, rate, outRate = 16000) {
  let n = 0;
  for (const c of chunks) n += c.length;
  const ratio = rate / outRate, outLen = Math.floor(n / ratio);
  const buf = new ArrayBuffer(44 + outLen * 2), v = new DataView(buf);
  const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  str(0, 'RIFF'); v.setUint32(4, 36 + outLen * 2, true); str(8, 'WAVE'); str(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, outRate, true);
  v.setUint32(28, outRate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, 'data'); v.setUint32(40, outLen * 2, true);
  const all = new Float32Array(n);
  let o = 0;
  for (const c of chunks) { all.set(c, o); o += c.length; }
  // average each group of input samples (a simple low-pass) while downsampling
  for (let i = 0; i < outLen; i++) {
    const a = Math.floor(i * ratio), b = Math.min(n, Math.floor((i + 1) * ratio));
    let s = 0; for (let j = a; j < b; j++) s += all[j];
    const x = Math.max(-1, Math.min(1, s / Math.max(1, b - a)));
    v.setInt16(44 + i * 2, x < 0 ? x * 0x8000 : x * 0x7fff, true);
  }
  return new Blob([buf], { type: 'audio/wav' });
}

export async function listen({ onUtterance, onLevel = () => {}, onSpeech = () => {}, onBlocked = () => {}, continuous = false,
  threshold = 0.025, silenceMs = 850, minVoicedMs = 300, maxSeconds = 14, minSeconds = 20 } = {}) {
  const ac = audioEngine();          // made (and woken) by the tap that opened this screen
  wakeEngine();
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  wakeEngine();
  const src = ac.createMediaStreamSource(stream);
  const proc = ac.createScriptProcessor(4096, 1, 1);
  const rate = ac.sampleRate;
  let paused = false, speaking = false, chunks = [], pre = [], preMs = 0, voiced = 0, silent = 0, total = 0, noise = 0.008;

  const finish = () => {
    const c = chunks, dur = total / 1000, enough = voiced >= minVoicedMs;
    speaking = false; chunks = []; pre = []; preMs = 0; voiced = 0; silent = 0; total = 0;
    onSpeech(false);
    if (enough && c.length) onUtterance(encodeWav(c, rate), dur);
  };

  // iOS: if no sound arrives, the engine is paused - say so (the screen asks for a tap) and keep trying to wake it
  let lastAudio = 0, alive = true;
  const watchdog = setInterval(() => {
    if (!alive) return;
    const blocked = ac.state !== 'running' || performance.now() - lastAudio > 1500;
    onBlocked(blocked);
    if (blocked) wakeEngine();
  }, 1000);

  proc.onaudioprocess = (e) => {
    lastAudio = performance.now();
    e.outputBuffer.getChannelData(0).fill(0);
    const d = new Float32Array(e.inputBuffer.getChannelData(0));
    const ms = (d.length / rate) * 1000;
    let sum = 0; for (let i = 0; i < d.length; i++) sum += d[i] * d[i];
    const rms = Math.sqrt(sum / d.length);
    onLevel(Math.min(1, rms * 8));
    if (paused) return;
    const loud = rms > Math.max(threshold, noise * 3);
    if (!loud) noise = noise * 0.97 + rms * 0.03;
    if (continuous) {
      if (!speaking) { speaking = true; onSpeech(true); }
      chunks.push(d); total += ms;
      if (loud) { voiced += ms; silent = 0; } else silent += ms;
      if ((total >= minSeconds * 1000 && silent >= 250) || total >= maxSeconds * 1000) finish();
      return;
    }
    if (!speaking) {
      pre.push(d); preMs += ms;
      while (preMs > 320 && pre.length > 1) { preMs -= (pre[0].length / rate) * 1000; pre.shift(); }
      if (loud) { speaking = true; chunks = pre.slice(); total = preMs; voiced = ms; silent = 0; pre = []; preMs = 0; onSpeech(true); }
      return;
    }
    chunks.push(d); total += ms;
    if (loud) { voiced += ms; silent = 0; } else silent += ms;
    if (silent >= silenceMs || total >= maxSeconds * 1000) finish();
  };
  src.connect(proc); proc.connect(ac.destination);

  return {
    /** Stops handing over clips (e.g. while AURA is talking) - what's heard meanwhile is dropped. */
    pause() { paused = true; speaking = false; chunks = []; pre = []; preMs = 0; voiced = 0; total = 0; },
    resume() { paused = false; wakeEngine(); },        // iOS may have paused the engine while AURA was speaking
    get paused() { return paused; },
    /** Hands over whatever has been recorded so far (end of a lecture). */
    flush() { if (chunks.length) finish(); },
    stop() {
      paused = true; alive = false;
      clearInterval(watchdog);
      try { proc.disconnect(); src.disconnect(); } catch (e) { /* ignore */ }
      stream.getTracks().forEach((t) => t.stop());
      // the shared engine stays on for the next screen
    },
  };
}
