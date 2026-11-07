// Files - what AURA made on the PC, opened on the phone: web briefings, screenshots, file-search
// results, CDC WONDER workbooks, the PRISMA diagram, and PDFs you've added. Plus: add a PDF from
// the phone (iOS Files / iCloud) so AURA can analyse it.
import { el, h, openPage, button, panel, empty, loading, toast } from '../../ui/components.js';
import { fmtSize, whenOf } from '../../core/format.js';
import * as haptics from '../../platform/haptics.js';

const SOURCES = [
  ['briefings', 'WEB BRIEFINGS', 'Research pages AURA built ("bring up everything from the web about …")'],
  ['screenshots', 'SCREENSHOTS', 'Taken with "take a screenshot"'],
  ['search', 'FILE SEARCHES', 'Results of "bring up everything I have on …"'],
  ['cdc', 'CDC WONDER', 'Extraction folders and Excel workbooks'],
  ['uploads', 'ADDED FROM PHONE', 'PDFs and files you sent from this phone'],
];

const VIEWABLE = /\.(html?|png|jpe?g|gif|webp|txt|md)$/i;       // shown inside the app
const BROWSER = /\.pdf$/i;                                  // iOS shows PDFs properly only in its own viewer

export function open(ctx, arg) {
  if (arg && arg.kind) return openFolder(ctx, arg.kind, arg.path || '', arg.title || arg.kind.toUpperCase());
  openPage('FILES', (body) => {
    const ul = h('ul', { class: 'list' });
    for (const [kind, title, sub] of SOURCES) {
      const li = h('li', { class: 'link', role: 'button' }, h('div', { class: 'txt' }, el('div', null, title), el('div', 'meta', sub)), el('span', 'chev', '›'));
      li.onclick = () => openFolder(ctx, kind, '', title);
      ul.append(li);
    }
    const prisma = h('li', { class: 'link', role: 'button' }, h('div', { class: 'txt' }, el('div', null, 'PRISMA DIAGRAM'), el('div', 'meta', 'The last flow diagram AURA generated')), el('span', 'chev', '›'));
    prisma.onclick = () => openViewer(ctx, { kind: 'aura', path: 'prisma_flow.html', title: 'PRISMA' });
    ul.append(prisma);
    body.append(panel('AURA\'S FILES', 'PC', ul),
      h('div', { class: 'row-btns' }, button('ADD A PDF', () => openUpload(ctx)), button('TAKE A SCREENSHOT', async () => {
        if (await ctx.send('take a screenshot')) toast('Screenshot', 'Taken on your PC - it shows up under SCREENSHOTS in a moment.', 'info', 3500);
      })));
  });
}

function openFolder(ctx, kind, path, title) {
  openPage(title, async (body) => {
    body.append(loading('Loading…'));
    try {
      const { files } = await ctx.api.files(kind, path);
      body.innerHTML = '';
      if (!files.length) { body.append(empty('Nothing here yet.')); return; }
      const ul = h('ul', { class: 'list' });
      for (const f of files) {
        const li = h('li', { class: 'link', role: 'button' },
          el('span', 'kind', f.dir ? 'DIR' : (f.name.split('.').pop() || 'FILE').slice(0, 4).toUpperCase()),
          h('div', { class: 'txt' }, el('div', null, f.name), el('div', 'meta', `${whenOf(f.time)}${f.dir ? '' : ' · ' + fmtSize(f.size)}`)),
          el('span', 'chev', '›'));
        li.onclick = () => { haptics.tap(); f.dir ? openFolder(ctx, kind, f.path, f.name) : openViewer(ctx, { kind, path: f.path, title: f.name }); };
        ul.append(li);
      }
      body.append(ul);
    } catch (e) { body.innerHTML = ''; body.append(empty(e.message)); }
  });
}

/** Opens one file from the PC: shown inside the app when the phone can display it, else downloaded. */
export async function openViewer(ctx, { kind, path, title, link = null }) {
  if (link) {
    path = decodeURIComponent(link.split('/').pop() || title || 'file');     // a link AURA already made (e.g. a found file)
  } else {
    try { link = (await ctx.api.view(kind, path)).url; } catch (e) { toast("Couldn't open it", e.message, 'error'); return; }
  }
  const name = title || path;
  if (BROWSER.test(path)) { window.open(link, '_blank', 'noopener'); return; }
  if (!VIEWABLE.test(path)) {
    // not viewable in the app: hand it to iOS (Files / share sheet)
    const a = h('a', { href: link, download: name.split(/[\\/]/).pop() });
    document.body.append(a); a.click(); a.remove();
    return;
  }
  openPage(String(name).toUpperCase(), (body, page) => {
    const frame = /\.(png|jpe?g|gif|webp)$/i.test(path)
      ? h('div', { style: 'overflow:auto;flex:1' }, h('img', { src: link, alt: name, style: 'width:100%;display:block' }))
      : h('iframe', { class: 'viewer-frame', src: link, title: name, sandbox: 'allow-same-origin allow-popups allow-popups-to-escape-sandbox' });
    body.append(frame);
    page.actions.append(h('a', { class: 'btn', href: link, download: String(name).split(/[\\/]/).pop(), 'aria-label': 'Save to phone' }, 'SAVE'));
  }, { flush: true });
}

export function openUpload(ctx) {
  const input = h('input', { type: 'file', accept: 'application/pdf,.pdf,.docx,.txt,.pptx,.xlsx', hidden: true });
  document.body.append(input);
  input.onchange = async () => {
    const f = input.files && input.files[0];
    input.remove();
    if (!f) return;
    if (f.size > 25 * 1024 * 1024) { toast('Too big', 'Files up to 25 MB can be sent to AURA.', 'error'); return; }
    toast('Uploading', `Sending ${f.name} to your PC…`, 'info', 3000);
    try {
      const r = await ctx.api.upload(f);
      haptics.success();
      const base = r.name.replace(/\.[^.]+$/, '');
      toast('Added to AURA', `Saved as ${r.name} in Documents\\AURA Uploads. Try: "summarise the methods of ${base}".`, 'focus', 9000);
    } catch (e) { toast('Upload failed', e.message, 'error'); }
  };
  input.click();
}
