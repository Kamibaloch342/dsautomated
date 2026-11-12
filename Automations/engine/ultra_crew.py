"""
ultra_crew.py — ULTRA MODE: a placer's Amazon work runs beside the placing, not between it.

Rush mode stops buying for its Amazon work. Measured 2 Nov 2026 on HS_RESELLER's 995-order queue:
every 100 orders cost 10-17 min scraping order pages + 40-47 min placing + 27-36 min writing WB
labels back, so for more than half of the run nothing was being bought.

Here two background threads each own a chromedriver session on the placer's Chrome:

    AMZ-SCRAPE  reads the next orders' pages, a few orders AHEAD of the placer
    AMZ-WRITE   writes the journaled WB labels + seller notes BEHIND it

Each has its own WINDOW, not a tab. A new tab makes the Zoro tab `hidden` and Chrome throttles its
timers (measured: 4 ticks in 3 s instead of 30); a window leaves it visible and untouched. And two
windows, not one: a scrape costs ~10 s and a write-back ~13 s, so a single window would need ~23 s
per order and become the new limit.

What keeps this safe:
  * Every money decision stays on the main thread, one order at a time: duplicate check, placement
    lock, margin, checkout. Nothing in this file can buy anything.
  * These threads never recycle the browser — a stall here must not kill a checkout in flight.
    Only the main thread does, with pause() / resume() around it.
  * They never touch Gmail: the API client objects are not thread-safe.
  * Every failure falls back to plain rush behaviour: an order with no scrape result is scraped by
    the main thread as always, and if the writer dies the main thread's batched flush takes over.
  * A write-back leaves the journal only once Amazon shows it, so a crash loses nothing.

This module knows nothing about orders.py: the Chrome and Amazon work is handed in, so it can be
exercised on its own against a throwaway Chrome.
"""
import threading
import time


class LoginWall(Exception):
    """Seller Central asked to sign in. Only the main thread logs in, and only one thread may."""


class _Worker:
    def __init__(self, name):
        self.name = name
        self.driver = None
        self.thread = None
        self.fails = 0
        self.parked = False
        self.dead = False


class UltraCrew:
    MAX_FAILS = 5          # consecutive failures before a worker gives up and rush takes over
    FAIL_BACKOFF_S = 5
    LOGIN_BACKOFF_S = 30
    SCRAPE_TRIES = 2       # per order, then the main thread scrapes it itself

    def __init__(self, attach, release, scrape_one, write_one, next_write, ahead=12):
        """attach()                  → a driver on a new window of the placer's Chrome (raises on failure)
        release(driver, close=True)  → end that session; never touches Chrome or any other window
        scrape_one(driver, order)    → detail dict, or None; may raise LoginWall
        write_one(driver, entry)     → True when Amazon shows it; may raise LoginWall
        next_write(skip_id)          → the next journal entry to write, or None
        ahead                        → how many orders past the placer's position to scrape"""
        self._attach = attach
        self._release = release
        self._scrape_one = scrape_one
        self._write_one = write_one
        self._next_write = next_write
        self.ahead = ahead

        self._cv = threading.Condition()
        self._stop = False
        self._paused = False
        self._wall_until = 0.0

        self._orders = []
        self._index = {}
        self._head = 0             # the order the placer is on
        self._cursor = 0           # the next order to scrape
        self._details = {}
        self._missed = set()       # orders the main thread has to scrape itself
        self._tries = {}

        self.busy_id = None        # the order in process_order: its journal entry may still grow
        self.scraped = 0
        self.written = 0

        self._scraper = _Worker("AMZ-SCRAPE")
        self._writer = _Worker("AMZ-WRITE")
        self._workers = (self._scraper, self._writer)

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self, orders):
        """Attach both windows HERE, on the caller's thread: a Chrome that refuses a second session
        is then known before anything depends on it. False → the caller stays on plain rush."""
        self._orders = list(orders)
        self._index = {o["amazon_id"]: i for i, o in enumerate(self._orders)}
        for w in self._workers:
            try:
                w.driver = self._attach()
            except Exception as e:
                print(f"  ⚠️  ULTRA: could not open the {w.name} window ({type(e).__name__}: {str(e)[:160]})")
                for other in self._workers:
                    self._drop(other)
                return False
        self._scraper.thread = threading.Thread(
            target=self._run, name=self._scraper.name, daemon=True,
            args=(self._scraper, self._pick_scrape, self._scrape_one, self._done_scrape))
        self._writer.thread = threading.Thread(
            target=self._run, name=self._writer.name, daemon=True,
            args=(self._writer, self._pick_write, self._write_one, self._done_write))
        for w in self._workers:
            w.thread.start()
        return True

    def stop(self, join_s=20):
        """End both workers, each closing its own window. One stuck in a page load is unstuck by
        killing its chromedriver, which leaves Chrome and the placing window running."""
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        for w in self._workers:
            if w.thread:
                w.thread.join(join_s)
        self.kill()
        for w in self._workers:
            if w.thread:
                w.thread.join(5)

    def kill(self):
        """Hard stop for process exit: no waiting and no browser commands, which a stuck page
        would block. Safe to call from the signal handler."""
        self._stop = True
        for w in self._workers:
            drv, w.driver = w.driver, None
            if drv is not None:
                try:
                    self._release(drv, close=False)
                except Exception:
                    pass

    def pause(self, timeout=20):
        """Park both workers, before the main thread recycles the browser. Each ends its session
        first: it dies with that Chrome anyway, and re-attaches on resume()."""
        with self._cv:
            self._paused = True
            self._cv.notify_all()
            end = time.time() + timeout
            while time.time() < end and not all(
                    w.parked or not (w.thread and w.thread.is_alive()) for w in self._workers):
                self._cv.wait(0.25)

    def resume(self):
        with self._cv:
            self._paused = False
            self._cv.notify_all()

    # ── What the main thread asks ─────────────────────────────────────

    def scraper_alive(self):
        return bool(self._scraper.thread and self._scraper.thread.is_alive() and not self._scraper.dead)

    def writer_alive(self):
        return bool(self._writer.thread and self._writer.thread.is_alive() and not self._writer.dead)

    def get_detail(self, amazon_id, wait_s=30):
        """This order's scraped page, waiting for it if the scraper is on it. None → the main
        thread scrapes it itself, as it always has."""
        idx = self._index.get(amazon_id)
        if idx is None:
            return None
        with self._cv:
            self._head = max(self._head, idx)
            self._cv.notify_all()
            end = time.time() + wait_s
            while True:
                d = self._details.pop(amazon_id, None)
                if d is not None:
                    return d
                if amazon_id in self._missed or self._stop:
                    return None
                # Behind a login wall the main thread must take over: only it can sign in.
                if not self.scraper_alive() or time.time() < self._wall_until:
                    return None
                left = end - time.time()
                if left <= 0:
                    self._missed.add(amazon_id)
                    return None
                self._cv.wait(min(left, 0.25))

    def peek_detail(self, amazon_id):
        """A copy of this order's scraped page if the scraper already has it, without taking it (the
        order may still be placed on its own later) and without moving the scraper. None otherwise."""
        import copy
        with self._cv:
            d = self._details.get(amazon_id)
            return copy.deepcopy(d) if d is not None else None

    def ahead_details(self):
        """[(order, detail)] for what is scraped past the placer, for the batched duplicate check."""
        with self._cv:
            out = []
            for o in self._orders[self._head + 1:self._head + 1 + self.ahead]:
                d = self._details.get(o["amazon_id"])
                if d is not None:
                    out.append((o, d))
            return out

    def status_line(self):
        with self._cv:
            ready = len(self._details)
        # Only a worker that gave up counts as down: after stop() both are simply finished.
        return (f"scraped {self.scraped} ({ready} ready ahead) | written back {self.written}"
                f"{' | ⚠️ scrape window down' if self._scraper.dead else ''}"
                f"{' | ⚠️ write window down' if self._writer.dead else ''}")

    # ── The workers ───────────────────────────────────────────────────

    def _pick_scrape(self):
        if time.time() < self._wall_until:
            return None
        self._cursor = max(self._cursor, self._head)
        if self._cursor >= len(self._orders) or self._cursor >= self._head + self.ahead:
            return None
        order = self._orders[self._cursor]
        self._cursor += 1
        return order

    def _done_scrape(self, order, detail, failed):
        aid = order["amazon_id"]
        idx = self._index[aid]
        if detail:
            self._details[aid] = detail
            self.scraped += 1
            return
        self._tries[aid] = self._tries.get(aid, 0) + 1
        if failed and self._tries[aid] < self.SCRAPE_TRIES and idx >= self._head:
            self._cursor = min(self._cursor, idx)
        else:
            self._missed.add(aid)

    def _pick_write(self):
        return self._next_write(self.busy_id)

    def _done_write(self, _entry, ok, _failed):
        if ok:
            self.written += 1

    def _run(self, w, pick, work, done):
        try:
            while True:
                self._park_if_paused(w)
                with self._cv:
                    if self._stop:
                        break
                    if self._paused:
                        continue
                    job = pick()
                    if job is None:
                        self._cv.wait(0.5)
                        continue
                result, failed = None, False
                try:
                    if w.driver is None:
                        w.driver = self._attach()
                    result = work(w.driver, job)
                    w.fails = 0
                except LoginWall:
                    failed = True
                    with self._cv:
                        self._wall_until = time.time() + self.LOGIN_BACKOFF_S
                    print(f"  🔐 Seller Central wants a login — the Amazon windows wait "
                          f"{self.LOGIN_BACKOFF_S} s; the placer's next order page signs in")
                except Exception as e:
                    failed = True
                    if not self._paused and not self._stop:      # a recycle in progress is no fault
                        w.fails += 1
                        print(f"  ⚠️  {type(e).__name__}: {str(e)[:160]} "
                              f"(failure {w.fails}/{self.MAX_FAILS}) — opening a fresh window")
                    self._drop(w)
                with self._cv:
                    done(job, result, failed)
                    self._cv.notify_all()
                if w.fails >= self.MAX_FAILS:
                    w.dead = True
                    print(f"  🛑 {self.MAX_FAILS} failures in a row — this window stops; "
                          f"the placer does its work the rush way")
                    break
                if w.fails:
                    time.sleep(self.FAIL_BACKOFF_S)
        except Exception as e:
            w.dead = True
            print(f"  🛑 Worker error: {type(e).__name__}: {str(e)[:200]} — the placer does its work the rush way")
        finally:
            self._drop(w)
            with self._cv:
                self._cv.notify_all()

    def _park_if_paused(self, w):
        with self._cv:
            if not self._paused or self._stop:
                return
        self._drop(w)
        with self._cv:
            w.parked = True
            self._cv.notify_all()
            while self._paused and not self._stop:
                self._cv.wait(0.5)
            w.parked = False

    def _drop(self, w):
        drv, w.driver = w.driver, None
        if drv is not None:
            try:
                self._release(drv)
            except Exception:
                pass
