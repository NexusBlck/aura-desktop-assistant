// BABEL - a live interpreter. Put the phone between two people: the top half faces them (upside down),
// the bottom half faces you. Each of you just talks in your own language; AURA works out who spoke,
// translates, shows it on the other person's side and says it in a native voice.
import { h, el, icon, toast } from '../../../ui/components.js';
import * as audio from '../../../platform/audio.js';
import * as haptics from '../../../platform/haptics.js';
import * as storage from '../../../platform/storage.js';
import { keepAwake } from '../../../platform/sensors.js';
import { stage, toB64, playReply, micError } from './common.js';

const LANGS = ['English', 'Urdu', 'Arabic', 'Hindi', 'French', 'Spanish', 'German', 'Chinese', 'Japanese', 'Korean', 'Turkish',
  'Persian', 'Russian', 'Italian', 'Portuguese', 'Bengali', 'Malay', 'Indonesian'];
const RTL = new Set(['Urdu', 'Arabic', 'Persian']);

export function open(ctx) {
  stage('BABEL', 'SYS-02', (body, page, onCleanup) => {
    const S = { a: storage.get('nx.babel.a', 'English'), b: storage.get('nx.babel.b', 'Urdu'), mic: null, alive: true, busy: false, paused: false, log: [] };

    function half(side) {
      const sel = h('select', { class: 'nx-lang', 'aria-label': 'Language' }, LANGS.map((l) => h('option', { value: l }, l)));
      sel.value = S[side];
      sel.onchange = () => { S[side] = sel.value; storage.set(`nx.babel.${side}`, sel.value); big.dir = RTL.has(sel.value) ? 'rtl' : 'ltr'; };
      const big = el('div', 'nx-babel-big', side === 'a' ? 'Talk in your language - I\'ll translate for them.' : 'Speak in your language - I will translate.');
      const small = el('div', 'nx-babel-small');
      const roman = el('div', 'nx-babel-roman');
      big.dir = RTL.has(S[side]) ? 'rtl' : 'ltr';
      const node = h('div', { class: `nx-babel-half ${side === 'b' ? 'flip' : ''}` }, h('div', { class: 'nx-babel-head' }, el('span', null, side === 'a' ? 'YOU' : 'THEM'), sel), big, roman, small);
      return { node, big, small, roman, sel };
    }
    const A = half('a'), B = half('b');
    const level = el('i');
    const state = el('span', null, 'STARTING');
    const pauseBtn = h('button', { class: 'nx-babel-btn', 'aria-label': 'Pause' }, icon('mic'));
    const swapBtn = h('button', { class: 'nx-babel-btn', 'aria-label': 'Swap languages' }, icon('globe'));
    const mid = h('div', { class: 'nx-babel-mid' }, swapBtn, h('div', { class: 'nx-babel-meter' }, level), state, pauseBtn);
    body.append(h('div', { class: 'nx-babel' }, B.node, mid, A.node));

    swapBtn.onclick = () => {
      haptics.tap();
      [S.a, S.b] = [S.b, S.a]; A.sel.value = S.a; B.sel.value = S.b;
      storage.set('nx.babel.a', S.a); storage.set('nx.babel.b', S.b);
    };
    pauseBtn.onclick = () => {
      haptics.tap();
      S.paused = !S.paused; pauseBtn.classList.toggle('on', S.paused);
      if (S.mic) S.paused ? S.mic.pause() : S.mic.resume();
      state.textContent = S.paused ? 'PAUSED' : 'LISTENING';
    };

    async function onUtterance(blob) {
      if (S.busy || !S.alive) return;
      S.busy = true; S.mic.pause(); state.textContent = 'TRANSLATING';
      try {
        const r = await ctx.api.nexus('babel', { audio: await toB64(blob), mime: 'audio/wav', a: S.a, b: S.b }, 60000);
        if (r.heard && r.translation && S.alive) {
          const speakerSide = r.from === S.a ? A : B, listenerSide = r.from === S.a ? B : A;
          listenerSide.big.textContent = r.translation;
          listenerSide.big.dir = RTL.has(r.to) ? 'rtl' : 'ltr';
          listenerSide.roman.textContent = r.romanized || '';
          listenerSide.small.textContent = '';
          speakerSide.small.textContent = '✓ ' + r.original;
          listenerSide.node.classList.remove('pop'); void listenerSide.node.offsetWidth; listenerSide.node.classList.add('pop');
          haptics.success();
          state.textContent = 'SPEAKING';
          await playReply(r);
        }
      } catch (e) { toast('BABEL', e.message, 'error', 3500); }
      S.busy = false;
      if (S.alive && !S.paused) { S.mic.resume(); state.textContent = 'LISTENING'; }
    }

    (async () => {
      try {
        S.mic = await audio.listen({ onUtterance, maxSeconds: 25, silenceMs: 1000, onLevel: (l) => { level.style.width = `${Math.round(l * 100)}%`; },
          onBlocked: (b) => { if (S.busy || S.paused) return; state.textContent = b ? 'TAP SCREEN TO HEAR' : 'LISTENING'; mid.classList.toggle('blocked', b); } });
        if (!S.alive) { S.mic.stop(); return; }
        state.textContent = 'LISTENING';
      } catch (e) { micError(e); state.textContent = 'NO MIC'; }
    })();
    const release = keepAwake();
    onCleanup(() => { S.alive = false; release(); if (S.mic) S.mic.stop(); audio.stop(); });
  });
}
