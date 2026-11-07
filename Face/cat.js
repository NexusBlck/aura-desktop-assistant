// cat.js
// AURA's cat - one animated SVG cat shared by the HUD (where it lives) and
// the walker window (when it comes out to explain something on screen).
//
//   const cat = AuraCat.mount(element, { onClick })
//   cat.setState('idle' | 'walk' | 'sit' | 'think' | 'talk' | 'listen' | 'sleep')
//   cat.setFacing('right' | 'left')
//   cat.pet()      // happy squint + floating heart
//
// Everything is drawn in AURA's neon-cyan style; all motion is CSS, with a
// little JS for the random parts (blinks, ear twitches, glances).
(function () {
  const CSS = `
  .aura-cat { position: relative; width: 100%; height: 100%; user-select: none; -webkit-user-select: none; }
  .aura-cat svg { width: 100%; height: 100%; overflow: visible; display: block;
    filter: drop-shadow(0 0 3px rgba(58,214,255,.75)) drop-shadow(0 0 10px rgba(58,214,255,.25)); }
  .aura-cat .flip { transform-origin: 80px 70px; transition: transform .18s ease; }
  .aura-cat.left .flip { transform: scaleX(-1); }

  .aura-cat .fur { fill: #0a1a26; stroke: #3ad6ff; stroke-width: 2.4; stroke-linejoin: round; stroke-linecap: round; }
  .aura-cat .fur-far { fill: #061019; stroke: #1f7f99; stroke-width: 2.2; stroke-linejoin: round; }
  .aura-cat .stripe { fill: none; stroke: #1b6a80; stroke-width: 2; stroke-linecap: round; }
  .aura-cat .inner-ear { fill: rgba(255,120,190,.35); }
  .aura-cat .nose { fill: #ff8fc8; }
  .aura-cat .line { fill: none; stroke: #9ef3ff; stroke-width: 1.6; stroke-linecap: round; }
  .aura-cat .whisker { fill: none; stroke: rgba(158,243,255,.55); stroke-width: 1; stroke-linecap: round; }
  .aura-cat .iris { fill: #9ef3ff; }
  .aura-cat .pupil { fill: #02121c; transition: transform .25s ease; }
  .aura-cat .shine { fill: #ffffff; }
  .aura-cat .lid { fill: none; stroke: #9ef3ff; stroke-width: 2; stroke-linecap: round; opacity: 0; }

  /* pose groups crossfade */
  .aura-cat .pose { opacity: 0; transition: opacity .22s ease; }
  .aura-cat.p-walk .pose-walk, .aura-cat.p-sit .pose-sit, .aura-cat.p-sleep .pose-sleep { opacity: 1; }

  /* the head moves between poses */
  .aura-cat .head-pos { transition: transform .3s cubic-bezier(.3,1.4,.5,1); transform: translate(80px, 52px); }
  .aura-cat.p-walk .head-pos { transform: translate(116px, 60px); }
  .aura-cat.p-sleep .head-pos { transform: translate(52px, 102px) rotate(-12deg) scale(.92); }
  .aura-cat .head { transform-box: view-box; }

  /* breathing */
  .aura-cat .breathe { transform-box: fill-box; transform-origin: 50% 100%; animation: ac-breathe 3.2s ease-in-out infinite; }
  .aura-cat.p-sleep .breathe { animation-duration: 4.4s; }
  @keyframes ac-breathe { 50% { transform: scaleY(1.035); } }

  /* tails */
  .aura-cat .tail { fill: none; stroke: #3ad6ff; stroke-width: 7; stroke-linecap: round; }
  .aura-cat .tail-core { fill: none; stroke: #0a1a26; stroke-width: 3.2; stroke-linecap: round; }
  .aura-cat .tail-walk { transform-origin: 38px 80px; animation: ac-tail-walk 1s ease-in-out infinite; }
  @keyframes ac-tail-walk { 50% { transform: rotate(-14deg); } }
  .aura-cat .tail-sit { transform-origin: 100px 122px; animation: ac-tail-sit 3.4s ease-in-out infinite; }
  @keyframes ac-tail-sit { 0%,70%,100% { transform: rotate(0); } 80% { transform: rotate(-9deg); } 90% { transform: rotate(5deg); } }
  .aura-cat.s-think .tail-sit { animation-duration: 1.1s; }
  .aura-cat.s-listen .tail-sit { animation: none; transform: rotate(-16deg); }

  /* walking legs */
  .aura-cat .leg { transform-box: fill-box; transform-origin: 50% 8%; }
  .aura-cat.s-walk .leg { animation: ac-step .46s ease-in-out infinite; }
  .aura-cat.s-walk .leg.b { animation-delay: -.23s; }
  @keyframes ac-step { 0%,100% { transform: rotate(24deg); } 50% { transform: rotate(-24deg); } }
  .aura-cat.s-walk .bob { animation: ac-bob .23s ease-in-out infinite; }
  @keyframes ac-bob { 50% { transform: translateY(-2.5px); } }

  /* ears */
  .aura-cat .ear { transform-box: fill-box; transform-origin: 50% 100%; transition: transform .2s ease; }
  .aura-cat .ear.twitch { animation: ac-twitch .35s ease; }
  @keyframes ac-twitch { 40% { transform: rotate(-14deg) scaleY(.9); } }
  .aura-cat.s-listen .ear { transform: scale(1.14) translateY(-2px); }
  .aura-cat.p-sleep .ear { transform: rotate(-8deg) scaleY(.85); }

  /* eyes */
  .aura-cat .eye-open { transform-box: fill-box; transform-origin: 50% 50%; transition: transform .2s ease, opacity .15s; }
  .aura-cat .eye-open.blink { transform: scaleY(.08); }
  .aura-cat.s-listen .eye-open { transform: scale(1.18); }
  .aura-cat.p-sleep .eye-open, .aura-cat.happy .eye-open { opacity: 0; }
  .aura-cat.p-sleep .lid.sleepy, .aura-cat.happy .lid.joy { opacity: 1; }
  .aura-cat.s-think .pupil { transform: translate(1.6px, -2.4px); }

  /* mouth */
  .aura-cat .mouth-open { fill: #2a0d1c; stroke: #9ef3ff; stroke-width: 1.4; transform-box: fill-box; transform-origin: 50% 0%; transform: scaleY(0); }
  .aura-cat.s-talk .mouth-open { animation: ac-talk .26s ease-in-out infinite alternate; }
  @keyframes ac-talk { from { transform: scaleY(.15); } to { transform: scaleY(1); } }

  /* head tilt while thinking / listening */
  .aura-cat .tilt { transition: transform .35s ease; }
  .aura-cat.s-think .tilt { transform: rotate(-9deg); }
  .aura-cat.s-listen .tilt { transform: rotate(6deg); }

  /* thought bubble */
  .aura-cat .thought { opacity: 0; transition: opacity .25s; }
  .aura-cat.s-think .thought { opacity: 1; }
  .aura-cat .thought circle.d { fill: #9ef3ff; animation: ac-dot 1.2s ease-in-out infinite; }
  .aura-cat .thought circle.d:nth-of-type(2) { animation-delay: .2s; }
  .aura-cat .thought circle.d:nth-of-type(3) { animation-delay: .4s; }
  @keyframes ac-dot { 0%,100% { opacity: .25; } 40% { opacity: 1; } }

  /* sleep Zzz */
  .aura-cat .zzz { opacity: 0; font: 700 13px 'Orbitron','Segoe UI',sans-serif; fill: #9ef3ff; }
  .aura-cat.p-sleep .zzz { animation: ac-z 3s ease-in infinite; }
  .aura-cat.p-sleep .zzz.z2 { animation-delay: 1s; } .aura-cat.p-sleep .zzz.z3 { animation-delay: 2s; }
  @keyframes ac-z { 0% { opacity: 0; transform: translate(0,0) scale(.6); } 20% { opacity: 1; } 100% { opacity: 0; transform: translate(14px,-30px) scale(1.2); } }

  /* hearts when petted */
  .aura-cat .heart { position: absolute; pointer-events: none; color: #ff8fc8; font-size: 16px; text-shadow: 0 0 8px rgba(255,143,200,.8);
    animation: ac-heart 1.3s ease-out forwards; }
  @keyframes ac-heart { from { opacity: 1; transform: translate(-50%, 0) scale(.7); } to { opacity: 0; transform: translate(-50%, -46px) scale(1.25); } }

  /* hop (used when it jumps off the HUD / lands) */
  .aura-cat.hop svg { animation: ac-hop .38s ease-out; }
  @keyframes ac-hop { 40% { transform: translateY(-14px); } }

  /* ---- travel poses (used by the desktop stage) ---- */
  .aura-cat .pose-root { transform-origin: 80px 100px; transition: transform .2s ease; }
  /* climbing: side-view body turned upright, legs reaching alternately */
  .aura-cat.s-climb .pose-root { transform: rotate(-78deg) translate(-6px, -4px); }
  .aura-cat.s-climb .leg { animation: ac-step .5s ease-in-out infinite; }
  .aura-cat.s-climb .leg.b { animation-delay: -.25s; }
  /* airborne: legs tucked, tail streaming */
  .aura-cat.s-air .leg { transform: rotate(38deg); }
  .aura-cat.s-air .leg.b { transform: rotate(-30deg); }
  .aura-cat.s-air .pose-root { transform: rotate(-8deg); }
  .aura-cat.s-air .tail-walk { animation-duration: .35s; }
  /* pushing: low and leaning in, slow heavy steps */
  .aura-cat.s-push .pose-root { transform: rotate(9deg) translate(4px, 6px); }
  .aura-cat.s-push .leg { animation: ac-step .8s ease-in-out infinite; }
  .aura-cat.s-push .leg.b { animation-delay: -.4s; }
  /* hanging from a balloon / umbrella / parachute: gentle swing */
  .aura-cat.s-hang .pose-root { transform-origin: 80px 20px; animation: ac-swing 1.6s ease-in-out infinite; }
  @keyframes ac-swing { 0%,100% { transform: rotate(-7deg); } 50% { transform: rotate(7deg); } }
  /* tumbling */
  .aura-cat.s-tumble .pose-root { transform-origin: 80px 80px; animation: ac-spin .5s linear infinite; }
  @keyframes ac-spin { to { transform: rotate(360deg); } }
  /* sneaking: crouched, slow */
  .aura-cat.s-sneak .pose-root { transform: scaleY(.82) translateY(16px); }
  .aura-cat.s-sneak .leg { animation: ac-step .9s ease-in-out infinite; }
  .aura-cat.s-sneak .leg.b { animation-delay: -.45s; }
  /* riding (skateboard / elevator / beanstalk): standing still, tail up */
  .aura-cat.s-ride .tail-walk { animation: ac-tail-walk .6s ease-in-out infinite; }
  /* squash + stretch on landing */
  .aura-cat.land svg { animation: ac-land .32s ease-out; }
  @keyframes ac-land { 0% { transform: scale(1.15, .8) translateY(8px); } 60% { transform: scale(.95, 1.06); } 100% { transform: none; } }

  /* ---- outfits ---- */
  .aura-cat .acc { display: none; }
  .aura-cat.acc-hat .acc-hat, .aura-cat.acc-shades .acc-shades, .aura-cat.acc-scarf .acc-scarf,
  .aura-cat.acc-bow .acc-bow, .aura-cat.acc-crown .acc-crown, .aura-cat.acc-headphones .acc-headphones { display: inline; }
  `;

  // viewBox 0 0 160 140, ground at y = 128, facing right.
  const SVG = `
  <svg viewBox="0 0 160 140" xmlns="http://www.w3.org/2000/svg">
   <g class="flip">
    <g class="pose-root">
    <g class="bob">

     <!-- ===== WALK pose (side view) ===== -->
     <g class="pose pose-walk">
      <g class="tail-walk">
        <path class="tail" d="M40 82 C 20 80, 12 62, 20 42 C 23 35, 30 34, 31 40"/>
        <path class="tail-core" d="M40 82 C 20 80, 12 62, 20 42 C 23 35, 30 34, 31 40"/>
      </g>
      <rect class="fur-far leg b" x="51" y="92" width="10" height="35" rx="5"/>
      <rect class="fur-far leg a" x="97" y="92" width="10" height="35" rx="5"/>
      <ellipse class="fur" cx="76" cy="86" rx="40" ry="21"/>
      <path class="stripe" d="M62 68 q4 8 0 14 M74 66 q4 8 0 15 M86 67 q4 8 0 14"/>
      <rect class="fur leg a" x="42" y="94" width="11" height="34" rx="5.5"/>
      <rect class="fur leg b" x="89" y="94" width="11" height="34" rx="5.5"/>
     </g>

     <!-- ===== SIT pose (front, three-quarter) ===== -->
     <g class="pose pose-sit">
      <g class="tail-sit">
        <path class="tail" d="M98 120 C 122 126, 138 112, 132 92 C 130 86, 124 86, 124 92"/>
        <path class="tail-core" d="M98 120 C 122 126, 138 112, 132 92 C 130 86, 124 86, 124 92"/>
      </g>
      <g class="breathe">
        <path class="fur" d="M80 66 C 56 66, 50 96, 54 114 C 56 124, 64 128, 80 128 C 96 128, 104 124, 106 114 C 110 96, 104 66, 80 66 Z"/>
        <path class="stripe" d="M70 84 q10 6 20 0 M68 96 q12 7 24 0"/>
      </g>
      <ellipse class="fur" cx="70" cy="125" rx="8" ry="5"/>
      <ellipse class="fur" cx="90" cy="125" rx="8" ry="5"/>
     </g>

     <!-- ===== SLEEP pose (curled up) ===== -->
     <g class="pose pose-sleep">
      <g class="breathe">
        <ellipse class="fur" cx="82" cy="112" rx="44" ry="18"/>
        <path class="stripe" d="M80 98 q5 7 0 14 M94 99 q5 7 0 13 M108 102 q4 6 0 11"/>
      </g>
      <path class="tail" d="M124 116 C 122 132, 70 134, 44 124"/>
      <path class="tail-core" d="M124 116 C 122 132, 70 134, 44 124"/>
      <text class="zzz z1" x="70" y="78">z</text><text class="zzz z2" x="78" y="70">z</text><text class="zzz z3" x="86" y="62">Z</text>
     </g>

     <!-- ===== HEAD (shared by every pose) ===== -->
     <g class="head-pos"><g class="tilt"><g class="head">
      <g class="ear el"><path class="fur" d="M-22 -8 L-17 -34 L-3 -20 Z"/><path class="inner-ear" d="M-18 -13 L-15.5 -27 L-8 -19 Z"/></g>
      <g class="ear er"><path class="fur" d="M22 -8 L17 -34 L3 -20 Z"/><path class="inner-ear" d="M18 -13 L15.5 -27 L8 -19 Z"/></g>
      <ellipse class="fur" cx="0" cy="0" rx="26" ry="22"/>
      <path class="stripe" d="M-5 -19 v6 M0 -21 v7 M5 -19 v6"/>
      <g class="eye-open"><ellipse class="iris" cx="-9.5" cy="-2" rx="5" ry="6.4"/><ellipse class="pupil" cx="-9.5" cy="-1.6" rx="2" ry="4.6"/><circle class="shine" cx="-7.8" cy="-4.6" r="1.3"/></g>
      <g class="eye-open"><ellipse class="iris" cx="9.5" cy="-2" rx="5" ry="6.4"/><ellipse class="pupil" cx="9.5" cy="-1.6" rx="2" ry="4.6"/><circle class="shine" cx="11.2" cy="-4.6" r="1.3"/></g>
      <path class="lid sleepy" d="M-14 -1 q4.5 3.5 9 0 M5 -1 q4.5 3.5 9 0"/>
      <path class="lid joy" d="M-14 0 q4.5 -5.5 9 0 M5 0 q4.5 -5.5 9 0"/>
      <path class="nose" d="M-2.6 6 h5.2 l-2.6 3 Z"/>
      <path class="line" d="M0 9 v1.6 M-5 10 q2.5 3.6 5 0.6 q2.5 3 5 -0.6"/>
      <ellipse class="mouth-open" cx="0" cy="14" rx="3.6" ry="3.4"/>
      <path class="whisker" d="M-10 8 L-32 4 M-10 10.5 L-31 12 M10 8 L32 4 M10 10.5 L31 12"/>
      <g class="thought">
        <circle cx="26" cy="-30" r="2" fill="#9ef3ff" opacity=".6"/>
        <circle cx="32" cy="-38" r="3" fill="#9ef3ff" opacity=".7"/>
        <rect x="30" y="-62" width="34" height="18" rx="9" fill="#07121b" stroke="#3ad6ff" stroke-width="1.5"/>
        <circle class="d" cx="40" cy="-53" r="2.2"/><circle class="d" cx="47" cy="-53" r="2.2"/><circle class="d" cx="54" cy="-53" r="2.2"/>
      </g>
      <!-- outfits (one picked at random per trip) -->
      <g class="acc acc-hat"><path d="M-17 -26 h34 l-3 -4 h-28 Z" fill="#ff5a6a" stroke="#ffd0d6" stroke-width="1"/><path d="M-12 -30 q12 -18 24 0 Z" fill="#ff5a6a" stroke="#ffd0d6" stroke-width="1"/></g>
      <g class="acc acc-shades"><rect x="-17" y="-7" width="13" height="8" rx="3" fill="#02121c" stroke="#9ef3ff" stroke-width="1.2"/><rect x="4" y="-7" width="13" height="8" rx="3" fill="#02121c" stroke="#9ef3ff" stroke-width="1.2"/><path d="M-4 -4 h8" stroke="#9ef3ff" stroke-width="1.2"/></g>
      <g class="acc acc-scarf"><path d="M-18 16 q18 9 36 0 v6 q-18 9 -36 0 Z" fill="#ffb547" stroke="#ffe2b0" stroke-width="1"/><path d="M10 20 l5 14 l6 -2 l-4 -13 Z" fill="#ffb547" stroke="#ffe2b0" stroke-width="1"/></g>
      <g class="acc acc-bow"><path d="M0 19 l-9 -6 v12 Z M0 19 l9 -6 v12 Z" fill="#a78bfa" stroke="#ddd0ff" stroke-width="1"/><circle cx="0" cy="19" r="2.5" fill="#ddd0ff"/></g>
      <g class="acc acc-crown"><path d="M-13 -24 l4 -12 l5 7 l4 -10 l4 10 l5 -7 l4 12 Z" fill="#ffd34d" stroke="#fff3b0" stroke-width="1"/></g>
      <g class="acc acc-headphones"><path d="M-24 -2 q0 -32 24 -32 q24 0 24 32" fill="none" stroke="#4ef0a3" stroke-width="3"/><rect x="-29" y="-6" width="9" height="14" rx="3" fill="#4ef0a3"/><rect x="20" y="-6" width="9" height="14" rx="3" fill="#4ef0a3"/></g>
     </g></g></g>

    </g>
    </g>
   </g>
  </svg>`;

  const POSE_FOR_STATE = { idle: 'sit', sit: 'sit', think: 'sit', talk: 'sit', listen: 'sit', walk: 'walk', sleep: 'sleep',
                           climb: 'walk', air: 'walk', push: 'walk', hang: 'sit', tumble: 'walk', sneak: 'walk', ride: 'sit' };
  const OUTFITS = ['hat', 'shades', 'scarf', 'bow', 'crown', 'headphones'];

  function injectCss() {
    if (document.getElementById('aura-cat-css')) return;
    const s = document.createElement('style');
    s.id = 'aura-cat-css';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  function mount(container, opts = {}) {
    injectCss();
    const root = document.createElement('div');
    root.className = 'aura-cat p-sit s-idle';
    root.innerHTML = SVG;
    container.appendChild(root);

    let state = 'idle';
    const eyes = root.querySelectorAll('.eye-open');
    const ears = root.querySelectorAll('.ear');
    const pupils = root.querySelectorAll('.pupil');

    function setState(next) {
      if (!POSE_FOR_STATE[next]) next = 'idle';
      state = next;
      root.className = root.className.replace(/\b[ps]-\w+/g, '').trim() + ` p-${POSE_FOR_STATE[next]} s-${next}`;
    }

    function setFacing(dir) { root.classList.toggle('left', dir === 'left'); }

    function blink() {
      eyes.forEach(e => e.classList.add('blink'));
      setTimeout(() => eyes.forEach(e => e.classList.remove('blink')), 130);
    }

    function twitch() {
      const ear = ears[Math.random() < 0.5 ? 0 : 1];
      ear.classList.remove('twitch'); void ear.offsetWidth; ear.classList.add('twitch');
    }

    function glance() {
      if (state !== 'idle' && state !== 'sit') return;
      const dx = (Math.random() * 2 - 1) * 2.2, dy = (Math.random() * 2 - 1) * 1.4;
      pupils.forEach(p => p.style.transform = `translate(${dx}px, ${dy}px)`);
      setTimeout(() => pupils.forEach(p => p.style.transform = ''), 1400 + Math.random() * 1200);
    }

    function pet() {
      root.classList.add('happy');
      const h = document.createElement('div');
      h.className = 'heart'; h.textContent = '❤';
      h.style.left = '50%'; h.style.top = '4%';
      root.appendChild(h);
      setTimeout(() => h.remove(), 1400);
      setTimeout(() => root.classList.remove('happy'), 1300);
    }

    function hop() {
      root.classList.remove('hop'); void root.offsetWidth; root.classList.add('hop');
      setTimeout(() => root.classList.remove('hop'), 420);
    }

    function land() {
      root.classList.remove('land'); void root.offsetWidth; root.classList.add('land');
      setTimeout(() => root.classList.remove('land'), 340);
    }

    function setOutfit(name) {   // null = none
      OUTFITS.forEach(o => root.classList.remove('acc-' + o));
      if (name) root.classList.add('acc-' + name);
    }

    // Random life: blinks, ear twitches, little glances.
    (function life() {
      setTimeout(() => {
        if (state !== 'sleep') {
          const r = Math.random();
          if (r < 0.6) blink(); else if (r < 0.8) twitch(); else glance();
          if (Math.random() < 0.15) setTimeout(blink, 220); // double blink
        }
        life();
      }, 1400 + Math.random() * 2600);
    })();

    if (opts.onClick) root.addEventListener('click', opts.onClick);
    return { el: root, setState, setFacing, pet, hop, land, blink, setOutfit, get state() { return state; } };
  }

  window.AuraCat = { mount, OUTFITS };
})();
