// Important emails AURA is reminding you about (Mini AURA's email list): dismiss one, or all.
import { el, h, openPage, button, empty, loading, toast, confirm } from '../../ui/components.js';
import * as haptics from '../../platform/haptics.js';

export function open(ctx) {
  openPage('IMPORTANT EMAILS', async (body, page) => {
    const list = h('ul', { class: 'list' });
    const render = (emails) => {
      list.innerHTML = '';
      if (!emails.length) {
        list.append(h('li', {}, empty('Nothing waiting. AURA tells you here (and with a notification) when an important email arrives.')));
        allBtn.hidden = true;
        return;
      }
      allBtn.hidden = false;
      for (const m of emails) {
        const x = h('button', { class: 'x', 'aria-label': `Dismiss the email from ${m.from}` }, '×');
        const li = h('li', {}, h('div', { class: 'txt' }, el('div', null, h('b', {}, m.from)), el('div', 'meta', m.summary || m.subject)), x);
        x.onclick = async () => {
          li.style.opacity = '.4'; haptics.tap();
          try { render((await ctx.api.dismissEmails([m.id])).emails); ctx.refresh(); }
          catch (e) { li.style.opacity = ''; toast("Couldn't dismiss", e.message, 'error'); }
        };
        list.append(li);
      }
    };
    const allBtn = button('DISMISS ALL', async () => {
      if (!(await confirm('DISMISS ALL?', 'AURA will stop reminding you about all of these emails.', 'DISMISS ALL'))) return;
      render((await ctx.api.dismissAllEmails()).emails); ctx.refresh();
    });
    const ask = button('ASK AURA ABOUT MY EMAIL', () => { page.close(); ctx.switchTab('comms'); ctx.send('any important emails?'); }, 'block');
    body.append(h('div', { class: 'help', style: 'margin-bottom:10px' },
      'Tap × once you\'ve dealt with one - AURA stops reminding you about it. You can also say "dismiss the Rayyan one" or "read me the email from …".'),
    list, h('div', { class: 'row-btns', style: 'margin-top:14px' }, allBtn), h('div', { style: 'margin-top:8px' }, ask));
    list.append(loading('Checking…'));
    try { render((await ctx.api.emails()).emails || []); }
    catch (e) { list.innerHTML = ''; list.append(h('li', {}, empty(e.message))); }
  });
}
