// CDC WONDER - the PC's extractor window on the phone: set up conditions / ICD-10 codes / ages,
// start the extraction (it runs on the PC, one query every 2 minutes as CDC asks), follow progress,
// stop it, build the Excel workbooks and open them on the phone.
import { el, h, openPage, panel, button, empty, loading, toast, confirm } from '../../ui/components.js';
import * as haptics from '../../platform/haptics.js';
import { openViewer } from './files.js';

export function open(ctx) {
  let poll = null;
  const page = openPage('CDC WONDER', async (body) => {
    body.append(loading('Loading…'));
    let setup;
    try { setup = await ctx.api.cdc('setup'); } catch (e) { body.innerHTML = ''; body.append(empty(e.message)); return; }
    const st = setup.status;
    if (st && ['running', 'api_done'].includes(st.state)) showRunning(st); else showSetup();

    // ---------------------------------------------------------------- setup
    function showSetup() {
      clearInterval(poll);
      body.innerHTML = '';
      const pre = setup.prefill || {};
      const row = (label, ph1, ph2, v = {}) => {
        const name = h('input', { class: 'input', placeholder: ph1, value: v.name || '' });
        const codes = h('input', { class: 'input', placeholder: ph2, value: v.codes || '', autocapitalize: 'characters', style: 'margin-top:6px' });
        const note = el('div', 'help');
        codes.addEventListener('blur', async () => {
          if (!codes.value.trim()) { note.textContent = ''; return; }
          try {
            const r = await ctx.api.cdc('check', { text: codes.value });
            note.textContent = r.errors && r.errors.length ? `Not recognised: ${r.errors.join(', ')}` : `✓ ${r.codes.length} code group(s): ${r.codes.join(', ')}`;
            note.style.color = r.errors && r.errors.length ? 'var(--red)' : 'var(--green)';
          } catch (e) { note.textContent = e.message; }
        });
        return { node: h('div', { class: 'field' }, el('label', null, label), name, codes, note), name, codes };
      };
      const conds = pre.conditions || [];          // filled in when you asked by voice ("extract CDC data for heart failure I50…")
      const A = row('CONDITION A', 'e.g. Heart failure', 'ICD-10 codes, e.g. I50', conds[0]);
      const B = row('CONDITION B (OPTIONAL)', 'e.g. Arthrosis', 'ICD-10 codes, e.g. M15-M19', conds[1]);

      const selected = new Set(pre.ages || []);
      const ages = h('div', { class: 'chips' });
      const paintAges = () => ages.querySelectorAll('.chip').forEach((c) => c.classList.toggle('warn', selected.has(c.dataset.v)));
      for (const a of setup.ages) {
        const c = h('button', { class: 'chip', 'data-v': a.value }, a.label);
        c.onclick = () => { selected.has(a.value) ? selected.delete(a.value) : selected.add(a.value); paintAges(); haptics.tap(); };
        ages.append(c);
      }
      const presets = h('div', { class: 'chips' }, ...[['all', 'ALL AGES'], ['25', 'ADULTS 25+'], ['45', '45+'], ['65', '65+']].map(([p, l]) => {
        const b = h('button', { class: 'chip log' }, l);
        b.onclick = () => { selected.clear(); if (p !== 'all') setup.ages.forEach((a) => { const m = a.value.match(/^(\d+)/); if (m && +m[1] >= +p) selected.add(a.value); }); paintAges(); };
        return b;
      }));
      paintAges();

      let cause = pre.cause === 'underlying' ? 'underlying' : 'multiple';
      const causeBox = h('div', { class: 'chips' });
      const causes = [['multiple', 'Both anywhere on the certificate'], ['underlying', 'A as the underlying cause, B contributing']];
      const paintCause = () => causeBox.querySelectorAll('.chip').forEach((c) => c.classList.toggle('warn', c.dataset.v === cause));
      causes.forEach(([v, l]) => { const c = h('button', { class: 'chip', 'data-v': v, style: 'white-space:normal;text-align:left;min-height:44px' }, l); c.onclick = () => { cause = v; paintCause(); }; causeBox.append(c); });
      paintCause();

      const terms = h('input', { type: 'checkbox', id: 'cdcTerms', style: 'width:24px;height:24px;accent-color:var(--cyan)' });
      const termsBox = setup.consented ? null : panel('CDC DATA USE RESTRICTIONS', 'REQUIRED',
        h('ul', { class: 'help', style: 'padding-left:18px;margin:0 0 10px' },
          h('li', {}, 'Use the data for health statistical reporting and analysis only.'),
          h('li', {}, 'Never present or publish death counts of 9 or fewer, or rates based on them.'),
          h('li', {}, 'Make no attempt to identify any person or establishment, and report any accidental discovery to NCHS.')),
        h('label', { for: 'cdcTerms', style: 'display:flex;gap:10px;align-items:center;min-height:44px' }, terms, "I agree to CDC WONDER's data use restrictions"));
      const err = el('div', 'err');

      const start = button('▶ START EXTRACTION', async () => {
        err.textContent = '';
        const spec = { a: { name: A.name.value.trim(), codes: A.codes.value.trim() }, b: { name: B.name.value.trim(), codes: B.codes.value.trim() },
          ages: [...selected], cause, accept_terms: setup.consented || terms.checked };
        const r = await ctx.api.cdc('start', spec);
        if (r.error) { err.textContent = r.error; haptics.warning(); return; }
        haptics.success();
        showRunning(r.status);
      }, 'solid block');

      body.append(
        h('div', { class: 'help', style: 'margin-bottom:12px' }, 'Leave B empty to study one condition alone. Codes can be single (I50), detailed (I50.9) or ranges (M15-M19).'),
        panel('1 · CONDITIONS & ICD-10 CODES', null, A.node, B.node),
        panel('2 · AGE GROUPS', 'TEN-YEAR', presets, ages, el('div', 'help', 'No groups selected = all ages.')),
        panel('3 · OPTIONS', 'WHEN B IS GIVEN', causeBox),
        ...[termsBox, err, start].filter(Boolean));
    }

    // ---------------------------------------------------------------- running
    function showRunning(status) {
      body.innerHTML = '';
      const prog = el('div', 'big', ''), eta = el('div', 'help'), now = el('div', 'help'), fails = el('div', 'err');
      const barI = el('i'); const bar = h('div', { class: 'bar' }, barI);
      const checklist = h('ul', { class: 'list' }), outputs = h('ul', { class: 'list' });
      const stop = button('■ STOP', async () => {
        if (await confirm('STOP EXTRACTION?', 'Queries already finished are kept.', 'STOP', true)) render(await ctx.api.cdc('stop', {}));
      }, 'danger');
      const build = button('⟳ BUILD EXCEL NOW', async () => { const r = await ctx.api.cdc('build', {}); render(r.status); toast('CDC WONDER', `${(r.outputs || []).length} workbook(s) ready.`, 'focus', 3500); });
      const again = button('+ NEW EXTRACTION', async () => { setup = await ctx.api.cdc('setup'); setup.status = null; showSetup(); });

      body.append(
        panel('A · AUTOMATIC PART · API', 'ON YOUR PC', prog, bar, eta, now, fails,
          el('div', 'help', 'CDC asks automated tools to send one query every 2 minutes, so this runs in the background on your PC - you can close this. AURA tells you when it\'s done.'),
          h('div', { class: 'row-btns', style: 'margin-top:10px' }, stop)),
        panel('B · LOCATION DATA FROM THE WEBSITE', 'ON YOUR PC', el('div', 'help', 'CDC only allows these exports by hand on its website. Do them on your PC - AURA ticks each one off when the file lands in Downloads.'), checklist),
        panel('C · EXCEL WORKBOOKS', 'OUTPUT', outputs, h('div', { class: 'row-btns', style: 'margin-top:10px' }, build, again)));

      function render(s) {
        if (!s) return;
        prog.textContent = `${s.done} / ${s.total} queries`;
        barI.style.width = s.total ? `${(s.done / s.total) * 100}%` : '0%';
        const stateText = { running: 'RUNNING', api_done: 'API PART DONE', stopped: 'STOPPED', failed: 'FAILED' }[s.state] || String(s.state || '').toUpperCase();
        eta.textContent = s.state === 'running' ? `${stateText} · ~${s.eta_minutes} min left${s.next_in ? ` · next query in ${s.next_in}s` : ''}` : stateText;
        now.textContent = s.running ? `Now: ${s.running}` : '';
        fails.textContent = (s.failed || []).join('\n');
        checklist.innerHTML = '';
        (s.checklist || []).forEach((c) => checklist.append(h('li', {}, h('div', { class: 'txt' }, el('div', null, `${c.condition} · ${c.grouping}`), el('div', 'meta', c.dataset)),
          el('span', 'count', c.file ? '✓ ' + c.file : 'WAITING'))));
        if (!(s.checklist || []).length) checklist.append(h('li', {}, empty('No website exports needed.')));
        outputs.innerHTML = '';
        (s.outputs_rel || []).forEach((o) => {
          const li = h('li', { class: 'link', role: 'button' }, el('span', 'kind', 'XLSX'), el('span', 'txt', o.split(/[\\/]/).pop()), el('span', 'chev', '›'));
          li.onclick = () => openViewer(ctx, { kind: 'cdc', path: o, title: o.split(/[\\/]/).pop() });
          outputs.append(li);
        });
        if (!(s.outputs_rel || []).length) outputs.append(h('li', {}, empty('Built when the API part finishes - or tap BUILD EXCEL NOW.')));
      }
      render(status);
      clearInterval(poll);
      poll = setInterval(async () => { try { render(await ctx.api.cdc('status')); } catch (e) { /* PC offline - keep last view */ } }, 5000);
    }
  });
  page.onClose = () => clearInterval(poll);
}
