"""One Seller Central Chrome per store, several users (28 Sep 2026).

The tracking uploader, closing's report download, the exception-only Seller Central scrape and
messaging all drive the SAME Chrome per store (HOANK 9331, Hathoro 9333). Run at the same time
they navigate each other's tab — 19 Sep: the HOANK tracker halted mid report-download on a review
page — and closing's download folder captures the tracker's reports. With auto-closing at 04:00
next to trackers that run all day, that became a daily event.

`sc_turn(port, who)` gives one unit of work the Chrome to itself — one tracker cycle, one report
download, one scrape pass, one buyer message — while the others wait for their turn. It is an
exclusive lock on data/sc_chrome_<port>.lock (utils.jsonstore.locked: flock, released by the
kernel even when a process dies, so it can never go stale). Units are short, so nobody waits
long: messaging takes a turn per order, so tracking uploads never stop for the length of a
messaging run (late shipments).

Waiting is bounded. With on_timeout="skip" (the tracker) a turn that does not come within
`max_wait` raises SCBusy and the caller skips that cycle; with "proceed" (closing, scrape,
messaging) the caller goes ahead with a warning. A lock that cannot be used at all (disk,
permissions) never blocks anyone: the work runs as it did before this existed.
"""
import os
import time
from contextlib import contextmanager

from utils.jsonstore import locked

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


class SCBusy(Exception):
    """The Seller Central Chrome stayed busy for longer than the caller wanted to wait."""


def _owner_path(port):
    return os.path.join(DATA, f"sc_chrome_{int(port)}.owner")


def holder(port) -> str:
    """Who holds the turn right now (for log lines), '' when unknown."""
    try:
        with open(_owner_path(port), "r", encoding="utf-8") as f:
            return f.read().strip()[:120]
    except OSError:
        return ""


@contextmanager
def sc_turn(port, who: str, max_wait: float = 900.0, on_timeout: str = "proceed"):
    """Hold the Seller Central Chrome on `port` for the duration of the with-block.
    Yields True when the turn is held, False when the caller goes ahead without it."""
    path = os.path.join(DATA, f"sc_chrome_{int(port)}")
    t0 = time.monotonic()
    cm, announced = None, False
    while True:
        left = max_wait - (time.monotonic() - t0)
        try:
            # a short first try so a wait is announced at once, then half-minute slices
            cm = locked(path, timeout=max(0.5, min(2.0 if not announced else 30.0, left)))
            cm.__enter__()
            break
        except TimeoutError:
            cm = None
            if not announced:
                who_has = holder(port)
                print(f"  ⏳ [{who}] Seller Central Chrome {port} is busy"
                      + (f" ({who_has})" if who_has else "") + " — waiting for its turn…", flush=True)
                announced = True
            if time.monotonic() - t0 >= max_wait:
                if on_timeout == "skip":
                    raise SCBusy(f"Seller Central Chrome {port} busy for {max_wait / 60:.0f} min ({holder(port)})")
                print(f"  ⚠️ [{who}] waited {max_wait / 60:.0f} min for Chrome {port} — going ahead", flush=True)
                break
        except Exception as e:                       # the lock itself is unusable: never block the work
            cm = None
            print(f"  ⚠️ [{who}] Chrome turn lock unavailable ({type(e).__name__}: {e}) — going ahead", flush=True)
            break
    if cm is not None:
        if announced:
            print(f"  ▶️ [{who}] Seller Central Chrome {port}: my turn "
                  f"(waited {time.monotonic() - t0:.0f}s)", flush=True)
        try:
            with open(_owner_path(port), "w", encoding="utf-8") as f:
                f.write(f"{who} · pid {os.getpid()} · since {time.strftime('%H:%M:%S')}")
        except OSError:
            pass
    try:
        yield cm is not None
    finally:
        if cm is not None:
            try:
                os.remove(_owner_path(port))
            except OSError:
                pass
            try:
                cm.__exit__(None, None, None)
            except Exception:
                pass
