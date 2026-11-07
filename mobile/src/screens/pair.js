// First launch: pair this phone with AURA on your PC. The PC shows a 6-digit code (proving you're at
// the PC); the phone gets its own revocable key. Replaces the PC's login window for the phone.
import { el, h, LOGO, button, toast } from '../ui/components.js';
import { api, setToken } from '../core/api.js';
import { deviceName, isIOS, isStandalone } from '../platform/device.js';
import * as haptics from '../platform/haptics.js';

export function showPairing(onDone) {
  const root = document.getElementById('app');
  root.innerHTML = '';
  const body = h('div', { class: 'screen active', style: 'display:block;padding-top:calc(var(--safe-t) + 24px)' });
  root.append(body);

  const head = h('div', { style: 'text-align:center;margin-bottom:18px' },
    h('div', { html: LOGO, style: 'width:64px;height:64px;margin:0 auto 10px' }),
    el('div', 'boot-logo', 'AURA'), el('div', 'boot-sub', 'CONNECT TO YOUR PC'));
  const area = h('div');
  body.append(head, area);

  async function check() {
    area.innerHTML = '';
    area.append(h('div', { class: 'loading' }, el('span', 'spinner'), 'Looking for AURA on your PC…'));
    try {
      const r = await api.hello();
      if (r.paired) { onDone(); return; }
      showCodeStep(r.pc);
    } catch (e) {
      showUnreachable();
    }
  }

  function showUnreachable() {
    area.innerHTML = '';
    area.append(h('section', { class: 'panel' },
      h('div', { class: 'ph' }, h('div', { class: 'title' }, "CAN'T REACH AURA"), h('div', { class: 'code' }, 'LINK')),
      h('ol', { class: 'help', style: 'padding-left:18px;margin:0' },
        h('li', {}, 'Make sure AURA is running on your PC.'),
        h('li', {}, 'Make sure Tailscale is on, on both the PC and this phone (same account).'),
        h('li', {}, 'Open the address from mobile/SETUP.md (it ends in .ts.net).')),
      h('div', { style: 'margin-top:14px' }, button('TRY AGAIN', check, 'solid block'))));
  }

  function showCodeStep(pc) {
    area.innerHTML = '';
    const code = h('input', { class: 'input code-input', inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: '7', placeholder: '••••••', 'aria-label': 'Pairing code' });
    const name = h('input', { class: 'input', value: deviceName(), maxlength: '40', 'aria-label': 'Name for this phone' });
    const err = el('div', 'err');
    code.addEventListener('input', () => { code.value = code.value.replace(/\D/g, '').slice(0, 6); if (code.value.length === 6) pair.click(); });
    const ask = button('SHOW A CODE ON MY PC', async () => {
      try { await api.requestCode(); haptics.tap(); toast('Pairing', `Check your PC${pc ? ' (' + pc + ')' : ''} - a code just popped up.`, 'info', 5000); code.focus(); }
      catch (e) { err.textContent = e.message; }
    });
    const pair = button('PAIR THIS PHONE', async () => {
      err.textContent = '';
      if (code.value.length !== 6) { err.textContent = 'Enter the 6-digit code shown on your PC.'; return; }
      try {
        const r = await api.pair(code.value, name.value.trim() || deviceName());
        setToken(r.token);
        try { localStorage.setItem('aura.deviceId', JSON.stringify(r.device.id)); } catch (e) { /* ignore */ }
        haptics.success();
        toast('Paired', `This phone is now connected to AURA${pc ? ' on ' + pc : ''}.`, 'focus', 4000);
        onDone();
      } catch (e) { err.textContent = e.message; code.value = ''; haptics.warning(); }
    }, 'solid block');

    if (isIOS && !isStandalone()) {
      area.append(h('section', { class: 'panel', style: 'border-color:var(--amber)' },
        h('div', { class: 'ph' }, h('div', { class: 'title' }, 'FIRST: ADD TO HOME SCREEN'), h('div', { class: 'code' }, 'IPHONE')),
        el('div', 'help', 'Tap Share in Safari → "Add to Home Screen", then open AURA from its new icon and pair there. (The Home Screen app keeps its own login, so pairing here in Safari won\'t carry over.) That gives you full screen and notifications.')));
    }
    area.append(
      h('section', { class: 'panel' },
        h('div', { class: 'ph' }, h('div', { class: 'title' }, 'PAIR THIS PHONE'), h('div', { class: 'code' }, pc || 'PC')),
        el('div', 'help', 'Tap the button below - AURA shows a 6-digit code on your PC. (Or right-click the AURA tray icon → "Pair a phone".) Type the code here. It works for 5 minutes.'),
        h('div', { style: 'margin:14px 0' }, ask),
        h('div', { class: 'field' }, el('label', null, 'CODE FROM YOUR PC'), code),
        h('div', { class: 'field' }, el('label', null, 'NAME FOR THIS PHONE'), name),
        pair, err));
  }

  check();
}
