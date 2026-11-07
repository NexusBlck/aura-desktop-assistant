// The phone brain's "think it through" path for HARD questions (same idea as smart.py on the PC), free models only:
//   1. PLAN    what's needed - your study material? the web? exact maths?
//   2. GATHER  your material (knowledge.js + ECHO recordings on the phone), exact calculations (calc.js)
//   3. ANSWER  the strongest model thinking at length (Groq's web search when it needs current facts)
//   4. CHECK   a model from the OTHER family reviews it and fixes real mistakes
import { ask, askJSON, answeredBy } from './llm.js';
import * as kit from './kit.js';
import * as kb from './knowledge.js';
import { calc } from './calc.js';
import * as storage from '../platform/storage.js';

const today = () => new Date().toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });

/** ECHO recordings made on the phone, as passages (so "what did my lecture say" works for new lectures too). */
function echoPassages() {
  const out = [];
  for (const row of storage.get('brain.echo.index', [])) {
    const rec = storage.get('brain.echo.r.' + row.id, null);
    if (!rec) continue;
    let buf = [], start = null;
    for (const s of rec.segments || []) {
      if (start == null) start = s.t;
      buf.push(s.text);
      if (buf.join(' ').length > 900) { out.push({ t: buf.join(' '), title: rec.title, src: 'Lecture', where: `${String(Math.floor(start / 60)).padStart(2, '0')}:${String(Math.floor(start % 60)).padStart(2, '0')}` }); buf = []; start = null; }
    }
    if (buf.length) out.push({ t: buf.join(' '), title: rec.title, src: 'Lecture', where: '' });
  }
  return out;
}

export function relevantLessons(text, n = 6) {
  const all = (kit.get() || {}).lessons || [];
  const words = new Set(kb.tokens(text));
  const picked = all.map((l, i) => [kb.tokens(l).filter((w) => words.has(w)).length, i, l]).filter((x) => x[0] > 0)
    .sort((a, b) => b[0] - a[0] || b[1] - a[1]).slice(0, n).map((x) => x[2]);
  for (const l of all.slice(-3).reverse()) if (!picked.includes(l)) picked.push(l);
  return picked;
}

function spoken(text) {
  return String(text || '').replace(/\n\s*(?:[•\-*]|\d+[.)])\s+/g, '\n').replace(/\*\*|__|#+ /g, '')
    .replace(/\s*[[(]\s*(?:M\d+(?:\s*[,;&]\s*M\d+)*|WEB[^\])]*)\s*[\])]/g, '').replace(/[ \t]{2,}/g, ' ').trim();
}

export async function deep(question, history = []) {
  const convo = history.slice(-6).map((m) => `${m.role === 'user' ? 'User' : 'AURA'}: ${String(m.content).slice(0, 300)}`).join('\n');
  // 1. plan
  const plan = await askJSON({ maxTokens: 300, temperature: 0.1, prompt: `You plan how AURA (a personal AI for a medical student) will answer a hard question.
Today is ${today()} - "this year" / "latest" means now.
Recent conversation:
${convo || '(none)'}
Question: "${question}"
The user has a personal knowledge base of their own lectures, study guides and notes.
Respond ONLY with JSON: {"use_material": true/false, "material_query": "keywords", "needs_web": true/false (current facts / things you might misremember),
"calculations": ["0-4 plain arithmetic expressions to compute exactly"], "approach": "one line"}` }).catch(() => ({}));

  // 2. gather
  const parts = [];
  let used = [];
  if (plan.use_material) {
    const hits = await kb.search(plan.material_query || question, 6, echoPassages());
    hits.forEach((h, i) => parts.push(`[M${i + 1}] (${h.source}: ${h.title}${h.where ? ' ' + h.where : ''})\n${h.text}`));
    used = hits.map((h) => h.title);
  }
  const calcs = [];
  for (const expr of (plan.calculations || []).slice(0, 4)) { try { calcs.push(`${expr} = ${calc(expr)}`); } catch (e) { /* not plain maths */ } }
  if (calcs.length) parts.push(`[EXACT CALCULATIONS - use these numbers]\n${calcs.join('\n')}`);
  const lessons = relevantLessons(question);

  // 3. answer
  const system = `You are AURA, a brilliant personal AI and tutor for the user (a medical student). Think carefully before you answer.
Today is ${today()}.
${kit.who()}
${lessons.length ? 'Lessons from their past corrections - follow them:\n' + lessons.map((l) => '- ' + l).join('\n') : ''}
How to answer:
- Be correct above all. Reason privately; give the result, not your scratch work.
${used.length ? '- Passages from THEIR OWN material are below (tagged M1, M2...). Base your answer on them where relevant and say so naturally ("your study guide says...", "in your lecture...").'
    : '- None of the user\'s own material was used, so NEVER say "your notes / study guide / lecture says".'}
- Use the exact calculations given. Don't mention tools, searches, calculations or tags. Never write tags like [M1].
- It's read aloud: natural sentences in paragraphs - NO bullet points, lists or markdown. Lead with the answer. Usually 3-8 sentences;
  more only if they asked for a lot. For a quiz, ask ONE question at a time.
- If you're not sure, say what you're unsure about instead of guessing.
Plan: ${plan.approach || ''}`;
  const user = `${convo ? 'Recent conversation:\n' + convo + '\n\n' : ''}Question: ${question}${parts.length ? '\n\nMaterial and tools:\n' + parts.join('\n\n').slice(0, 14000) : ''}`;
  let reply = await ask({ system, messages: [{ role: 'user', content: user }], think: true, search: !!plan.needs_web, maxTokens: 1400, temperature: 0.3 });
  const by = answeredBy();

  // 4. second opinion from the other family
  try {
    const check = await askJSON({ prefer: by.startsWith('g:') ? 'groq' : 'gemini', maxTokens: 1400, temperature: 0.1, prompt: `You are a strict expert reviewer (medicine, science, maths). Check this answer for factual errors, wrong numbers, unsafe medical claims, or not answering the question. Use the material as ground truth where relevant.
Question: "${question}"
${parts.length ? 'Material:\n' + parts.join('\n\n').slice(0, 8000) : ''}
Answer to check:
"""${reply}"""
Respond ONLY with JSON: {"verdict": "ok" | "fix", "problems": ["..."], "fixed": "if fix: the full corrected answer in the same spoken style, else empty"}
Only say "fix" for real mistakes, not style.` });
    if (check.verdict === 'fix' && String(check.fixed || '').length > 40) reply = check.fixed;
  } catch (e) { /* the answer stands */ }
  return spoken(reply);
}
