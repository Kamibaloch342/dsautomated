#!/usr/bin/env python3
"""
dashboard_watchdog.py — keep the dashboard permanently up.

Run every minute by a Windows Scheduled Task (see install command in chat / README).
It is CHEAP and IDEMPOTENT:
  * if http://127.0.0.1:8000/ answers  -> do nothing, exit immediately.
  * if it does NOT answer               -> free the port and (re)start the server.

This is what makes the dashboard "always running": it comes back after a reboot
(the task re-arms itself and fires within a minute of you logging in) and it
self-heals if the server ever crashes. No long-lived supervisor process to die —
the Windows Task Scheduler is the supervisor.
"""
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
for _p in (HERE, AUTO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PORT = "8000"


def _up():
    try:
        # any HTTP response (even the login page / a 4xx) means the server is alive
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=5)
        return True
    except urllib.error.HTTPError:
        return True   # responded, just gated — still "up"
    except Exception:
        return False


def main():
    if _up():
        return
    # Down: clear anything squatting on the port (a hung server), then start fresh.
    import start_dashboard
    try:
        start_dashboard._stop()
    except Exception:
        pass
    start_dashboard._start()


if __name__ == "__main__":
    main()
