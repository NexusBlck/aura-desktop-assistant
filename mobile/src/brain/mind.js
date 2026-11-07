// The phone brain's mind: what main.py + intent.py do on the PC, done on the phone while the PC is off.
// One AI call understands the message and decides what to do (answer, add a task, set a reminder, search
// the web, open an app...); the actions run on the phone and sync to the PC later (local.js).
import * as store from '../core/store.js';
import * as audio from '../platform/audio.js';
import * as kit from './kit.js';
import * as local from './local.js';
import { ask, askJSON } from './llm.js';
import { speechChunks } from '../core/format.js';
import { deep, relevantLessons } from './deep.js';

// iPhone apps AURA can open by name (same list as phone_actions.APP_LINKS on the PC)
const APP_LINKS = {
  whatsapp: 'whatsapp://', instagram: 'instagram://', youtube: 'youtube://', spotify: 'spotify:', maps: 'maps://', 'apple maps': 'maps://',
  'google maps': 'comgooglemaps://', gmail: 'googlegmail://', calendar: 'calshow://', photos: 'photos-redirect://', notes: 'mobilenotes://',
  messages: 'sms:', phone: 'tel:', mail: 'message://', facetime: 'facetime://', music: 'music://', 'apple music': 'music://', shortcuts: 'shortcuts://',
  telegram: 'tg://', snapchat: 'snapchat://', twitter: 'twitter://', x: 'twitter://', linkedin: 'linkedin://', discord: 'discord://', netflix: 'nflx://',
  reddit: 'reddit://', uber: 'uber://', zoom: 'zoomus://', teams: 'msteams://', outlook: 'ms-outlook://', word: 'ms-word://', excel: 'ms-excel://',
  powerpoint: 'ms-powerpoint://', 'google drive': 'googledrive://', notion: 'notion://', obsidian: 'obsidian://', roblox: 'roblox://',
  chrome: 'googlechrome://', facebook: 'fb://', pinterest: 'pinterest://', threads: 'barcelona://', files: 'shareddocuments://', 'app store': 'itms-apps://',
};
const TOOLS = ['vision', 'explain', 'nexus', 'nexus-live', 'nexus-babel', 'nexus-echo', 'nexus-radar', 'nexus-pulse', 'nexus-protocols', 'journal', 'log', 'settings'];

let doAction = () => {};
/** app.js passes its "AURA acting on the phone" handler (open a link / a screen). */
export function setDo(fn) { doAction = fn; }

function localISO(d = new Date()) {
  const off = -d.getTimezoneOffset(), sign = off >= 0 ? '+' : '-', p = (n) => String(Math.floor(Math.abs(n))).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}${sign}${p(off / 60)}:${p(off % 60)}`;
}
const nowLine = () => `${new Date().toLocaleString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}, ${localISO()}`;

export function describeDue(due) {
  const d = new Date(due), mins = Math.round((d - Date.now()) / 60000);
  if (mins < 60) return `in ${Math.max(1, mins)} minute${mins === 1 ? '' : 's'}`;
  const sameDay = d.toDateString() === new Date().toDateString();
  const tmrw = d.toDateString() === new Date(Date.now() + 864e5).toDateString();
  const t = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  return sameDay ? `at ${t}` : tmrw ? `tomorrow at ${t}` : `on ${d.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'short' })} at ${t}`;
}

export async function parseReminder(text) {
  const m = /\bin (\d+)\s*(min|minute|minutes|hour|hours|hr|hrs)\b/i.exec(text);
  if (m) {
    const due = new Date(Date.now() + Number(m[1]) * (/^h/i.test(m[2]) ? 3600e3 : 60e3));
    const what = text.replace(m[0], '').replace(/^\s*(remind me|reminder)\s*(to)?\s*/i, '').replace(/\s+to\s*$/i, '').trim();
    return { text: what || 'Reminder', due: due.toISOString() };
  }
  const d = await askJSON({ maxTokens: 200, temperature: 0, prompt: `Now: ${nowLine()}.
Extract the reminder from: "${text}". Respond ONLY with JSON: {"text": "what to be reminded of, short imperative", "due": "ISO 8601 date-time with the same UTC offset as now, or empty if no time was given"}` });
  return { text: String(d.text || text).slice(0, 200), due: d.due || '' };
}

function speak(text) {
  if (!text || !store.get('settings').voice || document.visibilityState !== 'visible') return;
  audio.playSequence(speechChunks(text).map((p) => () => Promise.resolve({ speak: p })));
}

function system(text) {
  const s = store.get('state') || {};
  const lessons = relevantLessons(text || '');
  const tasks = (s.tasks || []).slice(0, 15).map((t) => `- ${t}`).join('\n') || '(none)';
  const rems = (s.reminders || []).slice(0, 10).map((r) => `- ${r.text} (${new Date(r.due).toLocaleString()})`).join('\n') || '(none)';
  const k = kit.get() || {};
  return `You are AURA, the user's personal AI assistant - warm, sharp, brief - talking through their PHONE.
Their PC (where AURA normally runs) is OFF right now, so you are running on the phone by yourself.
${kit.who()}
${k.location ? `They live in ${k.location}.` : ''} Now: ${nowLine()}.
Their open tasks:
${tasks}
Their reminders:
${rems}
${lessons.length ? 'Lessons from their past corrections - follow them:\n' + lessons.map((l) => '- ' + l).join('\n') + '\n' : ''}
Decide what to do with their message and respond ONLY with JSON:
{"reply": "what you say back - 1-3 natural spoken sentences, no markdown, no lists",
 "actions": [ ...zero or more of these... ],
 "remember": [{"category": "facts|projects|preferences", "text": "a durable fact about them worth remembering, only if they shared one"}],
 "difficulty": "easy" | "hard" - hard = needs careful thinking or expertise (medicine / science explanations, exam or clinical questions,
     maths, comparisons, planning, coding) OR asks about their OWN material (lectures, notes, study guide, "quiz me"). Chat / simple facts = easy.
     For a hard question with no other action, leave "reply" empty - AURA will think it through.
 "correction": "" or, if they're correcting you (misunderstood, wrong fact, how you should behave), the lesson as one clear line}
Actions:
 {"type": "task_add", "text": "..."}            {"type": "task_done", "text": "which task"}
 {"type": "reminder_add", "text": "short imperative", "due": "ISO 8601 with the same UTC offset as now"}
 {"type": "reminder_cancel", "text": "which reminder"}
 {"type": "note_add", "text": "..."}
 {"type": "search", "query": "..."}             - anything needing current or factual web info (news, weather, prices, sports, people, places, opening hours, research). Leave "reply" empty then.
 {"type": "open", "url": "https://... or app link", "label": "what it is"} - websites; YouTube search: https://www.youtube.com/results?search_query=...;
     Spotify search: https://open.spotify.com/search/...; directions: https://maps.apple.com/?daddr=...; call: tel:...
 {"type": "app", "name": "whatsapp|instagram|spotify|maps|..."} - open an app on the phone / iPad
 {"type": "shortcut", "name": "..."}            - run an iOS Shortcut (alarms, settings, messages)
 {"type": "tool", "tool": "${TOOLS.join('|')}"} - open one of AURA's screens (vision = camera)
 {"type": "journal", "mood": 1-10 or null, "good": "...", "mind": "..."}
 {"type": "log_session", "kind": "study|gym|bjj|...", "rating": 1-10 or null}
 {"type": "pc_job", "command": "the request as they said it"} - ONLY things that truly need the PC: typing/clicking on the PC, PC apps or files,
     the browser agent, building apps, email (Gmail lives on the PC), CDC WONDER, shutting the PC down. Tell them it will wait for the PC.
Be honest: never claim to have done something you didn't put in actions.`;
}

async function search(query, question) {
  return ask({ search: true, maxTokens: 700, temperature: 0.3, prompt: `${kit.who()}\nNow: ${nowLine()}.
The user asked: "${question}". Search the web for: ${query}
Answer in 2-4 natural spoken sentences (it's read aloud): the direct answer first, metric units (°C, km), no markdown, no links, no citations.` });
}

let busy = Promise.resolve();
/** Handles one message to AURA on the phone (typed, spoken, a protocol step). One at a time, in order. */
export function handle(text) {
  text = String(text || '').trim();
  if (!text) return Promise.resolve();
  const run = busy.then(() => think(text)).catch(() => {});
  busy = run;
  return run;
}

async function think(text) {
  local.addChat('you', text);
  store.set('status', { ...store.get('status'), thinking: true });
  let reply = '';
  try {
    const history = (store.get('chat') || []).slice(-11, -1).map((m) => ({ role: m.role === 'you' ? 'user' : 'assistant', content: m.text }));
    const d = await askJSON({ system: system(text), messages: [...history, { role: 'user', content: text }], maxTokens: 700, temperature: 0.4 });
    reply = String(d.reply || (d.actions ? '' : d.text) || '').trim();
    if (d.correction) local.lesson(String(d.correction), text);
    else if (/(from now on|don'?t ever|stop (doing|saying)|call me|i meant|that'?s (wrong|not right)|you got (it|that) wrong)/i.test(text)) {
      const pref = (Array.isArray(d.remember) ? d.remember : []).find((m) => m && m.text);
      if (pref) local.lesson(String(pref.text), text);                 // a behaviour instruction: keep it as a lesson too
    }
    const acts = Array.isArray(d.actions) ? d.actions : [];
    const hard = d.difficulty === 'hard' || /\d\s*(mg|kg|ml|%|units?)\b.*\b(how much|per|dose|total)\b|\b(quiz|test) me\b|\bmy (notes|lectures?|study guide|module)\b/i.test(text);
    if (hard && acts.every((a) => a && a.type === 'search')) {
      // think it through: plan -> your material / exact maths / the web -> strongest model -> second opinion (deep.js)
      store.set('status', { ...store.get('status'), thinking: true });
      reply = await deep(text, history);
      d.actions = [];
    }
    const done = [];
    for (const a of Array.isArray(d.actions) ? d.actions : []) {
      try { const r = await act(a, text); if (r) done.push(r); } catch (e) { done.push(`(${a.type} didn't work: ${e.message})`); }
    }
    const answer = done.find((x) => x && x.answer);
    if (answer) reply = [reply, answer.answer].filter(Boolean).join(' ');
    const notes = done.filter((x) => typeof x === 'string');
    if (!reply) reply = notes.join(' ') || 'Done.';
    for (const m of Array.isArray(d.remember) ? d.remember : []) if (m && m.text) local.remember(['facts', 'projects', 'preferences'].includes(m.category) ? m.category : 'facts', String(m.text).slice(0, 300));
  } catch (e) {
    reply = e.message || 'Something went wrong on my end.';
  } finally {
    store.set('status', { ...store.get('status'), thinking: false });
  }
  local.addChat('aura', reply);
  speak(reply);
  return reply;
}

async function act(a, text) {
  switch (a && a.type) {
    case 'task_add': local.addTask(a.text); return null;
    case 'task_done': return local.completeTask(a.text) ? null : `I couldn't find a task like "${a.text}".`;
    case 'reminder_add': {
      let due = a.due;
      if (!due || isNaN(new Date(due))) due = (await parseReminder(text)).due;
      if (!due || isNaN(new Date(due))) return 'When should I remind you?';
      if (new Date(due) <= Date.now()) return 'That time has already passed.';
      local.addReminder(a.text || text, due);
      return null;
    }
    case 'reminder_cancel': {
      const q = String(a.text || '').toLowerCase();
      const r = ((store.get('state') || {}).reminders || []).find((x) => x.text.toLowerCase().includes(q) || q.includes(x.text.toLowerCase()));
      return r && local.cancelReminder(r.id) ? null : `I couldn't find that reminder.`;
    }
    case 'note_add': {
      const notes = local.getNotes();
      local.saveNotes((notes.trim() ? notes.trim() + '\n\n' : '') + a.text);
      return null;
    }
    case 'search': return { answer: await search(a.query || text, text) };
    case 'open': if (a.url) doAction({ action: 'open', url: a.url, label: a.label || 'it' }); return null;
    case 'app': {
      const key = String(a.name || '').toLowerCase().replace(/\b(app|the)\b/g, '').trim();
      const link = APP_LINKS[key] || Object.entries(APP_LINKS).find(([k]) => k.length > 2 && key.includes(k))?.[1];
      if (!link) return `I can't open ${a.name} from here - iOS only lets me open apps that support links.`;
      doAction({ action: 'open', url: link, label: a.name });
      return null;
    }
    case 'shortcut': doAction({ action: 'open', url: 'shortcuts://run-shortcut?name=' + encodeURIComponent(a.name || ''), label: `Shortcut: ${a.name}` }); return null;
    case 'tool': if (TOOLS.includes(a.tool)) doAction({ action: 'tool', tool: a.tool }); return null;
    case 'journal': local.queue('journal', { mood: a.mood, good: a.good || '', mind: a.mind || '' }); return null;
    case 'log_session': local.queue('log_session', { type: a.kind || 'session', rating: a.rating, timestamp: null }); return null;
    case 'pc_job': local.addPcJob(a.command || text); return null;
    default: return null;
  }
}
