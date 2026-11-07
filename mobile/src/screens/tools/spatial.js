// SPATIAL (SYS-09) - AURA's universal 3D workshop. "Bring me anything": AURA understands the request, searches every source
// (spatial.py on the PC), picks the best honest representation and loads it here. Every model can be taken apart; every piece
// can be grabbed, pulled out, flung away, called back, inspected or identified by AI. Your hands (front camera) work on it
// directly, Tony Stark style; words and touch work too. Every change is reversible: UNDO, the ✕ on each change, RESTORE ALL.
import { h, el, icon, toast } from '../../ui/components.js';
import * as haptics from '../../platform/haptics.js';
import * as audio from '../../platform/audio.js';
import { stage, micError, say } from './nexus/common.js';
import { Viewer } from '../../spatial/engine.js';
import { parseLocal } from '../../spatial/local.js';
import { startHands, report } from '../../spatial/gestures.js';

const IDEAS = ['a human heart', 'a V8 engine', 'the structure of DNA', 'a cat skeleton', 'caffeine', 'a mechanical watch movement', 'a BJJ gi',
  'the Apollo 11 command module', 'a human knee joint', 'a Klein bottle', 'a turbofan engine', 'hemoglobin', 'a medieval sword', 'a satellite',
  'a lithium-ion battery cell', 'a rose', 'a human brain', 'a camera lens', 'the Wright Flyer', 'a USB-C connector', 'a bacterium', 'a radial aircraft engine'];
const LEVEL_TONE = { 1: 'real', 2: 'real', 3: 'real', 4: 'gen', 5: 'ai', 6: 'flat', 7: 'none' };
const KIND_LABEL = { model: 'REAL MODEL', procedural: 'AI-BUILT', molecule: 'MOLECULE', macromolecule: 'STRUCTURE', image: '2D PICTURE', embed: 'EXTERNAL VIEWER', none: 'UNAVAILABLE' };
// operations that change the scene get an undo checkpoint first
const CHANGES = new Set(['highlight', 'unhighlight', 'hide', 'show', 'isolate', 'unisolate', 'showall', 'opacity', 'xray', 'wireframe', 'color', 'explode', 'section',
  'rotate', 'spin', 'animate', 'label', 'annotate', 'measure', 'duplicate', 'disassemble', 'reassemble', 'move', 'inspect', 'hologram']);
const store = { get(k, d) { try { const v = localStorage.getItem('aura.sp.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('aura.sp.' + k, JSON.stringify(v)); } catch (e) { /* private mode */ } } };

let live = null;

export function open(ctx, arg = {}) {
  if (live && live.alive) { live.handle(arg || {}); return; }
  stage('SPATIAL', 'SYS-09', (body, page, onCleanup) => {
    const S = { alive: true, viewer: null, scenes: [], job: null, poll: 0, hands: null, explainMode: false };
    live = { get alive() { return S.alive; }, handle };
    window.__auraSpatial = S;                     // inspectable from the browser console
    onCleanup(() => {
      S.alive = false; clearTimeout(S.poll); live = null;
      if (S.job) ctx.api.spatial('cancel', { job: S.job }).catch(() => {});
      S.hands?.stop(); S.viewer?.dispose();
      if (arg?.standalone) setTimeout(() => window.close(), 50);
    });

    // ---------------------------------------------------------------- layout
    const view = h('div', { class: 'sp-view' });
    const handCanvas = h('canvas', { class: 'sp-handlayer' });
    const embed = h('iframe', { class: 'sp-embed', hidden: true, title: 'External 3D viewer', allow: 'autoplay; fullscreen; xr-spatial-tracking', sandbox: 'allow-scripts allow-same-origin allow-popups' });
    const title = el('div', 'sp-name');
    const badge = h('div', { class: 'sp-badge' });
    const hudTop = h('div', { class: 'sp-top', hidden: true }, title, badge);
    const reply = h('div', { class: 'sp-reply', hidden: true });
    const selCard = h('div', { class: 'sp-sel', hidden: true });
    const sheet = h('div', { class: 'sp-sheet', hidden: true });
    const loadingBox = h('div', { class: 'sp-loading', hidden: true });
    const launcher = h('div', { class: 'sp-launch' });
    const stateBar = h('div', { class: 'sp-state', hidden: true });
    const rail = h('div', { class: 'sp-rail', hidden: true });
    const hoverTag = h('div', { class: 'sp-hovertag', hidden: true });
    const input = h('input', { class: 'sp-input', placeholder: 'Bring me anything…', enterkeyhint: 'go', autocomplete: 'off', 'aria-label': 'Ask or command' });
    const micBtn = h('button', { class: 'sp-ic', type: 'button', 'aria-label': 'Speak' }, icon('mic'));
    const handBtn = h('button', { class: 'sp-ic', type: 'button', 'aria-label': 'Hand control' }, icon('hand'));
    const partsBtn = h('button', { class: 'sp-ic', type: 'button', 'aria-label': 'Parts', disabled: true }, icon('layers'));
    const toolsBtn = h('button', { class: 'sp-ic', type: 'button', 'aria-label': 'Tools', disabled: true }, icon('sliders'));
    const dock = h('form', { class: 'sp-dock' }, micBtn, input, handBtn, partsBtn, toolsBtn);
    const panel = h('div', { class: 'sp-panel', hidden: true });
    // hands: your camera fills the screen behind the model (mirrored) so you see exactly where your hands are
    const handVideo = h('video', { playsinline: true, muted: true, autoplay: true, class: 'sp-camfeed', hidden: true });
    const handLabel = el('div', 'sp-hand-gesture', '');
    const handDiag = el('div', 'sp-hand-diag', '');
    const handGuide = h('div', { class: 'sp-hand-guide' },
      ...[['✋', 'HOVER', 'point at a piece'], ['✋…', 'HOLD STILL', 'select it'], ['🤏', 'PINCH ON PIECE', 'grab & move it'], ['🤏💨', 'PINCH + FLICK', 'throw it away'],
        ['🤏', 'PINCH ON EMPTY', 'rotate'], ['✊', 'FIST', 'move view'], ['🤏🤏', 'TWO PINCHES', 'zoom / twist'], ['✋✋', 'PALMS APART', 'explode']]
        .map(([g, t, d]) => h('div', {}, el('b', null, g), el('span', null, t), el('i', null, d))));
    const handsBox = h('div', { class: 'sp-handstatus', hidden: true }, handDiag, handLabel, handGuide);
    const cursor = h('div', { class: 'sp-cursor', hidden: true }, h('i'));
    const root = h('div', { class: 'sp' }, handVideo, view, handCanvas, embed, launcher, hudTop, rail, reply, stateBar, selCard, loadingBox, sheet, panel, cursor, hoverTag, handsBox, dock);
    body.append(root);

    // ---------------------------------------------------------------- launcher (nothing loaded yet)
    function showLauncher() {
      const picks = [...IDEAS].sort(() => Math.random() - 0.5).slice(0, 6);
      const hist = h('div', { class: 'sp-hist' });
      launcher.replaceChildren(
        h('div', { class: 'sp-orb' }, h('i'), h('i'), h('i')),
        el('div', 'sp-launch-t', 'BRING ME ANYTHING'),
        el('div', 'sp-launch-s', 'Describe anything - a machine, an organ, a molecule, a building, something you imagined. I find it, build it or generate it. Then take it apart with your hands, your voice or a touch.'),
        h('div', { class: 'sp-chips' }, picks.map((q) => { const b = h('button', { class: 'sp-chip', type: 'button' }, q); b.onclick = () => ask('Bring up ' + q); return b; })),
        hist);
      ctx.api.spatial('history').then((r) => {
        const items = (r.items || []).slice(0, 8);
        if (!items.length || !S.alive) return;
        hist.append(el('div', 'sp-label', 'RECENT'), h('div', { class: 'sp-chips' }, items.map((it) => {
          const b = h('button', { class: 'sp-chip ghost', type: 'button' }, it.name); b.onclick = () => ask(it.query); return b;
        })));
      }).catch(() => {});
      launcher.hidden = false;
    }

    // ---------------------------------------------------------------- talking to it
    dock.onsubmit = (e) => { e.preventDefault(); const t = input.value.trim(); if (t) { input.value = ''; input.blur(); ask(t); } };
    let rec = null;
    micBtn.onclick = async () => {
      if (rec) { rec.stop(); return; }
      try { rec = await audio.record({ maxSeconds: 15 }); } catch (e) { micError(e); return; }
      micBtn.classList.add('on'); haptics.tap();
      const blob = await rec.done; rec = null; micBtn.classList.remove('on');
      if (!blob) return;
      try { const r = await ctx.api.transcribe(blob); if (r.text) ask(r.text); } catch (e) { toast('Spatial', e.message, 'error'); }
    };

    function speak(text) {
      if (!text) return;
      reply.textContent = text; reply.hidden = false;
      clearTimeout(reply._t); reply._t = setTimeout(() => { reply.hidden = true; }, 7000);
      say(ctx, text);
    }

    const loaded = () => !!(S.viewer && S.viewer.objects.length) || !embed.hidden;
    const isLoadRequest = (t) => /^(bring( me)?( up)?|show me (a|an)|display|visuali[sz]e|load|open|give me|let me see|i want to see|can i see|pull up|get me)\b/i.test(t.trim());

    async function ask(text) {
      text = String(text || '').trim();
      if (!text) return;
      const has = loaded();
      const local = has ? parseLocal(text, { hasSel: !!S.viewer?.state.selection, loaded: true }) : null;
      if (local) { await runOps(local); return; }
      if (!has) { resolve(text); return; }
      try {
        dock.classList.add('thinking');
        const r = await ctx.api.spatial('command', { text, context: S.viewer ? S.viewer.context() : { subject: S.scenes[0]?.display_name, kind: 'embed' } });
        dock.classList.remove('thinking');
        if (r.error) throw new Error(r.error);
        await runOps(r.ops || [], r.reply);
      } catch (e) {
        dock.classList.remove('thinking');
        if (isLoadRequest(text)) resolve(text); else toast('Spatial', e.message, 'error');
      }
    }

    // ---------------------------------------------------------------- operations from words / buttons / gestures
    async function runOps(ops, replyText) {
      const v = S.viewer;
      const said = [];
      if (v && ops.some((o) => CHANGES.has(o.op))) v.checkpoint();
      for (const o of ops) {
        const t = o.targets && o.targets.length ? o.targets : null;
        try {
          switch (o.op) {
            case 'load': resolve(o.query, { mode: o.mode === 'add' ? 'add' : 'replace' }); break;
            case 'select': if (v) { if (!t) v.select(null); else if (!v.select(t[0])) said.push(`I can't find "${t[0]}" in this model.`); } break;
            case 'highlight': if (v && !v.highlight(t || [], o.color || '#ffb547').length) said.push(`No part called "${(t || [])[0]}" here.`); break;
            case 'unhighlight': v?.unhighlight(); break;
            case 'hide': v?.hide(t); break;
            case 'show': { const shown = v?.show(t || []); if (v && !t && !shown.length && v.state.isolated) v.unisolate(); break; }
            case 'isolate': if (v && !v.isolate(t).length) said.push("I couldn't find that part to isolate."); break;
            case 'unisolate': v?.unisolate(); break;
            case 'showall': v?.showAll(); break;
            case 'opacity': v?.opacity(t, +o.value); break;
            case 'xray': v?.xray(o.on !== false); break;
            case 'wireframe': v?.wireframe(o.on !== false); break;
            case 'color': v?.color(t || [], o.color); break;
            case 'explode': if (v) { if (+o.amount > 0 && !t) await ensureParts(); v.explode(+o.amount, { targets: t }); } break;
            case 'disassemble': if (v) { await ensureParts(); if (v.parts.length < 2) said.push('This one is a single piece.'); else if (o.step === false) v.explode(1); else v.sequence(true); } break;
            case 'dissect': if (v) await ensureParts(true); break;
            case 'reassemble': if (v) { if (o.step && !t) v.sequence(false); else v.reassemble(t); } break;
            case 'move': if (v && !v.movePart(t || ['it'], o.direction || 'out', +o.amount || 0.4).length) said.push("I couldn't find that piece."); break;
            case 'section': v?.section(o.axis || null, o.value == null ? null : +o.value); break;
            case 'rotate': v?.rotate(o.axis || 'y', +o.degrees || 90); break;
            case 'spin': v?.spin(o.on !== false); break;
            case 'view': v?.view(o.preset || 'iso'); break;
            case 'zoom': v?.zoom(+o.factor || 1.5); break;
            case 'focus': if (v && !v.focus(o.target || o.targets || 'it').length) said.push("I couldn't find that part."); break;
            case 'inspect': if (v && !v.inspect(o.target || (t && t[0]) || 'it')) said.push('Select a piece to inspect first.'); break;
            case 'hologram': v?.holo(o.on !== false, { record: false }); break;
            case 'undo': if (!v?.undo()) said.push('Nothing to undo.'); break;
            case 'restore': case 'reset': v?.restoreAll(); break;
            case 'identify': identify(); break;
            case 'animate': {
              if (!v) break;
              const list = v.animate(o.name, o.on !== false);
              if (o.on !== false && !list.length) { v.spin(true); said.push('This model has no motion of its own - spinning it so you can see all sides.'); }
              else if (o.on !== false && list.some((a) => !a.authored)) said.push('Playing an illustrative animation (made by AI, not scientifically exact).');
              break;
            }
            case 'label': v?.labels(o.on !== false); break;
            case 'annotate': v?.annotate(o.target || 'it', o.text); break;
            case 'measure': { const m = v?.measure(o.a, o.b); if (m) said.push(`About ${m.distance < 10 ? m.distance.toFixed(2) : Math.round(m.distance)} ${m.units} apart.`); break; }
            case 'duplicate': v?.duplicate(o.target || 'it'); break;
            case 'explain': explain(o.target || null); break;
            case 'switch': switchTo(o.to); break;
            case 'remove': v?.removeObject(+o.object || 0); break;
            case 'fullscreen': toggleFullscreen(); break;
            default: break;
          }
        } catch (e) { console.warn('op failed', o, e); }
      }
      refreshUi();
      const text = [...new Set([replyText, ...said].filter(Boolean))].join(' ');
      if (text) speak(text);
    }

    // ---------------------------------------------------------------- resolving (search -> evaluate -> fetch -> load)
    async function resolve(query, { mode = 'replace', choice = null, prefer = null, quiet = false } = {}) {
      if (S.job) ctx.api.spatial('cancel', { job: S.job }).catch(() => {});
      launcher.hidden = true;
      if (mode === 'inside') query = 'Building the inside: ' + query;
      showLoading(query, ['Understanding request…']);
      let r;
      try {
        r = await ctx.api.spatial('resolve', { query, choice, prefer, context: S.viewer?.context() });
        if (r.error) throw new Error(r.error);
      } catch (e) { hideLoading(); if (!loaded()) showLauncher(); toast("Couldn't start", e.message, 'error'); return; }
      const jid = S.job = r.job;
      const tick = async () => {
        if (!S.alive || S.job !== jid) return;
        let j;
        try { j = await ctx.api.spatial(`job?id=${jid}`); } catch (e) { S.poll = setTimeout(tick, 1500); return; }
        if (j.state === 'working') { showLoading(query, j.steps || [j.step]); S.poll = setTimeout(tick, 700); return; }
        S.job = null;
        if (j.state === 'done') await present(j.scene, mode);
        else if (j.state !== 'cancelled') { hideLoading(); if (!loaded()) showLauncher(); toast("Couldn't bring that up", j.error || 'Something went wrong.', 'error', 8000); }
      };
      tick();
    }

    function showLoading(query, steps) {
      loadingBox.hidden = false;
      const list = h('div', { class: 'sp-steps' }, steps.filter(Boolean).map((s, i) => h('div', { class: 'sp-step' + (i === steps.length - 1 ? ' now' : ' done') }, h('i'), s)));
      const cancel = h('button', { class: 'sp-link', type: 'button' }, 'CANCEL');
      cancel.onclick = () => { if (S.job) ctx.api.spatial('cancel', { job: S.job }).catch(() => {}); S.job = null; hideLoading(); if (!loaded()) showLauncher(); };
      loadingBox.replaceChildren(h('div', { class: 'sp-orb small' }, h('i'), h('i'), h('i')), el('div', 'sp-load-q', query), list, cancel);
    }
    function hideLoading() { loadingBox.hidden = true; }

    async function present(scene, mode) {
      if (scene.kind === 'none') {
        hideLoading();
        if (!loaded()) showLauncher();
        speak(scene.message);
        showUnavailable(scene);
        return;
      }
      if (scene.kind === 'embed') {
        hideLoading();
        S.viewer?.clear();
        embed.src = scene.embed; embed.hidden = false;
        S.scenes = [scene];
        setHud(scene); speak(scene.message); refreshUi();
        return;
      }
      embed.hidden = true; embed.src = 'about:blank';
      try {
        showLoading(scene.display_name, ['Preparing 3D environment…']);
        if (!S.viewer) {
          S.viewer = await Viewer.create(view, {
            onSelect, onChange: () => refreshUi(true), onHover,
            onThrow: (p) => { haptics.tap(); speak(`${p.name} set aside. Say "bring it back" or tap HIDDEN to return it.`); },
            onGrab: (p, on) => { root.classList.toggle('grabbing', !!on); if (!on) refreshUi(); },
          });
        }
        let lastPct = -1;
        const insideOf = mode === 'inside' ? mainObj()?.index : null;
        if (mode === 'inside' && insideOf == null) { hideLoading(); return; }
        await S.viewer.add(scene, {
          mode: mode === 'inside' ? 'add' : mode, inside: insideOf, onProgress: (k) => {
            const pct = Math.round(k * 100);
            if (pct !== lastPct && pct % 5 === 0) { lastPct = pct; showLoading(scene.display_name, ['Preparing 3D environment…', `Loading… ${pct}%`]); }
          },
        });
        if (mode === 'inside') scene.inside = true;
        if (mode === 'replace') S.scenes = [scene]; else S.scenes.push(scene);
        hideLoading();
        if (mode === 'inside') {
          const innerIdx = S.viewer.objects.at(-1).index;
          const shells = S.viewer.parts.filter((p) => p.obj === innerIdx && /(outline|silhouette|shell|skin|envelope|casing|body outline|outer)/i.test(p.name));
          if (shells.length) S.viewer.hide(shells.map((p) => p.name));      // the real model is the shell
          S.viewer.ghost(insideOf, 0.25);
          const n = S.viewer.parts.filter((p) => p.obj === S.viewer.objects.at(-1).index).length;
          speak(`Built the inside: ${n} parts (${scene.display_name}) - AI-built and approximate, fitted inside the real model. Explode to see them.`);
          partsBtn.disabled = toolsBtn.disabled = false; refreshUi();
          if (S.afterInside) { const f = S.afterInside; S.afterInside = null; f(); }
          return;
        }
        setHud(scene);
        const obj = S.viewer.objects.at(-1);
        const pieces = S.viewer.parts.filter((p) => p.obj === obj.index).length;
        const extra = obj.split?.method === 'islands' ? ` I split it into its ${pieces} real pieces - tap EXPLODE or grab one.`
          : obj.split?.method === 'sections' ? ` It's one solid scan, so I cut it into ${pieces} sections you can take apart.` : pieces > 1 ? ` ${pieces} pieces - tap EXPLODE or grab one.` : '';
        speak(scene.message + extra);
        if (obj.triangles > 3_000_000) toast('Heavy model', 'This model is very detailed - it may run slowly on this device.', 'info', 5000);
        partsBtn.disabled = toolsBtn.disabled = false;
        refreshUi();
        if (store.get('hands', !!arg?.standalone) && !S.hands) startHandControl(true);
      } catch (e) {
        console.error(e);
        hideLoading();
        if (!loaded()) showLauncher();
        toast("Couldn't display it", (e && e.message) || String(e), 'error', 8000);
      }
    }

    function setHud(scene) {
      hudTop.hidden = false;
      const mains = S.scenes.filter((x) => !x.inside);
      title.textContent = mains.length > 1 ? mains.map((x) => x.display_name).join('  vs  ') : (mains[0] || scene).display_name;
      const s = scene.source || {};
      badge.replaceChildren(
        h('span', { class: 'sp-lvl ' + (LEVEL_TONE[scene.level] || '') }, `L${scene.level} · ${KIND_LABEL[scene.kind] || scene.kind}`),
        el('span', 'sp-src', [s.label, s.author && s.author !== s.label ? `by ${s.author}` : null, s.license].filter(Boolean).join(' · ')));
      if (s.url) badge.append(h('a', { href: s.url, target: '_blank', rel: 'noopener noreferrer', class: 'sp-srclink' }, 'SOURCE ↗'));
      if (s.note) badge.append(el('div', 'sp-note-line', s.note));
      page.setTitle(S.scenes.length > 1 ? 'SPATIAL · COMPARE' : 'SPATIAL');
    }

    function showUnavailable(scene) {
      sheet.hidden = false;
      sheet.replaceChildren(h('div', { class: 'sp-sheet-head' }, el('b', null, 'NOT AVAILABLE'), closeBtn(() => { sheet.hidden = true; })),
        el('p', null, scene.message), ...(scene.tried || []).map((t) => el('div', 'sp-tried', `${t.source}: ${t.error}`)), altList(scene));
    }

    // ---------------------------------------------------------------- the always-visible controls: state bar, rail, selection card
    const op = (ops) => runOps(ops);
    function refreshUi(soft) {
      const v = S.viewer, has = !!(v && v.objects.length);
      rail.hidden = !has;
      if (has) renderRail();
      renderState();
      if (v?.state.selection) renderSel(v.byName(v.state.selection)); else selCard.hidden = true;
      refreshPanels(soft);
    }

    function renderState() {
      const v = S.viewer;
      const ch = v ? v.changes() : [];
      stateBar.hidden = !ch.length && !(v && v.canUndo());
      if (stateBar.hidden) return;
      const undo = h('button', { class: 'sp-undo', type: 'button', disabled: !v.canUndo() }, '↶ UNDO');
      undo.onclick = () => { haptics.tap(); v.undo(); refreshUi(); };
      const chips = ch.map((c) => {
        const b = h('button', { class: 'sp-chg', type: 'button', title: 'Undo this' }, c.label, h('i', {}, '✕'));
        b.onclick = () => { haptics.tap(); v.clearChange(c.id); refreshUi(); };
        return b;
      });
      const all = ch.length ? h('button', { class: 'sp-undo all', type: 'button' }, '⟲ RESTORE ALL') : null;
      if (all) all.onclick = () => { haptics.success(); v.restoreAll(); refreshUi(); };
      stateBar.replaceChildren(undo, ...chips, all);
    }

    function railBtn(label, glyph, fn, on) {
      const b = h('button', { class: 'sp-rb' + (on ? ' on' : ''), type: 'button', 'aria-label': label }, h('span', { class: 'sp-rb-g' }, glyph), h('span', { class: 'sp-rb-t' }, label));
      b.onclick = () => { haptics.tap(); fn(); };
      return b;
    }
    function renderRail() {
      const v = S.viewer, s = v.state;
      const exploded = s.explode > 0.01 || v.parts.some((p) => p.ex > 0.01 || p.off.lengthSq() > 1e-6);
      rail.replaceChildren(
        railBtn(exploded ? 'ASSEMBLE' : 'EXPLODE', exploded ? '◎' : '✳', () => op(exploded ? [{ op: 'reassemble', step: false }] : [{ op: 'explode', amount: 0.7 }]), exploded),
        railBtn(exploded ? 'STEP BACK' : 'STEP APART', '⋯', () => op([exploded ? { op: 'reassemble', step: true } : { op: 'disassemble', step: true }])),
        railBtn('HOLOGRAM', '◈', () => op([{ op: 'hologram', on: !s.holo }]), s.holo),
        railBtn('X-RAY', '◐', () => op([{ op: 'xray', on: !s.xray }]), s.xray),
        railBtn('DISSECT', '✂', () => op([{ op: 'dissect' }]), !!mainObj()?.dissected),
        railBtn('AI SCAN', '✦', identify),
        railBtn('HANDS', '✋', () => handBtn.click(), !!S.hands),
        ...(S.hands ? [railBtn('CAMERA', '◉', () => { const off = !handVideo.hidden; handVideo.hidden = off; root.classList.toggle('camera', !off); store.set('camera', !off); renderRail(); }, !handVideo.hidden)] : []),
      );
    }

    function renderSel(part) {
      if (!part) { selCard.hidden = true; return; }
      const v = S.viewer, s = v.state;
      const hidden = v.isHidden(part), isolated = !!s.isolated, moved = part.off.lengthSq() > 1e-6 || part.ex > 0.01;
      const btn = (label, fn, cls = '') => { const b = h('button', { class: 'sp-mini ' + cls, type: 'button' }, label); b.onclick = () => { haptics.tap(); fn(); }; return b; };
      const P = [part.name];
      selCard.replaceChildren(
        h('div', { class: 'sp-sel-h' }, el('b', null, (part.ai ? '✦ ' : '') + part.name), part.group ? el('span', null, part.group) : null, closeBtn(() => { v.select(null); })),
        part.desc ? el('div', 'sp-sel-d', part.desc + (part.ai ? ' (named by AI - may be wrong)' : '')) : null,
        h('div', { class: 'sp-sel-b' },
          btn('EXPLAIN', () => explain(part.name)),
          btn('INSPECT', () => op([{ op: 'inspect', target: part.name }])),
          moved ? btn('PUT BACK', () => op([{ op: 'reassemble', targets: P }]), 'warn') : btn('PULL OUT', () => op([{ op: 'move', targets: P, direction: 'out', amount: 0.5 }])),
          hidden ? btn('SHOW', () => op([{ op: 'show', targets: P }]), 'warn') : btn('HIDE', () => op([{ op: 'hide', targets: P }])),
          isolated ? btn('EXIT ISOLATE', () => op([{ op: 'unisolate' }]), 'warn') : btn('ISOLATE', () => op([{ op: 'isolate', targets: P }])),
          s.xray ? btn('SOLID', () => op([{ op: 'xray', on: false }]), 'warn') : btn('X-RAY OTHERS', () => op([{ op: 'xray', on: true }])),
          btn('COLOUR', () => op([{ op: 'color', targets: P, color: ['#ff5a6a', '#ffb547', '#4ef0a3', '#a78bfa', '#60a5fa'][Math.floor(Math.random() * 5)] }]))),
        el('div', 'sp-hint', 'Drag it (or pinch it with your hand) to pull it out · flick to throw it aside'));
      selCard.hidden = false;
    }

    // ---------------------------------------------------------------- selection + hover + explanations
    function onSelect(part, from) {
      if (!part) { selCard.hidden = true; return; }
      haptics.tap();
      renderSel(part);
      if (S.explainMode && from !== 'explain') explain(part.name);
    }
    function onHover(part) {
      if (!part) { hoverTag.hidden = true; return; }
      hoverTag.textContent = (part.ai ? '✦ ' : '') + part.name;
      hoverTag.hidden = false;
    }
    root.addEventListener('pointermove', (e) => { if (!hoverTag.hidden && !S.hands) hoverTag.style.transform = `translate(${e.clientX + 16}px, ${e.clientY + 14}px)`; });

    async function explain(target) {
      sheet.hidden = false;
      sheet.replaceChildren(h('div', { class: 'sp-sheet-head' }, el('b', null, target || "WHAT YOU'RE LOOKING AT"), closeBtn(() => { sheet.hidden = true; })), h('div', { class: 'loading' }, el('span', 'spinner'), 'Looking it up…'));
      try {
        const r = await ctx.api.spatial('explain', { target, context: S.viewer ? S.viewer.context() : { subject: S.scenes[0]?.display_name } });
        if (r.error) throw new Error(r.error);
        const ai = r.ai || {};
        const kids = [h('div', { class: 'sp-sheet-head' }, el('b', null, ai.name || target || r.subject), closeBtn(() => { sheet.hidden = true; }))];
        if (r.source && !r.source.mismatch) {
          kids.push(h('div', { class: 'sp-facts' }, el('div', 'sp-label', 'FACTUAL SOURCE · ' + r.source.source.toUpperCase()), el('p', null, r.source.text),
            r.source.url ? h('a', { href: r.source.url, target: '_blank', rel: 'noopener noreferrer', class: 'sp-srclink' }, `${r.source.title} ↗`) : null));
        }
        const aiBox = h('div', { class: 'sp-ai' }, el('div', 'sp-label', 'AI EXPLANATION · may contain mistakes'));
        for (const [k, label] of [['what', 'WHAT IT IS'], ['function', 'WHAT IT DOES'], ['connections', 'CONNECTS TO']]) if (ai[k]) aiBox.append(el('div', 'sp-k', label), el('p', null, ai[k]));
        if (Array.isArray(ai.facts) && ai.facts.length) aiBox.append(el('div', 'sp-k', 'KEY POINTS'), h('ul', {}, ai.facts.map((f) => el('li', null, f))));
        if (ai.caution) aiBox.append(el('div', 'sp-caution', ai.caution));
        kids.push(aiBox);
        sheet.replaceChildren(...kids);
        if (ai.what) say(ctx, `${ai.name || target || ''}. ${ai.what}`);
      } catch (e) {
        sheet.replaceChildren(h('div', { class: 'sp-sheet-head' }, el('b', null, 'EXPLAIN'), closeBtn(() => { sheet.hidden = true; })), el('div', 'err', e.message));
      }
    }

    function mainObj() { const v = S.viewer; return v ? [...v.objects].reverse().find((o) => o.insideOf == null) : null; }

    /** Before taking something apart: make sure it's made of REAL parts. A one-piece model (a scan, a skin, a garment) gets
     *  AI-DISSECTED - the vision AI marks its real parts on a front view and the real geometry is cut along them - and if the real
     *  thing has an inside (organs, skeleton, engine internals) AURA builds it and fits it inside. */
    let dissecting = null;
    async function ensureParts(force = false) {
      const v = S.viewer, o = mainObj();
      if (!v || !o || o.scene.kind !== 'model') return;
      const st = v.structure(o.index);
      if (!force && (st.dissected || st.named >= 8)) return;
      if (st.dissected && force) { speak('Already dissected - explode it, or tap AI SCAN to rename pieces.'); return; }
      if (dissecting) return dissecting;
      dissecting = (async () => {
        speak(st.named >= 8 ? 'Dissecting it further…' : `This model is ${st.total <= 4 ? 'one solid piece' : 'only rough chunks'} - dissecting it into its real parts…`);
        root.classList.add('scanning');
        try {
          v.checkpoint();
          const shot = v.frontShot(o.index);
          const r = await ctx.api.spatial('dissect', { image: shot.image, subject: o.scene.display_name, context: v.context() });
          if (r.error) throw new Error(r.error);
          const n = r.parts?.length ? v.dissectWith(o.index, r.parts, shot.cam) : 0;
          if (n) speak(`Cut it into ${n} real parts: ${r.parts.slice(0, 6).map((p) => p.name).join(', ')}${r.parts.length > 6 ? '…' : ''}.`);
          else speak("I couldn't find separate parts on it.");
          if (r.has_internals && r.internal_query && !v.objects.some((x) => x.insideOf === o.index)) {
            speak(`Building what's inside: ${r.internal_query}…`);
            const u = { ...(o.scene.understanding || {}), subject: r.internal_query, display_name: 'Inside: ' + r.internal_query, aspect: 'internal', needs_parts: true, expected_parts: [] };
            await new Promise((done) => { S.afterInside = done; resolve(r.internal_query, { mode: 'inside', choice: { provider: 'procedural', understanding: u } }); setTimeout(done, 90000); });
          }
          refreshUi();
        } catch (e) { toast('Dissect', e.message, 'error'); }
        root.classList.remove('scanning');
        dissecting = null;
      })();
      return dissecting;
    }

    /** AI SCAN: explode it, photograph it with every piece numbered, and let AURA's vision AI name the pieces. */
    async function identify() {
      const v = S.viewer;
      if (!v || !v.parts.length) return;
      if (v.parts.length < 2) { speak('This is a single piece - nothing to identify separately.'); return; }
      speak('Scanning the pieces…');
      root.classList.add('scanning');
      if (v.state.explode < 0.5) { v.checkpoint(); v.explode(0.7); }
      await new Promise((r) => setTimeout(r, 1300));
      try {
        const shot = v.numberedShot(24);
        const r = await ctx.api.spatial('identify', { image: shot.image, items: shot.items, context: v.context() });
        if (r.error) throw new Error(r.error);
        let n = 0;
        for (const it of r.pieces || []) {
          const item = shot.items.find((x) => x.n === +it.n);
          if (item && it.name && !/unknown/i.test(it.name) && it.confidence !== 'low' && v.rename(item.name, it.name, it.desc)) n++;
        }
        v.labels(true); refreshUi();
        speak(n ? `Identified ${n} pieces. ${r.summary || ''} Names marked ✦ come from AI and may be wrong.` : (r.summary || "I couldn't confidently identify the pieces."));
      } catch (e) { toast('AI scan', e.message, 'error'); }
      root.classList.remove('scanning');
    }

    const closeBtn = (fn) => { const b = h('button', { class: 'sp-x', type: 'button', 'aria-label': 'Close' }, icon('close')); b.onclick = fn; return b; };

    // ---------------------------------------------------------------- alternatives / switching representation
    function altList(scene) {
      const alts = scene?.alternatives || [];
      if (!alts.length) return el('div', 'empty', 'No other representations.');
      return h('div', { class: 'sp-alts' }, alts.map((a) => {
        const b = h('button', { class: 'sp-alt', type: 'button' },
          a.thumb ? h('img', { src: a.thumb, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer' }) : h('span', { class: 'sp-alt-ic' }, icon(a.provider === 'procedural' ? 'layers' : a.provider === 'generated' ? 'bolt' : 'cube')),
          h('span', { class: 'sp-alt-t' }, el('b', null, a.title || a.provider), el('span', null, [a.label, a.author ? `by ${a.author}` : null, a.license].filter(Boolean).join(' · '))));
        b.onclick = () => { panel.hidden = true; sheet.hidden = true; resolve(scene.query, { choice: { ...a, understanding: scene.understanding } }); };
        return b;
      }));
    }
    function switchTo(to) {
      const scene = S.scenes.at(-1);
      if (!scene) return;
      if (to === 'procedural' || to === 'generated') resolve(scene.query, { choice: { provider: to, understanding: scene.understanding } });
      else { const next = (scene.alternatives || [])[0]; if (next) resolve(scene.query, { choice: { ...next, understanding: scene.understanding } }); }
    }

    // ---------------------------------------------------------------- side panels (parts list, tools)
    let panelMode = null;
    partsBtn.onclick = () => openPanel(panelMode === 'parts' ? null : 'parts');
    toolsBtn.onclick = () => openPanel(panelMode === 'tools' ? null : 'tools');
    function openPanel(mode) { panelMode = mode; panel.hidden = !mode; partsBtn.classList.toggle('on', mode === 'parts'); toolsBtn.classList.toggle('on', mode === 'tools'); refreshPanels(); }

    function refreshPanels(soft) {
      if (!panelMode || panel.hidden) return;
      if (soft && panel.contains(document.activeElement) && document.activeElement.tagName === 'INPUT') return;
      if (panelMode === 'parts') renderParts(); else renderTools();
    }

    function renderParts() {
      const v = S.viewer;
      const filter = h('input', { class: 'sp-filter', placeholder: 'Find a part…', autocomplete: 'off' });
      const list = h('div', { class: 'sp-plist' });
      const draw = () => {
        list.innerHTML = '';
        const q = filter.value.trim().toLowerCase();
        const parts = (v?.parts || []).filter((p) => !q || p.name.toLowerCase().includes(q) || p.group.toLowerCase().includes(q));
        if (!parts.length) { list.append(el('div', 'empty', v?.parts.length ? 'No match.' : 'This view has no separate parts.')); return; }
        let grp = null;
        for (const p of parts.slice(0, 500)) {
          if (p.group && p.group !== grp) { grp = p.group; list.append(el('div', 'sp-pgroup', grp)); }
          const hidden = v.isHidden(p);
          const eye = h('button', { class: 'sp-eye' + (hidden ? ' off' : ''), type: 'button', 'aria-label': hidden ? 'Show' : 'Hide' }, icon('eye'));
          eye.onclick = (e) => { e.stopPropagation(); v.checkpoint(); if (hidden) v.show([p.name]); else v.hide([p.name]); refreshUi(); };
          const row = h('div', { class: 'sp-prow' + (v.state.selection === p.name ? ' on' : '') + (hidden ? ' hid' : '') }, el('span', null, (p.ai ? '✦ ' : '') + p.name), eye);
          row.onclick = () => { v.select(p.name, { from: 'list' }); v.focus(p.name); };
          list.append(row);
        }
      };
      filter.oninput = draw;
      const mini2 = (label, fn) => { const b = h('button', { class: 'sp-mini', type: 'button' }, label); b.onclick = () => { haptics.tap(); fn(); refreshUi(); }; return b; };
      panel.replaceChildren(h('div', { class: 'sp-panel-h' }, el('b', null, `PARTS · ${v?.parts.length || 0}`), closeBtn(() => openPanel(null))), filter,
        h('div', { class: 'sp-row' }, mini2('SHOW ALL', () => { v.checkpoint(); v.showAll(); }), mini2('LABELS', () => { v.checkpoint(); v.labels(); }), mini2('AI NAME PIECES', identify)), list);
      draw();
    }

    const mini = (label, fn, on) => { const b = h('button', { class: 'sp-mini' + (on ? ' on' : ''), type: 'button' }, label); b.onclick = () => { haptics.tap(); fn(); refreshUi(); }; return b; };
    function slider(label, value, fn) {
      const r = h('input', { type: 'range', min: 0, max: 100, value: Math.round(value * 100), class: 'sp-range' });
      let marked = false;
      r.onpointerdown = () => { if (!marked) { S.viewer?.checkpoint(); marked = true; } };
      r.onchange = () => { marked = false; refreshUi(); };
      r.oninput = () => fn(r.value / 100);
      return h('label', { class: 'sp-slider' }, el('span', null, label), r);
    }

    function renderTools() {
      const v = S.viewer, s = v?.state || {};
      const scene = S.scenes.at(-1);
      const anims = v ? v.animations() : [];
      const kids = [h('div', { class: 'sp-panel-h' }, el('b', null, 'TOOLS'), closeBtn(() => openPanel(null)))];
      if (v && v.objects.length) {
        kids.push(
          h('div', { class: 'sp-row' }, mini('↶ UNDO', () => v.undo()), mini('⟲ RESTORE ALL', () => v.restoreAll())),
          slider('EXPLODE', s.explode || 0, (k) => v.explode(k, { frame: false })), slider('OPACITY', 1, (k) => v.opacity([], Math.max(0.05, k))),
          el('div', 'sp-label', 'TAKE IT APART'),
          h('div', { class: 'sp-row' }, mini('STEP APART', () => op([{ op: 'disassemble', step: true }])), mini('STEP BACK TOGETHER', () => op([{ op: 'reassemble', step: true }])), mini('AI NAME PIECES', identify)),
          el('div', 'sp-label', 'LOOK'),
          h('div', { class: 'sp-row' }, mini('HOLOGRAM', () => op([{ op: 'hologram', on: !s.holo }]), s.holo), mini('X-RAY', () => op([{ op: 'xray', on: !s.xray }]), s.xray),
            mini('WIREFRAME', () => op([{ op: 'wireframe', on: !s.wireframe }]), s.wireframe), mini('SPIN', () => op([{ op: 'spin', on: !s.spin }]), s.spin), mini('LABELS', () => op([{ op: 'label', on: !s.labels }]), s.labels)),
          el('div', 'sp-label', 'CUTAWAY'),
          h('div', { class: 'sp-row' }, ...['x', 'y', 'z'].map((a) => mini(`CUT ${a.toUpperCase()}`, () => op([{ op: 'section', axis: s.section?.axis === a ? null : a, value: s.section?.axis === a ? null : 0 }]), s.section?.axis === a))),
          s.section ? slider('CUT POSITION', (s.section.value + 1) / 2, (k) => v.section(s.section.axis, k * 2 - 1)) : null,
          el('div', 'sp-label', 'VIEW'),
          h('div', { class: 'sp-row' }, ...['front', 'back', 'left', 'right', 'top', 'iso'].map((p) => mini(p.toUpperCase(), () => v.view(p)))),
          anims.length ? el('div', 'sp-label', 'MOTION') : null,
          anims.length ? h('div', { class: 'sp-row' }, ...anims.map((a) => mini(`${a.name}${a.authored ? '' : ' *'}`, () => op([{ op: 'animate', name: a.name, on: !s.playing.has(`${a.obj}:${a.name}`) }]), s.playing.has(`${a.obj}:${a.name}`)))) : null,
          anims.some((a) => !a.authored) ? el('div', 'sp-hint', '* illustrative motion made by AI - not scientifically exact') : null,
          h('div', { class: 'sp-row' }, mini('EXPLAIN MODE', () => { S.explainMode = !S.explainMode; toast('Explain mode', S.explainMode ? 'On - tap any part to learn about it.' : 'Off', 'info', 2500); }, S.explainMode),
            mini('FULLSCREEN', toggleFullscreen)));
      }
      if (scene) kids.push(el('div', 'sp-label', 'OTHER REPRESENTATIONS'), altList(scene));
      kids.push(el('div', 'sp-label', 'SOURCES'), sourcesBox());
      panel.replaceChildren(...kids.filter(Boolean));
    }

    function sourcesBox() {
      const box = h('div', { class: 'sp-sources' }, h('div', { class: 'loading' }, el('span', 'spinner'), '…'));
      ctx.api.spatial('status').then((st) => {
        const tokenIn = h('input', { class: 'sp-filter', placeholder: 'Sketchfab API token (free account)', autocomplete: 'off', autocapitalize: 'off' });
        const save = mini(st.sketchfab ? 'REPLACE' : 'CONNECT', async () => {
          try { const r = await ctx.api.spatial('settings', { sketchfab_token: tokenIn.value }); if (r.error) throw new Error(r.error); toast('Sketchfab', r.sketchfab ? 'Connected - real models now load directly.' : 'Disconnected.', 'info', 3500); refreshPanels(); } catch (e) { toast('Sketchfab', e.message, 'error'); }
        });
        box.replaceChildren(
          el('div', 'sp-hint', `${st.sketchfab ? '✅' : '⚪'} Sketchfab - millions of real models. ${st.sketchfab ? 'Connected.' : 'Free: sketchfab.com → log in → Settings → Password & API → copy the API token here.'}`),
          h('div', { class: 'sp-row' }, tokenIn, save),
          el('div', 'sp-hint', `${st.hf ? '✅' : '⚪'} AI 3D generation (Hugging Face) ${st.hf ? 'ready.' : '- add your token in Studio → MAGIC AI EDITS.'}`),
          el('div', 'sp-hint', `Always on: ${st.providers.join(', ')}, AI-built models, Wikipedia pictures. Your own models: put .glb / .obj / .stl files in ${st.library}`));
      }).catch(() => box.replaceChildren(el('div', 'err', "Couldn't read the sources from your PC.")));
      return box;
    }

    function toggleFullscreen() {
      const d = document;
      if (d.fullscreenElement || d.webkitFullscreenElement) (d.exitFullscreen || d.webkitExitFullscreen).call(d);
      else (root.requestFullscreen || root.webkitRequestFullscreen)?.call(root);
    }

    // ---------------------------------------------------------------- hands (the main way to work on the model)
    handBtn.onclick = () => {
      if (S.hands) { S.hands.stop(); S.hands = null; handsBox.hidden = true; handVideo.hidden = true; cursor.hidden = true; hoverTag.hidden = true; handBtn.classList.remove('on'); root.classList.remove('hands', 'camera'); store.set('hands', false); refreshUi(); return; }
      startHandControl(false);
    };
    async function startHandControl(auto) {
      if (!S.viewer || !S.viewer.objects.length) { if (!auto) toast('Hand control', 'Bring something up first.', 'info', 2500); return; }
      handsBox.hidden = false; handBtn.classList.add('on'); root.classList.add('hands'); handLabel.textContent = 'Starting…';
      const showCam = store.get('camera', true);
      handVideo.hidden = !showCam; root.classList.toggle('camera', showCam);
      handGuide.classList.add('open'); setTimeout(() => handGuide.classList.remove('open'), 9000);
      const v = S.viewer;
      const toClient = (x, y) => { const r = view.getBoundingClientRect(); return [r.left + x * r.width, r.top + y * r.height]; };
      const ndc = (x, y) => ({ x: x * 2 - 1, y: -(y * 2 - 1) });
      try {
        S.hands = await startHands(handVideo, handCanvas, {
          status: (t) => { handLabel.textContent = t; },
          gesture: (g) => { if (g) handLabel.textContent = g; },
          diag: (d) => {
            const dot = (ok) => (ok ? '●' : '○');
            handDiag.textContent = `${dot(d.camera === 'on')} CAMERA ${d.camera.toUpperCase()}  ·  ${dot(d.tracker === 'on')} TRACKER ${d.tracker.toUpperCase()}${d.delegate ? ' (' + d.delegate + ')' : ''}  ·  ${d.hands} HAND${d.hands === 1 ? '' : 'S'}  ·  ${d.fps} FPS`;
            handDiag.classList.toggle('bad', d.camera !== 'on' || d.tracker === 'failed');
          },
          hover: (x, y) => {
            if (x == null) { cursor.hidden = true; v.hover(null); hoverTag.hidden = true; return; }
            const [cx, cy] = toClient(x, y);
            cursor.hidden = false; cursor.className = 'sp-cursor';
            cursor.style.transform = `translate(${cx}px, ${cy}px)`;
            const n = ndc(x, y), p = v.pickNdc(n.x, n.y);
            v.hover(p);
            if (p) hoverTag.style.transform = `translate(${cx + 20}px, ${cy + 16}px)`;
          },
          dwell: (k) => { cursor.style.setProperty('--k', k); },
          select: (x, y) => { const n = ndc(x, y), p = v.pickNdc(n.x, n.y); v.select(p ? p.name : null, { from: 'hand' }); haptics.tap(); },
          pinchStart: (x, y) => {
            const n = ndc(x, y), p = v.pickNdc(n.x, n.y);
            if (p && v.grabStart(p, n)) { root.classList.add('grabbing'); haptics.tap(); return 'grab'; }
            return 'orbit';
          },
          pinchMove: (x, y, dx, dy, depth) => {
            v.grabMove(ndc(x, y), depth);
            const [cx, cy] = toClient(x, y); cursor.hidden = false; cursor.className = 'sp-cursor grab'; cursor.style.transform = `translate(${cx}px, ${cy}px)`;
          },
          pinchEnd: (flick) => { if (v.grab) { v.grabEnd(!!flick); root.classList.remove('grabbing'); refreshUi(); } },
          orbit: (dx, dy) => v.orbitBy(dx, dy),
          pan: (dx, dy) => v.panBy(dx, dy),
          zoom: (f) => v.dollyBy(f),
          roll: (r) => v.rollBy(r),
          explode: (d) => { v.explodeBy(d); renderRail(); },
        }, null);
        store.set('hands', true);
        refreshUi();
        if (!auto) speak('Hands on. Hover a piece to light it up, pinch to grab it and pull it out, flick to throw it, spread both palms to explode.');
      } catch (e) {
        handsBox.hidden = true; handVideo.hidden = true; handBtn.classList.remove('on'); root.classList.remove('hands', 'camera'); S.hands = null;
        report(`start failed: ${e && e.name} ${e && e.message}`);
        const msg = e && e.name === 'NotAllowedError' ? 'Camera access is blocked. iPhone/iPad: Settings → Safari (or AURA) → Camera → Allow. PC: click the camera icon in the address bar → Allow.'
          : e && e.name === 'NotFoundError' ? 'No camera found on this device.'
            : e && e.name === 'NotReadableError' ? 'The camera is busy in another app - close it and try again.' : (e.message || "Couldn't start hand tracking.");
        toast('Hand control', msg, 'error', 9000);
      }
    }

    // ---------------------------------------------------------------- entry
    function handle(a = {}) {
      if (a.query) ask(a.query);
      else if (a.text) ask(a.text);
    }
    showLauncher();
    handle(arg || {});
    setTimeout(() => { if (!arg?.query && !arg?.text) input.focus({ preventScroll: true }); }, 400);
  });
}
