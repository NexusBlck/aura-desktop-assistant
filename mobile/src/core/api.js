// Talking to AURA on the PC (phone_bridge.py). Platform-independent.
import * as storage from '../platform/storage.js';
import { countUpload } from '../platform/vitals.js';

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

let token = storage.get('token');
let onUnauthorized = () => {};

export const isPaired = () => !!token;
export function setToken(t) { token = t; t ? storage.set('token', t) : storage.remove('token'); }
export function onLostPairing(fn) { onUnauthorized = fn; }
export const authHeader = () => (token ? { Authorization: `Bearer ${token}` } : {});

// The phone brain (src/brain/local.js): while the PC is unreachable, requests are answered on the phone.
const NEVER_LOCAL = /^(hello|pair|events|state|brain\/|client-log|push\/|devices|watch\/)/;
const CACHE = new Set(['emails', 'vision/memory', 'nexus/protocols', 'nexus/bio', 'nexus/echo']);   // kept for when the PC is off
let localRoute = null, localMode = false;
export function useLocal(fn) { localRoute = fn; }
const waiting = new Set();     // requests to the PC still in the air
export function setLocalMode(on) {
  localMode = !!on;
  if (localMode) waiting.forEach((c) => c.abort());       // stuck on an unreachable PC: hand them to the phone brain now
}
export const isLocalMode = () => localMode;
async function runLocal(method, path, body, raw) {
  try { return await localRoute(method, path, body, raw); } catch (e) { throw new ApiError(e.message || 'Something went wrong on the phone.', e.status || 503); }
}

/** fetch with auth, timeout and readable errors. Returns parsed JSON (or the Response for raw). */
export async function request(path, { method = 'GET', body, raw = false, timeout = 30000, headers = {} } = {}) {
  const canLocal = localRoute && !NEVER_LOCAL.test(path);
  if (localMode && canLocal) return runLocal(method, path, body, raw);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  if (canLocal) waiting.add(ctrl);
  const opts = { method, headers: { ...authHeader(), ...headers }, signal: ctrl.signal, cache: 'no-store' };
  if (body !== undefined) {
    if (body instanceof Blob || body instanceof ArrayBuffer) {
      opts.body = body;
    } else {
      opts.body = JSON.stringify(body);
      opts.headers['Content-Type'] = 'application/json';
    }
  }
  if (opts.body) countUpload(opts.body.size || opts.body.byteLength || opts.body.length || 0);    // SYSTEM's "AURA UP"
  let res;
  try {
    res = await fetch(`/api/${path}`, opts);
  } catch (e) {
    // the PC just went away (or it hung and AURA has meanwhile switched to the phone brain)
    if (canLocal && (e.name !== 'AbortError' || localMode)) return runLocal(method, path, body, raw);
    throw new ApiError(e.name === 'AbortError' ? 'Your PC took too long to answer.' : "Can't reach your PC.", 0);
  } finally {
    clearTimeout(timer);
    waiting.delete(ctrl);
  }
  if (res.status === 401) {
    setToken(null);
    onUnauthorized();
    throw new ApiError('This phone is no longer paired with your PC.', 401);
  }
  if (raw) {
    if (!res.ok) throw new ApiError(`Your PC answered with an error (${res.status}).`, res.status);
    return res;
  }
  let data = {};
  try { data = await res.json(); } catch (e) { /* empty body */ }
  if (!res.ok) throw new ApiError(data.error || `Your PC answered with an error (${res.status}).`, res.status);
  if (method === 'GET' && CACHE.has(path)) storage.set('brain.cache.' + path, data);
  return data;
}

export const get = (path, opts) => request(path, opts);
export const post = (path, body, opts = {}) => request(path, { ...opts, method: 'POST', body: body ?? {} });

// ---- named calls (one place that knows the PC's API)
export const api = {
  hello: () => get('hello', { timeout: 8000 }),
  requestCode: () => post('pair/request'),
  pair: (code, name) => post('pair', { code, name }),
  state: (timeout = 15000) => get('state', { timeout }),
  brainKit: () => get('brain/kit', { timeout: 45000 }),
  watchKey: () => post('watch/key'),
  // AURA OVERDRIVE (overdrive.py): status / aegis / recall / pc / air
  overdrive: (op, body) => (body === undefined ? get(`overdrive/${op}`, { timeout: 30000 }) : post(`overdrive/${op}`, body, { timeout: 90000 })),
  // AURA SPATIAL (spatial.py): resolve / job / command / explain / status / history / settings / cancel
  spatial: (op, body) => (body === undefined ? get(`spatial/${op}`, { timeout: 30000 }) : post(`spatial/${op}`, body, { timeout: 90000 })),
  // AURA STUDIO (studio.py)
  studio: (op, body) => (body === undefined ? get(`studio/${op}`, { timeout: 30000 }) : post(`studio/${op}`, body, { timeout: 60000 })),
  studioUpload: (file) => request(`studio/upload?name=${encodeURIComponent(file.name || 'upload')}`, { method: 'POST', body: file, headers: { 'Content-Type': file.type || 'application/octet-stream' }, timeout: 600000 }),
  command: (text) => post('command', { text }),
  voice: (blob) => request('voice', { method: 'POST', body: blob, headers: { 'Content-Type': blob.type || 'audio/mp4' }, timeout: 45000 }),
  tts: (text) => request('tts', { method: 'POST', body: { text }, raw: true, timeout: 30000 }).then((r) => r.blob()),
  addTask: (text) => post('tasks/add', { text }),
  completeTask: (text) => post('tasks/complete', { text }),
  notes: () => get('notes'),
  saveNotes: (text) => post('notes', { text }),
  addReminder: (text) => post('reminders/add', { text }, { timeout: 45000 }),
  cancelReminder: (id) => post('reminders/cancel', { id }),
  focus: (action, minutes, label) => post('focus', { action, minutes, label }),
  dnd: (on) => post('dnd', { on }),
  emails: () => get('emails'),
  dismissEmails: (ids) => post('emails/dismiss', { ids }),
  dismissAllEmails: () => post('emails/dismiss', { all: true }),
  dashboard: () => get('dashboard', { timeout: 20000 }),
  logSession: (type, timestamp, rating) => post('log-session', { type, timestamp, rating }),
  journal: (mood, good, mind) => post('journal', { mood, good, mind }),
  explain: (text) => post('explain', { text }, { timeout: 90000 }),
  followUp: (id, question) => post('explain/follow', { id, question }, { timeout: 90000 }),
  transcribe: (blob) => request('transcribe', { method: 'POST', body: blob, headers: { 'Content-Type': blob.type || 'audio/mp4' }, timeout: 45000 }),
  lab: (op, body) => post(`vision/${op}`, body, { timeout: 120000 }),
  memory: () => get('vision/memory', { timeout: 20000 }),
  appendNote: (title, text) => post('notes/append', { title, text }),
  addCalendar: (text) => post('calendar/add', { text }, { timeout: 60000 }),
  vision: (op, blob, hint = '') => request(`vision/${op}?hint=${encodeURIComponent(hint)}`, { method: 'POST', body: blob, headers: { 'Content-Type': blob.type || 'image/jpeg' }, timeout: 90000 }),
  files: (kind, path = '') => get(`files?kind=${encodeURIComponent(kind)}&path=${encodeURIComponent(path)}`),
  view: (kind, path) => post('view', { kind, path }),
  upload: (file) => request(`upload?name=${encodeURIComponent(file.name)}`, { method: 'POST', body: file, headers: { 'Content-Type': file.type || 'application/octet-stream' }, timeout: 120000 }),
  cdc: (op, body) => (body === undefined ? get(`cdc/${op}`, { timeout: 30000 }) : post(`cdc/${op}`, body, { timeout: 60000 })),
  pc: (action) => post('pc', { action }),
  devices: () => get('devices'),
  revoke: (id) => post('devices/revoke', { id }),
  pushKey: () => get('push/key'),
  pushSubscribe: (subscription) => post('push/subscribe', { subscription }),
  pushTest: () => post('push/test'),
  // NEXUS (nexus.py)
  nexus: (op, body, timeout = 90000) => (body === undefined ? get(`nexus/${op}`, { timeout: 30000 }) : post(`nexus/${op}`, body, { timeout })),
  screen: (w, q) => request(`nexus/screen?w=${w}&q=${q}`, { raw: true, timeout: 12000 }),
};
