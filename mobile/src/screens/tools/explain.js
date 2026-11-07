// Explain - the PC's explain cat (highlight + Ctrl+Alt+E) on the phone: paste or type any word,
// phrase or passage; the cat explains it with web sources and what it means for you; ask follow-ups.
import { el, h, openPage, button, panel, empty, loading, toast } from '../../ui/components.js';
import { speechChunks } from '../../core/format.js';
import * as audio from '../../platform/audio.js';
import * as haptics from '../../platform/haptics.js';

export function open(ctx, preset = '') {
  openPage('EXPLAIN', (body) => {
    const catBox = h('div', { style: 'width:110px;height:96px;margin:0 auto 6px', 'aria-hidden': 'true' });
    const text = h('textarea', { class: 'textarea', style: 'min-height:110px', maxlength: '4000', placeholder: 'Paste or type anything you want explained…', 'aria-label': 'Text to explain' });
    text.value = typeof preset === 'string' ? preset : '';
    const out = h('div');
    let current = null, cat = null;

    const say = (a) => {
      if (!ctx.store.get('settings').voice) return;
      const t = [a.explanation || a.headline, a.for_you].filter(Boolean).join(' ');
      audio.stop();
      cat && cat.setState('talk');
      audio.playSequence(speechChunks(t).map((p) => () => ctx.api.tts(p).catch(() => null)));
    };
    ctx.store.on('speakingLocal', (on) => { if (cat && !on && cat.state === 'talk') cat.setState('sit'); });

    function showAnswer(a, question) {
      const sources = (a.sources || []).map((s) => h('li', { class: 'link' }, h('a', { href: s.url, target: '_blank', rel: 'noopener noreferrer', class: 'txt' }, s.title || s.domain), el('span', 'kind', s.domain || 'WEB')));
      const card = panel(question ? 'FOLLOW-UP' : 'EXPLANATION', question ? question.slice(0, 28).toUpperCase() : 'CAT.EXPLAIN',
        h('div', { class: 'answer' },
          a.headline ? h('h3', {}, a.headline) : null,
          a.explanation ? h('p', {}, a.explanation) : (a.text ? h('p', {}, a.text) : null),
          a.for_you ? h('p', { class: 'for-you' }, a.for_you) : null,
          sources.length ? h('div', {}, el('div', 'section-label', 'SOURCES'), h('ul', { class: 'list' }, ...sources)) : null,
          h('button', { class: 'mini', onclick: () => say(a) }, '▶ READ ALOUD')));
      out.append(card);
      card.scrollIntoView({ behavior: 'smooth', block: 'start' });
      say(a);
    }

    const followIn = h('input', { class: 'input', placeholder: 'Ask a follow-up…', enterkeyhint: 'send', 'aria-label': 'Follow-up question' });
    const followRow = h('div', { class: 'add-row', hidden: true }, followIn);
    followIn.addEventListener('keydown', async (e) => {
      if (e.key !== 'Enter' || !followIn.value.trim() || !current) return;
      const q = followIn.value.trim(); followIn.value = ''; followIn.blur();
      const wait = loading('Thinking…'); out.append(wait); cat && cat.setState('think');
      try {
        const a = await ctx.api.followUp(current, q);
        wait.remove();
        if (a.error) { toast('Explain', a.error, 'error'); return; }
        showAnswer(a, q);
      } catch (err) { wait.remove(); toast('Explain', err.message, 'error'); cat && cat.setState('sit'); }
    });

    const go = button('EXPLAIN IT', async () => {
      const t = text.value.trim();
      if (!t) { toast('Explain', 'Paste or type something first.', 'info', 2500); return; }
      text.blur(); out.innerHTML = ''; followRow.hidden = true; haptics.tap();
      out.append(loading('Sniffing around the web…')); cat && cat.setState('think');
      try {
        const a = await ctx.api.explain(t);
        out.innerHTML = '';
        current = a.id;
        showAnswer(a);
        followRow.hidden = false;
      } catch (e) { out.innerHTML = ''; out.append(empty(e.message)); cat && cat.setState('sit'); }
    }, 'solid');
    const paste = button('PASTE', async () => {
      try { text.value = await navigator.clipboard.readText(); } catch (e) { text.focus(); toast('Paste', 'Long-press in the box and choose Paste.', 'info', 3000); }
    });

    body.append(catBox, h('div', { class: 'help', style: 'text-align:center;margin-bottom:12px' },
      'Copied something in another app? Paste it here and the cat explains it - like Ctrl+Alt+E on your PC.'),
    text, h('div', { class: 'row-btns', style: 'margin:10px 0 14px' }, paste, go), out, followRow);
    if (window.AuraCat) { cat = window.AuraCat.mount(catBox, { onClick() { cat.pet(); haptics.success(); } }); cat.setState('sit'); }
    if (text.value) go.click();
  });
}
