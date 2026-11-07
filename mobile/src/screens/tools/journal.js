// Journal check-in - the same three questions AURA asks by voice on the PC ("journal time").
// Two ways: fill them in here, or have AURA ask you in the conversation (answers by text or voice).
import { el, h, openPage, button, toast } from '../../ui/components.js';
import * as haptics from '../../platform/haptics.js';

export function open(ctx) {
  openPage('JOURNAL', (body, page) => {
    let mood = null;
    const moods = h('div', { class: 'chips' });
    for (let i = 1; i <= 10; i++) {
      const b = h('button', { class: 'chip', style: 'min-width:44px;justify-content:center', 'aria-label': `Mood ${i} out of 10` }, String(i));
      b.onclick = () => { mood = i; moods.querySelectorAll('.chip').forEach((c, j) => c.classList.toggle('warn', j + 1 === i)); haptics.tap(); };
      moods.append(b);
    }
    const good = h('textarea', { class: 'textarea', style: 'min-height:90px', maxlength: '1000', placeholder: 'One good thing about today' });
    const mind = h('textarea', { class: 'textarea', style: 'min-height:90px', maxlength: '1000', placeholder: 'Anything on your mind for tomorrow' });
    const save = button('SAVE CHECK-IN', async () => {
      if (mood == null && !good.value.trim() && !mind.value.trim()) { toast('Journal', 'Answer at least one question first.', 'info', 3000); return; }
      try { await ctx.api.journal(mood, good.value.trim(), mind.value.trim()); haptics.success(); toast('Journal', 'Got it, logged. Rest well.', 'focus', 4000); page.close(); ctx.refresh(); }
      catch (e) { toast("Couldn't save", e.message, 'error'); }
    }, 'solid block');
    const byVoice = button('LET AURA ASK ME', () => { page.close(); ctx.switchTab('comms'); ctx.send('journal time'); }, 'block');
    body.append(
      h('div', { class: 'help', style: 'margin-bottom:14px' }, 'A quick reflective check-in. It feeds your mood trend on the Dashboard.'),
      h('div', { class: 'field' }, el('label', null, 'HOW ARE YOU FEELING TODAY? (1-10)'), moods),
      h('div', { class: 'field' }, el('label', null, "WHAT'S ONE GOOD THING ABOUT TODAY?"), good),
      h('div', { class: 'field' }, el('label', null, 'ANYTHING ON YOUR MIND FOR TOMORROW?'), mind),
      save, h('div', { style: 'margin-top:8px' }, byVoice));
  });
}
