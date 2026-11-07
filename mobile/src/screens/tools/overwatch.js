// OVERWATCH (SYS-10) - command of your PC from anywhere: AEGIS guard (arm it as you leave; anyone who touches the PC is
// photographed, locked out and reported here), TOTAL RECALL (ask about anything that was on your screen, scrub back through
// your day), AIR CONTROL (start hand control on the PC) and PC POWER (lock, sleep, volume, media, screenshot).
import { h, el, toast, button, confirm, loading } from '../../ui/components.js';
import * as haptics from '../../platform/haptics.js';
import { stage, say } from './nexus/common.js';

const fmt = (t) => new Date(t * 1000).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' });

export function open(ctx) {
  stage('OVERWATCH', 'SYS-10', (body, page, onCleanup) => {
    const S = { alive: true, st: null, day: null, entries: [] };
    onCleanup(() => { S.alive = false; clearInterval(S.timer); offEv?.(); });
    const aegisBox = h('div', { class: 'ow-card ow-aegis' });
    const recallBox = h('div', { class: 'ow-card' });
    const airBox = h('div', { class: 'ow-card' });
    const pcBox = h('div', { class: 'ow-card' });
    body.append(h('div', { class: 'nx-scroll ow' }, el('div', 'nx-label', 'AEGIS · GUARD MODE'), aegisBox, el('div', 'nx-label', 'TOTAL RECALL · SCREEN MEMORY'), recallBox,
      el('div', 'nx-label', 'AIR CONTROL'), airBox, el('div', 'nx-label', 'PC POWER'), pcBox));

    const api = (op, b) => ctx.api.overdrive(op, b);
    async function refresh() {
      try { S.st = await api('status'); } catch (e) { aegisBox.replaceChildren(el('div', 'err', e.message)); return; }
      if (!S.alive) return;
      renderAegis(); renderRecallHead();
    }
    const offEv = ctx.on ? ctx.on('aegis', () => refresh()) : null;
    S.timer = setInterval(refresh, 8000);

    // ---------------------------------------------------------------- AEGIS
    function renderAegis() {
      const a = S.st.aegis;
      const big = h('button', { class: 'ow-shield' + (a.armed ? ' on' : ''), type: 'button' },
        h('span', { class: 'ow-shield-ring' }), el('b', null, a.armed ? 'ARMED' : 'DISARMED'), el('span', null, a.armed ? `since ${fmt(a.armed_at)} · tap to disarm` : 'tap to arm as you leave'));
      big.onclick = async () => {
        haptics.tap();
        try {
          const r = await api('aegis', { action: a.armed ? 'disarm' : 'arm' });
          if (r.summary) { toast('AEGIS', r.summary, 'info', 6000); say(ctx, r.summary); }
          else if (r.armed) { toast('AEGIS', 'Armed - if anyone touches your PC it locks, takes their photo and alerts you here.', 'info', 5000); }
          refresh();
        } catch (e) { toast('AEGIS', e.message, 'error'); }
      };
      const autoRow = h('div', { class: 'ow-chips' }, el('span', 'ow-k', 'AUTO-ARM'), ...[0, 5, 10, 30].map((m) => {
        const b = h('button', { class: 'ow-chip' + (a.auto_minutes === m ? ' on' : ''), type: 'button' }, m ? `${m} MIN AWAY` : 'OFF');
        b.onclick = async () => { await api('aegis', { action: 'auto', minutes: m }); refresh(); };
        return b;
      }));
      const lockRow = h('div', { class: 'ow-chips' }, el('span', 'ow-k', 'ON TOUCH'), ...[[true, 'LOCK + PHOTO + ALERT'], [false, 'PHOTO + ALERT ONLY']].map(([v, t]) => {
        const b = h('button', { class: 'ow-chip' + (a.lock_on_touch === v ? ' on' : ''), type: 'button' }, t);
        b.onclick = async () => { await api('aegis', { action: 'lock_on_touch', on: v }); refresh(); };
        return b;
      }));
      const evs = [...a.events].reverse().slice(0, 8);
      const log = evs.length ? h('div', { class: 'ow-events' }, evs.map((e) => h('div', { class: 'ow-ev' },
        h('div', { class: 'ow-ev-h' }, el('b', null, '⚠ INTRUSION'), el('span', null, fmt(e.t) + (e.locked ? ' · locked' : ''))),
        h('div', { class: 'ow-ev-ph' }, (e.urls || []).filter(Boolean).map((u) => { const i = h('img', { src: u, alt: 'photo of the person at your PC', loading: 'lazy' }); i.onclick = () => window.open(u, '_blank'); return i; }),
          !(e.urls || []).filter(Boolean).length ? el('span', 'ow-hint', 'no photo (camera busy or missing)') : null)))) : el('div', 'ow-hint', 'No intrusions recorded.');
      aegisBox.replaceChildren(big, autoRow, lockRow, el('div', 'ow-k', 'LOG'), log,
        el('div', 'ow-hint', 'When you come back, just unlock Windows - AEGIS stands down and tells you what happened. Or disarm here before you sit down.'));
    }

    // ---------------------------------------------------------------- TOTAL RECALL
    const ask = h('input', { class: 'input', placeholder: 'What was I working on yesterday at 3?', enterkeyhint: 'search' });
    const answer = h('div', { class: 'ow-answer', hidden: true });
    const timeline = h('div', { class: 'ow-timeline' });
    ask.onkeydown = (e) => { if (e.key === 'Enter' && ask.value.trim()) search(ask.value.trim()); };
    function renderRecallHead() {
      const r = S.st.recall;
      const tog = h('button', { class: 'ow-toggle' + (r.on ? ' on' : ''), type: 'button' }, el('b', null, r.on ? (r.paused ? 'PAUSED' : 'REMEMBERING') : 'OFF'),
        el('span', null, r.on ? `${r.today} moments today · ${r.size_mb} MB · keeps ${r.keep_days} days` : 'tap to let AURA remember your screen'));
      tog.onclick = async () => {
        if (!r.on && !(await confirm('Turn on Total Recall?', 'AURA will keep small screenshots of your PC screen while you use it - only on your PC, never password, banking or private windows, deleted after ' + r.keep_days + ' days. You can pause or delete it any time.', 'TURN ON'))) return;
        await api('recall', { action: r.on ? 'off' : 'on' }); refresh();
      };
      const ctl = h('div', { class: 'ow-chips' },
        r.on ? chip(r.paused ? 'RESUME' : 'PAUSE 30 MIN', async () => { await api('recall', { action: r.paused ? 'resume' : 'pause', minutes: 30 }); refresh(); }) : null,
        chip('DELETE TODAY', async () => { if (await confirm('Delete today?', "Today's screen memory will be erased.", 'DELETE', true)) { await api('recall', { action: 'delete', day: 'today' }); refresh(); loadDay(); } }),
        chip('DELETE ALL', async () => { if (await confirm('Delete everything?', 'All screen memory will be erased.', 'DELETE ALL', true)) { await api('recall', { action: 'delete', day: 'all' }); refresh(); loadDay(); } }));
      const days = h('div', { class: 'ow-chips' }, el('span', 'ow-k', 'REWIND'), ...(r.days.slice(-7).reverse()).map((d) => chip(d.slice(5), () => loadDay(d), d === S.day)));
      recallBox.replaceChildren(tog, h('div', { class: 'nx-row', style: 'margin-top:10px' }, ask, button('ASK', () => ask.value.trim() && search(ask.value.trim()), 'solid')), answer, ctl, days, timeline);
    }
    const chip = (label, fn, on) => { const b = h('button', { class: 'ow-chip' + (on ? ' on' : ''), type: 'button' }, label); b.onclick = () => { haptics.tap(); fn(); }; return b; };

    async function search(q) {
      answer.hidden = false; answer.replaceChildren(loading('Looking back through your screen…'));
      try {
        const r = await api('recall', { action: 'search', q });
        answer.replaceChildren(el('p', null, r.answer), h('div', { class: 'ow-moments' }, (r.moments || []).map((m) => h('figure', {},
          m.url ? h('img', { src: m.url, alt: m.title, loading: 'lazy', onclick: () => window.open(m.url, '_blank') }) : null, h('figcaption', {}, `${fmt(m.t)} · ${m.title}`)))));
        say(ctx, r.answer);
      } catch (e) { answer.replaceChildren(el('div', 'err', e.message)); }
    }

    async function loadDay(d) {
      S.day = d || S.day;
      if (!S.day) { timeline.replaceChildren(); return; }
      timeline.replaceChildren(loading());
      try {
        const r = await api(`recall/day?d=${encodeURIComponent(S.day)}`);
        S.entries = r.entries || [];
        if (!S.entries.length) { timeline.replaceChildren(el('div', 'ow-hint', 'Nothing remembered that day.')); return; }
        const img = h('img', { class: 'ow-frame', alt: '' });
        const cap = el('div', 'ow-cap', '');
        const range = h('input', { type: 'range', min: 0, max: S.entries.length - 1, value: S.entries.length - 1, class: 'ow-range' });
        const showAt = (i) => { const e = S.entries[i]; img.src = e.url; cap.textContent = `${new Date(e.t * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' })} · ${e.title || e.app}`; };
        range.oninput = () => showAt(+range.value);
        showAt(S.entries.length - 1);
        timeline.replaceChildren(img, cap, range, el('div', 'ow-hint', `${S.entries.length} moments - drag to rewind ${S.day}`));
      } catch (e) { timeline.replaceChildren(el('div', 'err', e.message)); }
      renderRecallHead();
    }

    // ---------------------------------------------------------------- AIR + PC POWER
    airBox.replaceChildren(el('div', 'ow-hint', 'Control your whole PC with your hands through its webcam: point to move the cursor, pinch to click, pinch-and-move to drag, fist to scroll, two palms for task view / desktop.'),
      button('START AIR CONTROL ON THE PC', async () => { const r = await api('air/open', {}); toast('Air Control', r.say || 'Starting on your PC.', 'info', 5000); }, 'solid'));
    const P = [['lock', '🔒', 'LOCK'], ['sleep', '🌙', 'SLEEP'], ['mute', '🔇', 'MUTE'], ['voldown', '🔉', 'VOL −'], ['volup', '🔊', 'VOL +'],
      ['prev', '⏮', 'PREV'], ['play', '⏯', 'PLAY'], ['next', '⏭', 'NEXT'], ['screenshot', '📸', 'SCREENSHOT'], ['show_desktop', '🖥', 'DESKTOP']];
    pcBox.replaceChildren(h('div', { class: 'ow-power' }, P.map(([a, g, t]) => {
      const b = h('button', { class: 'ow-pw', type: 'button' }, el('b', null, g), el('span', null, t));
      b.onclick = async () => {
        haptics.tap();
        if (a === 'sleep' && !(await confirm('Put the PC to sleep?', 'AURA on the PC stops answering until it wakes up.', 'SLEEP'))) return;
        try { const r = await api('pc', { action: a }); if (r.error) throw new Error(r.error); if (r.url) window.open(r.url, '_blank'); else toast('PC', t + ' ✓', 'info', 1500); } catch (e) { toast('PC', e.message, 'error'); }
      };
      return b;
    })));

    refresh().then(() => { const d = S.st?.recall?.days?.slice(-1)[0]; if (d) loadDay(d); });
  });
}
