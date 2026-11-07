# tray_icon.py
# Keeps AURA running in the background via a system tray icon - closing a
# window just hides it now, it doesn't quit AURA. Left-click the tray icon
# to bring the main window back; right-click for more options.
import threading
from PIL import Image, ImageDraw
import pystray


def _make_icon_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), outline=(58, 214, 255, 255), width=4)
    draw.ellipse((20, 20, 44, 44), fill=(58, 214, 255, 255))
    return img


def start_tray(show_face, show_dashboard, show_schedule, quit_app, pair_phone=None):
    icon_image = _make_icon_image()

    def _show_face(icon, item):
        show_face()

    def _show_dashboard(icon, item):
        show_dashboard()

    def _show_schedule(icon, item):
        show_schedule()

    def _pair_phone(icon, item):
        if pair_phone:
            pair_phone()

    def _quit(icon, item):
        icon.stop()
        quit_app()

    menu = pystray.Menu(
        pystray.MenuItem("Show AURA", _show_face, default=True),
        pystray.MenuItem("Open Dashboard", _show_dashboard),
        pystray.MenuItem("Open Schedule", _show_schedule),
        pystray.MenuItem("Pair a phone (AURA Mobile)...", _pair_phone),
        pystray.MenuItem("Quit AURA", _quit),
    )

    icon = pystray.Icon("AURA", icon_image, "AURA", menu)
    threading.Thread(target=icon.run, daemon=True).start()
    return icon