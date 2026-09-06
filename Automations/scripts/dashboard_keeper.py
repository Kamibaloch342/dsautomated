#!/usr/bin/env python3
"""dashboard_keeper.py — keep the ZUHRA dashboard up, no matter what kills it.

WHY THIS EXISTS (2026-08-19): the dashboard went down and stayed down for hours.
Two independent reasons, both fixed here:

  1. NOTHING RESTARTED IT. The com.dropship.dashboard LaunchAgent is loaded and
     KeepAlive is on, but every spawn dies instantly with exit 78 (EX_CONFIG) and
     no output — 648 attempts logged. macOS TCC blocks a background launchd agent
     from reaching this project under ~/Downloads, so launchd can never run it.
     (Fix for that is a Full Disk Access grant; this keeper does not need one
     because it inherits the permissions of whoever starts it.)

  2. IT SHARED A PROCESS GROUP WITH THE FLEET. The launcher's Stop All does
     os.killpg(...) on each child's whole group; at 10:12:35 both trackers took
     SIGTERM and the dashboard died in the same sweep. Children started here get
     start_new_session=True, i.e. their OWN session and process group, so no
     killpg aimed at the launcher's tree can ever reach the dashboard again.

    python3 scripts/dashboard_keeper.py            # supervise (foreground)
    python3 scripts/dashboard_keeper.py --once     # one check, then exit
    python3 scripts/dashboard_keeper.py --stop     # stop keeper + dashboard
"""
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(AUTO)
PY = os.path.join(ROOT, ".venv", "bin", "python")
if not os.path.exists(PY):
    PY = sys.executable
SERVER = os.path.join(AUTO, "dashboard", "server.py")
PORT = os.environ.get("DASH_PORT", "8000")
LOG = os.path.join(AUTO, "data", "dashboard_service.log")
KEEPER_LOG = os.path.join(AUTO, "data", "dashboard_keeper.log")
PID_FILE = os.path.join(AUTO, "data", "dashboard_keeper.pid")
INTERVAL = int(os.environ.get("DASH_KEEPER_INTERVAL", "30"))


def say(msg):
    line = f"[{time.strftime('%F %T')}] {msg}"
    print(line, flush=True)
    try:
        with open(KEEPER_LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def is_up():
    """Any HTTP answer means alive — the login gate returns 200, and a 4xx/5xx
    still proves the server is serving. Only a refused/timed-out socket is down."""
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=6)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def start():
    """Launch the server detached: its own session (immune to the launcher's
    killpg sweeps) and caffeinate so the Mac won't idle-sleep it away."""
    with open(LOG, "a") as log:
        p = subprocess.Popen(
            ["/usr/bin/caffeinate", "-s", PY, SERVER, PORT],
            cwd=AUTO, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            start_new_session=True,          # ← the killpg immunity
        )
    for _ in range(20):                       # up to ~20s to bind the port
        time.sleep(1)
        if is_up():
            say(f"dashboard started (pid {p.pid}) — serving on :{PORT}")
            return True
    say(f"⚠️ started pid {p.pid} but :{PORT} never answered — see {LOG}")
    return False


def stop_all():
    try:
        keeper = int(open(PID_FILE).read().strip())
        if keeper != os.getpid():
            os.kill(keeper, 15)
    except Exception:
        pass
    for f in (PID_FILE,):
        try:
            os.remove(f)
        except OSError:
            pass
    subprocess.call(["pkill", "-f", f"dashboard/server.py {PORT}"])
    say("keeper + dashboard stopped")


def main():
    if "--stop" in sys.argv:
        stop_all(); return
    if "--once" in sys.argv:
        say("up" if is_up() else "down → starting")
        if not is_up():
            start()
        return

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    say(f"keeper watching :{PORT} every {INTERVAL}s")
    if not is_up():
        start()
    while True:
        time.sleep(INTERVAL)
        if not is_up():
            say("dashboard DOWN — restarting")
            start()


if __name__ == "__main__":
    main()
