"""
launcher_state.py — is the Tk launcher (gui/launcher.py) running?

Owner's hard rule (16 Oct 2026): when the launcher is closed, NO alert goes out — not WhatsApp
(the gateway holds its queue, whatsapp_gateway/server.js) and not Telegram (dropped here, see
utils/telegram.py). Same check as the gateway: pgrep for a Python process running
gui/launcher.py; any error counts as closed (fail closed). Cached for 5 s.
"""
import subprocess
import time

_PATTERN = r"[Pp]ython.*gui/launcher\.py"   # "[Pp]ython" never matches its own text
_cache = {"at": 0.0, "up": False}


def launcher_running(max_age=5.0) -> bool:
    now = time.time()
    if now - _cache["at"] < max_age:
        return _cache["up"]
    try:
        up = subprocess.run(["pgrep", "-f", _PATTERN], capture_output=True, timeout=5).returncode == 0
    except Exception:
        up = False
    _cache.update(at=now, up=up)
    return up


if __name__ == "__main__":
    print("launcher running" if launcher_running() else "launcher closed — alerts held")
