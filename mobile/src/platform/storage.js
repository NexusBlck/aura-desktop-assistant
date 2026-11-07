// Small key-value storage for this device (pairing token, settings, cached state, history).
// Web: localStorage (can be unavailable in private mode - every call is guarded).
// Native later (Capacitor): swap for @capacitor/preferences + the Keychain for the token.
const PREFIX = 'aura.';

export function get(key, fallback = null) {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    return raw == null ? fallback : JSON.parse(raw);
  } catch (e) {
    return fallback;
  }
}

export function set(key, value) {
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify(value));
    return true;
  } catch (e) {
    return false;
  }
}

export function remove(key) {
  try { localStorage.removeItem(PREFIX + key); } catch (e) { /* ignore */ }
}
