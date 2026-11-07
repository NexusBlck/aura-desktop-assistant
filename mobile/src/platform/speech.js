// The phone's own voice (built-in text-to-speech) - used when AURA's voice from the PC isn't available
// (the phone brain, with the PC off). iOS needs one tap before it will speak: warm() runs on the first touch.
const synth = window.speechSynthesis;
const LANG = { English: 'en', Urdu: 'ur', Arabic: 'ar', Hindi: 'hi', French: 'fr', Spanish: 'es', German: 'de', Chinese: 'zh', Japanese: 'ja',
  Korean: 'ko', Turkish: 'tr', Persian: 'fa', Russian: 'ru', Italian: 'it', Portuguese: 'pt', Bengali: 'bn', Malay: 'ms', Indonesian: 'id' };

export const available = () => !!synth;

export function warm() {
  if (!synth) return;
  try { const u = new SpeechSynthesisUtterance(' '); u.volume = 0; synth.speak(u); } catch (e) { /* ignore */ }
}

function pickVoice(code) {
  const voices = synth.getVoices().filter((v) => v.lang && v.lang.toLowerCase().startsWith(code));
  if (!voices.length) return null;
  const score = (v) => (/premium|enhanced|neural|natural/i.test(v.name) ? 4 : 0) + (/samantha|ava|zoe|serena|karen|siri/i.test(v.name) ? 2 : 0)
    + (v.lang.toLowerCase() === 'en-us' ? 1 : 0) + (v.localService ? 1 : 0);
  return voices.sort((a, b) => score(b) - score(a))[0];
}

let current = null;
/** Speaks text; resolves when finished (or stopped). language: 'English', 'Urdu'… */
export function speak(text, language = 'English') {
  return new Promise((resolve) => {
    if (!synth || !text) { resolve(); return; }
    const u = new SpeechSynthesisUtterance(String(text).slice(0, 3000));
    const code = LANG[language] || 'en';
    const v = pickVoice(code);
    if (v) u.voice = v;
    u.lang = v ? v.lang : code;
    u.rate = 1.02;
    let done = false;
    const finish = () => { if (!done) { done = true; current = null; resolve(); } };
    u.onend = finish; u.onerror = finish;
    current = u;
    synth.speak(u);
    setTimeout(finish, Math.max(4000, text.length * 120));        // safety net: iOS sometimes never fires onend
  });
}

export function cancel() {
  if (synth && (synth.speaking || synth.pending)) synth.cancel();
  current = null;
}
