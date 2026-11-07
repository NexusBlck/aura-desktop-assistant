// The phone brain's "kit": AI keys + models, what AURA knows about you, your notes and protocols.
// Downloaded from the PC (phone_sync.kit) whenever it's reachable and kept on the phone, so AURA can
// think on its own when the PC is off.
import * as storage from '../platform/storage.js';

let kit = storage.get('brain.kit', null);

export const get = () => kit;
export const ready = () => !!(kit && kit.keys && (kit.keys.groq || kit.keys.gemini));
export const age = () => (kit ? Date.now() / 1000 - kit.t : Infinity);

export function set(k) {
  if (!k || !k.keys) return;
  kit = k;
  storage.set('brain.kit', k);
}

export function patch(fields) {
  if (!kit) return;
  kit = { ...kit, ...fields };
  storage.set('brain.kit', kit);
}

/** A few lines about the user, so answers fit them (same idea as vision_lab._who on the PC). */
export function who() {
  if (!kit) return '';
  const m = kit.memory || {};
  const facts = [...(m.facts || []), ...(m.projects || [])].slice(-12);
  const prefs = (m.preferences || []).slice(-6);
  return [kit.name ? `The user's name is ${kit.name}.` : '', facts.length ? `About the user: ${facts.join('; ')}` : '',
    prefs.length ? `Their preferences: ${prefs.join('; ')}` : ''].filter(Boolean).join('\n');
}

export function addMemory(category, text) {
  if (!kit) return;
  const m = { ...(kit.memory || {}) };
  m[category] = [...(m[category] || []), text].slice(-80);
  patch({ memory: m });
}
