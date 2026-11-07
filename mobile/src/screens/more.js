// MORE - every other AURA tool: the PC's extra windows (dashboard, vision, explain cat, CDC WONDER),
// emails, files, logging, journal and settings.
import { el, h, icon, panel } from '../ui/components.js';
import * as haptics from '../platform/haptics.js';

const TOOLS = [
  ['emails', 'EMAILS', 'mail'], ['dashboard', 'DASHBOARD', 'dash'], ['vision', 'VISION', 'eye'],
  ['appearance', 'APPEARANCE', 'sliders'], ['overwatch', 'OVERWATCH', 'target'], ['spatial', 'SPATIAL 3D', 'cube'], ['studio', 'STUDIO', 'pen'], ['explain', 'EXPLAIN', 'cat'], ['cdc', 'CDC WONDER', 'db'], ['files', 'FILES', 'folder'],
  ['upload', 'ADD A PDF', 'upload'], ['log', 'LOG SESSION', 'log'], ['journal', 'JOURNAL', 'pen'],
  ['settings', 'SETTINGS', 'gear'],
];

let ctx, badges = {};

export function mount(root, c) {
  ctx = c;
  const nexus = h('button', { class: 'nx-entry', 'aria-label': 'NEXUS - futuristic systems' },
    h('span', { class: 'nx-entry-ic' }, icon('nexus')),
    h('span', { class: 'nx-entry-txt' }, el('b', null, 'NEXUS'), el('span', null, 'LIVE · BABEL · ECHO · RADAR · PULSE · LINK · PROTOCOLS')),
    icon('chev'));
  nexus.onclick = () => { haptics.tap(); ctx.openTool('nexus'); };
  root.append(nexus);
  const grid = h('div', { class: 'tools' });
  for (const [id, label, ic] of TOOLS) {
    const b = h('button', { class: 'tool', 'aria-label': label }, icon(ic), label);
    b.onclick = () => { haptics.tap(); ctx.openTool(id); };
    badges[id] = b;
    grid.append(b);
  }
  const tips = h('ul', { class: 'list' },
    h('li', {}, el('span', 'txt', 'Everything you can say to AURA on the PC works here too - type it or tap the mic in COMMS.')),
    h('li', {}, el('span', 'txt', 'Add AURA to your Home Screen (Safari → Share → Add to Home Screen): it opens full-screen like an app and can send you notifications.')),
    h('li', {}, el('span', 'txt', 'Tap the core on the CORE screen to talk; tap it again while AURA is speaking to stop.')));
  root.append(grid, el('div', 'section-label', 'GOOD TO KNOW'), panel(null, null, tips));

  ctx.store.on('state', (s) => {
    const n = (s && s.emails && s.emails.length) || 0;
    let b = badges.emails.querySelector('.badge');
    if (!n) { if (b) b.remove(); return; }
    if (!b) { b = el('span', 'badge'); badges.emails.append(b); }
    b.textContent = n;
  });
}
