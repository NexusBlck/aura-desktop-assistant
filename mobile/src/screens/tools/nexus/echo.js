// ECHO - a recorder with a brain. Records a lecture / meeting / conversation in ~25 s pieces, each one
// transcribed on the PC as you go; when you stop, AURA writes the notes (summary, key points, likely exam
// points, flashcards, terms, to-dos). Everything is kept in Documents\AURA Echo - and you can ask
// questions about one recording or all of them ("what did Dr. Khan say about beta blockers?").
import { h, el, icon, toast, button, confirm, loading, empty } from '../../../ui/components.js';
import * as haptics from '../../../platform/haptics.js';
import * as audio from '../../../platform/audio.js';
import { keepAwake } from '../../../platform/sensors.js';
import { fmtDuration, whenOf } from '../../../core/format.js';
import { stage, toB64, chips, micError, say } from './common.js';

const KINDS = [['lecture', 'LECTURE'], ['meeting', 'MEETING'], ['conversation', 'TALK']];

export function open(ctx, arg) {
  stage('ECHO', 'SYS-03', (body, page, onCleanup) => {
    const wrap = h('div', { class: 'nx-scroll' });
    body.append(wrap);
    let rec = null;               // the recording in progress
    onCleanup(() => { if (rec) rec.stop(true); });

    // ---------------------------------------------------------------- home: start + list + ask all
    async function home() {
      wrap.innerHTML = '';
      let kind = 'lecture';
      const title = h('input', { class: 'input', placeholder: 'Title (optional) - e.g. Pharmacology: beta blockers', maxlength: 80 });
      const k = chips(KINDS, kind, (v) => { kind = v; });
      const recBtn = h('button', { class: 'nx-rec-big', 'aria-label': 'Start recording' }, h('span'), 'START RECORDING');
      recBtn.onclick = () => { haptics.tap(); record(kind, title.value.trim()); };
      wrap.append(h('div', { class: 'nx-panel' }, el('div', 'nx-label', 'NEW RECORDING'), k.node, title, recBtn,
        el('p', 'nx-note', 'Keep AURA open on screen while it records (the screen stays on). Each piece is transcribed on your PC as you go.')));

      const q = h('input', { class: 'input', placeholder: 'Ask all your recordings…', enterkeyhint: 'search' });
      const out = h('div');
      const ask = async () => {
        if (!q.value.trim()) return;
        out.replaceChildren(loading('Searching your recordings…'));
        try { const r = await ctx.api.nexus('echo/ask', { question: q.value.trim() }); showAnswer(out, r); } catch (e) { out.replaceChildren(el('div', 'err', e.message)); }
      };
      q.onkeydown = (e) => { if (e.key === 'Enter') ask(); };
      wrap.append(h('div', { class: 'nx-panel' }, el('div', 'nx-label', 'ASK ECHO'), h('div', { class: 'nx-row' }, q, button('ASK', ask, 'solid')), out));

      const list = h('div', {}, loading('Loading recordings…'));
      wrap.append(el('div', 'nx-label', 'RECORDINGS'), list);
      try {
        const r = await ctx.api.nexus('echo');
        list.innerHTML = '';
        if (!r.items.length) list.append(empty('Nothing recorded yet.'));
        for (const it of r.items) {
          const row = h('button', { class: 'nx-item' },
            h('div', {}, el('b', null, it.title), el('span', null, `${whenOf(it.started)} · ${fmtDuration(it.duration)} · ${it.words} words${it.done ? '' : ' · no notes yet'}`)),
            icon('chev'));
          row.onclick = () => detail(it.id);
          list.append(row);
        }
      } catch (e) { list.replaceChildren(el('div', 'err', e.message)); }
    }

    function showAnswer(out, r) {
      out.replaceChildren(h('div', { class: 'nx-answer' }, el('p', null, r.answer || r.error || 'No answer.'),
        (r.sources || []).length ? el('div', 'nx-src', '◆ ' + r.sources.join('  ◆ ')) : null));
      say(ctx, r.answer);
    }

    // ---------------------------------------------------------------- recording
    async function record(kind, title) {
      let info;
      try { info = await ctx.api.nexus('echo/start', { kind, title }); } catch (e) { toast('ECHO', e.message, 'error'); return; }
      wrap.innerHTML = '';
      const timer = el('div', 'nx-rec-timer', '00:00');
      const bars = h('div', { class: 'nx-rec-bars' }, Array.from({ length: 32 }, () => el('i')));
      const live = h('div', { class: 'nx-rec-live' }, el('p', 'nx-note', 'Listening… the transcript appears here every ~25 seconds.'));
      const queueInfo = el('span', 'nx-note');
      const pauseBtn = button('PAUSE', () => togglePause());
      const stopBtn = button('STOP & WRITE NOTES', () => finish(), 'solid');
      wrap.append(h('div', { class: 'nx-panel nx-recording' }, h('div', { class: 'nx-rec-head' }, h('span', { class: 'nx-rec-dot' }), el('b', null, info.title)),
        timer, bars, h('div', { class: 'row-btns' }, pauseBtn, stopBtn), queueInfo), el('div', 'nx-label', 'LIVE TRANSCRIPT'), live);

      const R = { id: info.id, t: 0, start: Date.now(), elapsed: 0, paused: false, queue: [], sending: false, mic: null, done: false };
      const release = keepAwake();
      let lvl = 0, firstText = true;
      const tick = setInterval(() => {
        if (!R.paused) R.elapsed += 0.25;
        timer.textContent = fmtDuration(R.elapsed);
        const kids = bars.children;
        for (let i = 0; i < kids.length - 1; i++) kids[i].style.height = kids[i + 1].style.height;
        kids[kids.length - 1].style.height = `${Math.max(4, Math.round((R.paused ? 0 : lvl) * 100))}%`;
        queueInfo.textContent = R.blocked && !R.paused ? 'Not hearing anything - tap the screen once so your device turns the microphone on.'
          : R.queue.length ? `${R.queue.length} piece(s) waiting to be transcribed` : '';
      }, 250);

      async function pump() {
        if (R.sending) return;
        R.sending = true;
        while (R.queue.length) {
          const job = R.queue[0];
          let ok = false;
          for (let attempt = 0; attempt < 3 && !ok; attempt++) {
            try {
              const r = await ctx.api.nexus('echo/chunk', { id: R.id, audio: job.b64, mime: 'audio/wav', t: job.t }, 60000);
              ok = true;
              if (r.text) {
                if (firstText) { live.innerHTML = ''; firstText = false; }
                live.append(h('p', {}, el('span', 'nx-ts', fmtDuration(job.t)), ' ', r.text));
                live.lastChild.scrollIntoView({ block: 'end', behavior: 'smooth' });
              }
            } catch (e) { await new Promise((res) => setTimeout(res, 1500 * (attempt + 1))); }
          }
          R.queue.shift();
        }
        R.sending = false;
      }

      try {
        R.mic = await audio.listen({
          continuous: true, minSeconds: 22, maxSeconds: 30,
          onLevel: (l) => { lvl = lvl * 0.7 + l * 0.3; },
          onBlocked: (b) => { R.blocked = b; },
          onUtterance: async (blob, dur) => {
            const t = R.t; R.t += dur;
            R.queue.push({ t, b64: await toB64(blob) });
            pump();
          },
        });
      } catch (e) { micError(e); clearInterval(tick); release(); home(); return; }

      function togglePause() {
        R.paused = !R.paused;
        if (R.paused) { R.mic.flush(); R.mic.pause(); } else R.mic.resume();
        pauseBtn.textContent = R.paused ? 'RESUME' : 'PAUSE';
        wrap.querySelector('.nx-recording').classList.toggle('paused', R.paused);
      }

      async function stop(silent) {
        if (R.done) return;
        R.done = true;
        clearInterval(tick); release();
        R.mic.flush(); R.mic.stop();
        rec = null;
        await new Promise((res) => setTimeout(res, 300));         // the last piece is being encoded
        while (R.queue.length || R.sending) { await pump(); await new Promise((res) => setTimeout(res, 300)); }
        if (silent) ctx.api.nexus('echo/finish', { id: R.id }, 180000).catch(() => {});      // page closed: notes are still written
      }

      async function finish() {
        haptics.success();
        wrap.innerHTML = '';
        wrap.append(loading('Transcribing the last piece and writing your notes…'));
        await stop(false);
        try {
          const r = await ctx.api.nexus('echo/finish', { id: R.id }, 180000);
          if (r.error) { toast('ECHO', r.error, 'error'); home(); return; }
          detail(R.id, r);
        } catch (e) { toast('ECHO', e.message, 'error'); home(); }
      }
      rec = { stop };
    }

    // ---------------------------------------------------------------- one recording
    async function detail(id, data) {
      wrap.innerHTML = '';
      wrap.append(loading());
      try { data = data || await ctx.api.nexus(`echo?id=${encodeURIComponent(id)}`); } catch (e) { wrap.replaceChildren(el('div', 'err', e.message)); return; }
      wrap.innerHTML = '';
      const n = data.notes || {};
      const backBtn = h('button', { class: 'nx-link' }, '‹ ALL RECORDINGS');
      backBtn.onclick = home;
      wrap.append(backBtn, h('h2', { class: 'nx-title' }, data.title), el('div', 'nx-note', whenOf(data.started)));

      if (!data.notes) {
        wrap.append(h('div', { class: 'nx-panel' }, el('p', null, "Notes haven't been written for this one yet."),
          button('WRITE NOTES NOW', async () => { const r = await ctx.api.nexus('echo/finish', { id }, 180000); detail(id, r.error ? null : r); }, 'solid')));
      }
      const sec = (label, ...kids) => wrap.append(h('div', { class: 'nx-panel' }, el('div', 'nx-label', label), ...kids));
      if (n.summary) sec('SUMMARY', el('p', null, n.summary));
      const list = (items) => h('ul', { class: 'nx-ul' }, items.map((x) => el('li', null, x)));
      if ((n.key_points || []).length) sec('KEY POINTS', list(n.key_points));
      if ((n.exam_points || []).length) sec('LIKELY EXAM POINTS', list(n.exam_points));
      if ((n.decisions || []).length) sec('DECISIONS', list(n.decisions));
      if ((n.action_items || []).length) {
        sec('TO DO', h('div', {}, n.action_items.map((a) => h('div', { class: 'nx-todo' }, el('span', null, a),
          button('+ TASK', async () => { await ctx.api.addTask(a); toast('Added', a, 'info', 2500); })))));
      }
      if ((n.flashcards || []).length) {
        sec('FLASHCARDS', h('div', { class: 'nx-cards' }, n.flashcards.map((c) => {
          const card = h('div', { class: 'vx-card' }, h('div', { class: 'in' }, el('div', 'face', c.q), el('div', 'face back', c.a)));
          card.onclick = () => card.classList.toggle('flip');
          return card;
        })));
      }
      if ((n.terms || []).length) sec('TERMS', h('dl', { class: 'nx-dl' }, n.terms.flatMap((t) => [el('dt', null, t.term), el('dd', null, t.meaning)])));

      const q = h('input', { class: 'input', placeholder: 'Ask about this recording…', enterkeyhint: 'search' });
      const out = h('div');
      const ask = async () => {
        if (!q.value.trim()) return;
        out.replaceChildren(loading());
        try { showAnswer(out, await ctx.api.nexus('echo/ask', { id, question: q.value.trim() })); } catch (e) { out.replaceChildren(el('div', 'err', e.message)); }
      };
      q.onkeydown = (e) => { if (e.key === 'Enter') ask(); };
      sec('ASK', h('div', { class: 'nx-row' }, q, button('ASK', ask, 'solid')), out);

      const tr = h('div', { class: 'nx-rec-live', hidden: true }, (data.segments || []).map((s) => h('p', {}, el('span', 'nx-ts', fmtDuration(s.t)), ' ', s.text)));
      const toggle = button(`TRANSCRIPT (${(data.segments || []).length} PARTS)`, () => { tr.hidden = !tr.hidden; });
      wrap.append(toggle, tr);
      wrap.append(h('div', { class: 'row-btns', style: 'margin-top:14px' }, button('DELETE RECORDING', async () => {
        if (!(await confirm('Delete recording?', `"${data.title}" and its notes will be removed from your PC.`, 'DELETE', true))) return;
        await ctx.api.nexus('echo/delete', { id }); home();
      }, 'danger')));
    }

    if (arg && arg.id) detail(arg.id); else home();
  });
}
