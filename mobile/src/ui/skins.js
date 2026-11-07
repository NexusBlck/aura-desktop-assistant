// AURA UI skins: the original HUD plus five alternative interfaces, each with its own themes. The choice is stored on
// this device (it survives closing AURA) and applied before the app draws. Skins only set two attributes on <html>
// (data-skin, data-skin-theme) and load their fonts; all of the look lives in ui/skins/*.css.
const KEY = 'aura.skin';
const GF = 'https://fonts.googleapis.com/css2?display=swap&';

export const SKINS = [
  { id: 'hud', name: 'AURA HUD', tag: 'The original', desc: 'Sci-fi heads-up display - glowing cyan, scanlines, the reactor.',
    themes: [{ id: 'default', name: 'Original', c: ['#02050a', '#07121b', '#d6f6ff', '#3ad6ff'] }] },
  { id: 'ledger', name: 'Ledger', tag: 'Editorial', desc: 'Serif headlines, ruled sections, one ink accent. Offices, legal, finance, admin.',
    fonts: 'family=Newsreader:ital,wght@0,400;0,500;0,600;1,500&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500',
    themes: [{ id: 'paper', name: 'Paper', c: ['#f3eee4', '#fbf8f1', '#211d18', '#8a2e1d'] },
      { id: 'linen', name: 'Linen', c: ['#f4f5f2', '#ffffff', '#141c26', '#1d3a5f'] },
      { id: 'night', name: 'Night edition', c: ['#1a1916', '#22211d', '#ece6d8', '#c9a35b'] }] },
  { id: 'hmi', name: 'HMI', tag: 'Industrial', desc: 'Control-room panels (ISA-101): neutral greys, colour only for status. Plants, labs, ops.',
    fonts: 'family=Barlow:wght@400;500;600&family=Barlow+Condensed:wght@500;600;700&family=JetBrains+Mono:wght@400;600',
    themes: [{ id: 'daylight', name: 'Daylight', c: ['#cfd0cc', '#e4e5e1', '#151617', '#275c9b'] },
      { id: 'nightshift', name: 'Night shift', c: ['#1c1f22', '#262a2e', '#d9dde0', '#4a8fd8'] },
      { id: 'hazard', name: 'Hazard', c: ['#1a1a1a', '#242424', '#efefef', '#f2c200'] }] },
  { id: 'swiss', name: 'Swiss', tag: 'Corporate', desc: 'Heavy grotesk type, strict grid, thick rules, flat colour. Business and product teams.',
    fonts: 'family=Archivo:wght@400;500;600;700;800;900&family=Roboto+Mono:wght@400;500',
    themes: [{ id: 'signal', name: 'Signal', c: ['#ffffff', '#ffffff', '#111111', '#e30613'] },
      { id: 'klein', name: 'Klein', c: ['#f6f6f3', '#ffffff', '#111111', '#002fa7'] },
      { id: 'noir', name: 'Noir', c: ['#0d0d0d', '#161616', '#f2f2f2', '#ff3b30'] }] },
  { id: 'console', name: 'Console', tag: 'Operations', desc: 'Text-mode ops console: one monospace face, windows titled on their border. IT, labs, engineering.',
    fonts: 'family=IBM+Plex+Mono:wght@400;500;600',
    themes: [{ id: 'amber', name: 'Amber', c: ['#120d04', '#1a1307', '#ffb000', '#ffc94d'] },
      { id: 'phosphor', name: 'Phosphor', c: ['#04100a', '#08180f', '#3cff6e', '#8bffaa'] },
      { id: 'daylight', name: 'Daylight', c: ['#f4f4ef', '#ffffff', '#1d1d1b', '#005fcc'] }] },
  { id: 'haven', name: 'Haven', tag: 'Calm & accessible', desc: 'Hyperlegible type, soft rounded cards, big targets. Clinics, schools, care, families.',
    fonts: 'family=Atkinson+Hyperlegible:wght@400;700&family=Atkinson+Hyperlegible+Mono:wght@400;700',
    themes: [{ id: 'sand', name: 'Sand', c: ['#f3ece3', '#fffaf4', '#3a2e26', '#b85a36'] },
      { id: 'sage', name: 'Sage', c: ['#ecf0ea', '#fbfcfa', '#25302a', '#46755a'] },
      { id: 'dusk', name: 'Dusk', c: ['#211d1a', '#2e2925', '#f1e7dc', '#e3a468'] }] },
];

const byId = (id) => SKINS.find((s) => s.id === id) || SKINS[0];

export function current() {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || 'null');
    if (v && byId(v.skin).id === v.skin) return { skin: v.skin, theme: byId(v.skin).themes.some((t) => t.id === v.theme) ? v.theme : byId(v.skin).themes[0].id };
  } catch (e) { /* first run / private mode */ }
  return { skin: 'hud', theme: 'default' };
}

function loadFonts(s) {
  if (!s.fonts || document.getElementById('skin-font-' + s.id)) return;
  const l = document.createElement('link');
  l.rel = 'stylesheet'; l.id = 'skin-font-' + s.id; l.href = GF + s.fonts;
  document.head.append(l);
}

export function apply(skin, theme) {
  const s = byId(skin), root = document.documentElement;
  if (s.id === 'hud') { root.removeAttribute('data-skin'); root.removeAttribute('data-skin-theme'); }
  else {
    loadFonts(s);
    root.setAttribute('data-skin', s.id);
    root.setAttribute('data-skin-theme', s.themes.some((t) => t.id === theme) ? theme : s.themes[0].id);
  }
  const t = s.themes.find((x) => x.id === theme) || s.themes[0];
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', s.id === 'hud' ? '#02050a' : t.c[0]);
  // the app's icon follows the UI: the tab icon now, and the home-screen icon the next time AURA is added to it
  const base = s.id === 'hud' ? null : `/icons/skins/${s.id}-${t.id}`;
  const setHref = (sel, href) => { const l = document.querySelector(sel); if (l) l.setAttribute('href', href); };
  setHref('link[rel="icon"]', base ? `${base}-192.png` : '/icons/icon-192.png');
  setHref('link[rel="apple-touch-icon"]', base ? `${base}-180.png` : '/icons/apple-touch-icon.png');
  setHref('link[rel="manifest"]', base ? `${base}.webmanifest` : '/manifest.webmanifest');
}

/** The icon for a UI + theme (for the Appearance screen). */
export function iconFor(skin, theme) { return skin === 'hud' ? '/icons/apple-touch-icon.png' : `/icons/skins/${skin}-${theme}-180.png`; }

/** Choose (and remember) a UI + theme on this device. */
export function set(skin, theme) {
  const s = byId(skin);
  const th = s.themes.some((t) => t.id === theme) ? theme : s.themes[0].id;
  try { localStorage.setItem(KEY, JSON.stringify({ skin: s.id, theme: th })); } catch (e) { /* ignore */ }
  apply(s.id, th);
  return { skin: s.id, theme: th };
}

/** Next UI in the list (for a quick cycle button / gesture). */
export function cycle() {
  const cur = current(), i = SKINS.findIndex((s) => s.id === cur.skin);
  const next = SKINS[(i + 1) % SKINS.length];
  return set(next.id, next.themes[0].id);
}

// applied as soon as the app loads, before anything is drawn
const start = current();
apply(start.skin, start.theme);
