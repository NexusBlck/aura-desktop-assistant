// AURA Mobile - app controller: boot, pairing gate, live connection to the PC, voice in/out,
// tabs and tools. Screens live in ./screens/, shared logic in ./core/, device specifics in ./platform/.
import { api, post, isPaired, onLostPairing, ApiError, useLocal, setLocalMode, authHeader, setToken } from './core/api.js';
import * as storage from './platform/storage.js';
import { connectEvents } from './core/events.js';
import * as store from './core/store.js';
import * as commands from './core/commands.js';
import { speechChunks } from './core/format.js';
import * as audio from './platform/audio.js';
import * as device from './platform/device.js';
import * as notify from './platform/notify.js';
import * as haptics from './platform/haptics.js';
import { el, h, icon, LOGO, toast, closeTopPage } from './ui/components.js';

import * as coreScreen from './screens/core.js';
import * as commsScreen from './screens/comms.js';
import * as listsScreen from './screens/lists.js';
import * as systemScreen from './screens/system.js';
import * as moreScreen from './screens/more.js';
import { showPairing } from './screens/pair.js';
import * as skins from './ui/skins.js';
import { openTool, bindTools } from './screens/tools.js';
import * as ipad from './screens/ipad.js';
import * as brainKit from './brain/kit.js';
import * as brainLocal from './brain/local.js';
import * as mind from './brain/mind.js';
import * as brainKb from './brain/knowledge.js';

const TABS = [
  { id: 'core', label: 'CORE', icon: 'core', mod: coreScreen },
  { id: 'comms', label: 'COMMS', icon: 'comms', mod: commsScreen },
  { id: 'lists', label: 'LISTS', icon: 'lists', mod: listsScreen },
  { id: 'system', label: 'SYSTEM', icon: 'system', mod: systemScreen },
  { id: 'more', label: 'MORE', icon: 'more', mod: moreScreen },
];

let events = null;
let pollTimer = null;
let activeTab = 'core';
const tabButtons = {};
const screens = {};
let lastPhoneCommandAt = 0;

// ------------------------------------------------------------------ theme
function applyTheme() {
  const t = store.get('settings').theme;
  const light = t === 'light' || (t === 'system' && matchMedia('(prefers-color-scheme: light)').matches);
  document.documentElement.dataset.theme = light ? 'light' : 'dark';
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', light ? '#eef6fa' : '#02050a');
}

// ------------------------------------------------------------------ actions every screen uses
export const ctx = {
  store, api,
  /** Sends a message to AURA (same queue as voice / HUD / Mini). */
  async send(text) {
    text = (text || '').trim();
    if (!text) return false;
    if (store.get('conn') === 'offline' && !brainKit.ready()) { toast('PC offline', "Your PC isn't reachable, and the phone brain isn't set up yet - open AURA once while your PC is on.", 'error'); return false; }
    audio.stop();
    commands.remember(text);
    lastPhoneCommandAt = Date.now();
    try {
      await api.command(text);
      return true;
    } catch (e) {
      toast("Couldn't send", e.message, 'error');
      return false;
    }
  },
  /** Push-to-talk: records until you stop talking, Whisper on the PC turns it into a command. */
  talk,
  stopSpeaking() { audio.stop(); api.pc('stop_speaking').catch(() => {}); },
  openTool: (name, arg) => openTool(name, arg),
  switchTab,
  refresh,
};

let recorder = null;
async function talk() {
  if (recorder) { recorder.stop(); return; }
  if (!audio.canRecord()) { toast('Microphone', "This browser can't record audio.", 'error'); return; }
  if (store.get('conn') === 'offline' && !brainKit.ready()) { toast('PC offline', "Your PC isn't reachable, and the phone brain isn't set up yet.", 'error'); return; }
  audio.stop();
  haptics.tap();
  try {
    recorder = await audio.record({});
  } catch (e) {
    recorder = null;
    toast('Microphone', e && e.name === 'NotAllowedError'
      ? 'Microphone access is off - allow it in Settings → Safari (or AURA) → Microphone.'
      : "Couldn't start the microphone.", 'error');
    return;
  }
  store.set('recording', true);
  const blob = await recorder.done;
  recorder = null;
  store.set('recording', false);
  if (!blob) return;
  lastPhoneCommandAt = Date.now();
  try {
    const res = await api.voice(blob);
    if (!res.text) toast('Didn\'t catch that', 'Try again a little closer to the mic.', 'info', 3500);
    else commands.remember(res.text);
  } catch (e) {
    toast("Couldn't send", e.message, 'error');
  }
}

// ------------------------------------------------------------------ speaking replies on the phone
function speakOnPhone(text) {
  const s = store.get('settings');
  if (!s.voice || document.visibilityState !== 'visible') return;
  const parts = speechChunks(text);
  if (!parts.length) return;
  audio.playSequence(parts.map((p) => () => api.tts(p).catch(() => null)));
}
audio.onPlaybackState((on) => store.set('speakingLocal', on));

// ------------------------------------------------------------------ live data
// When the PC is off behind the VPN, requests to it don't fail - they hang. So: never wait long for it, never
// pile up requests, and decide "PC unreachable -> phone brain" within a few seconds.
let refreshing = false;
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const s = await api.state(store.get('conn') === 'online' ? 15000 : 6000);
    store.set('state', s);
    // the PC's log is the source of truth, but only redraw the conversation when it really changed
    const sig = (c) => (c && c.length ? `${c.length}|${c[c.length - 1].role}|${c[c.length - 1].text}` : '');
    if (s.chat && sig(s.chat) !== sig(store.get('chat'))) store.set('chat', s.chat);
    if (s.status) store.set('status', s.status);
    if (events) events.setLast(s.seq);
    setConn('online');
    store.persist();
    syncBrain();
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return;
    setConn('offline');
  } finally {
    refreshing = false;
  }
}

/** A 3.5-second "is the PC there?" check, so AURA switches to the phone brain right away at start-up. */
async function quickProbe() {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 3500);
  try {
    const r = await fetch('/api/hello', { cache: 'no-store', signal: ctrl.signal, headers: authHeader() });
    if (!r.ok) throw new Error(String(r.status));
    const hello = await r.json().catch(() => ({}));
    if (hello.device) storage.set('deviceId', hello.device);        // which replies are this device's own
  } catch (e) {
    if (store.get('conn') !== 'online') setConn('offline');
  } finally { clearTimeout(timer); }
}

function setConn(c) {
  if (store.get('conn') === c) return;
  const was = store.get('conn');
  store.set('conn', c);
  setLocalMode(c === 'offline');
  if (c === 'online' && was === 'offline') backOnline();
}

// ------------------------------------------------------------------ the phone brain (works while the PC is off)
let syncing = false;
async function syncBrain(force) {
  if (syncing) return;
  syncing = true;
  try {
    if (brainLocal.pending()) {
      const n = await brainLocal.flush(post);
      if (n) { toast('Synced', `${n} change(s) from the phone are now on your PC.`, 'info', 3500); refreshSoon(300); }
    }
    if (force || brainKit.age() > 600) {
      const k = await api.brainKit();
      brainKit.set(k);
      if (!brainLocal.pending()) brainLocal.adoptPcNotes(k.notes || '');
    }
    if (force || brainKb.age() > 6 * 3600) brainKb.refresh().catch(() => {});      // your study material, for smart answers with the PC off
  } catch (e) { /* try again on the next refresh */ }
  syncing = false;
}

function backOnline() {
  syncBrain(true);
  const jobs = brainLocal.pcJobs();
  if (jobs.length) {
    toast('Your PC is back', `${jobs.length} job(s) waited for it: ${jobs.map((j) => `"${j.command}"`).join(', ').slice(0, 160)}`, 'focus', 60000,
      { label: `RUN ${jobs.length > 1 ? 'THEM' : 'IT'} ON MY PC`, onClick: async () => { brainLocal.clearPcJobs(); for (const j of jobs) await ctx.send(`${j.command} on my PC`); } });
  }
}

function fireReminder(r) {
  haptics.warning();
  toast('Reminder', r.text, 'reminder', 60000);
  if (store.get('settings').voice) audio.playSequence([() => Promise.resolve({ speak: `Reminder: ${r.text}` })]);
  notify.localNotification('AURA · Reminder', r.text);
}

let refreshSoonTimer = null;
const refreshSoon = (ms = 700) => { clearTimeout(refreshSoonTimer); refreshSoonTimer = setTimeout(refresh, ms); };

// iPhone + iPad (+ more): AURA's spoken answer and anything it opens belong to the device that asked
const mine = (ev) => !ev.device || !storage.get('deviceId') || ev.device === storage.get('deviceId');

function onEvent(ev) {
  switch (ev.type) {
    case 'hello':
      if (ev.status) store.set('status', ev.status);
      break;
    case 'resync':
      refresh();
      break;
    case 'status':
      store.set('status', { ...store.get('status'), ...ev });
      if (ev.asking && store.get('settings').autoListen && Date.now() - lastPhoneCommandAt < 10 * 60e3) {
        haptics.warning();
      }
      if (ev.thinking === false) refreshSoon(400);
      break;
    case 'log': {
      const chat = [...(store.get('chat') || []), { role: ev.role, text: ev.text, t: ev.t }].slice(-120);
      store.set('chat', chat);
      store.persist();
      if (ev.role === 'aura' && ev.for_phone && mine(ev)) speakOnPhone(ev.text);
      refreshSoon();
      break;
    }
    case 'toast':
      toast(ev.title || 'AURA', ev.body || '', ev.kind || 'info');
      refreshSoon(300);
      break;
    case 'do':
      if (mine(ev)) handleDo(ev);
      break;
    case 'aegis':
      if (ev.event === 'intrusion') {
        haptics.warning();
        toast('⚠ AEGIS', `Someone touched your PC at ${new Date(ev.t * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}${ev.locked ? ' - it is locked' : ''}.`, 'reminder', 30000,
          { label: 'SEE PHOTOS', onClick: () => openTool('overwatch') });
      }
      break;
    case 'protocol':
      store.set('protocol', ev);
      if (ev.state === 'start') toast(`${ev.name} protocol`, `Engaged - ${ev.total} steps.`, 'focus', 4000);
      else if (ev.state === 'done') toast(`${ev.name} protocol`, 'Complete.', 'info', 4000);
      break;
    case 'briefing': {
      const s = store.get('state');
      if (s) store.set('state', { ...s, briefing: ev });
      break;
    }
    default:
      break;
  }
}

// AURA acting on the phone: open a site / app / file, open one of the app's screens, or offer the PC.
function handleDo(ev) {
  if (ev.action === 'tool') { openTool(ev.tool, ev.arg); return; }
  if (ev.action === 'close_tool') { closeTopPage(); return; }
  if (ev.action === 'open' && ev.url) {
    const label = ev.label || 'it';
    haptics.success();
    toast('Ready on your phone', `Tap to open ${label}.`, 'focus', 30000, { label: `OPEN ${String(label).toUpperCase().slice(0, 28)}`, onClick: () => openUrl(ev.url, label) });
    return;
  }
  if (ev.action === 'pc_offer' && ev.command) {
    toast('Needs your PC', `"${ev.command}"`, 'info', 30000, { label: 'DO IT ON MY PC', onClick: () => ctx.send(`${ev.command} on my PC`) });
  }
}

function openUrl(url, label) {
  if (url.startsWith('/v/')) { openTool('viewer', { link: url, title: label }); return; }       // a file from the PC
  if (/^https?:/i.test(url)) { window.open(url, '_blank', 'noopener'); return; }                  // Safari / universal links (YouTube, Spotify...)
  window.location.href = url;                                                                       // app links: whatsapp://, shortcuts://...
}

useLocal(brainLocal.route);
mind.setDo((ev) => handleDo(ev));
setInterval(() => brainLocal.checkReminders(fireReminder), 15000);

function startLive() {
  if (events) events.stop();
  events = connectEvents({
    onEvent,
    onOpen: () => { setConn('online'); refresh(); },
    onClose: (why) => { if (why === 'unreachable') setConn('offline'); },
  });
  quickProbe();
  refresh();
  clearInterval(pollTimer);
  pollTimer = setInterval(() => { if (document.visibilityState === 'visible') refresh(); }, 3000);
}

// ------------------------------------------------------------------ shell
function buildShell() {
  const root = document.getElementById('app');
  root.innerHTML = '';

  const pillText = el('span', null, 'CONNECTING');
  const pill = h('div', { class: 'pill', role: 'status', 'aria-live': 'polite' }, el('span', 'dot'), pillText);
  const dnd = h('button', { class: 'tb-btn', 'aria-label': 'Do Not Disturb', title: 'Do Not Disturb' }, icon('moon'));
  dnd.onclick = async () => {
    haptics.tap();
    const s = store.get('state') || {};
    try {
      const r = await api.dnd(!s.dnd);
      store.set('state', { ...s, dnd: r.dnd });
      toast('Do Not Disturb', r.dnd ? "On - AURA won't announce anything on the PC." : 'Off - AURA will speak up again.', 'info', 3500);
    } catch (e) { toast("Couldn't change it", e.message, 'error'); }
  };
  const brand = h('div', { class: 'brand' }, h('span', { html: LOGO }), el('span', 'name', 'AURA'));
  const top = h('header', { class: 'topbar' }, brand, pill, dnd);

  const offlineText = el('span', null, 'PC OFFLINE');
  const offline = h('div', { class: 'banner offline', hidden: true, role: 'alert' },
    offlineText, h('button', { onclick: () => { events && events.kick(); refresh(); } }, 'RETRY'));
  const asking = h('div', { class: 'banner asking', hidden: true, role: 'alert' },
    el('span', null, 'AURA IS ASKING YOU SOMETHING'), h('button', { onclick: () => switchTab('comms') }, 'ANSWER'));

  const wrap = h('main', { class: 'screens' });
  for (const t of TABS) {
    const sc = h('section', { class: 'screen' + (t.id === 'comms' ? ' flex' : ''), id: 'screen-' + t.id, 'aria-label': t.label });
    if (t.id === 'comms') sc.style.padding = '0';
    wrap.append(sc);
    screens[t.id] = sc;
    t.mod.mount(sc, ctx);
  }

  const bar = h('nav', { class: 'tabbar', 'aria-label': 'Sections' });
  for (const t of TABS) {
    const b = h('button', { class: 'tab', 'aria-label': t.label, role: 'tab' }, icon(t.icon), el('span', null, t.label));
    b.onclick = () => { haptics.tap(); switchTab(t.id); };
    tabButtons[t.id] = b;
    bar.append(b);
  }
  root.append(top, offline, asking, wrap, bar);
  ipad.enhance(ctx, { topbar: top, tabbar: bar, screens });       // iPad only: the big-screen command deck

  // live indicators
  const paintStatus = () => {
    const st = store.get('status') || {}, conn = store.get('conn'), rec = store.get('recording'), local = store.get('speakingLocal');
    const phoneBrain = conn === 'offline' && brainKit.ready();          // PC off: AURA runs on the phone
    const mode = conn === 'offline' && !phoneBrain ? 'offline' : rec ? 'listening' : st.asking ? 'asking' : (st.talking || local) ? 'speaking' : st.thinking ? 'thinking' : st.listening ? 'listening' : 'idle';
    pill.className = 'pill ' + (phoneBrain && mode === 'idle' ? 'phone' : mode);
    pillText.textContent = { offline: 'PC OFFLINE', listening: 'LISTENING', asking: 'AWAITING ANSWER', speaking: 'SPEAKING', thinking: phoneBrain ? 'THINKING · PHONE' : 'PROCESSING',
      idle: phoneBrain ? 'PHONE BRAIN' : conn === 'connecting' ? 'CONNECTING' : 'STANDING BY' }[mode];
    document.body.classList.toggle('thinking', mode === 'thinking');
    document.body.classList.toggle('speaking', mode === 'speaking');
    document.body.classList.toggle('listening', mode === 'listening' || mode === 'asking');
    document.body.classList.toggle('recording', !!rec);
    offline.hidden = conn !== 'offline';
    offlineText.textContent = brainKit.ready() ? 'PC OFF · AURA IS RUNNING ON YOUR PHONE' : "PC OFFLINE · open AURA once with your PC on to set up the phone brain";
    asking.hidden = !st.asking || activeTab === 'comms';
  };
  ['status', 'conn', 'recording', 'speakingLocal'].forEach((k) => store.on(k, paintStatus));
  store.on('state', (s) => {
    dnd.classList.toggle('on', !!(s && s.dnd));
    dnd.setAttribute('aria-pressed', s && s.dnd ? 'true' : 'false');
    const n = (s && s.emails && s.emails.length) || 0;
    setBadge('more', n);
  });
  paintStatus();
  switchTab(store.get('lastTab') || 'core');
}

function setBadge(tab, n) {
  const b = tabButtons[tab];
  if (!b) return;
  let badge = b.querySelector('.badge');
  if (!n) { if (badge) badge.remove(); return; }
  if (!badge) { badge = el('span', 'badge'); b.append(badge); }
  badge.textContent = n > 9 ? '9+' : n;
}

export function switchTab(id) {
  if (!screens[id]) id = 'core';
  activeTab = id;
  for (const t of TABS) {
    screens[t.id].classList.toggle('active', t.id === id);
    tabButtons[t.id].classList.toggle('active', t.id === id);
    tabButtons[t.id].setAttribute('aria-selected', t.id === id ? 'true' : 'false');
    if (t.id === id && t.mod.shown) t.mod.shown();
  }
  store.set('lastTab', id);
  try { localStorage.setItem('aura.lastTab', JSON.stringify(id)); } catch (e) { /* ignore */ }
  const st = store.get('status') || {};
  const asking = document.querySelector('.banner.asking');
  if (asking) asking.hidden = !st.asking || id === 'comms';
}

// ------------------------------------------------------------------ boot
function bootSequence() {
  return new Promise((resolve) => {
    const lines = [['Secure link to PC', 'OK'], ['Voice · speech recognition', 'OK'], ['Intent router · 55 actions', 'OK'],
      ['Tasks · reminders · focus timer', 'OK'], ['Memory store', 'OK'], ['Mobile render pipeline', 'ONLINE']];
    const box = h('div', { class: 'boot-lines' }), bar = el('i');
    const boot = h('div', { class: 'boot', 'aria-hidden': 'true' }, h('div', { class: 'boot-inner' },
      el('div', 'boot-logo', 'AURA'), el('div', 'boot-sub', 'PERSONAL INTELLIGENCE SYSTEM · MOBILE'), box, h('div', { class: 'boot-bar' }, bar)));
    document.body.append(boot);
    const fast = device.reducedMotion() || sessionStorage.getItem('aura.booted');
    let i = 0;
    const next = () => {
      if (i < lines.length) {
        box.append(h('div', {}, el('span', null, '› ' + lines[i][0]), el('span', 'ok', `[${lines[i][1]}]`)));
        i++; bar.style.width = `${(i / lines.length) * 100}%`;
        setTimeout(next, fast ? 0 : 110);
      } else {
        try { sessionStorage.setItem('aura.booted', '1'); } catch (e) { /* ignore */ }
        setTimeout(() => { boot.classList.add('gone'); setTimeout(() => boot.remove(), 520); resolve(); }, fast ? 0 : 250);
      }
    };
    next();
  });
}

// AURA's own PC window (spatial_window.py) opens /?pc=<this run's local key>&spatial=<what to bring up>
const LINK = new URLSearchParams(location.search);
if (LINK.get('pc')) setToken('pc.' + LINK.get('pc'));

function startMain() {
  bindTools(ctx);
  if (LINK.has('tool') && LINK.has('pc')) {
    const tool = LINK.get('tool');
    history.replaceState(null, '', '/');
    setTimeout(() => openTool(tool, { standalone: true }), 350);
  }
  if (LINK.has('spatial')) {
    const query = LINK.get('spatial') || null, standalone = LINK.has('pc');
    history.replaceState(null, '', '/');
    setTimeout(() => openTool('spatial', { query, standalone }), 350);
  }
  // a Siri Shortcut / home-screen link can open a tab directly: /?tab=comms
  const wanted = new URLSearchParams(location.search).get('tab');
  if (wanted) { try { localStorage.setItem('aura.lastTab', JSON.stringify(wanted)); } catch (e) { /* ignore */ } history.replaceState(null, '', '/'); }
  store.set('lastTab', (() => { try { return JSON.parse(localStorage.getItem('aura.lastTab')); } catch (e) { return 'core'; } })() || 'core');
  buildShell();
  startLive();
}

async function init() {
  applyTheme();
  store.on('settings', applyTheme);
  matchMedia('(prefers-color-scheme: light)').addEventListener?.('change', applyTheme);
  device.trackViewport();
  notify.registerServiceWorker();
  document.addEventListener('touchstart', audio.unlock, { once: true, passive: true });
  document.addEventListener('click', audio.unlock, { once: true });

  device.onVisibility((visible) => {
    if (!visible) { audio.stop(); return; }
    notify.clearNotifications();
    if (isPaired() && events) { events.kick(); quickProbe(); refresh(); }
  });
  device.onNetwork((online) => { if (online && events) { events.kick(); refresh(); } else if (!online) setConn('offline'); });
  onLostPairing(() => {
    if (events) events.stop();
    clearInterval(pollTimer);
    try { localStorage.removeItem('aura.brain.kit'); } catch (e) { /* ignore */ }      // removed on the PC: the phone's copy of the keys goes too
    toast('Unpaired', 'This phone was removed from AURA on your PC. Pair it again to continue.', 'error', 8000);
    showPairing(() => startMain());
  });
  // Android back button / browser back closes the top page instead of leaving the app
  history.pushState({ aura: 1 }, '');
  window.addEventListener('popstate', () => { if (closeTopPage()) history.pushState({ aura: 1 }, ''); });

  await bootSequence();
  if (!isPaired()) { showPairing(() => startMain()); return; }
  startMain();
  notify.clearNotifications();
  // boot.js downloads updates in the background; they apply the next time AURA opens
  const upd = setInterval(() => {
    if (window.__auraUpdateReady) {
      clearInterval(upd);
      toast('Update ready', 'A new version of AURA is ready.', 'focus', 20000, { label: 'RESTART AURA', onClick: () => location.reload() });
    }
  }, 5000);
}

init();
