# system_monitor.py
# Samples the PC's vitals every 2 seconds in the background (so the HUD
# never waits on it): CPU and RAM history for the graphs, network
# upload/download speed, disk space, and the programs using the most CPU.
import time
import threading
from collections import deque
import psutil

SAMPLE_SECONDS = 1
HISTORY = 60  # points kept for each graph (= 1 minute)

_lock = threading.Lock()
_data = {
    "cpu": deque(maxlen=HISTORY), "ram": deque(maxlen=HISTORY),
    "down": deque(maxlen=HISTORY), "up": deque(maxlen=HISTORY),
    "top": [], "disk": None, "battery": None, "plugged": None, "cores": psutil.cpu_count() or 1,
}
_started = False


def _sample_processes(cores):
    procs = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            cpu = p.cpu_percent(None) / cores  # first call per process returns 0 - fine
            name = p.info["name"] or ""
            if not name or name.lower() in ("system idle process", "idle"):
                continue
            procs.append((cpu, (p.info["memory_info"].rss if p.info["memory_info"] else 0), name))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    # Merge multi-process apps (Chrome, Edge, Discord...) into one line each.
    merged = {}
    for cpu, mem, name in procs:
        key = name.lower().removesuffix(".exe")
        c, m, _ = merged.get(key, (0.0, 0, name))
        merged[key] = (c + cpu, m + mem, name.removesuffix(".exe"))
    top = sorted(merged.values(), key=lambda x: (x[0], x[1]), reverse=True)[:6]
    return [{"name": n, "cpu": round(c, 1), "ram_mb": round(m / 1_048_576)} for c, m, n in top]


PROCESS_SAMPLE_SECONDS = 5  # listing every program is slow on Windows (~3s), so it runs separately


def _vitals_loop():
    """Fast loop: CPU, RAM, network, disk, battery - cheap to read."""
    psutil.cpu_percent(None)
    last_net = psutil.net_io_counters()
    last_t = time.time()
    while True:
        time.sleep(SAMPLE_SECONDS)
        try:
            now = time.time()
            net = psutil.net_io_counters()
            elapsed = max(now - last_t, 0.001)
            down = (net.bytes_recv - last_net.bytes_recv) / elapsed
            up = (net.bytes_sent - last_net.bytes_sent) / elapsed
            last_net, last_t = net, now

            battery = psutil.sensors_battery()
            try:
                disk = psutil.disk_usage("C:\\")
                disk_info = {"used_gb": round(disk.used / 1e9), "total_gb": round(disk.total / 1e9), "percent": disk.percent}
            except OSError:
                disk_info = None

            with _lock:
                _data["cpu"].append(psutil.cpu_percent(None))
                _data["ram"].append(psutil.virtual_memory().percent)
                _data["down"].append(round(down))
                _data["up"].append(round(up))
                _data["disk"] = disk_info
                _data["battery"] = battery.percent if battery else None
                _data["plugged"] = battery.power_plugged if battery else None
        except Exception as e:
            print(f"(system monitor: {e})")


def _process_loop():
    """Slow loop: which programs are using the most CPU."""
    while True:
        try:
            top = _sample_processes(_data["cores"])
            with _lock:
                _data["top"] = top
        except Exception as e:
            print(f"(system monitor processes: {e})")
        time.sleep(PROCESS_SAMPLE_SECONDS)


def start():
    global _started
    if not _started:
        _started = True
        threading.Thread(target=_vitals_loop, daemon=True).start()
        threading.Thread(target=_process_loop, daemon=True).start()


def snapshot():
    with _lock:
        return {
            "cpu_history": list(_data["cpu"]), "ram_history": list(_data["ram"]),
            "down_history": list(_data["down"]), "up_history": list(_data["up"]),
            "cpu": _data["cpu"][-1] if _data["cpu"] else None,
            "ram": _data["ram"][-1] if _data["ram"] else None,
            "down": _data["down"][-1] if _data["down"] else 0,
            "up": _data["up"][-1] if _data["up"] else 0,
            "top": list(_data["top"]), "disk": _data["disk"],
            "battery": _data["battery"], "plugged": _data["plugged"],
            "ram_total_gb": round(psutil.virtual_memory().total / 1e9, 1), "cores": _data["cores"],
        }
