// iPad-only extras: the big screen gets a full command deck. NOTHING here exists on the phone - it's only built when the
// window can be tablet-sized, and every piece has the class "ipad-only", which tablet.css shows from 768pt up
// (narrow iPad Split View hides it again). The phone UI and the PC HUD are untouched.
//   top bar   live HUD strip: clock, date, weather, PC link, next reminder
//   rail      status lights + a big TALK button
//   CORE      comms feed + NEXUS deck | weather (12 h graph), mission board (tasks), agenda (countdowns), telemetry
//   COMMS     a context panel beside the chat (landscape)
//   LISTS     all four lists on screen at once (tablet.css)
//   MORE      the NEXUS systems as big cards
import { el, h, icon, panel, drawSpark, toast } from '../ui/components.js';
import { fmtCountdown, timeOf, pad } from '../core/format.js';
import * as haptics from '../platform/haptics.js';
import * as storage from '../platform/storage.js';
import * as vitals from '../platform/vitals.js';
import * as brainKit from '../brain/kit.js';
import { isIPad } from '../platform/device.js';

export const wanted = () => isIPad || window.matchMedia('(min-width: 768px)').matches;

const SYSTEMS = [['nexus-live', 'LIVE', 'live'], ['nexus-babel', 'BABEL', 'globe'], ['nexus-echo', 'ECHO', 'wave'], ['nexus-radar', 'RADAR', 'radar'],
  ['nexus-pulse', 'PULSE', 'heart'], ['nexus-link', 'LINK', 'remote'], ['nexus-protocols', 'PROTOCOLS', 'bolt'], ['vision', 'VISION', 'eye'], ['studio', 'STUDIO', 'pen'], ['spatial', 'SPATIAL', 'cube'], ['overwatch', 'OVERWATCH', 'target']];
const WMO = { 0: ['Clear sky', '☀'], 1: ['Mainly clear', '🌤'], 2: ['Partly cloudy', '⛅'], 3: ['Overcast', '☁'], 45: ['Fog', '🌫'], 48: ['Fog', '🌫'],
  51: ['Light drizzle', '🌦'], 53: ['Drizzle', '🌦'], 55: ['Heavy drizzle', '🌧'], 61: ['Light rain', '🌦'], 63: ['Rain', '🌧'], 65: ['Heavy rain', '🌧'],
  71: ['Light snow', '🌨'], 73: ['Snow', '🌨'], 75: ['Heavy snow', '❄'], 80: ['Showers', '🌦'], 81: ['Showers', '🌧'], 82: ['Violent showers', '⛈'],
  95: ['Thunderstorm', '⛈'], 96: ['Thunderstorm, hail', '⛈'], 99: ['Thunderstorm, hail', '⛈'] };

let ctx;
const W = { data: null };

// ------------------------------------------------------------------ weather (free: Open-Meteo, no key)
async function weather() {
  try {
    let place = storage.get('ipad.place', null);
    const want = ((brainKit.get() || {}).location || '').split(',')[0].trim();
    if (!want) return;                                     // no city configured (LOCATION in .env)
    if (!place || place.q !== want) {
      const g = await (await fetch(`https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(want)}&count=1`)).json();
      const r = (g.results || [])[0];
      if (!r) return;
      place = { q: want, name: r.name, lat: r.latitude, lon: r.longitude };
      storage.set('ipad.place', place);
    }
    const u = `https://api.open-meteo.com/v1/forecast?latitude=${place.lat}&longitude=${place.lon}&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m`
      + '&hourly=temperature_2m&daily=temperature_2m_max,temperature_2m_min,sunrise,sunset&forecast_days=2&timezone=auto';
    const d = await (await fetch(u)).json();
    const nowIdx = Math.max(0, (d.hourly.time || []).findIndex((t) => new Date(t) >= new Date()) - 1);
    W.data = { place: place.name, cur: d.current, hours: d.hourly.temperature_2m.slice(nowIdx, nowIdx + 13), times: d.hourly.time.slice(nowIdx, nowIdx + 13),
      max: d.daily.temperature_2m_max[0], min: d.daily.temperature_2m_min[0], sunrise: d.daily.sunrise[0], sunset: d.daily.sunset[0] };
    storage.set('ipad.weather', W.data);
  } catch (e) { W.data = W.data || storage.get('ipad.weather', null); }
  paintWeather();
}

// ------------------------------------------------------------------ top bar strip + rail
const strip = {};
function topStrip(topbar) {
  strip.time = el('b', 'ip-time', '--:--'); strip.sec = el('span', 'ip-sec', '');
  strip.date = el('span', 'ip-date', '');
  strip.wx = el('span', 'ip-chip', '—');
  strip.link = el('span', 'ip-chip', 'PC —');
  strip.next = el('span', 'ip-chip', 'NO REMINDERS');
  const node = h('div', { class: 'ip-strip ipad-only' }, h('div', { class: 'ip-clock' }, strip.time, strip.sec), strip.date, strip.wx, strip.link, strip.next);
  topbar.insertBefore(node, topbar.querySelector('.pill'));
}

const rail = {};
function railExtras(tabbar) {
  const light = (label) => { const i = el('i'); return [h('div', { class: 'ip-light' }, i, el('span', null, label)), i]; };
  const [pcL, pcI] = light('PC'), [brL, brI] = light('BRAIN'), [netL, netI] = light('NET');
  Object.assign(rail, { pc: pcI, brain: brI, net: netI });
  const talk = h('button', { class: 'ip-talk', 'aria-label': 'Talk to AURA' }, icon('mic'), el('span', null, 'TALK'));
  talk.onclick = () => { haptics.tap(); ctx.talk(); };
  ctx.store.on('recording', (r) => talk.classList.toggle('on', !!r));
  tabbar.append(h('div', { class: 'ip-rail ipad-only' }, h('div', { class: 'ip-lights' }, pcL, brL, netL), talk));
}

// ------------------------------------------------------------------ CORE
const core = {};
function coreExtras(screen) {
  const main = screen.querySelector('.core-main'), side = screen.querySelector('.core-side');
  if (!main || !side) return;

  // comms feed
  core.feed = h('div', { class: 'ip-feed' });
  const feedPanel = panel('COMMS FEED', 'LIVE', core.feed);
  feedPanel.classList.add('ipad-only', 'tap');
  feedPanel.onclick = () => ctx.switchTab('comms');

  // NEXUS deck
  const deck = h('div', { class: 'ip-deck' });
  for (const [tool, name, ic] of SYSTEMS) {
    const b = h('button', { class: 'ip-deck-btn', 'aria-label': name }, icon(ic), el('span', null, name));
    b.onclick = () => { haptics.tap(); ctx.openTool(tool); };
    deck.append(b);
  }
  const deckPanel = panel('NEXUS DECK', '11 SYSTEMS', deck);
  deckPanel.classList.add('ipad-only');
  main.append(feedPanel, deckPanel);

  // weather
  core.wx = h('div', { class: 'ip-wx' });
  core.wxSpark = h('canvas', { class: 'ip-wx-spark', 'aria-hidden': 'true' });
  core.wxHours = h('div', { class: 'ip-wx-hours' });
  const wxPanel = panel('WEATHER', 'OPEN-METEO', core.wx, core.wxSpark, core.wxHours);
  wxPanel.classList.add('ipad-only', 'ip-first');
  core.wxCode = wxPanel.querySelector('.code');

  // mission board (tasks)
  core.tasks = h('ul', { class: 'list ip-tasks' });
  const add = h('input', { class: 'input', placeholder: 'Add a task…', maxlength: '200', enterkeyhint: 'done', 'aria-label': 'New task' });
  add.addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter' || !add.value.trim()) return;
    const v = add.value.trim(); add.value = '';
    try { await ctx.api.addTask(v); haptics.success(); ctx.refresh(); } catch (err) { add.value = v; toast("Couldn't add it", err.message, 'error'); }
  });
  const taskPanel = panel('MISSION BOARD', 'TASKS', core.tasks, add);
  taskPanel.classList.add('ipad-only');
  core.taskCode = taskPanel.querySelector('.code');

  // agenda (reminders)
  core.agenda = h('ul', { class: 'list ip-agenda' });
  const agendaPanel = panel('AGENDA', 'REMINDERS', core.agenda);
  agendaPanel.classList.add('ipad-only');

  // telemetry
  core.tIpad = h('canvas', { class: 'spark' }); core.tPc = h('canvas', { class: 'spark' });
  core.tIpadTxt = el('span', 'ip-tele-v', '--'); core.tPcTxt = el('span', 'ip-tele-v', '--');
  const telePanel = panel('TELEMETRY', 'LIVE',
    h('div', { class: 'ip-tele-row' }, el('span', 'ip-tele-k', isIPad ? 'IPAD' : 'THIS DEVICE'), core.tIpadTxt), core.tIpad,
    h('div', { class: 'ip-tele-row' }, el('span', 'ip-tele-k', 'PC'), core.tPcTxt), core.tPc);
  telePanel.classList.add('ipad-only', 'tap');
  telePanel.onclick = () => ctx.switchTab('system');

  side.prepend(wxPanel);
  side.append(taskPanel, agendaPanel, telePanel);
}

function paintWeather() {
  const d = W.data;
  const parts = d ? (WMO[d.cur.weather_code] || ['—', '•']) : ['Loading weather…', '…'];
  if (strip.wx) strip.wx.textContent = d ? `${parts[1]} ${Math.round(d.cur.temperature_2m)}°C` : "—";
  if (!core.wx) return;
  if (!d) { core.wx.replaceChildren(el('div', 'empty', 'Weather appears when the iPad is online.')); return; }
  core.wxCode.textContent = d.place.toUpperCase();
  core.wx.replaceChildren(
    h('div', { class: 'ip-wx-main' }, el('span', 'ip-wx-icon', parts[1]), el('b', null, `${Math.round(d.cur.temperature_2m)}°`),
      h('div', {}, el('div', 'ip-wx-cond', parts[0]), el('div', 'ip-wx-sub', `Feels ${Math.round(d.cur.apparent_temperature)}° · H ${Math.round(d.max)}° L ${Math.round(d.min)}°`))),
    h('div', { class: 'ip-wx-stats' },
      h('div', {}, el('span', null, 'HUMIDITY'), el('b', null, `${d.cur.relative_humidity_2m}%`)),
      h('div', {}, el('span', null, 'WIND'), el('b', null, `${Math.round(d.cur.wind_speed_10m)} km/h`)),
      h('div', {}, el('span', null, 'SUNRISE'), el('b', null, new Date(d.sunrise).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }))),
      h('div', {}, el('span', null, 'SUNSET'), el('b', null, new Date(d.sunset).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })))));
  const lo = Math.min(...d.hours) - 1, hi = Math.max(...d.hours) + 1;
  requestAnimationFrame(() => drawSpark(core.wxSpark, [{ data: d.hours.map((t) => t - lo), max: hi - lo, color: '#ffb547', fill: 'rgba(255,181,71,.12)' }]));
  core.wxHours.replaceChildren(...d.hours.filter((_, i) => i % 3 === 0).map((t, i) => h('div', {},
    el('span', null, new Date(d.times[i * 3]).toLocaleTimeString([], { hour: 'numeric' })), el('b', null, `${Math.round(t)}°`))));
}

function paintState(s) {
  if (!s) return;
  const tasks = s.tasks || [], rems = s.reminders || [];
  if (core.tasks) {
    core.taskCode.textContent = `${tasks.length} OPEN`;
    core.tasks.replaceChildren(...(tasks.length ? tasks.slice(0, 8).map((t) => {
      const li = h('li');
      const box = h('button', { class: 'check', 'aria-label': `Mark "${t}" as done` });
      box.onclick = async () => {
        li.classList.add('done'); haptics.success();
        try { await ctx.api.completeTask(t); } catch (e) { li.classList.remove('done'); toast("Couldn't update", e.message, 'error'); }
        setTimeout(ctx.refresh, 400);
      };
      li.append(box, el('span', 'txt', t));
      return li;
    }) : [h('li', {}, el('div', 'empty', 'All clear - nothing pending.'))]));
  }
  if (core.agenda) {
    core.agenda.replaceChildren(...(rems.length ? rems.slice(0, 6).map((r) => {
      const c = el('span', 'count'); c.dataset.due = new Date(r.due).getTime();
      return h('li', {}, h('div', { class: 'txt' }, el('div', null, r.text),
        el('div', 'meta', new Date(r.due).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }))), c);
    }) : [h('li', {}, el('div', 'empty', 'No reminders. Say "remind me at 6 to…"'))]));
  }
  const next = rems[0];
  if (strip.next) strip.next.dataset.due = next ? new Date(next.due).getTime() : '';
  if (strip.next) strip.next.dataset.text = next ? next.text : '';
  paintTele();
}

function paintFeed(chat) {
  if (!core.feed) return;
  const last = (chat || []).slice(-5);
  core.feed.replaceChildren(...(last.length ? last.map((m) => h('div', { class: `ip-msg ${m.role === 'you' ? 'you' : 'aura'}` },
    el('span', 'ip-msg-who', m.role === 'you' ? 'YOU' : 'AURA'), el('span', 'ip-msg-txt', m.text), el('span', 'ip-msg-t', timeOf(m.t))))
    : [el('div', 'empty', 'Nothing said yet - type below or tap TALK.')]));
}

function paintTele() {
  if (!core.tIpad) return;
  const v = vitals.get(), s = (ctx.store.get('state') || {}).system || {};
  core.tIpadTxt.textContent = `LOAD ${Math.round(v.load)}% · ${v.fps} FPS · ${v.ping != null ? v.ping + ' ms' : '--'}`;
  core.tPcTxt.textContent = ctx.store.get('conn') === 'offline' ? 'OFFLINE' : `CPU ${s.cpu != null ? Math.round(s.cpu) : '--'}% · RAM ${s.ram != null ? Math.round(s.ram) : '--'}%`;
  if (!document.getElementById('screen-core')?.classList.contains('active')) return;
  drawSpark(core.tIpad, [{ data: v.fpsHistory.map((f) => Math.min(100, (f / (v.maxFps || 60)) * 100)), max: 100, color: '#a78bfa', fill: 'rgba(167,139,250,0.08)' },
    { data: v.loadHistory, max: 100, color: '#3ad6ff', fill: 'rgba(58,214,255,0.12)' }]);
  drawSpark(core.tPc, [{ data: s.ram_history, max: 100, color: '#a78bfa', fill: 'rgba(167,139,250,0.08)' }, { data: s.cpu_history, max: 100, color: '#3ad6ff', fill: 'rgba(58,214,255,0.12)' }]);
}

// ------------------------------------------------------------------ COMMS context panel
function commsExtras(screen) {
  const quick = h('div', { class: 'ip-quick' });
  for (const [label, cmd] of [['☀ Daily briefing', 'good morning'], ['📋 My tasks', "what's on my task list"], ['⏰ My reminders', 'what are my reminders'],
    ['📧 Important emails', 'do I have any important emails'], ['◎ Focus 25', 'start a 25 minute focus session'], ['🧠 Quiz me', 'quiz me on my notes']]) {
    const b = h('button', { class: 'ip-quick-btn' }, label);
    b.onclick = () => { haptics.tap(); ctx.send(cmd); };
    quick.append(b);
  }
  const deck = h('div', { class: 'ip-deck small' });
  for (const [tool, name, ic] of SYSTEMS) {
    const b = h('button', { class: 'ip-deck-btn', 'aria-label': name }, icon(ic), el('span', null, name));
    b.onclick = () => { haptics.tap(); ctx.openTool(tool); };
    deck.append(b);
  }
  core.ctxTasks = el('div', 'ip-ctx-v', '--'); core.ctxRem = el('div', 'ip-ctx-v', '--'); core.ctxBrain = el('div', 'ip-ctx-v', '--');
  screen.append(h('aside', { class: 'ip-comms-side ipad-only' },
    panel('QUICK ASK', 'ONE TAP', quick),
    panel('AT A GLANCE', 'STATUS', h('div', { class: 'ip-ctx' },
      h('div', {}, el('span', null, 'OPEN TASKS'), core.ctxTasks), h('div', {}, el('span', null, 'REMINDERS'), core.ctxRem),
      h('div', {}, el('span', null, 'BRAIN'), core.ctxBrain))),
    panel('SYSTEMS', 'NEXUS', deck),
    panel('TODAY', 'LOG.D', core.ctxToday = h('div', { class: 'ip-ctx' }))));
}

function paintToday(s) {
  if (!core.ctxToday || !s) return;
  const t = s.today || {};
  core.ctxToday.replaceChildren(...[['FOCUS MIN', t.focus_minutes || 0], ['SESSIONS', t.sessions_today || 0], ['TASKS DONE', t.tasks_done_today || 0]]
    .map(([k, v]) => h('div', {}, el('span', null, k), el('div', 'ip-ctx-v', String(v)))));
}

// ------------------------------------------------------------------ LISTS: the week ahead (reminders + task deadlines)
function listsExtras(screen) {
  core.week = h('div', { class: 'ip-week' });
  const p = panel('WEEK AHEAD', 'REMINDERS · DEADLINES', core.week);
  p.classList.add('ipad-only', 'ip-span');
  screen.append(p);
}

function paintWeek(s) {
  if (!core.week || !s) return;
  const day0 = new Date(); day0.setHours(0, 0, 0, 0);
  const items = [];
  for (const r of s.reminders || []) items.push({ t: new Date(r.due), text: r.text, kind: 'rem' });
  for (const t of s.tasks || []) {
    const m = /\bdue\s+(\d{4}-\d{2}-\d{2})/i.exec(t);                    // tasks like "... - due 2026-10-06"
    if (m) items.push({ t: new Date(m[1] + 'T09:00:00'), text: t.replace(/\s*-?\s*due\s+\d{4}-\d{2}-\d{2}/i, ''), kind: 'task' });
  }
  core.week.replaceChildren(...Array.from({ length: 7 }, (_, i) => {
    const d = new Date(day0.getTime() + i * 864e5), next = new Date(d.getTime() + 864e5);
    const todays = items.filter((x) => x.t >= d && x.t < next).sort((a, b) => a.t - b.t);
    return h('div', { class: 'ip-day' + (i === 0 ? ' today' : '') + (todays.length ? ' busy' : '') },
      el('span', 'ip-day-name', i === 0 ? 'TODAY' : d.toLocaleDateString([], { weekday: 'short' }).toUpperCase()),
      el('b', 'ip-day-num', String(d.getDate())),
      ...(todays.length ? todays.slice(0, 4).map((x) => h('div', { class: `ip-ev ${x.kind}` },
        x.kind === 'rem' ? el('span', 'ip-ev-t', x.t.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })) : el('span', 'ip-ev-t', 'DUE'), el('span', null, x.text)))
        : [el('div', 'ip-day-free', 'free')]));
  }));
}

// ------------------------------------------------------------------ MORE: NEXUS cards
function moreExtras(screen) {
  const TEXT = { 'nexus-live': 'Hands-free talk with eyes', 'nexus-babel': 'Live two-way interpreter', 'nexus-echo': 'Lectures → notes & flashcards',
    'nexus-radar': 'Places, AR, Qibla, sky', 'nexus-pulse': 'Heart rate, breathing, reflex', 'nexus-link': 'Your PC, live and in hand',
    'nexus-protocols': 'One word, many actions', vision: 'The camera HUD', studio: 'Make anything, edit with words', spatial: 'Bring me anything in 3D', overwatch: 'Guard · recall · air control' };
  const grid = h('div', { class: 'ip-systems' });
  for (const [tool, name, ic] of SYSTEMS) {
    const b = h('button', { class: 'ip-sys' }, h('span', { class: 'ip-sys-ic' }, icon(ic)), el('b', null, name), el('span', null, TEXT[tool]));
    b.onclick = () => { haptics.tap(); ctx.openTool(tool); };
    grid.append(b);
  }
  const say = h('div', { class: 'ip-say' });
  for (const [label, cmd] of [['Good morning', 'good morning'], ["What's my schedule today?", "what's on my schedule today"],
    ['Research heart failure on PubMed', 'search pubmed for heart failure'], ['Quiz me on my notes', 'quiz me on my notes'],
    ['Run study protocol', 'run study protocol'], ['Play lofi on Spotify', 'play lofi on spotify'],
    ['Any important emails?', 'do I have any important emails'], ['Start a 50 minute focus', 'start a 50 minute focus session']]) {
    const b = h('button', { class: 'ip-say-btn' }, el('span', null, '“'), label, el('span', null, '”'));
    b.onclick = () => { haptics.tap(); ctx.send(cmd); ctx.switchTab('comms'); };
    say.append(b);
  }
  screen.append(h('div', { class: 'ipad-only' }, el('div', 'section-label', 'NEXUS SYSTEMS'), grid,
    el('div', 'section-label', 'SAY IT · ONE TAP'), say));
}

// ------------------------------------------------------------------ start
export function enhance(c, { topbar, tabbar, screens }) {
  if (!wanted()) return;
  ctx = c;
  document.documentElement.classList.add('has-ipad-deck');
  topStrip(topbar);
  railExtras(tabbar);
  coreExtras(screens.core);
  commsExtras(screens.comms);
  moreExtras(screens.more);
  listsExtras(screens.lists);

  W.data = storage.get('ipad.weather', null);
  paintWeather();
  weather();
  setInterval(weather, 20 * 60e3);

  ctx.store.on('state', paintState);
  ctx.store.on('state', paintWeek);
  ctx.store.on('state', paintToday);
  paintWeek(ctx.store.get('state')); paintToday(ctx.store.get('state'));
  ctx.store.on('chat', paintFeed);
  ctx.store.on('conn', () => paintState(ctx.store.get('state')));
  paintState(ctx.store.get('state'));
  paintFeed(ctx.store.get('chat'));
  vitals.on(paintTele);

  setInterval(() => {                                         // clock, countdowns, lights - once a second
    const now = new Date();
    strip.time.textContent = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
    strip.sec.textContent = pad(now.getSeconds());
    strip.date.textContent = now.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' }).toUpperCase();
    const due = Number(strip.next.dataset.due || 0);
    strip.next.textContent = due ? `⏰ ${fmtCountdown((due - Date.now()) / 1000)} · ${String(strip.next.dataset.text).slice(0, 26).toUpperCase()}` : 'NO REMINDERS';
    document.querySelectorAll('.ip-agenda .count').forEach((n) => { n.textContent = fmtCountdown((Number(n.dataset.due) - Date.now()) / 1000); });
    const conn = ctx.store.get('conn'), s = ctx.store.get('state') || {};
    strip.link.textContent = conn === "online" ? "PC LINKED" : conn === "offline" ? (brainKit.ready() ? "PHONE BRAIN" : "PC OFF") : "LINKING…";
    strip.link.className = 'ip-chip ' + (conn === 'online' ? 'ok' : conn === 'offline' ? 'warn' : '');
    rail.pc.className = conn === 'online' ? 'on' : conn === 'offline' ? 'off' : '';
    rail.brain.className = brainKit.ready() ? 'on' : 'off';
    rail.net.className = navigator.onLine ? 'on' : 'off';
    if (core.ctxTasks) { core.ctxTasks.textContent = (s.tasks || []).length; core.ctxRem.textContent = (s.reminders || []).length; core.ctxBrain.textContent = conn === 'offline' ? 'PHONE' : 'PC'; }
  }, 1000);
}
