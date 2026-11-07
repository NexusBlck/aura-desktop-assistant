// SYSTEM - THIS PHONE first (what the phone really lets a web app measure: load, smoothness, network,
// storage, model / iOS / chip), then YOUR PC (the HUD's vitals, processes, modules, PC controls).
import { el, h, panel, gauge, drawSpark, button, confirm, toast } from '../ui/components.js';
import { fmtRate, fmtSize, fmtDuration, pad } from '../core/format.js';
import * as vitals from '../platform/vitals.js';
import { isIPad } from '../platform/device.js';
import * as brainKit from '../brain/kit.js';
import * as brainLocal from '../brain/local.js';

let ctx, refs = {}, uptimeBase = 0, uptimeAt = 0;
const ph = {};

const kv = (label) => { const b = el('b', null, '--'); return [h('div', { class: 'kv' }, el('span', null, label), b), b]; };

// ------------------------------------------------------------------ THIS PHONE
function phonePanels() {
  ph.load = gauge('LOAD %', '#3ad6ff');      // how hard this phone / iPad works to run AURA
  ph.fps = gauge('FPS', '#a78bfa');
  ph.spark = h('canvas', { class: 'spark', 'aria-hidden': 'true' });
  ph.rows = {};
  const row = (k, label) => { const [r, b] = kv(label); ph.rows[k] = b; return r; };
  ph.storageBar = el('i');
  ph.picker = h('select', { class: 'input', style: 'margin-top:8px', hidden: true, 'aria-label': 'Which model is this?' });
  ph.picker.onchange = () => vitals.pickModel(ph.picker.value);
  ph.code = el('div', 'code', 'PHONE');
  const core = panel(isIPad ? 'THIS IPAD' : 'THIS PHONE', null, h('div', { class: 'gauges' }, ph.load.node, ph.fps.node), ph.spark,
    row('model', 'MODEL'), row('chip', 'CHIP'), row('cores', 'CPU CORES'), row('ram', 'RAM'), row('os', 'SYSTEM'), row('screen', 'SCREEN'),
    row('mode', 'RUNNING AS'), row('storage', 'AURA STORAGE'), h('div', { class: 'bar' }, ph.storageBar), row('power', 'BATTERY'), ph.picker,
    el('div', 'help', "LOAD = how hard this device is working to run AURA; FPS = how smooth the screen is. iOS and iPadOS don't let any app read the device's total RAM in use, battery level or temperature, so those stay hidden."));
  core.querySelector('.ph').append(ph.code);

  ph.down = el('div', 'big', '--'); ph.upRate = el('div', 'big up', '--');
  ph.netSpark = h('canvas', { class: 'spark', 'aria-hidden': 'true' });
  const [pingRow, ping] = kv('PING'), [onRow, online] = kv('CONNECTION');
  ph.ping = ping; ph.online = online;
  const net = panel(isIPad ? 'IPAD NETWORK' : 'PHONE NETWORK', 'NET.IO', h('div', { class: 'net-row' },
    h('div', {}, el('div', 'cap', '▼ AURA DOWN'), ph.down), h('div', {}, el('div', 'cap', '▲ AURA UP'), ph.upRate)), ph.netSpark, pingRow, onRow);

  const [upRow, up] = kv('AURA OPEN FOR'), [brRow, br] = kv('PHONE BRAIN'), [syRow, sy] = kv('WAITING TO SYNC');
  Object.assign(ph, { uptime: up, brain: br, sync: sy });
  return [core, net, panel(isIPad ? 'AURA ON THIS IPAD' : 'AURA ON THIS PHONE', 'APP', upRow, brRow, syRow)];
}

function renderPhone(v) {
  const d = v.device || {}, r = ph.rows, sp = d.specs;
  ph.load.set(v.load);
  ph.fps.set((v.fps / (v.maxFps || 60)) * 100);
  ph.fps.node.querySelector('.val').textContent = v.fps || '--';
  ph.code.textContent = d.cores ? `${d.cores} CORES` : 'PHONE';
  r.model.textContent = d.model || '--';
  r.chip.textContent = sp ? `${sp.chip} · ${sp.cpu}` : d.chip || '--';
  r.cores.textContent = d.cores || '--';
  r.ram.textContent = d.ramGb ? `${d.ramGb} GB · use hidden by iOS` : 'hidden by iOS';
  r.os.textContent = d.os || '--';
  r.screen.textContent = `${d.screen || '--'} · ${v.maxFps} Hz`;
  r.mode.textContent = d.standalone ? 'HOME SCREEN APP' : 'IN THE BROWSER';
  if (v.storage) {
    r.storage.textContent = v.storage.quota ? `${fmtSize(v.storage.used)} of ${fmtSize(v.storage.quota)} allowed` : fmtSize(v.storage.used);
    ph.storageBar.style.width = v.storage.quota ? `${Math.max(1, (v.storage.used / v.storage.quota) * 100)}%` : '2%';
  }
  r.power.textContent = v.battery ? `${v.battery.level}%${v.battery.charging ? ' · CHARGING' : ''}` : 'hidden by iOS';
  if (d.guessed && d.candidates.length && !ph.picker.options.length) {
    ph.picker.append(h('option', { value: '' }, `Which ${d.kind} is this? (for exact specs)`), ...d.candidates.map((m) => h('option', { value: m }, m)));
    ph.picker.hidden = false;
  }
  if (!d.guessed) ph.picker.hidden = true;
  ph.down.textContent = fmtRate(v.down); ph.upRate.textContent = fmtRate(v.up);
  ph.ping.textContent = v.ping != null ? `${v.ping} ms → ${v.pingTarget}` : v.pingTarget || '--';
  ph.online.textContent = v.online ? 'ONLINE' : 'NO INTERNET';
  ph.brain.textContent = brainKit.ready() ? (ctx.store.get('conn') === 'offline' ? 'RUNNING (PC OFF)' : 'READY') : 'NOT SET UP';
  ph.sync.textContent = `${brainLocal.pending()} change(s)`;
  if (document.getElementById('screen-system')?.classList.contains('active')) {
    drawSpark(ph.spark, [
      { data: v.fpsHistory.map((f) => Math.min(100, (f / (v.maxFps || 60)) * 100)), max: 100, color: '#a78bfa', fill: 'rgba(167,139,250,0.08)' },
      { data: v.loadHistory, max: 100, color: '#3ad6ff', fill: 'rgba(58,214,255,0.12)' }]);
    const m = Math.max(...v.downHistory, ...v.upHistory, 1024);
    drawSpark(ph.netSpark, [{ data: v.downHistory, max: m, color: '#3ad6ff', fill: 'rgba(58,214,255,0.12)' },
      { data: v.upHistory, max: m, color: '#a78bfa', fill: 'rgba(167,139,250,0.08)' }]);
  }
}

// ------------------------------------------------------------------ YOUR PC
export function mount(root, c) {
  ctx = c;
  vitals.start();
  const phoneEls = phonePanels();

  refs.cpu = gauge('CPU %', '#3ad6ff');
  refs.ram = gauge('RAM %', '#a78bfa');
  refs.cpuSpark = h('canvas', { class: 'spark', 'aria-hidden': 'true' });
  const [mem, memV] = kv('MEMORY'), [disk, diskV] = kv('DISK C:'), [pow, powV] = kv('POWER');
  refs.diskBar = el('i'); refs.diskWrap = h('div', { class: 'bar' }, refs.diskBar);
  refs.battBar = el('i'); refs.battWrap = h('div', { class: 'bar' }, refs.battBar);
  Object.assign(refs, { memV, diskV, powV });
  refs.cores = el('div', 'code', 'SYS.01');
  const corePanel = panel('PC CORE', null, h('div', { class: 'gauges' }, refs.cpu.node, refs.ram.node), refs.cpuSpark, mem, disk, refs.diskWrap, pow, refs.battWrap);
  corePanel.querySelector('.ph').append(refs.cores);

  refs.down = el('div', 'big', '--'); refs.up = el('div', 'big up', '--');
  refs.netSpark = h('canvas', { class: 'spark', 'aria-hidden': 'true' });
  const net = panel('PC NETWORK', 'NET.IO', h('div', { class: 'net-row' },
    h('div', {}, el('div', 'cap', '▼ DOWNLOAD'), refs.down), h('div', {}, el('div', 'cap', '▲ UPLOAD'), refs.up)), refs.netSpark);

  refs.procs = h('ul', { class: 'list procs' });
  refs.mods = h('div', { class: 'mods' });
  refs.uptime = el('b', null, '00:00:00');
  refs.pc = el('b', null, '--');
  refs.pcNote = el('div', 'help', '');

  const onPc = (action, msg) => () => ctx.api.pc(action).then(() => toast('PC', msg, 'info', 2500)).catch((e) => toast("Couldn't do that", e.message, 'error'));
  const pcActions = h('div', { class: 'row-btns' },
    button('SHOW HUD', onPc('show_hud', 'AURA is open on your PC.')),
    button('DASHBOARD', onPc('dashboard', 'The dashboard is open on your PC.')),
    button('SCHEDULE', onPc('schedule', 'Your schedule is open on your PC.')),
    button('STOP SPEAKING', () => ctx.stopSpeaking()));
  const quit = button('QUIT AURA ON PC', async () => {
    if (await confirm('QUIT AURA?', "AURA will close on your PC, and this phone can't reach it again until you start AURA on the PC.", 'QUIT', true)) {
      await ctx.api.pc('quit'); toast('AURA', 'AURA is shutting down on your PC.', 'info');
    }
  }, 'danger block');
  quit.style.marginTop = '8px';

  root.append(...phoneEls, el('div', 'section-label', 'YOUR PC'), refs.pcNote, corePanel, net, panel('PC PROCESSES', 'CPU · RAM', refs.procs),
    panel('MODULES', 'LINK', refs.mods, h('div', { class: 'kv', style: 'margin-top:10px' }, el('span', null, 'PC'), refs.pc),
      h('div', { class: 'kv' }, el('span', null, 'AURA UPTIME'), refs.uptime)),
    panel('PC CONTROLS', 'TRAY', pcActions, quit));

  ctx.store.on('state', render);
  ctx.store.on('conn', () => render(ctx.store.get('state')));
  if (ctx.store.get('state')) render(ctx.store.get('state'));
  vitals.on(renderPhone);
  renderPhone(vitals.get());
  setInterval(() => {
    ph.uptime.textContent = fmtDuration((Date.now() - vitals.get().startedAt) / 1000);
    const up = uptimeBase + (uptimeAt ? (Date.now() - uptimeAt) / 1000 : 0);
    refs.uptime.textContent = `${pad(Math.floor(up / 3600))}:${pad(Math.floor((up % 3600) / 60))}:${pad(Math.floor(up % 60))}`;
  }, 1000);
}

export function shown() { render(ctx.store.get('state')); renderPhone(vitals.get()); }

let modSig = '';
function render(d) {
  if (!d) return;
  const offline = ctx.store.get('conn') === 'offline';
  refs.pcNote.textContent = offline ? 'Your PC is off or unreachable - these are its last readings.' : `Live from ${d.pc || 'your PC'}.`;
  const s = d.system || {};
  refs.cpu.set(s.cpu); refs.ram.set(s.ram);
  refs.cores.textContent = s.cores ? `${s.cores} THREADS` : 'SYS.01';
  refs.memV.textContent = s.ram_total_gb ? `${((s.ram_total_gb * (s.ram || 0)) / 100).toFixed(1)} / ${s.ram_total_gb} GB` : '--';
  if (s.disk) {
    refs.diskV.textContent = `${s.disk.used_gb} / ${s.disk.total_gb} GB`;
    refs.diskBar.style.width = s.disk.percent + '%';
    refs.diskWrap.classList.toggle('warn', s.disk.percent > 90);
  }
  if (s.battery != null) {
    refs.powV.textContent = Math.round(s.battery) + '%' + (s.plugged ? ' · CHARGING' : ' · BATTERY');
    refs.battBar.style.width = s.battery + '%';
    refs.battWrap.classList.toggle('warn', s.battery < 20 && !s.plugged);
  } else { refs.powV.textContent = 'MAINS POWER'; refs.battBar.style.width = '100%'; }
  refs.down.textContent = fmtRate(s.down); refs.up.textContent = fmtRate(s.up);
  if (document.getElementById('screen-system')?.classList.contains('active')) {
    drawSpark(refs.cpuSpark, [
      { data: s.ram_history, max: 100, color: '#a78bfa', fill: 'rgba(167,139,250,0.08)' },
      { data: s.cpu_history, max: 100, color: '#3ad6ff', fill: 'rgba(58,214,255,0.12)' }]);
    const netMax = Math.max(...(s.down_history || [0]), ...(s.up_history || [0]), 1024);
    drawSpark(refs.netSpark, [
      { data: s.down_history, max: netMax, color: '#3ad6ff', fill: 'rgba(58,214,255,0.12)' },
      { data: s.up_history, max: netMax, color: '#a78bfa', fill: 'rgba(167,139,250,0.08)' }]);
  }
  refs.procs.innerHTML = '';
  (s.top || []).forEach((p) => refs.procs.append(h('li', {}, el('span', null, p.name), el('span', 'c', p.cpu.toFixed(1) + '%'), el('span', 'm', p.ram_mb + ' MB'))));
  if (!(s.top || []).length) refs.procs.append(h('li', {}, el('span', 'empty', 'Sampling…')));

  const sig = JSON.stringify(d.modules || {});
  if (sig !== modSig) {
    modSig = sig; refs.mods.innerHTML = '';
    Object.entries(d.modules || {}).forEach(([k, on]) => refs.mods.append(h('span', { class: 'mod' + (on ? ' on' : ''), title: k + (on ? ' connected' : ' not set up') }, el('i'), k)));
  }
  refs.pc.textContent = d.pc || '--';
  uptimeBase = d.uptime || 0; uptimeAt = Date.now();
}
