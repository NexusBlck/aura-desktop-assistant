// Log a session (the HUD's "Log Gym / BJJ / Judo / Study / Coding" dialog): when + how it went.
import { el, h, openSheet, button, toast } from '../../ui/components.js';
import * as haptics from '../../platform/haptics.js';

const TYPES = ['gym', 'bjj', 'judo', 'study', 'coding'];

function localIso(d) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function open(ctx, type) {
  openSheet('LOG A SESSION', (body, sheet) => {
    let kind = TYPES.includes(type) ? type : (type || 'gym');
    let rating = 7;
    const types = h('div', { class: 'chips' });
    const paintTypes = () => types.querySelectorAll('.chip').forEach((b) => b.classList.toggle('warn', b.dataset.t === kind));
    for (const t of TYPES) {
      const b = h('button', { class: 'chip', 'data-t': t }, t.toUpperCase());
      b.onclick = () => { kind = t; other.value = ''; paintTypes(); haptics.tap(); };
      types.append(b);
    }
    const other = h('input', { class: 'input', placeholder: 'or something else, e.g. running', maxlength: '30' });
    other.oninput = () => { if (other.value.trim()) { kind = other.value.trim(); paintTypes(); } };
    const when = h('input', { class: 'input', type: 'datetime-local', value: localIso(new Date()), max: localIso(new Date(Date.now() + 864e5)) });
    const rlabel = el('b', null, '7 / 10');
    const slider = h('input', { type: 'range', min: '1', max: '10', value: '7', style: 'width:100%;accent-color:var(--cyan);min-height:44px', 'aria-label': 'How much you enjoyed it, 1 to 10' });
    slider.oninput = () => { rating = +slider.value; rlabel.textContent = `${rating} / 10`; };
    const save = button('LOG IT', async () => {
      const ts = when.value ? new Date(when.value) : new Date();
      try {
        const r = await ctx.api.logSession(kind, localIso(ts) + ':00', rating);
        haptics.success(); toast('Logged', r.message, 'focus', 4000); sheet.close(); ctx.refresh();
      } catch (e) { toast("Couldn't log it", e.message, 'error'); }
    }, 'solid block');
    body.append(h('div', { class: 'field' }, el('label', null, 'WHAT'), types, other),
      h('div', { class: 'field' }, el('label', null, 'WHEN'), when),
      h('div', { class: 'field' }, h('label', {}, 'HOW MUCH DID YOU ENJOY IT? ', rlabel), slider), save);
    paintTypes();
  });
}
