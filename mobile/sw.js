// AURA Mobile service worker: AURA opens INSTANTLY from the copy saved on the phone - with the PC off, away
// from home, on any network - and shows push notifications from AURA on the PC. API calls are never cached.
//
// The start-up files are served from the phone's cache first, then refreshed from the PC in the background
// (with a short time limit). Never wait for the PC before showing AURA: when the PC is off behind the VPN,
// requests to it don't fail - they hang, and the screen would stay black.
const VERSION = 'aura-mobile-v10';
// Only the tiny start-up files are kept here - the app itself is ONE pack saved by boot.js in the phone's storage.
const SHELL = ['/', '/index.html', '/boot.js', '/src/errors.js', '/manifest.webmanifest', '/icons/icon-192.png', '/icons/apple-touch-icon.png'];
const AI_CACHE = 'aura-ai-v1';     // Vision's AI libraries + models (~15 MB): downloaded once, then instant
const AI_HOSTS = ['cdn.jsdelivr.net', 'storage.googleapis.com', 'tfhub.dev', 'www.kaggle.com'];
const NET_LIMIT = 4000;            // how long a background refresh may take

function fetchWithin(req, ms) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  return fetch(req, { cache: 'no-store', signal: ctrl.signal }).finally(() => clearTimeout(timer));
}

/** The saved copy of a start-up file from ANY of AURA's caches (so an update can never leave it missing). */
async function saved(path) {
  for (const name of [VERSION, ...(await caches.keys()).filter((k) => k !== VERSION && k !== AI_CACHE)]) {
    const hit = await (await caches.open(name)).match(path, { ignoreSearch: true });
    if (hit) return hit;
  }
  return null;
}

self.addEventListener('install', (e) => {
  e.waitUntil((async () => {
    const cache = await caches.open(VERSION);
    for (const path of SHELL) {
      try {
        const res = await fetchWithin(path, 15000);
        if (res.ok) { await cache.put(path, res); continue; }
      } catch (err) { /* not reachable - keep the copy we already have */ }
      const old = await saved(path);
      if (old) await cache.put(path, old.clone());
    }
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    const cache = await caches.open(VERSION);
    // only throw old caches away once this one really has the page
    if (await cache.match('/index.html') || await cache.match('/')) {
      for (const k of await caches.keys()) if (k !== VERSION && k !== AI_CACHE) await caches.delete(k);
    }
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method === 'GET' && AI_HOSTS.includes(url.hostname)) {
    e.respondWith(caches.open(AI_CACHE).then(async (c) => {
      const hit = await c.match(e.request);
      if (hit) return hit;
      const res = await fetch(e.request);
      if (res.ok) c.put(e.request, res.clone());
      return res;
    }));
    return;
  }
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/v/') || url.pathname.startsWith('/app-')) return;   // always live
  const isPage = e.request.mode === 'navigate';
  const path = isPage ? '/' : url.pathname;
  if (!isPage && !SHELL.includes(path)) return;                                         // everything else: straight to the network
  e.respondWith((async () => {
    const cache = await caches.open(VERSION);
    const refresh = fetchWithin(e.request, NET_LIMIT).then((res) => {
      if (res.ok) cache.put(path, res.clone());
      return res;
    });
    const hit = (await saved(path)) || (isPage ? await saved('/index.html') : null);
    if (hit) {                                   // show AURA now; the fresh copy is used next time
      e.waitUntil(refresh.catch(() => {}));
      return hit;
    }
    try { return await refresh; } catch (err) { return Response.error(); }          // first ever start: needs the PC once
  })());
});

self.addEventListener('push', (e) => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch (err) { data = { body: e.data && e.data.text() }; }
  const title = data.title || 'AURA';
  // Always show it: iOS revokes push for apps that receive one without showing a notification.
  // (The PC only sends AURA's answers as pushes while the app is closed.)
  e.waitUntil(self.registration.showNotification(title, {
    body: data.body || '', icon: '/icons/icon-192.png', badge: '/icons/icon-192.png',
    tag: data.kind === 'reply' ? 'aura-reply' : undefined, data: { kind: data.kind },
  }));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const target = e.notification.data && e.notification.data.kind === 'reply' ? '/?tab=comms' : '/';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
    for (const w of wins) { if ('focus' in w) return w.focus(); }
    return self.clients.openWindow(target);
  }));
});
