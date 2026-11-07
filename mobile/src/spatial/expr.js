// A tiny SAFE maths-expression compiler for parametric surfaces ("cos(u)*(2+cos(v))"): a recursive-descent parser over a
// fixed vocabulary that builds closures - no eval, no Function, nothing from the expression is ever run as code.
const FUN = { sin: Math.sin, cos: Math.cos, tan: Math.tan, asin: Math.asin, acos: Math.acos, atan: Math.atan, sinh: Math.sinh, cosh: Math.cosh,
  tanh: Math.tanh, sqrt: (x) => Math.sqrt(Math.max(0, x)), abs: Math.abs, exp: Math.exp, log: (x) => Math.log(Math.max(1e-9, x)), floor: Math.floor,
  sign: Math.sign, pow: Math.pow, min: Math.min, max: Math.max, atan2: Math.atan2 };
const CONST = { pi: Math.PI, e: Math.E, tau: Math.PI * 2 };

/** Returns f(vars) -> number, or throws on anything outside the vocabulary. */
export function compile(src, varNames = ['u', 'v', 't']) {
  const s = String(src || '0').toLowerCase().replace(/\*\*/g, '^').slice(0, 300);
  const toks = s.match(/\d*\.?\d+(?:e[+-]?\d+)?|[a-z_]\w*|[-+*/^(),]/g) || [];
  if (toks.join('') !== s.replace(/\s+/g, '')) throw new Error('bad expression');
  let i = 0;
  const peek = () => toks[i], take = () => toks[i++];
  const expect = (t) => { if (take() !== t) throw new Error('bad expression'); };
  function primary() {
    const t = take();
    if (t === undefined) throw new Error('bad expression');
    if (/^\d|^\./.test(t)) { const n = parseFloat(t); return () => n; }
    if (t === '(') { const e = add(); expect(')'); return e; }
    if (t === '-') { const e = power(); return (x) => -e(x); }
    if (t === '+') return power();
    if (FUN[t]) {
      expect('(');
      const args = [add()];
      while (peek() === ',') { take(); args.push(add()); }
      expect(')');
      const f = FUN[t];
      return args.length === 1 ? (x) => f(args[0](x)) : (x) => f(...args.map((a) => a(x)));
    }
    if (t in CONST) { const c = CONST[t]; return () => c; }
    const k = varNames.indexOf(t);
    if (k >= 0) return (x) => x[k];
    throw new Error(`unknown "${t}"`);
  }
  function power() {
    const b = primary();
    if (peek() === '^') { take(); const ex = power(); return (x) => Math.pow(b(x), ex(x)); }
    return b;
  }
  function mul() {
    let l = power();
    while (peek() === '*' || peek() === '/') {
      const op = take(), r = power(), a = l;
      l = op === '*' ? (x) => a(x) * r(x) : (x) => a(x) / r(x);
    }
    return l;
  }
  function add() {
    let l = mul();
    while (peek() === '+' || peek() === '-') {
      const op = take(), r = mul(), a = l;
      l = op === '+' ? (x) => a(x) + r(x) : (x) => a(x) - r(x);
    }
    return l;
  }
  const f = add();
  if (i !== toks.length) throw new Error('bad expression');
  return (...vals) => { const r = f(vals); return Number.isFinite(r) ? r : 0; };
}
