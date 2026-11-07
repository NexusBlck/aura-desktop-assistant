// Exact arithmetic for the phone brain (like smart.calc on the PC): numbers, + - * / ^ %, brackets and a few
// maths functions. A tiny parser - never runs code - so AI answers use computed numbers, not guesses.
const FUNCS = { sqrt: Math.sqrt, log: Math.log10, ln: Math.log, log10: Math.log10, exp: Math.exp, abs: Math.abs, round: Math.round,
  floor: Math.floor, ceil: Math.ceil, sin: Math.sin, cos: Math.cos, tan: Math.tan };
const CONST = { pi: Math.PI, e: Math.E };

export function calc(expr) {
  const s = String(expr).replace(/×/g, '*').replace(/÷/g, '/').replace(/\*\*/g, '^').replace(/\s+/g, '');
  let i = 0;
  const peek = () => s[i];
  const num = () => {
    const m = /^(\d+\.?\d*|\.\d+)(e[+-]?\d+)?/i.exec(s.slice(i));
    if (m) { i += m[0].length; return parseFloat(m[0]); }
    const w = /^[a-z]+\d*/i.exec(s.slice(i));
    if (w) {
      const name = w[0].toLowerCase(); i += w[0].length;
      if (CONST[name] != null) return CONST[name];
      if (FUNCS[name] && peek() === '(') { i++; const v = add(); if (s[i++] !== ')') throw new Error(')'); return FUNCS[name](v); }
    }
    throw new Error('not plain maths');
  };
  const atom = () => {
    if (peek() === '(') { i++; const v = add(); if (s[i++] !== ')') throw new Error(')'); return v; }
    if (peek() === '-') { i++; return -atom(); }
    if (peek() === '+') { i++; return atom(); }
    return num();
  };
  const pow = () => { const base = atom(); if (peek() === '^') { i++; return base ** pow(); } return base; };
  const mul = () => { let v = pow(); while ('*/%'.includes(peek()) && peek()) { const op = s[i++]; const r = pow(); v = op === '*' ? v * r : op === '/' ? v / r : v % r; } return v; };
  const add = () => { let v = mul(); while (peek() === '+' || peek() === '-') { const op = s[i++]; const r = mul(); v = op === '+' ? v + r : v - r; } return v; };
  const v = add();
  if (i !== s.length || !Number.isFinite(v)) throw new Error('not plain maths');
  return Math.round(v * 1e6) / 1e6;
}
