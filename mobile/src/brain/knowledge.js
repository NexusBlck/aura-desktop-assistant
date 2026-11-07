// Your study material on the phone (knowledge.py's passages, downloaded from the PC), searchable with the PC off.
// Kept in the phone's Cache Storage (it's ~1 MB - too big for localStorage) and ranked with the same BM25 + light
// stemming as the PC, so "according to my study guide..." works anywhere.
import { get as apiGet } from '../core/api.js';

const CACHE = 'aura-knowledge', KEY = '/aura-knowledge.json';
let data = null, bm = null, loading = null;

const STOP = new Set(('a an the and or but if then of to in on at by for with from as is are was were be been being it its this that these those i you he she '
  + 'we they me my your our their them his her what which who whom whose when where why how do does did done can could should would will shall may might must '
  + 'not no yes so than too very just about into over under again further once here there all any both each few more most other some such only own same also '
  + 'up down out off s t don now please tell explain').split(' '));
function stem(w) {
  for (const suf of ['ization', 'ations', 'ation', 'ings', 'ing', 'edly', 'ies', 'ied', 'es', 'ed', 'ly', 's']) {
    if (w.length > suf.length + 3 && w.endsWith(suf)) return w.slice(0, -suf.length) + (suf === 'ies' || suf === 'ied' ? 'y' : '');
  }
  return w;
}
export const tokens = (text) => (String(text || '').toLowerCase().match(/[a-z0-9]+/g) || []).filter((w) => w.length > 1 && !STOP.has(w)).map(stem);

async function load() {
  if (data) return data;
  if (!loading) {
    loading = (async () => {
      try { const r = await (await caches.open(CACHE)).match(KEY); data = r ? await r.json() : { passages: [] }; } catch (e) { data = { passages: [] }; }
      return data;
    })();
  }
  return loading;
}

/** Downloads the latest passages from the PC (call while it's reachable). */
export async function refresh() {
  const fresh = await apiGet('brain/knowledge', { timeout: 60000 });
  if (!fresh || !Array.isArray(fresh.passages)) return 0;
  data = fresh; bm = null; loading = Promise.resolve(fresh);
  try { await (await caches.open(CACHE)).put(KEY, new Response(JSON.stringify(fresh), { headers: { 'Content-Type': 'application/json' } })); } catch (e) { /* storage full */ }
  try { localStorage.setItem('aura.brain.kbAt', String(Date.now())); } catch (e) { /* ignore */ }
  return fresh.passages.length;
}
export const age = () => { try { return (Date.now() - Number(localStorage.getItem('aura.brain.kbAt') || 0)) / 1000; } catch (e) { return Infinity; } };

function build(passages) {
  const docs = [], df = {};
  for (const p of passages) {
    const tf = {};
    for (const t of tokens(`${p.t} ${p.title}`)) tf[t] = (tf[t] || 0) + 1;
    let len = 0; for (const k in tf) { len += tf[k]; df[k] = (df[k] || 0) + 1; }
    docs.push({ p, tf, len });
  }
  return { docs, df, avg: docs.reduce((s, d) => s + d.len, 0) / Math.max(1, docs.length), n: docs.length };
}

/** The best passages for a question: [{ text, title, source, where, score }]. extra = more passages (e.g. ECHO recordings on the phone). */
export async function search(query, k = 6, extra = []) {
  const d = await load();
  const passages = [...(d.passages || []), ...extra];
  if (!passages.length) return [];
  if (!bm || extra.length) bm = build(passages);
  const q = [...new Set(tokens(query))];
  const k1 = 1.5, b = 0.75, scored = [];
  for (const doc of bm.docs) {
    let s = 0;
    for (const t of q) {
      const f = doc.tf[t];
      if (!f) continue;
      const idf = Math.log(1 + (bm.n - bm.df[t] + 0.5) / (bm.df[t] + 0.5));
      s += (idf * f * (k1 + 1)) / (f + k1 * (1 - b + (b * doc.len) / bm.avg));
    }
    if (s >= 0.8) scored.push([s, doc.p]);
  }
  scored.sort((x, y) => y[0] - x[0]);
  const best = scored.length ? scored[0][0] : 0, perFile = {}, out = [];
  for (const [s, p] of scored) {
    if (s < best * 0.55 || (perFile[p.title] || 0) >= 3) continue;
    perFile[p.title] = (perFile[p.title] || 0) + 1;
    out.push({ text: p.t, title: p.title, source: p.src, where: p.where || '', score: Math.round(s * 100) / 100 });
    if (out.length >= k) break;
  }
  return out;
}
