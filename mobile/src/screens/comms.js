// COMMS - the conversation (shared with the HUD log and Mini AURA), live thinking indicator,
// AURA asking you something, the command bar and push-to-talk.
import { el, h, icon } from '../ui/components.js';
import { timeOf, speechChunks } from '../core/format.js';
import * as audio from '../platform/audio.js';
import { commandBar } from './cmdbar.js';

let ctx, transcript, thinking, bar, jump, rendered = 0, lastSig = '';

export function mount(root, c) {
  ctx = c;
  transcript = h('div', { class: 'transcript', role: 'log', 'aria-live': 'polite', 'aria-label': 'Conversation with AURA' });
  thinking = h('div', { class: 't-line t-thinking', hidden: true }, 'AURA is thinking', el('span', 'dots'));
  bar = commandBar(ctx, { placeholder: 'Message AURA…' });
  jump = h('button', { class: 'btn jump', hidden: true, 'aria-label': 'Jump to latest' }, icon('down'));
  jump.onclick = () => scrollEnd(true);
  transcript.addEventListener('scroll', () => { jump.hidden = nearEnd(); });
  root.append(h('div', { class: 'comms' }, transcript, jump, h('div', { class: 'comms-input' }, bar.node)));

  ctx.store.on('chat', renderChat);
  ctx.store.on('status', (st) => {
    thinking.hidden = !st.thinking;
    bar.setPlaceholder(st.asking ? 'Type your answer…' : 'Message AURA…');
    if (!thinking.hidden && nearEnd()) scrollEnd();
  });
  renderChat(ctx.store.get('chat') || []);
}

export function shown() {
  scrollEnd();
  const st = ctx.store.get('status') || {};
  if (st.asking) setTimeout(() => bar.focus(), 250);
}

const nearEnd = () => transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 80;
function scrollEnd(smooth) { transcript.scrollTo({ top: transcript.scrollHeight, behavior: smooth ? 'smooth' : 'auto' }); jump.hidden = true; }

function line(m) {
  const you = m.role === 'you';
  const tag = h('div', { class: 'tag' }, you ? 'YOU' : 'AURA', el('span', 'time', timeOf(m.t)));
  if (!you) {
    const say = h('button', { class: 'say', 'aria-label': 'Read this aloud' }, '▶ PLAY');
    say.onclick = () => { audio.stop(); audio.playSequence(speechChunks(m.text).map((p) => () => ctx.api.tts(p).catch(() => null))); };
    tag.append(say);
  }
  return h('div', { class: 't-line ' + (you ? 't-you' : 't-aura') }, tag, m.text);
}

function renderChat(chat) {
  chat = chat || [];
  const sig = chat.length ? `${chat.length}|${chat[0].t}|${chat[chat.length - 1].t}` : '';
  if (sig === lastSig) return;
  const stick = nearEnd() || !rendered;
  // append only what's new when possible (long conversations stay fast)
  const canAppend = rendered && rendered <= chat.length && transcript.childElementCount - 1 === rendered && lastSig.split('|')[1] === String(chat[0] && chat[0].t);
  if (!canAppend) {
    transcript.innerHTML = '';
    if (!chat.length) transcript.append(el('div', 'empty', 'Nothing said yet. Type below or tap the mic and talk.'));
    rendered = 0;
  }
  const frag = document.createDocumentFragment();
  for (let i = rendered; i < chat.length; i++) frag.append(line(chat[i]));
  if (chat.length) transcript.querySelector('.empty')?.remove();
  transcript.append(frag);
  transcript.append(thinking);
  rendered = chat.length;
  lastSig = sig;
  if (stick) requestAnimationFrame(() => scrollEnd());
  else jump.hidden = false;
}
