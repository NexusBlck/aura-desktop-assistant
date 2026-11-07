// Turns a structured AI result (MED lens, comparisons...) into readable sections - any shape of JSON.
import { el, h } from '../ui/components.js';

const LABELS = {
  strength: 'STRENGTH', form: 'FORM', class: 'DRUG CLASS', uses: 'USED FOR', how_it_works: 'HOW IT WORKS', typical_dose: 'TYPICAL DOSE',
  side_effects: 'SIDE EFFECTS', interactions: 'INTERACTIONS', warnings: 'WARNINGS', confidence: 'CONFIDENCE', rate: 'RATE', rhythm: 'RHYTHM',
  axis: 'AXIS', pr: 'PR', qrs: 'QRS', qt: 'QT', st_t: 'ST / T', findings: 'FINDINGS', impression: 'IMPRESSION', differentials: 'DIFFERENTIALS',
  next_steps: 'NEXT STEPS', systematic: 'SYSTEMATIC REVIEW', tests: 'RESULTS', abnormal: 'OUT OF RANGE', pattern: 'WHAT IT SUGGESTS',
  questions_for_doctor: 'ASK YOUR DOCTOR', description: 'DESCRIPTION', features: 'FEATURES', red_flags: 'RED FLAGS', advice: 'ADVICE',
  differences: 'DIFFERENCES', similarities: 'SIMILARITIES', disclaimer: '',
};
const SHORT = new Set(['strength', 'form', 'class', 'rate', 'rhythm', 'axis', 'pr', 'qrs', 'qt', 'st_t', 'confidence']);

/** Sections for every field of `data` (skipping `skip`). Strings → text, lists → bullet rows, test tables → rows. */
export function renderFields(data, skip = []) {
  const out = [];
  if (!data || typeof data !== 'object') return out;
  const shortRows = [];
  for (const [k, v] of Object.entries(data)) {
    if (skip.includes(k) || v == null || v === '' || (Array.isArray(v) && !v.length)) continue;
    const label = LABELS[k] ?? k.replace(/_/g, ' ').toUpperCase();
    if (SHORT.has(k) && typeof v !== 'object') {
      shortRows.push(h('div', { class: 'kv' }, el('span', null, label), el('b', { style: k === 'confidence' ? `color:${v === 'high' ? 'var(--green)' : v === 'low' ? 'var(--red)' : 'var(--amber)'}` : '' }, String(v))));
      continue;
    }
    if (Array.isArray(v)) {
      out.push(el('div', 'lbl', label));
      for (const item of v) {
        if (item && typeof item === 'object') {       // e.g. lab tests {name, value, range, flag}
          const flag = String(item.flag || '').toLowerCase();
          out.push(h('div', { class: 'kv' }, el('span', null, item.name || item.label || ''),
            el('b', { style: flag === 'high' || flag === 'low' ? 'color:var(--amber)' : '' }, [item.value, item.range ? `(${item.range})` : '', flag && flag !== 'normal' ? '▲▼'[flag === 'low' ? 1 : 0] : ''].filter(Boolean).join(' '))));
        } else out.push(h('p', { style: 'margin:0 0 6px' }, '◆ ' + item));
      }
      continue;
    }
    if (k === 'disclaimer') { out.push(h('p', { style: 'font-size:.8rem;color:var(--text-dim);margin-top:12px' }, '⚕ ' + v)); continue; }
    out.push(el('div', 'lbl', label), h('p', {}, String(v)));
  }
  return [...shortRows, ...out];
}
