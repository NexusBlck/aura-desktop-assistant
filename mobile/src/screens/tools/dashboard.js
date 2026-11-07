// Dashboard - the PC's dashboard window: this week, session breakdown, mood / enjoyment trends,
// tasks & papers per week, journal feed, and the full profile (what AURA knows, sessions, tasks, papers).
import { el, h, openPage, panel, button, empty, loading } from '../../ui/components.js';

function barChart(labels, values, { violet = false, max = null, fmt = (v) => v } = {}) {
  const top = max ?? Math.max(1, ...values.map((v) => v || 0));
  const box = h('div', { class: 'bars', role: 'img', 'aria-label': labels.map((l, i) => `${l}: ${values[i] ?? 'none'}`).join(', ') });
  labels.forEach((l, i) => {
    const v = values[i];
    box.append(h('div', { class: 'b' + (violet ? ' v' : '') },
      h('em', {}, v == null ? '' : fmt(v)), h('i', { style: `height:${v == null ? 0 : Math.max(2, (v / top) * 80)}px` }), h('span', {}, l)));
  });
  return box;
}

function hbars(obj) {
  const entries = Object.entries(obj || {}).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return empty('No sessions yet - log one from CORE or say "log a gym session".');
  const top = Math.max(...entries.map((e) => e[1]));
  return h('div', {}, ...entries.map(([k, v]) => h('div', { style: 'margin-bottom:8px' },
    h('div', { class: 'kv', style: 'padding:0' }, el('span', null, k.toUpperCase()), el('b', null, String(v))),
    h('div', { class: 'bar' }, h('i', { style: `width:${(v / top) * 100}%` })))));
}

const list = (items, render, none) => {
  if (!items || !items.length) return empty(none);
  const ul = h('ul', { class: 'list' });
  items.forEach((x) => ul.append(render(x)));
  return ul;
};

export function open(ctx) {
  openPage('DASHBOARD', async (body, page) => {
    let view = 'overview';
    const seg = h('div', { class: 'seg' });
    const content = h('div');
    const tabs = [['overview', 'OVERVIEW'], ['profile', 'FULL PROFILE']];
    const btns = tabs.map(([id, label]) => { const b = h('button', {}, label); b.onclick = () => { view = id; paint(); }; seg.append(b); return [id, b]; });
    body.append(seg, content);
    page.actions.append(button('↻', () => load()));

    let data = null;
    async function load() {
      content.innerHTML = ''; content.append(loading('Reading your data…'));
      try { data = await ctx.api.dashboard(); paint(); }
      catch (e) { content.innerHTML = ''; content.append(empty(e.message)); }
    }

    function paint() {
      btns.forEach(([id, b]) => b.classList.toggle('active', id === view));
      if (!data) return;
      content.innerHTML = '';
      const s = data.summary || {}, t = data.trend || {}, p = data.profile || {};
      if (view === 'overview') {
        const moodText = s.avg_mood != null ? `${s.avg_mood}/10` : '--';
        content.append(
          h('div', { class: 'reactor-zone' }, h('div', { class: 'callout', style: 'text-align:center;min-width:60%;padding:16px' },
            'AVG MOOD · THIS WEEK', h('b', { style: 'font-size:2rem' }, moodText),
            el('div', null, s.avg_mood != null ? `Based on ${s.journal_entries} check-in(s) this week` : 'No journal check-ins this week yet'))),
          panel('THIS WEEK', 'LOG.W', h('div', { class: 'stat-grid' },
            h('div', { class: 'stat' }, 'SESSIONS', h('b', {}, String(s.total_sessions ?? 0))),
            h('div', { class: 'stat' }, 'TASKS DONE', h('b', {}, String(s.tasks_completed ?? 0))),
            h('div', { class: 'stat' }, 'PAPERS ADDED', h('b', {}, String(s.papers_added ?? 0))),
            h('div', { class: 'stat' }, 'JOURNAL', h('b', {}, String(s.journal_entries ?? 0))))),
          panel('SESSION BREAKDOWN', '8 WEEKS', hbars(t.session_type_totals)),
          panel('MOOD TREND', '/10', barChart(t.week_labels || [], t.mood_per_week || [], { violet: true, max: 10 })),
          panel('SESSION ENJOYMENT', '/10', barChart(t.week_labels || [], t.rating_per_week || [], { max: 10 })),
          panel('TASKS PER WEEK', null, barChart(t.week_labels || [], t.tasks_per_week || [])),
          panel('PAPERS PER WEEK', null, barChart(t.week_labels || [], t.papers_per_week || [], { violet: true })),
          panel('RECENT JOURNAL', null, list(t.recent_journal, (j) => h('li', {}, h('div', { class: 'txt' },
            el('div', 'meta', `${new Date(j.timestamp).toLocaleDateString([], { month: 'short', day: 'numeric' })}${j.mood != null ? ' · Mood ' + j.mood + '/10' : ''}`),
            el('div', null, `${j.good_thing || ''}${j.on_mind ? ' — ' + j.on_mind : ''}`))), 'No entries yet - open JOURNAL or say "journal time".')),
          panel('SYSTEM SNAPSHOT', 'PC', h('div', { class: 'kv' }, el('span', null, 'CPU'), el('b', null, data.vitals?.cpu != null ? Math.round(data.vitals.cpu) + '%' : '--')),
            h('div', { class: 'kv' }, el('span', null, 'RAM'), el('b', null, data.vitals?.ram != null ? Math.round(data.vitals.ram) + '%' : '--'))),
          h('div', { class: 'row-btns' }, button('OPEN ON PC', () => ctx.api.pc('dashboard')), button('WEEKLY REPORT', () => { page.close(); ctx.switchTab('comms'); ctx.send('weekly report'); })),
        );
      } else {
        const pills = (arr, none) => (arr && arr.length ? h('div', { class: 'pills' }, ...arr.map((x) => h('span', {}, x))) : empty(none));
        const tasks = (p.tasks || []).slice().reverse();
        content.append(
          panel('FACTS', 'MEMORY', pills(p.facts, 'Nothing yet.')),
          panel('PROJECTS', 'MEMORY', pills(p.projects, 'Nothing yet.')),
          panel('PREFERENCES', 'MEMORY', pills(p.preferences, 'Nothing yet.')),
          panel('ALL-TIME SESSIONS', null, hbars(p.session_type_totals_alltime)),
          panel('SESSIONS', `${(p.sessions || []).length}`, list((p.sessions || []).slice(0, 60), (x) => h('li', {}, el('span', 'kind', x.type.toUpperCase()),
            h('div', { class: 'txt' }, el('div', 'meta', new Date(x.timestamp).toLocaleString([], { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' }))),
            x.rating != null ? el('span', 'count', `${x.rating}/10`) : null), 'Nothing yet.')),
          panel('TASKS', `${tasks.length}`, list(tasks.slice(0, 80), (x) => h('li', { class: x.done ? 'done' : '' }, el('span', 'txt', x.text)), 'Nothing yet.')),
          panel('PAPERS', `${(p.papers || []).length}`, list(p.papers, (x) => h('li', {}, h('div', { class: 'txt' }, el('div', null, x.title),
            el('div', 'meta', [x.authors, x.year, x.journal].filter(Boolean).join(' · ')))), 'No papers yet - say "add … to my paper library".')),
          h('div', { class: 'help' }, 'To change what AURA remembers, tell it: "forget that I …" or "forget everything".'),
        );
      }
    }
    load();
  });
}
