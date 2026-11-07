/* AURA UI skins on the PC (HUD + Mini AURA). Reads the saved choice from AURA (ui_skins.py via pywebview), sets
   data-skin / data-skin-theme on <html> and loads that skin's stylesheets + fonts. With the original HUD chosen it does
   nothing at all. On the HUD it adds a small "UI" button (and Ctrl+Shift+U) that opens the picker. */
(function () {
  var SKINS = [
    { id: 'hud', name: 'AURA HUD', tag: 'The original', themes: [['default', 'Original', ['#02050a', '#07121b', '#d6f6ff', '#3ad6ff']]] },
    { id: 'ledger', name: 'Ledger', tag: 'Editorial', fonts: 'family=Newsreader:ital,wght@0,400;0,500;0,600;1,500&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500',
      themes: [['paper', 'Paper', ['#f3eee4', '#fbf8f1', '#211d18', '#8a2e1d']], ['linen', 'Linen', ['#f4f5f2', '#ffffff', '#141c26', '#1d3a5f']], ['night', 'Night edition', ['#1a1916', '#22211d', '#ece6d8', '#c9a35b']]] },
    { id: 'hmi', name: 'HMI', tag: 'Industrial', fonts: 'family=Barlow:wght@400;500;600&family=Barlow+Condensed:wght@500;600;700&family=JetBrains+Mono:wght@400;600',
      themes: [['daylight', 'Daylight', ['#cfd0cc', '#e4e5e1', '#151617', '#275c9b']], ['nightshift', 'Night shift', ['#1c1f22', '#262a2e', '#d9dde0', '#4a8fd8']], ['hazard', 'Hazard', ['#1a1a1a', '#242424', '#efefef', '#f2c200']]] },
    { id: 'swiss', name: 'Swiss', tag: 'Corporate', fonts: 'family=Archivo:wght@400;500;600;700;800;900&family=Roboto+Mono:wght@400;500',
      themes: [['signal', 'Signal', ['#ffffff', '#ffffff', '#111111', '#e30613']], ['klein', 'Klein', ['#f6f6f3', '#ffffff', '#111111', '#002fa7']], ['noir', 'Noir', ['#0d0d0d', '#161616', '#f2f2f2', '#ff3b30']]] },
    { id: 'console', name: 'Console', tag: 'Operations', fonts: 'family=IBM+Plex+Mono:wght@400;500;600',
      themes: [['amber', 'Amber', ['#120d04', '#1a1307', '#ffb000', '#ffc94d']], ['phosphor', 'Phosphor', ['#04100a', '#08180f', '#3cff6e', '#8bffaa']], ['daylight', 'Daylight', ['#f4f4ef', '#ffffff', '#1d1d1b', '#005fcc']]] },
    { id: 'haven', name: 'Haven', tag: 'Calm & accessible', fonts: 'family=Atkinson+Hyperlegible:wght@400;700&family=Atkinson+Hyperlegible+Mono:wght@400;700',
      themes: [['sand', 'Sand', ['#f3ece3', '#fffaf4', '#3a2e26', '#b85a36']], ['sage', 'Sage', ['#ecf0ea', '#fbfcfa', '#25302a', '#46755a']], ['dusk', 'Dusk', ['#211d1a', '#2e2925', '#f1e7dc', '#e3a468']]] }
  ];
  var IS_MINI = /mini\.html/i.test(location.pathname);
  var root = document.documentElement, cur = { skin: 'hud', theme: 'default' };
  function byId(id) { for (var i = 0; i < SKINS.length; i++) if (SKINS[i].id === id) return SKINS[i]; return SKINS[0]; }
  function link(id, href) {
    if (document.getElementById(id)) return;
    var l = document.createElement('link'); l.rel = 'stylesheet'; l.id = id; l.href = href;
    document.head.appendChild(l);
  }
  function apply(v) {
    var s = byId(v && v.skin);
    cur = { skin: s.id, theme: v && v.theme || s.themes[0][0] };
    if (s.id === 'hud') { root.removeAttribute('data-skin'); root.removeAttribute('data-skin-theme'); return; }
    if (s.fonts) link('skin-font-' + s.id, 'https://fonts.googleapis.com/css2?display=swap&' + s.fonts);
    if (IS_MINI) link('skin-mini', 'skins/mini.css');
    else {
      link('skin-app-base', 'skins/app/base.css');
      ['ledger', 'hmi', 'swiss', 'console', 'haven'].forEach(function (n) { link('skin-app-' + n, 'skins/app/' + n + '.css'); });
      link('skin-hud', 'skins/hud.css');
    }
    root.setAttribute('data-skin', s.id);
    root.setAttribute('data-skin-theme', cur.theme);
  }
  function api() { return window.pywebview && window.pywebview.api; }
  function load() {
    var a = api();
    if (!a || !a.ui_skin_get) return false;
    a.ui_skin_get().then(function (v) { if (v && (v.skin !== cur.skin || v.theme !== cur.theme)) apply(v); });
    return true;
  }
  // Ask AURA for the saved choice as soon as the window's API is ready (and Mini keeps in step with the HUD)
  window.addEventListener('pywebviewready', load);
  var tries = 0, t = setInterval(function () { if (load() || ++tries > 40) clearInterval(t); }, 250);
  if (IS_MINI) setInterval(load, 4000);

  // ---------------------------------------------------------------- the picker (HUD only)
  if (IS_MINI) return;
  var css = '#skin-btn{margin-left:14px;border:1px solid var(--line);background:transparent;color:var(--text-dim,#6f97a6);font:600 10px var(--f-mono,monospace);letter-spacing:2px;padding:3px 10px;cursor:pointer}' +
    '#skin-btn:hover{color:var(--text);border-color:var(--line-strong)}' +
    '#skin-pick{position:fixed;inset:0;z-index:999;background:rgba(0,0,0,.55);display:none;align-items:center;justify-content:center}' +
    '#skin-pick.open{display:flex}#skin-pick .box{width:min(980px,94vw);max-height:90vh;overflow:auto;background:var(--panel-solid,#07121b);border:1px solid var(--line-strong);padding:20px 22px;color:var(--text);font-family:var(--f-ui,sans-serif);border-radius:var(--radius,0)}' +
    '#skin-pick h2{margin:0 0 4px;font:700 18px var(--f-ui,sans-serif);letter-spacing:0}#skin-pick p.s{margin:0 0 16px;color:var(--text-dim,#6f97a6);font-size:13px}' +
    '#skin-pick .grid6{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}#skin-pick .c{border:1px solid var(--line);padding:12px;border-radius:var(--radius,0)}' +
    '#skin-pick .c.on{border-color:var(--cyan);box-shadow:inset 0 0 0 1px var(--cyan)}#skin-pick .c b{font-size:15px}#skin-pick .c span.tg{color:var(--text-dim,#6f97a6);font-size:12px;margin-left:6px}' +
    '#skin-pick .sw{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}#skin-pick .sw button{display:flex;align-items:center;gap:6px;border:1px solid var(--line);background:transparent;color:var(--text);padding:5px 9px 5px 5px;cursor:pointer;font:500 12px var(--f-ui,sans-serif);border-radius:var(--radius,0)}' +
    '#skin-pick .sw button.on{border-color:var(--cyan);font-weight:700}#skin-pick .sw i{display:inline-flex}#skin-pick .sw i em{width:9px;height:18px;display:block}' +
    '#skin-pick .x{float:right;background:none;border:0;color:var(--text-dim,#6f97a6);font-size:20px;cursor:pointer}';
  var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  var pick;
  function render() {
    var box = pick.querySelector('.box'); box.innerHTML = '';
    var x = el('button', 'x', '×'); x.onclick = close; box.appendChild(x);
    box.appendChild(el('h2', null, 'Interface'));
    box.appendChild(el('p', 's', 'Choose how AURA looks on this PC. It changes instantly and stays until you change it. (Ctrl+Shift+U)'));
    var g = el('div', 'grid6');
    SKINS.forEach(function (s) {
      var c = el('div', 'c' + (cur.skin === s.id ? ' on' : ''));
      c.appendChild(el('b', null, s.name)); c.appendChild(el('span', 'tg', s.tag));
      var sw = el('div', 'sw');
      s.themes.forEach(function (th) {
        var b = el('button', cur.skin === s.id && cur.theme === th[0] ? 'on' : '');
        var i = el('i'); th[2].forEach(function (col) { var e = el('em'); e.style.background = col; i.appendChild(e); });
        b.appendChild(i); b.appendChild(document.createTextNode(th[1]));
        b.onclick = function () {
          apply({ skin: s.id, theme: th[0] });
          var a = api(); if (a && a.ui_skin_set) a.ui_skin_set(s.id, th[0]);
          render();
        };
        sw.appendChild(b);
      });
      c.appendChild(sw); g.appendChild(c);
    });
    box.appendChild(g);
  }
  function open() { if (!pick) { pick = el('div'); pick.id = 'skin-pick'; pick.appendChild(el('div', 'box')); pick.onclick = function (e) { if (e.target === pick) close(); }; document.body.appendChild(pick); } render(); pick.classList.add('open'); }
  function close() { if (pick) pick.classList.remove('open'); }
  document.addEventListener('keydown', function (e) { if (e.ctrlKey && e.shiftKey && (e.key === 'U' || e.key === 'u')) { e.preventDefault(); open(); } if (e.key === 'Escape') close(); });
  document.addEventListener('DOMContentLoaded', function () {
    var strip = document.querySelector('.strip');
    if (!strip) return;
    var b = el('button', null, 'UI'); b.id = 'skin-btn'; b.title = 'Change the interface (Ctrl+Shift+U)'; b.onclick = open;
    strip.appendChild(b);
  });
})();
