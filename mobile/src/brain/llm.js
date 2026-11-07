// The phone brain's AI: calls Gemini and Groq directly from the phone (no PC needed). Same idea as ai.py:
// smartest first, and a model that says "limit reached" is rested for a while so the next one answers.
//   ask({ prompt | messages, images, search, think, prefer, maxTokens, temperature }) -> text
// think: true = reason at length first (hard questions); prefer: 'groq' | 'gemini' = try that family first
// (the second-opinion check uses the OTHER family from the one that answered).
//   askJSON(...)                                                           -> object
// search: true uses Groq's built-in web search (news, weather, prices, anything recent).
import * as kit from './kit.js';

const resting = {};
const rest = (model, text) => { resting[model] = Date.now() + (/day|daily|quota/i.test(text || '') ? 3600e3 : 60e3); };
const awake = (list) => { const a = list.filter((m) => (resting[m] || 0) < Date.now()); return a.length ? a : list.slice(-1); };

export class BrainError extends Error {}

let lastModel = '';
/** Which model answered the last ask() ('g:...' Gemini, 'q:...' Groq). */
export const answeredBy = () => lastModel;

async function post(url, body, headers, timeout = 60000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  try {
    const isForm = body instanceof FormData;
    const res = await fetch(url, { method: 'POST', body: isForm ? body : JSON.stringify(body), signal: ctrl.signal,
      headers: isForm ? headers : { 'Content-Type': 'application/json', ...headers } });
    let data = null, text = '';
    try { text = await res.text(); data = JSON.parse(text); } catch (e) { /* not JSON */ }
    return { ok: res.ok, status: res.status, data, text };
  } catch (e) {
    throw new BrainError(e.name === 'AbortError' ? 'The AI took too long to answer.' : 'No internet connection.');
  } finally { clearTimeout(timer); }
}

function toMessages({ prompt, messages, system }) {
  const m = messages ? messages.slice() : [{ role: 'user', content: prompt }];
  if (system) m.unshift({ role: 'system', content: system });
  return m;
}

async function gemini(model, o) {
  const msgs = toMessages(o);
  const sys = msgs.filter((m) => m.role === 'system').map((m) => m.content).join('\n\n');
  const contents = msgs.filter((m) => m.role !== 'system').map((m, i, all) => {
    const parts = [{ text: String(m.content) }];
    if (i === all.length - 1 && o.images) for (const img of o.images) parts.push({ inline_data: { mime_type: 'image/jpeg', data: img } });
    return { role: m.role === 'assistant' ? 'model' : 'user', parts };
  });
  const body = { contents, generationConfig: { temperature: o.temperature ?? 0.4, maxOutputTokens: (o.maxTokens || 800) + (o.think ? 6000 : 1500) } };
  if (o.think) body.generationConfig.thinkingConfig = { thinkingBudget: 4096 };
  if (sys) body.systemInstruction = { parts: [{ text: sys }] };
  const r = await post(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${encodeURIComponent(kit.get().keys.gemini)}`, body, {});
  if (!r.ok) { rest('g:' + model, r.text); throw new BrainError(`gemini ${r.status}`); }
  const parts = (((r.data || {}).candidates || [])[0] || {}).content?.parts || [];
  const text = parts.filter((p) => p.text && !p.thought).map((p) => p.text).join('').trim();
  if (!text) throw new BrainError('empty');
  return text;
}

async function groq(model, o) {
  const msgs = toMessages(o).map((m, i, all) => {
    if (i === all.length - 1 && o.images && m.role === 'user') {
      return { role: 'user', content: [{ type: 'text', text: String(m.content) }, ...o.images.map((img) => ({ type: 'image_url', image_url: { url: `data:image/jpeg;base64,${img}` } }))] };
    }
    return m;
  });
  const body = { model, messages: msgs, temperature: o.temperature ?? 0.4, max_tokens: (o.maxTokens || 800) + (o.think ? 3000 : o.search ? 1200 : 600) };
  if (o.search) { body.tools = [{ type: 'browser_search' }]; body.tool_choice = 'auto'; }
  if (/gpt-oss/.test(model)) body.reasoning_effort = o.think ? 'medium' : 'low';
  if (/qwen/.test(model)) body.max_tokens = Math.min(body.max_tokens, 1000);        // Groq's free limit for that one
  const k = kit.get();
  const r = await post(`${k.groq_base}/chat/completions`, body, { Authorization: `Bearer ${k.keys.groq}` }, o.search ? 90000 : 60000);
  if (!r.ok) { if (r.status === 429 || r.status === 401 || r.status === 403 || r.status >= 500) rest('q:' + model, r.text); throw new BrainError(`groq ${r.status}`); }
  let text = (((r.data || {}).choices || [])[0] || {}).message?.content || '';
  text = text.replace(/<think>[\s\S]*?<\/think>/g, '').replace(/【[^】]*】/g, '').replace(/ /g, ' ').trim();
  if (!text) throw new BrainError('empty');
  return text;
}

export async function ask(o) {
  if (!kit.ready()) throw new BrainError("AURA's phone brain isn't set up yet - open AURA once while your PC is on.");
  const k = kit.get(), gem = k.keys.gemini ? (k.models.gemini || []).map((m) => 'g:' + m) : [];
  const grq = k.keys.groq ? (k.models.groq || []).map((m) => 'q:' + m) : [];
  let order;
  if (o.search) order = grq.filter((m) => /gpt-oss/.test(m));
  else if (o.images) order = [...gem, ...(k.keys.groq ? ['q:' + k.models.vision] : [])];
  else order = o.prefer === 'groq' ? [...grq, ...gem] : [...gem, ...grq];
  let last = null;
  for (const m of awake(order)) {
    try { const text = m.startsWith('g:') ? await gemini(m.slice(2), o) : await groq(m.slice(2), o); lastModel = m; return text; } catch (e) {
      last = e;
      if (e.message === 'No internet connection.') throw e;
    }
  }
  throw new BrainError(last && last.message === 'The AI took too long to answer.' ? last.message : 'The AI services are busy right now - try again in a minute.');
}

/** Pulls the first JSON object out of a reply (tolerates code fences / extra text), like ai.extract_json. */
export function extractJSON(raw) {
  const text = String(raw || '').trim().replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '');
  for (let i = 0; i < text.length; i++) {
    if (text[i] !== '{' && text[i] !== '[') continue;
    let depth = 0, str = false, esc = false;
    for (let j = i; j < text.length; j++) {
      const c = text[j];
      if (str) { if (esc) esc = false; else if (c === '\\') esc = true; else if (c === '"') str = false; continue; }
      if (c === '"') str = true;
      else if (c === '{' || c === '[') depth++;
      else if (c === '}' || c === ']') { depth--; if (!depth) { try { return JSON.parse(text.slice(i, j + 1)); } catch (e) { break; } } }
    }
  }
  return null;
}

export async function askJSON(o) {
  const raw = await ask(o);
  const d = extractJSON(raw);
  return d && typeof d === 'object' && !Array.isArray(d) ? d : { text: raw };
}

// ---------------------------------------------------------------- speech to text (Groq Whisper)
const HALLUCINATION = /^(thank you\.?|thanks for watching\.?|you|bye\.?|\.+)$/i;

export async function transcribe(blob, language) {
  if (!kit.ready() || !kit.get().keys.groq) throw new BrainError("AURA's phone brain isn't set up yet - open AURA once while your PC is on.");
  const k = kit.get();
  const ext = /wav/.test(blob.type) ? 'wav' : /webm/.test(blob.type) ? 'webm' : /ogg/.test(blob.type) ? 'ogg' : /mpeg/.test(blob.type) ? 'mp3' : 'm4a';
  const f = new FormData();
  f.append('file', blob, `speech.${ext}`);
  f.append('model', k.models.stt || 'whisper-large-v3-turbo');
  f.append('temperature', '0');
  f.append('response_format', 'verbose_json');
  if (language !== 'auto') {
    if (k.vocab) f.append('prompt', k.vocab);
    f.append('language', language || 'en');
  }
  const r = await post(`${k.groq_base}/audio/transcriptions`, f, { Authorization: `Bearer ${k.keys.groq}` }, 45000);
  if (!r.ok) throw new BrainError(r.status === 429 ? 'Speech recognition is used up for today.' : "Couldn't understand the audio.");
  const minLogprob = language === 'auto' ? -2.5 : -1.2;       // the interpreter: short phrases, any language
  const segs = (r.data.segments || []).filter((s) => (s.no_speech_prob || 0) < 0.6 && (s.avg_logprob || 0) > minLogprob);
  const text = (r.data.segments ? segs.map((s) => s.text.trim()).join(' ') : r.data.text || '').trim();
  return !text || HALLUCINATION.test(text) ? '' : text;
}
