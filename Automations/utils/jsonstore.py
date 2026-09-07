"""Cross-process safe JSON memories.

The two coupon placers (HS_COUPON and DM_COUPON) run at the same time on one
machine and share a few small JSON files: data/priority_verdicts.json,
data/dataset/coupon_intel.json, data/dataset/pdp_promo.json and the re-check
queues. A plain load → change → dump silently loses the other process's write
whenever the two overlap (a threading.Lock only serialises one process).

`mutate_json(path)` holds an advisory file lock for the whole read-modify-write
and writes atomically (tmp + os.replace). `locked(path)` gives the same lock to
code that wants to do its own reading/writing. A corrupt file is moved aside
(`<name>.corrupt-<ts>`) instead of being overwritten in place, so nothing is
lost without a trace.
"""
import json
import os
import random
import time
from contextlib import contextmanager

try:
    import fcntl
except ImportError:          # Windows
    fcntl = None


@contextmanager
def locked(path, timeout=15.0):
    """Exclusive advisory lock on `<path>.lock` (flock on POSIX, O_EXCL file on Windows)."""
    lock_path = path + ".lock"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    deadline = time.monotonic() + timeout
    fh = None
    if fcntl is not None:
        fh = open(lock_path, "a+")
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    fh.close()
                    raise TimeoutError(f"lock busy: {lock_path}")
                time.sleep(0.05 + random.random() * 0.1)
    else:
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    try:                                   # stale lock from a dead process
                        os.remove(lock_path)
                    except OSError:
                        pass
                    raise TimeoutError(f"lock busy: {lock_path}")
                time.sleep(0.05 + random.random() * 0.1)
    try:
        yield
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            fh.close()
        else:
            try:
                os.remove(lock_path)
            except OSError:
                pass


_MISSING = object()


def read_json(path, default=None, quarantine=False):
    """Parsed JSON, `default` when the file does not exist or cannot be parsed.
    quarantine=True additionally moves a corrupt file aside to `<path>.corrupt-<ts>`
    (used right before the file is rewritten, so nothing is overwritten in place);
    a plain read never touches the file."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception:
        if quarantine:
            try:
                bad = f"{path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
                os.replace(path, bad)
                print(f"  ⚠️  {os.path.basename(path)} was unreadable — moved to {os.path.basename(bad)}")
            except OSError:
                pass
        return default


def write_json_atomic(path, data, indent=0):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, sort_keys=isinstance(data, dict))
    os.replace(tmp, path)


@contextmanager
def mutate_json(path, default=None, indent=0):
    """`with mutate_json(p) as d:` — d is the current content (dict by default);
    whatever the block leaves in d is written back atomically, under the lock."""
    with locked(path):
        data = read_json(path, None, quarantine=True)
        if data is None:
            data = {} if default is None else default
        yield data
        write_json_atomic(path, data, indent)
