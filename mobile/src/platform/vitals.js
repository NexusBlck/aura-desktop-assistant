// This phone's vitals - only what the phone really lets a web app measure (nothing guessed or faked):
//   load     how busy the phone is running AURA (main-thread load from timer lag), %
//   fps      screen frames per second right now (smoothness); maxFps = the screen's best seen
//   ping     round trip to your PC (or to the internet when the PC is off), ms
//   traffic  data AURA downloaded / uploaded, per second
//   storage  space AURA uses on the phone (and the space iOS allows it)
//   device   model (from the screen - iPhones are identified by it), iOS version, cores, screen
//   battery  only where the browser shares it (Android / desktop) - iOS never does
// iOS does NOT give apps the phone's total RAM in use, battery, temperature or other apps - the UI says so.
const listeners = new Set();
const HISTORY = 60;
const v = {
  load: 0, fps: 0, maxFps: 60, ping: null, pingTarget: '', down: 0, up: 0, online: navigator.onLine,
  loadHistory: [], fpsHistory: [], downHistory: [], upHistory: [],
  storage: null, battery: null, startedAt: Date.now(), device: null,
};
let started = false;

export const get = () => v;
export function on(fn) { listeners.add(fn); return () => listeners.delete(fn); }
const emit = () => listeners.forEach((fn) => { try { fn(v); } catch (e) { /* ignore */ } });
const push = (arr, x) => { arr.push(x); if (arr.length > HISTORY) arr.shift(); };

// ---------------------------------------------------------------- which phone this is
// iPhones report the same user agent, but each screen size belongs to a few models.
const IPHONES = [
  [440, 956, 3, 'iPhone 16 Pro Max', 'A18 Pro', 8], [402, 874, 3, 'iPhone 16 Pro', 'A18 Pro', 8],
  [430, 932, 3, 'iPhone 14 Pro Max / 15 Plus / 15 Pro Max / 16 Plus', 'A16 / A17 Pro / A18', 6],
  [393, 852, 3, 'iPhone 14 Pro / 15 / 15 Pro / 16', 'A16 / A17 Pro / A18', 6],
  [428, 926, 3, 'iPhone 12 Pro Max / 13 Pro Max / 14 Plus', 'A14 / A15', 6], [390, 844, 3, 'iPhone 12 / 13 / 14 (+ Pro)', 'A14 / A15', 4],
  [375, 812, 3, 'iPhone X / XS / 11 Pro / 12-13 mini', 'A11-A15', 4], [414, 896, 3, 'iPhone XS Max / 11 Pro Max', 'A12 / A13', 4],
  [414, 896, 2, 'iPhone XR / 11', 'A12 / A13', 4], [375, 667, 2, 'iPhone 6-8 / SE (2nd-3rd gen)', 'A9-A15', 3],
];
// iPads (iPadOS calls itself a Mac in the user agent, so these are matched by screen size + touch)
const IPADS = [
  [1024, 1366, 2, 'iPad Pro 12.9" (3rd gen) / (4th gen) / (5th gen) / (6th gen)', 'A12X / A12Z / M1 / M2', 4],
  [834, 1194, 2, 'iPad Pro 11" (1st-4th gen)', 'A12X / A12Z / M1 / M2', 4], [1032, 1376, 2, 'iPad Pro 13" (M4)', 'M4', 8],
  [834, 1210, 2, 'iPad Pro 11" (M4)', 'M4', 8], [820, 1180, 2, 'iPad Air (4th/5th gen) / iPad (10th gen)', 'A14 / M1', 4],
  [834, 1112, 2, 'iPad Air (3rd gen) / iPad Pro 10.5"', 'A12 / A10X', 3], [810, 1080, 2, 'iPad (7th-9th gen)', 'A10-A13', 3],
  [744, 1133, 2, 'iPad mini (6th gen)', 'A15', 4], [768, 1024, 2, 'iPad (older) / iPad mini', 'A-series', 2],
];
const MODEL_KEY = 'aura.phoneModel';
export const KNOWN = {        // exact specs, for when you pick your model (Settings or SYSTEM)
  'iPhone 14 Pro Max': { chip: 'A16 Bionic', cpu: '6-core (2 performance + 4 efficiency)', gpu: '5-core', ram: 6, battery: '4,323 mAh', screen: '6.7" ProMotion 120 Hz' },
  'iPhone 15 Pro Max': { chip: 'A17 Pro', cpu: '6-core (2P + 4E)', gpu: '6-core', ram: 8, battery: '4,441 mAh', screen: '6.7" ProMotion 120 Hz' },
  'iPhone 15 Plus': { chip: 'A16 Bionic', cpu: '6-core (2P + 4E)', gpu: '5-core', ram: 6, battery: '4,383 mAh', screen: '6.7" 60 Hz' },
  'iPhone 16 Plus': { chip: 'A18', cpu: '6-core (2P + 4E)', gpu: '4-core', ram: 8, battery: '4,674 mAh', screen: '6.7" 60 Hz' },
  'iPad Pro 12.9" (3rd gen)': { chip: 'A12X Bionic', cpu: '8-core (4 performance + 4 efficiency)', gpu: '7-core', ram: 4, battery: '9,720 mAh (36.7 Wh)', screen: '12.9" Liquid Retina ProMotion 120 Hz' },
  'iPad Pro 12.9" (4th gen)': { chip: 'A12Z Bionic', cpu: '8-core (4P + 4E)', gpu: '8-core', ram: 6, battery: '9,720 mAh (36.7 Wh)', screen: '12.9" Liquid Retina ProMotion 120 Hz' },
  'iPad Pro 12.9" (5th gen)': { chip: 'Apple M1', cpu: '8-core (4P + 4E)', gpu: '8-core', ram: 8, battery: '10,758 mAh (40.9 Wh)', screen: '12.9" Liquid Retina XDR 120 Hz' },
  'iPad Pro 12.9" (6th gen)': { chip: 'Apple M2', cpu: '8-core (4P + 4E)', gpu: '10-core', ram: 8, battery: '10,758 mAh (40.9 Wh)', screen: '12.9" Liquid Retina XDR 120 Hz' },
};
export const pickedModel = () => { try { return localStorage.getItem(MODEL_KEY) || ''; } catch (e) { return ''; } };
export function pickModel(name) { try { name ? localStorage.setItem(MODEL_KEY, name) : localStorage.removeItem(MODEL_KEY); } catch (e) { /* ignore */ } v.device = device(); emit(); }

function device() {
  const ua = navigator.userAgent;
  const ios = (/OS (\d+)[._](\d+)/.exec(ua) || []).slice(1).join('.');
  const w = Math.min(screen.width, screen.height), hh = Math.max(screen.width, screen.height), dpr = Math.round(window.devicePixelRatio || 1);
  const iphone = /iPhone/.test(ua);
  const ipad = /iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 0);
  const match = iphone ? IPHONES.find((m) => m[0] === w && m[1] === hh && m[2] === dpr)
    : ipad ? IPADS.find((m) => m[0] === w && m[1] === hh && m[2] === dpr) : null;
  const safariVersion = (/Version\/(\d+(?:\.\d+)?)/.exec(ua) || [])[1];
  const picked = pickedModel();
  const android = (/Android [\d.]+; ([^;)]+)/.exec(ua) || [])[1];
  return {
    kind: ipad ? 'iPad' : iphone ? 'iPhone' : 'device',
    model: picked || (match ? match[3] : iphone ? 'iPhone' : ipad ? 'iPad' : android || 'This device'),
    guessed: !picked && !!match && match[3].includes('/'),
    candidates: !match ? [] : ipad
      ? match[3].split(' / ').map((m, i, all) => (m.startsWith('(') ? all[0].replace(/\s*\(.*$/, '') + ' ' + m : m))
      : match[3].split(' / ').map((m) => (m.startsWith('iPhone') ? m : 'iPhone ' + m)),
    specs: KNOWN[picked] || null, chip: match ? match[4] : '', ramGb: (KNOWN[picked] || {}).ram || (match ? match[5] : null),
    os: iphone ? `iOS ${ios || '?'}` : ipad ? `iPadOS ${ios || safariVersion || ''}`.trim() : (/Android [\d.]+/.exec(ua) || ['?'])[0],
    cores: navigator.hardwareConcurrency || null,
    screen: `${w}×${hh} pt · ${w * dpr}×${hh * dpr} px`, dpr,
    standalone: window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true,
    lang: navigator.language, tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
  };
}

// ---------------------------------------------------------------- live measurements
function startLoad() {
  // a 100 ms timer that fires late means the phone was busy: lag / elapsed = load
  let last = performance.now(), acc = 0, accLag = 0;
  setInterval(() => {
    const now = performance.now(), elapsed = now - last, lag = Math.max(0, elapsed - 100);
    last = now; acc += elapsed; accLag += lag;
    if (acc >= 1000) {
      v.load = Math.min(100, Math.round((accLag / acc) * 100 * 1.6));      // ×1.6: timer lag under-counts short bursts
      acc = 0; accLag = 0;
    }
  }, 100);
}

function startFps() {
  let frames = 0, t0 = performance.now();
  const loop = (t) => {
    frames++;
    if (t - t0 >= 1000) {
      v.fps = Math.round((frames * 1000) / (t - t0));
      if (document.visibilityState === 'visible') v.maxFps = Math.max(v.maxFps, v.fps > 100 ? 120 : v.fps > 70 ? 90 : 60);
      frames = 0; t0 = t;
    }
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}

let seenBytes = 0, seenUp = 0;
/** Uploads the app makes are counted by api.js through this (browsers don't report sent bytes). */
export function countUpload(bytes) { seenUp += bytes || 0; }
function startTraffic() {
  try {
    new PerformanceObserver((list) => { for (const e of list.getEntries()) seenBytes += e.transferSize || e.encodedBodySize || 0; })
      .observe({ type: 'resource', buffered: false });
  } catch (e) { /* not supported */ }
}

async function ping() {
  const t0 = performance.now();
  try {
    // the PC when it's reachable; otherwise the internet (the AI service the phone brain uses)
    const pcUp = document.querySelector('.pill') && !document.querySelector('.pill.offline') && !document.querySelector('.pill.phone');
    if (pcUp) { await fetch('/api/hello', { cache: 'no-store' }); v.pingTarget = 'PC'; } else { await fetch('https://api.groq.com/openai/v1/models', { method: 'HEAD', cache: 'no-store' }); v.pingTarget = 'INTERNET'; }
    v.ping = Math.round(performance.now() - t0);
  } catch (e) { v.ping = null; v.pingTarget = 'NO NETWORK'; }
}

async function storage() {
  try {
    const est = navigator.storage && navigator.storage.estimate ? await navigator.storage.estimate() : null;
    let local = 0;
    try { for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); local += (k.length + (localStorage.getItem(k) || '').length) * 2; } } catch (e) { /* ignore */ }
    v.storage = { used: est ? est.usage : local, quota: est ? est.quota : null, local };
  } catch (e) { v.storage = null; }
}

async function battery() {
  if (!navigator.getBattery) return;
  try {
    const b = await navigator.getBattery();
    const set = () => { v.battery = { level: Math.round(b.level * 100), charging: b.charging }; emit(); };
    b.addEventListener('levelchange', set); b.addEventListener('chargingchange', set); set();
  } catch (e) { /* ignore */ }
}

export function start() {
  if (started) return;
  started = true;
  v.device = device();
  startLoad(); startFps(); startTraffic(); battery(); storage(); ping();
  window.addEventListener('online', () => { v.online = true; emit(); });
  window.addEventListener('offline', () => { v.online = false; emit(); });
  let tick = 0;
  setInterval(() => {
    tick++;
    v.down = seenBytes / 2; v.up = seenUp / 2; seenBytes = 0; seenUp = 0;      // bytes per second over the last 2 s
    push(v.loadHistory, v.load); push(v.fpsHistory, v.fps); push(v.downHistory, v.down); push(v.upHistory, v.up);
    if (tick % 5 === 0 && document.visibilityState === 'visible') ping();
    if (tick % 30 === 0) storage();
    emit();
  }, 2000);
}
