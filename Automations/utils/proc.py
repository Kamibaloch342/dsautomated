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
