// The phone brain's skills - the same jobs the PC does in vision_lab.py, object_vision.py, nexus.py and
// explainer.py, done on the phone (Gemini / Groq called directly) so they work while the PC is off.
// Each returns the same shape the PC returns, so the screens don't need to know who answered.
import { ask, askJSON, transcribe, extractJSON } from './llm.js';
import * as kit from './kit.js';

const now = () => new Date().toLocaleString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' });
const vjson = (img, prompt, maxTokens = 700) => askJSON({ prompt, images: [img], maxTokens, temperature: 0.2 });

// ---------------------------------------------------------------- VISION (vision_lab.py)
export function visionAsk(img, question) {
  const q = (question || 'What am I looking at?').slice(0, 500);
  return vjson(img, `You are AURA, the user's personal assistant, looking through their phone camera.
${kit.who()}
They ask: "${q}"
Answer about what is actually in the image. Be specific and useful, plain spoken English (it is read aloud),
2-5 sentences, no markdown. If you can't tell from the image, say so honestly.
Respond ONLY with JSON: {"answer": "...", "follow_ups": ["2-3 short follow-up questions they might ask next"]}`, 450);
}

export function visionRead(img, mode = 'read', lang = 'English') {
  const task = mode === 'translate'
    ? `Read ALL the text in the image exactly, detect its language, and translate it into ${lang}.
Respond ONLY with JSON: {"language": "detected language", "text": "the original text", "translation": "the ${lang} translation"}`
    : `Read ALL the text in the image exactly as written (keep line breaks). Then pull out anything actionable.
Respond ONLY with JSON:
{"text": "the full text", "summary": "one line: what this is",
 "dates": [{"text": "the date/time as written", "event": "what happens then, as a calendar title"}],
 "todos": ["action items / deadlines worth adding as tasks"], "phones": [], "emails": [], "links": []}
Use [] for anything not present.`;
  return vjson(img, 'You read text from the user\'s phone camera for AURA, their assistant.\n' + task, 900);
}

const STUDY = {
  explain: `Act as a brilliant medical-school tutor. Explain what's in this image (a textbook page, slide, diagram, ECG, X-ray,
specimen, drug label... whatever it is) so the student really understands it.
Respond ONLY with JSON: {"title": "what it is", "explanation": "clear 4-7 sentence explanation, plain text",
"key_points": ["the 3-6 things to remember"], "clinical": "one line of clinical relevance, or empty", "mnemonic": "a mnemonic if one genuinely helps, else empty"}`,
  flashcards: `Turn what's in this image into high-yield flashcards for a medical student.
Respond ONLY with JSON: {"title": "topic", "cards": [{"q": "question", "a": "short answer"}]} with 5-8 cards.`,
  quiz: `Write a short exam-style quiz (MBBS level) on what's in this image.
Respond ONLY with JSON: {"title": "topic", "questions": [{"q": "question", "options": ["A","B","C","D"], "answer": 0-3 index, "why": "one-line explanation"}]} with 4-5 questions.`,
  solve: `Solve the problem shown in the image (maths, physics, chemistry, a calculation, a puzzle, a dose calculation...). Show the
method step by step, then the final answer. If nothing to solve, say so.
Respond ONLY with JSON: {"problem": "the problem in one line", "steps": ["step 1", "step 2", "..."], "answer": "final answer"}`,
  food: `Look at the food/drink in the image and estimate its nutrition for the portion shown. Be realistic, say the uncertainty.
Respond ONLY with JSON: {"items": [{"name": "...", "portion": "...", "kcal": number}], "total_kcal": number, "protein_g": number,
"carbs_g": number, "fat_g": number, "sugar_g": number, "verdict": "one honest line", "tip": "one practical tip"}`,
};
export const visionStudy = (img, mode = 'explain') => vjson(img, `You are AURA, looking through the user's phone camera.\n${kit.who()}\n${STUDY[mode] || STUDY.explain}`, 900);

export function observe(img, focus = '') {
  return vjson(img, `You are the perception core of a futuristic heads-up display, as observant as a world-class detective.
Look at EVERY part of this camera frame. ${focus ? `The user is especially interested in: "${focus}".` : ''}
Respond ONLY with compact JSON:
{"summary": "one line: what's going on",
 "objects": [{"label": "precise name (brand/model only if clearly readable)", "x": 0-1, "y": 0-1, "w": 0-1, "h": 0-1, "detail": "short useful detail"}],
 "text": ["any readable text, verbatim, short"], "notable": ["small details most people would miss"], "hazards": ["anything unsafe, else empty"]}
x,y = top-left corner, w,h = size, all as fractions of the image. Up to 10 objects, most important first.
For people describe only what's visible (never identity, age, ethnicity or other sensitive traits).`, 600);
}

export function detective(img, question = '') {
  return vjson(img, `You are AURA in DETECTIVE mode - Sherlock Holmes' powers of observation, but rigorously honest.
${question ? `Focus on: "${question}".` : 'Work out the story of this scene.'}
Find the small clues (wear, stains, labels, positions, shadows, reflections, time cues, what's missing) and reason from them.
Never claim identity of people or sensitive traits. Separate what you SEE from what you INFER.
Respond ONLY with JSON:
{"clues": [{"n": 1, "clue": "what you observe", "x": 0-1, "y": 0-1}],
 "deductions": [{"claim": "what it suggests", "because": "which clues (by number) and why", "confidence": "high|medium|low"}],
 "conclusion": "your best overall reading of the scene in 1-2 sentences", "check_next": ["what to look at to confirm or rule it out"]}
x,y = where the clue is in the image (fractions). 4-8 clues.`, 900);
}

export function watch(img, conditions) {
  const conds = (conditions || []).map((c) => String(c).slice(0, 150)).filter((c) => c.trim()).slice(0, 6);
  if (!conds.length) return Promise.resolve({ triggered: [], description: '' });
  return vjson(img, `You are a security/monitoring camera's brain. The user asked to be alerted when:
${conds.map((c, i) => `${i + 1}. ${c}`).join('\n')}
Look at this frame. For each condition, decide if it is TRUE RIGHT NOW (be strict - only if clearly visible).
Respond ONLY with JSON: {"triggered": [{"condition": "the condition text", "evidence": "what you see that proves it"}], "description": "one short line describing the scene"}`, 300);
}

const MED = {
  drug: `Identify the medicine (pill, blister, box, bottle, label). Respond ONLY with JSON:
{"name": "generic (brand) - only if readable/clear, else best guess marked 'possibly'", "strength": "...", "form": "...", "class": "drug class",
"uses": ["main indications"], "how_it_works": "one line mechanism", "typical_dose": "standard adult dosing as printed or typical",
"side_effects": ["common + serious"], "interactions": ["important interactions"], "warnings": ["contraindications / red flags"], "confidence": "high|medium|low"}`,
  ecg: `Interpret this ECG systematically, like teaching an MBBS student. Respond ONLY with JSON:
{"rate": "...", "rhythm": "...", "axis": "...", "pr": "...", "qrs": "...", "qt": "...", "st_t": "...", "findings": ["key abnormalities"],
"impression": "most likely interpretation", "differentials": ["..."], "next_steps": ["what a clinician would do next"], "confidence": "high|medium|low"}`,
  xray: `Describe this radiograph/scan systematically for an MBBS student. Respond ONLY with JSON: {"study": "what it is (view, region)",
"systematic": ["each step of the review with findings"], "findings": ["key findings"], "impression": "...", "differentials": ["..."], "confidence": "high|medium|low"}`,
  lab: `Read this lab report. Respond ONLY with JSON: {"tests": [{"name": "...", "value": "...", "range": "...", "flag": "high|low|normal"}],
"abnormal": ["what's out of range and what it can mean"], "pattern": "what the results together suggest", "questions_for_doctor": ["..."]}`,
  lesion: `Describe this skin lesion / wound objectively (site, size estimate, shape, border, colour, surface, surrounding skin) as a student
would in a case presentation. Do NOT diagnose definitively. Respond ONLY with JSON: {"description": "...", "features": ["ABCDE-style features"],
"differentials": ["possibilities to consider"], "red_flags": ["features that need prompt review"], "advice": "when to see a doctor"}`,
};
const MED_WHAT = { drug: 'a medicine (pill, tablet, capsule, blister pack, box, bottle or drug label)', ecg: 'an ECG / rhythm strip',
  xray: 'a radiograph or medical scan', lab: 'a lab / test report', lesion: 'skin, a lesion or a wound' };
export async function med(img, kind = 'drug') {
  const what = MED_WHAT[kind] || MED_WHAT.drug, short = what.split(' (')[0].replace(/^an? /, '');
  const d = await vjson(img, `You are AURA's clinical lens for a medical student. Educational only - be accurate and honest about uncertainty.
FIRST check: does this image clearly show ${what}? If it does NOT, do not guess or invent anything - respond ONLY with
{"not_found": "No ${short} visible - the image shows <what it actually shows>."}
Otherwise:
${MED[kind] || MED.drug}`, 900);
  if (d.not_found) return { not_found: d.not_found };
  return { ...d, disclaimer: 'Educational - not a diagnosis. Check with a clinician / the official drug information.' };
}

export async function compare(a, b, question = '') {
  const d = await askJSON({ images: [a, b], maxTokens: 700, temperature: 0.2, prompt: `You are AURA. The user photographed two things (image A, then image B) and asks: "${question || "Compare them - what's the difference, and which is better for what?"}"
Respond ONLY with JSON: {"a": "what A is", "b": "what B is", "differences": ["..."], "similarities": ["..."], "verdict": "a direct answer to their question", "pick": "A|B|depends"}` });
  return d.text && !d.verdict ? { verdict: d.text } : d;
}

export function identify(img, hint = '') {
  return vjson(img, `You are the targeting computer of a futuristic heads-up display, looking at a close-up crop from the user's camera.
${hint ? `A fast detector guessed it might be a "${hint}" - confirm or correct that.` : ''}
Identify the MAIN object in the centre of this image as precisely as you can: exact kind, and brand/model ONLY if clearly readable.
If it's a person, describe only what's visible - never guess who they are, their age, ethnicity or anything sensitive. Be honest about uncertainty.
Respond ONLY with JSON: {"name": "short precise name", "category": "one or two words", "confidence": "high|medium|low",
"summary": "2 sentences: what it is and what it's for", "details": [{"label": "Colour / Material / Brand / Typical use", "value": "..."}],
"fact": "one genuinely interesting fact about this kind of object"}
Give 3-5 details. Say "Not visible" for anything you can't actually see.`, 600);
}

// ---------------------------------------------------------------- EXPLAIN (the cat)
const explainers = {};
export async function explain(text) {
  const id = Math.random().toString(36).slice(2, 10);
  explainers[id] = { text: text.slice(0, 4000), history: [] };
  return { id, ...(await explainAsk(id, 'Explain what this word, phrase or passage means.')) };
}
export async function followUp(id, question) {
  if (!explainers[id]) return { error: 'That explanation has expired - explain it again.' };
  const earlier = explainers[id].history.slice(-3).join('\n');
  return explainAsk(id, `You already told them: "${earlier}"\n\nNow they ask a follow-up: "${question}"\nAnswer that question specifically (the headline should answer it in one line).`);
}
async function explainAsk(id, task) {
  const ex = explainers[id];
  const raw = await ask({ search: true, maxTokens: 900, temperature: 0.3, prompt: `You are AURA's explain cat - friendly, sharp, concise. ${kit.who()}
The user highlighted: """${ex.text}"""
${task}
Use a quick web search if it helps you be accurate or current. Respond ONLY with JSON:
{"headline": "one-line answer", "explanation": "3-6 sentences, plain spoken English, no markdown", "for_you": "one line on what it means for THIS user, or empty",
"sources": [{"title": "page title", "url": "https://...", "domain": "site.com"}]}` });
  const d = extractJSON(raw) || { explanation: raw };
  ex.history.push(d.headline || d.explanation || '');
  d.sources = (d.sources || []).filter((s) => s && /^https?:\/\//.test(s.url || '')).slice(0, 4);
  return d;
}

// ---------------------------------------------------------------- NEXUS (nexus.py)
const CAN_DO = 'add tasks, set reminders, take notes, search the web, open apps and websites on the phone, play music (Spotify / YouTube links), '
  + 'navigation, journal, log study sessions';

export async function liveTurn(b, audioBlob) {
  const heard = await transcribe(audioBlob);
  if (!heard) return { heard: '' };
  const convo = (b.history || []).slice(-8).map((h) => `${h.role === 'user' ? 'User' : 'AURA'}: ${String(h.text).slice(0, 300)}`).join('\n');
  const prompt = `You are AURA, the user's personal AI, in LIVE mode: a hands-free, real-time voice conversation
${b.image ? 'while looking through their phone camera (the image is what the camera sees right now)' : '(camera off)'}.
${kit.who()}
Time: ${now()}.
Conversation so far:
${convo || '(just started)'}
User just said: "${heard}"

Reply like a brilliant, warm companion talking out loud: 1-3 short sentences, natural speech, no lists, no markdown.
If they ask about what's in view, look at the image carefully and be specific.
If they want something DONE (${CAN_DO}), put one clear instruction for it in "command" written as the user would say it, and say you're on it.
Otherwise "command" is "". If they say goodbye / stop / end live mode, set "end" to true.
Respond ONLY with JSON: {"answer": "...", "command": "", "end": false}`;
  const d = await askJSON({ prompt, images: b.image ? [b.image] : undefined, maxTokens: 400, temperature: 0.6 });
  const answer = String(d.answer || d.text || '').trim() || 'Sorry, I lost that - say it again?';
  return { heard, answer, command: String(d.command || '').trim(), end: !!d.end, say: answer };
}

export async function interpret(b, audioBlob) {
  const a = b.a || 'English', c = b.b || 'Urdu';
  const heard = await transcribe(audioBlob, 'auto');
  if (!heard) return { heard: '' };
  const d = await askJSON({ maxTokens: 600, temperature: 0.2, prompt: `You are a live interpreter between a ${a} speaker and a ${c} speaker.
Someone just said (speech-to-text, may have small errors - it sometimes writes Urdu in Hindi/Devanagari letters): "${heard}"
Work out which of the two languages it is (${a} or ${c}; if it's neither, treat it as ${a}), then translate it naturally - meaning and tone,
not word-for-word - into the OTHER language. Use that language's own script.
Respond ONLY with JSON: {"from": "${a} or ${c}", "to": "the other one", "original": "cleaned-up original, in its own language's proper script",
"translation": "...", "romanized": "the translation in Latin letters if its script isn't Latin, else empty"}` });
  const to = [a, c].includes(d.to) ? d.to : d.from === a ? c : a;
  return { heard, original: d.original || heard, from: to === a ? c : a, to, translation: d.translation || '', romanized: d.romanized || '',
    say: d.translation || '', sayLang: to };
}

export async function echoNotes(rec) {
  const lecture = rec.kind === 'lecture';
  const transcript = rec.segments.map((s) => `[${String(Math.floor(s.t / 60)).padStart(2, '0')}:${String(Math.floor(s.t % 60)).padStart(2, '0')}] ${s.text}`).join('\n').slice(-60000);
  return askJSON({ maxTokens: 2200, temperature: 0.3, prompt: `You are AURA. Turn this ${rec.kind} transcript into excellent notes for the user.
${kit.who()}
Transcript (timestamps are mm:ss):
${transcript}

Respond ONLY with JSON: {"title": "a specific title", "summary": "4-6 sentence summary", "key_points": ["the most important points"],
${lecture ? '"flashcards": [{"q": "...", "a": "..."}] (6-10 high-yield cards), "exam_points": ["what is likely to be examined"],' : '"decisions": ["what was decided"],'}
"action_items": ["deadlines / things to do that were mentioned"], "terms": [{"term": "...", "meaning": "..."}]}` });
}

export async function echoAsk(recs, question) {
  const per = Math.floor(60000 / Math.max(1, recs.length));
  const corpus = recs.map((r) => `=== ${r.title} (${new Date(r.started * 1000).toDateString()}) ===\n`
    + r.segments.map((s) => `[${String(Math.floor(s.t / 60)).padStart(2, '0')}:${String(Math.floor(s.t % 60)).padStart(2, '0')}] ${s.text}`).join('\n').slice(0, per)).join('\n\n');
  const d = await askJSON({ maxTokens: 900, prompt: `Answer the user's question using ONLY these recordings of their lectures / meetings.
Quote the recording title and timestamp you used. If the answer isn't in them, say so.
${corpus}

Question: "${question}"
Respond ONLY with JSON: {"answer": "clear answer, plain text, 1-6 sentences", "sources": ["Title @ mm:ss"]}` });
  return { answer: d.answer || d.text || '', sources: d.sources || [] };
}

export async function aroundAsk(b) {
  const lines = (b.places || []).slice(0, 60).map((p) => `- ${p.name} (${p.kind}, ${p.dist} m, bearing ${p.bearing}°${p.open ? ', hours ' + p.open : ''})`).join('\n');
  const d = await askJSON({ maxTokens: 500, temperature: 0.4, prompt: `You are AURA. The user is at ${b.address || `lat ${b.lat}, lon ${b.lon}`}, facing ${b.heading}° (compass). Time: ${now()}.
Places around them (from OpenStreetMap):
${lines || '(none found)'}
They ask: "${b.question || 'What is around me?'}"
Answer helpfully in 2-4 spoken sentences; use directions like "about 200 metres to your left" based on bearing vs their heading.
If you recommend a place, name it exactly as listed.
Respond ONLY with JSON: {"answer": "...", "highlight": ["names of places you mentioned"]}` });
  const answer = d.answer || d.text || '';
  return { answer, highlight: d.highlight || [], say: answer };
}

export async function missionPlan(goal) {
  const d = await askJSON({ maxTokens: 900, temperature: 0.3, prompt: `You are AURA's mission planner. The user's goal: "${goal}"
${kit.who()}
Time now: ${now()}. The user's PC is OFF, so only plan things the phone can do now: ${CAN_DO}.
Break the goal into 2-8 concrete commands, in order, that directly serve THIS goal (use what you know about the user only to tailor them -
never add their other projects), each written exactly as the user would say it to AURA. Drafts only - never send or submit anything.
Respond ONLY with JSON: {"title": "short mission name", "steps": [{"command": "...", "why": "few words"}], "note": "one line on anything the user must do themselves, or empty"}` });
  return { title: String(d.title || goal).slice(0, 60), steps: (d.steps || []).filter((s) => s && s.command).slice(0, 8), note: d.note || '' };
}

// ---------------------------------------------------------------- maps (straight from the phone)
const CATEGORIES = { food: ['amenity', 'restaurant|fast_food|food_court'], cafe: ['amenity', 'cafe|ice_cream'], pharmacy: ['amenity', 'pharmacy'],
  hospital: ['amenity', 'hospital|clinic|doctors'], atm: ['amenity', 'atm|bank'], fuel: ['amenity', 'fuel|charging_station'], mosque: ['amenity', 'place_of_worship'],
  shop: ['shop', 'supermarket|convenience|mall|bakery'], transport: ['amenity', 'bus_station|taxi'], study: ['amenity', 'library|university|college'], park: ['leisure', 'park|garden'] };
const OVERPASS = ['https://overpass-api.de/api/interpreter', 'https://overpass.private.coffee/api/interpreter', 'https://overpass-api.de/api/interpreter',
  'https://overpass.kumi.systems/api/interpreter'];

function distBearing(lat1, lon1, lat2, lon2) {
  const r = Math.PI / 180, p1 = lat1 * r, p2 = lat2 * r, dl = (lon2 - lon1) * r;
  const a = Math.sin((p2 - p1) / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  const y = Math.sin(dl) * Math.cos(p2), x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return [2 * 6371000 * Math.asin(Math.sqrt(a)), ((Math.atan2(y, x) / r) + 360) % 360];
}

export async function places(b) {
  const { lat, lon } = b, radius = Math.max(200, Math.min(b.radius || 1200, 5000));
  const cats = (b.cats || Object.keys(CATEGORIES)).filter((c) => CATEGORIES[c]);
  const byKey = {};
  for (const c of cats) (byKey[CATEGORIES[c][0]] = byKey[CATEGORIES[c][0]] || []).push(CATEGORIES[c][1]);
  const q = Object.entries(byKey).map(([k, v]) => `nwr["${k}"~"^(${v.join('|')})$"](around:${radius},${lat},${lon});`).join('');
  let elements = null;
  for (const server of OVERPASS) {
    try {
      const ctrl = new AbortController(); const t = setTimeout(() => ctrl.abort(), 14000);
      const r = await fetch(server, { method: 'POST', body: new URLSearchParams({ data: `[out:json][timeout:20];(${q});out center 300;` }), signal: ctrl.signal });
      clearTimeout(t);
      if (r.ok) { elements = (await r.json()).elements || []; break; }
    } catch (e) { /* next server */ }
  }
  if (!elements) return { error: 'The map services are busy right now - try again in a moment.', places: [] };
  const out = [];
  for (const e of elements) {
    const tags = e.tags || {}, plat = e.lat ?? e.center?.lat, plon = e.lon ?? e.center?.lon;
    if (plat == null) continue;
    const cat = cats.find((c) => new RegExp(`^(${CATEGORIES[c][1]})$`).test(tags[CATEGORIES[c][0]] || '')) || 'place';
    const kind = CATEGORIES[cat] ? tags[CATEGORIES[cat][0]] : (tags.amenity || tags.shop || 'place');
    const [d, br] = distBearing(lat, lon, plat, plon);
    out.push({ name: tags['name:en'] || tags.name || kind.replace(/_/g, ' '), kind, cat, lat: plat, lon: plon, dist: Math.round(d), bearing: Math.round(br * 10) / 10,
      open: tags.opening_hours || '', phone: tags.phone || tags['contact:phone'] || '' });
  }
  return { places: out.sort((x, y) => x.dist - y.dist).slice(0, 150) };
}

export async function where(b) {
  try {
    const r = await fetch(`https://nominatim.openstreetmap.org/reverse?lat=${b.lat}&lon=${b.lon}&format=jsonv2&zoom=17&accept-language=en`);
    const d = await r.json(), a = d.address || {};
    const short = [a.road, a.suburb || a.neighbourhood, a.city || a.town].filter(Boolean).join(', ');
    return { address: d.display_name || '', short: short || (d.display_name || '').slice(0, 80) };
  } catch (e) { return { address: '', short: '' }; }
}
