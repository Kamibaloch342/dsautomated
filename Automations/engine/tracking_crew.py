"""
tracking_crew.py — FAST TRACKING: finding tracking ids and uploading them never wait for each other.

The normal cycle does one thing at a time: search Gmail for ten orders, upload what it found, search
the next ten. Measured 3 Nov 2026 with 2,560 open WB orders: 731 already had a tracking id, yet each
order with no shipping e-mail cost ~4 s of Gmail before anything was uploaded, and the queue was cut
to 1,000 orders. So here the two jobs run side by side:

    TRK-FIND    keeps asking the archive DB and Gmail about the orders with no tracking id yet
    TRK-UP1..   upload every order that has one, oldest first, each on its own Chrome window

What keeps this safe:
  * An order is taken off the ready list by the window that uploads it, so it is only ever in one.
  * upload() asks the order page first: already shipped means nothing is typed. A crash between
    Confirm and the record therefore costs nothing; the next try reads it off the page.
  * A failed order waits RETRY_S and never holds up the orders behind it.
  * A window that keeps throwing gives up alone; the others carry on.

This module knows nothing about Selenium or Gmail: both are handed in, so it can be exercised on its
own. Run from engine/tracking.py (`_fast_cycle`).
"""
import threading
import time


class _Window:
    def __init__(self, name, driver):
        self.name = name
        self.driver = driver
        self.thread = None
        self.fails = 0
        self.dead = False


class TrackingCrew:
    MAX_WINDOW_FAILS = 5   # exceptions in a row before a window gives up
    RETRY_S = 1800         # a failed order is not tried again for this long
    SWEEP_GAP_S = 60       # pause between two full Gmail sweeps
    CHUNK = 50             # WBs per Gmail query

    def __init__(self, orders, find, upload, connect=None, after_sweep=None, retry_at=None, max_uploads=0):
        """orders       [{"amazon_id", "wb_ids"}], oldest first
        find(wbs, services)  → {wb: (tracking_ids, shipped_time)}
        upload(driver, job)  → "success" | "already_shipped" | "failed"; raises when the window is broken
        connect()            → the Gmail services, built on the finder's own thread (not thread-safe)
        after_sweep()        → called on the finder's thread after each full sweep
        retry_at             → {amazon_id: epoch}, kept by the caller so a failure outlives the cycle
        max_uploads          → stop after this many successes (0 = no limit)"""
        self._find = find
        self._upload = upload
        self._connect = connect
        self._after_sweep = after_sweep
        self._retry_at = retry_at if retry_at is not None else {}
        self.max_uploads = max_uploads

        self._cv = threading.Condition()
        self._stop = False
        self._pos = {o["amazon_id"]: i for i, o in enumerate(orders)}
        self._waiting = {o["amazon_id"]: o for o in orders}   # no tracking id known yet
        self._ready = {}           # amazon_id -> job
        self._flying = 0

        self.windows = []
        self._finder = None
        self._t0 = 0.0
        self.uploaded = 0
        self.already_shipped = 0
        self.failed = 0
        self.sweeps = 0

    # ── Lookups ───────────────────────────────

    def seed(self, lookup, chunk=500):
        """Fill the ready list before any window starts. lookup(wbs) → {wb: (ids, shipped_time)}."""
        wbs = self._waiting_wbs()
        for i in range(0, len(wbs), chunk):
            self._add_found(lookup(wbs[i:i + chunk]) or {})

    def _waiting_wbs(self):
        with self._cv:
            return [wb for o in self._waiting.values() for wb in o["wb_ids"]]

    def _add_found(self, found):
        with self._cv:
            for aid in list(self._waiting):
                order = self._waiting[aid]
                ids, shipped = [], None
                for wb in order["wb_ids"]:
                    hit = found.get(wb)
                    if hit and hit[0]:
                        ids.extend(t for t in hit[0] if t not in ids)
                        shipped = shipped or hit[1]
                if ids:
                    del self._waiting[aid]
                    self._ready[aid] = dict(order, tracking_ids=ids, shipped_time=shipped)
            self._cv.notify_all()

    def _find_loop(self):
        services = {}
        if self._connect:
            try:
                services = self._connect() or {}
            except Exception as e:
                print(f"  ⚠️  TRK-FIND: Gmail not reachable ({str(e)[:100]}) — archive DB only this cycle")
        while not self._stop:
            wbs = self._waiting_wbs()
            for i in range(0, len(wbs), self.CHUNK):
                if self._stop:
                    return
                try:
                    self._add_found(self._find(wbs[i:i + self.CHUNK], services) or {})
                except Exception as e:
                    print(f"  ⚠️  TRK-FIND: {type(e).__name__}: {str(e)[:120]} — carrying on")
                    self._pause(5)
            self.sweeps += 1
            if self._after_sweep:
                try:
                    self._after_sweep()
                except Exception as e:
                    print(f"  ⚠️  TRK-FIND: after-sweep step failed: {str(e)[:120]}")
            self._pause(self.SWEEP_GAP_S)

    def _pause(self, seconds):
        with self._cv:
            if not self._stop:
                self._cv.wait(seconds)

    # ── Uploads ───────────────────────────────

    def _cap_hit(self):
        return bool(self.max_uploads) and self.uploaded + self._flying >= self.max_uploads

    def _next_job(self):
        with self._cv:
            while not self._stop:
                if not self._cap_hit():
                    now = time.time()
                    due = [a for a in self._ready if self._retry_at.get(a, 0) <= now]
                    if due:
                        self._flying += 1
                        return self._ready.pop(min(due, key=self._pos.get))
                self._cv.wait(1.0)
        return None

    def _upload_loop(self, w):
        while True:
            job = self._next_job()
            if job is None:
                return
            t0 = time.time()
            try:
                status = self._upload(w.driver, job)
                w.fails = 0
            except Exception as e:
                status = "failed"
                w.fails += 1
                print(f"  ⚠️  {w.name}: {job['amazon_id']}: {type(e).__name__}: {str(e)[:120]}")
            self._done(w, job, status, time.time() - t0)
            if w.fails >= self.MAX_WINDOW_FAILS:
                w.dead = True
                print(f"  ⚠️  {w.name} gave up after {w.fails} errors in a row")
                return

    def _done(self, w, job, status, secs):
        aid = job["amazon_id"]
        with self._cv:
            self._flying -= 1
            if status == "success":
                self.uploaded += 1
            elif status == "already_shipped":
                self.already_shipped += 1
            else:
                self.failed += 1
                self._retry_at[aid] = time.time() + self.RETRY_S
            self._cv.notify_all()
        mark = {"success": "✅", "already_shipped": "⏩"}.get(status, "❌")
        print(f"  {w.name}: {mark} {aid} {status} {', '.join(job.get('tracking_ids') or [])} ({secs:.0f} s)")

    # ── Lifecycle ─────────────────────────────

    def counts(self):
        with self._cv:
            return len(self._ready), len(self._waiting)

    def status_line(self):
        ready, waiting = self.counts()
        mins = max((time.time() - self._t0) / 60.0, 1 / 60.0)
        up = sum(1 for w in self.windows if not w.dead)
        return (f"⚡ FAST: {self.uploaded} uploaded, {self.uploaded / mins:.1f}/min | "
                f"{self.already_shipped} already shipped | {self.failed} failed | {ready} ready | "
                f"{waiting} no shipping e-mail yet | {up} window(s) | Gmail sweeps {self.sweeps}")

    def all_down(self):
        return bool(self.windows) and all(w.dead for w in self.windows)

    def _finished(self):
        with self._cv:
            nothing_left = not self._waiting and not self._ready and not self._flying
            capped = bool(self.max_uploads) and self.uploaded >= self.max_uploads
        return nothing_left or capped or self.all_down()

    def run(self, drivers, seconds, status_every=60):
        """Find and upload for `seconds`, then let every window finish its order and return."""
        self.windows = [_Window(f"TRK-UP{i}", d) for i, d in enumerate(drivers, 1)]
        self._t0 = time.time()
        self._finder = threading.Thread(target=self._find_loop, name="TRK-FIND", daemon=True)
        self._finder.start()
        for w in self.windows:
            w.thread = threading.Thread(target=self._upload_loop, name=w.name, daemon=True, args=(w,))
            w.thread.start()
        end, next_status = self._t0 + seconds, self._t0 + status_every
        while time.time() < end and not self._finished():
            time.sleep(0.25)
            if time.time() >= next_status:
                print("  " + self.status_line())
                next_status += status_every
        self.stop()
        print("  " + self.status_line())

    def stop(self, join_s=90):
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        for t in [w.thread for w in self.windows] + [self._finder]:
            if t:
                t.join(join_s)
