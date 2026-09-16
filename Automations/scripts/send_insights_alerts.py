#!/usr/bin/env python3
"""
send_insights_alerts.py — Dispatch operational insights directly to WhatsApp groups.

Routes insights with strict account separation:
  • HOANK Insights   -> 120363429340767368@g.us
  • Hathoro Insights -> 120363413450875187@g.us

Covers all 8 overhauled insight categories:
  1. Amazon Earnings/Unit (Increase ↑ vs Decrease ↓)
  2. Amazon Buyer Shipping Status Changed (Free <-> Paid)
  3. Zoro Base Unit Price (Increase ↑ vs Decrease ↓)
  4. Zoro Shipping Rule Deviation ($50 free / $5.99)
  5. Handling-Time Tuner (Delivery slack vs Zoro ETA)
  6. Out of Stock (OOS) / Supplier Depletion & Alt SKU
  7. Low Margin Split (< 8% vs Unplaced Loss-Kamran)
  8. Low Zoro Stock (< 20 units)
Plus operational protections (Zip mismatch, Undelivered past due, Refunded, Shipped late, etc.).

Deduplication:
  Dispatched insights are recorded in SQLite table `whatsapp_dispatched_insights`
  so that each unique transition/order is alerted only ONCE.
"""

import os
import sys
import argparse
import sqlite3
import datetime
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_AUTO_DIR = os.path.dirname(_SCRIPT_DIR)
_ROOT_DIR = os.path.dirname(_AUTO_DIR)

if _AUTO_DIR not in sys.path:
    sys.path.insert(0, _AUTO_DIR)
if os.path.join(_AUTO_DIR, "dashboard") not in sys.path:
    sys.path.insert(0, os.path.join(_AUTO_DIR, "dashboard"))

import insights
from utils.whatsapp import is_whatsapp_enabled, get_whatsapp_group, send_whatsapp_message

DB_PATH = os.path.join(_AUTO_DIR, "data", "automations_unified.db")

CATEGORY_HEADERS = {
    "earnings_increase": "📈 *AMAZON EARNINGS INCREASED* 🟢",
    "earnings_decrease": "📉 *AMAZON EARNINGS DECREASED* 🔴",
    "shipping_change": "🚚 *BUYER SHIPPING STATUS CHANGED*",
    "zoro_price_increase": "🏷️ *ZORO BASE PRICE INCREASED* 🔴",
    "zoro_price_decrease": "🏷️ *ZORO BASE PRICE DECREASED* 🟢",
    "zoro_ship_rule": "⚠️ *ZORO SHIPPING RULE DEVIATION*",
    "handling_time_tune": "⏱️ *HANDLING-TIME TUNER (SLACK)*",
    "oos_alert": "🚨 *OUT OF STOCK / SUPPLIER DEPLETION*",
    "low_margin": "⚠️ *LOW MARGIN WARNING (< 8%)*",
    "low_zoro_stock": "📦 *LOW ZORO STOCK (< 20 UNITS)*",
    "zip_mismatch": "📍 *BUYER ZIP CODE MISMATCH*",
    "undelivered": "📬 *PACKAGE OVERDUE / UNDELIVERED*",
    "refunded": "💵 *REFUND ACTION REQUIRED*",
    "wrong_qty": "🔢 *WRONG QUANTITY MULTIPLIER*",
    "shipped_late": "⏰ *SHIPPED LATE VS AMAZON PROMISE*",
    "wb_unmatched": "❓ *WB ORDER UNMATCHED*",
}


def _init_tracking_table(con: sqlite3.Connection):
    """Ensure the dispatch tracking table exists."""
    con.execute(
        """CREATE TABLE IF NOT EXISTS whatsapp_dispatched_insights (
            ref_key TEXT PRIMARY KEY,
            account TEXT,
            category TEXT,
            dispatched_at TEXT,
            message TEXT
        )"""
    )
    con.commit()


def _get_dispatched_keys(con: sqlite3.Connection) -> set:
    """Load previously dispatched ref_keys."""
    _init_tracking_table(con)
    rows = con.execute("SELECT ref_key FROM whatsapp_dispatched_insights").fetchall()
    return {r[0] for r in rows}


def _record_dispatched(con: sqlite3.Connection, ref_key: str, account: str, category: str, message: str):
    """Record that an insight was dispatched."""
    now_iso = datetime.datetime.now().isoformat()
    con.execute(
        """INSERT OR REPLACE INTO whatsapp_dispatched_insights (ref_key, account, category, dispatched_at, message)
           VALUES (?, ?, ?, ?, ?)""",
        (ref_key, account, category, now_iso, message),
    )
    con.commit()


def _load_asin_meta(con: sqlite3.Connection) -> dict:
    """Pre-index ASIN -> {title, sku} metadata for 100% card fidelity."""
    meta = {}
    try:
        for r in con.execute(
            "SELECT UPPER(asin), title, sku FROM closing_orders WHERE asin IS NOT NULL AND title IS NOT NULL GROUP BY UPPER(asin)"
        ):
            if r[0]:
                meta[r[0]] = {"title": str(r[1] or "").strip(), "sku": str(r[2] or "").strip()}
    except Exception:
        pass
    try:
        for r in con.execute(
            "SELECT UPPER(asin), title, sku FROM order_placer_orders WHERE asin IS NOT NULL AND title IS NOT NULL GROUP BY UPPER(asin)"
        ):
            if r[0]:
                entry = meta.setdefault(r[0], {})
                if r[1]:
                    entry["title"] = str(r[1]).strip()
                if r[2]:
                    entry["sku"] = str(r[2]).strip()
    except Exception:
        pass
    return meta


def dispatch_account_insights(
    account: str,
    days: int = 7,
    limit: int = 20,
    category_filter: str = None,
    dry_run: bool = False,
    all_time: bool = False
) -> int:
    """Compute and send pending insights for a given account (HOANK or Hathoro)."""
    if not is_whatsapp_enabled() and not dry_run:
        print("  [WhatsApp] WhatsApp notifications are disabled in bot_config.json.")
        return 0

    target_group = get_whatsapp_group(account, kind="insights")
    print(f"\n=======================================================")
    print(f"  🔍 Processing Insights for {account.upper()}")
    print(f"  🎯 Target Group: {target_group}")
    print(f"=======================================================")

    today_str = datetime.date.today().isoformat()
    date_from = None
    if not all_time:
        if days > 0:
            d = datetime.date.today() - datetime.timedelta(days=days)
            date_from = d.isoformat()
            print(f"  📅 Window: {date_from} to today ({days} days)")
        else:
            date_from = today_str
            print(f"  📅 Window: Today only ({today_str})")
    else:
        print(f"  📅 Window: All Time")

    res = insights.compute(account=account, date_from=date_from, db_path=DB_PATH)
    all_insights = res.get("insights", {})

    con = sqlite3.connect(DB_PATH, timeout=10)
    already_sent = _get_dispatched_keys(con)
    asin_meta = _load_asin_meta(con)

    sent_count = 0
    categories = [category_filter] if category_filter else list(all_insights.keys())

    for cat in categories:
        items = all_insights.get(cat, [])
        if not items:
            continue

        header = CATEGORY_HEADERS.get(cat, f"💡 *{cat.upper().replace('_', ' ')}*")

        for item in items:
            ref_key = item.get("ref_key")
            if not ref_key or ref_key in already_sent:
                continue

            # USER MANDATE: Only send NEW ones (today's alerts), never historical backlogs
            d_str = str(item.get("date") or "")[:10]
            if not all_time and d_str and d_str < today_str:
                continue

            msg_body = item.get("message") or "Insight alert"
            order_id = item.get("order_id")
            asin = (item.get("asin") or "").strip().upper()
            title = str(item.get("title") or "").strip()
            sku = str(item.get("sku") or item.get("main_sku") or "").strip()

            # Fallback lookup in pre-indexed metadata
            if asin and asin in asin_meta:
                if not title:
                    title = asin_meta[asin].get("title", "")
                if not sku:
                    sku = asin_meta[asin].get("sku", "")

            # If still missing and order_id exists, lookup by order_id
            if (not title or not sku) and order_id:
                try:
                    row = con.execute(
                        "SELECT title, sku, asin FROM order_placer_orders WHERE amazon_order_id = ? AND title IS NOT NULL LIMIT 1",
                        (order_id,)
                    ).fetchone()
                    if row:
                        title = title or str(row[0] or "").strip()
                        sku = sku or str(row[1] or "").strip()
                        if not asin and row[2]:
                            asin = str(row[2]).strip().upper()
                except Exception:
                    pass

            lines = [header]
            lines.append(f"• Title: *{title[:80] if title else 'Product Listing'}*")
            lines.append(f"• SKU: `{sku if sku else 'N/A'}`")
            if asin:
                lines.append(f"• ASIN: `{asin}`")
            if order_id:
                lines.append(f"• Amazon Order: `{order_id}`")
            if d_str:
                lines.append(f"• Date: {d_str}")

            lines.append(f"• Detail: {msg_body}")
            full_text = "\n".join(lines)

            print(f"  📤 [{cat}] -> {ref_key}")
            if dry_run:
                print(f"     [DRY-RUN] Would send:\n{full_text}\n")
            else:
                ok = send_whatsapp_message(full_text, to=target_group, background=False)
                if ok:
                    _record_dispatched(con, ref_key, account, cat, msg_body)
                    already_sent.add(ref_key)
                    sent_count += 1
                    time.sleep(0.4)
                else:
                    print(f"     ⚠️ Failed to send via gateway!")

            if limit > 0 and sent_count >= limit:
                print(f"  🛑 Reached per-run limit of {limit} alerts.")
                break

        if limit > 0 and sent_count >= limit:
            break

    con.close()
    print(f"  ✅ Finished {account}: {sent_count} alert(s) dispatched to WhatsApp.")
    return sent_count


def main():
    parser = argparse.ArgumentParser(description="Dispatch operational insights to WhatsApp groups.")
    parser.add_argument("--account", choices=["HOANK", "Hathoro", "all"], default="all", help="Target store account")
    parser.add_argument("--days", type=int, default=0, help="Lookback window in days (default: 0 = today only)")
    parser.add_argument("--all-time", action="store_true", help="Process all-time insights without date cutoff")
    parser.add_argument("--limit", type=int, default=25, help="Max alerts to send per account per run (default: 25, 0 = unlimited)")
    parser.add_argument("--category", type=str, default=None, help="Filter by specific category key (e.g. oos_alert)")
    parser.add_argument("--dry-run", action="store_true", help="Preview alerts without sending")

    args = parser.parse_args()

    total_sent = 0
    accounts = ["HOANK", "Hathoro"] if args.account == "all" else [args.account]

    for acc in accounts:
        total_sent += dispatch_account_insights(
            account=acc,
            days=args.days,
            limit=args.limit,
            category_filter=args.category,
            dry_run=args.dry_run,
            all_time=args.all_time,
        )

    print(f"\n🎉 Total insights dispatched: {total_sent}")


if __name__ == "__main__":
    main()
