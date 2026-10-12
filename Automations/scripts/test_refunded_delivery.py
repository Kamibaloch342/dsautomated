#!/usr/bin/env python3
"""
test_refunded_delivery.py — refunded orders get their delivery from Zoro; flags show on the Daily report.

13 Oct 2026: WB4834992254 (Amazon-refunded, "Ask Zoro for Refund") was delivered on 16 Sep but the
dashboard said "in transit" — the Zoro sweep skipped every order labelled Refunded (391 in 60 days,
241 of them delivered). And a flag set in the drawer never showed on the Daily report rows.

Run:  python scripts/test_refunded_delivery.py   (no Chrome, no network; temporary DBs only)
"""
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)
sys.path.insert(0, os.path.join(AUTO, "dashboard"))

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   ({detail})" if detail and not ok else ""))


TMP = tempfile.mkdtemp()

# ── 1. the sweep picks refunded orders and never relabels them ───────────────────
import zoro_delivery_check as Z  # noqa: E402

Z.upsert_package_tracking = lambda *a, **k: None      # never the real DB (no parcels are passed anyway)
cols = [x.strip() for x in Z._COLS.split(",")]
dbp = os.path.join(TMP, "unified.db")
c = sqlite3.connect(dbp)
c.row_factory = sqlite3.Row
c.execute(f"CREATE TABLE closing_orders ({', '.join(cols)}, profit_percent, delivery_source, enriched_from_fedex, "
          "stalled_days, last_scan_time, last_scan_location, estimated_window, left_at, signed_by)")
recent = __import__("datetime").date.today().isoformat()
base = dict.fromkeys(cols, "")
base.update(account="Hathoro", order_status="Shipped", purchase_date=recent + " 15:28:00", tracking_ids="540940696936",
            shipped_email_time=recent + " 18:52:28", earnings=41.64, zoro_order_total=33.91,
            return_requested="Yes", refund_done="Yes", delivery_checked_at=None)
rows = [dict(base, amazon_order_id="114-0000000-0000001", wb_order_id="WB0000000001", order_label="Refunded"),
        dict(base, amazon_order_id="114-0000000-0000002", wb_order_id="WB0000000002", order_label="Cancelled"),
        dict(base, amazon_order_id="114-0000000-0000003", wb_order_id="WB0000000003", order_label="Incomplete",
             return_requested="", refund_done="")]
for r in rows:
    c.execute(f"INSERT INTO closing_orders ({', '.join(r)}) VALUES ({','.join('?' * len(r))})", list(r.values()))
c.commit()
Z._live_columns(c)
Z.ACCOUNT, Z.ONLY = "Hathoro", []
got = {r["wb_order_id"] for r in Z.load_candidates(c)}
check("a shipped Refunded order is now checked", "WB0000000001" in got, str(got))
check("Cancelled-labelled orders are still skipped", "WB0000000002" not in got, str(got))
check("normal undelivered orders are still checked", "WB0000000003" in got, str(got))

delivered = {"outcome": "ok", "order": {"order_status": "Complete", "totals": {}},
             "packages": [],
             "aggregate": {"delivery_status": "Delivered", "delivery_date": "Wednesday, 9/16/2026 10:42 am",
                           "last_scan_time": "2026-09-16 10:42", "last_scan_location": "Farmerville, LA",
                           "delivery_note": "", "stalled_days": None, "untracked": 0}}
row = dict(c.execute(f"SELECT {Z._COLS} FROM closing_orders WHERE wb_order_id='WB0000000001'").fetchone())
lab = Z.persist(c, row, delivered, "2026-10-13 03:00:00")
out = dict(c.execute("SELECT order_label, delivery_status, delivery_date FROM closing_orders "
                     "WHERE wb_order_id='WB0000000001'").fetchone())
check("delivery recorded on the refunded order", out["delivery_status"] == "Delivered"
      and out["delivery_date"].startswith("Wednesday"), str(out))
check("…and it stays Refunded", out["order_label"] == "Refunded" and lab == "", f"{out} {lab!r}")

import closing as C  # noqa: E402
_real = C.determine_label
C.determine_label = lambda o: "Completed"            # even if the label rule ever said otherwise
c.execute("UPDATE closing_orders SET delivery_status='' WHERE wb_order_id='WB0000000001'")
row = dict(c.execute(f"SELECT {Z._COLS} FROM closing_orders WHERE wb_order_id='WB0000000001'").fetchone())
Z.persist(c, row, delivered, "2026-10-13 03:00:00")
C.determine_label = _real
check("guard: the sweep never moves a Refunded label",
      c.execute("SELECT order_label FROM closing_orders WHERE wb_order_id='WB0000000001'").fetchone()[0] == "Refunded")
c.close()

# ── 2. Daily report rows carry flag / tags / urgent ──────────────────────────────
import month_report as M  # noqa: E402

print("\ndaily report")


def report_db(with_new_cols):
    p = os.path.join(TMP, f"rep{int(with_new_cols)}.db")
    d = sqlite3.connect(p)
    d.execute("CREATE TABLE closing_orders (amazon_order_id, account, purchase_date, order_status, order_label, "
              "wb_order_id, delivery_status, tracking_ids, shipped_email_time, flag_color)")
    d.executemany("INSERT INTO closing_orders VALUES (?,?,?,?,?,?,?,?,?,?)", [
        ("114-1", "Hathoro", "2026-09-13 15:28:00", "Shipped", "Refunded", "WB1", "Delivered", "5409", "x", "#067647"),
        ("114-2", "Hathoro", "2026-09-13 16:00:00", "Shipped", "Incomplete", "WB2", "", "5410", "x", "#d92d20"),
        ("114-3", "Hathoro", "2026-09-13 17:00:00", "Shipped", "Incomplete", "WB3", "", "5411", "x", "")])
    extra = ", tags TEXT, is_urgent TEXT" if with_new_cols else ""
    d.execute(f"CREATE TABLE order_crm (amazon_order_id TEXT PRIMARY KEY, flag_color TEXT, manual_delivered TEXT{extra})")
    if with_new_cols:
        d.executemany("INSERT INTO order_crm VALUES (?,?,?,?,?)", [
            ("114-1", "#067647", "", "Ask Zoro for Refund", ""), ("114-2", "", "", "", ""),
            ("114-3", "", "Yes", "", "Yes")])
    else:
        d.executemany("INSERT INTO order_crm VALUES (?,?,?)", [("114-1", "#067647", ""), ("114-3", "", "Yes")])
    d.commit()
    d.close()
    return p


M.DB = report_db(True)
M._CACHE.clear()
o = {x["id"]: x for x in M._collect(M._ro(), "2026-09-13", "2026-09-13", "")}
check("flag and tag from the drawer show on the row",
      o["114-1"]["flag"] == "#067647" and o["114-1"]["tags"] == "Ask Zoro for Refund", str(o["114-1"]))
check("refunded + delivered reads Delivered, not In transit", o["114-1"]["dstate"] == "delivered", o["114-1"]["dstate"])
check("urgent mark shows", o["114-3"]["urgent"] is True and not o["114-1"]["urgent"])
check("hand-marked delivery still counts as delivered", o["114-3"]["dstate"] == "delivered", o["114-3"]["dstate"])
check("closing's flag_color is the fallback when the CRM row has none", o["114-2"]["flag"] == "#d92d20", str(o["114-2"]))
M.DB = report_db(False)
o = {x["id"]: x for x in M._collect(M._ro(), "2026-09-13", "2026-09-13", "")}
check("an order_crm without tags / is_urgent columns still gives flags + manual delivery",
      o["114-1"]["flag"] == "#067647" and o["114-3"]["dstate"] == "delivered", str(o["114-1"]))

# ── 3. wiring ────────────────────────────────────────────────────────────────────
print("\nwiring")
src = open(os.path.join(AUTO, "dashboard", "server.py"), encoding="utf-8").read()
seg = src[src.index("def update_order("):][:3200]
check("a CRM save drops the Daily report's stored days", "_mr._CACHE.pop(k, None)" in seg
      and seg.index("_mr._CACHE") < seg.index("push_crm_event"))
v = open(os.path.join(AUTO, "dashboard", "v2.html"), encoding="utf-8").read()
check("drawer save patches the open Daily report", "Report.patch(id, fields);" in v)
check("day rows draw the flag dot and tags", "${U.flagdot(o.flag)}${esc(o.id)}" in v and "U.tags(o.tags)" in v)
check("Refresh rebuilds the open day on the server", "fresh}, this.who())" in v and "this._dayFresh = true" in v)
check("a shipped order with no answer yet is not called 'not shipped'", "shipped · not tracked yet" in v
      and "U.deliv(s.delivery_status, s.tracking_ids || s.shipped_email_time)" in v)
ca = open(os.path.join(AUTO, "scripts", "cloud_agent.py"), encoding="utf-8").read()
fs = ca[ca.index("_CRM_FIELDS = ("):][:300]
check("urgent marks made on Render are replayed onto the master", '"is_urgent"' in fs)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
