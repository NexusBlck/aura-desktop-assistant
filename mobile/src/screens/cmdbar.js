// The command bar (HUD's "›" box) for touch: auto-growing input, suggestions + your history,
// push-to-talk mic and send. Used on CORE and COMMS.
import { el, h, icon } from '../ui/components.js';
import { suggestionsFor } from '../core/commands.js';
import * as haptics from '../platform/haptics.js';

export function commandBar(ctx, { placeholder = 'Message AURA…', afterSend = null } = {}) {
  const input = h('textarea', { rows: '1', placeholder, 'aria-label': 'Message AURA', autocomplete: 'off', autocapitalize: 'sentences', enterkeyhint: 'send' });
  const mic = h('button', { class: 'mic', type: 'button', 'aria-label': 'Talk to AURA' }, icon('mic'));
  const send = h('button', { class: 'send', type: 'button', 'aria-label': 'Send', disabled: true }, icon('send'));
  const suggest = h('ul', { class: 'suggest', hidden: true, role: 'listbox' });
  const node = h('div', { class: 'cmdbar' }, suggest, h('div', { class: 'cmd-box' }, el('span', 'prompt', '›'), input, mic, send));

  const grow = () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 120) + 'px';
    send.disabled = !input.value.trim();
  };

  async function submit() {
    const text = input.value.trim();
    if (!text) return;
    haptics.tap();
    input.value = ''; grow(); closeSuggest();
    const ok = await ctx.send(text);
    if (!ok) { input.value = text; grow(); return; }
    if (afterSend) afterSend();
  }

  function closeSuggest() { suggest.hidden = true; }
  function openSuggest() {
    const items = suggestionsFor(input.value);
    suggest.innerHTML = '';
    if (!items.length) return closeSuggest();
    for (const [s, k] of items) {
      const li = h('li', { role: 'option' }, el('span', null, s), el('span', 'k', k));
      li.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        input.value = s; grow(); input.focus();
        if (!s.endsWith(' ')) submit(); else closeSuggest();
      });
      suggest.append(li);
    }
    suggest.hidden = false;
  }

  input.addEventListener('input', () => { grow(); openSuggest(); });
  input.addEventListener('focus', () => { if (!input.value) openSuggest(); });
  input.addEventListener('blur', () => setTimeout(closeSuggest, 150));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
    if (e.key === 'Escape') { closeSuggest(); input.blur(); }
  });
  send.onclick = submit;
  mic.onclick = () => ctx.talk();
  ctx.store.on('recording', (on) => {
    mic.classList.toggle('rec', !!on);
    mic.setAttribute('aria-label', on ? 'Stop and send' : 'Talk to AURA');
  });

  return {
    node,
    fill(text) { input.value = text; grow(); input.focus(); const n = input.value.length; input.setSelectionRange(n, n); closeSuggest(); },
    focus() { input.focus(); },
    setPlaceholder(p) { input.placeholder = p; },
  };
}
