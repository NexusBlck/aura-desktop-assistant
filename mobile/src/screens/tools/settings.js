// Settings - voice, look, notifications, this phone's link to the PC, paired devices.
import { el, h, openPage, panel, button, empty, toast, confirm } from '../../ui/components.js';
import { setToken } from '../../core/api.js';
import * as notify from '../../platform/notify.js';
import * as device from '../../platform/device.js';
import { whenOf } from '../../core/format.js';
import * as brainKit from '../../brain/kit.js';
import * as brainLocal from '../../brain/local.js';
import { post } from '../../core/api.js';

function toggle(label, sub, on, onChange) {
  const t = h('button', { class: 'toggle', role: 'switch', 'aria-checked': on ? 'true' : 'false', 'aria-label': label });
  t.onclick = () => { const v = t.getAttribute('aria-checked') !== 'true'; t.setAttribute('aria-checked', v ? 'true' : 'false'); onChange(v); };
  return h('div', { class: 'setting' }, h('div', { class: 'txt' }, el('b', null, label), el('div', 'help', sub)), t);
}

export function open(ctx) {
  openPage('SETTINGS', async (body, page) => {
    const s = ctx.store.get('settings');
    const set = (patch) => ctx.store.update('settings', patch);

    // ---- voice & look
    const themes = h('div', { class: 'chips', style: 'margin-top:8px' });
    const paintTheme = () => themes.querySelectorAll('.chip').forEach((c) => c.classList.toggle('warn', c.dataset.v === ctx.store.get('settings').theme));
    [['dark', 'DARK (AS ON PC)'], ['system', 'MATCH IPHONE'], ['light', 'LIGHT']].forEach(([v, l]) => {
      const c = h('button', { class: 'chip', 'data-v': v }, l);
      c.onclick = () => { set({ theme: v }); paintTheme(); };
      themes.append(c);
    });
    paintTheme();

    // ---- notifications
    const pushState = el('div', 'help');
    const paintPush = () => {
      const st = notify.pushStatus();
      pushState.textContent = {
        'needs-install': 'On iPhone and iPad, notifications need AURA on your Home Screen: Safari → Share → Add to Home Screen, then open it from there.',
        unsupported: "This browser can't show notifications.",
        granted: 'On - reminders, focus timer, important emails and AURA\'s answers (when the app is closed).',
        denied: 'Blocked - turn them on in Settings → Notifications → AURA.',
        default: 'Off',
      }[st] || st;
    };
    paintPush();
    const enable = button('TURN ON NOTIFICATIONS', async () => {
      try {
        const { key } = await ctx.api.pushKey();
        const sub = await notify.enablePush(key);
        await ctx.api.pushSubscribe(sub);
        toast('Notifications', 'On. Sending a test…', 'focus', 3000);
        await ctx.api.pushTest();
      } catch (e) { toast('Notifications', e.message, 'error', 8000); }
      paintPush();
    }, 'solid');
    const test = button('SEND A TEST', () => ctx.api.pushTest().then(() => toast('Notifications', 'Sent - it can take a few seconds.', 'info', 3000)));

    // ---- connection & devices
    const devices = h('ul', { class: 'list' });
    async function loadDevices() {
      devices.innerHTML = '';
      try {
        const { devices: list } = await ctx.api.devices();
        if (!list.length) devices.append(h('li', {}, empty('No devices.')));
        for (const d of list) {
          const rm = h('button', { class: 'mini danger' }, d.me ? 'UNPAIR' : 'REMOVE');
          rm.onclick = async () => {
            const ok = await confirm(d.me ? 'UNPAIR THIS PHONE?' : `REMOVE ${d.name.toUpperCase()}?`,
              d.me ? 'This phone will stop working with AURA until you pair it again with a code from your PC.' : 'That device will no longer be able to reach AURA.', d.me ? 'UNPAIR' : 'REMOVE', true);
            if (!ok) return;
            await ctx.api.revoke(d.id);
            if (d.me) { setToken(null); try { localStorage.removeItem('aura.brain.kit'); } catch (e) { /* ignore */ } location.reload(); } else loadDevices();
          };
          devices.append(h('li', {}, h('div', { class: 'txt' }, el('div', null, d.name + (d.me ? ' (this phone)' : '')),
            el('div', 'meta', `Paired ${whenOf(d.created)}${d.last_seen ? ' · last seen ' + whenOf(d.last_seen) : ''}`)), rm));
        }
      } catch (e) { devices.append(h('li', {}, empty(e.message))); }
    }

    const install = device.isIOS && !device.isStandalone()
      ? panel('INSTALL', 'HOME SCREEN', el('div', 'help', 'For the full app experience (full screen, notifications, its own icon): tap Share in Safari, then "Add to Home Screen".'))
      : null;

    // ---- the phone brain (AURA without the PC)
    const brainInfo = h('div');
    const paintBrain = () => {
      const k = brainKit.get();
      brainInfo.replaceChildren(
        h('div', { class: 'kv' }, el('span', null, 'STATUS'), el('b', null, brainKit.ready() ? 'READY' : 'NOT SET UP - open AURA with your PC on')),
        h('div', { class: 'kv' }, el('span', null, 'LAST UPDATE FROM PC'), el('b', null, k ? whenOf(k.t) : '--')),
        h('div', { class: 'kv' }, el('span', null, 'AI'), el('b', null, k ? [k.keys.gemini ? 'Gemini' : '', k.keys.groq ? 'Groq' : ''].filter(Boolean).join(' + ') : '--')),
        h('div', { class: 'kv' }, el('span', null, 'WAITING TO SYNC'), el('b', null, `${brainLocal.pending()} change(s)`)),
        h('div', { class: 'kv' }, el('span', null, 'JOBS WAITING FOR THE PC'), el('b', null, String(brainLocal.pcJobs().length))));
    };
    paintBrain();
    const syncNow = button('SYNC NOW', async () => {
      try {
        const n = await brainLocal.flush(post);
        brainKit.set(await ctx.api.brainKit());
        toast('Phone brain', `Up to date${n ? ` - ${n} change(s) sent to your PC` : ''}.`, 'info', 3000);
      } catch (e) { toast('Phone brain', "Your PC isn't reachable - it syncs by itself when it is.", 'error', 4000); }
      paintBrain();
    }, 'solid');

    // ---- Apple Watch ("Ask AURA" shortcut)
    const watchOut = h('div');
    const copyRow = (label, value) => {
      const b = button('COPY', async () => {
        try { await navigator.clipboard.writeText(value); toast('Copied', label, 'info', 2000); } catch (e) { toast('Copy', 'Long-press the text to copy it.', 'info'); }
      });
      return h('div', { class: 'field' }, el('label', null, label), h('div', { class: 'add-row', style: 'gap:8px' },
        h('input', { class: 'input', readonly: true, value, style: 'font-family:var(--f-mono);font-size:.8rem' }), b));
    };
    const watchBtn = button('SET UP APPLE WATCH', async () => {
      try {
        const r = await ctx.api.watchKey();
        watchOut.replaceChildren(
          el('div', 'help', 'Copy this ONE link and paste it into the "Ask AURA" shortcut (step 4 of the guide). It is shown only once - remove "Apple Watch" under Paired devices to switch it off. Don\'t share it.'),
          copyRow('WATCH LINK', r.link));
        loadDevices();
      } catch (e) { toast('Apple Watch', e.message, 'error'); }
    }, 'solid');

    if (install) body.append(install);
    body.append(
      panel('VOICE', 'AUDIO', toggle("Read AURA's answers aloud", 'AURA answers in its own voice through the phone. Your PC stays quiet for things you ask from here.', s.voice, (v) => set({ voice: v }))),
      panel('LOOK', 'THEME', el('div', 'help', 'AURA is designed dark, like the HUD on your PC.'), themes),
      panel('NOTIFICATIONS', 'PUSH', pushState, h('div', { class: 'row-btns', style: 'margin-top:10px' }, enable, test)),
      panel('CONNECTION', 'LINK', h('div', { class: 'kv' }, el('span', null, 'PC'), el('b', null, (ctx.store.get('state') || {}).pc || '--')),
        h('div', { class: 'kv' }, el('span', null, 'STATUS'), el('b', null, String(ctx.store.get('conn')).toUpperCase())),
        h('div', { class: 'kv' }, el('span', null, 'ADDRESS'), el('b', { style: 'word-break:break-all' }, location.host))),
      panel('PHONE BRAIN', 'PC OFF', el('div', 'help', "When your PC is off or unreachable, AURA runs on the phone itself: chat, memory, tasks, reminders, notes, web search, Vision, LIVE, BABEL, ECHO, RADAR, PULSE, Explain. Changes sync to your PC when it's back. Jobs that need the PC (typing on it, email, LINK) wait for it."),
        brainInfo, h('div', { class: 'row-btns', style: 'margin-top:10px' }, syncNow)),
      panel('APPLE WATCH', 'ASK AURA', el('div', 'help', 'Talk to AURA from your wrist: a Shortcut on the watch sends what you say to your PC and shows + speaks the answer. Works anywhere through Tailscale Funnel; the watch has its own key.'),
        h('div', { class: 'row-btns', style: 'margin-top:10px' }, watchBtn), watchOut),
      panel('PAIRED DEVICES', 'SECURITY', devices, el('div', 'help', 'Only paired devices can reach AURA. Pair another phone from the AURA tray icon on your PC → "Pair a phone".')),
      panel('ABOUT', 'AURA MOBILE', el('div', 'help', 'AURA Mobile is the phone side of the AURA on your PC - same brain, same memory, same tasks. While the PC is on, everything runs there. For the phone brain, this phone keeps a copy of your Groq / Gemini keys - unpairing the phone deletes them.')),
    );
    loadDevices();
  });
}
