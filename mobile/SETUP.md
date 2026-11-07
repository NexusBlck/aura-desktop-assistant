# AURA Mobile — put AURA on your iPhone (free)

AURA Mobile runs on your PC and opens on your phone like an app. Your phone reaches the PC through
**Tailscale**, a free private network for your own devices: it works at home and when you're out,
and nobody else can reach AURA.

## One-time setup (about 10 minutes)

### 1. Tailscale on the PC
1. Download Tailscale for Windows from https://tailscale.com/download and install it.
2. Sign in (Google account is fine). Free for personal use.

### 2. Tailscale on the iPhone
1. Install **Tailscale** from the App Store, open it, and sign in with the **same account**.
2. Turn the VPN switch on when it asks.

### 3. Give AURA a secure address (on the PC)
Open **PowerShell** and run:

```
tailscale serve --bg 8740
```

The first time, it gives you a link to switch on HTTPS for your tailnet. Open it, click **Enable**,
then run the command again. It prints your AURA address, something like:

```
https://your-pc-name.your-tailnet.ts.net/
```

This stays on, even after a restart. To switch it off: `tailscale serve --https=443 off`

> **Apple Watch only:** the "Ask AURA" watch shortcut can't use Tailscale, so it needs
> `tailscale funnel --bg 8740` instead of `serve`. Funnel makes the address reachable from the **public
> internet**. AURA then answers only the watch endpoint there, requires the watch's own key, rate-limits
> requests and (with `SAFE_MODE=true`) only allows simple actions like answers, reminders and tasks. Don't
> turn Funnel on unless you use the watch. Switch it off with `tailscale funnel --https=443 off`.

### 4. Install AURA on the iPhone
1. On the iPhone, open that address in **Safari**.
2. Tap **Share** → **Add to Home Screen** → **Add**.
3. Open **AURA** from the new home-screen icon (not from Safari).
4. Tap **SHOW A CODE ON MY PC**. A code pops up on your PC. Type it in, then tap **PAIR**.
   (You can also get a code from the AURA tray icon → **Pair a phone…**)
5. In **MORE → SETTINGS**, tap **TURN ON NOTIFICATIONS** for reminders, focus timer, important emails,
   and AURA's answers while the app is closed.

## Day to day
* AURA has to be running on the PC (it starts with Windows).
* Tailscale stays on in the background on both devices.
* Tap the **core** or the **mic** to talk, or type in **COMMS**. Anything you can say to AURA on the PC works.
* Remove a phone: **MORE → SETTINGS → PAIRED DEVICES**, or delete `aura_phone_devices.json` on the PC to unpair everything.

## If something's wrong
| You see | Fix |
|---|---|
| "Can't reach AURA" | Is AURA running on the PC? Is Tailscale on (on both)? Is `tailscale serve status` showing port 8740? |
| "PC OFFLINE" banner | Same as above. The app shows what AURA last knew until the PC is back. |
| No notifications | iOS 16.4 or later, opened from the Home Screen icon, notifications allowed in iPhone Settings → Notifications → AURA. |
| Mic doesn't work | iPhone Settings → AURA (or Safari) → Microphone. |
| Asked to pair again after adding to Home Screen | Normal on iPhone: the Home Screen app has its own storage. Pair once more there. |

## Android later
The same address works in Chrome on Android (**Install app**), with notifications and the mic.
A Play Store / App Store build can wrap this app with Capacitor. Only `mobile/src/platform/` needs
native versions; see `ARCHITECTURE.md`.
