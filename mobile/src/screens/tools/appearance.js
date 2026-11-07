// APPEARANCE - choose AURA's interface on this device: the original HUD or one of five alternative UIs, each with its
// own themes. A tap applies it instantly and it stays chosen until you change it (even after AURA closes).
import { h, el, openPage, toast } from '../../ui/components.js';
import * as haptics from '../../platform/haptics.js';
import { SKINS, current, set, iconFor } from '../../ui/skins.js';

const FONT = { hud: "'Orbitron', sans-serif", ledger: "'Newsreader', Georgia, serif", hmi: "'Barlow Condensed', 'Arial Narrow', sans-serif",
  swiss: "'Archivo', Arial, sans-serif", console: "'IBM Plex Mono', Consolas, monospace", haven: "'Atkinson Hyperlegible', sans-serif" };

/** A small drawing of the UI in a theme's colours - so you can see it before choosing. */
function preview(skin, theme) {
  const [bg, panel, text, accent] = theme.c;
  const f = FONT[skin.id];
  const box = h('div', { class: `ap-prev ap-${skin.id}`, style: `background:${bg};color:${text};font-family:${f}` });
  const head = h('div', { class: 'ap-p-head', style: `border-color:${skin.id === 'hud' ? accent : text}` },
    el('b', null, skin.id === 'console' ? 'aura@:~$' : skin.id === 'swiss' ? 'aura.' : 'AURA'), h('i', { style: `background:${accent}` }));
  const status = h('div', { class: 'ap-p-status' }, el('span', null, skin.id === 'console' ? '> idle' : skin.id === 'ledger' || skin.id === 'haven' ? 'Idle' : 'IDLE'));
  if (skin.id === 'haven') status.style.cssText = `background:${panel};box-shadow:inset 0 0 0 5px ${accent}22`;
  if (skin.id === 'hmi') status.style.cssText = `background:${panel};border:2px solid ${text}55`;
  if (skin.id === 'hud') status.style.cssText = `border:1px solid ${accent};box-shadow:0 0 12px ${accent}66;color:${accent}`;
  const row = h('div', { class: 'ap-p-row' },
    h('div', { class: 'ap-p-card', style: `background:${panel};border-color:${text}22` }, el('small', null, 'Tasks'), el('b', null, '3')),
    h('div', { class: 'ap-p-card', style: `background:${panel};border-color:${text}22` }, el('small', null, 'Focus'), el('b', null, '25:00')));
  const btn = h('div', { class: 'ap-p-btn', style: `background:${accent};color:${bg}` }, skin.id === 'console' ? '[ run ]' : 'Start');
  box.append(head, status, row, btn);
  return box;
}

export function open() {
  openPage('Appearance', (body, page) => {
    const wrap = h('div', { class: 'ap' });
    const render = () => {
      const cur = current();
      const ios = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
      const iconBox = h('div', { class: 'ap-icon' }, h('img', { src: iconFor(cur.skin, cur.theme), alt: 'AURA icon for this interface' }),
        h('div', {}, el('b', null, 'App icon'),
          el('p', null, ios
            ? 'The icon now matches this UI, but iPhone/iPad only update a home-screen icon when it is added again: Safari → open AURA → Share → Add to Home Screen, then delete the old icon. (A newly added copy starts fresh - pair it with your PC once; your PC keeps all your data.)'
            : 'The icon now matches this UI. If your home-screen icon did not change, remove AURA from the home screen and install it again from the browser menu.')));
      wrap.replaceChildren(
        el('p', 'help', 'Pick the interface for this device. It changes instantly and stays until you change it. The PC has its own choice (the UI button on the PC screen).'),
        iconBox,
        ...SKINS.map((s) => {
          const active = cur.skin === s.id;
          const theme = s.themes.find((t) => t.id === (active ? cur.theme : s.themes[0].id)) || s.themes[0];
          const card = h('section', { class: 'ap-card' + (active ? ' on' : '') },
            preview(s, theme),
            h('div', { class: 'ap-info' },
              h('div', { class: 'ap-name' }, el('b', null, s.name), el('span', null, s.tag), active ? el('em', null, 'In use') : null),
              el('p', null, s.desc),
              h('div', { class: 'ap-themes' }, s.themes.map((t) => {
                const on = active && cur.theme === t.id;
                const b = h('button', { class: 'ap-theme' + (on ? ' on' : ''), type: 'button', 'aria-pressed': on ? 'true' : 'false' },
                  h('span', { class: 'ap-sw' }, t.c.map((c) => h('i', { style: `background:${c}` }))), el('span', null, t.name));
                b.onclick = () => { haptics.tap(); set(s.id, t.id); render(); toast('Appearance', `${s.name} · ${t.name}`, 'info', 1800); };
                return b;
              }))));
          return card;
        }));
    };
    render();
    body.append(wrap);
  });
}
