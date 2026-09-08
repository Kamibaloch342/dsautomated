#!/usr/bin/env python3
"""Owner's-cockpit analytics — the prescriptive backend.

Six modules, each returns ready-to-render data with the ACTION surfaced:
  today()    — cross-cutting command center (top actions + $ at stake)
  sourcing() — list-next pipeline, restock/OOS radar, price-spike, expansion map
  portfolio()— scale board, kill list, fragile-margin watch
  leakage()  — recoverable-vs-sunk waterfall, Zoro claim queue, double-loss
  health()   — per-account suspension-risk scoreboard
  ops()      — stuck-order control tower, SLA, throughput pulse

Read-only against automations_unified.db (+ the Zoro catalog combined_reports.db).
"""
import os, re, sqlite3, json
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AUTO = os.path.join(ROOT, "Automations")
DB = os.environ.get("CLOUD_DB_PATH") if (os.environ.get("CLOUD_MODE") == "1" and os.environ.get("CLOUD_DB_PATH")) else os.path.join(AUTO, "data", "automations_unified.db")
CATALOG = os.path.join(ROOT, "TSV Files", "combined_reports.db")
DELIVERED = ("Delivered", "AllDelivered")
MARGIN_FLOOR = 12.0
TODAY = date.today().isoformat()


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ro(path=DB):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _acct_where(account, zoro_account=None, date_from=None, date_to=None):
    w, a = "", []
    if account and account.lower() != "all":
        w += " AND account=?"; a.append(account)
    if zoro_account and zoro_account.lower() != "all":
        w += " AND zoro_account_name=?"; a.append(zoro_account)
    if date_from:
        w += " AND substr(purchase_date,1,10) >= ?"; a.append(date_from)
    if date_to:
        w += " AND substr(purchase_date,1,10) <= ?"; a.append(date_to)
    return w, a


def _rows(account, zoro_account=None, date_from=None, date_to=None):
    w, a = _acct_where(account, zoro_account, date_from, date_to)
    c = _ro()
    rows = [dict(r) for r in c.execute(f"SELECT * FROM closing_orders WHERE 1=1{w}", a)]
    c.close()
    return rows


def _catalog(zoro_nos):
    """zoro_no -> dict(sales_price, availabilityStatus, brand, l1, l2, min_order_qty)."""
    out = {}
    if not os.path.exists(CATALOG) or not zoro_nos:
        return out
    try:
        c = sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True, timeout=10)
    except Exception:
        return out
    nos = list(zoro_nos)
    for i in range(0, len(nos), 400):
        ch = nos[i:i + 400]
        q = ("SELECT zoro_no, sales_price, availabilityStatus, zoro_brand_name, "
             "level_one_name, level_two_name, min_order_qty, is_dropship "
             "FROM zoro_data WHERE zoro_no IN (%s)" % ",".join("?" * len(ch)))
        try:
            for r in c.execute(q, ch):
                if r[0] not in out:
                    out[r[0]] = {"price": _f(r[1]), "status": r[2], "brand": r[3],
                                 "l1": r[4], "l2": r[5], "moq": r[6], "dropship": r[7]}
        except Exception:
            break
    c.close()
    return out


# ── PORTFOLIO ────────────────────────────────────────────────────────────────
def portfolio(account=None, zoro_account=None, date_from=None, date_to=None):
    rows = _rows(account, zoro_account, date_from, date_to)
    cutoff14 = (date.today() - timedelta(days=14)).isoformat()
    cutoff28 = (date.today() - timedelta(days=28)).isoformat()
    agg = {}
    for r in rows:
        a = r.get("asin") or "?"
        g = agg.setdefault(a, {"asin": a, "sku": r.get("sku"), "title": r.get("title"),
                               "zoro_sku": r.get("zoro_sku"), "orders": 0, "units": 0, "profit": 0,
                               "earnings": 0, "margins": [], "loss": 0, "u_recent": 0, "u_prev": 0})
        g["orders"] += 1; g["units"] += _f(r.get("quantity")) or 0
        g["profit"] += _f(r.get("profit_dollars")) or 0; g["earnings"] += _f(r.get("earnings")) or 0
        if r.get("profit_percent") is not None:
            g["margins"].append(_f(r.get("profit_percent")))
        if r.get("order_label") == "Loss":
            g["loss"] += 1
        d = (r.get("purchase_date") or "")[:10]
        if d >= cutoff14:
            g["u_recent"] += _f(r.get("quantity")) or 0
        elif cutoff28 <= d < cutoff14:
            g["u_prev"] += _f(r.get("quantity")) or 0
    items = []
    for g in agg.values():
        m = g["margins"]
        avg_m = round(sum(m) / len(m), 1) if m else None
        sd = round((sum((x - sum(m) / len(m)) ** 2 for x in m) / len(m)) ** 0.5, 1) if len(m) > 1 else 0
        vel_recent = g["u_recent"] / 14.0; vel_prev = g["u_prev"] / 14.0
        items.append({**{k: g[k] for k in ("asin", "sku", "title", "zoro_sku", "orders")},
                      "units": int(g["units"]), "profit": round(g["profit"], 2),
                      "margin": avg_m, "margin_sd": sd, "loss_rate": round(g["loss"] / g["orders"] * 100, 0) if g["orders"] else 0,
                      "vel_recent": round(vel_recent, 2), "vel_prev": round(vel_prev, 2),
                      "trend": round(vel_recent - vel_prev, 2)})
    total_profit = sum(i["profit"] for i in items) or 1
    winners = sorted(items, key=lambda x: -x["profit"])
    scale = [i for i in winners if (i["margin"] or 0) >= 20 and i["profit"] > 200][:30]
    risers = sorted([i for i in items if i["trend"] > 1 and i["profit"] > 0], key=lambda x: -x["trend"])[:20]
    kill = [i for i in items if (i["profit"] < 0 and i["orders"] >= 3) or (i["loss_rate"] >= 50 and i["orders"] >= 10)]
    kill = sorted(kill, key=lambda x: x["profit"])[:40]
    dead = sorted([i for i in items if i["vel_recent"] == 0 and i["vel_prev"] > 0.3], key=lambda x: -x["vel_prev"])[:20]
    fragile = sorted([i for i in items if (i["margin"] is not None and i["margin"] < 8 and i["units"] >= 50)],
                     key=lambda x: -x["units"])[:20]
    # concentration
    cum = 0; top50 = sorted(items, key=lambda x: -x["profit"])[:50]
    top50_profit = round(sum(i["profit"] for i in top50), 2)
    return {"total_profit": round(total_profit, 2),
            "concentration": {"top10": round(sum(i["profit"] for i in winners[:10]) / total_profit * 100, 1),
                              "top50": round(top50_profit / total_profit * 100, 1)},
            "scale": scale, "risers": risers, "kill": kill, "dead": dead, "fragile": fragile,
            "net_losers": len([i for i in items if i["profit"] < 0]), "asin_count": len(items)}


# ── LEAKAGE / ZORO CLAIMS ────────────────────────────────────────────────────
def leakage(account=None, zoro_account=None, date_from=None, date_to=None):
    rows = _rows(account, zoro_account, date_from, date_to)
    refunded = [r for r in rows if r.get("refund_done") == "Yes"]
    lost = []        # not delivered + refunded -> claim from Zoro (lost package)
    double = []      # delivered + refunded -> return-for-credit needed
    sunk = []        # delivered, sold below cost / buyer remorse
    for r in refunded:
        amt = _f(r.get("refund_amount")) or 0
        deliv = r.get("delivery_status") in DELIVERED
        rec = {"order_id": r["amazon_order_id"], "account": r.get("account"), "asin": r.get("asin"),
               "wb": r.get("wb_order_id"), "refund": round(amt, 2), "zoro_cost": _f(r.get("zoro_order_total")),
               "reason": r.get("return_reason") or r.get("refund_reason"),
               "case": r.get("zoro_case_number"), "ra": r.get("zoro_ra_number"),
               "recovered": _f(r.get("zoro_refund_amount")) or 0, "case_status": r.get("case_status"),
               "delivery_status": r.get("delivery_status")}
        if not deliv:
            lost.append(rec)
        else:
            double.append(rec)
    # also lost-package candidates = not delivered, exception, past due (even if not yet refunded)
    stuck_claims = [{"order_id": r["amazon_order_id"], "account": r.get("account"), "asin": r.get("asin"),
                     "wb": r.get("wb_order_id"), "zoro_cost": _f(r.get("zoro_order_total")),
                     "delivery_status": r.get("delivery_status")}
                    for r in rows if r.get("delivery_status") in ("Shipment exception", "Delivery exception")
                    and r.get("refund_done") != "Yes"]
    recoverable = round(sum(x["refund"] for x in lost) + sum((x["zoro_cost"] or 0) for x in double), 2)
    recovered = round(sum(x["recovered"] for x in refunded if _f(x.get("zoro_refund_amount"))) if False else
                      sum((_f(r.get("zoro_refund_amount")) or 0) for r in rows), 2)
    open_claims = [x for x in (lost + double) if x["case_status"] != "Resolved" and not x["ra"]]
    return {"refund_total": round(sum(_f(r.get("refund_amount")) or 0 for r in refunded), 2),
            "refund_count": len(refunded),
            "recoverable": recoverable, "recovered": recovered,
            "lost_package": {"count": len(lost), "amount": round(sum(x["refund"] for x in lost), 2), "rows": lost[:200]},
            "double_loss": {"count": len(double), "amount": round(sum((x["zoro_cost"] or 0) for x in double), 2), "rows": double[:200]},
            "stuck": {"count": len(stuck_claims), "rows": stuck_claims[:100]},
            "claim_queue": sorted(open_claims, key=lambda x: -(x["refund"] or x["zoro_cost"] or 0))[:200]}


# ── HEALTH / SUSPENSION RISK ─────────────────────────────────────────────────
def health(account=None, zoro_account=None, date_from=None, date_to=None):
    c = _ro()
    accts = [account] if (account and account.lower() != "all") else ["HOANK", "Hathoro"]
    zw, za = ("", [])
    if zoro_account and zoro_account.lower() != "all":
        zw, za = " AND zoro_account_name=?", [zoro_account]
    out = []
    for acct in accts:
        rows = [dict(r) for r in c.execute(f"SELECT * FROM closing_orders WHERE account=?{zw}", [acct] + za)]
        n = len(rows) or 1
        cancelled = sum(1 for r in rows if r.get("order_status") == "Cancelled")
        late = sum(1 for r in rows if r.get("on_time") == "Late")
        ontime_n = sum(1 for r in rows if r.get("on_time") in ("Late", "On Time")) or 1
        refunds = sum(1 for r in rows if r.get("refund_done") == "Yes")
        returns = sum(1 for r in rows if r.get("return_requested") == "Yes")
        exc = sum(1 for r in rows if r.get("delivery_status") in ("Shipment exception", "Delivery exception"))
        def band(v, amber, red):
            return "red" if v >= red else "amber" if v >= amber else "green"
        cancel_rate = round(cancelled / n * 100, 2)
        late_rate = round(late / ontime_n * 100, 1)
        refund_rate = round(refunds / n * 100, 2)
        return_rate = round(returns / n * 100, 2)
        metrics = [
            {"name": "Cancellation rate", "value": cancel_rate, "unit": "%", "threshold": 2.5, "band": band(cancel_rate, 1.5, 2.5)},
            {"name": "Late delivery rate", "value": late_rate, "unit": "%", "threshold": 4.0, "band": band(late_rate, 4, 8)},
            {"name": "Refund rate", "value": refund_rate, "unit": "%", "threshold": 5.0, "band": band(refund_rate, 3, 5)},
            {"name": "Return rate", "value": return_rate, "unit": "%", "threshold": 5.0, "band": band(return_rate, 4, 6)},
            {"name": "Delivery exceptions", "value": round(exc / n * 100, 2), "unit": "%", "threshold": 2.0, "band": band(exc / n * 100, 1, 2)},
        ]
        worst = max((m["band"] for m in metrics), key=lambda b: {"green": 0, "amber": 1, "red": 2}[b])
        # IP complaints from archive_messages (best-effort)
        try:
            ip = c.execute("SELECT COUNT(*) FROM archive_messages WHERE LOWER(COALESCE(subject,'')||' '||COALESCE(message_body,'')) LIKE '%intellectual property%' OR LOWER(COALESCE(subject,'')) LIKE '%authoriz%'").fetchone()[0]
        except Exception:
            ip = 0
        out.append({"account": acct, "orders": n, "verdict": worst, "metrics": metrics, "ip_flags": ip})
    c.close()
    return {"accounts": out}


# ── DATA HEALTH / DISCREPANCY MONITOR ────────────────────────────────────────
def data_health(account=None, zoro_account=None, date_from=None, date_to=None):
    """Data-integrity checks (NOT business KPIs): enrichment coverage, stale-derived
    fields, returns propagation, label sanity, FedEx/delivery, anomalies. Each check is
    a count of orders in a BAD state — green≈0. Mirrors the manual audits we ran."""
    c = _ro()
    accts = [account] if (account and account.lower() != "all") else ["HOANK", "Hathoro"]
    dw, dp = "", []
    if date_from:
        dw += " AND purchase_date>=?"; dp.append(date_from)
    if date_to:
        dw += " AND purchase_date<?"; dp.append(date_to)
    out = []
    for acct in accts:
        base = f"FROM closing_orders WHERE account=?{dw}"
        params = [acct] + dp
        def cnt(extra, p=()):
            return c.execute(f"SELECT COUNT(*) {base} AND {extra}", params + list(p)).fetchone()[0]
        total = c.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0] or 1
        shipped = cnt("order_status='Shipped'") or 1
        S = "order_status='Shipped'"
        checks = []
        def chk(name, group, miss, tot, zero=False, hint=""):
            tot = tot or 1
            pct = round(100 * miss / tot, 1)
            band = ("green" if miss == 0 else "amber" if pct < 1 else "red") if zero \
                else ("green" if pct < 2 else "amber" if pct < 8 else "red")
            checks.append({"name": name, "group": group, "count": miss, "total": tot,
                           "pct": pct, "band": band, "hint": hint})
        # Enrichment coverage (Shipped) — should be ~0 after reconcile
        chk("Earnings missing", "Enrichment (Shipped)", cnt(f"{S} AND (earnings IS NULL OR earnings='')"), shipped, hint="run reconcile_and_reenrich")
        chk("Zoro cost missing", "Enrichment (Shipped)", cnt(f"{S} AND (zoro_order_total IS NULL OR zoro_order_total=0)"), shipped, hint="fetch_zoro_pdf_receipts / reconcile")
        chk("Shipped-email time missing", "Enrichment (Shipped)", cnt(f"{S} AND (shipped_email_time IS NULL OR shipped_email_time='')"), shipped, hint="backfill_shipped_time")
        chk("Zoro payment missing", "Enrichment (Shipped)", cnt(f"{S} AND (zoro_payment_method IS NULL OR zoro_payment_method='')"), shipped, hint="CC Sales Receipt PDF")
        # Stale-derived (base present, derived empty) — should be 0; >0 ⇒ run reconcile
        chk("placed_pkt stale", "Stale derived", cnt("zoro_email_ts IS NOT NULL AND zoro_email_ts!='' AND (zoro_placed_pkt IS NULL OR zoro_placed_pkt='')"), total, zero=True, hint="recompute_zoro_timing")
        chk("placement_speed stale", "Stale derived", cnt("sold_email_time IS NOT NULL AND sold_email_time!='' AND zoro_email_ts IS NOT NULL AND zoro_email_ts!='' AND (placement_speed IS NULL OR placement_speed='')"), total, zero=True, hint="recompute_zoro_timing")
        chk("ship_speed stale", "Stale derived", cnt("zoro_email_ts IS NOT NULL AND zoro_email_ts!='' AND shipped_email_time IS NOT NULL AND shipped_email_time!='' AND (zoro_ship_speed IS NULL OR zoro_ship_speed='')"), total, zero=True, hint="recompute_zoro_timing")
        # Returns integrity
        propgap = c.execute(f"SELECT COUNT(*) FROM closing_orders co WHERE co.account=?{dw} AND (co.return_requested IS NULL OR co.return_requested!='Yes') AND EXISTS(SELECT 1 FROM archive_returns ar WHERE ar.amazon_order_id=co.amazon_order_id)", params).fetchone()[0]
        chk("Return not propagated to order", "Returns integrity", propgap, total, zero=True, hint="propagate_returns_to_closing")
        chk("Refunded but label not Refunded/Cancelled", "Returns integrity", cnt("refund_done='Yes' AND order_label NOT IN ('Refunded','Cancelled')"), total, zero=True, hint="reconcile relabels")
        # FedEx / delivery
        chk("Tracking present but not FedEx-checked", "FedEx / delivery", cnt(f"{S} AND tracking_ids IS NOT NULL AND tracking_ids!='' AND (delivery_status IS NULL OR delivery_status='')"), shipped, hint="run daily FedEx")
        chk("FedEx 404 (Unknown)", "FedEx / delivery", cnt("delivery_status='Unknown'"), total, hint="bad/unindexed tracking")
        dlv = cnt("delivery_status IN ('Delivered','AllDelivered')") or 1
        dbm = cnt("delivery_status IN ('Delivered','AllDelivered') AND (deliver_by_date IS NULL OR deliver_by_date='')")
        # known historical gap (deliver-by is report/SC-only, not in any archive) → never red
        checks.append({"name": "Delivered but no deliver-by date", "group": "FedEx / delivery",
                       "count": dbm, "total": dlv, "pct": round(100 * dbm / dlv, 1),
                       "band": "green" if dbm == 0 else "amber", "hint": "SC scrape only — needs scrape_sc_fields"})
        # Anomalies
        chk("Shipped WB/SO with no Zoro cost", "Anomalies", cnt(f"{S} AND (merchant_order_id LIKE 'WB%' OR merchant_order_id LIKE 'SO%') AND (zoro_order_total IS NULL OR zoro_order_total=0)"), shipped, zero=True, hint="no Zoro email anywhere")
        verdict = "red" if any(x["band"] == "red" for x in checks) else "amber" if any(x["band"] == "amber" for x in checks) else "green"
        out.append({"account": acct, "total": total, "shipped": shipped, "verdict": verdict, "checks": checks})
    c.close()
    return {"accounts": out, "note": "Live Gmail-vs-DB returns reconcile is a CLI check (fetch_account_returns.py + sync_gmail_archive.py)."}


# ── REFUND ISSUES (tickets to resolve) ───────────────────────────────────────
def refund_issues(account=None, zoro_account=None, date_from=None, date_to=None):
    """Flag refunds that are money-leak risks, as resolvable tickets:
      • no_return_request — refunded but NO return-authorization email (refund w/o a return)
      • instant_auto      — refunded ≤2 days after the return request (Amazon auto-refund,
                            likely BEFORE the item came back)
      • delayed           — refunded >15 days after request (process lag)
    Timing uses archive_returns email_timestamp (return_auth = request, refund_initiated =
    refund) because closing_orders.refund_date is not captured by the parser.
    case_status comes from order_crm so resolved tickets can be filtered out."""
    c = _ro()
    accts = [account] if (account and account.lower() != "all") else ["HOANK", "Hathoro"]
    dw, dp = "", []
    if date_from:
        dw += " AND co.purchase_date>=?"; dp.append(date_from)
    if date_to:
        dw += " AND co.purchase_date<?"; dp.append(date_to)
    issues = []
    for acct in accts:
        q = f"""
        WITH req AS (SELECT amazon_order_id, MIN(email_timestamp) rts FROM archive_returns WHERE email_type='return_auth' GROUP BY 1),
             ref AS (SELECT amazon_order_id, MIN(email_timestamp) fts, MAX(COALESCE(refund_reason,return_reason)) frr FROM archive_returns WHERE email_type='refund_initiated' GROUP BY 1)
        SELECT co.amazon_order_id, co.account, co.asin, co.sku, co.title,
               co.refund_amount, co.profit_dollars, co.net_profit_after_refund, co.return_reason,
               req.rts, ref.fts, ref.frr,
               ROUND(JULIANDAY(ref.fts)-JULIANDAY(req.rts),1) AS days,
               crm.case_status, crm.flag_color
        FROM closing_orders co
        LEFT JOIN req ON req.amazon_order_id=co.amazon_order_id
        LEFT JOIN ref ON ref.amazon_order_id=co.amazon_order_id
        LEFT JOIN order_crm crm ON crm.amazon_order_id=co.amazon_order_id
        WHERE co.account=? AND co.refund_done='Yes'{dw}
        """
        for r in c.execute(q, [acct] + dp):
            d = dict(r)
            days = d["days"]
            if d["rts"] is None:
                cat = "no_return_request"
            elif days is not None and days <= 2:
                cat = "instant_auto"
            elif days is not None and days > 15:
                cat = "delayed"
            else:
                continue                       # normal refund — not an issue
            issues.append({
                "order_id": d["amazon_order_id"], "account": d["account"], "asin": d["asin"],
                "sku": d["sku"], "title": d["title"], "refund_amount": _f(d["refund_amount"]),
                "profit": _f(d["profit_dollars"]), "net_profit": _f(d["net_profit_after_refund"]),
                "days": days, "category": cat,
                "reason": (d["frr"] or d["return_reason"] or "").strip(),
                "request_ts": d["rts"], "refund_ts": d["fts"],
                "case_status": d["case_status"] or "", "flag": d["flag_color"] or "",
                "resolved": (d["case_status"] or "").lower() in ("resolved", "won", "lost", "closed", "ignored"),
            })
    c.close()
    CATS = ["no_return_request", "instant_auto", "delayed"]
    LABEL = {"no_return_request": "No return request", "instant_auto": "Instant ≤2d (Amazon auto-refund)", "delayed": "Delayed >15d"}
    summary = []
    for cat in CATS:
        items = [x for x in issues if x["category"] == cat]
        openn = [x for x in items if not x["resolved"]]
        summary.append({"category": cat, "label": LABEL[cat], "total": len(items),
                        "open": len(openn), "exposure": round(sum((x["refund_amount"] or 0) for x in openn), 2)})
    issues.sort(key=lambda x: (x["resolved"], -(x["refund_amount"] or 0)))
    return {"summary": summary, "issues": issues[:3000], "count": len(issues)}


# ── SEVERE ISSUES (action center — money-leak items needing a human) ──────────
_SEV_INBOX = {"Hathoro": ("abecomllc26", "salesmercentile", "wrenchlyllc"),
              "HOANK": ("asad_amazon_us", "inhur_llc")}

def severe_issues(account=None, zoro_account=None, date_from=None, date_to=None):
    """The high-severity, money-leaking items consolidated for action:
      • placed_not_captured — Zoro order placed+paid but no Amazon order in closing_orders
      • refund_no_return    — refunded with NO return-authorization (refund without a return)
      • refund_loss         — refunded AND net profit after refund is negative (real loss)
    Each refund category is resolvable via the order case_status (order_crm)."""
    acct = account if (account and account.lower() != "all") else "Hathoro"
    date_from = date_from or "2026-05-01"   # default: May 2026 onwards (older = pre-closing noise)
    inbox = _SEV_INBOX.get(acct, _SEV_INBOX["Hathoro"])
    c = _ro()
    # placed-but-not-captured (DB-only, no bank needed)
    ph = ",".join("?" * len(inbox)); azp = list(inbox); azw = ""
    if date_from:
        azw += " AND email_timestamp>=?"; azp.append(date_from)
    if date_to:
        azw += " AND email_timestamp<?"; azp.append(date_to)
    az = {}
    for wb, tot in c.execute(f"SELECT wb_order_id, order_total FROM archive_zoro WHERE gmail_account IN ({ph}){azw} ORDER BY email_timestamp", azp):
        if wb:
            az[wb] = round(tot or 0, 2)
    ID = re.compile(r"(?:WB|SO)\d+")
    cl = set()
    for moid, wb in c.execute("SELECT merchant_order_id, wb_order_id FROM closing_orders WHERE account=?", (acct,)):
        for x in ID.findall((moid or "").upper()):
            cl.add(x)
        if wb:
            cl.add(wb)
    crm_wb = {r[0]: (r[1] or "", r[2] or "") for r in c.execute("SELECT amazon_order_id, case_status, flag_color FROM order_crm")}
    RESV = ("resolved", "won", "lost", "closed", "ignored")
    gap_items = [{"wb": w, "amount": az[w], "case_status": crm_wb.get(w, ("", ""))[0],
                  "flag": crm_wb.get(w, ("", ""))[1],
                  "resolved": (crm_wb.get(w, ("", ""))[0] or "").lower() in RESV}
                 for w in az if w not in cl]
    gap_items.sort(key=lambda x: (x["resolved"], -(x["amount"] or 0)))
    open_gap = [x for x in gap_items if not x["resolved"]]
    placed = {"count": len(open_gap), "total": len(gap_items),
              "exposure": round(sum(x["amount"] or 0 for x in open_gap), 2),
              "items": gap_items[:1500]}
    # refund categories
    dw, dp = "", [acct]
    if date_from:
        dw += " AND co.purchase_date>=?"; dp.append(date_from)
    if date_to:
        dw += " AND co.purchase_date<?"; dp.append(date_to)
    SEL = ("SELECT co.amazon_order_id AS order_id, co.asin, co.sku, co.title, co.refund_amount, "
           "co.profit_dollars, co.net_profit_after_refund, co.return_reason, crm.case_status, crm.flag_color AS flag "
           "FROM closing_orders co LEFT JOIN order_crm crm ON crm.amazon_order_id=co.amazon_order_id "
           "WHERE co.account=? AND co.refund_done='Yes'" + dw)
    norr = [dict(r) for r in c.execute(
        SEL + " AND NOT EXISTS(SELECT 1 FROM archive_returns ar WHERE ar.amazon_order_id=co.amazon_order_id AND ar.email_type='return_auth')"
        " ORDER BY CAST(NULLIF(co.refund_amount,'') AS REAL) DESC", dp)]
    loss = [dict(r) for r in c.execute(
        SEL + " AND CAST(NULLIF(co.net_profit_after_refund,'') AS REAL) < 0"
        " ORDER BY CAST(NULLIF(co.net_profit_after_refund,'') AS REAL) ASC", dp)]
    c.close()
    def resolved(r): return (r.get("case_status") or "").lower() in ("resolved", "won", "lost", "closed", "ignored")
    def pack(rows, exposure_key="refund_amount"):
        for r in rows:
            r["resolved"] = resolved(r)
        openn = [r for r in rows if not r["resolved"]]
        return {"count": len(rows), "open": len(openn),
                "exposure": round(sum(_f(r.get(exposure_key)) or 0 for r in openn), 2),
                "items": rows[:800]}
    return {"account": acct,
            "placed_not_captured": placed,
            "refund_no_return": pack(norr),
            "refund_loss": pack(loss)}


# ── OPS / CONTROL TOWER ──────────────────────────────────────────────────────
def ops(account=None, zoro_account=None, date_from=None, date_to=None):
    rows = _rows(account, zoro_account, date_from, date_to)
    stuck = []
    for r in rows:
        ds = r.get("delivery_status")
        no_track = not (r.get("tracking_ids") or "").strip()
        pending = r.get("order_label") == "Pending"
        db_ = (r.get("deliver_by_date") or "")[:10]
        if (no_track or (ds and ds not in DELIVERED)) and db_ and db_ < TODAY and r.get("manual_delivered") != "Yes":
            stuck.append({"order_id": r["amazon_order_id"], "account": r.get("account"), "asin": r.get("asin"),
                          "wb": r.get("wb_order_id"), "zoro_cost": _f(r.get("zoro_order_total")),
                          "deliver_by": db_, "delivery_status": ds or ("Pending/no-tracking" if no_track else "?"),
                          "tracking": (r.get("tracking_ids") or "")[:30]})
    # capital in-flight (Pending)
    pend = [r for r in rows if r.get("order_label") == "Pending"]
    inflight_cost = round(sum(_f(r.get("zoro_order_total")) or 0 for r in pend), 2)
    inflight_profit = round(sum(_f(r.get("profit_dollars")) or 0 for r in pend), 2)
    untracked = [r for r in pend if not (r.get("tracking_ids") or "").strip()]
    # ship-speed SLA by zoro account
    sla = {}
    for r in rows:
        za = r.get("zoro_account_name") or "?"
        s = sla.setdefault(za, {"zoro_account": za, "n": 0, "ship_hrs": [], "slow": 0})
        s["n"] += 1
        h = _f(r.get("time_to_ship_hrs"))
        if h is not None:
            s["ship_hrs"].append(h)
            if h > 48:
                s["slow"] += 1
    sla_rows = []
    for s in sla.values():
        hrs = s["ship_hrs"]
        sla_rows.append({"zoro_account": s["zoro_account"], "orders": s["n"],
                         "avg_ship_hrs": round(sum(hrs) / len(hrs), 1) if hrs else None, "slow_48h": s["slow"]})
    # throughput by day (last 21 days of data)
    daily = {}
    for r in rows:
        d = (r.get("purchase_date") or "")[:10]
        if d:
            daily[d] = daily.get(d, 0) + 1
    series = [{"date": d, "orders": daily[d]} for d in sorted(daily)][-30:]
    return {"stuck": {"count": len(stuck), "amount": round(sum(x["zoro_cost"] or 0 for x in stuck), 2),
                      "rows": sorted(stuck, key=lambda x: -(x["zoro_cost"] or 0))[:300]},
            "inflight": {"orders": len(pend), "cost": inflight_cost, "profit": inflight_profit,
                         "untracked": len(untracked), "untracked_cost": round(sum(_f(r.get("zoro_order_total")) or 0 for r in untracked), 2)},
            "sla": sorted(sla_rows, key=lambda x: -(x["avg_ship_hrs"] or 0)),
            "throughput": series}


# ── SOURCING / CATALOG GROWTH ────────────────────────────────────────────────
def sourcing(account=None, zoro_account=None, date_from=None, date_to=None):
    rows = _rows(account, zoro_account, date_from, date_to)
    # our active zoro_skus + their margin/profit
    our = {}
    for r in rows:
        zs = (r.get("zoro_sku") or "").strip()
        if not zs:
            continue
        g = our.setdefault(zs, {"zoro_sku": zs, "asin": r.get("asin"), "profit": 0, "units": 0, "paid": [], "margins": []})
        g["profit"] += _f(r.get("profit_dollars")) or 0; g["units"] += _f(r.get("quantity")) or 0
        sub = _f(r.get("zoro_subtotal")); qz = _f(r.get("zoro_qty")) or 1
        if sub:
            g["paid"].append(sub / qz if qz else sub)
        if r.get("profit_percent") is not None:
            g["margins"].append(_f(r.get("profit_percent")))
    cat = _catalog(set(our.keys()))
    # restock-risk (our winners going OOS) + price-spike
    restock, spike = [], []
    our_l2 = {}   # winning subcategory -> profit, count
    for zs, g in our.items():
        info = cat.get(zs)
        if not info:
            continue
        if info.get("l2"):
            s = our_l2.setdefault(info["l2"], {"l2": info["l2"], "l1": info.get("l1"), "our_skus": 0, "profit": 0})
            s["our_skus"] += 1; s["profit"] += g["profit"]
        status = (info.get("status") or "").lower()
        if status and status not in ("in stock", "drop ship") and g["profit"] > 50:
            restock.append({"zoro_sku": zs, "asin": g["asin"], "status": info.get("status"),
                            "profit": round(g["profit"], 2), "brand": info.get("brand")})
        paid = sum(g["paid"]) / len(g["paid"]) if g["paid"] else None
        cp = info.get("price")
        if paid and cp and cp > 0 and (cp - paid) / paid >= 0.08:
            spike.append({"zoro_sku": zs, "asin": g["asin"], "paid": round(paid, 2), "catalog": round(cp, 2),
                          "rise_pct": round((cp - paid) / paid * 100, 0), "profit": round(g["profit"], 2)})
    # expansion map + list-next: catalog depth in our winning subcategories
    expansion, list_next = [], []
    top_l2 = sorted([s for s in our_l2.values() if s["profit"] > 0], key=lambda x: -x["profit"])[:12]
    if os.path.exists(CATALOG) and top_l2:
        try:
            c = sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True, timeout=15)
            ours_set = set(our.keys())
            for s in top_l2:
                try:
                    depth = c.execute("SELECT COUNT(*) FROM zoro_data WHERE level_two_name=? AND availabilityStatus='In Stock'",
                                      (s["l2"],)).fetchone()[0]
                except Exception:
                    depth = 0
                expansion.append({**s, "profit": round(s["profit"], 2), "catalog_depth": depth})
                # list-next candidates from this subcat (in stock, not ours), cheapest-first proxy
                try:
                    for r in c.execute("SELECT zoro_no, zoro_brand_name, short_description, sales_price FROM zoro_data "
                                       "WHERE level_two_name=? AND availabilityStatus='In Stock' "
                                       "ORDER BY sales_price LIMIT 60", (s["l2"],)):
                        if r[0] in ours_set:
                            continue
                        list_next.append({"zoro_sku": r[0], "brand": r[1], "desc": (r[2] or "")[:60],
                                          "price": _f(r[3]), "subcategory": s["l2"], "l1": s["l1"]})
                except Exception:
                    pass
            c.close()
        except Exception:
            pass
    list_next = list_next[:300]
    return {"our_skus": len(our),
            "restock_risk": sorted(restock, key=lambda x: -x["profit"])[:100],
            "price_spike": sorted(spike, key=lambda x: -x["rise_pct"])[:100],
            "expansion": expansion,
            "list_next": list_next,
            "list_next_total": len(list_next)}


# ── MARGIN (reprice/pause radar) ─────────────────────────────────────────────
def margin_radar(account=None, zoro_account=None, date_from=None, date_to=None):
    rows = _rows(account, zoro_account, date_from, date_to)
    our = {}
    for r in rows:
        zs = (r.get("zoro_sku") or "").strip()
        if not zs:
            continue
        g = our.setdefault(zs, {"zoro_sku": zs, "asin": r.get("asin"), "sku": r.get("sku"),
                                "orders": 0, "profit": 0, "paid": [], "margins": []})
        g["orders"] += 1; g["profit"] += _f(r.get("profit_dollars")) or 0
        sub = _f(r.get("zoro_subtotal")); qz = _f(r.get("zoro_qty")) or 1
        if sub:
            g["paid"].append(sub / qz if qz else sub)
        if r.get("profit_percent") is not None:
            g["margins"].append(_f(r.get("profit_percent")))
    cat = _catalog(set(our.keys()))
    radar = []
    for zs, g in our.items():
        info = cat.get(zs)
        if not info or not g["paid"]:
            continue
        paid = sum(g["paid"]) / len(g["paid"]); cp = info.get("price")
        if cp and cp > 0 and (cp - paid) / paid >= 0.08:
            cur_m = round(sum(g["margins"]) / len(g["margins"]), 1) if g["margins"] else None
            radar.append({"zoro_sku": zs, "asin": g["asin"], "sku": g["sku"], "orders": g["orders"],
                          "paid": round(paid, 2), "catalog": round(cp, 2),
                          "rise_pct": round((cp - paid) / paid * 100, 0), "margin": cur_m,
                          "profit": round(g["profit"], 2), "brand": info.get("brand")})
    return {"reprice": sorted(radar, key=lambda x: -x["rise_pct"])[:200], "count": len(radar)}


# ── TODAY / COMMAND CENTER ───────────────────────────────────────────────────
def today(account=None, zoro_account=None, date_from=None, date_to=None):
    lk = leakage(account, zoro_account, date_from, date_to); op = ops(account, zoro_account, date_from, date_to); pf = portfolio(account, zoro_account, date_from, date_to)
    mg = margin_radar(account, zoro_account, date_from, date_to); hl = health(account, zoro_account, date_from, date_to)
    risk_accts = [a["account"] for a in hl["accounts"] if a["verdict"] == "red"]
    cards = [
        {"key": "recover", "title": "Recover from Zoro", "value": f"${lk['recoverable']:,.0f}",
         "sub": f"{lk['lost_package']['count']+lk['double_loss']['count']} orders · only ${lk['recovered']:,.0f} clawed back", "tab": "recover", "sev": "high"},
        {"key": "reprice", "title": "Reprice / pause now", "value": mg["count"],
         "sub": "SKUs whose Zoro cost rose ≥8%", "tab": "profit", "sev": "high"},
        {"key": "protect", "title": "Account risk", "value": (", ".join(risk_accts) or "OK"),
         "sub": "accounts in the red vs Amazon thresholds", "tab": "protect", "sev": "high" if risk_accts else "ok"},
        {"key": "stuck", "title": "Stuck orders", "value": op["stuck"]["count"],
         "sub": f"${op['stuck']['amount']:,.0f} past deliver-by / no tracking", "tab": "operate", "sev": "high"},
        {"key": "grow", "title": "List next (growth)", "value": pf["asin_count"],
         "sub": "open the sourcing pipeline", "tab": "grow", "sev": "ok"},
        {"key": "kill", "title": "Kill / fix listings", "value": len(pf["kill"]),
         "sub": f"{pf['net_losers']} net-losing ASINs", "tab": "profit", "sev": "med"},
    ]
    return {"cards": cards}


_FUNCS = {"today": today, "sourcing": sourcing, "portfolio": portfolio,
          "leakage": leakage, "health": health, "ops": ops, "margin": margin_radar}

if __name__ == "__main__":
    import sys
    fn = sys.argv[1] if len(sys.argv) > 1 else "today"
    acct = sys.argv[2] if len(sys.argv) > 2 else None
    print(json.dumps(_FUNCS[fn](acct), indent=2, default=str)[:3000])
