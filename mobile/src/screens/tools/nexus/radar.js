// RADAR - situational awareness. GPS + compass on the phone, places from OpenStreetMap (fetched by the PC):
//   SCOPE  a heading-up radar with every place around you as a blip, plus Qibla, the sun and the moon
//   AR     the camera with floating labels where things really are
//   LIST   nearest first
//   ASK    "where's the nearest pharmacy that's open?" - AURA answers with directions relative to you
import { h, el, icon, toast, openSheet, button, loading } from '../../../ui/components.js';
import * as haptics from '../../../platform/haptics.js';
import * as storage from '../../../platform/storage.js';
import * as audio from '../../../platform/audio.js';
import { requestMotion, onOrientation, watchPosition, keepAwake, distBearing, cardinal, sunPosition, moonPosition, KAABA } from '../../../platform/sensors.js';
import { stage, camera, stopStream, camError, readout, playReply } from './common.js';

const CATS = [['food', 'FOOD', '#ffb547'], ['cafe', 'CAFE', '#f59e7a'], ['pharmacy', 'PHARMACY', '#4ef0a3'], ['hospital', 'HOSPITAL', '#ff5a6a'],
  ['atm', 'ATM', '#9ef3ff'], ['fuel', 'FUEL', '#c084fc'], ['mosque', 'MOSQUE', '#86efac'], ['shop', 'SHOP', '#60a5fa'],
  ['transport', 'TRANSPORT', '#fbbf24'], ['study', 'STUDY', '#a78bfa'], ['park', 'PARK', '#34d399']];
const COLOR = Object.fromEntries(CATS.map(([k, , c]) => [k, c]));
const RANGES = [[500, '500 M'], [1000, '1 KM'], [2000, '2 KM'], [4000, '4 KM']];
const FOV = 56;     // the iPhone main camera's horizontal view in portrait, roughly

const fmtDist = (m) => (m >= 1000 ? `${(m / 1000).toFixed(m >= 10000 ? 0 : 1)} km` : `${Math.round(m)} m`);

export function open(ctx) {
  stage('RADAR', 'SYS-04', (body, page, onCleanup) => {
    const S = {
      view: storage.get('nx.radar.view', 'scope'), cats: storage.get('nx.radar.cats', ['food', 'cafe', 'pharmacy', 'hospital', 'atm', 'mosque']),
      range: storage.get('nx.radar.range', 1000), pos: null, heading: null, beta: 90, places: [], fetchedAt: null, highlight: new Set(),
      address: '', alive: true, stream: null, aligned: false, motion: false,
    };

    // ---------------------------------------------------------------- DOM
    const viewSeg = h('div', { class: 'seg nx-seg' });
    const views = [['scope', 'SCOPE'], ['ar', 'AR'], ['list', 'LIST']].map(([v, label]) => {
      const b = h('button', { class: v === S.view ? 'active' : '' }, label);
      b.onclick = () => { haptics.tap(); setView(v); };
      viewSeg.append(b);
      return [v, b];
    });
    const rHead = readout('HEADING'), rPos = readout('POSITION'), rAlt = readout('ALT'), rSpd = readout('SPEED');
    const addr = el('div', 'nx-addr', 'Finding you…');
    const qibla = el('div', 'nx-qibla', '');
    const catsBox = h('div', { class: 'nx-chips scroll' });
    for (const [k, label, color] of CATS) {
      const b = h('button', { class: 'nx-chip' + (S.cats.includes(k) ? ' on' : ''), style: `--c:${color}` }, label);
      b.onclick = () => {
        haptics.tap();
        S.cats = S.cats.includes(k) ? S.cats.filter((x) => x !== k) : [...S.cats, k];
        b.classList.toggle('on'); storage.set('nx.radar.cats', S.cats); fetchPlaces(true);
      };
      catsBox.append(b);
    }
    const rangeBox = h('div', { class: 'nx-chips' });
    const rangeBtns = RANGES.map(([m, label]) => {
      const b = h('button', { class: 'nx-chip' + (m === S.range ? ' on' : '') }, label);
      b.onclick = () => { S.range = m; storage.set('nx.radar.range', m); rangeBtns.forEach((x) => x.classList.toggle('on', x === b)); fetchPlaces(true); };
      rangeBox.append(b);
      return b;
    });

    const scope = h('canvas', { class: 'nx-scope' });
    const scopeWrap = h('div', { class: 'nx-scope-wrap' }, scope);
    const video = h('video', { playsinline: true, muted: true, autoplay: true });
    const arLayer = h('div', { class: 'nx-ar-layer' });
    const arWrap = h('div', { class: 'nx-ar' }, video, arLayer, h('div', { class: 'nx-ar-horizon' }), h('div', { class: 'nx-ar-cross' }));
    const listBox = h('div', { class: 'nx-list' });
    const compassBtn = h('button', { class: 'btn solid block', hidden: true }, icon('target'), 'ENABLE COMPASS');
    compassBtn.onclick = async () => { const ok = await requestMotion(); if (ok) { startCompass(); compassBtn.hidden = true; } else toast('Compass', 'Motion access was declined - allow it in Settings → Safari → Motion & Orientation Access.', 'error'); };

    const ask = h('input', { class: 'input', placeholder: 'Ask about here… "nearest pharmacy open now?"', enterkeyhint: 'search' });
    const askOut = h('div');
    const doAsk = async () => {
      if (!ask.value.trim() || !S.pos) return;
      askOut.replaceChildren(loading('Looking around…'));
      try {
        const r = await ctx.api.nexus('around', { question: ask.value.trim(), lat: S.pos.lat, lon: S.pos.lon, heading: S.heading == null ? '?' : Math.round(S.heading),
          address: S.address, places: nearestPerCat(8) }, 60000);
        S.highlight = new Set((r.highlight || []).map((x) => String(x).toLowerCase()));
        askOut.replaceChildren(h('div', { class: 'nx-answer' }, el('p', null, r.answer || 'No answer.')));
        if (ctx.store.get('settings').voice) playReply(r);
        renderList();
      } catch (e) { askOut.replaceChildren(el('div', 'err', e.message)); }
    };
    ask.onkeydown = (e) => { if (e.key === 'Enter') doAsk(); };

    const main = h('div', { class: 'nx-radar-main' }, scopeWrap, arWrap, listBox);
    body.append(h('div', { class: 'nx-scroll nx-radar' }, viewSeg, compassBtn,
      h('div', { class: 'nx-reads' }, rHead.node, rPos.node, rAlt.node, rSpd.node), addr, qibla, main, catsBox, rangeBox,
      h('div', { class: 'nx-panel' }, el('div', 'nx-label', 'ASK ABOUT HERE'), h('div', { class: 'nx-row' }, ask, button('ASK', doAsk, 'solid')), askOut)));

    // ---------------------------------------------------------------- views
    async function setView(v) {
      S.view = v; storage.set('nx.radar.view', v);
      views.forEach(([k, b]) => b.classList.toggle('active', k === v));
      scopeWrap.hidden = v !== 'scope'; arWrap.hidden = v !== 'ar'; listBox.hidden = v !== 'list';
      if (v === 'ar' && !S.stream) {
        try { S.stream = await camera('environment'); video.srcObject = S.stream; await video.play().catch(() => {}); } catch (e) { camError(e); }
      } else if (v !== 'ar' && S.stream) { stopStream(S.stream); S.stream = null; }
      if (v === 'list') renderList();
    }

    // ---------------------------------------------------------------- sensors
    let stopCompass = () => {};
    function startCompass() {
      stopCompass();
      S.motion = true;
      stopCompass = onOrientation((o) => {
        if (o.heading != null) S.heading = S.heading == null ? o.heading : smoothAngle(S.heading, o.heading, 0.25);
        if (o.beta != null) S.beta = S.beta * 0.8 + o.beta * 0.2;
      });
    }
    const smoothAngle = (a, b, k) => { const d = ((b - a + 540) % 360) - 180; return (a + d * k + 360) % 360; };
    if (typeof (window.DeviceOrientationEvent || {}).requestPermission === 'function') compassBtn.hidden = false; else startCompass();

    const stopGps = watchPosition((p) => {
      const first = !S.pos;
      S.pos = p;
      rPos.set(`${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}`);
      rAlt.set(p.alt != null ? `${Math.round(p.alt)} m` : '--');
      rSpd.set(p.speed != null && p.speed >= 0 ? `${(p.speed * 3.6).toFixed(p.speed < 3 ? 1 : 0)} km/h` : '--');
      const q = distBearing(p.lat, p.lon, KAABA.lat, KAABA.lon);
      S.qibla = q;
      if (first) {
        fetchPlaces(true);
        ctx.api.nexus('where', { lat: p.lat, lon: p.lon }, 20000).then((r) => { S.address = r.address || ''; addr.textContent = r.short || r.address || `±${Math.round(p.acc)} m`; }).catch(() => {});
      } else fetchPlaces(false);
    }, (e) => { addr.textContent = e && e.code === 1 ? 'Location is off - allow it in Settings → Privacy → Location Services → Safari Websites.' : 'Waiting for GPS…'; });

    let fetching = false;
    async function fetchPlaces(force) {
      if (!S.pos || fetching) return;
      if (!force && S.fetchedAt && distBearing(S.pos.lat, S.pos.lon, S.fetchedAt.lat, S.fetchedAt.lon).dist < 150) return;
      if (!S.cats.length) { S.places = []; renderList(); return; }
      fetching = true;
      try {
        const r = await ctx.api.nexus('places', { lat: S.pos.lat, lon: S.pos.lon, radius: S.range, cats: S.cats }, 75000);
        if (r.error) toast('RADAR', r.error, 'error', 4000);
        S.places = r.places || [];
        S.fetchedAt = { lat: S.pos.lat, lon: S.pos.lon };
        renderList();
      } catch (e) { toast('RADAR', e.message, 'error', 3500); }
      fetching = false;
    }

    // what AURA gets to answer from: the closest few of every kind, so "nearest pharmacy" works even when food is closer
    function nearestPerCat(n) {
      const by = {};
      for (const p of S.places.map((x) => ({ ...x, ...live(x) })).sort((a, b) => a.dist - b.dist)) {
        by[p.cat] = by[p.cat] || [];
        if (by[p.cat].length < n) by[p.cat].push({ ...p, dist: Math.round(p.dist), bearing: Math.round(p.bearing) });
      }
      return Object.values(by).flat();
    }

    // live distance/bearing (you move, the list doesn't need refetching)
    const live = (p) => (S.pos ? distBearing(S.pos.lat, S.pos.lon, p.lat, p.lon) : { dist: p.dist, bearing: p.bearing });

    function renderList() {
      if (S.view !== 'list') return;
      listBox.innerHTML = '';
      if (!S.places.length) { listBox.append(el('div', 'empty', S.pos ? 'Nothing found in range - widen the range or pick more categories.' : 'Waiting for your location…')); return; }
      const rows = S.places.map((p) => ({ p, ...live(p) })).sort((a, b) => a.dist - b.dist).slice(0, 80);
      for (const { p, dist, bearing } of rows) {
        const rel = S.heading == null ? null : ((bearing - S.heading + 540) % 360) - 180;
        const row = h('button', { class: 'nx-item' + (S.highlight.has(p.name.toLowerCase()) ? ' hl' : '') },
          h('span', { class: 'nx-dot', style: `background:${COLOR[p.cat] || '#9ef3ff'}` }),
          h('div', {}, el('b', null, p.name), el('span', null, `${p.kind.replace(/_/g, ' ')} · ${fmtDist(dist)} ${cardinal(bearing)}${p.open ? ' · ' + p.open : ''}`)),
          h('span', { class: 'nx-arrow', style: rel == null ? '' : `transform:rotate(${rel}deg)` }, '➤'));
        row.onclick = () => placeSheet(p);
        listBox.append(row);
      }
    }

    function placeSheet(p) {
      haptics.tap();
      const { dist, bearing } = live(p);
      openSheet(p.name, (b) => {
        b.append(h('div', { class: 'kv' }, el('span', null, 'TYPE'), el('b', null, p.kind.replace(/_/g, ' '))),
          h('div', { class: 'kv' }, el('span', null, 'DISTANCE'), el('b', null, `${fmtDist(dist)} · ${Math.round(bearing)}° ${cardinal(bearing)}`)),
          p.open ? h('div', { class: 'kv' }, el('span', null, 'HOURS'), el('b', null, p.open)) : null,
          h('div', { class: 'row-btns', style: 'margin-top:14px' },
            button('NAVIGATE', () => { window.location.href = `https://maps.apple.com/?daddr=${p.lat},${p.lon}&dirflg=w`; }, 'solid'),
            p.phone ? button('CALL', () => { window.location.href = `tel:${p.phone.replace(/[^\d+]/g, '')}`; }) : null));
      });
    }

    // ---------------------------------------------------------------- drawing
    let raf = 0, lastList = 0;
    const blipHits = [];
    function frame() {
      if (!S.alive) return;
      const hd = S.heading;
      rHead.set(hd == null ? (S.motion ? 'CALIBRATING' : '--') : `${Math.round(hd)}° ${cardinal(hd)}`);
      if (S.qibla) {
        const rel = hd == null ? null : ((S.qibla.bearing - hd + 540) % 360) - 180;
        const aligned = rel != null && Math.abs(rel) < 4;
        qibla.textContent = `◆ QIBLA ${Math.round(S.qibla.bearing)}° ${cardinal(S.qibla.bearing)} · ${fmtDist(S.qibla.dist)}${rel == null ? '' : aligned ? ' · ALIGNED' : ` · turn ${rel > 0 ? 'right' : 'left'} ${Math.abs(Math.round(rel))}°`}`;
        qibla.classList.toggle('on', aligned);
        if (aligned && !S.aligned) haptics.success();
        S.aligned = aligned;
      }
      if (S.view === 'scope') drawScope();
      else if (S.view === 'ar') drawAR();
      else if (performance.now() - lastList > 1500) { lastList = performance.now(); renderList(); }
      raf = requestAnimationFrame(frame);
    }

    function drawScope() {
      const dpr = window.devicePixelRatio || 1, W = scope.clientWidth;
      if (!W) return;
      if (scope.width !== W * dpr) { scope.width = W * dpr; scope.height = W * dpr; }
      const g = scope.getContext('2d');
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, W, W);
      const c = W / 2, R = W / 2 - 22, t = performance.now() / 1000, rot = S.heading == null ? 0 : -S.heading;
      const toXY = (bearing, frac) => { const a = ((bearing + rot - 90) * Math.PI) / 180; return [c + Math.cos(a) * R * frac, c + Math.sin(a) * R * frac]; };
      // rings
      g.strokeStyle = 'rgba(58,214,255,.25)'; g.lineWidth = 1;
      for (let i = 1; i <= 4; i++) { g.beginPath(); g.arc(c, c, (R * i) / 4, 0, Math.PI * 2); g.stroke(); }
      g.fillStyle = 'rgba(111,151,166,.9)'; g.font = '10px "Share Tech Mono", monospace'; g.textAlign = 'left';
      for (let i = 1; i <= 4; i++) g.fillText(fmtDist((S.range * i) / 4), c + 4, c - (R * i) / 4 + 11);
      // sweep
      const sw = (t * 1.6) % (Math.PI * 2);
      const grad = g.createConicGradient ? g.createConicGradient(sw - 0.9, c, c) : null;
      if (grad) {
        grad.addColorStop(0, 'rgba(58,214,255,0)'); grad.addColorStop(0.14, 'rgba(58,214,255,.28)'); grad.addColorStop(0.1401, 'rgba(58,214,255,0)');
        g.fillStyle = grad; g.beginPath(); g.arc(c, c, R, 0, Math.PI * 2); g.fill();
      }
      // compass rose
      g.textAlign = 'center'; g.textBaseline = 'middle'; g.font = 'bold 12px Orbitron, sans-serif';
      for (const [b, label] of [[0, 'N'], [90, 'E'], [180, 'S'], [270, 'W']]) {
        const [x, y] = toXY(b, 1.09); g.fillStyle = label === 'N' ? '#ff5a6a' : '#9ef3ff'; g.fillText(label, x, y);
      }
      for (let b = 0; b < 360; b += 10) {
        const [x1, y1] = toXY(b, 1), [x2, y2] = toXY(b, b % 30 ? 0.98 : 0.95);
        g.strokeStyle = 'rgba(58,214,255,.55)'; g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.stroke();
      }
      // you
      g.fillStyle = '#9ef3ff'; g.beginPath(); g.moveTo(c, c - 9); g.lineTo(c + 6, c + 7); g.lineTo(c, c + 3); g.lineTo(c - 6, c + 7); g.closePath(); g.fill();
      // places
      blipHits.length = 0;
      const sweepDeg = ((sw * 180) / Math.PI + 90 - rot + 720) % 360;
      let labels = 0;
      const sorted = S.places.map((p) => ({ p, ...live(p) })).filter((x) => x.dist <= S.range).sort((a, b) => a.dist - b.dist);
      for (const { p, dist, bearing } of sorted) {
        const [x, y] = toXY(bearing, Math.min(1, dist / S.range));
        const since = (sweepDeg - bearing + 360) % 360;                 // fades after the sweep passes
        const hl = S.highlight.has(p.name.toLowerCase());
        const alpha = hl ? 1 : 0.35 + 0.65 * Math.max(0, 1 - since / 300);
        g.globalAlpha = alpha; g.fillStyle = COLOR[p.cat] || '#9ef3ff';
        g.beginPath(); g.arc(x, y, hl ? 6 + 2 * Math.sin(t * 6) : 4, 0, Math.PI * 2); g.fill();
        if (labels < 6 || hl) {
          g.font = '600 11px Rajdhani, sans-serif'; g.textAlign = 'left'; g.fillText(p.name.slice(0, 18), x + 7, y - 6); labels++;
        }
        g.globalAlpha = 1;
        blipHits.push({ x, y, p });
      }
      // sky + Qibla markers on the rim
      if (S.pos) {
        const now = new Date();
        const sun = sunPosition(now, S.pos.lat, S.pos.lon), moon = moonPosition(now, S.pos.lat, S.pos.lon);
        const mark = (bearing, color, label, faded) => {
          const [x, y] = toXY(bearing, 0.9);
          g.globalAlpha = faded ? 0.4 : 1; g.fillStyle = color; g.beginPath(); g.arc(x, y, 7, 0, Math.PI * 2); g.fill();
          g.fillStyle = '#02121c'; g.font = 'bold 8px Orbitron, sans-serif'; g.textAlign = 'center'; g.fillText(label, x, y + 0.5); g.globalAlpha = 1;
        };
        mark(sun.az, '#ffb547', '☀', sun.alt < 0);
        mark(moon.az, '#d6f6ff', '☾', moon.alt < 0);
        if (S.qibla) {
          const [x, y] = toXY(S.qibla.bearing, 1);
          g.strokeStyle = S.aligned ? '#4ef0a3' : 'rgba(78,240,163,.6)'; g.lineWidth = S.aligned ? 3 : 1.5; g.setLineDash([4, 4]);
          g.beginPath(); g.moveTo(c, c); g.lineTo(x, y); g.stroke(); g.setLineDash([]);
          g.fillStyle = '#4ef0a3'; g.font = 'bold 10px Orbitron, sans-serif'; g.textAlign = 'center'; g.fillText('◆ QIBLA', x, y - 10);
        }
      }
    }
    scope.addEventListener('click', (e) => {
      const r = scope.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
      let best = null, bd = 24;
      for (const b of blipHits) { const d = Math.hypot(b.x - x, b.y - y); if (d < bd) { bd = d; best = b; } }
      if (best) placeSheet(best.p);
    });

    const arNodes = new Map();
    function drawAR() {
      const W = arWrap.clientWidth, H = arWrap.clientHeight;
      if (!W || S.heading == null) return;
      const pxPerDeg = W / FOV, pitch = S.beta - 90;            // camera tilt above the horizon
      const horizonY = H / 2 + pitch * pxPerDeg;
      arWrap.style.setProperty('--hy', `${horizonY}px`);
      const seen = new Set();
      const items = S.places.map((p) => ({ p, ...live(p) })).filter((x) => x.dist <= S.range).sort((a, b) => a.dist - b.dist).slice(0, 40);
      if (S.pos) {
        const now = new Date(), sun = sunPosition(now, S.pos.lat, S.pos.lon), moon = moonPosition(now, S.pos.lat, S.pos.lon);
        items.push({ p: { name: '☀ SUN', cat: 'sky', key: 'sun' }, bearing: sun.az, alt: sun.alt });
        items.push({ p: { name: `☾ MOON ${Math.round(moon.lit * 100)}%`, cat: 'sky', key: 'moon' }, bearing: moon.az, alt: moon.alt });
        if (S.qibla) items.push({ p: { name: '◆ QIBLA', cat: 'qibla', key: 'qibla' }, bearing: S.qibla.bearing, dist: S.qibla.dist, alt: 0 });
      }
      items.forEach((it, i) => {
        const rel = ((it.bearing - S.heading + 540) % 360) - 180;
        const key = it.p.key || `${it.p.name}|${it.p.lat}`;
        let node = arNodes.get(key);
        if (Math.abs(rel) > FOV / 2 + 8) { if (node) node.hidden = true; return; }
        if (!node) {
          node = h('button', { class: `nx-ar-tag ${it.p.cat}`, style: `--c:${COLOR[it.p.cat] || ''}` }, el('b'), el('span'));
          if (it.p.lat) node.onclick = () => placeSheet(it.p);
          arLayer.append(node); arNodes.set(key, node);
        }
        node.hidden = false; seen.add(key);
        node.firstChild.textContent = it.p.name;
        node.lastChild.textContent = it.dist != null ? fmtDist(it.dist) : `${Math.round(it.alt)}° up`;
        const x = W / 2 + rel * pxPerDeg;
        const y = it.alt != null && it.p.cat === 'sky' ? horizonY - it.alt * pxPerDeg : horizonY - 30 - (i % 6) * 30 - Math.max(0, 1 - it.dist / S.range) * 40;
        node.style.transform = `translate(${x}px, ${y}px) translate(-50%, -100%) scale(${it.dist != null ? Math.max(0.7, 1.15 - it.dist / S.range / 2) : 1})`;
        node.classList.toggle('hl', S.highlight.has(String(it.p.name).toLowerCase()));
      });
      for (const [k, n] of arNodes) if (!seen.has(k)) n.hidden = true;
    }

    raf = requestAnimationFrame(frame);
    setView(S.view);
    const release = keepAwake();
    onCleanup(() => { S.alive = false; cancelAnimationFrame(raf); stopGps(); stopCompass(); stopStream(S.stream); release(); audio.stop(); });
  });
}
