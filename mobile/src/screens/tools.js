// Opens AURA's tools as full-screen pages (or sheets). Each tool is its own module.
import { toast } from '../ui/components.js';

let ctx = null;
export function bindTools(c) { ctx = c; }

const loaders = {
  emails: () => import('./tools/emails.js'),
  dashboard: () => import('./tools/dashboard.js'),
  vision: () => import('./tools/vision.js'),
  explain: () => import('./tools/explain.js'),
  cdc: () => import('./tools/cdc.js'),
  files: () => import('./tools/files.js'),
  viewer: () => import('./tools/files.js'),
  upload: () => import('./tools/files.js'),
  log: () => import('./tools/log.js'),
  journal: () => import('./tools/journal.js'),
  settings: () => import('./tools/settings.js'),
  nexus: () => import('./tools/nexus/hub.js'),
  'nexus-live': () => import('./tools/nexus/live.js'),
  'nexus-babel': () => import('./tools/nexus/babel.js'),
  'nexus-echo': () => import('./tools/nexus/echo.js'),
  'nexus-radar': () => import('./tools/nexus/radar.js'),
  'nexus-pulse': () => import('./tools/nexus/pulse.js'),
  'nexus-link': () => import('./tools/nexus/link.js'),
  'nexus-protocols': () => import('./tools/nexus/protocols.js'),
  studio: () => import('./tools/studio.js'),
  spatial: () => import('./tools/spatial.js'),
  overwatch: () => import('./tools/overwatch.js'),
  appearance: () => import('./tools/appearance.js'),
  air: () => import('./tools/air.js'),
};

export async function openTool(name, arg) {
  const load = loaders[name];
  if (!load || !ctx) return;
  try {
    const mod = await load();
    const fn = mod[{ viewer: 'openViewer', upload: 'openUpload' }[name] || 'open'];
    fn(ctx, arg);
  } catch (e) {
    console.error(e);
    toast("Couldn't open that", String(e.message || e), 'error');
  }
}
