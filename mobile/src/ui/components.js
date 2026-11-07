// Reusable UI pieces in AURA's HUD style. Platform-independent (plain DOM).
import * as haptics from '../platform/haptics.js';

export function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

export function h(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'html') e.innerHTML = v;            // only ever used with our own static SVG
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) e.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return e;
}

// ---------------------------------------------------------------- icons (stroke SVG, currentColor)
const P = {
  core: '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="8" stroke-dasharray="3 3"/><path d="M12 1v3M12 20v3M1 12h3M20 12h3"/>',
  comms: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 21l1.9-5.4A8 8 0 1 1 21 12z"/>',
  lists: '<path d="M9 6h12M9 12h12M9 18h12"/><path d="M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2"/>',
  system: '<rect x="3" y="4" width="18" height="12" rx="1"/><path d="M8 20h8M12 16v4"/><path d="M7 12l2-3 2 2 3-4 3 5"/>',
  more: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>',
  back: '<path d="M15 5l-7 7 7 7"/>',
  mic: '<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  send: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  stop: '<rect x="6" y="6" width="12" height="12"/>',
  mail: '<rect x="3" y="5" width="18" height="14"/><path d="M3 7l9 6 9-6"/>',
  eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  dash: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  cat: '<path d="M5 20c0-6 2-9 7-9s7 3 7 9"/><path d="M7 11L6 4l4 4M17 11l1-7-4 4"/><circle cx="10" cy="15" r=".8"/><circle cx="14" cy="15" r=".8"/>',
  db: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  folder: '<path d="M3 6h7l2 2h9v11H3z"/>',
  upload: '<path d="M12 16V4M6 10l6-6 6 6M4 20h16"/>',
  pen: '<path d="M4 20h4L20 8l-4-4L4 16z"/>',
  log: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  pc: '<rect x="2" y="4" width="20" height="13"/><path d="M8 21h8M12 17v4"/>',
  camera: '<path d="M4 7h4l2-3h4l2 3h4v13H4z"/><circle cx="12" cy="13" r="4"/>',
  image: '<rect x="3" y="3" width="18" height="18"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/>',
  file: '<path d="M14 3H6v18h12V7z"/><path d="M14 3v4h4"/>',
  speaker: '<path d="M4 9h4l5-4v14l-5-4H4z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>',
  check: '<path d="M4 12l5 5L20 6"/>',
  down: '<path d="M6 9l6 6 6-6"/>',
  chev: '<path d="M9 5l7 7-7 7"/>',
  power: '<path d="M12 3v9M6.3 6.3a8 8 0 1 0 11.4 0"/>',
  bell: '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  // NEXUS
  nexus: '<polygon points="12,2 21,7 21,17 12,22 3,17 3,7"/><circle cx="12" cy="12" r="3"/><path d="M12 2v7M21 17l-6-3.5M3 17l6-3.5"/>',
  live: '<circle cx="12" cy="12" r="3"/><path d="M7.8 7.8a6 6 0 0 0 0 8.4M16.2 7.8a6 6 0 0 1 0 8.4M4.9 4.9a10 10 0 0 0 0 14.2M19.1 4.9a10 10 0 0 1 0 14.2"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
  wave: '<path d="M2 12h2M6 8v8M10 4v16M14 7v10M18 10v4M22 12h-2"/>',
  radar: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 12l6-6"/><circle cx="16" cy="15" r="1"/>',
  heart: '<path d="M12 20s-7-4.5-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.5-7 10-7 10z"/><path d="M3 12h4l2-3 3 6 2-3h7"/>',
  remote: '<rect x="2" y="3" width="14" height="10"/><path d="M6 17h6"/><rect x="16" y="9" width="6" height="12" rx="1"/><path d="M19 18h0"/>',
  bolt: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  key: '<rect x="2" y="6" width="20" height="12" rx="1"/><path d="M6 10h0M10 10h0M14 10h0M18 10h0M7 14h10"/>',
  play: '<path d="M7 4l13 8-13 8z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  copy: '<rect x="8" y="8" width="12" height="12"/><path d="M16 8V4H4v12h4"/>',
  cube: '<path d="M12 2l9 5v10l-9 5-9-5V7z"/><path d="M12 22V12M21 7l-9 5-9-5"/>',
  hand: '<path d="M8 13V5.5a1.5 1.5 0 0 1 3 0V12M11 11.5V4a1.5 1.5 0 0 1 3 0v8M14 11.5V5.5a1.5 1.5 0 0 1 3 0V13"/><path d="M17 9.5a1.5 1.5 0 0 1 3 0V15a7 7 0 0 1-7 7h-1a7 7 0 0 1-6-3.4L3.3 14a1.6 1.6 0 0 1 2.7-1.7L8 15"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
  sliders: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  expand: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
};

export function icon(name) {
  const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  s.setAttribute('viewBox', '0 0 24 24');
  s.setAttribute('fill', 'none');
  s.setAttribute('stroke', 'currentColor');
  s.setAttribute('stroke-width', '1.8');
  s.setAttribute('stroke-linecap', 'round');
  s.setAttribute('stroke-linejoin', 'round');
  s.setAttribute('aria-hidden', 'true');
  s.innerHTML = P[name] || '';
  return s;
}

export const LOGO = `<svg viewBox="0 0 40 40" aria-hidden="true"><polygon points="20,2 36,11 36,29 20,38 4,29 4,11" fill="none" stroke="#3ad6ff" stroke-width="1.5"/>
<polygon points="20,9 30,14.5 30,25.5 20,31 10,25.5 10,14.5" fill="rgba(58,214,255,0.15)" stroke="#9ef3ff" stroke-width="1"/><circle cx="20" cy="20" r="4" fill="#9ef3ff"/></svg>`;

// ---------------------------------------------------------------- panels & bits
export function panel(title, code, ...children) {
  const p = el('section', 'panel');
  if (title) p.append(h('div', { class: 'ph' }, h('div', { class: 'title' }, title), code ? h('div', { class: 'code' }, code) : null));
  p.append(...children.filter(Boolean));
  return p;
}

export function button(label, onClick, cls = '') {
  const b = h('button', { class: 'btn ' + cls, type: 'button' }, label);
  b.addEventListener('click', async (e) => {
    haptics.tap();
    if (b.disabled) return;
    b.disabled = true;
    try { await onClick(e); } finally { b.disabled = false; }
  });
  return b;
}

export const empty = (text) => el('div', 'empty', text);
export const loading = (text = 'Working…') => h('div', { class: 'loading', role: 'status' }, el('span', 'spinner'), text);

// ---------------------------------------------------------------- toasts
let toastBox = null;
export function toast(title, body = '', kind = 'info', ms = 6000, action = null) {
  if (!toastBox) { toastBox = h('div', { class: 'toasts', 'aria-live': 'polite' }); document.body.append(toastBox); }
  const t = h('div', { class: `toast ${kind}`, role: 'status' }, el('div', 'tt', String(title).toUpperCase()), el('div', 'tb', body));
  if (action) {
    // a real tap is needed to open other apps / pages on iPhone, so actions are buttons
    const go = h('button', { class: 'btn solid', style: 'margin-top:10px;width:100%' }, action.label);
    go.onclick = () => { t.remove(); action.onClick(); };
    t.append(go);
  }
  const close = h('button', { class: 'close', 'aria-label': 'Dismiss' }, '×');
  close.onclick = () => t.remove();
  t.append(close);
  toastBox.prepend(t);
  while (toastBox.children.length > 3) toastBox.lastChild.remove();
  setTimeout(() => t.remove(), ms);
  if (kind === 'reminder' || kind === 'focus') haptics.warning();
}

// ---------------------------------------------------------------- pages (pushed sub-screens)
const pageStack = [];

/** Opens a full-screen page over the tabs. render(body, page) fills it; returns { close }. */
export function openPage(title, render, { flush = false } = {}) {
  const back = h('button', { class: 'back', 'aria-label': 'Back' }, icon('back'));
  const heading = h('h1', {}, title);
  const actions = h('div', { class: 'row-btns', style: 'flex:0 0 auto' });
  const body = h('div', { class: 'page-body' + (flush ? ' flush' : '') });
  const page = h('div', { class: 'page', role: 'dialog', 'aria-label': title }, h('header', { class: 'page-head' }, back, heading, actions), body);
  const api = {
    el: page, body, actions,
    setTitle(t) { heading.textContent = t; },
    onClose: null,
    close() {
      if (!page.isConnected) return;
      const i = pageStack.indexOf(api); if (i >= 0) pageStack.splice(i, 1);
      page.classList.add('closing');
      setTimeout(() => page.remove(), 220);
      if (api.onClose) api.onClose();
    },
  };
  back.onclick = () => api.close();
  edgeSwipe(page, () => api.close());
  document.body.append(page);
  pageStack.push(api);
  render(body, api);
  setTimeout(() => back.focus({ preventScroll: true }), 300);
  return api;
}

export const closeTopPage = () => { const p = pageStack[pageStack.length - 1]; if (p) { p.close(); return true; } return false; };

/** iOS-style "swipe from the left edge to go back". */
function edgeSwipe(node, onBack) {
  let x0 = null, y0 = 0, dx = 0;
  node.addEventListener('touchstart', (e) => {
    const t = e.touches[0];
    if (t.clientX - node.getBoundingClientRect().left < 24) { x0 = t.clientX; y0 = t.clientY; dx = 0; } else x0 = null;      // iPad: pages start beside the rail
  }, { passive: true });
  node.addEventListener('touchmove', (e) => {
    if (x0 == null) return;
    const t = e.touches[0];
    dx = t.clientX - x0;
    if (Math.abs(t.clientY - y0) > 60) { x0 = null; node.style.transform = ''; return; }
    if (dx > 0) node.style.transform = `translateX(${dx}px)`;
  }, { passive: true });
  node.addEventListener('touchend', () => {
    if (x0 == null) return;
    node.style.transition = 'transform .2s ease';
    if (dx > window.innerWidth * 0.33) { node.style.transform = 'translateX(100%)'; setTimeout(onBack, 180); }
    else node.style.transform = '';
    setTimeout(() => { node.style.transition = ''; }, 220);
    x0 = null;
  });
}

// ---------------------------------------------------------------- bottom sheets
export function openSheet(title, render) {
  const back = h('div', { class: 'sheet-back' });
  const body = h('div', { class: 'sheet-body' });
  const sheet = h('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    h('div', { class: 'grab' }), title ? h('h2', {}, title) : null, body);
  const api = {
    el: sheet, body,
    close() {
      if (!sheet.isConnected) return;
      sheet.classList.add('closing'); back.remove();
      setTimeout(() => sheet.remove(), 200);
    },
  };
  back.onclick = () => api.close();
  // drag the grabber down to close
  let y0 = null;
  sheet.addEventListener('touchstart', (e) => { if (e.target.closest('.grab, h2')) y0 = e.touches[0].clientY; }, { passive: true });
  sheet.addEventListener('touchmove', (e) => { if (y0 != null) { const d = Math.max(0, e.touches[0].clientY - y0); sheet.style.transform = `translateY(${d}px)`; } }, { passive: true });
  sheet.addEventListener('touchend', (e) => {
    if (y0 == null) return;
    const d = e.changedTouches[0].clientY - y0; y0 = null;
    if (d > 90) api.close(); else sheet.style.transform = '';
  });
  document.body.append(back, sheet);
  render(body, api);
  return api;
}

/** A yes/no question as a sheet. Resolves true/false. */
export function confirm(title, text, yes = 'CONFIRM', danger = false) {
  return new Promise((resolve) => {
    let done = false;
    const s = openSheet(title, (body, api) => {
      body.append(h('p', { style: 'margin:0 0 16px;line-height:1.5' }, text),
        h('div', { class: 'row-btns' },
          button('CANCEL', () => { done = true; api.close(); resolve(false); }),
          button(yes, () => { done = true; api.close(); resolve(true); }, danger ? 'danger' : 'solid')));
    });
    const obs = new MutationObserver(() => { if (!s.el.isConnected) { obs.disconnect(); if (!done) resolve(false); } });
    obs.observe(document.body, { childList: true });
  });
}

// ---------------------------------------------------------------- charts
/** Line sparkline(s) like the HUD's: series = [{ data, max, color, fill }]. */
export function drawSpark(canvas, series) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, hh = canvas.clientHeight;
  if (!w || !hh) return;
  canvas.width = w * dpr; canvas.height = hh * dpr;
  const g = canvas.getContext('2d');
  g.scale(dpr, dpr);
  g.clearRect(0, 0, w, hh);
  for (const s of series) {
    const d = s.data || [];
    if (d.length < 2) continue;
    const step = w / (d.length - 1);
    g.beginPath();
    d.forEach((v, i) => { const y = hh - (Math.min(v, s.max) / s.max) * (hh - 2) - 1; i ? g.lineTo(i * step, y) : g.moveTo(0, y); });
    g.strokeStyle = s.color; g.lineWidth = 1.5; g.stroke();
    g.lineTo(w, hh); g.lineTo(0, hh); g.closePath(); g.fillStyle = s.fill; g.fill();
  }
}

/** The HUD reactor (same SVG geometry), with ids for the CPU/RAM rings. */
export function reactorSVG() {
  // identical geometry to the HUD's reactor (Face/aura_face.html)
  const ticks = Array.from({ length: 120 }, (_, i) => {
    const a = (i / 120) * Math.PI * 2, long = i % 10 === 0, r1 = 186, r2 = long ? 196 : 191;
    return `<line x1="${200 + r1 * Math.cos(a)}" y1="${200 + r1 * Math.sin(a)}" x2="${200 + r2 * Math.cos(a)}" y2="${200 + r2 * Math.sin(a)}" stroke="#3ad6ff" stroke-opacity="${long ? 0.9 : 0.35}" stroke-width="${long ? 2 : 1}"/>`;
  }).join('');
  const segs = Array.from({ length: 12 }, (_, i) => {
    const a0 = (i / 12) * Math.PI * 2 + 0.05, a1 = a0 + (Math.PI * 2) / 12 - 0.16, r = 126, major = i % 3 === 0;
    return `<path d="M${200 + r * Math.cos(a0)} ${200 + r * Math.sin(a0)} A${r} ${r} 0 0 1 ${200 + r * Math.cos(a1)} ${200 + r * Math.sin(a1)}" fill="none" stroke="${major ? '#9ef3ff' : '#3ad6ff'}" stroke-width="${major ? 4 : 2}" stroke-opacity="${major ? 0.9 : 0.4}"/>`;
  }).join('');
  return `<svg viewBox="0 0 400 400" aria-hidden="true">
  <defs><radialGradient id="coreGlow"><stop offset="0%" stop-color="#9ef3ff" stop-opacity=".55"/><stop offset="55%" stop-color="#3ad6ff" stop-opacity=".12"/><stop offset="100%" stop-color="#3ad6ff" stop-opacity="0"/></radialGradient></defs>
  <circle cx="200" cy="200" r="130" fill="url(#coreGlow)"/>
  <g class="spin s1">${ticks}</g>
  <g class="spin s2"><circle cx="200" cy="200" r="172" fill="none" stroke="#3ad6ff" stroke-opacity=".5" stroke-width="2" stroke-dasharray="60 30 10 30"/></g>
  <circle cx="200" cy="200" r="158" fill="none" stroke="rgba(58,214,255,0.12)" stroke-width="7"/>
  <circle id="rCpu" cx="200" cy="200" r="158" fill="none" stroke="#3ad6ff" stroke-width="7" stroke-dasharray="0 993" transform="rotate(-90 200 200)" style="filter:drop-shadow(0 0 5px #3ad6ff);transition:stroke-dasharray .8s"/>
  <circle cx="200" cy="200" r="146" fill="none" stroke="rgba(167,139,250,0.12)" stroke-width="4"/>
  <circle id="rRam" cx="200" cy="200" r="146" fill="none" stroke="#a78bfa" stroke-width="4" stroke-dasharray="0 918" transform="rotate(-90 200 200)" style="filter:drop-shadow(0 0 5px #a78bfa);transition:stroke-dasharray .8s"/>
  <g class="spin s3">${segs}</g>
  <g class="spin s4"><circle cx="200" cy="200" r="112" fill="none" stroke="#9ef3ff" stroke-opacity=".35" stroke-width="1" stroke-dasharray="3 6"/><circle cx="312" cy="200" r="4" fill="#9ef3ff"/><circle cx="88" cy="200" r="3" fill="#3ad6ff"/></g>
  <g class="spin s5"><path d="M200 105 A95 95 0 0 1 295 200" fill="none" stroke="#9ef3ff" stroke-width="2.5" stroke-linecap="round"/><path d="M200 295 A95 95 0 0 1 105 200" fill="none" stroke="#3ad6ff" stroke-width="2.5" stroke-linecap="round" stroke-opacity=".7"/></g>
  <polygon points="200,128 262,164 262,236 200,272 138,236 138,164" fill="rgba(58,214,255,0.05)" stroke="#3ad6ff" stroke-opacity=".6" stroke-width="1.2"/>
  <line x1="200" y1="18" x2="200" y2="48" stroke="#3ad6ff" stroke-opacity=".6"/><line x1="200" y1="352" x2="200" y2="382" stroke="#3ad6ff" stroke-opacity=".6"/>
  <line x1="18" y1="200" x2="48" y2="200" stroke="#3ad6ff" stroke-opacity=".6"/><line x1="352" y1="200" x2="382" y2="200" stroke="#3ad6ff" stroke-opacity=".6"/></svg>`;
}

export function setArc(node, pct, circumference, sweep) {
  if (!node) return;
  const len = (Math.max(0, Math.min(100, pct || 0)) / 100) * (sweep || circumference);
  node.setAttribute('stroke-dasharray', `${len} ${circumference}`);
}

/** HUD-style ring gauge (CPU/RAM). Returns { node, set(pct) }. */
export function gauge(label, color) {
  const node = h('div', { class: 'gauge' });
  node.innerHTML = `<svg viewBox="0 0 120 120"><circle cx="60" cy="60" r="50" fill="none" stroke="${color}" stroke-opacity=".14" stroke-width="8" stroke-dasharray="235.6 314.2" transform="rotate(135 60 60)"/>
    <circle class="arc" cx="60" cy="60" r="50" fill="none" stroke="${color}" stroke-width="8" stroke-dasharray="0 314.2" transform="rotate(135 60 60)" style="filter:drop-shadow(0 0 4px ${color});transition:stroke-dasharray .6s"/>
    <circle cx="60" cy="60" r="38" fill="none" stroke="${color}" stroke-opacity=".3" stroke-width="1" stroke-dasharray="2 4"/></svg>`;
  const val = el('div', 'val', '--');
  node.append(val, el('div', 'lbl', label));
  return { node, set(p) { val.textContent = p != null ? Math.round(p) : '--'; setArc(node.querySelector('.arc'), p, 314.2, 235.6); } };
}
