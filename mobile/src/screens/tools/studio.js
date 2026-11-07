// AURA STUDIO - make anything, then change it with words. Describe it (or attach a photo / video and say what to
// change); the PC makes it (studio.py) and every version is kept: tap v1 / v2 / v3 to compare, "Change it…" to keep editing.
import { h, el, icon, toast, button, confirm, loading, empty } from '../../ui/components.js';
import * as haptics from '../../platform/haptics.js';
import * as audio from '../../platform/audio.js';
import { whenOf } from '../../core/format.js';
import { stage, micError } from './nexus/common.js';

const IDEAS = [['📊 Pie chart', 'A pie chart of '], ['🖼 Picture', 'A picture of '], ['🧭 Flowchart', 'A flowchart for '], ['📽 Slides', 'A 6 slide presentation on '],
  ['🎬 Video', 'A short video explaining '], ['✒ Logo', 'A logo for '], ['📄 Document', 'A Word document about '], ['📈 Graph', 'A line graph of '],
  ['🌐 Web page', 'A web page for '], ['🔳 QR code', 'A QR code for ']];
const KIND = { image: ['PICTURE', 'image'], photo_edit: ['PHOTO', 'image'], chart: ['CHART', 'dash'], diagram: ['DIAGRAM', 'link'], document: ['WORD', 'file'],
  slides: ['SLIDES', 'file'], spreadsheet: ['EXCEL', 'file'], webpage: ['WEB PAGE', 'globe'], svg: ['VECTOR', 'pen'], qr: ['QR', 'more'], audio: ['AUDIO', 'speaker'],
  text: ['TEXT', 'file'], video: ['VIDEO', 'camera'], video_edit: ['VIDEO', 'camera'] };

let live = null;          // the open Studio page, so AURA can show a new creation in it instead of opening another

export function open(ctx, arg = {}) {
  if (live && live.alive) { live.show(arg); return; }
  stage('STUDIO', 'SYS-08', (body, page, onCleanup) => {
    const S = { alive: true, project: null, version: null, job: null, attachments: [], poll: 0 };
    live = { get alive() { return S.alive; }, show };
    onCleanup(() => { S.alive = false; clearTimeout(S.poll); live = null; });

    // ---------------------------------------------------------------- composer
    const prompt = h('textarea', { class: 'textarea st-prompt', rows: 3, placeholder: 'Describe anything to make…\n"a pie chart of my week: study 30, gym 6, sleep 49"' });
    const attachRow = h('div', { class: 'st-atts' });
    const fileIn = h('input', { type: 'file', accept: 'image/*,video/*', multiple: true, hidden: true });
    const attachBtn = h('button', { class: 'iconbtn', 'aria-label': 'Attach a photo or video' }, icon('image'));
    const micBtn = h('button', { class: 'iconbtn', 'aria-label': 'Say it' }, icon('mic'));
    const ideas = h('div', { class: 'nx-chips scroll' }, IDEAS.map(([label, fill]) => {
      const b = h('button', { class: 'nx-chip' }, label);
      b.onclick = () => { prompt.value = fill; prompt.focus(); };
      return b;
    }));
    const createBtn = button('CREATE', () => go(prompt.value, null), 'solid');
    attachBtn.onclick = () => fileIn.click();
    fileIn.onchange = async () => {
      for (const f of fileIn.files) await upload(f);
      fileIn.value = '';
    };
    let rec = null;
    micBtn.onclick = async () => {
      if (rec) { rec.stop(); return; }
      try { rec = await audio.record({ maxSeconds: 30 }); } catch (e) { micError(e); return; }
      micBtn.classList.add('on');
      const blob = await rec.done; rec = null; micBtn.classList.remove('on');
      if (!blob) return;
      try { const r = await ctx.api.transcribe(blob); if (r.text) prompt.value = r.text; } catch (e) { toast('Studio', e.message, 'error'); }
    };

    // ---------------------------------------------------------------- progress + result
    const progress = h('div', { class: 'st-progress', hidden: true }, h('span', { class: 'st-orb' }), el('div', 'st-step', 'Thinking…'));
    const result = h('div', { class: 'st-result', hidden: true });
    const gallery = h('div', { class: 'st-gallery' });
    const hfBox = h('div');

    body.append(h('div', { class: 'nx-scroll st' },
      h('div', { class: 'nx-panel st-compose' }, el('div', 'nx-label', 'MAKE ANYTHING'), prompt, attachRow,
        h('div', { class: 'st-tools' }, attachBtn, micBtn, h('div', { style: 'flex:1' }), createBtn), ideas, fileIn),
      progress, result,
      h('div', { class: 'nx-label-row' }, el('div', 'nx-label', 'YOUR CREATIONS'), h('button', { class: 'nx-link', onclick: loadGallery }, 'REFRESH')),
      gallery, el('div', 'nx-label', 'MAGIC AI EDITS'), hfBox));

    async function upload(file) {
      const chip = h('span', { class: 'st-att busy' }, (file.type.startsWith('video') ? '🎬 ' : '🖼 ') + file.name.slice(0, 22) + ' …');
      attachRow.append(chip);
      try {
        const r = await ctx.api.studioUpload(file);
        if (r.error) throw new Error(r.error);
        S.attachments.push(r.id);
        chip.classList.remove('busy');
        chip.textContent = (r.kind === 'video' ? '🎬 ' : '🖼 ') + r.name.slice(0, 22);
        const x = h('button', { class: 'st-att-x', 'aria-label': 'Remove' }, '×');
        x.onclick = () => { S.attachments = S.attachments.filter((a) => a !== r.id); chip.remove(); };
        chip.append(x);
        if (!prompt.value.trim()) prompt.placeholder = r.kind === 'video' ? 'What should I do with this video? "trim to the first 10 s, add music-free narration…"' : 'What should I change? "make it brighter, crop square, add my name at the bottom"';
      } catch (e) { chip.remove(); toast("Couldn't upload", e.message, 'error'); }
    }

    async function go(text, projectId) {
      text = String(text || '').trim();
      if (!text) { toast('Studio', 'Describe what to make (or what to change).', 'info', 2500); return; }
      haptics.tap();
      try {
        const r = await ctx.api.studio('create', { prompt: text, project: projectId, attachments: S.attachments });
        if (r.error) throw new Error(r.error);
        if (!projectId) { prompt.value = ''; S.attachments = []; attachRow.innerHTML = ''; }
        follow(r.job);
      } catch (e) { toast("Couldn't start", e.message, 'error'); }
    }

    function follow(jid) {
      S.job = jid; progress.hidden = false;
      progress.querySelector('.st-step').textContent = 'Thinking about it…';
      progress.scrollIntoView({ behavior: 'smooth', block: 'center' });
      const tick = async () => {
        if (!S.alive || S.job !== jid) return;
        let j;
        try { j = await ctx.api.studio(`job?id=${jid}`); } catch (e) { S.poll = setTimeout(tick, 3000); return; }
        if (j.state === 'working') { progress.querySelector('.st-step').textContent = j.step || 'Working…'; S.poll = setTimeout(tick, 1500); return; }
        progress.hidden = true; S.job = null;
        if (j.state === 'done') { haptics.success(); toast('Studio', j.message || 'Done.', 'info', 3500); showProject(j.project, j.version); loadGallery(); }
        else if (j.state === 'error') toast("Studio couldn't make that", j.error || 'Something went wrong.', 'error', 8000);
        else { toast('Studio', 'That job was lost (the PC restarted?) - check YOUR CREATIONS or ask again.', 'info', 6000); loadGallery(); }
      };
      tick();
    }

    // ---------------------------------------------------------------- one creation
    async function showProject(pid, version) {
      result.hidden = false;
      result.replaceChildren(loading());
      let p;
      try { p = await ctx.api.studio(`project?id=${pid}`); } catch (e) { result.replaceChildren(el('div', 'err', e.message)); return; }
      if (p.error) { result.replaceChildren(el('div', 'err', p.error)); return; }
      S.project = p;
      const v = p.versions.find((x) => x.v === version) || p.versions[p.versions.length - 1];
      S.version = v.v;
      const strip = h('div', { class: 'st-versions' }, p.versions.map((x) => {
        const b = h('button', { class: 'st-ver' + (x.v === v.v ? ' on' : ''), title: x.request }, `v${x.v}`);
        b.onclick = () => showProject(pid, x.v);
        return b;
      }));
      const change = h('input', { class: 'input', placeholder: 'Change it… "make the bars blue", "shorter", "add a title"', enterkeyhint: 'send' });
      change.onkeydown = (e) => { if (e.key === 'Enter' && change.value.trim()) { go(change.value, pid); change.value = ''; } };
      const changeMic = h('button', { class: 'iconbtn', 'aria-label': 'Say the change' }, icon('mic'));
      changeMic.onclick = async () => {
        let r2;
        try { r2 = await audio.record({ maxSeconds: 20 }); } catch (e) { micError(e); return; }
        changeMic.classList.add('on');
        const blob = await r2.done; changeMic.classList.remove('on');
        if (!blob) return;
        try { const t = (await ctx.api.transcribe(blob)).text; if (t) go(t, pid); } catch (e) { toast('Studio', e.message, 'error'); }
      };
      result.replaceChildren(h('div', { class: 'nx-panel' },
        h('div', { class: 'st-head' }, h('div', {}, el('b', null, p.title), el('span', null, `${(KIND[v.kind] || ['FILE'])[0]} · v${v.v} · ${whenOf(v.t)}`)),
          h('button', { class: 'nx-link', onclick: () => { result.hidden = true; S.project = null; } }, 'CLOSE')),
        preview(v), el('div', 'st-msg', v.message || ''), el('div', 'st-req', '“' + (v.request || '') + '”'), strip,
        h('div', { class: 'nx-row', style: 'margin-top:10px' }, change, changeMic, button('CHANGE', () => { if (change.value.trim()) { go(change.value, pid); change.value = ''; } }, 'solid')),
        h('div', { class: 'row-btns', style: 'margin-top:10px' },
          v.link ? button('OPEN / SAVE', () => window.open(v.link, '_blank', 'noopener')) : null,
          button('DELETE', async () => {
            if (!(await confirm('Delete this creation?', `"${p.title}" and all its versions will be removed from your PC.`, 'DELETE', true))) return;
            await ctx.api.studio('delete', { id: pid }); result.hidden = true; loadGallery();
          }, 'danger'))));
      result.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function preview(v) {
      const f = (v.file || '').toLowerCase(), src = v.link;
      if (!src) return el('div', 'empty', 'Preview unavailable - the file link expired. Reopen it.');
      if (/\.(png|jpe?g|gif|webp|svg)$/.test(f)) return h('div', { class: 'st-preview' }, h('img', { src, alt: v.message || 'creation' }));
      if (/\.(mp4|mov|webm)$/.test(f)) return h('div', { class: 'st-preview' }, h('video', { src, controls: true, playsinline: true, preload: 'metadata', poster: v.thumb || undefined }));
      if (/\.mp3$/.test(f)) return h('div', { class: 'st-preview audio' }, h('audio', { src, controls: true }));
      if (/\.html?$/.test(f)) return h('div', { class: 'st-preview page' }, h('iframe', { src, sandbox: '', title: 'web page preview' }));
      if (/\.(md|txt|py|js|csv|json|css)$/.test(f)) {
        const pre = h('pre', { class: 'st-text' }, 'Loading…');
        fetch(src).then((r) => r.text()).then((t) => { pre.textContent = t.slice(0, 20000); }).catch(() => { pre.textContent = 'Open it to read.'; });
        return h('div', { class: 'st-preview text' }, pre);
      }
      const k = KIND[v.kind] || ['FILE', 'file'];
      return h('div', { class: 'st-preview file' }, icon(k[1]), el('b', null, `${k[0]} FILE`), el('span', null, 'Tap OPEN / SAVE - your phone shows it and can save or share it.'));
    }

    // ---------------------------------------------------------------- gallery
    async function loadGallery() {
      gallery.replaceChildren(loading());
      try {
        const { items } = await ctx.api.studio('projects');
        gallery.innerHTML = '';
        if (!items.length) { gallery.append(empty('Nothing made yet - describe anything above.')); return; }
        for (const it of items) {
          const k = KIND[it.kind] || ['FILE', 'file'];
          const card = h('button', { class: 'st-card' },
            it.thumb ? h('img', { src: it.thumb, alt: '', loading: 'lazy' }) : h('div', { class: 'st-card-ic' }, icon(k[1])),
            h('div', { class: 'st-card-txt' }, el('b', null, it.title), el('span', null, `${k[0]} · ${it.versions} version${it.versions > 1 ? 's' : ''}`)));
          card.onclick = () => showProject(it.id);
          gallery.append(card);
        }
      } catch (e) { gallery.replaceChildren(el('div', 'err', e.message)); }
    }

    // ---------------------------------------------------------------- Hugging Face token (magic AI edits)
    async function loadHf() {
      let st = { ready: false };
      try { st = await ctx.api.studio('hf'); } catch (e) { /* PC off */ }
      const tokenIn = h('input', { class: 'input', placeholder: 'hf_… (your free Hugging Face token)', autocapitalize: 'off', autocomplete: 'off' });
      hfBox.replaceChildren(h('div', { class: 'nx-panel' },
        el('div', 'help', st.ready
          ? '✅ ON - AURA can do AI edits ("add sunglasses", "change the background to a beach"), sharper AI pictures and AI video clips. Free daily limits apply.'
          : 'OFF - AI edits like "add sunglasses to me" or "make the background a beach", sharper pictures and AI video clips need a FREE Hugging Face token: huggingface.co → Sign up → Settings → Access Tokens → Create new token (type: Read) → copy it here.'),
        h('div', { class: 'nx-row', style: 'margin-top:10px' }, tokenIn, button(st.ready ? 'REPLACE' : 'SAVE', async () => {
          try {
            const r = await ctx.api.studio('hf', { token: tokenIn.value });
            if (r.error) throw new Error(r.error);
            toast('AI edits', r.ready ? 'On - magic edits unlocked.' : 'Token removed.', 'info', 3500); loadHf();
          } catch (e) { toast('AI edits', e.message, 'error'); }
        }, 'solid'))));
    }

    function show(a = {}) {
      if (a.job) follow(a.job);
      else if (a.project) showProject(a.project);
    }

    loadGallery();
    loadHf();
    show(arg || {});
  });
}
