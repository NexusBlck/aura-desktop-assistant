// AURA Mobile starter. The whole app is ONE pack (app_bundle.py) kept in this phone's own storage:
//  * a pack is saved   -> start from it instantly (no network needed), check for an update in the background;
//  * no pack yet       -> download it (retrying until it arrives), save it, start.
// So a flaky VPN moment can never stop AURA from opening again.
(function () {
  var KEY = 'aura.pack';
  var started = false;

  function read() {
    try { var p = JSON.parse(localStorage.getItem(KEY)); return p && p.js && p.version ? p : null; } catch (e) { return null; }
  }
  function save(p) {
    try { localStorage.setItem(KEY, JSON.stringify(p)); return true; } catch (e) { return false; }
  }
  function start(p) {
    if (started) return;
    started = true;
    window.__auraVersion = p.version;
    var st = document.createElement('style');
    st.textContent = p.css;
    document.head.appendChild(st);
    var s = document.createElement('script');
    s.src = URL.createObjectURL(new Blob([p.js], { type: 'text/javascript' }));
    s.onerror = function () { if (window.__auraRepair) window.__auraRepair('pack'); };
    document.body.appendChild(s);
  }
  function getJSON(url, timeout) {
    return new Promise(function (resolve, reject) {
      var x = new XMLHttpRequest();
      x.open('GET', url + (url.indexOf('?') < 0 ? '?' : '&') + 't=' + Date.now(), true);
      x.timeout = timeout || 30000;
      x.onload = function () {
        if (x.status !== 200) return reject(new Error('HTTP ' + x.status));
        try { resolve(JSON.parse(x.responseText)); } catch (e) { reject(e); }
      };
      x.onerror = x.ontimeout = function () { reject(new Error('network')); };
      x.send();
    });
  }
  function download(tries, onTry) {
    var n = 0;
    return new Promise(function (resolve, reject) {
      (function attempt() {
        n++;
        if (onTry) onTry(n);
        getJSON('/app-pack.json', 45000).then(function (p) {
          if (p && p.js && p.version) resolve(p); else throw new Error('bad pack');
        }).catch(function (e) {
          if (n >= tries) return reject(e);
          setTimeout(attempt, Math.min(800 * n, 4000));
        });
      })();
    });
  }
  function screen(text, retry) {
    var d = document.getElementById('aura-boot');
    if (!d) {
      d = document.createElement('div');
      d.id = 'aura-boot';
      d.setAttribute('style', 'position:fixed;inset:0;display:flex;flex-direction:column;gap:18px;align-items:center;justify-content:center;' +
        'background:#02050a;color:#9ef3ff;font:600 13px ui-monospace,Menlo,monospace;letter-spacing:2px;text-align:center;padding:24px;z-index:10');
      document.body.appendChild(d);
    }
    d.innerHTML = '';
    var logo = document.createElement('div');
    logo.textContent = 'AURA';
    logo.setAttribute('style', 'font:900 34px system-ui,sans-serif;letter-spacing:14px;color:#9ef3ff;text-shadow:0 0 18px rgba(58,214,255,.8)');
    var t = document.createElement('div');
    t.textContent = text;
    t.setAttribute('style', 'max-width:320px;line-height:1.7;color:#6f97a6;white-space:pre-line');
    d.appendChild(logo); d.appendChild(t);
    if (retry) {
      var b = document.createElement('button');
      b.textContent = 'TRY AGAIN';
      b.setAttribute('style', 'min-height:44px;padding:0 22px;border:1px solid #3ad6ff;background:rgba(58,214,255,.08);color:#9ef3ff;font:700 12px ui-monospace,Menlo,monospace;letter-spacing:3px');
      b.onclick = function () { location.reload(); };
      d.appendChild(b);
    }
  }

  var cached = read();
  if (cached) {
    start(cached);
    // quietly fetch a newer version for next time
    setTimeout(function () {
      getJSON('/app-version', 15000).then(function (v) {
        if (v && v.version && v.version !== cached.version) {
          return download(3).then(function (p) { save(p); window.__auraUpdateReady = p.version; });
        }
      }).catch(function () { /* offline - fine */ });
    }, 4000);
  } else {
    screen('LOADING…');
    download(8, function (n) { if (n > 1) screen('CONNECTING TO YOUR PC… (TRY ' + n + ')'); }).then(function (p) {
      save(p);
      var d = document.getElementById('aura-boot'); if (d) d.remove();
      start(p);
    }).catch(function () {
      screen("CAN'T REACH YOUR PC.\nIS TAILSCALE ON (ON THIS PHONE) AND AURA RUNNING ON THE PC?", true);
    });
  }
})();
