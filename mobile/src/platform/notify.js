// Service worker + push notifications.
// iPhone: Web Push works only for apps added to the Home Screen (iOS 16.4+), and permission can
// only be asked from a tap. Android/desktop browsers: works in the browser too.
import { isIOS, isStandalone } from './device.js';

let registration = null;

export async function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) return null;
  try {
    registration = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
    return registration;
  } catch (e) {
    console.warn('service worker failed', e);
    return null;
  }
}

/** 'unsupported' | 'needs-install' | 'default' | 'granted' | 'denied' */
export function pushStatus() {
  if (isIOS && !isStandalone()) return 'needs-install';
  if (!('Notification' in window) || !('PushManager' in window) || !('serviceWorker' in navigator)) return 'unsupported';
  return Notification.permission;
}

function keyToBytes(base64url) {
  const pad = '='.repeat((4 - (base64url.length % 4)) % 4);
  const raw = atob((base64url + pad).replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

/** Must be called from a tap. Returns the subscription JSON to send to the PC, or throws. */
export async function enablePush(publicKey) {
  const status = pushStatus();
  if (status === 'needs-install') throw new Error('On iPhone and iPad, add AURA to your Home Screen first (Share → Add to Home Screen), then open it from there.');
  if (status === 'unsupported') throw new Error("This browser can't show notifications.");
  const perm = await Notification.requestPermission();
  if (perm !== 'granted') throw new Error('Notifications are turned off for AURA - allow them in Settings → Notifications.');
  const reg = registration || (await navigator.serviceWorker.ready);
  let sub = await reg.pushManager.getSubscription();
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyToBytes(publicKey) });
  return sub.toJSON();
}

/** Clears the app-icon badge / delivered notifications when the app is opened. */
export async function clearNotifications() {
  try {
    if (navigator.clearAppBadge) navigator.clearAppBadge();
    const reg = registration || (await navigator.serviceWorker.getRegistration());
    if (reg) (await reg.getNotifications()).forEach((n) => n.close());
  } catch (e) { /* ignore */ }
}

/** A notification shown by the phone itself (phone brain reminders while the PC is off). */
export async function localNotification(title, body) {
  try {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    const reg = registration || (await navigator.serviceWorker.getRegistration());
    if (reg) await reg.showNotification(title, { body, icon: '/icons/icon-192.png', badge: '/icons/icon-192.png', tag: 'aura-local' });
  } catch (e) { /* ignore */ }
}
