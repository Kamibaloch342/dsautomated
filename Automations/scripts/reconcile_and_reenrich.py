"""
reconcile_and_reenrich.py — THE daily catch-up pass. Fixes the systemic loophole where
terminal/closed orders never get re-enriched with late-arriving archive data, and where
derived fields go stale after a base-field backfill.

For EVERY order (incl. Completed/Refunded/Cancelled/non-WB), it:
  1. GAP-FILLS data fields from the archives (never overwrites a present value):
       archive_earnings  → earnings, amazon_fees, shipping_amt, sold_email_time, ship_by_date
       archive_zoro      → zoro cost (summed across split legs) + base fields (latest leg)
                           + coupon_savings + zoro_item_desc
       archive_tracking  → shipped_email_time, tracking_ids, carrier
       archive_returns   → return_requested / refund_done+amount+date+reason / return_reason
  2. RECOMPUTES every derived field by calling closing.py's OWN functions (can't drift):
       derive_fields (pkt timestamps + placement/ship speeds), calculate_profit,
       compute_timeliness, compute_ship_timeliness, compute_net_profit, determine_label
  3. order_label recomputed ONLY for placed (WB/SO) orders with no manual_review flag;
     WW/NotPlaced/Manual labels are preserved.

Idempotent, DB-only (no Gmail/network) → safe to run daily after sync+placer+closing+fedex.

Usage:
  ../.venv/bin/python scripts/reconcile_and_reenrich.py Hathoro          # preview
  ../.venv/bin/python scripts/reconcile_and_reenrich.py Hathoro --go
"""
import os, sys, sqlite3
_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _AUTO)

ACCT = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "Hathoro"
GO = "--go" in sys.argv
os.environ.setdefault("CLOSING_ACCOUNT", ACCT)
DB = os.path.join(_AUTO, "data", "automations_unified.db")

import closing as C
from engine import closing_signals as CS

def empty(v): return v is None or str(v).strip() in ("", "None")

con = sqlite3.connect(DB, timeout=120); con.execute("PRAGMA busy_timeout=120000")
con.row_factory = sqlite3.Row

# ---- build in-memory archive indexes (one pass each; latest row wins) ----
print(f"[{ACCT}] indexing archives…")
earn = {}
for r in con.execute("SELECT * FROM archive_earnings ORDER BY email_timestamp"):
    earn[r["amazon_order_id"]] = r
zoro = {}
for r in con.execute("SELECT * FROM archive_zoro ORDER BY email_timestamp"):
    zoro[r["wb_order_id"]] = r           # latest leg row
trk = {}
for r in con.execute("SELECT * FROM archive_tracking WHERE wb_order_id IS NOT NULL ORDER BY email_timestamp"):
    trk[r["wb_order_id"]] = r
rets = {}
for r in con.execute("SELECT * FROM archive_returns ORDER BY email_timestamp DESC"):
    rets.setdefault(r["amazon_order_id"], []).append(r)
# tracking-uploader log → tracking_id_uploaded (no SC scrape needed)
upload = {}
try:
    import json
    for e in json.load(open(os.path.join(_AUTO, "data", "tracking_upload_log.json"))):
        tids = [t for t in (e.get("tracking_ids") or []) if t]
        if tids and e.get("amazon_order_id"):
            upload[e["amazon_order_id"]] = {"tracking_id_uploaded": ", ".join(tids),
                                            "tracking_upload_date": e.get("timestamp", "")}
except Exception:
    pass
print(f"  earnings={len(earn)} zoro_legs={len(zoro)} tracking_legs={len(trk)} return_orders={len(rets)} upload_log={len(upload)}")

EARN_MAP = {"earnings": "total_your_earnings", "amazon_fees": "total_amazon_fees",
            "shipping_amt": "total_shipping", "sold_email_time": "email_timestamp",
            "ship_by_date": "ship_by_date"}
ZORO_BASE = {"zoro_account_name": "zoro_account_name", "zoro_gmail_account": "gmail_account",
             "zoro_po_number": "po_number", "zoro_sku": "sku", "zoro_qty": "qty",
             "zoro_buyer_zip": "ship_zip", "zoro_email_ts": "email_timestamp",
             "zoro_order_date": "order_date", "zoro_payment_card": "payment_card_last4",
             "zoro_payment_method": "payment_method"}
ZORO_COST = {"zoro_order_total": "order_total", "zoro_subtotal": "subtotal",
             "zoro_tax": "tax", "zoro_shipping": "shipping_cost"}
TRACK_MAP = {"shipped_email_time": "email_timestamp", "tracking_ids": "tracking_ids", "carrier": "carrier"}
DELIVERED = ("Delivered", "AllDelivered", "PartialDelivered", "OutForDelivery")
# columns we OVERWRITE (recomputed/derived) vs GAP-FILL (data)
RECOMPUTE = ["order_received_pkt", "zoro_placed_pkt", "zoro_shipped_pkt", "placement_speed",
             "placement_speed_hrs", "zoro_ship_speed", "zoro_ship_speed_hrs", "profit_dollars",
             "profit_percent", "on_time", "time_to_ship_hrs", "shipped_on_time", "ship_margin_hrs",
             "net_profit_after_refund"]
FLOAT_COLS = {"profit_dollars", "profit_percent", "net_profit_after_refund", "time_to_ship_hrs",
              "ship_margin_hrs", "placement_speed_hrs", "zoro_ship_speed_hrs"}

rows = con.execute("SELECT * FROM closing_orders WHERE account=?", (ACCT,)).fetchall()
print(f"  orders: {len(rows)}")
w = sqlite3.connect(DB, timeout=120); w.execute("PRAGMA busy_timeout=120000")
stats = {"earn": 0, "zoro": 0, "trk": 0, "ret": 0, "label": 0, "rows": 0}

for row in rows:
    o = dict(row)
    cur = dict(row)                       # snapshot of original values
    legs = C._extract_wb_ids(o.get("merchant_order_id"))
    if not legs and o.get("wb_order_id"):
        legs = [o["wb_order_id"]]
    gap = {}                              # data fields to gap-fill

    # --- archive_earnings (by amazon id) ---
    e = earn.get(o["amazon_order_id"])
    if e:
        for dest, src in EARN_MAP.items():
            if empty(cur.get(dest)) and not empty(e[src]):
                gap[dest] = e[src]

    # --- archive_zoro: cost summed across legs, base from latest leg ---
    leg_rows = [zoro[l] for l in legs if l in zoro]
    if leg_rows:
        for dest, src in ZORO_COST.items():
            if empty(cur.get(dest)):
                tot = sum(float(z[src]) for z in leg_rows if not empty(z[src]))
                if tot:
                    gap[dest] = round(tot, 2)
        latest = leg_rows[-1]
        for dest, src in ZORO_BASE.items():
            if empty(cur.get(dest)) and not empty(latest[src]):
                gap[dest] = latest[src]
        body = latest["raw_body_snippet"]
        if empty(cur.get("coupon_savings")):
            cs, clabel = CS.extract_zoro_promo(body) if body else (None, None)
            if cs:
                gap["coupon_savings"] = cs
                if empty(cur.get("coupon_used")) and clabel:
                    gap["coupon_used"] = clabel
            elif not empty(latest["coupon_savings"]):
                gap["coupon_savings"] = latest["coupon_savings"]
        if empty(cur.get("zoro_item_desc")) and body:
            zd = CS.extract_zoro_item(body)
            if zd:
                gap["zoro_item_desc"] = zd

    # --- archive_tracking (latest leg) ---
    tleg = [trk[l] for l in legs if l in trk]
    if tleg:
        t = sorted(tleg, key=lambda x: x["email_timestamp"] or "")[-1]
        for dest, src in TRACK_MAP.items():
            if empty(cur.get(dest)) and not empty(t[src]):
                gap[dest] = t[src]

    # --- tracking_id_uploaded from the uploader log (no SC scrape) ---
    ul = upload.get(o["amazon_order_id"])
    if ul:
        for dest in ("tracking_id_uploaded", "tracking_upload_date"):
            if empty(cur.get(dest)) and not empty(ul[dest]):
                gap[dest] = ul[dest]

    # --- archive_returns (authoritative flags) ---
    ars = rets.get(o["amazon_order_id"])
    if ars:
        if cur.get("return_requested") != "Yes":
            gap["return_requested"] = "Yes"
        rf = next((r for r in ars if r["email_type"] == "refund_initiated" or r["refund_done"] == "Yes"), None)
        if rf and cur.get("refund_done") != "Yes":
            gap["refund_done"] = "Yes"
            if empty(cur.get("refund_amount")) and not empty(rf["refund_amount"]): gap["refund_amount"] = rf["refund_amount"]
            if empty(cur.get("refund_date")) and not empty(rf["refund_date"]): gap["refund_date"] = rf["refund_date"]
            if empty(cur.get("refund_reason")) and (rf["refund_reason"] or rf["return_reason"]): gap["refund_reason"] = rf["refund_reason"] or rf["return_reason"]
        ra = next((r for r in ars if r["email_type"] == "return_auth"), ars[0])
        if empty(cur.get("return_reason")) and not empty(ra["return_reason"]): gap["return_reason"] = ra["return_reason"]
        if empty(cur.get("return_customer_comment")) and not empty(ra["customer_comment"]): gap["return_customer_comment"] = ra["customer_comment"]

    o.update(gap)

    # --- recompute derived via closing's OWN functions ---
    C.derive_fields(o)
    o["profit_dollars"], o["profit_percent"] = C.calculate_profit(o)
    o.update(C.compute_timeliness(o))
    o.update(C.compute_ship_timeliness(o))
    o["net_profit_after_refund"] = C.compute_net_profit(o)
    new_label = None
    if legs and empty(cur.get("manual_review")):          # only placed, non-manual orders
        new_label = C.determine_label(o)

    # --- assemble write set: gap-fill data + overwrite recomputed (only on real change) ---
    sets, vals = [], []
    for k, v in gap.items():
        sets.append(f"{k}=?"); vals.append(v)
    for k in RECOMPUTE:
        nv = o.get(k)
        if nv is None:
            continue
        ov = cur.get(k)
        if k in FLOAT_COLS and not empty(ov):
            try:
                if abs(float(nv) - float(ov)) < 0.005:
                    continue          # numerically identical → don't churn float formatting
            except Exception:
                pass
        if str(nv) == str(ov if ov is not None else ""):
            continue
        sets.append(f"{k}=?"); vals.append(nv)
    if new_label and new_label != cur.get("order_label"):
        sets.append("order_label=?"); vals.append(new_label); stats["label"] += 1
    if not sets:
        continue
    if any(k in gap for k in EARN_MAP): stats["earn"] += 1
    if any(k in gap for k in {**ZORO_BASE, **ZORO_COST}): stats["zoro"] += 1
    if any(k in gap for k in TRACK_MAP): stats["trk"] += 1
    if "return_requested" in gap or "refund_done" in gap: stats["ret"] += 1
    stats["rows"] += 1
    if GO:
        w.execute(f"UPDATE closing_orders SET {','.join(sets)} WHERE amazon_order_id=?", vals + [o["amazon_order_id"]])
if GO:
    w.commit()
print(f"\n[{ACCT}] orders changed: {stats['rows']}  "
      f"(earnings gap +{stats['earn']}, zoro +{stats['zoro']}, tracking +{stats['trk']}, "
      f"returns +{stats['ret']}, label changes {stats['label']})  [{'APPLIED' if GO else 'dry-run'}]")
if not GO: print("  re-run with --go to apply")
con.close(); w.close()
