# cat_director.py
# The Python half of the cat's desktop adventures (the animation itself is
# Face/cat_stage.js). This module:
#   * reads the real screen: the taskbar ("ground"), window title bars the cat can
#     use as ledges, and which windows are in its way;
#   * opens the cat's transparent stage over the screen for the trip, then shrinks
#     it to just the cat once it has arrived (so the rest of the screen stays clickable);
#   * shoves windows that are in the way aside (moves them - never closes them) and
#     slides them back when the cat goes home.
# All maths here is in PHYSICAL pixels; the stage page works in logical (CSS) pixels.
import ctypes
import json
import threading
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL

CAT_W, CAT_H = 112, 98          # logical size of the cat (feet at bottom-centre)
REST_W, REST_H = 112, 124       # the stage window while resting (headroom for the thought bubble)
SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
HWND_TOPMOST = wintypes.HWND(-1)
DWMWA_CLOAKED = 14


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def monitor_at(x, y):
    """(monitor rect, work rect) in physical px for the monitor containing x,y."""
    mon = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    user32.GetMonitorInfoW(mon, ctypes.byref(info))
    m, w = info.rcMonitor, info.rcWork
    return (m.left, m.top, m.right, m.bottom), (w.left, w.top, w.right, w.bottom)


def _title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _cloaked(hwnd):
    val = ctypes.c_int(0)
    try:
        dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        return False
    return val.value != 0


def visible_windows():
    """Normal app windows, top of the z-order first: [(hwnd, title, rect, maximized)]."""
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or _cloaked(hwnd):
            return True
        ex = user32.GetWindowLongW(hwnd, -20)
        if ex & 0x80:            # tool windows (incl. AURA's cat + bubble)
            return True
        title = _title(hwnd)
        if not title or title.startswith("AURA") or title in ("Program Manager",) or title.startswith("Activate Windows"):
            return True
        r = _rect(hwnd)
        if r[2] - r[0] < 120 or r[3] - r[1] < 80:
            return True
        out.append((hwnd, title, r, bool(user32.IsZoomed(hwnd))))
        return True
    user32.EnumWindows(cb, 0)
    return out


def taskbar_top(monitor):
    """Physical y of the taskbar's top edge if it's along the bottom of this monitor, else the monitor bottom."""
    tb = user32.FindWindowW("Shell_TrayWnd", None)
    if tb and user32.IsWindowVisible(tb):
        l, t, r, b = _rect(tb)
        ml, mt, mr, mb = monitor
        if b >= mb - 2 and t > mt + (mb - mt) // 2 and l <= ml + 5:
            return t
    return monitor[3]


def _overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class Stage:
    """Controls the cat's stage window for one adventure."""

    def __init__(self, hwnd, evaluate_js):
        self.hwnd = hwnd
        self.js = evaluate_js
        self.monitor = None
        self.scale = 1.0
        self.feet = None            # physical feet position while resting
        self.moved = {}             # hwnd -> (original rect, rect we moved it to)
        self.obstacles = {}         # id -> (hwnd, dx physical)
        self.arrived = threading.Event()
        self.home = threading.Event()
        self.arrived_at = None

    # ---- coordinates
    def _to_local(self, x, y):
        return (x - self.monitor[0]) / self.scale, (y - self.monitor[1]) / self.scale

    def _to_phys(self, lx, ly):
        return self.monitor[0] + lx * self.scale, self.monitor[1] + ly * self.scale

    def _fullscreen(self):
        ml, mt, mr, mb = self.monitor
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, ml, mt, mr - ml, mb - mt, SWP_NOACTIVATE)
        user32.ShowWindow(self.hwnd, 4)   # SW_SHOWNOACTIVATE
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, ml, mt, mr - ml, mb - mt, SWP_NOACTIVATE)

    def rest_rect(self):
        """Physical rect of the small resting window (feet at bottom-centre)."""
        fx, fy = self.feet
        w, h = REST_W * self.scale, REST_H * self.scale
        return int(fx - w / 2), int(fy - h), int(w), int(h)

    # ---- the trip out
    def plan(self, start_phys, start_from, target_phys, target_hwnd, bubble_rect):
        """Builds the geometry the stage page needs (logical coords)."""
        self.monitor, work = monitor_at(*target_phys)
        self.scale = (user32.GetDpiForWindow(self.hwnd) or 96) / 96
        ground_phys = taskbar_top(self.monitor)
        gx, ground = self._to_local(0, ground_phys)

        tx, ty = self._to_local(*target_phys)
        # Sit just under the highlighted line - or above it if that's too near the taskbar.
        feet_y = ty + 18 + CAT_H
        if feet_y > ground - 20:
            feet_y = ty - 16
        target = {"x": tx, "y": max(CAT_H + 40, feet_y)}

        if start_phys:
            sx, sy = self._to_local(*start_phys)
        else:   # out of the taskbar, near the clock
            sx, sy = (self.monitor[2] - self.monitor[0]) / self.scale - 220, ground
        start = {"x": sx, "y": sy, "from": start_from}

        ledges, obstacles = [], []
        clear_zone = (
            self.monitor[0] + (target["x"] - 90) * self.scale, self.monitor[1] + (target["y"] - CAT_H - 30) * self.scale,
            self.monitor[0] + (target["x"] + 90) * self.scale, ground_phys,
        )
        zones = [clear_zone] + ([bubble_rect] if bubble_rect else [])
        for i, (hwnd, title, r, maxed) in enumerate(visible_windows()):
            if maxed:
                continue
            l, t, rr, b = r
            lx1, ly = self._to_local(l, t)
            lx2, _ = self._to_local(rr, t)
            if 0 < ly < ground - 60:
                ledges.append({"x1": lx1, "x2": lx2, "y": ly})
            if hwnd == target_hwnd or len(obstacles) >= 2:
                continue
            zone = next((z for z in zones if _overlaps(r, z)), None)
            if not zone:
                continue
            # push it sideways, away from the zone, keeping a good chunk of it on screen
            cx = (l + rr) / 2
            dx = (zone[0] - 12 - rr) if cx < (zone[0] + zone[2]) / 2 else (zone[2] + 12 - l)
            new_l, new_r = l + dx, rr + dx
            if new_r < self.monitor[0] + 160 or new_l > self.monitor[2] - 160:
                continue   # would end up (nearly) off screen - leave it alone
            oid = f"w{i}"
            self.obstacles[oid] = (hwnd, int(dx))
            ox, oy = self._to_local(l, t)
            obstacles.append({"id": oid, "x": ox, "y": oy, "w": (rr - l) / self.scale, "h": (b - t) / self.scale,
                              "dx": dx / self.scale, "title": title[:60]})
        return {"stage": {"w": (self.monitor[2] - self.monitor[0]) / self.scale,
                          "h": (self.monitor[3] - self.monitor[1]) / self.scale},
                "ground": ground, "start": start, "target": target, "ledges": ledges[:12], "obstacles": obstacles}

    def run_trip(self, plan, timeout=45):
        self.arrived.clear()
        self._fullscreen()
        time.sleep(0.12)   # let the page lay out at full size
        self.js(f"window.startTrip({json.dumps(plan)})")
        self.arrived.wait(timeout)
        lx, ly = self.arrived_at or (plan["target"]["x"], plan["target"]["y"])
        self.feet = self._to_phys(lx, ly)
        x, y, w, h = self.rest_rect()
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE)
        self.js(f"window.restAt({REST_W / 2}, {REST_H})")
        return self.feet

    # ---- obstacles
    def push(self, oid):
        hwnd, dx = self.obstacles.get(oid, (None, 0))
        if not hwnd or not user32.IsWindow(hwnd):
            return
        start = _rect(hwnd)
        if hwnd not in self.moved:
            self.moved[hwnd] = (start, None)
        threading.Thread(target=self._slide, args=(hwnd, start, dx), daemon=True).start()

    def _slide(self, hwnd, start, dx, ms=520):
        l, t, r, b = start
        t0 = time.perf_counter()
        while True:
            p = min(1.0, (time.perf_counter() - t0) / (ms / 1000))
            e = 1 - (1 - p) ** 3
            user32.SetWindowPos(hwnd, None, int(l + dx * e), t, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
            if p >= 1:
                break
            try:
                dwmapi.DwmFlush()
            except Exception:
                time.sleep(1 / 60)
        if hwnd in self.moved:
            self.moved[hwnd] = (self.moved[hwnd][0], _rect(hwnd))

    def restore_windows(self):
        """Slides shoved windows back - unless the user has moved them since."""
        threads = []
        for hwnd, (orig, moved_to) in list(self.moved.items()):
            if not user32.IsWindow(hwnd) or moved_to is None or _rect(hwnd) != moved_to:
                continue
            t = threading.Thread(target=self._slide, args=(hwnd, moved_to, orig[0] - moved_to[0]), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(2)
        self.moved.clear()
        self.obstacles.clear()

    # ---- the trip home
    def run_home(self, home_phys, timeout=40):
        self.home.clear()
        fx, fy = self.feet
        self._fullscreen()
        time.sleep(0.12)
        lx, ly = self._to_local(fx, fy)
        ground = self._to_local(0, taskbar_top(self.monitor))[1]
        home = None
        if home_phys:
            hx, hy = self._to_local(*home_phys)
            home = {"x": hx, "y": hy}
        self.js(f"window.goHome({json.dumps({'from': {'x': lx, 'y': ly}, 'home': home, 'ground': ground})})")
        self.home.wait(timeout)
        user32.ShowWindow(self.hwnd, 0)
        self.restore_windows()
