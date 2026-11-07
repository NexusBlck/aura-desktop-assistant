// NEXUS - the hub for AURA's futuristic systems.
import { h, el, icon } from '../../../ui/components.js';
import * as haptics from '../../../platform/haptics.js';
import { stage } from './common.js';

const SYSTEMS = [
  ['nexus-live', 'LIVE', 'live', 'SYS-01', 'Hands-free. Just talk - AURA hears you, sees through your camera and acts.'],
  ['nexus-babel', 'BABEL', 'globe', 'SYS-02', 'Live interpreter. Two people, two languages, one conversation.'],
  ['nexus-echo', 'ECHO', 'wave', 'SYS-03', 'Records lectures & meetings → transcript, notes, flashcards. Ask them anything later.'],
  ['nexus-radar', 'RADAR', 'radar', 'SYS-04', 'Your surroundings: places, AR labels, compass, Qibla, sun & moon.'],
  ['nexus-pulse', 'PULSE', 'heart', 'SYS-05', 'Bio-scanner: heart rate & stress from your fingertip, breathing, reflexes.'],
  ['nexus-link', 'LINK', 'remote', 'SYS-06', 'Your PC in your hand: live screen, touch, trackpad, air-mouse, keyboard.'],
  ['nexus-protocols', 'PROTOCOLS', 'bolt', 'SYS-07', 'One word runs a chain of actions. Give AURA a goal - it plans the mission.'],
  ['overwatch', 'OVERWATCH', 'target', 'SYS-10', 'Command your PC from anywhere: AEGIS guard, TOTAL RECALL screen memory, AIR CONTROL with your hands, PC power.'],
  ['spatial', 'SPATIAL', 'cube', 'SYS-09', 'Bring me anything in 3D - organs, engines, molecules, buildings. Explore it with words, touch or your hands.'],
  ['studio', 'STUDIO', 'pen', 'SYS-08', 'Make anything - pictures, charts, videos, slides, docs - and change it with words. Edit your photos & videos.'],
];

export function open(ctx) {
  stage('NEXUS', 'v1 · 10 SYSTEMS', (body) => {
    const hero = h('div', { class: 'nx-hero' });
    hero.innerHTML = `<svg viewBox="0 0 200 200" aria-hidden="true">
      <g class="nx-orb o1"><circle cx="100" cy="100" r="88" fill="none" stroke="#3ad6ff" stroke-opacity=".35" stroke-dasharray="2 6"/></g>
      <g class="nx-orb o2"><circle cx="100" cy="100" r="72" fill="none" stroke="#a78bfa" stroke-opacity=".6" stroke-dasharray="40 14 4 14"/></g>
      <g class="nx-orb o3"><polygon points="100,44 148,72 148,128 100,156 52,128 52,72" fill="rgba(58,214,255,.06)" stroke="#9ef3ff" stroke-width="1.2"/></g>
      <circle cx="100" cy="100" r="18" fill="rgba(158,243,255,.25)" stroke="#9ef3ff"/><circle cx="100" cy="100" r="6" fill="#9ef3ff"/>
      ${SYSTEMS.map((_, i) => { const a = (i / SYSTEMS.length) * Math.PI * 2 - Math.PI / 2; return `<circle class="nx-node" style="animation-delay:${i * 0.25}s" cx="${100 + 72 * Math.cos(a)}" cy="${100 + 72 * Math.sin(a)}" r="4" fill="#3ad6ff"/>`; }).join('')}
    </svg>`;
    hero.append(h('div', { class: 'nx-hero-txt' }, el('b', null, 'NEXUS'), el('span', null, 'AURA · EXTENDED SYSTEMS · ALL ONLINE')));

    const grid = h('div', { class: 'nx-grid' });
    SYSTEMS.forEach(([tool, name, ic, code, text], i) => {
      const card = h('button', { class: 'nx-card', style: `animation-delay:${i * 50}ms`, 'aria-label': name },
        h('div', { class: 'nx-card-top' }, h('span', { class: 'nx-card-ic' }, icon(ic)), el('span', 'nx-card-code', code)),
        el('b', null, name), el('p', null, text));
      card.onclick = () => { haptics.tap(); ctx.openTool(tool); };
      grid.append(card);
    });

    const tips = h('div', { class: 'nx-tip' },
      el('b', null, 'SAY IT'), el('span', null, '"Bring up a human heart" · "open radar" · "make me a pie chart of…" · "record this lecture" · "run study protocol"'));
    const wrap = h('div', { class: 'nx-scroll' }, hero, grid, tips);
    body.append(wrap);
  });
}
