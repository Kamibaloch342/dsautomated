#!/usr/bin/env python3
"""
start_dashboard.py — cross-platform dashboard starter (Windows + macOS).

Starts dashboard/server.py on port 8000 detached (survives closing the console),
after stopping any previous instance. On macOS it wraps caffeinate so the Mac
stays awake; Windows has no direct equivalent (set Power options to 'Never sleep'
when plugged in). The Mac-only Tailscale/funnel link logic stays in
setup_shared_dashboard.sh — this script is just the portable start/stop core.

    python scripts/start_dashboard.py          # start (restarts if running)
    python scripts/start_dashboard.py --stop   # stop
"""
import os
import subprocess
import sys
import time

AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(AUTO)
PORT = "8000"
LOG = os.path.join(AUTO, "data", "dashboard_service.log")


def _venv_python():
    for p in (os.path.join(ROOT, ".venv", "Scripts", "python.exe"),
              os.path.join(ROOT, ".venv", "bin", "python")):
        if os.path.exists(p):
            return p
    return sys.executable


def _stop():
    """Stop any running dashboard server by freeing its port.

    NOTE: the previous version used `wmic`, which Microsoft REMOVED in Windows 11
    24H2 (build 26100+) — it now throws FileNotFoundError. We kill by listening
    port instead, via utils.proc (netstat + taskkill on Windows, lsof on macOS).
    Killing by port only touches whatever holds :8000, never other python procs
    like the placer.
    """
    if AUTO not in sys.path:
        sys.path.insert(0, AUTO)
    try:
        from utils.proc import kill_pids_on_port
        return kill_pids_on_port(int(PORT))
    except Exception:
        # utils.proc should always be importable; if not, only use a SAFE, targeted
        # fallback on macOS. Never blanket-kill python.exe on Windows (would take the
        # placer down with it).
        if os.name != "nt":
            subprocess.run(["pkill", "-f", "dashboard/server.py"], capture_output=True)
        return 0


def _start():
    py = _venv_python()
    os.makedirs(os.path.join(AUTO, "data"), exist_ok=True)
    logf = open(LOG, "a", encoding="utf-8", errors="replace")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    cmd = [py, os.path.join(AUTO, "dashboard", "server.py"), PORT]
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | \
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(cmd, cwd=AUTO, stdout=logf, stderr=logf, env=env,
                         creationflags=flags)
    else:
        # keep the Mac awake while serving, if caffeinate exists
        if os.path.exists("/usr/bin/caffeinate"):
            cmd = ["/usr/bin/caffeinate", "-s"] + cmd
        subprocess.Popen(cmd, cwd=AUTO, stdout=logf, stderr=logf, env=env,
                         start_new_session=True)


def main():
    if "--stop" in sys.argv:
        _stop()
        print("dashboard stopped.")
        return
    _stop()
    time.sleep(1)
    _start()
    time.sleep(2)
    try:
        import urllib.request
        urllib.request.urlopen(f"http://localhost:{PORT}/", timeout=5)
        print(f"✅ dashboard UP on http://localhost:{PORT}   (log: {LOG})")
    except Exception:
        print(f"⚠️  started but not reachable yet — check the log: {LOG}")


if __name__ == "__main__":
    main()
