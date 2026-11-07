// Loaded before the app (classic script): if anything fails to start - e.g. a browser that doesn't
// support something - show it on screen instead of a black page, and send it to the PC's log.
(function () {
  var shown = false;
  function report(msg, where) {
    var text = String(msg || 'Unknown error') + (where ? ' @ ' + where : '');
    try {
      var x = new XMLHttpRequest();
      x.open('POST', '/api/client-log', true);
      x.setRequestHeader('Content-Type', 'application/json');
      x.send(JSON.stringify({ error: text.slice(0, 800), ua: navigator.userAgent }));
    } catch (e) { /* ignore */ }
    if (shown) return;
    shown = true;
    var box = document.createElement('div');
    box.setAttribute('style', 'position:fixed;left:12px;right:12px;bottom:calc(env(safe-area-inset-bottom) + 12px);z-index:999;' +
      'background:#1a0610;border:1px solid #ff5a6a;color:#ffd6dc;padding:12px;font:13px/1.4 ui-monospace,Menlo,monospace;white-space:pre-wrap');
    box.textContent = 'AURA hit a problem starting up (your PC has been told):\n' + text;
    (document.body || document.documentElement).appendChild(box);
  }
  window.addEventListener('error', function (e) {
    report(e.message || (e.target && e.target.src ? 'Failed to load ' + e.target.src : 'Script error'),
      e.filename ? e.filename.replace(location.origin, '') + ':' + e.lineno + ':' + e.colno : '');
  }, true);
  window.addEventListener('unhandledrejection', function (e) {
    var r = e.reason;
    report(r && (r.stack || r.message) ? (r.message + (r.stack ? '\n' + String(r.stack).split('\n').slice(0, 3).join('\n') : '')) : String(r), 'promise');
  });
  // Self-repair: if the app fails to start, throw away the offline copy (service worker + caches) and reload
  // once - a stale or half-updated copy is the usual cause. A second failure shows the error instead.
  function repair(reason) {
    var tried = false;
    try { tried = sessionStorage.getItem('aura.repaired') === '1'; } catch (e) { /* ignore */ }
    if (tried) return false;
    try { sessionStorage.setItem('aura.repaired', '1'); } catch (e) { /* ignore */ }
    var msg = document.createElement('div');
    msg.setAttribute('style', 'position:fixed;inset:0;display:grid;place-items:center;background:#02050a;color:#9ef3ff;z-index:1000;font:600 14px ui-monospace,Menlo,monospace;letter-spacing:2px');
    msg.textContent = 'REPAIRING AURA…';
    (document.body || document.documentElement).appendChild(msg);
    var done = function () { location.reload(); };
    var jobs = [];
    try { if (navigator.serviceWorker) jobs.push(navigator.serviceWorker.getRegistrations().then(function (rs) { return Promise.all(rs.map(function (r) { return r.unregister(); })); })); } catch (e) { /* ignore */ }
    try { localStorage.removeItem('aura.pack'); } catch (e) { /* ignore */ }
    try { if (window.caches) jobs.push(caches.keys().then(function (ks) { return Promise.all(ks.filter(function (k) { return k.indexOf('aura-mobile') === 0; }).map(function (k) { return caches.delete(k); })); })); } catch (e) { /* ignore */ }
    Promise.all(jobs).then(done, done);
    setTimeout(done, 4000);
    return true;
  }
  window.__auraRepair = repair;
  window.addEventListener('error', function (e) {
    var t = e.target;
    if (t && t.tagName === 'SCRIPT' && /\/src\/app\.js/.test(t.src || '')) repair('app.js');
  }, true);
  // if the app never drew anything, repair (once), else report it
  setTimeout(function () {
    var app = document.getElementById('app');
    if (app && !app.children.length && !document.querySelector('.boot') && !document.getElementById('aura-boot')) {
      if (!repair('nothing drawn')) report('The app did not start (nothing was drawn after 8 s).', 'startup');
    } else {
      try { sessionStorage.removeItem('aura.repaired'); } catch (e) { /* ignore */ }
    }
  }, 12000);
})();
