# aura_launcher.pyw
# Starts AURA in its own terminal window (minimised on the taskbar). You're in control:
# closing that window, killing AURA, or "Quit AURA" in the tray stops it for good.
# Only if AURA crashes by itself is it restarted (waiting longer each time if it keeps crashing).
#
# A shortcut to this file sits in the Windows Startup folder, so AURA opens as soon
# as you log in. Remove it with:  python aura_launcher.pyw --uninstall
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STARTUP_LINK = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup\AURA.lnk")


def _pythonw():
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return w if os.path.exists(w) else exe


def install():
    """Creates the Startup-folder shortcut (runs this launcher at every login)."""
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{STARTUP_LINK}');"
          f"$s.TargetPath='{_pythonw()}';$s.Arguments='\"{os.path.abspath(__file__)}\"';"
          f"$s.WorkingDirectory='{HERE}';$s.Description='AURA personal assistant';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    print(f"AURA will now start when you log in ({STARTUP_LINK}).")


def uninstall():
    if os.path.exists(STARTUP_LINK):
        os.remove(STARTUP_LINK)
    print("AURA won't start with Windows any more.")


def _python():
    """python.exe (with a console window), next to the pythonw.exe running this launcher."""
    exe = os.path.join(os.path.dirname(sys.executable), "python.exe")
    return exe if os.path.exists(exe) else sys.executable


CRASHED = 3      # main.py exits with this only when AURA itself crashed (see main._exit_on_crash)


def supervise():
    """Runs AURA in its own terminal window (started minimised). Closing that window, killing AURA, or
    "Quit AURA" in the tray stops it for good - it's only restarted if AURA crashed by itself."""
    delays = [3, 10, 30, 60, 300]
    crashes, last_start = 0, 0.0
    while True:
        last_start = time.time()
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 7               # SW_SHOWMINNOACTIVE: minimised on the taskbar, doesn't steal focus
        code = subprocess.call([_python(), os.path.join(HERE, "main.py"), "--background"], cwd=HERE,
                               creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=si)
        if code != CRASHED:
            return                       # you closed it / killed it / quit it - stay stopped
        if time.time() - last_start > 600:
            crashes = 0                  # it ran fine for a while - start the back-off over
        time.sleep(delays[min(crashes, len(delays) - 1)])
        crashes += 1


if __name__ == "__main__":
    if "--install" in sys.argv:
        install()
    elif "--uninstall" in sys.argv:
        uninstall()
    else:
        supervise()
