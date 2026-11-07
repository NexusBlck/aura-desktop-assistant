// Quick chips, suggestions and command history - the same set as the HUD's command bar.
import * as storage from '../platform/storage.js';

// [label, command] - `fill` puts text in the box to finish; `log` opens the log-a-session sheet.
export const CHIPS = [
  { label: '☀ Daily briefing', cmd: 'good morning' },
  { label: '⌖ Web research', fill: 'bring up everything from the web about ' },
  { label: '▤ Find my files', fill: 'bring up everything I have on ' },
  { label: '⏰ Remind me', fill: 'remind me in 30 minutes to ' },
  { label: '◎ Focus 25', cmd: 'start a 25 minute focus session' },
  { label: '◉ Vision mode', screen: 'vision' },
  { label: '⛁ CDC WONDER', screen: 'cdc' },
  { label: '▦ Dashboard', screen: 'dashboard' },
  { label: '✎ Journal', screen: 'journal' },
  { label: 'Log Gym', log: 'gym' },
  { label: 'Log BJJ', log: 'bjj' },
  { label: 'Log Judo', log: 'judo' },
  { label: 'Log Study', log: 'study' },
  { label: 'Log Coding', log: 'coding' },
];

export const SUGGESTIONS = [
  ['bring up everything from the web about ', 'web research'], ['bring up everything I have on ', 'my files'],
  ['remind me in 20 minutes to ', 'reminder'], ['remind me at 6pm to ', 'reminder'],
  ['start a 25 minute focus session', 'focus'], ['start a 50 minute focus session on ', 'focus'], ['stop the focus timer', 'focus'],
  ['good morning', 'daily briefing'], ["what's my schedule today", 'calendar'], ['add BJJ tonight at 7 to my calendar', 'calendar'],
  ['open word and write ', 'apps'], ['open ', 'apps'], ['play  on spotify', 'music'], ['play  on youtube', 'music'],
  ['search pubmed for ', 'research'], ['extract data from CDC WONDER for ', 'research'], ['explain this error', 'coding'], ['build me a ', 'coding'],
  ['any important emails?', 'email'], ['take a screenshot', 'system'], ['volume up', 'system'], ['weekly report', 'life'],
  ['do not disturb', 'presence'],
];

let history = storage.get('history', []);

export function remember(text) {
  history = [text, ...history.filter((h) => h !== text)].slice(0, 50);
  storage.set('history', history);
}

export function suggestionsFor(query) {
  const q = (query || '').toLowerCase().trim();
  const pool = [...history.map((h) => [h, 'recent']), ...SUGGESTIONS];
  const seen = new Set();
  return pool.filter(([s]) => {
    const k = s.toLowerCase();
    if (seen.has(k)) return false;
    seen.add(k);
    return !q || (k.includes(q) && k !== q);
  }).slice(0, 8);
}
