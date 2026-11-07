// PROTOCOLS - one word runs a whole chain of AURA commands ("run study protocol"), and MISSION:
// tell AURA a goal, it plans the steps with what it can actually do, you check them, it carries them out.
// Steps run one after another through AURA's normal brain on the PC (nexus.py), with live progress here.
import { h, el, icon, toast, button, openSheet, confirm, loading, empty } from '../../../ui/components.js';
import * as haptics from '../../../platform/haptics.js';
import * as audio from '../../../platform/audio.js';
import { stage, micError } from './common.js';

export function open(ctx) {
  stage('PROTOCOLS', 'SYS-07', (body, page, onCleanup) => {
    const wrap = h('div', { class: 'nx-scroll' });
    body.append(wrap);

    // ---------------------------------------------------------------- live progress
    const runBox = h('div', { class: 'nx-run', hidden: true });
    const runName = el('b'), runStep = el('span'), runBar = el('i');
    const stopBtn = button('ABORT', async () => { await ctx.api.nexus('protocols/stop', {}); }, 'danger');
    runBox.append(h('div', { class: 'nx-run-head' }, h('span', { class: 'nx-rec-dot' }), runName, stopBtn), runStep, h('div', { class: 'bar' }, runBar));
    const paint = (ev) => {
      if (!ev) return;
      const active = ev.state === 'start' || ev.state === 'step';
      runBox.hidden = false;
      runBox.classList.toggle('done', !active);
      runName.textContent = `${ev.name.toUpperCase()} PROTOCOL`;
      runStep.textContent = ev.state === 'step' ? `STEP ${ev.step}/${ev.total} · ${ev.text}` : ev.state === 'done' ? 'COMPLETE' : ev.state === 'stopped' ? 'ABORTED' : 'ENGAGING…';
      runBar.style.width = `${(ev.step / Math.max(1, ev.total)) * 100}%`;
      stopBtn.hidden = !active;
    };
    onCleanup(ctx.store.on('protocol', paint));

    // ---------------------------------------------------------------- mission
    const goal = h('textarea', { class: 'textarea nx-goal', placeholder: 'Give AURA a goal…\ne.g. "get me ready for my cardiology exam on Friday"', rows: 3 });
    const micBtn = h('button', { class: 'iconbtn', 'aria-label': 'Say the goal' }, icon('mic'));
    let rec = null;
    micBtn.onclick = async () => {
      if (rec) { rec.stop(); return; }
      try { rec = await audio.record({ maxSeconds: 25 }); } catch (e) { micError(e); return; }
      micBtn.classList.add('on');
      const blob = await rec.done; rec = null; micBtn.classList.remove('on');
      if (!blob) return;
      try { const r = await ctx.api.transcribe(blob); if (r.text) goal.value = r.text; } catch (e) { toast('Mission', e.message, 'error'); }
    };
    const planBtn = button('PLAN MISSION', () => plan(), 'solid');

    async function plan() {
      if (!goal.value.trim()) { goal.focus(); return; }
      let r;
      try { r = await ctx.api.nexus('mission', { goal: goal.value.trim() }, 90000); } catch (e) { toast('Mission', e.message, 'error'); return; }
      if (r.error || !(r.steps || []).length) { toast('Mission', r.error || "AURA couldn't plan that - try saying it differently.", 'error'); return; }
      haptics.success();
      openSheet(`MISSION · ${r.title.toUpperCase()}`, (b, sheet) => {
        const rows = r.steps.map((s, i) => {
          const cb = h('input', { type: 'checkbox', checked: true });
          const txt = h('input', { class: 'input', value: s.command });
          b.append(h('label', { class: 'nx-step' }, cb, h('div', {}, h('span', { class: 'nx-step-n' }, `${String(i + 1).padStart(2, '0')}`), txt, el('em', null, s.why || ''))));
          return { cb, txt };
        });
        if (r.note) b.append(el('p', 'nx-note', '⚠ ' + r.note));
        const chosen = () => rows.filter((x) => x.cb.checked && x.txt.value.trim()).map((x) => x.txt.value.trim());
        b.append(h('div', { class: 'row-btns', style: 'margin-top:12px' },
          button('SAVE AS PROTOCOL', async () => { await save({ name: r.title, steps: chosen(), icon: 'target' }); sheet.close(); }),
          button('ENGAGE', async () => { const steps = chosen(); if (!steps.length) return; await run({ name: r.title, steps }); sheet.close(); }, 'solid')));
      });
    }

    // ---------------------------------------------------------------- protocols
    const list = h('div');
    async function load() {
      list.replaceChildren(loading());
      try {
        const r = await ctx.api.nexus('protocols');
        list.innerHTML = '';
        if (!r.items.length) list.append(empty('No protocols yet.'));
        for (const p of r.items) {
          const engage = h('button', { class: 'nx-engage', 'aria-label': `Engage ${p.name}` }, icon('play'));
          engage.onclick = (e) => { e.stopPropagation(); run({ id: p.id, name: p.name }); };
          const card = h('div', { class: 'nx-proto' }, h('div', { class: 'nx-proto-ic' }, icon(p.icon && ['bolt', 'moon', 'target', 'heart', 'globe'].includes(p.icon) ? p.icon : 'bolt')),
            h('div', { class: 'nx-proto-txt' }, el('b', null, p.name.toUpperCase()), el('span', null, p.steps.join(' → '))), engage);
          card.onclick = () => edit(p);
          list.append(card);
        }
      } catch (e) { list.replaceChildren(el('div', 'err', e.message)); }
    }

    async function run(body) {
      haptics.warning();
      try {
        const r = await ctx.api.nexus('protocols/run', body.steps ? { name: body.name, steps: body.steps } : { id: body.id });
        if (r.error) toast('Protocol', r.error, 'error');
      } catch (e) { toast('Protocol', e.message, 'error'); }
    }

    async function save(p) {
      const r = await ctx.api.nexus('protocols/save', p);
      if (r.error) { toast('Protocol', r.error, 'error'); return false; }
      toast('Saved', `Say "run ${r.protocol.name} protocol" any time.`, 'info', 4000);
      load();
      return true;
    }

    function edit(p = { name: '', steps: [] }) {
      openSheet(p.id ? 'EDIT PROTOCOL' : 'NEW PROTOCOL', (b, sheet) => {
        const name = h('input', { class: 'input', value: p.name, placeholder: 'Name - e.g. Gym', maxlength: 40 });
        const steps = h('textarea', { class: 'textarea', placeholder: 'One command per line, as you\'d say it to AURA:\nturn on do not disturb\nplay workout music on spotify\nstart a 45 minute focus session' }, p.steps.join('\n'));
        b.append(h('div', { class: 'field' }, el('label', null, 'NAME'), name), h('div', { class: 'field' }, el('label', null, 'STEPS'), steps),
          h('div', { class: 'row-btns' },
            p.id ? button('DELETE', async () => {
              if (!(await confirm('Delete protocol?', `"${p.name}" will be removed.`, 'DELETE', true))) return;
              await ctx.api.nexus('protocols/delete', { id: p.id }); sheet.close(); load();
            }, 'danger') : null,
            button('SAVE', async () => { if (await save({ id: p.id, name: name.value, icon: p.icon, steps: steps.value.split('\n') })) sheet.close(); }, 'solid')));
      });
    }

    wrap.append(runBox,
      h('div', { class: 'nx-panel nx-mission' }, el('div', 'nx-label', 'MISSION'), h('div', { class: 'nx-row top' }, goal, micBtn), planBtn,
        el('p', 'nx-note', 'AURA breaks the goal into steps it can really do, you approve them, then it runs them one by one.')),
      h('div', { class: 'nx-label-row' }, el('div', 'nx-label', 'PROTOCOLS'), h('button', { class: 'nx-link', onclick: () => edit() }, '+ NEW')),
      list, el('p', 'nx-note', 'Tip: say "run study protocol" (or "engage night protocol") to AURA on the phone or the PC.'));
    paint(ctx.store.get('protocol'));
    load();
  });
}
