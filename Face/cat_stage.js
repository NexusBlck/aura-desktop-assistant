// cat_stage.js
// The desktop "stage": when AURA's cat comes out to explain something, its
// transparent window covers the route for the trip, and this file plans and
// animates the whole journey at full frame rate (requestAnimationFrame) - no
// window dragging, so it's perfectly smooth.
//
// Every trip is assembled from random pieces, so it's different each time:
//   entrance  -> down to the taskbar -> travel along the taskbar ->
//   clear obstacles (shove windows aside) -> climb/fly/jump up to the text -> land
// and the way home is random too. Outfits, speeds and little flourishes vary.
//
// Python (explain_popup.py) gives us real screen geometry: the taskbar top
// ("ground"), window title bars to use as ledges, and windows in the way.
(function () {
  const stage = document.getElementById('stage');
  const props = document.getElementById('props');
  const wrap = document.getElementById('catwrap');
  const cat = AuraCat.mount(wrap, { onClick() { cat.pet(); api() && api().clicked(); } });
  const api = () => (window.pywebview && window.pywebview.api) || null;

  const CAT_W = 112, CAT_H = 98;
  const S = { x: 0, y: 0, rot: 0, facing: 'right' };

  // ------------------------------------------------------------ tiny animation kit
  const rand = (a, b) => a + Math.random() * (b - a);
  const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
  const chance = (p) => Math.random() < p;
  const lerp = (a, b, t) => a + (b - a) * t;
  const ease = {
    inOut: t => t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2,
    out: t => 1 - Math.pow(1 - t, 3),
    in: t => t * t * t,
    lin: t => t,
    back: t => { const c = 1.7; return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2); },
  };
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));

  function place(x, y, rot = 0) {
    S.x = x; S.y = y; S.rot = rot;
    wrap.style.transform = `translate(${x - CAT_W / 2}px, ${y - CAT_H}px) rotate(${rot}deg)`;
  }
  function face(dir) { S.facing = dir; cat.setFacing(dir); }
  function faceTo(x) { if (Math.abs(x - S.x) > 4) face(x < S.x ? 'left' : 'right'); }
  function pose(p) { cat.setState(p); }

  function tween(ms, fn, e = ease.inOut) {
    return new Promise(res => {
      const t0 = performance.now();
      function frame(now) {
        const raw = Math.min(1, (now - t0) / ms);
        fn(e(raw), raw);
        if (raw < 1) requestAnimationFrame(frame); else res();
      }
      requestAnimationFrame(frame);
    });
  }

  function prop(svg, x, y, w, h, extra = '') {
    const d = document.createElement('div');
    d.className = 'prop ' + extra;
    d.style.cssText = `left:${x}px;top:${y}px;width:${w}px;height:${h}px`;
    d.innerHTML = svg;
    props.appendChild(d);
    return d;
  }
  function vanish(el, ms = 350) {
    el.style.transition = `opacity ${ms}ms, transform ${ms}ms`;
    el.style.opacity = '0'; el.style.transform = (el.style.transform || '') + ' scale(.6)';
    setTimeout(() => el.remove(), ms + 20);
  }
  function poof(x, y, size = 70) {
    const p = prop(`<svg viewBox="0 0 100 100" width="100%" height="100%">
      ${[0, 1, 2, 3, 4, 5].map(i => { const a = i / 6 * Math.PI * 2;
        return `<circle cx="${50 + Math.cos(a) * 26}" cy="${50 + Math.sin(a) * 26}" r="16" fill="#9ef3ff" opacity=".85"/>`; }).join('')}
      <circle cx="50" cy="50" r="22" fill="#e9fdff"/></svg>`, x - size / 2, y - size / 2, size, size, 'poof');
    setTimeout(() => p.remove(), 650);
  }
  function sparkle(x, y) {
    const p = prop(`<svg viewBox="0 0 40 40" width="100%" height="100%"><path d="M20 0 L24 16 L40 20 L24 24 L20 40 L16 24 L0 20 L16 16Z" fill="#ffd34d"/></svg>`,
      x - 10, y - 10, 20, 20, 'sparkle');
    setTimeout(() => p.remove(), 700);
  }
  function say(text, ms = 1400) {   // little comic-style exclamation over the cat's head
    const b = document.createElement('div');
    b.className = 'quip'; b.textContent = text;
    b.style.left = (S.x - 40) + 'px'; b.style.top = (S.y - CAT_H - 34) + 'px';
    stage.appendChild(b);
    setTimeout(() => b.remove(), ms);
  }

  // ------------------------------------------------------------ moving the cat
  async function walkTo(x, speed = 190, style = 'walk') {
    faceTo(x);
    if (style === 'moonwalk') face(x < S.x ? 'right' : 'left');   // facing the "wrong" way, gliding backwards
    pose(style === 'sneak' ? 'sneak' : 'walk');
    const x0 = S.x, dist = Math.abs(x - x0);
    if (dist < 2) return;
    await tween(dist / speed * 1000, t => place(lerp(x0, x, t), S.y), ease.lin);
  }

  async function jumpTo(x, y, height = null, ms = null) {
    faceTo(x);
    const x0 = S.x, y0 = S.y;
    const h = height ?? Math.max(50, Math.abs(y - y0) * .45 + 40);
    const top = Math.min(y0, y) - h;
    ms = ms ?? 420 + Math.min(500, Math.hypot(x - x0, y - y0) * .55);
    pose('air');
    await tween(ms, (_, t) => {
      // quadratic Bezier through a peak above both points
      const cy = 2 * top - (y0 + y) / 2;
      const px = lerp(x0, x, t);
      const py = (1 - t) * (1 - t) * y0 + 2 * (1 - t) * t * cy + t * t * y;
      place(px, py, (x > x0 ? 1 : -1) * Math.sin(t * Math.PI) * 10);
    }, ease.lin);
    place(x, y, 0);
    cat.land(); pose('sit');
    await sleep(160);
  }

  // ------------------------------------------------------------ props (neon style)
  const C = { line: '#3ad6ff', bright: '#9ef3ff', dark: '#0a1a26', amber: '#ffb547', red: '#ff5a6a', violet: '#a78bfa', green: '#4ef0a3', pink: '#ff8fc8' };

  function ladderSVG(h) {
    let rungs = '';
    for (let y = 14; y < h - 6; y += 22) rungs += `<line x1="6" y1="${y}" x2="38" y2="${y}" stroke="${C.amber}" stroke-width="3" stroke-linecap="round"/>`;
    return `<svg viewBox="0 0 44 ${h}" width="44" height="${h}"><line x1="6" y1="0" x2="6" y2="${h}" stroke="${C.amber}" stroke-width="4"/>
      <line x1="38" y1="0" x2="38" y2="${h}" stroke="${C.amber}" stroke-width="4"/>${rungs}</svg>`;
  }

  // ------------------------------------------------------------ ENTRANCES
  const ENTRANCES = {
    async here(p) {           // already out on the desktop - set off from where it's sitting
      place(p.start.x, p.start.y); pose('sit');
      cat.hop(); await sleep(250);
      say(pick(['Another one?', 'On my way!', 'Ooh, what now?']));
    },
    async hud(p) {            // hop out of the AURA window
      place(p.start.x, p.start.y); pose('sit'); face('right');
      await sleep(150); cat.hop(); await sleep(280);
      say(pick(['On it!', 'Coming!', 'Mrrp!', 'Let me see...', 'Hold on!']));
    },
    async tray(p) {           // pop up out of the taskbar
      place(p.start.x, p.ground + CAT_H); pose('sit'); face('left');
      sparkle(p.start.x, p.ground);
      await tween(420, t => place(p.start.x, lerp(p.ground + CAT_H, p.ground, t)), ease.back);
      say(pick(['Hi!', 'Meow!', 'Need me?', '*stretches*']));
    },
    async parachute(p) {      // float down from the top of the screen
      const x = clamp(p.target.x + rand(-200, 200), 70, p.stage.w - 70);
      place(x, -20); pose('hang'); face(pick(['left', 'right']));
      const chute = prop(`<svg viewBox="0 0 120 80" width="120" height="80"><path d="M5 40 Q60 -25 115 40 Z" fill="${C.violet}" stroke="${C.bright}" stroke-width="2"/>
        <path d="M5 40 L60 80 M115 40 L60 80 M35 30 L60 80 M85 30 L60 80" stroke="${C.bright}" stroke-width="1.2"/></svg>`, 0, 0, 120, 80);
      await tween(2200, (e, t) => {
        const y = lerp(-20, p.ground, e), x2 = x + Math.sin(t * 7) * 28;
        place(x2, y, Math.sin(t * 7) * 6);
        chute.style.transform = `translate(${x2 - 60}px, ${y - CAT_H - 70}px)`;
      }, ease.out);
      vanish(chute); cat.land(); pose('sit');
      p.start = { x: S.x, y: S.y, from: 'chute' };
    },
  };

  // ------------------------------------------------------------ DOWN TO THE TASKBAR
  const DESCENTS = {
    async jump(p) { await jumpTo(S.x + rand(40, 120) * (chance(.5) ? 1 : -1), p.ground, 60); },
    async umbrella(p) {
      pose('hang');
      const um = prop(`<svg viewBox="0 0 100 90" width="90" height="81"><path d="M5 40 Q50 -20 95 40 Q83 32 72 40 Q61 32 50 40 Q39 32 28 40 Q17 32 5 40Z" fill="${C.red}" stroke="${C.pink}" stroke-width="2"/>
        <path d="M50 40 V82 q0 8 -8 8" fill="none" stroke="${C.bright}" stroke-width="3"/></svg>`, 0, 0, 90, 81);
      const x0 = S.x, y0 = S.y, x1 = x0 + rand(-120, 120);
      await tween(Math.max(900, (p.ground - y0) * 3.2), (e, t) => {
        const x = lerp(x0, x1, e) + Math.sin(t * 9) * 14, y = lerp(y0, p.ground, e);
        place(x, y, Math.sin(t * 9) * 5);
        um.style.transform = `translate(${x - 45}px, ${y - CAT_H - 62}px)`;
      }, ease.lin);
      vanish(um); cat.land(); pose('sit');
    },
    async pole(p) {           // slide down a fire pole
      const pl = prop(`<svg width="10" height="${p.ground - S.y + 120}"><rect width="8" height="100%" rx="4" fill="${C.bright}"/></svg>`,
        S.x + 26, S.y - 110, 10, p.ground - S.y + 120);
      pose('climb'); const y0 = S.y;
      await tween(Math.max(500, (p.ground - y0) * 1.4), e => place(S.x, lerp(y0, p.ground, e)), ease.in);
      cat.land(); pose('sit'); vanish(pl);
    },
    async slinky(p) {         // bounce down in big springy hops
      const y0 = S.y, n = 3;
      for (let i = 1; i <= n; i++) await jumpTo(S.x + rand(25, 60) * (chance(.5) ? 1 : -1), lerp(y0, p.ground, i / n), 30, 380);
    },
  };

  // ------------------------------------------------------------ ALONG THE TASKBAR
  const TRAVELS = {
    async walk(x) { await walkTo(x, rand(170, 230)); },
    async trot(x) { await walkTo(x, rand(300, 380)); },
    async sneak(x) { await walkTo(x, 110, 'sneak'); },
    async moonwalk(x) { await walkTo(x, 160, 'moonwalk'); face(x > S.x ? 'right' : 'left'); },
    async skateboard(x) {
      faceTo(x); pose('ride');
      const sk = prop(`<svg viewBox="0 0 90 22" width="90" height="22"><rect x="2" y="2" width="86" height="7" rx="3.5" fill="${C.green}"/>
        <circle cx="20" cy="15" r="5" fill="${C.bright}"/><circle cx="70" cy="15" r="5" fill="${C.bright}"/></svg>`, 0, 0, 90, 22);
      const x0 = S.x, y0 = S.y;
      await tween(Math.abs(x - x0) / 420 * 1000 + 200, e => {
        place(lerp(x0, x, e), y0 - 12);
        sk.style.transform = `translate(${S.x - 45}px, ${y0 - 14}px)`;
      }, ease.inOut);
      place(S.x, y0); vanish(sk);
    },
    async butterfly(x) {      // chase a butterfly that flutters toward the destination
      const bf = prop(`<svg viewBox="0 0 30 24" width="30" height="24"><path d="M15 12 Q2 -4 2 10 Q4 22 15 12 Q28 -4 28 10 Q26 22 15 12Z" fill="${C.pink}"/></svg>`, 0, 0, 30, 24, 'flutter');
      const x0 = S.x, y0 = S.y, dist = Math.abs(x - x0);
      faceTo(x); pose('walk');
      await tween(dist / 240 * 1000 + 300, (e, t) => {
        place(lerp(x0, x, e), y0);
        bf.style.transform = `translate(${lerp(x0, x, Math.min(1, e + .18)) - 15 + Math.sin(t * 20) * 8}px, ${y0 - 120 + Math.sin(t * 13) * 18}px)`;
      }, ease.lin);
      bf.style.transition = 'transform 1s ease-in, opacity 1s'; bf.style.opacity = '0';
      bf.style.transform += ' translateY(-140px)'; setTimeout(() => bf.remove(), 1000);
      say(pick(['Aww...', 'Next time!', 'So close!']));
    },
    async tumble(x) {         // trips halfway and rolls the rest
      const mid = lerp(S.x, x, .45);
      await walkTo(mid, 220);
      say('Whoa!', 800); pose('tumble');
      const x0 = S.x;
      await tween(Math.abs(x - x0) / 320 * 1000 + 200, e => place(lerp(x0, x, e), S.y), ease.out);
      pose('sit'); cat.land(); await sleep(250); say(pick(['I meant that.', 'Nailed it.', '...ow']));
    },
    async zoomies(x) {        // overshoots, skids, comes back
      const over = x + (x > S.x ? 1 : -1) * rand(60, 120);
      await walkTo(over, 520);
      say('Oops!', 700); await sleep(150);
      await walkTo(x, 200);
    },
  };

  // ------------------------------------------------------------ UP TO THE TEXT
  const ASCENTS = {
    async ladder(tx, ty) {
      await walkTo(tx - 34, 220); face('right'); pose('sit');
      say(pick(['Ladder time!', 'One sec...', '*pulls out a ladder*']));
      const h = S.y - ty + 20;
      const lad = prop(ladderSVG(h), tx - 22, ty - 20, 44, h, 'grow-up');
      await tween(650, e => { lad.style.transform = `scaleY(${e})`; }, ease.back);
      await walkTo(tx, 200);
      pose('climb'); const y0 = S.y;
      await tween(Math.max(700, (y0 - ty) * 6), (e, t) => place(tx, lerp(y0, ty, e) + Math.sin(t * 40) * 1.5), ease.inOut);
      place(tx, ty); pose('sit'); cat.land();
      await sleep(200);
      lad.style.transformOrigin = 'top'; await tween(380, e => { lad.style.transform = `scaleY(${1 - e})`; }, ease.in);
      lad.remove();
    },
    async rope(tx, ty) {      // grappling hook
      await walkTo(tx + pick([-30, 30]), 220); face(tx > S.x ? 'right' : 'left'); pose('sit');
      const ns = 'http://www.w3.org/2000/svg';
      const svg = document.createElementNS(ns, 'svg');
      svg.setAttribute('class', 'overlay'); svg.setAttribute('width', stage.clientWidth); svg.setAttribute('height', stage.clientHeight);
      const line = document.createElementNS(ns, 'line'); line.setAttribute('stroke', C.amber); line.setAttribute('stroke-width', 2.5);
      const hook = document.createElementNS(ns, 'path'); hook.setAttribute('d', 'M0 0 q-8 -2 -8 -10 M0 0 q8 -2 8 -10 M0 0 v6');
      hook.setAttribute('stroke', C.bright); hook.setAttribute('fill', 'none'); hook.setAttribute('stroke-width', 2.5);
      svg.append(line, hook); props.appendChild(svg);
      const sx = S.x, sy = S.y - 60;
      await tween(520, (e) => {
        const hx = lerp(sx, tx, e), hy = lerp(sy, ty, e) - Math.sin(e * Math.PI) * 60;
        line.setAttribute('x1', sx); line.setAttribute('y1', sy); line.setAttribute('x2', hx); line.setAttribute('y2', hy);
        hook.setAttribute('transform', `translate(${hx},${hy})`);
      }, ease.out);
      sparkle(tx, ty); say('Clink!', 700);
      pose('climb'); const x0 = S.x, y0 = S.y;
      await tween(Math.max(800, (y0 - ty) * 7), (e, t) => {
        place(lerp(x0, tx, e), lerp(y0, ty, e), Math.sin(t * 18) * 4);
        line.setAttribute('x1', S.x); line.setAttribute('y1', S.y - 50);
      }, ease.inOut);
      place(tx, ty); pose('sit'); cat.land(); svg.remove();
    },
    async balloon(tx, ty) {
      await walkTo(tx + rand(-40, 40), 220); pose('hang');
      const colors = [C.red, C.violet, C.green, C.amber, C.pink];
      const bl = prop(`<svg viewBox="0 0 80 110" width="80" height="110">${[[-14, 0], [14, 4], [0, -12]].map(([dx, dy], i) =>
        `<path d="M40 105 Q${40 + dx / 2} 70 ${40 + dx} ${40 + dy}" stroke="${C.bright}" fill="none" stroke-width="1"/>
         <ellipse cx="${40 + dx}" cy="${26 + dy}" rx="15" ry="19" fill="${colors[(i + Math.floor(rand(0, 5))) % 5]}"/>`).join('')}</svg>`, 0, 0, 80, 110);
      const x0 = S.x, y0 = S.y;
      await tween(Math.max(1200, (y0 - ty) * 6), (e, t) => {
        const x = lerp(x0, tx, e) + Math.sin(t * 6) * 22, y = lerp(y0, ty, e);
        place(x, y, Math.sin(t * 6) * 5);
        bl.style.transform = `translate(${x - 40}px, ${y - CAT_H - 95}px)`;
      }, ease.inOut);
      poof(S.x, S.y - CAT_H - 60); bl.remove(); say('Pop!', 600);
      place(tx, ty); cat.land(); pose('sit');
    },
    async jetpack(tx, ty) {
      faceTo(tx); pose('ride');
      const jp = prop(`<svg viewBox="0 0 40 70" width="40" height="70"><rect x="8" y="4" width="24" height="34" rx="8" fill="${C.line}" stroke="${C.bright}"/>
        <path class="flame" d="M12 38 Q20 70 28 38Z" fill="${C.amber}"/></svg>`, 0, 0, 40, 70, 'jet');
      say(pick(['Liftoff!', '3... 2... 1...', 'Vrrroom!']));
      const x0 = S.x, y0 = S.y;
      await tween(1100 + Math.hypot(tx - x0, ty - y0) * 1.2, (e, t) => {
        const x = lerp(x0, tx, e) + Math.sin(t * 25) * 3, y = lerp(y0, ty, ease.out(e)) + Math.sin(t * 31) * 2;
        place(x, y, (tx > x0 ? 1 : -1) * 8);
        jp.style.transform = `translate(${x - (S.facing === 'right' ? 52 : -12)}px, ${y - 70}px)`;
      }, ease.inOut);
      vanish(jp); place(tx, ty); cat.land(); pose('sit');
    },
    async pogo(tx, ty) {
      const n = Math.max(3, Math.min(6, Math.round((S.y - ty) / 120)));
      const stick = prop(`<svg viewBox="0 0 20 60" width="20" height="60"><rect x="8" y="0" width="4" height="60" fill="${C.bright}"/>
        <path d="M2 40 h16 M4 46 h12 M4 52 h12" stroke="${C.amber}" stroke-width="3"/></svg>`, 0, 0, 20, 60);
      const follow = () => stick.style.transform = `translate(${S.x - 10}px, ${S.y - 30}px)`;
      const x0 = S.x, y0 = S.y;
      for (let i = 1; i <= n; i++) {
        const x = lerp(x0, tx, i / n), y = lerp(y0, ty, i / n);
        const xs = S.x, ys = S.y, peak = Math.min(ys, y) - 70;
        pose('air');
        await tween(420, (_, t) => {
          const cy = 2 * peak - (ys + y) / 2;
          place(lerp(xs, x, t), (1 - t) * (1 - t) * ys + 2 * (1 - t) * t * cy + t * t * y); follow();
        }, ease.lin);
        cat.land();
      }
      vanish(stick); pose('sit');
    },
    async spring(tx, ty) {
      await walkTo(tx + pick([-110, 110]), 220);
      const sp = prop(`<svg viewBox="0 0 40 40" width="40" height="40"><path d="M4 36 h32 M8 30 l24 -6 l-24 -6 l24 -6 l-24 -6" stroke="${C.green}" stroke-width="3" fill="none"/></svg>`,
        S.x - 20, S.y - 36, 40, 40);
      place(S.x, S.y - 30); await sleep(150);
      await tween(160, e => { sp.style.transform = `scaleY(${1 - e * .5})`; place(S.x, S.y + 1.2); });
      say('Boing!', 700);
      await jumpTo(tx, ty, 140, 800);
      vanish(sp);
    },
    async boxes(tx, ty) {     // stacks cardboard boxes into stairs and hops up
      const steps = Math.max(2, Math.min(7, Math.round((S.y - ty) / 70)));
      const dir = tx > S.x ? 1 : -1, size = (S.y - ty) / steps;
      const startX = tx - dir * steps * 46;
      await walkTo(startX - dir * 30, 220);
      const els = [];
      for (let i = 0; i < steps; i++) {
        const bx = startX + dir * i * 46 - 23, h = size * (i + 1);
        const b = prop(`<svg viewBox="0 0 46 ${h}" width="46" height="${h}" preserveAspectRatio="none"><rect x="1" y="1" width="44" height="${h - 2}" fill="#7a5a2e" stroke="${C.amber}" stroke-width="2"/></svg>`,
          bx, S.y - h, 46, h, 'drop-in');
        els.push(b); await sleep(110);
      }
      for (let i = 0; i < steps; i++) await jumpTo(startX + dir * i * 46, S.y - size, 26, 300);
      await jumpTo(tx, ty, 30, 360);
      els.forEach((b, i) => setTimeout(() => vanish(b), i * 70));
    },
    async teleport(tx, ty) {
      say(pick(['Abracadabra!', 'Beam me up!', 'Shortcut!']));
      const ring = (x, y) => prop(`<svg viewBox="0 0 120 40" width="120" height="40"><ellipse cx="60" cy="20" rx="56" ry="15" fill="#0b0420" stroke="${C.violet}" stroke-width="4"/></svg>`,
        x - 60, y - 20, 120, 40, 'portal');
      const a = ring(S.x, S.y);
      await tween(500, e => { wrap.style.clipPath = `inset(0 0 ${e * 100}% 0)`; place(S.x, S.y + 1.6); });
      const yStart = S.y;
      const b = ring(tx, ty);
      place(tx, ty + CAT_H);
      await tween(500, e => { place(tx, lerp(ty + CAT_H, ty, e)); wrap.style.clipPath = `inset(0 0 ${(1 - e) * 100}% 0)`; });
      wrap.style.clipPath = ''; vanish(a); vanish(b); cat.land(); sparkle(tx, ty - 50);
    },
    async beanstalk(tx, ty) {
      await walkTo(tx, 220); pose('ride');
      const h = S.y - ty + 10, y0 = S.y;
      const vine = prop(`<svg viewBox="0 0 40 ${h}" width="40" height="${h}" preserveAspectRatio="none">
        <path d="M20 ${h} C0 ${h * .75} 40 ${h * .5} 20 ${h * .25} S30 0 20 0" stroke="${C.green}" stroke-width="6" fill="none"/>
        ${[.2, .4, .6, .8].map((k, i) => `<ellipse cx="${i % 2 ? 30 : 10}" cy="${h * k}" rx="9" ry="5" fill="${C.green}"/>`).join('')}</svg>`,
        tx - 20, ty - 10, 40, h, 'grow-up');
      await tween(Math.max(900, h * 4), e => { vine.style.transform = `scaleY(${e})`; place(tx, lerp(y0, ty, e)); }, ease.out);
      cat.land(); pose('sit'); await sleep(300); vanish(vine, 600);
    },
    async elevator(tx, ty) {
      await walkTo(tx, 220); pose('ride');
      const pf = prop(`<svg viewBox="0 0 100 14" width="100" height="14"><rect width="100" height="8" rx="3" fill="${C.line}"/>
        <rect x="10" y="8" width="80" height="4" fill="${C.dark}"/></svg>`, 0, 0, 100, 14);
      say('Going up!', 900);
      const y0 = S.y;
      await tween(Math.max(900, (y0 - ty) * 3.4), e => { place(tx, lerp(y0, ty, e)); pf.style.transform = `translate(${tx - 50}px, ${S.y}px)`; }, ease.inOut);
      say('*ding*', 700); vanish(pf); pose('sit');
    },
  };

  // Parkour: hop up window title bars ("ledges") that sit between the taskbar and the text.
  async function parkour(p, tx, ty) {
    let ledges = (p.ledges || []).filter(l => l.y < S.y - 40 && l.y > ty + 30 && l.x2 - l.x1 > 80)
      .sort((a, b) => b.y - a.y);
    const chain = [];
    let cy = S.y;
    for (const l of ledges) {
      if (cy - l.y > 60 && cy - l.y < 320 && chain.length < 4) { chain.push(l); cy = l.y; }
    }
    if (!chain.length || ty < cy - 340) return false;
    say(pick(['Parkour!', 'Watch this!', 'Hup!']));
    for (const l of chain) {
      const x = clamp(lerp(S.x, tx, .5), l.x1 + 40, l.x2 - 40);
      await jumpTo(x, l.y, 60);
      if (chance(.4)) { await walkTo(clamp(x + rand(-80, 80), l.x1 + 30, l.x2 - 30), 240); }
    }
    await jumpTo(tx, ty, 70);
    return true;
  }

  // ------------------------------------------------------------ OBSTACLES
  async function shove(o) {
    // walk to the side of the window that faces away from where it'll go, and push
    const dir = o.dx > 0 ? 1 : -1;
    const sideX = clamp(dir > 0 ? o.x + 20 : o.x + o.w - 20, 60, stage.clientWidth - 60);
    if (Math.abs(S.x - sideX) > 20) await walkTo(sideX, 260);
    face(dir > 0 ? 'right' : 'left'); pose('push');
    say(pick(['Excuse me!', 'Move!', 'Coming through!', 'Hnnngh!', 'Scoot over!']), 1100);
    if (api()) api().push_window(o.id);
    await tween(700, (_, t) => place(S.x + dir * Math.sin(t * Math.PI) * 10, S.y), ease.lin);
    pose('sit');
  }

  // ------------------------------------------------------------ EXITS (going home)
  const EXITS = {
    async walkOff(p) {        // strolls off the edge of the taskbar
      await walkTo(chance(.5) ? -60 : stage.clientWidth + 60, 260);
    },
    async dig(p) {
      say(pick(['Bye!', 'See ya!', '*digs*']));
      await tween(600, e => { wrap.style.clipPath = `inset(0 0 ${e * 100}% 0)`; place(S.x, S.y + 1.5); });
    },
    async plane(p) {          // paper airplane off into the distance
      const pl = prop(`<svg viewBox="0 0 90 40" width="90" height="40"><path d="M2 20 L88 2 L40 24 Z" fill="#e9fdff"/><path d="M40 24 L88 2 L52 38 Z" fill="#b8eef7"/></svg>`, 0, 0, 90, 40);
      pose('ride'); say('Wheee!', 900);
      const x0 = S.x, y0 = S.y, dir = chance(.5) ? 1 : -1; face(dir > 0 ? 'right' : 'left');
      await tween(1600, (e, t) => {
        const x = x0 + dir * e * (stage.clientWidth + 200), y = y0 - e * 260 + Math.sin(t * 8) * 16;
        place(x, y - 10, dir * -6); pl.style.transform = `translate(${x - 45}px, ${y - 30}px) scaleX(${dir})`;
      }, ease.in);
      pl.remove();
    },
    async poofAway(p) { say(pick(['Ta-da!', 'Poof!'])); await sleep(300); poof(S.x, S.y - 40, 110); wrap.style.opacity = '0'; await sleep(300); },
    async balloonAway(p) {
      pose('hang');
      const bl = prop(`<svg viewBox="0 0 40 70" width="40" height="70"><path d="M20 66 Q18 50 20 38" stroke="${C.bright}" fill="none"/><ellipse cx="20" cy="20" rx="16" ry="20" fill="${C.red}"/></svg>`, 0, 0, 40, 70);
      const x0 = S.x, y0 = S.y;
      await tween(2000, (e, t) => { const x = x0 + Math.sin(t * 6) * 20, y = y0 - e * (y0 + 220); place(x, y);
        bl.style.transform = `translate(${x - 20}px, ${y - CAT_H - 60}px)`; }, ease.in);
      bl.remove();
    },
  };

  // ------------------------------------------------------------ helpers
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function fresh(table, kind) {
    // random choice that avoids repeating what was used last time
    const keys = Object.keys(table);
    let last = null;
    try { last = JSON.parse(localStorage.getItem('aura-cat-last') || '{}')[kind]; } catch (e) {}
    const options = keys.length > 1 ? keys.filter(k => k !== last) : keys;
    const choice = pick(options);
    try { const m = JSON.parse(localStorage.getItem('aura-cat-last') || '{}'); m[kind] = choice; localStorage.setItem('aura-cat-last', JSON.stringify(m)); } catch (e) {}
    return choice;
  }
  function resetCat() { wrap.style.clipPath = ''; wrap.style.opacity = '1'; props.innerHTML = ''; }

  // ------------------------------------------------------------ the trip (called from Python)
  let running = false;
  window.startTrip = async function (p) {
    if (running) return;
    running = true;
    resetCat();
    try {
      cat.setOutfit(chance(.75) ? pick(AuraCat.OUTFITS) : null);
      const entry = ['hud', 'here'].includes(p.start.from) ? p.start.from : (chance(.35) ? 'parachute' : 'tray');
      await ENTRANCES[entry](p);

      const tx = clamp(p.target.x, 60, p.stage.w - 60), ty = p.target.y;
      // Close to the target already (e.g. the text is right above the AURA cat)? Just jump.
      if (Math.hypot(tx - S.x, ty - S.y) < 140) {
        await jumpTo(tx, ty, 50);
      } else {
        if (S.y < p.ground - 10) await DESCENTS[fresh(DESCENTS, 'descent')](p);
        // obstacles in the climb column / where the answer bubble goes get shoved aside first
        for (const o of (p.obstacles || []).slice(0, 2)) await shove(o);
        const goal = clamp(tx + pick([-1, 1]) * rand(0, 40), 60, p.stage.w - 60);
        await TRAVELS[fresh(TRAVELS, 'travel')](goal);
        const triedParkour = chance(.35) && await parkour(p, tx, ty);
        if (!triedParkour) await ASCENTS[fresh(ASCENTS, 'ascent')](tx, ty);
      }
      place(tx, ty); pose('sit'); face(pick(['left', 'right']));
    } catch (e) {
      console.error('trip failed', e);
      place(p.target.x, p.target.y); pose('sit');
    } finally {
      running = false;
      api() && api().arrived(S.x, S.y);
    }
  };

  window.goHome = async function (p) {
    if (running) return;
    running = true;
    try {
      place(p.from.x, p.from.y); pose('sit');
      if (p.home) {
        // back into the AURA window: down to the taskbar, travel under it, climb up, hop in
        if (S.y < p.ground - 10) await DESCENTS[fresh(DESCENTS, 'descent')](p);
        await TRAVELS[fresh(TRAVELS, 'travel')](p.home.x);
        await ASCENTS[fresh(ASCENTS, 'ascent')](p.home.x, p.home.y);
        cat.hop(); await sleep(250);
      } else {
        if (S.y < p.ground - 10 && chance(.6)) await DESCENTS[fresh(DESCENTS, 'descent')](p);
        await EXITS[fresh(EXITS, 'exit')](p);
      }
    } catch (e) {
      console.error('home trip failed', e);
    } finally {
      running = false;
      api() && api().home_reached();
    }
  };

  // Resting next to the answer bubble (the window has shrunk to just the cat).
  window.restAt = function (x, y) { resetCat(); place(x, y); pose('sit'); };
  window.catState = (s) => cat.setState(s);
  window.catFacing = (d) => cat.setFacing(d);
  window.catHop = () => cat.hop();
})();
