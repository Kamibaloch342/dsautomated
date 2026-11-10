#!/usr/bin/env python3
"""
install_mac_autostart.py — keep the dashboard permanently up on the Mac.

Installs a LaunchAgent that:
  * starts the dashboard automatically when you log in (survives reboots),
  * restarts it within seconds if it ever crashes or is killed (KeepAlive),
  * wraps it in `caffeinate -s` so the Mac won't sleep while it is serving.

This is the macOS twin of the Windows "AutomationsDashboard" scheduled task. It runs
as YOU (a LaunchAgent, not a system LaunchDaemon), so it needs no sudo and no stored
password — the trade-off is that it starts at LOGIN rather than at boot. For a laptop
you log into, that is the right choice.

    python scripts/install_mac_autostart.py              # install / update
    python scripts/install_mac_autostart.py --uninstall  # remove
    python scripts/install_mac_autostart.py --status     # is it loaded?

Paths are derived from this file's location, so it keeps working if the project moves.
"""
import os
import plistlib
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)                       # .../Automations
ROOT = os.path.dirname(AUTO)                       # .../Development_Unit (holds .venv)
LABEL = "com.zuhra.dashboard"
PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")
PY = os.path.join(ROOT, ".venv", "bin", "python")
SERVER = os.path.join(AUTO, "dashboard", "server.py")
# The log MUST live outside ~/Desktop, ~/Documents and ~/Downloads. launchd opens the
# StandardOutPath itself, before the program runs, and macOS TCC denies it inside those
# three protected folders — the job then dies with "78: EX_CONFIG" and never starts.
# ~/Library/Logs is not protected, so it always works regardless of where the project lives.
LOG = os.path.expanduser("~/Library/Logs/zuhra-dashboard.log")
PORT = "8000"

OK, WARN, ERR = "[ OK ]", "[WARN]", "[FAIL]"


def say(tag, msg):
    print(f"  {tag} {msg}", flush=True)


def _domain():
    return f"gui/{os.getuid()}"


def _bootout():
    """Unload the agent, tolerating both modern and legacy launchctl."""
    subprocess.run(["launchctl", "bootout", f"{_domain()}/{LABEL}"],
                   capture_output=True, text=True)
    subprocess.run(["launchctl", "unload", "-w", PLIST], capture_output=True, text=True)


def cmd_status():
    r = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    loaded = any(LABEL in ln for ln in (r.stdout or "").splitlines())
    say(OK if loaded else WARN, f"LaunchAgent {LABEL}: {'LOADED' if loaded else 'not loaded'}")
    say(OK if os.path.exists(PLIST) else WARN, f"plist: {PLIST}")
    try:
        import urllib.request
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=5)
        say(OK, f"dashboard responding on :{PORT}")
    except Exception as e:
        say(WARN, f"dashboard NOT responding on :{PORT} ({type(e).__name__})")


def cmd_uninstall():
    _bootout()
    if os.path.exists(PLIST):
        os.remove(PLIST)
        say(OK, f"removed {PLIST}")
    else:
        say(WARN, "no plist to remove")
    say(OK, "auto-start disabled. The dashboard will no longer restart by itself.")


def cmd_install():
    if not os.path.exists(PY):
        say(ERR, f"venv python not found: {PY}")
        sys.exit(1)
    if not os.path.exists(SERVER):
        say(ERR, f"server not found: {SERVER}")
        sys.exit(1)

    # caffeinate -s keeps the Mac awake while the server runs (idle sleep only; the lid
    # closing on battery will still sleep it — that is macOS, not something we can override).
    args = [PY, SERVER, PORT]
    if os.path.exists("/usr/bin/caffeinate"):
        args = ["/usr/bin/caffeinate", "-s"] + args

    plist = {
        "Label": LABEL,
        "ProgramArguments": args,
        "WorkingDirectory": AUTO,
        "RunAtLoad": True,        # start as soon as it is loaded, and at every login
        "KeepAlive": True,        # restart automatically if it exits for ANY reason
        "ThrottleInterval": 10,   # ...but wait 10s between restarts so a crash-loop is visible
        "StandardOutPath": LOG,
        "StandardErrorPath": LOG,
        "EnvironmentVariables": {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
    }

    os.makedirs(os.path.dirname(PLIST), exist_ok=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)

    # Stop anything already holding the port, including a previous copy of this agent,
    # so the new one starts cleanly instead of crash-looping on "address in use".
    _bootout()
    try:
        sys.path.insert(0, AUTO)
        from utils.proc import kill_pids_on_port
        n = kill_pids_on_port(int(PORT))
        if n:
            say(WARN, f"stopped {n} process(es) already on :{PORT}")
    except Exception:
        pass

    with open(PLIST, "wb") as f:
        plistlib.dump(plist, f)
    say(OK, f"wrote {PLIST}")

    r = subprocess.run(["launchctl", "bootstrap", _domain(), PLIST],
                       capture_output=True, text=True)
    if r.returncode != 0:
        r = subprocess.run(["launchctl", "load", "-w", PLIST],
                           capture_output=True, text=True)
    if r.returncode != 0:
        say(ERR, f"launchctl could not load the agent: {(r.stderr or r.stdout).strip()[:300]}")
        sys.exit(1)
    say(OK, "LaunchAgent loaded")

    import time
    for _ in range(20):
        time.sleep(1)
        try:
            import urllib.request
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=3)
            say(OK, f"dashboard is UP on http://localhost:{PORT}")
            break
        except Exception:
            continue
    else:
        say(WARN, f"agent loaded but the dashboard has not answered yet — check {LOG}")

    print()
    say(OK, "The dashboard now:")
    print("         - starts automatically when you log in (survives reboot)")
    print("         - restarts itself within ~10s if it crashes or is killed")
    print("         - keeps the Mac awake while it is serving")
    print(f"\n       Manage it:  launchctl list | grep {LABEL}")
    print(f"       Turn it off: python scripts/install_mac_autostart.py --uninstall")


def main():
    if sys.platform != "darwin":
        print("macOS only (uses launchd). Nothing done.")
        return
    if "--uninstall" in sys.argv:
        cmd_uninstall()
    elif "--status" in sys.argv:
        cmd_status()
    else:
        cmd_install()


if __name__ == "__main__":
    main()
