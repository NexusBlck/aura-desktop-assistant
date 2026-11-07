// Live events from the PC (Server-Sent Events read through fetch, so the auth header can be sent).
// Reconnects with backoff and resumes after the last event it saw. Platform-independent.
import { authHeader } from './api.js';

const BACKOFF = [500, 1000, 2000, 4000, 8000, 15000, 20000];

export function connectEvents({ onEvent, onOpen, onClose }) {
  let last = 0;
  let ctrl = null;
  let gen = 0;            // each connection attempt has a number; stale ones exit quietly
  let retry = 0;
  let timer = null;

  async function run(my) {
    if (my !== gen) return;
    ctrl = new AbortController();
    let opened = false;
    try {
      const res = await fetch(`/api/events?after=${last}`, {
        headers: { ...authHeader(), Accept: 'text/event-stream', 'Last-Event-ID': String(last) },
        signal: ctrl.signal, cache: 'no-store',
      });
      if (my !== gen) return;
      if (res.status === 401) { onClose && onClose('unauthorized'); return; }
      if (!res.ok || !res.body) throw new Error(`events ${res.status}`);
      opened = true; retry = 0;
      onOpen && onOpen();
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done || my !== gen) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const block = buf.slice(0, i); buf = buf.slice(i + 2);
          let id = null, data = '';
          for (const line of block.split('\n')) {
            if (line.startsWith('id:')) id = Number(line.slice(3).trim());
            else if (line.startsWith('data:')) data += line.slice(5).trim();
          }
          if (id) last = Math.max(last, id);
          if (data) { try { onEvent(JSON.parse(data)); } catch (e) { console.warn('bad event', e); } }
        }
      }
    } catch (e) { /* network error / aborted - handled below */ }
    if (my !== gen) return;
    onClose && onClose(opened ? 'dropped' : 'unreachable');
    retry = Math.min(retry + 1, BACKOFF.length - 1);
    timer = setTimeout(() => run(my), BACKOFF[retry]);
  }

  function start() {
    gen++;
    clearTimeout(timer);
    if (ctrl) ctrl.abort();
    const my = gen;
    setTimeout(() => run(my), 30);
  }

  start();
  return {
    stop() { gen++; clearTimeout(timer); if (ctrl) ctrl.abort(); },
    /** Reconnect right now (e.g. the app came back to the foreground). */
    kick() { retry = 0; start(); },
    setLast(seq) { last = Math.max(last, seq || 0); },
  };
}
