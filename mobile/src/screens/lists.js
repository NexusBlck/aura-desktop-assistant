// LISTS - the HUD's Tasks / Reminders / Notes / Recent tabs.
import { el, h, empty, toast } from '../ui/components.js';
import { fmtCountdown, whenOf } from '../core/format.js';
import * as storage from '../platform/storage.js';
import * as haptics from '../platform/haptics.js';

let ctx, refs = {}, tab = storage.get('listsTab', 'tasks');
const TABS = [['tasks', 'TASKS'], ['reminders', 'REMINDERS'], ['notes', 'NOTES'], ['recent', 'RECENT']];

export function mount(root, c) {
  ctx = c;
  const seg = h('div', { class: 'seg', role: 'tablist' });
  refs.tabBtns = {};
  refs.bodies = {};
  for (const [id, label] of TABS) {
    const n = el('span', 'n');
    const b = h('button', { role: 'tab' }, label, n);
    b.onclick = () => { haptics.tap(); select(id); };
    refs.tabBtns[id] = { b, n };
    seg.append(b);
    refs.bodies[id] = h('div', { role: 'tabpanel', 'data-title': label });
  }
  root.append(seg, ...Object.values(refs.bodies));

  // tasks
  refs.taskList = h('ul', { class: 'list' });
  const taskInput = h('input', { class: 'input', placeholder: 'Add a task', maxlength: '200', enterkeyhint: 'done', 'aria-label': 'New task' });
  taskInput.addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter' || !taskInput.value.trim()) return;
    const v = taskInput.value.trim(); taskInput.value = '';
    try { await ctx.api.addTask(v); haptics.success(); ctx.refresh(); } catch (err) { taskInput.value = v; toast("Couldn't add it", err.message, 'error'); }
  });
  refs.bodies.tasks.append(refs.taskList, h('div', { class: 'add-row' }, taskInput));

  // reminders
  refs.remList = h('ul', { class: 'list' });
  const remInput = h('input', { class: 'input', placeholder: 'e.g. in 20 minutes call Ali / at 6pm BJJ', maxlength: '200', enterkeyhint: 'done', 'aria-label': 'New reminder' });
  remInput.addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter' || !remInput.value.trim()) return;
    const v = remInput.value.trim();
    remInput.value = ''; remInput.disabled = true; remInput.placeholder = 'Setting reminder…';
    try {
      const r = await ctx.api.addReminder(/remind/i.test(v) ? v : 'remind me ' + v);
      toast('Reminders', r.message || 'Done.', 'reminder', 4000);
    } catch (err) { remInput.value = v; toast("Couldn't set it", err.message, 'error'); }
    finally { remInput.disabled = false; remInput.placeholder = 'e.g. in 20 minutes call Ali / at 6pm BJJ'; ctx.refresh(); }
  });
  refs.bodies.reminders.append(refs.remList, h('div', { class: 'add-row' }, remInput));

  // notes (autosave, like the HUD)
  refs.notes = h('textarea', { class: 'textarea', placeholder: 'Quick notes - saved automatically.', 'aria-label': 'Notes', style: 'min-height: 50vh' });
  refs.notesStatus = h('div', { class: 'help', style: 'text-align:right;margin-top:6px' }, '—');
  let timer = null;
  refs.notes.addEventListener('input', () => {
    refs.notesStatus.textContent = 'SAVING…'; clearTimeout(timer);
    timer = setTimeout(async () => {
      try { await ctx.api.saveNotes(refs.notes.value); refs.notesStatus.textContent = 'SAVED · ' + new Date().toLocaleTimeString(); }
      catch (e) { refs.notesStatus.textContent = "COULDN'T SAVE - " + e.message; }
    }, 700);
  });
  refs.bodies.notes.append(refs.notes, refs.notesStatus);

  // recent (web briefings + file searches)
  refs.recentList = h('ul', { class: 'list' });
  refs.bodies.recent.append(refs.recentList);

  ctx.store.on('state', render);
  if (ctx.store.get('state')) render(ctx.store.get('state'));
  select(tab);
  setInterval(tickReminders, 1000);
}

export function shown() { if (tab === 'notes' || window.matchMedia('(min-width: 768px)').matches) loadNotes(); }    // iPad shows every list at once

function select(id) {
  tab = id; storage.set('listsTab', id);
  for (const [k] of TABS) {
    refs.tabBtns[k].b.classList.toggle('active', k === id);
    refs.tabBtns[k].b.setAttribute('aria-selected', k === id ? 'true' : 'false');
    refs.bodies[k].hidden = k !== id;
  }
  if (id === 'notes') loadNotes();
}

async function loadNotes() {
  if (document.activeElement === refs.notes) return;
  try { const r = await ctx.api.notes(); refs.notes.value = r.text || ''; refs.notesStatus.textContent = 'SAVED'; }
  catch (e) { refs.notesStatus.textContent = e.message; }
}

let taskSig = '', remSig = '', recentSig = '', reminders = [];
function render(s) {
  if (!s) return;
  const tasks = s.tasks || [];
  refs.tabBtns.tasks.n.textContent = tasks.length || '';
  const ts = JSON.stringify(tasks);
  if (ts !== taskSig) {
    taskSig = ts;
    const ul = refs.taskList; ul.innerHTML = '';
    if (!tasks.length) ul.append(h('li', {}, empty('Nothing pending. Add one below or say "add … to my list".')));
    tasks.forEach((text) => {
      const li = h('li');
      const box = h('button', { class: 'check', 'aria-label': `Mark "${text}" as done` });
      box.onclick = async () => {
        li.classList.add('done'); haptics.success();
        try { await ctx.api.completeTask(text); } catch (e) { li.classList.remove('done'); toast("Couldn't update", e.message, 'error'); }
        taskSig = ''; setTimeout(ctx.refresh, 400);
      };
      li.append(box, el('span', 'txt', text));
      ul.append(li);
    });
  }

  reminders = s.reminders || [];
  refs.tabBtns.reminders.n.textContent = reminders.length || '';
  const rs = JSON.stringify(reminders.map((r) => r.id));
  if (rs !== remSig) {
    remSig = rs;
    const ul = refs.remList; ul.innerHTML = '';
    if (!reminders.length) ul.append(h('li', {}, empty('No reminders. Try: "in 20 minutes call Ali".')));
    reminders.forEach((r) => {
      const due = new Date(r.due);
      const count = el('span', 'count'); count.dataset.due = due.getTime();
      const x = h('button', { class: 'x', 'aria-label': `Cancel reminder: ${r.text}` }, '×');
      x.onclick = async () => {
        try { await ctx.api.cancelReminder(r.id); haptics.tap(); } catch (e) { toast("Couldn't cancel", e.message, 'error'); }
        remSig = ''; ctx.refresh();
      };
      ul.append(h('li', {}, h('div', { class: 'txt' }, el('div', null, r.text),
        el('div', 'meta', due.toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }))), count, x));
    });
    tickReminders();
  }

  const recent = s.recent || [];
  const rcs = JSON.stringify(recent);
  if (rcs !== recentSig) {
    recentSig = rcs;
    const ul = refs.recentList; ul.innerHTML = '';
    if (!recent.length) ul.append(h('li', {}, empty('Your web briefings and file searches will show up here.')));
    recent.forEach((it) => {
      const li = h('li', { class: 'link', role: 'button' }, el('span', 'kind', it.kind === 'briefing' ? 'WEB' : 'FILES'),
        h('div', { class: 'txt' }, el('div', null, it.title), el('div', 'meta', whenOf(it.time))), el('span', 'chev', '›'));
      li.onclick = () => {
        haptics.tap();
        if (it.kind === 'briefing') ctx.openTool('viewer', { kind: 'briefings', path: it.target, title: it.title });
        else ctx.openTool('files', { kind: 'search', path: it.target.split(/[\\/]/).pop(), title: it.title });
      };
      ul.append(li);
    });
  }
}

function tickReminders() {
  document.querySelectorAll('#screen-lists .count').forEach((c) => { c.textContent = fmtCountdown((+c.dataset.due - Date.now()) / 1000); });
}
