"""
placement_lock.py — hard, local, authoritative de-duplication lock for the Zoro placer.

WHY THIS EXISTS
---------------
The 2026-06/07 duplicate-order incident (~$11.7k, 116 orders re-bought up to 41x each)
happened because the placer had NO local record of what it had already purchased. Its
only guards were both AFTER-THE-FACT external round-trips:
  1) a "WB..." id written to / re-scraped from Amazon's own UI (fire-and-forget,
     unverified, return value ignored), and
  2) a Gmail search that was blind to some inboxes, lost an index-lag race, and
     FAILED OPEN (any error -> "no duplicate" -> buy again).
When either signal was missing, the infinite loop re-bought the same order every cycle.

THE FIX (this module)
---------------------
A claim is RESERVED for an Amazon order id BEFORE any money is spent, using a UNIQUE
primary key for atomicity. If a claim already exists, placement is HARD-BLOCKED — the
placer never tries a 2nd time. The claim is reconciled after the outcome is known:
  - CONFIRMED   : order placed (permanent block)
  - RELEASED    : clean pre-payment failure -> a legit retry is allowed next cycle
  - FAILED_KEPT : unknown / post-submit failure (money may have been spent) ->
                  PERMANENT block + manual review, so a WB-less spend can never re-buy.

FAIL-CLOSED: if the lock DB cannot be reached, reserve() returns ERROR and the caller
MUST NOT place. Losing the lock must never mean "buy freely".

Cross-process safe (shared automations_unified.db + UNIQUE constraint), so two placer
profiles cannot both claim the same order.
"""
import os
import random
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime

try:                      # POSIX only — the placer also runs on Windows, where the
    import fcntl          # queue degrades to retry-only (which alone fixes starvation:
except ImportError:       # the writers that starve us are other processes anyway).
    fcntl = None

_HERE = os.path.dirname(os.path.abspath(__file__))
_AUTOMATIONS_DIR = os.path.dirname(_HERE)
DB_PATH = os.path.join(_AUTOMATIONS_DIR, "data", "automations_unified.db")
ORDER_LOG = os.path.join(_AUTOMATIONS_DIR, "data", "rajar_logs", "order_log.txt")

# ── WRITE QUEUE (why this exists) ───────────────────────────────────────────
# placer_claims shares automations_unified.db (1.3 GB) with 4 concurrent placers,
# the always-on dashboard and the 10-min archive sync. SQLite allows exactly ONE
# writer file-wide, so a burst (e.g. HS_RESELLER writing out a 1,257-order sweep)
# can starve another placer past its busy_timeout. On 2026-08-18 00:29:28 that
# made reserve() fail after waiting the full 60 s and refuse a legitimate order;
# it had happened 38 times before that.
#
# The cure is to QUEUE instead of giving up: every claim write takes an OS file
# lock first, so our own processes take orderly turns rather than colliding, and
# each attempt then retries with jittered backoff until a generous deadline. The
# file lock is held by a file DESCRIPTOR, so a crashed placer's turn is released
# by the kernel automatically — no stale lock can ever wedge the queue.
#
# FAIL-CLOSED IS UNCHANGED: if the deadline is exhausted, reserve() still returns
# ERROR and the caller still refuses to place. We only stopped giving up early.
QUEUE_FILE = os.path.join(_AUTOMATIONS_DIR, "data", "placer_claims.queue.lock")
# reserve(): waiting beats skipping a real order. confirm()/release() get longer —
# losing THAT write leaves a paid order stuck PLACING → FAILED_KEPT for manual
# review (exactly how 3 shipped orders got stranded in July/August).
RESERVE_MAX_WAIT = int(os.environ.get("PLOCK_RESERVE_WAIT", "240"))
UPDATE_MAX_WAIT = int(os.environ.get("PLOCK_UPDATE_WAIT", "600"))
_BUSY_MS = int(os.environ.get("PLOCK_BUSY_MS", "15000"))   # per attempt, then retry

# reserve() outcomes
NEW = "NEW"          # claim created — safe to place
BLOCKED = "BLOCKED"  # already claimed (PLACING/CONFIRMED/FAILED_KEPT) — DO NOT place
ERROR = "ERROR"      # lock DB unreachable — FAIL CLOSED, DO NOT place

# Claim statuses that permanently block a re-placement.
_BLOCKING = ("PLACING", "CONFIRMED", "FAILED_KEPT")

_MEM = set()                 # in-process amazon_ids already claimed this run (fast path)
_LK = threading.Lock()       # serialize reserve() within a process


def _conn(busy_ms=60000):
    """Default keeps the original 60 s patience for the direct (non-retrying)
    callers — init(), reconcile_orphans(), locked_orders(). _write() passes the
    shorter _BUSY_MS because it re-queues and tries again instead of waiting once."""
    c = sqlite3.connect(DB_PATH, timeout=busy_ms / 1000.0)
    c.execute(f"PRAGMA busy_timeout={busy_ms}")
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _is_busy(exc):
    """SQLite's 'wait your turn' errors — retryable. Anything else is a real fault."""
    return isinstance(exc, sqlite3.OperationalError) and \
        any(s in str(exc).lower() for s in ("locked", "busy"))


@contextmanager
def _queue_turn(deadline, label):
    """Wait for this process's turn in the cross-process write queue.

    flock is advisory and tied to the open descriptor: if the holder dies, the
    kernel drops it, so the queue self-heals. Raises TimeoutError past deadline.
    A queue file we cannot even open must NOT block placement — the SQLite
    busy_timeout still protects correctness — so that degrades to no queueing.
    """
    if fcntl is None:                     # Windows: no flock — retry/backoff only
        yield
        return
    try:
        fh = open(QUEUE_FILE, "a+")
    except OSError as e:
        print(f"   [LOCK] queue file unusable ({e}) — proceeding without queueing")
        yield
        return
    try:
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"{label}: no turn in the write queue")
                # Jitter so four placers released together don't collide again.
                time.sleep(0.2 + random.random() * 0.3)
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        fh.close()


def _write(op, max_wait, label):
    """Run op(conn) inside one immediate write transaction, queued and retried.

    BEGIN IMMEDIATE takes the write lock up front, so we can never grab a read
    snapshot and then fail while upgrading it mid-claim. Returns op's value.
    Retries only on SQLite busy/locked; every other error propagates unchanged so
    IntegrityError still signals a lost claim race.
    """
    deadline = time.monotonic() + max_wait
    attempt = 0
    while True:
        attempt += 1
        try:
            with _queue_turn(deadline, label):
                c = _conn(_BUSY_MS)
                try:
                    _ensure(c)
                    c.execute("BEGIN IMMEDIATE")
                    out = op(c)
                    c.commit()
                    return out
                except Exception:
                    try:
                        c.rollback()
                    except Exception:
                        pass
                    raise
                finally:
                    c.close()
        except Exception as e:
            if not _is_busy(e) or time.monotonic() >= deadline:
                raise
            wait = min(2.0, 0.15 * attempt) + random.random() * 0.3
            if attempt in (5, 20, 60):     # occasional breadcrumb, not a flood
                left = int(deadline - time.monotonic())
                print(f"   [LOCK] {label}: DB busy, still queueing "
                      f"(attempt {attempt}, {left}s left)")
            time.sleep(wait)


def _ensure(c):
    c.execute(
        """CREATE TABLE IF NOT EXISTS placer_claims(
            amazon_order_id TEXT PRIMARY KEY,
            status          TEXT NOT NULL,      -- PLACING / CONFIRMED / RELEASED / FAILED_KEPT
            wb_order_id     TEXT,
            profile         TEXT,
            attempts        INTEGER DEFAULT 1,
            claimed_at      TIMESTAMP,
            reconciled_at   TIMESTAMP,
            note            TEXT
        )"""
    )


def _placed_ids_from_log(path):
    """{amazon_id: first_wb} for every successful placement in order_log.txt."""
    out = {}

    def field(line, key):
        m = re.search(r"\b" + re.escape(key) + r":([^|]*)", line)
        return m.group(1).strip() if m else ""

    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            wb = field(line, "WB")
            amz = field(line, "Amazon")
            if wb.startswith("WB") and amz:
                out.setdefault(amz, wb)  # keep the FIRST (legitimate) placement
    return out


def init(seed_log=True):
    """Create the table and seed the block-set from history. Call once at placer startup.

    Returns True on success. If it returns False, reserve() will still fail-closed per call,
    but the caller should treat a failed init as a reason to halt rather than run blind."""
    c = None
    try:
        c = _conn()
        _ensure(c)
        seeded = 0
        if seed_log and os.path.exists(ORDER_LOG):
            ids = _placed_ids_from_log(ORDER_LOG)
            now = datetime.now().isoformat()
            with c:
                for amz, wb in ids.items():
                    c.execute(
                        "INSERT OR IGNORE INTO placer_claims"
                        "(amazon_order_id,status,wb_order_id,profile,claimed_at,reconciled_at,note)"
                        " VALUES(?,?,?,?,?,?,?)",
                        (amz, "CONFIRMED", wb, "history", now, now, "seeded from order_log.txt"),
                    )
            _MEM.update(ids.keys())
            seeded = len(ids)
        # Always load any already-blocking claims into memory (covers a no-seed restart).
        for (amz,) in c.execute(
            "SELECT amazon_order_id FROM placer_claims WHERE status IN ('PLACING','CONFIRMED','FAILED_KEPT')"
        ):
            _MEM.add(amz)
        c.close()
        c = None
        # Heal any PLACING claims orphaned by a crashed prior run → FAILED_KEPT (visible for review).
        reconcile_orphans()
        print(f"   [LOCK] ready — {len(_MEM)} amazon_ids blocked "
              f"({seeded} seeded from order_log, DB={os.path.basename(DB_PATH)})")
        return True
    except Exception as e:
        print(f"   [LOCK] init FAILED — fail-closed will apply to every order: {e}")
        return False
    finally:
        if c is not None:
            try:
                c.close()
            except Exception:
                pass


def already_seen(amazon_id):
    """Fast in-memory check (no DB hit). True => already placed/claimed this run or in history."""
    return amazon_id in _MEM


def claim_info(amazon_id):
    """Return {'status':..., 'wb':...} for an amazon_id's claim, or None. Cheap lookup used only
    on the skip path (when already_seen is True) so the console can say WHY it's skipping —
    'already placed' (CONFIRMED, normal dedup) vs 'held for review' (FAILED_KEPT → Locked Orders)."""
    try:
        c = _conn()
        r = c.execute("SELECT status, wb_order_id FROM placer_claims WHERE amazon_order_id=?",
                      (amazon_id,)).fetchone()
        c.close()
        return {"status": r[0], "wb": r[1]} if r else None
    except Exception:
        return None


def reserve(amazon_id, profile=""):
    """Atomically CLAIM amazon_id BEFORE spending any money.

    Returns (state, info):
      (NEW, ...)     -> claim created; safe to proceed to checkout
      (BLOCKED, ...) -> a claim already exists; caller MUST skip (never place)
      (ERROR, None)  -> lock DB unreachable; FAIL CLOSED, caller MUST skip
    """
    with _LK:
        if amazon_id in _MEM:
            return BLOCKED, {"status": "MEM"}

        def _claim(c):
            row = c.execute(
                "SELECT status,wb_order_id,attempts FROM placer_claims WHERE amazon_order_id=?",
                (amazon_id,),
            ).fetchone()
            if row is not None:
                status, wb, attempts = row[0], row[1], row[2]
                if status in _BLOCKING:
                    return (BLOCKED, {"status": status, "wb": wb, "attempts": attempts})
                # status == RELEASED: a prior clean pre-payment failure — retry allowed.
                c.execute(
                    "UPDATE placer_claims SET status='PLACING', profile=?, attempts=attempts+1,"
                    " claimed_at=?, reconciled_at=NULL, note='re-reserve after release' WHERE amazon_order_id=?",
                    (profile, datetime.now().isoformat(), amazon_id),
                )
                return (NEW, {"reclaim": True, "attempts": (attempts or 1) + 1})
            c.execute(
                "INSERT INTO placer_claims(amazon_order_id,status,profile,attempts,claimed_at,note)"
                " VALUES(?,?,?,?,?,?)",
                (amazon_id, "PLACING", profile, 1, datetime.now().isoformat(), "reserved"),
            )
            return (NEW, None)

        try:
            result = _write(_claim, RESERVE_MAX_WAIT, f"reserve {amazon_id}")
            _MEM.add(amazon_id)
            return result
        except sqlite3.IntegrityError:
            # Lost the race to a concurrent claim between SELECT and INSERT — treat as blocked.
            _MEM.add(amazon_id)
            return BLOCKED, {"status": "RACE"}
        except Exception as e:
            # Queued and retried for the full RESERVE_MAX_WAIT and still no write turn.
            print(f"   [LOCK] reserve FAILED for {amazon_id} after {RESERVE_MAX_WAIT}s "
                  f"of queueing — FAIL CLOSED: {e}")
            return ERROR, None


def confirm(amazon_id, wb_order_id, note="placed"):
    """Order placed successfully — permanent block. Returns True when persisted.
    The in-memory block is set either way, so this run never re-places it."""
    ok = _update(amazon_id, "CONFIRMED", wb=wb_order_id, note=note)
    _MEM.add(amazon_id)
    return ok


def keep_failed(amazon_id, note="unknown failure after possible spend — manual review"):
    """Outcome unknown / money may have been spent — PERMANENT block, never auto-retry."""
    ok = _update(amazon_id, "FAILED_KEPT", note=note)
    _MEM.add(amazon_id)
    return ok


def release(amazon_id, note="clean pre-payment failure — retry allowed"):
    """Guaranteed no money spent — release so a legitimate retry can happen next cycle."""
    ok = _update(amazon_id, "RELEASED", note=note)
    _MEM.discard(amazon_id)
    return ok


def override(amazon_id, note="manual override — allow re-placement"):
    """Manual escape hatch: allow re-placing an amazon_id that was previously placed
    (e.g. the Zoro order was cancelled and genuinely needs re-ordering)."""
    _update(amazon_id, "RELEASED", note=note)
    _MEM.discard(amazon_id)


def refresh_releases():
    """Drop from the in-memory block-set any ids RELEASED in the DB (e.g. via the launcher's
    Unlock button or scripts/unlock_order.py) since this process started, so a RUNNING placer
    honours unlocks WITHOUT a restart. Call once per cycle. Returns the count of released ids."""
    try:
        c = _conn()
        rows = c.execute(
            "SELECT amazon_order_id FROM placer_claims WHERE status='RELEASED'").fetchall()
        c.close()
        for (amz,) in rows:
            _MEM.discard(amz)
        return len(rows)
    except Exception as e:
        print(f"   [LOCK] refresh_releases FAILED: {e}")
        return 0


def reconcile_orphans(max_age_minutes=10):
    """Heal orphaned PLACING claims: a claim reserved before checkout but NEVER reconciled
    (no SUCCESS / release / keep_failed — e.g. the placer crashed or errored mid-checkout) is
    stuck blocking its order forever while being invisible. Any PLACING older than
    max_age_minutes (a real placement reconciles in ~20-30s) is marked FAILED_KEPT so it surfaces
    in the Locked Orders tab for review. Call each cycle + at startup. Returns count healed."""
    c = None
    try:
        from datetime import timedelta
        c = _conn()
        _ensure(c)
        cutoff = (datetime.now() - timedelta(minutes=max_age_minutes)).isoformat()
        now = datetime.now().isoformat()
        with c:
            cur = c.execute(
                "UPDATE placer_claims SET status='FAILED_KEPT', reconciled_at=?, "
                "note='orphaned mid-placement (no outcome) — verify on Zoro before unlocking' "
                "WHERE status='PLACING' AND (reconciled_at IS NULL OR reconciled_at < claimed_at) AND claimed_at < ?",
                (now, cutoff))
            n = cur.rowcount
        c.close()
        c = None
        if n:
            print(f"   [LOCK] reconciled {n} orphaned PLACING claim(s) → FAILED_KEPT (held for review)")
        return n
    except Exception as e:
        print(f"   [LOCK] reconcile_orphans FAILED: {e}")
        return 0
    finally:
        if c is not None:
            try:
                c.close()
            except Exception:
                pass


def locked_orders(stale_placing_minutes=10):
    """Claims needing human review, for the launcher's 'Locked Orders' tab:
      • FAILED_KEPT — checkout failed with an unknown error (money may have been spent), and
      • orphaned PLACING — reserved before checkout but never reconciled and older than
        stale_placing_minutes (stuck mid-placement).
    Both are blocking the order and need a human to verify on Zoro and unlock. Returns dicts with
    amazon_order_id, profile, claimed_at, reconciled_at, note, status."""
    c = None
    try:
        from datetime import timedelta
        c = _conn()
        _ensure(c)
        rows = [dict(amazon_order_id=r[0], profile=r[1], claimed_at=r[2],
                     reconciled_at=r[3], note=r[4], status="FAILED_KEPT")
                for r in c.execute(
                    "SELECT amazon_order_id,profile,claimed_at,reconciled_at,note FROM placer_claims "
                    "WHERE status='FAILED_KEPT' ORDER BY COALESCE(reconciled_at,claimed_at) DESC")]
        cutoff = (datetime.now() - timedelta(minutes=stale_placing_minutes)).isoformat()
        for r in c.execute(
                "SELECT amazon_order_id,profile,claimed_at FROM placer_claims "
                "WHERE status='PLACING' AND (reconciled_at IS NULL OR reconciled_at < claimed_at) AND claimed_at < ? "
                "ORDER BY claimed_at DESC", (cutoff,)):
            rows.append(dict(amazon_order_id=r[0], profile=r[1], claimed_at=r[2], reconciled_at=None,
                             note="stuck mid-placement (no outcome) — verify on Zoro before unlocking",
                             status="PLACING(stuck)"))
        c.close()
        c = None
        return rows
    except Exception as e:
        print(f"   [LOCK] locked_orders FAILED: {e}")
        return []
    finally:
        if c is not None:
            try:
                c.close()
            except Exception:
                pass


def _update(amazon_id, status, wb=None, note=""):
    """Reconcile a claim to its outcome. Queued + retried like reserve(): losing
    this write is what strands a PAID order in PLACING until reconcile_orphans
    downgrades it to FAILED_KEPT for manual review. Returns True when persisted."""
    def _apply(c):
        now = datetime.now().isoformat()
        cur = c.execute(
            "UPDATE placer_claims SET status=?, wb_order_id=COALESCE(?,wb_order_id),"
            " reconciled_at=?, note=? WHERE amazon_order_id=?",
            (status, wb, now, note, amazon_id),
        )
        if cur.rowcount == 0:
            c.execute(
                "INSERT OR IGNORE INTO placer_claims"
                "(amazon_order_id,status,wb_order_id,claimed_at,reconciled_at,note)"
                " VALUES(?,?,?,?,?,?)",
                (amazon_id, status, wb, now, now, note),
            )
        return True

    try:
        return _write(_apply, UPDATE_MAX_WAIT, f"{status} {amazon_id}")
    except Exception as e:
        # Loud: for CONFIRMED this means a placed order is still marked PLACING.
        print(f"   [LOCK] ❌ _update({status}) FAILED for {amazon_id} after "
              f"{UPDATE_MAX_WAIT}s of queueing: {e}")
        if status == "CONFIRMED":
            print(f"   [LOCK] ⚠️  {amazon_id} WAS PLACED (WB {wb or '?'}) but the claim could "
                  f"not be marked CONFIRMED — it will surface in Locked Orders for review. "
                  f"DO NOT re-place it.")
        return False
