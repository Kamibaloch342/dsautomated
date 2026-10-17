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

Scope (26 Sep 2026): orders purchased in the last RECONCILE_DAYS (default 120) — older rows
practically never receive a new email — and the archives are loaded only for those orders'
ids instead of all 216k rows with their bodies. --all processes every row of the account.
Since 17 Oct 2026 the scope also takes every OLDER Shipped order that still has a hole in a
coverage column (the daily_bulk COVERAGE list): those were never looked at again once they
aged out, so the coverage report showed the same gaps run after run.

Besides the archives, empty fields are also gap-filled from (17 Oct 2026):
       order_placer_orders    → deliver_by_date, ship_by_date, shipped_email_time, tracking_ids,
                                zoro_order_total/tax, earnings/fees/shipping, zoro_deliver_by
                                (closing only takes these for orders it re-processes)
       merchant_order_id      → wb_order_id (first WB/SO leg)
       zoro_package_tracking  → tracking_ids + carrier (Zoro's own parcel list, from the
                                delivery check) when no shipping e-mail was ever found

Usage:
  ../.venv/bin/python scripts/reconcile_and_reenrich.py Hathoro          # preview
  ../.venv/bin/python scripts/reconcile_and_reenrich.py Hathoro --go
  ../.venv/bin/python scripts/reconcile_and_reenrich.py Hathoro --go --all
"""
import os, sys, sqlite3
_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _AUTO)

ACCT = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "Hathoro"
GO = "--go" in sys.argv
ALL = "--all" in sys.argv
DAYS = int(os.environ.get("RECONCILE_DAYS", "120") or 0)
os.environ.setdefault("CLOSING_ACCOUNT", ACCT)
DB = os.path.join(_AUTO, "data", "automations_unified.db")

import closing as C
from engine import closing_signals as CS

def empty(v): return v is None or str(v).strip() in ("", "None")


# zoro_order_total defaults to 0.0 in closing_orders — a 0 cost is "not captured", not free.
COST0 = {"zoro_order_total"}


def missing(k, v):
    if empty(v):
        return True
    if k in COST0:
        try:
            return float(v) == 0
        except (TypeError, ValueError):
            return False
    return False

con = sqlite3.connect(DB, timeout=120); con.execute("PRAGMA busy_timeout=120000")
con.row_factory = sqlite3.Row

# ---- the rows in scope, then the archive rows those need (id-scoped, latest wins) ----
# The coverage columns daily_bulk reports — an older Shipped row with one of these empty is
# still in scope. delivery_status is left out: only the delivery check can fill it.
GAP_COLS = ("earnings", "zoro_order_total", "shipped_email_time", "zoro_ship_speed",
            "placement_speed", "tracking_ids", "deliver_by_date", "profit_dollars", "wb_order_id")
_gap_sql = " OR ".join(f"{c} IS NULL OR TRIM(CAST({c} AS TEXT)) IN ('','None')" for c in GAP_COLS)
_where, _args = "account=?", [ACCT]
if not ALL and DAYS > 0:
    _where += (f" AND (substr(purchase_date,1,10) >= date('now', ?)"
               f"      OR (order_status='Shipped' AND ({_gap_sql})))")
    _args.append(f"-{DAYS} day")
rows = con.execute(f"SELECT * FROM closing_orders WHERE {_where}", _args).fetchall()
print(f"[{ACCT}] orders in scope: {len(rows)}" + ("" if ALL else f" (last {DAYS} days + older Shipped "
                                                                 f"orders with gaps; --all for every row)"))
_aids = sorted({r["amazon_order_id"] for r in rows if r["amazon_order_id"]})
_wbs = set()
for r in rows:
    _wbs.update(C._extract_wb_ids(r["merchant_order_id"]) or ([r["wb_order_id"]] if r["wb_order_id"] else []))
_wbs = sorted(_wbs)


def _chunks(seq, n=800):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _index(table, key, keys, order="email_timestamp"):
    out = {}
    for ch in _chunks(keys):
        ph = ",".join("?" * len(ch))
        for r in con.execute(f"SELECT * FROM {table} WHERE {key} IN ({ph}) ORDER BY {order}", ch):
            out[r[key]] = r                 # latest row wins
    return out


print(f"[{ACCT}] indexing archives for {len(_aids)} orders / {len(_wbs)} WB legs…")
earn = _index("archive_earnings", "amazon_order_id", _aids)
zoro = _index("archive_zoro", "wb_order_id", _wbs)             # latest leg row
trk = _index("archive_tracking", "wb_order_id", _wbs)
rets = {}
for ch in _chunks(_aids):
    ph = ",".join("?" * len(ch))
    for r in con.execute(f"SELECT * FROM archive_returns WHERE amazon_order_id IN ({ph}) "
                         f"ORDER BY email_timestamp DESC", ch):
        rets.setdefault(r["amazon_order_id"], []).append(r)
# tracking_id_uploaded: the tracker's own record first (action_log + placer table, written at
# upload time since 26 Sep 2026), then the older JSON upload log — no SC scrape needed.
upload = {}
try:
    from utils.unified_db import tracking_uploads_from_action_log
    for aid, u in tracking_uploads_from_action_log(_aids).items():
        upload[aid] = {k: v for k, v in u.items() if not k.startswith("_")}
except Exception as e:
    print(f"  action_log uploads unavailable: {e}")
try:
    for ch in _chunks(_aids):
        ph = ",".join("?" * len(ch))
        for r in con.execute(f"SELECT amazon_order_id, tracking_id_uploaded, tracking_upload_date, "
                             f"tracking_carrier_uploaded FROM order_placer_orders WHERE amazon_order_id IN ({ph}) "
                             f"AND tracking_id_uploaded IS NOT NULL AND TRIM(CAST(tracking_id_uploaded AS TEXT)) NOT IN ('','None')", ch):
            upload.setdefault(r["amazon_order_id"], {"tracking_id_uploaded": r["tracking_id_uploaded"],
                                                     "tracking_upload_date": r["tracking_upload_date"] or "",
                                                     "tracking_carrier_uploaded": r["tracking_carrier_uploaded"] or ""})
except sqlite3.OperationalError:
    pass                                    # placer table predates the upload columns
try:
    import json
    for e in json.load(open(os.path.join(_AUTO, "data", "tracking_upload_log.json"))):
        tids = [t for t in (e.get("tracking_ids") or []) if t]
        if tids and e.get("amazon_order_id") and e.get("amazon_order_id") not in upload:
            upload[e["amazon_order_id"]] = {"tracking_id_uploaded": ", ".join(tids),
                                            "tracking_upload_date": e.get("timestamp", "")}
except Exception:
    pass
# What the placer captured at placement (dates since 7 Sep 2026, cost, earnings, tracking).
PLACER_FILL = ("deliver_by_date", "ship_by_date", "shipped_email_time", "tracking_ids",
               "zoro_order_total", "zoro_tax", "earnings", "amazon_fees", "shipping_amt",
               "zoro_deliver_by")
placer = {}
try:
    _pcols = [c for c in PLACER_FILL
              if c in {r[1] for r in con.execute("PRAGMA table_info(order_placer_orders)")}]
    if _pcols:
        for ch in _chunks(_aids):
            ph = ",".join("?" * len(ch))
            for r in con.execute(f"SELECT amazon_order_id, {', '.join(_pcols)} FROM order_placer_orders "
                                 f"WHERE amazon_order_id IN ({ph})", ch):
                placer[r["amazon_order_id"]] = dict(r)
except sqlite3.OperationalError as e:
    print(f"  placer table unavailable: {e}")
# Zoro's own parcel list per WB leg (scripts/zoro_delivery_check.py) — the tracking numbers of
# orders whose Zoro shipping e-mail never reached any inbox.
parcels = {}
try:
    for ch in _chunks(_wbs):
        ph = ",".join("?" * len(ch))
        for r in con.execute(f"SELECT wb_order_id, tracking_number, carrier FROM zoro_package_tracking "
                             f"WHERE wb_order_id IN ({ph}) AND COALESCE(tracking_number,'') != ''", ch):
            parcels.setdefault(r["wb_order_id"], []).append(r)
except sqlite3.OperationalError:
    pass                                    # no delivery check has run on this DB yet
print(f"  earnings={len(earn)} zoro_legs={len(zoro)} tracking_legs={len(trk)} return_orders={len(rets)} "
      f"uploads={len(upload)} placer={len(placer)} parcel_legs={len(parcels)}")

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

w = sqlite3.connect(DB, timeout=120); w.execute("PRAGMA busy_timeout=120000")
stats = {"earn": 0, "zoro": 0, "trk": 0, "ret": 0, "placer": 0, "wb": 0, "parcel": 0, "label": 0, "rows": 0}
DATE_COLS = ("ship_by_date", "deliver_by_date")    # normalized by derive_fields before writing

for row in rows:
    o = dict(row)
    cur = dict(row)                       # snapshot of original values
    legs = C._extract_wb_ids(o.get("merchant_order_id"))
    if not legs and o.get("wb_order_id"):
        legs = [o["wb_order_id"]]
    gap = {}                              # data fields to gap-fill
    placer_hit = parcel_hit = False

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
            if missing(dest, cur.get(dest)):
                tot = sum(float(z[src]) for z in leg_rows if not empty(z[src]))
                if tot:
                    gap[dest] = round(tot, 2)
        latest = leg_rows[-1]
        for dest, src in ZORO_BASE.items():
            if empty(cur.get(dest)) and not empty(latest[src]):
                gap[dest] = latest[src]
        # promo / item description: derived columns from the archive sync (26 Sep 2026),
        # body parse only for rows synced before that while the body is still present
        lz = dict(latest)
        body = lz.get("raw_body_snippet")
        if empty(cur.get("coupon_savings")):
            cs, clabel = lz.get("zoro_promo_savings"), lz.get("zoro_promo_label")
            if empty(cs) and body:
                cs, clabel = CS.extract_zoro_promo(body)
            if cs:
                gap["coupon_savings"] = cs
                if empty(cur.get("coupon_used")) and clabel:
                    gap["coupon_used"] = clabel
            elif not empty(latest["coupon_savings"]):
                gap["coupon_savings"] = latest["coupon_savings"]
        if empty(cur.get("zoro_item_desc")):
            zd = lz.get("zoro_item_desc") or (CS.extract_zoro_item(body) if body else "")
            if zd:
                gap["zoro_item_desc"] = zd

    # --- archive_tracking (latest leg) ---
    tleg = [trk[l] for l in legs if l in trk]
    if tleg:
        t = sorted(tleg, key=lambda x: x["email_timestamp"] or "")[-1]
        for dest, src in TRACK_MAP.items():
            if empty(cur.get(dest)) and not empty(t[src]):
                gap[dest] = t[src]
        if empty(cur.get("zoro_deliver_by")):
            lt = dict(t)
            eta = lt.get("zoro_eta") or (CS.extract_zoro_eta(lt.get("raw_body_snippet"), lt.get("email_timestamp"))
                                         if lt.get("raw_body_snippet") else "")
            if eta:
                gap["zoro_deliver_by"] = eta

    # --- placer row: whatever is still empty after the archives ---
    pl = placer.get(o["amazon_order_id"])
    if pl:
        for k in PLACER_FILL:
            if k in pl and missing(k, cur.get(k)) and k not in gap and not missing(k, pl[k]):
                gap[k] = pl[k]
                placer_hit = True

    # --- wb_order_id from the merchant order id (WW / manual rows have no leg → stay empty) ---
    if empty(cur.get("wb_order_id")) and legs:
        gap["wb_order_id"] = legs[0]

    # --- tracking numbers from Zoro's parcel list when no e-mail or placer row had them ---
    if empty(cur.get("tracking_ids")) and "tracking_ids" not in gap:
        tids, pcar = [], ""
        for l in legs:
            for p in parcels.get(l, []):
                t = str(p["tracking_number"]).strip()
                if t and t not in tids:
                    tids.append(t)
                pcar = pcar or (p["carrier"] or "")
        if tids:
            gap["tracking_ids"] = ", ".join(tids)
            parcel_hit = True
            if empty(cur.get("carrier")) and "carrier" not in gap and pcar:
                gap["carrier"] = pcar

    # --- tracking_id_uploaded from the tracker's record (no SC scrape) ---
    ul = upload.get(o["amazon_order_id"])
    if ul:
        for dest in ("tracking_id_uploaded", "tracking_upload_date", "tracking_carrier_uploaded"):
            if empty(cur.get(dest)) and not empty(ul.get(dest)):
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
    for k in DATE_COLS:                   # store gap-filled dates in closing's one format
        if k in gap and not empty(o.get(k)):
            gap[k] = o[k]
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
    if placer_hit: stats["placer"] += 1
    if "wb_order_id" in gap: stats["wb"] += 1
    if parcel_hit: stats["parcel"] += 1
    stats["rows"] += 1
    if GO:
        w.execute(f"UPDATE closing_orders SET {','.join(sets)} WHERE amazon_order_id=?", vals + [o["amazon_order_id"]])
if GO:
    w.commit()
print(f"\n[{ACCT}] orders changed: {stats['rows']}  "
      f"(earnings gap +{stats['earn']}, zoro +{stats['zoro']}, tracking +{stats['trk']}, "
      f"returns +{stats['ret']}, placer +{stats['placer']}, wb id +{stats['wb']}, "
      f"parcel tracking +{stats['parcel']}, label changes {stats['label']})  [{'APPLIED' if GO else 'dry-run'}]")
if not GO: print("  re-run with --go to apply")
con.close(); w.close()
