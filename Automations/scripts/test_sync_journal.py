#!/usr/bin/env python3
"""
test_sync_journal.py — two machines, two SQLite files, a fake R2 (in memory). No network.

Proves: placements made on A appear on B; closing columns written on B come back to A
without clobbering A's newer placement columns on the same row; nothing echoes back;
append-only rows arrive exactly once; a batch is never applied twice; an INSERT of a row
both machines already have only fills gaps.

Run:  python scripts/test_sync_journal.py
"""
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from utils import sync_journal as sj  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail and not cond else ""))


class FakeStore:
    def __init__(self): self.blobs = {}
    def put_bytes(self, key, data, content_type=""): self.blobs[key] = data
    def get_bytes(self, key): return self.blobs.get(key)
    def list(self, prefix, max_keys=1000): return [{"key": k} for k in sorted(self.blobs) if k.startswith(prefix)]
    def delete(self, key): self.blobs.pop(key, None)
    def get_file(self, key, path, gunzip=False):
        import gzip
        b = self.blobs.get(key)
        if b is None: return False
        open(path, "wb").write(gzip.decompress(b) if gunzip else b); return True


SCHEMA = """
CREATE TABLE orders(id INTEGER PRIMARY KEY AUTOINCREMENT, amazon_order_id TEXT, merchant_order_id TEXT,
  order_status TEXT, tracking_number TEXT, carrier TEXT, updated_at TEXT);
CREATE TABLE order_placer_orders(id INTEGER PRIMARY KEY AUTOINCREMENT, amazon_order_id TEXT, wb_order_id TEXT,
  profit_dollars REAL, placed_at TEXT);
CREATE TABLE enrichment_log(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, amazon_order_id TEXT, field TEXT,
  new_value TEXT, timestamp TEXT);
CREATE TABLE placer_claims(amazon_order_id TEXT PRIMARY KEY, status TEXT, wb_order_id TEXT, profile TEXT);
"""


def mk(name):
    p = os.path.join(tempfile.mkdtemp(), name)
    c = sqlite3.connect(p); c.executescript(SCHEMA); c.commit(); c.close()
    return p


def q(p, sql, *a):
    c = sqlite3.connect(p)
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


def x(p, sql, *a):
    c = sqlite3.connect(p)
    try:
        c.execute(sql, a); c.commit()
    finally:
        c.close()


def main():
    A, B = mk("a.db"), mk("b.db")
    store = FakeStore()
    ia, ib = sj.install(A), sj.install(B)
    check("install: triggers on every synced table that exists, the rest skipped",
          set(ia["tables"]) == {"orders", "order_placer_orders", "enrichment_log", "placer_claims"} and "closing_orders" in ia["skipped"], str(ia))

    # A places an order
    x(A, "INSERT INTO orders(amazon_order_id, merchant_order_id, order_status, updated_at) VALUES ('111-1','WB1','SUCCESS','t1')")
    x(A, "INSERT INTO order_placer_orders(amazon_order_id, wb_order_id, profit_dollars, placed_at) VALUES ('111-1','WB1',5.2,'t1')")
    x(A, "INSERT INTO placer_claims VALUES ('111-1','PLACING',NULL,'HS_COUPON')")
    x(A, "UPDATE placer_claims SET status='CONFIRMED', wb_order_id='WB1' WHERE amazon_order_id='111-1'")
    check("journal: A recorded 3 inserts + 1 update", sj.pending_count(A) == 4, str(sj.pending_count(A)))
    r = sj.push(A, store, "mac-a")
    check("push: one batch uploaded and the journal emptied", r["files"] == 1 and r["events"] == 4 and sj.pending_count(A) == 0, str(r))

    r = sj.pull(B, store, "mac-b")
    check("pull: B applied the batch", r["files"] == 1 and r["events"] == 4 and r["errors"] == 0, str(r))
    check("B has the order, the placer record and the CONFIRMED claim (keyed, not by id)",
          q(B, "SELECT merchant_order_id, order_status FROM orders WHERE amazon_order_id='111-1'") == [("WB1", "SUCCESS")]
          and q(B, "SELECT profit_dollars FROM order_placer_orders WHERE wb_order_id='WB1'") == [(5.2,)]
          and q(B, "SELECT status, wb_order_id FROM placer_claims") == [("CONFIRMED", "WB1")])
    check("echo safety: applying on B produced no journal rows on B", sj.pending_count(B) == 0, str(sj.pending_count(B)))
    r2 = sj.pull(B, store, "mac-b")
    check("idempotent: pulling again applies nothing", r2["files"] == 0 and q(B, "SELECT COUNT(*) FROM orders")[0][0] == 1)
    check("own batches are never pulled back", sj.pull(A, store, "mac-a")["files"] == 0)

    # concurrent edits: B closes (tracking cols), A changes a placement column of the SAME row
    x(B, "UPDATE orders SET tracking_number='1Z999', carrier='UPS', updated_at='t2' WHERE amazon_order_id='111-1'")
    x(B, "INSERT INTO enrichment_log(run_id, amazon_order_id, field, new_value, timestamp) VALUES ('r1','111-1','tracking','1Z999','t2')")
    x(A, "UPDATE orders SET order_status='SHIPPED_CONFIRMED', updated_at='t3' WHERE amazon_order_id='111-1'")
    sj.push(B, store, "mac-b")
    r = sj.pull(A, store, "mac-a")
    row = q(A, "SELECT order_status, tracking_number, carrier FROM orders WHERE amazon_order_id='111-1'")
    check("column-level merge: A gets B's tracking columns and KEEPS its own newer status",
          row == [("SHIPPED_CONFIRMED", "1Z999", "UPS")], str(row))
    check("append-only: the enrichment row arrived once", q(A, "SELECT COUNT(*) FROM enrichment_log")[0][0] == 1)
    sj.pull(A, store, "mac-a")
    check("append-only: re-pull does not duplicate it", q(A, "SELECT COUNT(*) FROM enrichment_log")[0][0] == 1)

    # A's status change flows to B; B's row keeps its tracking
    sj.push(A, store, "mac-a"); sj.pull(B, store, "mac-b")
    row = q(B, "SELECT order_status, tracking_number FROM orders WHERE amazon_order_id='111-1'")
    check("both directions converge on the same row", row == [("SHIPPED_CONFIRMED", "1Z999")], str(row))
    check("no ping-pong: after convergence both journals are empty", sj.pending_count(A) == 0 and sj.pending_count(B) == 0)

    # same order inserted on both sides with different gaps → merged, not duplicated
    x(A, "INSERT INTO orders(amazon_order_id, merchant_order_id, updated_at) VALUES ('222-2','WB2','t4')")
    x(B, "INSERT INTO orders(amazon_order_id, carrier, updated_at) VALUES ('222-2','FedEx','t4')")
    sj.push(A, store, "mac-a"); sj.push(B, store, "mac-b")
    sj.pull(A, store, "mac-a"); sj.pull(B, store, "mac-b")
    ra = q(A, "SELECT COUNT(*), MAX(merchant_order_id), MAX(carrier) FROM orders WHERE amazon_order_id='222-2'")
    rb = q(B, "SELECT COUNT(*), MAX(merchant_order_id), MAX(carrier) FROM orders WHERE amazon_order_id='222-2'")
    check("double insert: one row per machine, gaps filled from the other side",
          ra == [(1, "WB2", "FedEx")] and rb == [(1, "WB2", "FedEx")], str((ra, rb)))

    # a column the other machine does not have is skipped, not fatal
    x(A, "ALTER TABLE orders ADD COLUMN new_col TEXT")
    sj.install(A)
    x(A, "UPDATE orders SET new_col='v', order_status='X' WHERE amazon_order_id='222-2'")
    sj.push(A, store, "mac-a"); r = sj.pull(B, store, "mac-b")
    check("schema drift: unknown column skipped, the rest applied",
          r["errors"] == 0 and q(B, "SELECT order_status FROM orders WHERE amazon_order_id='222-2'") == [("X",)]
          and "new_col" in r["skipped_cols"].get("orders", set()), str(r))

    # housekeeping
    s = sj.summary(A)
    check("summary reports installed / pending / applied", s["installed"] and s["pending"] == 0 and s["applied_files"] >= 2, str(s))
    check("prune keeps fresh batches", sj.prune_remote(store, days=1) == 0 and len(store.blobs) >= 5)
    # seed a brand-new machine from the slim DB
    import gzip
    store.blobs["db/dashboard.db.gz"] = gzip.compress(open(A, "rb").read())
    C = os.path.join(tempfile.mkdtemp(), "c.db")
    sj.seed_from_cloud(C, store)
    check("seed: a new machine starts from the cloud copy", q(C, "SELECT COUNT(*) FROM orders")[0][0] == 2)
    try:
        sj.seed_from_cloud(C, store); check("seed: refuses to overwrite an existing DB", False)
    except RuntimeError:
        check("seed: refuses to overwrite an existing DB", True)

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL)); sys.exit(1)


if __name__ == "__main__":
    main()
