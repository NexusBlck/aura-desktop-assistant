// Shared pieces for the NEXUS modules: audio/photo encoding, the camera, an immersive page, AURA's voice.
import { h, el, openPage, toast } from '../../../ui/components.js';
import * as audio from '../../../platform/audio.js';
import { speechChunks } from '../../../core/format.js';

export const toB64 = (blob) => new Promise((resolve) => {
  const r = new FileReader();
  r.onload = () => resolve(String(r.result).split(',')[1] || '');
  r.onerror = () => resolve('');
  r.readAsDataURL(blob);
});

export function fromB64(b64, type) {
  const bin = atob(b64), bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: type || 'audio/mpeg' });
}

/** Plays audio the PC already made (base64), resolving when it ends. */
export async function playB64(b64, type) {
  if (!b64) return;
  await audio.playOnce(fromB64(b64, type));
}

/** Speaks a NEXUS answer: the PC's audio when it made some, else the phone's own voice (phone brain). */
export async function playReply(res) {
  if (!res) return;
  if (res.audio) { await playB64(res.audio, res.ctype); return; }
  if (res.say) await audio.playOnce({ speak: res.say, lang: res.sayLang || 'English' });
}

/** Speaks text with AURA's voice (fetched from the PC), if voice replies are on. */
export function say(ctx, text) {
  if (!text || !ctx.store.get('settings').voice) return;
  audio.stop();
  audio.playSequence(speechChunks(text).map((p) => () => ctx.api.tts(p).catch(() => null)));
}

export async function camera(facing = 'environment', extra = {}) {
  return navigator.mediaDevices.getUserMedia({
    audio: false, video: { facingMode: { ideal: facing }, width: { ideal: 1280 }, height: { ideal: 720 }, ...extra },
  });
}

/** The current video frame as base64 JPEG (no data: prefix). */
export function grab(video, maxW = 768, quality = 0.72) {
  const w = video.videoWidth, hh = video.videoHeight;
  if (!w || !hh) return '';
  const s = Math.min(1, maxW / w), c = document.createElement('canvas');
  c.width = Math.round(w * s); c.height = Math.round(hh * s);
  c.getContext('2d').drawImage(video, 0, 0, c.width, c.height);
  return c.toDataURL('image/jpeg', quality).split(',')[1];
}

export const stopStream = (s) => { if (s) s.getTracks().forEach((t) => t.stop()); };

/** A full-screen NEXUS page (dark, immersive). cleanup() runs when it closes. */
export function stage(title, code, render) {
  return openPage(title, (body, page) => {
    page.el.classList.add('nx-page');
    if (code) page.actions.append(el('span', 'nx-code', code));
    const cleanups = [];
    page.onClose = () => { cleanups.forEach((fn) => { try { fn(); } catch (e) { /* ignore */ } }); };
    render(body, page, (fn) => cleanups.push(fn));
  }, { flush: true });
}

export function micError(e) {
  toast('Microphone', e && e.name === 'NotAllowedError'
    ? 'Microphone access is off - allow it in Settings → Safari (or AURA) → Microphone.'
    : "Couldn't start the microphone.", 'error');
}

export function camError(e) {
  toast('Camera', e && e.name === 'NotAllowedError'
    ? 'Camera access is off - allow it in Settings → Safari (or AURA) → Camera.'
    : "Couldn't start the camera.", 'error');
}

/** A row of choice chips. options = [[value, label]]; returns { node, set(value) }. */
export function chips(options, value, onPick) {
  const node = h('div', { class: 'nx-chips' });
  const btns = options.map(([v, label]) => {
    const b = h('button', { class: 'nx-chip' + (v === value ? ' on' : ''), type: 'button' }, label);
    b.onclick = () => { set(v); onPick(v); };
    node.append(b);
    return [v, b];
  });
  function set(v) { btns.forEach(([bv, b]) => b.classList.toggle('on', bv === v)); }
  return { node, set };
}

/** A small HUD readout: label over value. */
export function readout(label, value = '--') {
  const v = el('b', null, value);
  return { node: h('div', { class: 'nx-read' }, el('span', null, label), v), set(x) { v.textContent = x; } };
}
