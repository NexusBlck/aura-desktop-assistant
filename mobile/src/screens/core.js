// CORE - the HUD's centre column on a phone: reactor, live callouts, command bar with the cat,
// quick chips, today's briefing, important emails, focus timer, today's stats and dev status.
import { el, h, icon, panel, reactorSVG, setArc, toast, empty } from '../ui/components.js';
import { CHIPS } from '../core/commands.js';
import { fmtCountdown, fmtDuration, timeOf } from '../core/format.js';
import * as haptics from '../platform/haptics.js';
import * as vitals from '../platform/vitals.js';
import { isIPad } from '../platform/device.js';
import { commandBar } from './cmdbar.js';

let ctx, refs = {}, cat = null, lastActivity = Date.now();

// the reactor's rings and the first two callouts are THIS PHONE: load (outer ring) and smoothness (inner ring)
function renderPhone(v) {
  if (!refs.cpu) return;
  refs.cpu.textContent = Math.round(v.load) + '%';
  refs.ram.textContent = v.ping != null ? `${v.ping} ms` : '--';
  setArc(refs.rCpu, v.load, 993);
  setArc(refs.rRam, Math.min(100, (v.fps / (v.maxFps || 60)) * 100), 918);
}

export function mount(root, c) {
  ctx = c;
  vitals.start();
  vitals.on(renderPhone);
  // reactor
  const reactor = h('div', { class: 'reactor', role: 'button', 'aria-label': 'AURA core - tap to talk, tap again to stop' });
  reactor.innerHTML = reactorSVG();
  const l2 = el('div', 'l2', 'IDLE'), l3 = el('div', 'l3', 'ALL SYSTEMS NOMINAL');
  reactor.append(h('div', { class: 'core-text' }, el('div', 'l1', 'NEURAL CORE'), l2, l3));
  reactor.onclick = () => {
    const st = ctx.store.get('status') || {};
    if (ctx.store.get('speakingLocal') || st.talking) { ctx.stopSpeaking(); return; }
    ctx.talk();
  };
  refs.l2 = l2; refs.l3 = l3; refs.rCpu = reactor.querySelector('#rCpu'); refs.rRam = reactor.querySelector('#rRam');

  // callouts (this phone's load and ping, open tasks, next reminder)
  const co = (label) => { const b = el('b', null, '--'); return [h('div', { class: 'callout' }, label, b), b]; };
  const [c1, cpu] = co(isIPad ? 'IPAD LOAD' : 'PHONE LOAD'), [c2, ram] = co('PING'), [c3, tasks] = co('OPEN TASKS'), [c4, rem] = co('NEXT REMINDER');
  c1.onclick = () => ctx.switchTab('system'); c2.onclick = () => ctx.switchTab('system');
  c3.onclick = () => ctx.switchTab('lists'); c4.onclick = () => ctx.switchTab('lists');
  Object.assign(refs, { cpu, ram, tasks, rem });

  // latest transmission (last thing AURA said) -> tap to open the conversation
  refs.trans = el('div', 'transmission', 'Nothing said yet. Type below, tap the core to talk.');
  refs.transTime = el('div', 'code', 'COMMS');
  const transPanel = panel('LATEST TRANSMISSION', null, refs.trans);
  transPanel.querySelector('.ph').append(refs.transTime);
  transPanel.classList.add('tap');
  transPanel.onclick = () => ctx.switchTab('comms');

  // command bar with the HUD cat sitting on it
  const bar = commandBar(ctx, { placeholder: 'Tell AURA what to do…', afterSend: () => ctx.switchTab('comms') });
  const catBox = h('div', { class: 'hud-cat', 'aria-hidden': 'true' });
  bar.node.prepend(catBox);
  refs.input = bar;

  // chips
  const chips = h('div', { class: 'chips scroll', role: 'list' });
  for (const c of CHIPS) {
    const b = h('button', { class: 'chip' + (c.log ? ' log' : ''), role: 'listitem' }, c.label);
    b.onclick = () => {
      haptics.tap();
      if (c.cmd) { ctx.send(c.cmd); ctx.switchTab('comms'); }
      else if (c.fill) bar.fill(c.fill);
      else if (c.screen) ctx.openTool(c.screen);
      else if (c.log) ctx.openTool('log', c.log);
    };
    chips.append(b);
  }

  refs.emailBox = h('div');
  refs.briefBox = h('div');

  // focus timer
  refs.focusArc = null;
  const focusSvg = h('div', { html: `<svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="42" fill="none" stroke="rgba(58,214,255,0.12)" stroke-width="7"/>
    <circle class="farc" cx="50" cy="50" r="42" fill="none" stroke="#3ad6ff" stroke-width="7" stroke-linecap="round" stroke-dasharray="0 264" transform="rotate(-90 50 50)" style="filter:drop-shadow(0 0 4px #3ad6ff)"/>
    <circle cx="50" cy="50" r="31" fill="none" stroke="rgba(58,214,255,0.25)" stroke-dasharray="2 3"/></svg>` });
  refs.focusArc = focusSvg.querySelector('.farc');
  refs.ftime = el('div', 'ftime', '25:00'); refs.fmode = el('div', 'fmode', 'IDLE · READY TO FOCUS'); refs.fbtns = h('div', { class: 'row-btns' });
  refs.focusCard = h('div', { class: 'focus' }, focusSvg.firstChild, h('div', {}, refs.ftime, refs.fmode, refs.fbtns));
  refs.focusLabel = el('div', 'code', 'READY');
  const focusPanel = panel('FOCUS TIMER', null, refs.focusCard);
  focusPanel.querySelector('.ph').append(refs.focusLabel);

  // today + dev status
  const stat = (label) => { const b = el('b', null, '0'); return [h('div', { class: 'stat' }, label, b), b]; };
  const [s1, f] = stat('FOCUS MIN'), [s2, se] = stat('SESSIONS'), [s3, td] = stat('TASKS DONE'), [s4, pp] = stat('PAPERS');
  Object.assign(refs, { tFocus: f, tSessions: se, tTasks: td, tPapers: pp });
  refs.dev = h('div', { class: 'dev' }, empty('No builds yet.'));

  // two groups: one column on a phone, side by side on an iPad (tablet.css)
  root.append(h('div', { class: 'core-cols' },
    h('div', { class: 'core-main' },
      h('div', { class: 'reactor-zone' }, reactor),
      h('div', { class: 'callouts' }, c1, c2, c3, c4),
      transPanel,
      h('div', { style: 'margin: 34px 0 12px' }, bar.node),
      chips),
    h('div', { class: 'core-side' },
      refs.emailBox,
      refs.briefBox,
      focusPanel,
      panel('TODAY', 'LOG.D', h('div', { class: 'stat-grid' }, s1, s2, s3, s4)),
      panel('DEV STATUS', 'BUILD', refs.dev))));

  // the cat (shared with the PC HUD: Face/cat.js)
  if (window.AuraCat) {
    cat = window.AuraCat.mount(catBox, { onClick() { const wasAsleep = cat.state === 'sleep'; lastActivity = Date.now(); syncCat(); if (wasAsleep && cat.hop) cat.hop(); cat.pet(); haptics.success(); } });
  }

  ctx.store.on('state', render);
  ctx.store.on('chat', renderTransmission);
  ['status', 'recording', 'speakingLocal', 'conn'].forEach((k) => ctx.store.on(k, () => { lastActivity = Date.now(); renderStatus(); syncCat(); }));
  if (ctx.store.get('state')) render(ctx.store.get('state'));
  renderTransmission(ctx.store.get('chat'));
  setInterval(tick, 1000);
}

export function shown() { render(ctx.store.get('state')); }

function renderStatus() {
  const st = ctx.store.get('status') || {}, conn = ctx.store.get('conn');
  const rec = ctx.store.get('recording'), local = ctx.store.get('speakingLocal');
  let a = 'IDLE', b = 'ALL SYSTEMS NOMINAL';
  if (conn === 'offline') { a = 'OFFLINE'; b = 'PC UNREACHABLE'; }
  else if (rec) { a = 'LISTENING'; b = 'SPEAK NOW · TAP TO SEND'; }
  else if (st.asking) { a = 'ASKING'; b = 'AWAITING YOUR ANSWER'; }
  else if (st.talking || local) { a = 'SPEAKING'; b = 'TAP TO INTERRUPT'; }
  else if (st.thinking) { a = 'THINKING'; b = 'PROCESSING REQUEST'; }
  refs.l2.textContent = a; refs.l3.textContent = b;
}

function syncCat() {
  if (!cat) return;
  const st = ctx.store.get('status') || {};
  const want = (st.talking || ctx.store.get('speakingLocal')) ? 'talk' : st.thinking ? 'think'
    : (ctx.store.get('recording') || st.listening || st.asking) ? 'listen' : Date.now() - lastActivity > 120000 ? 'sleep' : 'idle';
  if (cat.state !== want) cat.setState(want);
}

function renderTransmission(chat) {
  const last = [...(chat || [])].reverse().find((m) => m.role !== 'you');
  if (last) { refs.trans.textContent = last.text; refs.transTime.textContent = timeOf(last.t); }
}

let focusState = null, focusAt = 0, focusSig = '', reminders = [];
function render(s) {
  if (!s) return;
  refs.tasks.textContent = (s.tasks || []).length;
  reminders = s.reminders || [];

  const t = s.today || {};
  refs.tFocus.textContent = t.focus_minutes || 0; refs.tSessions.textContent = t.sessions_today || 0;
  refs.tTasks.textContent = t.tasks_done_today || 0; refs.tPapers.textContent = t.papers_today || 0;

  // dev status
  const b = s.last_build;
  refs.dev.innerHTML = '';
  if (b) {
    const cls = b.status === 'ok' ? 'tag-ok' : b.status === 'error' ? 'tag-error' : b.status === 'building' ? 'tag-building' : 'tag-untested';
    const label = b.status === 'ok' ? '● TESTED OK' : b.status === 'error' ? '● NEEDS ATTENTION'
      : b.status === 'building' ? '● BUILDING' + (b.engine === 'claude' ? ' · CLAUDE CODE' : '') : '● UNTESTED';
    refs.dev.append(el('div', 'name', b.project_name || 'Project'), el('div', 'tagline ' + cls, label));
    if (b.step && b.status !== 'ok') refs.dev.append(el('div', 'build-step', String(b.step).split('\n')[0]));
    if (b.status === 'error') {
      const fix = h('button', { class: 'mini', style: 'margin-top:8px' }, 'FIX MY LAST APP');
      fix.onclick = () => { ctx.send('fix my last project'); ctx.switchTab('comms'); };
      refs.dev.append(fix);
    }
  } else refs.dev.append(empty('No builds yet.'));

  // important emails
  const n = (s.emails || []).length;
  refs.emailBox.innerHTML = '';
  if (n) {
    const chip = h('button', { class: 'chip warn', style: 'margin-bottom:12px' }, icon('mail'), `${n} important email${n === 1 ? '' : 's'}`);
    chip.onclick = () => ctx.openTool('emails');
    refs.emailBox.append(chip);
  }

  // today's briefing (from "good morning" / "what's my schedule")
  refs.briefBox.innerHTML = '';
  const br = s.briefing;
  if (br && br.at && Date.now() / 1000 - br.at < 16 * 3600) {
    const body = h('div');
    if (br.weather) body.append(h('div', { class: 'kv' }, el('span', null, 'WEATHER'), el('b', null, br.weather)));
    const list = (title, items) => {
      if (!items || !items.length) return;
      body.append(el('div', 'section-label', title));
      const ul = h('ul', { class: 'list' });
      items.forEach((x) => ul.append(h('li', {}, el('span', 'txt', x))));
      body.append(ul);
    };
    list('SCHEDULE', br.schedule); list('TASKS', br.tasks);
    if (!(br.schedule || []).length && !(br.tasks || []).length && !br.weather) body.append(empty('Nothing on the schedule.'));
    refs.briefBox.append(panel(`GOOD ${String(br.time_label || 'day').toUpperCase()}${br.name ? ', ' + br.name.toUpperCase() : ''}`, 'BRIEFING', body));
  }

  // focus timer
  focusState = s.focus || { mode: 'idle' }; focusAt = Date.now();
  if (focusState.mode !== focusSig) { focusSig = focusState.mode; buildFocusButtons(); }
  tick();
}

function buildFocusButtons() {
  const row = refs.fbtns; row.innerHTML = '';
  const mode = focusState ? focusState.mode : 'idle';
  const add = (label, fn, cls = '') => {
    const b = h('button', { class: 'mini ' + cls }, label);
    b.onclick = async () => { haptics.tap(); try { await fn(); } catch (e) { toast('Focus timer', e.message, 'error'); } ctx.refresh(); };
    row.append(b);
  };
  if (mode === 'idle') { add('▶ 25', () => ctx.api.focus('start', 25, 'Focus')); add('▶ 50', () => ctx.api.focus('start', 50, 'Deep work')); add('▶ 90', () => ctx.api.focus('start', 90, 'Deep work')); }
  else if (mode === 'paused') { add('▶ RESUME', () => ctx.api.focus('resume')); add('■ STOP', () => ctx.api.focus('stop'), 'danger'); }
  else { add('❚❚ PAUSE', () => ctx.api.focus('pause')); add('■ STOP', () => ctx.api.focus('stop'), 'danger'); }
}

function tick() {
  const f = focusState || { mode: 'idle', total: 0, remaining: 0 };
  let remaining = f.remaining || 0;
  if (f.mode === 'focus' || f.mode === 'break') remaining = Math.max(0, remaining - (Date.now() - focusAt) / 1000);
  refs.focusCard.classList.toggle('break', f.mode === 'break');
  refs.focusCard.classList.toggle('paused', f.mode === 'paused');
  refs.ftime.textContent = f.mode === 'idle' ? '25:00' : fmtDuration(remaining);
  refs.fmode.textContent = { idle: 'IDLE · READY TO FOCUS', focus: 'FOCUSING · ' + (f.label || 'FOCUS').toUpperCase(), break: 'BREAK · RECHARGE', paused: 'PAUSED' }[f.mode] || '';
  refs.focusLabel.textContent = f.mode === 'idle' ? 'READY' : String(f.mode).toUpperCase();
  const pct = f.total ? (1 - remaining / f.total) * 100 : 0;
  refs.focusArc.setAttribute('stroke-dasharray', `${(pct / 100) * 264} 264`);
  refs.focusArc.setAttribute('stroke', f.mode === 'break' ? '#4ef0a3' : f.mode === 'paused' ? '#ffb547' : '#3ad6ff');
  refs.rem.textContent = reminders.length ? fmtCountdown((new Date(reminders[0].due) - Date.now()) / 1000) : 'NONE';
  syncCat();
}
