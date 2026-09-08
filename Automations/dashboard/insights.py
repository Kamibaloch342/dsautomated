#!/usr/bin/env python3
"""Business insights engine for the CRM dashboard.

Computes the alert categories day-by-day from closing_orders + the verified-SKU
dataset, including per-ASIN day-over-day transition detection (item price,
earnings/unit, shipping/unit, Zoro code/unit cost, handling business-days) with
all values normalized PER UNIT. Each alert carries the order's key fields so
the UI can render an actionable row with a Resolve button. Resolved alerts
(logged in insight_resolutions) are filtered out.

Read-only against automations_unified.db (safe to run while closing writes, WAL).
"""
import os, csv, re, sqlite3, json
from datetime import datetime, date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # Development_Unit
AUTO = os.path.join(ROOT, "Automations")
DB = os.environ.get("CLOUD_DB_PATH") if (os.environ.get("CLOUD_MODE") == "1" and os.environ.get("CLOUD_DB_PATH")) else os.path.join(AUTO, "data", "automations_unified.db")
CATALOG = os.path.join(ROOT, "TSV Files", "combined_reports.db")
VERIFIED_CSV = os.path.join(ROOT, "DS_Closing", "Hypersale_Closing", "verified_skus_cache.csv")

DELIVERED = ("Delivered", "AllDelivered")


def _digits(z):
    return re.sub(r"\D", "", str(z or ""))


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_verified():
    """ASIN -> {zoro_code, qty_to_place, zoro_multiple, second}."""
    out = {}
    if not os.path.exists(VERIFIED_CSV):
        return out
    with open(VERIFIED_CSV, newline="", encoding="utf-8", errors="ignore") as f:
        for row in csv.DictReader(f):
            asin = (row.get("ASIN") or "").strip()
            if not asin:
                continue
            out[asin] = {
                "zoro_code": (row.get("Zoro Code") or "").strip(),
                "qty_to_place": _f(row.get("Qty to Place")) or 1,
                "zoro_multiple": _f(row.get("zoro_multiple")) or 1,
                "second": (row.get("2nd_part") or "").strip(),
            }
    return out


def load_catalog_prices(codes):
    """zoro_code -> current catalog sales_price, for the given codes only (fast)."""
    out = {}
    if not os.path.exists(CATALOG) or not codes:
        return out
    try:
        c = sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True, timeout=10)
        c.execute("CREATE INDEX IF NOT EXISTS ix_zoro_no ON zoro_data(zoro_no)")  # no-op if RO fails
    except Exception:
        try:
            c = sqlite3.connect(CATALOG, timeout=10)
            c.execute("CREATE INDEX IF NOT EXISTS ix_zoro_no ON zoro_data(zoro_no)")
        except Exception:
            return out
    codes = list(codes)
    for i in range(0, len(codes), 400):
        chunk = codes[i:i + 400]
        q = "SELECT zoro_no, sales_price FROM zoro_data WHERE zoro_no IN (%s)" % ",".join("?" * len(chunk))
        try:
            for zn, sp in c.execute(q, chunk):
                if zn not in out and sp is not None:
                    out[zn] = _f(sp)
        except Exception:
            break
    c.close()
    return out


def _resolved_keys(con):
    s = {}
    try:
        for t, k in con.execute("SELECT insight_type, ref_key FROM insight_resolutions"):
            s.setdefault(t, set()).add(k)
    except sqlite3.OperationalError:
        pass
    return s


def _date_clause(account, date_from, date_to):
    where, args = ["1=1"], []
    if account and account not in ("All", "all", ""):
        where.append("account = ?"); args.append(account)
    if date_from:
        where.append("substr(purchase_date,1,10) >= ?"); args.append(date_from)
    if date_to:
        where.append("substr(purchase_date,1,10) <= ?"); args.append(date_to)
    return " AND ".join(where), args


def compute(account=None, date_from=None, date_to=None, db_path=None):
    # Normal connection + query_only (NOT mode=ro): a WAL database can fail to open
    # read-only when the -wal/-shm are active ("unable to open database file").
    con = sqlite3.connect(db_path or DB, timeout=10)
    con.execute("PRAGMA busy_timeout=10000")
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    # Default window: last 60 days when the caller gives no explicit range, so the
    # full-table scan (and the per-ASIN transition detection) stays fast.
    if not date_from and not date_to:
        date_from = (date.today() - timedelta(days=60)).isoformat()
    where, args = _date_clause(account, date_from, date_to)
    rows = [dict(r) for r in con.execute(f"SELECT * FROM closing_orders WHERE {where}", args)]
    resolved = _resolved_keys(con)
    verified = load_verified()
    # NOTE: the Zoro catalog (load_catalog_prices) is no longer consulted — the old
    # paid-vs-catalog check was removed (coupons made it pure noise), so skipping the
    # catalog DB open saves time on every /api/insights call.

    today = date.today().isoformat()
    # placer coverage window = date span of the Pending (placer-sourced) rows.
    # WB-match only makes sense inside it; pre-script (e.g. April) orders are not "unmatched".
    _pend = sorted((r.get("purchase_date") or "")[:10] for r in rows
                   if r.get("order_label") == "Pending" and r.get("purchase_date"))
    placer_lo, placer_hi = (_pend[0], _pend[-1]) if _pend else (None, None)
    # NOTE: zoro_price_change (paid-unit vs catalog) was REMOVED — we use coupons, so the
    # Zoro paid total legitimately differs from catalog; flagging it was always noise.
    out = {k: [] for k in [
        "item_price_change", "zoro_price_change", "wrong_qty", "zoro_code_change",
        "low_margin", "shipping_change", "zip_mismatch", "undelivered", "refunded", "wb_unmatched",
        "zoro_deliver_mismatch", "low_zoro_stock", "shipped_late", "zoro_slow_risk",
        "earnings_unit_change", "shipping_amt_change", "handling_time_change",
        "zoro_ship_rule", "zoro_unit_cost_change"]}

    def _d(s):
        try:
            return date.fromisoformat(str(s)[:10])
        except Exception:
            return None

    oid_date = {r["amazon_order_id"]: (r.get("purchase_date") or "")[:10] for r in rows}

    def add(kind, oid, msg, sev, extra=None):
        if oid in resolved.get(kind, set()):
            return
        a = {"ref_key": oid, "order_id": oid, "message": msg, "severity": sev,
             "date": oid_date.get(oid, "")}
        if extra:
            a.update(extra)
        out[kind].append(a)

    # per-ASIN groups for the day-over-day transition detection further below
    by_asin = {}
    for r in rows:
        by_asin.setdefault(r.get("asin"), []).append(r)

    placed_wbs = set()
    try:
        placed_wbs = {w[0] for w in con.execute("SELECT wb_order_id FROM order_placer_orders WHERE wb_order_id!=''")}
    except sqlite3.OperationalError:
        pass
    # ── Zoro-slower-than-Amazon-promise = live A-to-z risk ────────────────────
    # zoro_deliver_by is captured at PLACEMENT into order_placer_orders (Pending
    # orders are NOT in closing_orders), so closing's zoro_deliver_mismatch can't
    # see fresh orders. Read the placer directly: an UNSHIPPED order whose Zoro ETA
    # is LATER than Amazon's promised deliver-by will arrive late → buyer can file
    # A-to-z. Still in-flight = actionable (expedite / cancel-reship / raise handling).
    try:
        pw, pa = ["zoro_deliver_by!='' AND deliver_by_date!='' AND (shipped_email_time IS NULL OR shipped_email_time='')"], []
        if account and account.lower() != "all":
            pw.append("account=?"); pa.append(account)
        if date_from:
            pw.append("substr(placed_at,1,10)>=?"); pa.append(date_from)
        if date_to:
            pw.append("substr(placed_at,1,10)<=?"); pa.append(date_to)
        prisk = con.execute(
            "SELECT amazon_order_id,asin,deliver_by_date,zoro_deliver_by,substr(placed_at,1,10) pd "
            "FROM order_placer_orders WHERE " + " AND ".join(pw), pa).fetchall()
        for r in prisk:
            oid = r["amazon_order_id"]
            if oid in resolved.get("zoro_slow_risk", set()):
                continue
            zdb, adb = _d(r["zoro_deliver_by"]), _d(r["deliver_by_date"])
            if not (zdb and adb) or zdb <= adb:
                continue
            out["zoro_slow_risk"].append({
                "ref_key": oid, "order_id": oid, "asin": r["asin"],
                "date": r["pd"] or "", "severity": "high",
                "message": f"Zoro ETA {zdb} is AFTER Amazon promise {adb} ({(zdb - adb).days:+d}d) — late-delivery / A-to-z risk",
                "zoro_deliver_by": str(zdb), "deliver_by_date": str(adb)})
    except sqlite3.OperationalError:
        pass

    # WB-match only makes sense when the closing window overlaps the placer window.
    # If almost nothing matches, the two periods don't overlap yet → suppress (not real).
    wbs_here = [(_.get("wb_order_id") or "").strip() for _ in rows]
    wbs_here = [w for w in wbs_here if w]
    matched = sum(1 for w in wbs_here if w in placed_wbs)
    do_wb = bool(placed_wbs) and wbs_here and (matched / len(wbs_here)) >= 0.30

    for r in rows:
        oid = r["amazon_order_id"]; asin = r.get("asin"); zc = (r.get("zoro_sku") or "").strip()
        pct = _f(r.get("profit_percent")); paid = _f(r.get("zoro_order_total"))
        # Zoro delivery estimate vs Amazon deliver-by — flag a gap of >1 day (LATE = high)
        zdb, adb = _d(r.get("zoro_deliver_by")), _d(r.get("deliver_by_date"))
        if zdb and adb and abs((zdb - adb).days) > 1:
            late = zdb > adb
            add("zoro_deliver_mismatch", oid,
                f"Zoro delivers {zdb} vs Amazon due {adb} ({(zdb - adb).days:+d}d{' — LATE' if late else ''})",
                "high" if late else "med", {"asin": asin, "zoro_deliver_by": str(zdb), "deliver_by_date": str(adb)})
        # Low Zoro stock (Zoro only reports a count when it's low) — alert under 20
        zs = _f(r.get("zoro_stock"))
        if zs is not None and zs < 20:
            add("low_zoro_stock", oid, f"Zoro stock low: {int(zs)} left", "high" if zs < 5 else "med",
                {"asin": asin, "zoro_stock": int(zs)})
        # Shipped LATE vs Amazon ship-by (PKT→Pacific corrected) — hurts late-shipment metric
        if r.get("shipped_on_time") == "Late":
            mh = _f(r.get("ship_margin_hrs"))
            by = f" by {abs(mh)/24:.1f}d" if mh is not None else ""
            add("shipped_late", oid, f"Shipped LATE vs ship-by{by}", "high",
                {"asin": asin, "ship_margin_hrs": mh})
        # #5 low margin (red/yellow only surfaced as alerts; green is fine)
        if pct is not None and pct < 12:
            sev = "high" if pct < 8 else "med"
            add("low_margin", oid, f"Margin {pct:.1f}% ({'<8%' if pct < 8 else '8-12%'})", sev,
                {"asin": asin, "sku": r.get("sku"), "profit_percent": pct})
        # #7 zip mismatch — compare 5-digit (a different +4 isn't a wrong address)
        az, zz = _digits(r.get("ship_postal_code"))[:5], _digits(r.get("zoro_buyer_zip"))[:5]
        if len(az) == 5 and len(zz) == 5 and az != zz:
            add("zip_mismatch", oid, f"Ship zip {r.get('ship_postal_code')} != Zoro {r.get('zoro_buyer_zip')}", "high",
                {"asin": asin, "ship_city": r.get("ship_city")})
        # #8 undelivered past deliver-by — only for orders closing actually CHECKED
        # (a real delivery_status). Skips Pending/just-placed orders that closing
        # hasn't run FedEx on yet, so they don't false-positive.
        ds = r.get("delivery_status")
        if ds and ds not in DELIVERED and r.get("manual_delivered") != "Yes":
            db_ = (r.get("deliver_by_date") or "")[:10]
            if db_ and db_ < today:
                add("undelivered", oid, f"Not delivered (status {ds}), due {db_}", "high",
                    {"asin": asin, "delivery_status": ds, "deliver_by_date": db_})
        # #9 refunded
        if r.get("refund_done") == "Yes":
            deliv = r.get("delivery_status") in DELIVERED
            bucket = "customer_return" if deliv else "amazon_refund_claim_zoro"
            add("refunded", oid, f"Refunded ${_f(r.get('refund_amount')) or 0:.2f} — {'returned by customer' if deliv else 'NOT delivered → claim from Zoro'}", "high",
                {"asin": asin, "refund_amount": _f(r.get("refund_amount")), "bucket": bucket,
                 "case_status": r.get("case_status"), "delivery_status": r.get("delivery_status")})
        # #10 WB unmatched (order has no placed WB record) — order not script-placed/verify
        wb = (r.get("wb_order_id") or "").strip()
        # Only meaningful for RECENT, report-sourced (non-Pending) orders within the
        # placer's coverage. April pre-script orders & Pending placer rows aren't "unmatched".
        pd10 = (r.get("purchase_date") or "")[:10]
        if (do_wb and wb and wb not in placed_wbs and r.get("order_status") != "Cancelled"
                and r.get("order_label") != "Pending"
                and placer_lo and placer_lo <= pd10 <= placer_hi):
            add("wb_unmatched", oid, f"WB {wb} not in placed orders — verify/cancel", "med", {"asin": asin, "wb": wb})
        # #3 wrong qty/multiplier (vs verified dataset). zoro_code_change moved to the
        # per-ASIN transition detector below (old→new code day-over-day, not vs CSV).
        v = verified.get(asin)
        if v:
            exp_qty = (_f(r.get("quantity")) or 1) * v["qty_to_place"] * v["zoro_multiple"]
            zq = _f(r.get("zoro_qty"))
            # only flag when we actually KNOW the placed Zoro qty (>0); a 0/None
            # means not-yet-closed (Pending pre-fill) — don't false-positive.
            if zq and zq > 0 and exp_qty and abs(zq - exp_qty) >= 1:
                add("wrong_qty", oid, f"Placed Zoro qty {zq:g}, expected {exp_qty:g} (×{v['qty_to_place']:g}×{v['zoro_multiple']:g})", "high",
                    {"asin": asin, "zoro_qty": zq, "expected_qty": exp_qty})
        # Zoro shipping rule: free when the PRE-coupon subtotal ≥ $50, else the standard
        # small-order fee ($5.99 today; $5.00 before Jun-2026 — both accepted so the fee
        # rename doesn't flag 14k historical orders). The threshold is verified against
        # the data to apply to subtotal+coupon_savings (coupon rows still ship free).
        # A deviation usually means a manual carrier change (USPS etc.) — surface it.
        zsub, zship = _f(r.get("zoro_subtotal")), _f(r.get("zoro_shipping"))
        if zsub is not None and zsub > 0 and zship is not None:
            pre = zsub + (_f(r.get("coupon_savings")) or 0.0)
            exp = 0.0 if pre >= 50 else 5.99
            ok = abs(zship - exp) <= 0.01 or (exp == 5.99 and abs(zship - 5.00) <= 0.01)
            if not ok:
                add("zoro_ship_rule", oid,
                    f"Zoro shipping ${zship:.2f} ≠ expected ${exp:.2f} "
                    f"(pre-coupon subtotal ${pre:.2f} → {'free ≥$50' if pre >= 50 else '$5.99 <$50'})",
                    "high" if zship > exp + 0.01 else "med",
                    {"asin": asin, "zoro_shipping": zship, "expected_shipping": exp,
                     "zoro_subtotal": zsub, "pre_coupon_subtotal": round(pre, 2)})

    # ── Per-ASIN day-over-day TRANSITION detection ────────────────────────────
    # Every metric is computed PER UNIT so a different ORDER QUANTITY never
    # false-positives (qty 2 @ $97.00 total is the same $48.50/unit listing).
    # Per ASIN: each day's PREVAILING value = median (money/days) or mode (codes)
    # of that day's per-order values — orders from two accounts interleaving within
    # one day can't flip-flop. Consecutive days at the "same" value (within epsilon:
    # $0.01 money, <1 business day handling, exact for codes) form a RUN; each run
    # boundary = ONE insight (old → new) stamped with the first date at the new
    # value, the count of orders at it and a sample order id. A value the ASIN was
    # ALREADY seen at (A→B→A ping-pong between two Zoro accounts/codes) is reported
    # only the first time — one insight per (asin, metric, new_value).
    # ref_key = "{asin}:{kind}:{date}" (unique per day) so Resolve persists across
    # recomputes. Clicking the row opens the ASIN timeline.
    def _unit(g, field):
        v = _f(g.get(field)); qn = _f(g.get("quantity"))
        return (v / qn) if (v is not None and qn) else None

    def _pos_unit(g, field):
        # like _unit but treats 0/negative as "missing" (an item_price/earnings of 0
        # is unfilled data, not a real price) — shipping_amt keeps 0 as a real value.
        v = _unit(g, field)
        return v if (v is not None and v > 0) else None

    def _zpre(g):
        # exact Zoro item price BEFORE coupon, tax, shipping — per unit
        sub = _f(g.get("zoro_subtotal")); cs = _f(g.get("coupon_savings")) or 0.0
        zq = _f(g.get("zoro_qty")) or _f(g.get("quantity")) or 1
        return ((sub + cs) / zq) if (sub is not None and sub > 0 and zq) else None

    def _zunit(g):
        # Zoro PAID unit cost = subtotal/zoro_qty (fallback order_total when the
        # subtotal wasn't captured) — the actual rise/drop in what we pay Zoro.
        zq = _f(g.get("zoro_qty"))
        if not zq or zq <= 0:
            return None
        sub = _f(g.get("zoro_subtotal"))
        if sub is None or sub <= 0:
            sub = _f(g.get("zoro_order_total"))
        return (sub / zq) if (sub is not None and sub > 0) else None

    def _zcode(g):
        z = (g.get("zoro_sku") or "").strip()
        return z or None

    def _handling(g):
        # BUSINESS days purchase→ship-by (skip Sat/Sun): Friday purchase with a
        # Monday ship-by is 1 business day, so weekend spans never false-positive.
        p, s = _d(g.get("purchase_date")), _d(g.get("ship_by_date"))
        if not (p and s) or s < p or (s - p).days > 40:
            return None
        n, cur = 0, p
        while cur < s:
            cur += timedelta(days=1)
            if cur.weekday() < 5:
                n += 1
        return n

    def _money_sev(o, n):
        return "high" if (o <= 0.005 or abs(n - o) / o >= 0.10) else "med"

    def _vkey(v):
        return v if isinstance(v, str) else round(v, 2)

    def _emit_transitions(asin, grp, value_fn, kind, same, msg_fn, sev_fn):
        byday = {}                       # day -> that day's per-order values
        for g in grp:
            v = value_fn(g)
            d = (g.get("purchase_date") or "")[:10]
            if v is None or not d:
                continue
            e = byday.setdefault(d, {"vals": [], "oid": g["amazon_order_id"]})
            e["vals"].append(v)
        runs = []                        # [{val, date, oid, count}] consecutive days, same value
        for d in sorted(byday):
            vs = byday[d]["vals"]
            if isinstance(vs[0], str):
                val = max(set(vs), key=vs.count)              # mode for codes
            else:
                sv = sorted(vs); val = sv[len(sv) // 2]       # median for numbers
            if runs and same(runs[-1]["val"], val):
                runs[-1]["count"] += len(vs)
            else:
                runs.append({"val": val, "date": d, "oid": byday[d]["oid"], "count": len(vs)})
        seen = {_vkey(runs[0]["val"])} if runs else set()     # baseline value = already known
        for i in range(1, len(runs)):
            o, n = runs[i - 1]["val"], runs[i]["val"]
            if _vkey(n) in seen:                              # ping-pong back to a known value
                continue
            seen.add(_vkey(n))
            d, rk = runs[i]["date"], f"{asin}:{kind}:{runs[i]['date']}"
            if rk in resolved.get(kind, set()):
                continue
            out[kind].append({"ref_key": rk, "order_id": runs[i]["oid"], "asin": asin,
                              "date": d, "link": "asin", "severity": sev_fn(o, n),
                              "message": msg_fn(o, n, runs[i]["count"]),
                              "old": o if isinstance(o, str) else round(o, 2),
                              "new": n if isinstance(n, str) else round(n, 2),
                              "count": runs[i]["count"]})

    _same_money = lambda a, b: abs(a - b) <= 0.01
    _same_days = lambda a, b: abs(a - b) < 1          # a change needs ≥1 business day
    _same_code = lambda a, b: a == b

    def _money_msg(asin, label):
        def f(o, n, c):
            return (f"ASIN {asin}: {label} ${o:.2f} → ${n:.2f}/unit "
                    f"({'↑' if n > o else '↓'}, {c} order{'s' if c > 1 else ''} at new)")
        return f

    for asin, grp in by_asin.items():
        if not asin or len(grp) < 2:
            continue
        # walk in purchase order; skip Cancelled rows (their money fields are often
        # partial/zero and a cancellation is not a listing change).
        grp = sorted((g for g in grp if g.get("order_status") != "Cancelled"),
                     key=lambda g: (g.get("purchase_date") or "", g.get("amazon_order_id") or ""))
        _emit_transitions(asin, grp, lambda g: _pos_unit(g, "item_price"), "item_price_change",
                          _same_money, _money_msg(asin, "item price"), _money_sev)
        _emit_transitions(asin, grp, lambda g: _pos_unit(g, "earnings"), "earnings_unit_change",
                          _same_money, _money_msg(asin, "earnings"), _money_sev)
        _emit_transitions(asin, grp, lambda g: _unit(g, "shipping_amt"), "shipping_amt_change",
                          _same_money, _money_msg(asin, "shipping"), _money_sev)
        _emit_transitions(asin, grp, lambda g: _unit(g, "shipping_price"), "shipping_change",
                          _same_money, _money_msg(asin, "buyer shipping price"), _money_sev)
        _emit_transitions(asin, grp, _zpre, "zoro_price_change",
                          _same_money, _money_msg(asin, "Zoro base price (pre-coupon)"), _money_sev)
        _emit_transitions(asin, grp, _zunit, "zoro_unit_cost_change",
                          _same_money, _money_msg(asin, "Zoro unit cost"), _money_sev)
        _emit_transitions(asin, grp, _zcode, "zoro_code_change", _same_code,
                          lambda o, n, c, a=asin: f"ASIN {a}: zoro code {o} → {n} ({c} order{'s' if c > 1 else ''} at new)",
                          lambda o, n: "med")
        _emit_transitions(asin, grp, _handling, "handling_time_change", _same_days,
                          lambda o, n, c, a=asin: f"ASIN {a}: handling {o}d → {n}d business days ({c} order{'s' if c > 1 else ''} at new)",
                          lambda o, n: "high" if n > o else "med")

    con.close()
    counts = {k: len(v) for k, v in out.items()}
    return {"insights": out, "counts": counts, "total": sum(counts.values())}


if __name__ == "__main__":
    import sys
    acct = sys.argv[1] if len(sys.argv) > 1 else None
    res = compute(account=acct)
    print("counts:", json.dumps(res["counts"], indent=2))
    print("total alerts:", res["total"])
    for k, v in res["insights"].items():
        if v:
            print(f"\n{k} ({len(v)}):")
            for a in v[:3]:
                print("   ", a["order_id"], "|", a["message"])
