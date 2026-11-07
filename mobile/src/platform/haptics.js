// Haptic feedback. iOS Safari has no vibration API, so on an iPhone home-screen app this does
// nothing (documented limitation); Android browsers vibrate. A native build (Capacitor) would
// replace these with @capacitor/haptics without touching any screen code.
function buzz(ms) {
  try { if (navigator.vibrate) navigator.vibrate(ms); } catch (e) { /* ignore */ }
}

export const tap = () => buzz(8);
export const success = () => buzz([10, 40, 10]);
export const warning = () => buzz([30, 60, 30]);
