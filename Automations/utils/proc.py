"""
proc.py — cross-platform process helpers (Windows + macOS/Linux).

WHY THIS EXISTS: several places used Unix-only tricks that MISBEHAVE on Windows:
  - os.kill(pid, 0) as a liveness probe → on Windows os.kill with signal 0 calls
    TerminateProcess, i.e. it KILLS the process instead of probing it.
  - `lsof -ti :PORT` + os.kill(SIGKILL/SIGTERM) → lsof doesn't exist on Windows and
    signal.SIGKILL isn't even defined there.
This module gives one correct implementation used everywhere.
"""
import os
import platform
import subprocess

_IS_WIN = platform.system() == "Windows"


def pid_exists(pid):
    """True if a process with *pid* is alive. NEVER kills it (unlike os.kill(pid,0) on Windows)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if _IS_WIN:
        import ctypes
        SYNCHRONIZE = 0x00100000
        h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # exists, just not ours to signal
    except OSError:
        return False


def pids_on_port(port):
    """Return the PIDs listening on *port* (LISTEN state), cross-platform."""
    port = str(port)
    pids = set()
    try:
        if _IS_WIN:
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                 capture_output=True, text=True, timeout=8).stdout
            for line in out.splitlines():
                parts = line.split()
                # proto  local          foreign        state       pid
                if len(parts) >= 5 and parts[3].upper() == "LISTENING" \
                        and parts[1].rsplit(":", 1)[-1] == port:
                    if parts[-1].isdigit():
                        pids.add(int(parts[-1]))
        else:
            out = subprocess.run(["lsof", "-ti", f":{port}", "-sTCP:LISTEN"],
                                 capture_output=True, text=True, timeout=8).stdout
            for tok in out.split():
                if tok.isdigit():
                    pids.add(int(tok))
    except Exception:
        pass
    return pids


def kill_pid(pid):
    """Force-kill a single PID, cross-platform. Returns True on a best-effort attempt."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    try:
        if _IS_WIN:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=8)
        else:
            import signal
            os.kill(pid, signal.SIGKILL)
        return True
    except Exception:
        return False


def kill_pids_on_port(port):
    """Kill everything listening on *port*. Returns the count killed."""
    n = 0
    for pid in pids_on_port(port):
        if pid != os.getpid() and kill_pid(pid):
            n += 1
    return n


# ── Chrome browsers launched for the automation ─────────────────────────────
# Since the macOS 27 fix Chrome is started through LaunchServices ('open -a'), so it is no
# longer a child of the placer / tracking process: a SIGTERM to the process group no longer
# closes it. These helpers find the BROWSER process (not its --type= helpers) by the profile
# directory and/or the remote-debugging port and close it, gracefully first.

def _ps_lines():
    """[(pid, command)] of every process (macOS/Linux)."""
    out = []
    try:
        txt = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=8).stdout
        for line in txt.splitlines():
            line = line.strip()
            if not line:
                continue
            pid, _, cmd = line.partition(" ")
            if pid.isdigit():
                out.append((int(pid), cmd.strip()))
    except Exception:
        pass
    return out


def _norm_dir(p):
    p = str(p or "").strip().strip('"').strip("'")
    return p.rstrip("/\\").lower()


def _chrome_cmd_matches(cmd, user_data_dir=None, port=None):
    """True for a Chrome/Chromium BROWSER command line that runs the given profile dir
    and/or debugging port. Helpers (--type=renderer/gpu/utility) never match, nor does a
    Chrome without --user-data-dir (the user's own browser)."""
    c = cmd or ""
    low = c.lower()
    if "chrome" not in low and "chromium" not in low:
        return False
    if "--type=" in low or "helper" in low.split("--")[0]:
        return False
    if "--user-data-dir=" not in low:
        return False
    if user_data_dir is None and port is None:
        return False
    ok = True
    if user_data_dir is not None:
        want = _norm_dir(user_data_dir)
        have = ""
        for tok in c.split("--user-data-dir=")[1:]:
            have = tok.split(" --")[0].strip()
            break
        ok = ok and _norm_dir(have) == want
    if port is not None:
        ok = ok and f"--remote-debugging-port={int(port)}" in low
    return ok


def chrome_browser_pids(user_data_dir=None, port=None):
    """PIDs of the Chrome browser process(es) for a profile dir and/or debugging port."""
    if _IS_WIN:
        try:
            ps = subprocess.run(["powershell", "-NoProfile", "-Command",
                                 "Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" | "
                                 "ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }"],
                                capture_output=True, text=True, timeout=15).stdout
            out = set()
            for line in ps.splitlines():
                pid, _, cmd = line.strip().partition(" ")
                if pid.isdigit() and _chrome_cmd_matches(cmd, user_data_dir, port):
                    out.add(int(pid))
            return out
        except Exception:
            return set()
    return {pid for pid, cmd in _ps_lines() if _chrome_cmd_matches(cmd, user_data_dir, port)}


def terminate_pid(pid):
    """Ask a process to exit (SIGTERM; WM_CLOSE-style taskkill on Windows)."""
    try:
        pid = int(pid)
        if _IS_WIN:
            subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True, timeout=8)
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
        return True
    except Exception:
        return False


def close_chrome(user_data_dir=None, port=None, timeout=8.0, wait=True):
    """Close the automation's Chrome for this profile / port: SIGTERM the browser process
    (Chrome shuts down cleanly, so the profile is not marked crashed), wait up to `timeout`
    seconds, then SIGKILL whatever is left. Returns (asked, forced)."""
    import time as _t
    pids = set(chrome_browser_pids(user_data_dir, port))
    if port is not None:
        pids |= {p for p in pids_on_port(port) if p != os.getpid()}
    pids.discard(os.getpid())
    if not pids:
        return 0, 0
    for p in pids:
        terminate_pid(p)
    if not wait:
        return len(pids), 0
    deadline = _t.time() + max(0.0, float(timeout))
    alive = set(pids)
    while alive and _t.time() < deadline:
        _t.sleep(0.25)
        alive = {p for p in alive if pid_exists(p)}
    for p in alive:
        kill_pid(p)
    return len(pids), len(alive)
