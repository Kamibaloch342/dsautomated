#!/usr/bin/env python3
"""
install_dashboard_autostart.py — make the dashboard ALWAYS-ON on Windows.

Registers a Scheduled Task "AutomationsDashboard" that runs dashboard_watchdog.py
every minute (via pythonw.exe, so no console window ever flashes). The watchdog is
cheap and only acts when the dashboard is down, so this:
  * brings the dashboard back within ~1 min of a reboot / login,
  * self-heals if the server ever crashes,
  * runs as the logged-on user with LIMITED rights (no stored password).

    python scripts\\install_dashboard_autostart.py              # install / update
    python scripts\\install_dashboard_autostart.py --uninstall  # remove it

Paths are derived from THIS file's location, so it works wherever Automations lives.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))     # ...\Automations\scripts
ROOT = os.path.dirname(os.path.dirname(HERE))         # parent of Automations (holds .venv)
TASK = "AutomationsDashboard"
PYW = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")
WD = os.path.join(HERE, "dashboard_watchdog.py")


def main():
    if os.name != "nt":
        print("Windows-only (uses Task Scheduler). Nothing done.")
        return

    if "--uninstall" in sys.argv:
        r = subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"],
                           capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip() or f"removed '{TASK}'.")
        return

    if not os.path.exists(PYW):
        print(f"[FAIL] pythonw not found at:\n   {PYW}\n"
              f"Run  py -3 scripts\\windows_bootstrap.py  first to build the venv.")
        sys.exit(1)
    if not os.path.exists(WD):
        print(f"[FAIL] watchdog not found at:\n   {WD}\n"
              f"Pull dashboard_watchdog.py into scripts\\ first.")
        sys.exit(1)

    # subprocess quotes this whole argument correctly for schtasks — no manual escaping.
    tr = f'"{PYW}" "{WD}"'
    r = subprocess.run(
        ["schtasks", "/Create", "/TN", TASK, "/TR", tr,
         "/SC", "MINUTE", "/MO", "1", "/RL", "LIMITED", "/F"],
        capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode == 0:
        print(f"\n[ OK ] '{TASK}' installed. The dashboard is now always-on:")
        print("       - survives closing CMD (already did) AND reboots")
        print("       - restarts itself within a minute if it ever crashes")
        # kick it off right now so you don't wait for the first tick
        subprocess.run(["schtasks", "/Run", "/TN", TASK], capture_output=True)
        print("       - started it now.  Manage it:  Task Scheduler > AutomationsDashboard")
    else:
        print(f"\n[FAIL] schtasks returned {r.returncode} — see the message above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
