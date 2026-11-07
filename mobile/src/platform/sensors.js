// Phone sensors for NEXUS: compass / tilt, motion, location, keeping the screen awake.
// iOS asks for motion permission once per app, and only from inside a tap - call requestMotion() from a click.

export async function requestMotion() {
  let ok = true;
  for (const E of [window.DeviceOrientationEvent, window.DeviceMotionEvent]) {
    if (E && typeof E.requestPermission === 'function') {
      try { ok = ok && (await E.requestPermission()) === 'granted'; } catch (e) { ok = false; }
    }
  }
  return ok && !!(window.DeviceOrientationEvent || window.DeviceMotionEvent);
}

/** fn({ heading, beta, gamma, accuracy }) - heading in degrees from north (null until the compass knows). */
export function onOrientation(fn) {
  const absolute = 'ondeviceorientationabsolute' in window;
  const name = absolute ? 'deviceorientationabsolute' : 'deviceorientation';
  const handler = (e) => {
    let heading = e.webkitCompassHeading;                              // iPhone: true compass, tilt-compensated
    if (heading == null && e.alpha != null && (absolute || e.absolute)) heading = (360 - e.alpha) % 360;   // Android
    fn({ heading: heading == null ? null : heading, beta: e.beta, gamma: e.gamma, accuracy: e.webkitCompassAccuracy });
  };
  window.addEventListener(name, handler);
  return () => window.removeEventListener(name, handler);
}

/** fn({ gx, gy, gz, ra, rb, rg, dt }) - acceleration with gravity (m/s²) and rotation rate (°/s). */
export function onMotion(fn) {
  const handler = (e) => {
    const g = e.accelerationIncludingGravity || {}, r = e.rotationRate || {};
    fn({ gx: g.x || 0, gy: g.y || 0, gz: g.z || 0, ra: r.alpha || 0, rb: r.beta || 0, rg: r.gamma || 0, dt: e.interval || 16 });
  };
  window.addEventListener('devicemotion', handler);
  return () => window.removeEventListener('devicemotion', handler);
}

/** Live GPS. fn({ lat, lon, acc, alt, speed, course, t }); returns a stop function. */
export function watchPosition(fn, onError = () => {}) {
  if (!navigator.geolocation) { onError(new Error('no geolocation')); return () => {}; }
  const id = navigator.geolocation.watchPosition((p) => {
    const c = p.coords;
    fn({ lat: c.latitude, lon: c.longitude, acc: c.accuracy, alt: c.altitude, speed: c.speed, course: c.heading, t: p.timestamp });
  }, onError, { enableHighAccuracy: true, maximumAge: 3000, timeout: 20000 });
  return () => navigator.geolocation.clearWatch(id);
}

/** Keeps the screen on while a NEXUS module runs (re-taken when the app comes back). Returns release(). */
export function keepAwake() {
  let lock = null, on = true;
  const take = async () => {
    if (!on || document.visibilityState !== 'visible' || !navigator.wakeLock) return;
    try { lock = await navigator.wakeLock.request('screen'); } catch (e) { lock = null; }
  };
  const vis = () => { if (document.visibilityState === 'visible') take(); };
  document.addEventListener('visibilitychange', vis);
  take();
  return () => { on = false; document.removeEventListener('visibilitychange', vis); if (lock) lock.release().catch(() => {}); };
}

// ---------------------------------------------------------------- geo maths
const rad = (d) => (d * Math.PI) / 180, deg = (r) => (r * 180) / Math.PI;

export function distBearing(lat1, lon1, lat2, lon2) {
  const p1 = rad(lat1), p2 = rad(lat2), dl = rad(lon2 - lon1);
  const a = Math.sin((p2 - p1) / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  const d = 2 * 6371000 * Math.asin(Math.sqrt(a));
  const y = Math.sin(dl) * Math.cos(p2), x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return { dist: d, bearing: (deg(Math.atan2(y, x)) + 360) % 360 };
}

export const cardinal = (b) => ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'][Math.round((((b % 360) + 360) % 360) / 22.5) % 16];

// Sun and moon positions (compact form of the standard astronomical formulas, as in SunCalc).
// Returns { az, alt } in degrees, azimuth from north.
const J1970 = 2440588, J2000 = 2451545, OBL = rad(23.4397);
const toDays = (date) => date.valueOf() / 86400000 - 0.5 + J1970 - J2000;
const rasc = (l, b) => Math.atan2(Math.sin(l) * Math.cos(OBL) - Math.tan(b) * Math.sin(OBL), Math.cos(l));
const decl = (l, b) => Math.asin(Math.sin(b) * Math.cos(OBL) + Math.cos(b) * Math.sin(OBL) * Math.sin(l));
const sidereal = (d, lw) => rad(280.16 + 360.9856235 * d) - lw;
function horiz(H, phi, dc) {
  const az = Math.atan2(Math.sin(H), Math.cos(H) * Math.sin(phi) - Math.tan(dc) * Math.cos(phi));
  const alt = Math.asin(Math.sin(phi) * Math.sin(dc) + Math.cos(phi) * Math.cos(dc) * Math.cos(H));
  return { az: (deg(az) + 180 + 360) % 360, alt: deg(alt) };
}
function sunCoords(d) {
  const M = rad(357.5291 + 0.98560028 * d);
  const C = rad(1.9148 * Math.sin(M) + 0.02 * Math.sin(2 * M) + 0.0003 * Math.sin(3 * M));
  const L = M + C + rad(102.9372) + Math.PI;
  return { ra: rasc(L, 0), dec: decl(L, 0) };
}
export function sunPosition(date, lat, lon) {
  const d = toDays(date), s = sunCoords(d);
  return horiz(sidereal(d, rad(-lon)) - s.ra, rad(lat), s.dec);
}
export function moonPosition(date, lat, lon) {
  const d = toDays(date);
  const L = rad(218.316 + 13.176396 * d), M = rad(134.963 + 13.064993 * d), F = rad(93.272 + 13.22935 * d);
  const l = L + rad(6.289) * Math.sin(M), b = rad(5.128) * Math.sin(F), dist = 385001 - 20905 * Math.cos(M);
  const mra = rasc(l, b), mdec = decl(l, b), s = sunCoords(d);
  const phi = Math.acos(Math.sin(s.dec) * Math.sin(mdec) + Math.cos(s.dec) * Math.cos(mdec) * Math.cos(s.ra - mra));
  const inc = Math.atan2(149598000 * Math.sin(phi), dist - 149598000 * Math.cos(phi));
  return { ...horiz(sidereal(d, rad(-lon)) - mra, rad(lat), mdec), lit: (1 + Math.cos(inc)) / 2 };
}
export const KAABA = { lat: 21.4225, lon: 39.8262 };
