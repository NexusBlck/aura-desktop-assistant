// Formatting helpers shared by every screen (same rules as the HUD). Platform-independent.
export const pad = (n) => String(n).padStart(2, '0');

export function fmtRate(bps) {
  if (bps == null) return '--';
  const b = bps * 8;
  if (b >= 1e6) return (b / 1e6).toFixed(1) + ' Mbps';
  if (b >= 1e3) return (b / 1e3).toFixed(0) + ' Kbps';
  return b.toFixed(0) + ' bps';
}

export function fmtDuration(sec) {
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

export function fmtCountdown(sec) {
  if (sec <= 0) return 'NOW';
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${pad(m)}m`;
  return `${m}:${pad(s)}`;
}

export function fmtSize(bytes) {
  if (bytes == null) return '';
  if (bytes >= 1e9) return (bytes / 1e9).toFixed(1) + ' GB';
  if (bytes >= 1e6) return (bytes / 1e6).toFixed(1) + ' MB';
  if (bytes >= 1e3) return Math.round(bytes / 1e3) + ' KB';
  return bytes + ' B';
}

export const timeOf = (t) => new Date((t || Date.now() / 1000) * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
export const whenOf = (t) => new Date(t * 1000).toLocaleString([], { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });

/** Sentence-sized pieces for speech (same idea as voice.py's _chunks): replies start playing
 *  quickly and long answers stream. */
export function speechChunks(text, limit = 220) {
  const clean = String(text || '')
    .replace(/https?:\/\/\S+|www\.\S+/g, 'a link')
    .replace(/\[(.*?)\]\(.*?\)/g, '$1')
    .replace(/[*_#`>|~]+/g, '')
    .replace(/^\s*[-•]\s+/gm, '')
    .replace(/\s*\n+\s*/g, '. ')
    .replace(/\.\./g, '.')
    .replace(/\s{2,}/g, ' ')
    .trim();
  if (!clean) return [];
  const out = [];
  let cur = '';
  for (const raw of clean.match(/[^.!?]+[.!?]*/g) || []) {
    const s = raw.trim();
    if (!s) continue;
    if (cur && cur.length + s.length + 1 > limit) { out.push(cur); cur = s; } else cur = (cur + ' ' + s).trim();
    while (cur.length > limit) {
      let cut = Math.max(cur.lastIndexOf(', ', limit), cur.lastIndexOf(' ', limit));
      if (cut < 40) cut = limit;
      out.push(cur.slice(0, cut).replace(/[ ,]+$/, ''));
      cur = cur.slice(cut).replace(/^[ ,]+/, '');
    }
  }
  if (cur) out.push(cur);
  return out;
}
