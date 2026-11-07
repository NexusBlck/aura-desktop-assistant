// The phone brain's data + "local PC": while the PC is off, every request the app would send to the PC is
// answered here instead (api.js hands it over), using the phone's own copy of your tasks, reminders, notes,
// memory and recordings. Every change also goes into an OUTBOX that's replayed to the PC when it's back
// (phone_sync.apply), so nothing done on the phone is lost.
import * as storage from '../platform/storage.js';
import * as store from '../core/store.js';
import * as kit from './kit.js';
import * as skills from './skills.js';
import * as mind from './mind.js';
import { transcribe } from './llm.js';

export class LocalError extends Error {
  constructor(message, status = 503) { super(message); this.status = status; }
}
const NEEDS_PC = (what) => new LocalError(`${what} needs your PC, and it's off right now. Everything else works on the phone.`);
const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 8);

// ---------------------------------------------------------------- outbox
let outbox = storage.get('brain.outbox', []);
const saveOutbox = () => { storage.set('brain.outbox', outbox); store.set('brainPending', outbox.length); };
store.set('brainPending', outbox.length);

export function queue(type, data) {
  if (type === 'notes_set') {            // only the newest notes text matters, but keep the first base
    const old = outbox.find((o) => o.type === 'notes_set');
    if (old) { old.data.text = data.text; saveOutbox(); return; }
  }
  outbox.push({ id: uid(), type, data, t: Date.now() / 1000 });
  saveOutbox();
}
export const pending = () => outbox.length;

/** Sends everything waiting to the PC. post = api's POST (online). Returns how many went through. */
export async function flush(post) {
  let sent = 0;
  while (outbox.length) {
    const batch = outbox.slice(0, 200);
    const r = await post('brain/sync', { ops: batch });
    const done = new Set(r.done || []);
    if (!done.size) break;
    outbox = outbox.filter((o) => !done.has(o.id));
    saveOutbox();
    sent += done.size;
    if ((r.failed || []).length) break;
  }
  return sent;
}

// ---------------------------------------------------------------- the cached PC snapshot, edited locally
const state = () => store.get('state') || {};
function setState(patch) { store.set('state', { ...state(), ...patch }); store.persist(); }

export function addTask(text) {
  text = String(text || '').trim().slice(0, 200);
  if (!text) return false;
  setState({ tasks: [...(state().tasks || []), text] });
  queue('task_add', { text });
  return true;
}
export function completeTask(text) {
  const tasks = state().tasks || [], q = String(text || '').toLowerCase();
  const hit = tasks.find((t) => t.toLowerCase() === q) || tasks.find((t) => t.toLowerCase().includes(q) || q.includes(t.toLowerCase()));
  if (!hit) return false;
  setState({ tasks: tasks.filter((t) => t !== hit) });
  queue('task_done', { text: hit });
  return hit;
}
export function addReminder(text, due) {
  const d = new Date(due);
  if (!text || isNaN(d)) return null;
  const r = { id: 'p' + uid(), text: String(text).slice(0, 200), due: d.toISOString(), phone: true };
  setState({ reminders: [...(state().reminders || []), r].sort((a, b) => new Date(a.due) - new Date(b.due)) });
  queue('reminder_add', { text: r.text, due: r.due, local_id: r.id });
  return r;
}
export function cancelReminder(id) {
  const r = (state().reminders || []).find((x) => x.id === id);
  if (!r) return false;
  setState({ reminders: state().reminders.filter((x) => x.id !== id) });
  const queued = outbox.find((o) => o.type === 'reminder_add' && o.data.local_id === id);
  if (queued) { outbox = outbox.filter((o) => o !== queued); saveOutbox(); } else queue('reminder_cancel', { id, text: r.text });
  return true;
}
export function getNotes() { const n = storage.get('brain.notes', null); return n != null ? n : ((kit.get() || {}).notes || ''); }
export function saveNotes(text) {
  if (storage.get('brain.notes', null) == null) storage.set('brain.notesBase', (kit.get() || {}).notes || '');
  storage.set('brain.notes', text);
  queue('notes_set', { text, base: storage.get('brain.notesBase', '') });
}
/** The PC's notes came in fresh (back online, after syncing): the phone's copy follows the PC again. */
export function adoptPcNotes(text) { storage.remove('brain.notes'); storage.remove('brain.notesBase'); kit.patch({ notes: text }); }

export function addChat(role, text) {
  const entry = { role, text: String(text), t: Date.now() / 1000 };
  store.set('chat', [...(store.get('chat') || []), entry].slice(-120));
  store.persist();
  queue('chat', entry);
}

/** You corrected AURA on the phone: keep the lesson (and send it to the PC's lessons.py). */
export function lesson(note, said) {
  note = String(note || '').trim().slice(0, 300);
  if (!note) return;
  const k = kit.get();
  if (k && !(k.lessons || []).includes(note)) kit.patch({ lessons: [...(k.lessons || []), note].slice(-80) });
  queue('lesson', { text: note, said: String(said || '').slice(0, 200) });
}

export function remember(category, text) {
  kit.addMemory(category, text);
  queue('memory_add', { category, text });
}

// jobs that need the PC, kept until it's back (you decide whether to run them then)
export const pcJobs = () => storage.get('brain.pcjobs', []);
export function addPcJob(command) { storage.set('brain.pcjobs', [...pcJobs(), { command, t: Date.now() / 1000 }].slice(-20)); store.set('pcJobs', pcJobs().length); }
export function clearPcJobs() { storage.set('brain.pcjobs', []); store.set('pcJobs', 0); }
store.set('pcJobs', pcJobs().length);

// ---------------------------------------------------------------- reminders fire on the phone while the PC is off
const fired = new Set(storage.get('brain.fired', []));
export function checkReminders(onFire) {
  if (store.get('conn') !== 'offline') return;              // the PC handles them (and pushes a notification)
  const now = Date.now();
  for (const r of state().reminders || []) {
    if (fired.has(r.id) || new Date(r.due).getTime() > now) continue;
    fired.add(r.id);
    storage.set('brain.fired', [...fired].slice(-300));
    setState({ reminders: state().reminders.filter((x) => x.id !== r.id) });
    const queued = outbox.find((o) => o.type === 'reminder_add' && o.data.local_id === r.id);
    if (queued) { outbox = outbox.filter((o) => o !== queued); saveOutbox(); }        // already done - the PC needn't fire it again
    onFire(r);
  }
}

// ---------------------------------------------------------------- ECHO recordings kept on the phone
const echoIndex = () => storage.get('brain.echo.index', []);
const echoRec = (id) => storage.get('brain.echo.r.' + id, null);
function echoSave(rec) {
  storage.set('brain.echo.r.' + rec.id, rec);
  const words = rec.segments.reduce((n, s) => n + s.text.split(/\s+/).length, 0);
  const row = { id: rec.id, title: rec.title, kind: rec.kind, started: rec.started, words, duration: rec.segments.length ? rec.segments[rec.segments.length - 1].t : 0, done: !!rec.notes, local: true };
  storage.set('brain.echo.index', [row, ...echoIndex().filter((x) => x.id !== rec.id)].slice(0, 60));
}

// ---------------------------------------------------------------- protocols (run through the phone brain)
let protocolRun = null;
function protocolList() { return storage.get('brain.cache.nexus/protocols', null)?.items || (kit.get() || {}).protocols || []; }
async function runProtocol(name, steps) {
  if (protocolRun) return { error: 'A protocol is already running.' };
  const run = { name, stop: false };
  protocolRun = run;
  (async () => {
    store.set('protocol', { name, step: 0, total: steps.length, state: 'start' });
    for (let i = 0; i < steps.length; i++) {
      if (run.stop) { store.set('protocol', { name, step: i, total: steps.length, state: 'stopped' }); protocolRun = null; return; }
      store.set('protocol', { name, step: i + 1, total: steps.length, state: 'step', text: steps[i] });
      await mind.handle(steps[i]);
    }
    store.set('protocol', { name, step: steps.length, total: steps.length, state: 'done' });
    protocolRun = null;
  })();
  return { ok: true, total: steps.length };
}

// ---------------------------------------------------------------- the router
const fromB64 = (b64, type) => { const bin = atob(b64 || ''), u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); return new Blob([u], { type }); };
const blobB64 = (blob) => new Promise((res) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(',')[1] || ''); r.onerror = () => res(''); r.readAsDataURL(blob); });
export const cached = (path) => storage.get('brain.cache.' + path, null);

/** Answers one API request on the phone. Throws LocalError when it really needs the PC. */
export async function route(method, path, body, raw) {
  const [route, qs] = path.split('?');
  const query = new URLSearchParams(qs || '');
  const b = body && !(body instanceof Blob) ? body : {};

  if (route === 'tts') return { ok: true, blob: async () => ({ speak: b.text || '' }) };
  if (raw) throw NEEDS_PC('That');
  if (route === 'command') { mind.handle(String(b.text || '')); return { status: 'queued' }; }
  if (route === 'voice' || route === 'transcribe') {
    const text = await transcribe(body);
    if (route === 'voice' && text) mind.handle(text);
    return { text, status: text ? 'queued' : 'nothing' };
  }

  // ---- tasks, reminders, notes, journal, sessions
  if (route === 'tasks/add') return { ok: addTask(b.text) };
  if (route === 'tasks/complete') return { ok: !!completeTask(b.text) };
  if (route === 'notes') return method === 'GET' ? { text: getNotes() } : (saveNotes(String(b.text || '')), { ok: true });
  if (route === 'notes/append') { const add = `${b.title ? b.title + '\n' : ''}${b.text || ''}`; saveNotes((getNotes().trim() ? getNotes().trim() + '\n\n' : '') + add); return { ok: true }; }
  if (route === 'reminders/add') {
    const d = await mind.parseReminder(String(b.text || ''));
    if (!d.due) return { message: "When should I remind you? Say something like 'in 20 minutes' or 'at 6pm'." };
    addReminder(d.text, d.due);
    return { message: `Okay - I'll remind you to ${d.text} ${mind.describeDue(d.due)}.` };
  }
  if (route === 'reminders/cancel') return { ok: cancelReminder(b.id) };
  if (route === 'journal') { queue('journal', b); return { ok: true }; }
  if (route === 'log-session') { queue('log_session', b); return { ok: true, message: `Logged your ${b.type || 'session'} - it goes into your stats when your PC is back.` }; }
  if (route === 'calendar/add') { const d = await mind.parseReminder(String(b.text || '')); if (!d.due) return { error: "I couldn't find a date in that." }; addReminder(d.text, d.due); return { message: `Your PC is off, so I saved it as a reminder: ${d.text}, ${mind.describeDue(d.due)}.` }; }
  if (route === 'emails' && method === 'GET') return cached('emails') || { emails: [] };

  // ---- the explain cat
  if (route === 'explain') return skills.explain(String(b.text || ''));
  if (route === 'explain/follow') return skills.followUp(b.id, String(b.question || ''));

  // ---- Vision
  if (route === 'vision/identify' || route === 'vision/scan') {
    const img = await blobB64(body);
    if (route === 'vision/identify') return skills.identify(img, query.get('hint') || '');
    const r = await skills.observe(img);
    return { objects: (r.objects || []).map((o) => ({ label: o.label, x: o.x, y: o.y, w: o.w, h: o.h })) };
  }
  if (route === 'vision/memory') return cached('vision/memory') || { items: [] };
  if (route.startsWith('vision/')) {
    const op = route.slice(7);
    if (op === 'ask') return skills.visionAsk(b.image, b.question);
    if (op === 'read') return skills.visionRead(b.image, b.mode, b.lang);
    if (op === 'study') return skills.visionStudy(b.image, b.mode);
    if (op === 'observe') return skills.observe(b.image, b.focus);
    if (op === 'detective') return skills.detective(b.image, b.question);
    if (op === 'watch') return skills.watch(b.image, b.conditions);
    if (op === 'med') return skills.med(b.image, b.kind);
    if (op === 'compare') return skills.compare(b.a, b.b, b.question);
    if (op === 'memory/seen') return { ok: true };
    throw NEEDS_PC({ save: 'Saving to the PC', doc: 'Making a PDF', recall: 'RECALL', timeline: 'The timeline' }[op] || 'Changing what Vision remembers');
  }

  // ---- NEXUS
  if (route.startsWith('nexus/')) {
    const op = route.slice(6);
    if (op === 'live') return skills.liveTurn(b, fromB64(b.audio, b.mime || 'audio/wav'));
    if (op === 'babel') return skills.interpret(b, fromB64(b.audio, b.mime || 'audio/wav'));
    if (op === 'places') return skills.places(b);
    if (op === 'where') return skills.where(b);
    if (op === 'around') return skills.aroundAsk(b);
    if (op === 'mission') return skills.missionPlan(String(b.goal || ''));
    if (op === 'screen' || op === 'input' || op === 'clipboard') throw NEEDS_PC('LINK');
    if (op === 'bio') {
      const local = storage.get('brain.bio', []);
      if (method === 'GET') return { items: [...((cached('nexus/bio') || {}).items || []), ...local].sort((x, y) => x.t - y.t).slice(-300) };
      const entry = { kind: b.kind, t: Date.now() / 1000, value: Number(b.value) || 0, extra: b.extra || {} };
      storage.set('brain.bio', [...local, entry].slice(-300));
      queue('bio', b);
      return { ok: true };
    }
    if (op === 'protocols') return { items: protocolList(), running: protocolRun ? 'phone' : null };
    if (op === 'protocols/run') {
      if (b.steps) return runProtocol(b.name || 'Mission', b.steps);
      const p = protocolList().find((x) => x.id === b.id);
      return p ? runProtocol(p.name, p.steps) : { error: "That protocol wasn't found." };
    }
    if (op === 'protocols/stop') { if (protocolRun) protocolRun.stop = true; return { ok: true }; }
    if (op === 'protocols/save' || op === 'protocols/delete') {
      let items = protocolList();
      let item = null;
      if (op === 'protocols/save') {
        const name = String(b.name || '').trim().slice(0, 40), steps = (b.steps || []).map((s) => String(s).trim()).filter(Boolean).slice(0, 12);
        if (!name || !steps.length) return { error: 'A protocol needs a name and at least one step.' };
        item = { id: b.id || name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || uid(), name, icon: b.icon || 'bolt', steps };
        items = [...items.filter((p) => p.id !== item.id), item];
      } else items = items.filter((p) => p.id !== b.id);
      storage.set('brain.cache.nexus/protocols', { items });
      queue('protocols', { items });
      return { ok: true, protocol: item, items };
    }
    // ECHO
    if (op === 'echo' && method === 'GET') {
      const id = query.get('id');
      if (id) {
        const rec = echoRec(id);
        if (!rec) throw NEEDS_PC('Opening a recording made on the PC');
        return rec;
      }
      const pcList = ((cached('nexus/echo') || {}).items || []).filter((x) => !echoIndex().some((l) => l.id === x.id));
      return { items: [...echoIndex(), ...pcList].sort((x, y) => y.started - x.started) };
    }
    if (op === 'echo/start') {
      const kind = b.kind || 'lecture';
      const rec = { id: uid().slice(0, 12), title: (b.title || `${kind[0].toUpperCase() + kind.slice(1)} ${new Date().toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}`).slice(0, 80),
        kind, started: Date.now() / 1000, segments: [], notes: null };
      echoSave(rec);
      return { id: rec.id, title: rec.title };
    }
    if (op === 'echo/chunk') {
      const rec = echoRec(b.id);
      if (!rec) return { error: "That recording wasn't found." };
      const text = await transcribe(fromB64(b.audio, b.mime || 'audio/wav'), b.lang || undefined);
      if (text) { const fresh = echoRec(b.id) || rec; fresh.segments.push({ t: Number(b.t) || 0, text }); echoSave(fresh); }
      return { text };
    }
    if (op === 'echo/finish') {
      const rec = echoRec(b.id);
      if (!rec) throw NEEDS_PC('Writing notes for a recording made on the PC');
      if (!rec.segments.length) return { error: 'Nothing was heard in that recording.' };
      rec.notes = await skills.echoNotes(rec);
      if (rec.notes.title) rec.title = String(rec.notes.title).slice(0, 80);
      rec.ended = Date.now() / 1000;
      echoSave(rec);
      queue('echo_import', { rec });
      return rec;
    }
    if (op === 'echo/ask') {
      const recs = b.id ? [echoRec(b.id)] : echoIndex().map((x) => echoRec(x.id));
      const have = recs.filter((r) => r && r.segments && r.segments.length);
      if (!have.length) return { answer: b.id ? "That recording isn't on the phone." : 'There are no recordings on the phone yet (the ones on your PC can be searched when it\'s on).' };
      return skills.echoAsk(have, String(b.question || ''));
    }
    if (op === 'echo/delete') {
      storage.remove('brain.echo.r.' + b.id);
      storage.set('brain.echo.index', echoIndex().filter((x) => x.id !== b.id));
      outbox = outbox.filter((o) => !(o.type === 'echo_import' && o.data.rec.id === b.id)); saveOutbox();
      return { ok: true };
    }
  }

  if (route.startsWith('studio/')) throw NEEDS_PC('STUDIO');
  if (route.startsWith('spatial/')) throw NEEDS_PC('SPATIAL');
  if (route.startsWith('overdrive/')) throw NEEDS_PC('OVERWATCH');
  if (route === 'dnd' || route === 'focus' || route === 'pc' || route === 'files' || route === 'view' || route === 'upload' || route === 'dashboard' || route.startsWith('cdc/') || route.startsWith('emails/') || route === 'devices') {
    throw NEEDS_PC({ dnd: 'Do Not Disturb', focus: 'The focus timer', pc: 'That', files: 'Files', view: 'Opening files', upload: 'Uploading',
      dashboard: 'The dashboard', devices: 'Managing phones' }[route] || (route.startsWith('cdc/') ? 'CDC WONDER' : 'Email'));
  }
  throw NEEDS_PC('That');
}

