// App state with change notifications. Platform-independent.
//   store.get('state')            the PC snapshot (tasks, reminders, system, focus, chat...)
//   store.set('conn', 'online')   'connecting' | 'online' | 'offline'
//   store.on('state', fn)         fn(value) whenever it changes; returns an unsubscribe fn
import * as storage from '../platform/storage.js';

const values = {
  state: storage.get('cache.state', null),          // last snapshot, shown while the PC is unreachable
  conn: 'connecting',
  status: { thinking: false, talking: false, listening: false, asking: false },
  chat: storage.get('cache.chat', []),
  recording: false,
  speakingLocal: false,
  settings: {
    voice: true,            // read AURA's answers aloud on the phone
    theme: 'dark',          // 'dark' (as on the PC) | 'system' | 'light'
    autoListen: true,       // when AURA asks a question by voice, open the mic
    ...storage.get('settings', {}),
  },
};
const subs = {};

export function get(key) { return values[key]; }

export function set(key, value) {
  values[key] = value;
  if (key === 'settings') storage.set('settings', value);
  (subs[key] || []).slice().forEach((fn) => { try { fn(value); } catch (e) { console.error(e); } });
}

export function update(key, patch) { set(key, { ...values[key], ...patch }); }

export function on(key, fn) {
  (subs[key] = subs[key] || []).push(fn);
  return () => { subs[key] = subs[key].filter((f) => f !== fn); };
}

let saveTimer = null;
/** Keeps a copy of the last PC snapshot on the phone (throttled). */
export function persist() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    storage.set('cache.state', values.state);
    storage.set('cache.chat', (values.chat || []).slice(-80));
  }, 800);
}
