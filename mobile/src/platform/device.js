// What kind of device / shell this is, and app lifecycle events.
export const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 0);
// iPadOS tells websites it's a Mac - a "Mac" with a touch screen is an iPad
export const isIPad = /iPad/.test(navigator.userAgent) || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 0);
document.documentElement.classList.toggle('ipad', isIPad);
export const isAndroid = /Android/i.test(navigator.userAgent);
export const isStandalone = () => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
export const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/** fn(visible) when the app goes to the background / comes back. */
export function onVisibility(fn) {
  document.addEventListener('visibilitychange', () => fn(document.visibilityState === 'visible'));
  window.addEventListener('pageshow', (e) => { if (e.persisted) fn(true); });
}

/** fn(online) when the network comes and goes. */
export function onNetwork(fn) {
  window.addEventListener('online', () => fn(true));
  window.addEventListener('offline', () => fn(false));
}

/** Keeps --vvh = the visible height, so the input bar sits right above the iOS keyboard,
 *  and marks body.kb-open while the keyboard is up. */
export function trackViewport() {
  const vv = window.visualViewport;
  const update = () => {
    const h = vv ? vv.height : window.innerHeight;
    document.documentElement.style.setProperty('--vvh', `${Math.round(h)}px`);
    const kb = vv ? window.innerHeight - vv.height > 120 : false;
    document.body.classList.toggle('kb-open', kb);
    if (vv && vv.offsetTop) window.scrollTo(0, 0);     // iOS scrolls the page under the keyboard - undo it
  };
  (vv || window).addEventListener('resize', update);
  if (vv) vv.addEventListener('scroll', update);
  update();
}

export function deviceName() {
  if (isIPad) return 'iPad';
  if (isIOS) return 'iPhone';
  if (isAndroid) return 'Android phone';
  return 'Browser';
}
