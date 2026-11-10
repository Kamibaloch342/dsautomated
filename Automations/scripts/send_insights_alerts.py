#!/usr/bin/env python3
"""
send_insights_alerts.py — Dispatch operational insights directly to WhatsApp groups.

Routes insights with strict account separation, one Insights group per store
(Wrenchly has none yet, so its alerts go to the default group — utils.whatsapp.get_whatsapp_group).

Sent as they happen (hourly):
  1. Amazon Earnings/Unit (Increase ↑ vs Decrease ↓), once per real change
  2. Buyer paid for shipping on a free-shipping listing / a paid listing went free
  3. Zoro Price (Increase ↑ vs Decrease ↓), from the Unit Price on Zoro's order e-mails
  4. Zoro Shipping Rule Deviation ($50 free / $5.99)
  5. Zoro late against Amazon's promise, 2+ business days (increase handling)
  6. Out of Stock, and Alternative SKU used (one per ASIN per day)
  7. Low Margin (< 8%, one per ASIN per day; unplaced 'Loss-Kamran' per order)
  8. Zip mismatch, Wrong quantity

Sent as one summary per store:
  • daily, from SUMMARY_HOUR: Refunded, Undelivered past due, Shipped late, WB unmatched. These
    happen days after the purchase, so the "only new alerts" check below can never pass them.
  • weekly: the listings that really have handling days to cut.

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

ACCOUNTS = ("HOANK", "Hathoro", "Wrenchly")

CATEGORY_HEADERS = {
    "earnings_increase": "📈 *AMAZON EARNINGS INCREASED* 🟢",
    "earnings_decrease": "📉 *AMAZON EARNINGS DECREASED* 🔴",
    "shipping_change": "🚚 *BUYER SHIPPING*",
    "zoro_price_increase": "🏷️ *ZORO PRICE INCREASED* 🔴",
    "zoro_price_decrease": "🏷️ *ZORO PRICE DECREASED* 🟢",
    "zoro_ship_rule": "⚠️ *ZORO SHIPPING RULE DEVIATION*",
    "handling_time_tune": "⏱️ *ZORO LATE AGAINST AMAZON PROMISE*",
    "oos_alert": "🚨 *OUT OF STOCK*",
    "alt_sku_used": "🔄 *ALTERNATIVE SKU USED (order placed)*",
    "low_margin": "⚠️ *LOW MARGIN WARNING (< 8%)*",
    "zip_mismatch": "📍 *BUYER ZIP CODE MISMATCH*",
    "wrong_qty": "🔢 *WRONG QUANTITY MULTIPLIER*",
}

# One message per store instead of one message per item, in this order.
DAILY_SUMMARY = (
    ("refunded", "💵 Refunded"),
    ("undelivered", "📬 Undelivered past due"),
    ("shipped_late", "⏰ Shipped late"),
    ("wb_unmatched", "❓ WB not in placed orders"),
)
WEEKLY_SUMMARY = (("handling_review", "⏱️ Handling days that can be cut"),)
_SUMMARY_CATEGORIES = {c for c, _ in DAILY_SUMMARY + WEEKLY_SUMMARY}

# The daily summary waits for the morning's closing run. Local time.
SUMMARY_HOUR = int(os.environ.get("ZUHRA_SUMMARY_HOUR", "12") or 12)
SUMMARY_LOOKBACK_DAYS = 30
SUMMARY_MAX_PER_CATEGORY = 12

BASELINE_MESSAGE = "BASELINE_HISTORICAL"


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


def _record_many(con: sqlite3.Connection, rows: list):
    """Record (ref_key, account, category, message) rows in one short write."""
    if not rows:
        return
    now_iso = datetime.datetime.now().isoformat()
    con.executemany(
        """INSERT OR IGNORE INTO whatsapp_dispatched_insights (ref_key, account, category, dispatched_at, message)
           VALUES (?, ?, ?, ?, ?)""",
        [(k, a, c, now_iso, m) for k, a, c, m in rows],
    )
    con.commit()


def _pretty_day(iso: str) -> str:
    """'2026-11-07' → 'Sat, Nov 7' for the alert's Date line."""
    try:
        d = datetime.date.fromisoformat(str(iso)[:10])
        return f"{d:%a, %b} {d.day}"
    except ValueError:
        return str(iso or "")


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


def _summary_lines(label: str, items: list) -> list:
    lines = [f"*{label} ({len(items)})*"]
    for it in items[:SUMMARY_MAX_PER_CATEGORY]:
        who = it.get("order_id") or it.get("asin") or ""
        msg = str(it.get("message") or "")
        if msg.startswith("ASIN "):
            msg = msg.split(":", 1)[-1].strip()
        lines.append(f"• `{who}` {msg}" if who else f"• {msg}")
    if len(items) > SUMMARY_MAX_PER_CATEGORY:
        lines.append(f"…and {len(items) - SUMMARY_MAX_PER_CATEGORY} more on the dashboard")
    return lines


def _send_summary(con, account: str, target_group: str, state_key: str, title: str, sections: tuple,
                  all_insights: dict, already: set, dry_run: bool) -> int:
    """Send one summary of everything in `sections` not yet reported. 1 when a message went out."""
    blocks, records = [], []
    for cat, label in sections:
        fresh = [i for i in all_insights.get(cat, []) if i.get("ref_key") and i["ref_key"] not in already]
        if fresh:
            blocks.append("\n".join(_summary_lines(label, fresh)))
            records += [(i["ref_key"], account, cat, i.get("message") or "") for i in fresh]

    if not blocks:
        if not dry_run:
            _record_dispatched(con, state_key, account, "summary", "(nothing new)")
        return 0

    text = f"📋 *{title}*\n\n" + "\n\n".join(blocks)
    print(f"  📤 [summary] {state_key}: {len(records)} item(s)")
    if dry_run:
        print(f"     [DRY-RUN] Would send:\n{text}\n")
        return 0
    if not send_whatsapp_message(text, to=target_group, background=False):
        print("     ⚠️ Failed to send via gateway!")   # state not recorded: next run tries again
        return 0
    _record_many(con, records)
    _record_dispatched(con, state_key, account, "summary", f"{len(records)} item(s)")
    already.update(r[0] for r in records)
    return 1


def dispatch_summaries(account: str, dry_run: bool = False, force: bool = False) -> int:
    """The once-a-day and once-a-week messages for one store. Cheap when neither is due."""
    now = datetime.datetime.now()
    today = now.date()
    monday = today - datetime.timedelta(days=today.weekday())
    daily_key = f"summary:daily:{account}:{today.isoformat()}"
    weekly_key = f"summary:weekly:{account}:{monday.isoformat()}"

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        already = _get_dispatched_keys(con)
        due_daily = force or (now.hour >= SUMMARY_HOUR and daily_key not in already)
        due_weekly = force or (now.hour >= SUMMARY_HOUR and weekly_key not in already)
        if not (due_daily or due_weekly):
            return 0

        date_from = (today - datetime.timedelta(days=SUMMARY_LOOKBACK_DAYS)).isoformat()
        all_insights = insights.compute(account=account, date_from=date_from, db_path=DB_PATH).get("insights", {})
        target_group = get_whatsapp_group(account, kind="insights")

        sent = 0
        if due_daily:
            sent += _send_summary(con, account, target_group, daily_key,
                                  f"DAILY SUMMARY — {account} — {_pretty_day(today.isoformat())}",
                                  DAILY_SUMMARY, all_insights, already, dry_run)
        if due_weekly:
            sent += _send_summary(con, account, target_group, weekly_key,
                                  f"WEEKLY HANDLING REVIEW — {account}",
                                  WEEKLY_SUMMARY, all_insights, already, dry_run)
        return sent
    finally:
        con.close()


def dispatch_account_insights(
    account: str,
    days: int = 7,
    limit: int = 20,
    category_filter: str = None,
    dry_run: bool = False,
    all_time: bool = False,
    summaries: bool = False,
) -> int:
    """Compute and send pending insights for a given account."""
    if not is_whatsapp_enabled() and not dry_run:
        print("  [WhatsApp] WhatsApp notifications are disabled in bot_config.json.")
        return 0

    target_group = get_whatsapp_group(account, kind="insights")
    print(f"\n=======================================================")
    print(f"  🔍 Processing Insights for {account.upper()}")
    print(f"  🎯 Target Group: {target_group}")
    print(f"=======================================================")

    today = datetime.date.today()
    date_from = None
    if all_time:
        print(f"  📅 Window: All Time")
    else:
        date_from = (today - datetime.timedelta(days=max(days, 0))).isoformat()
        print(f"  📅 Window: {date_from} to today")

    res = insights.compute(account=account, date_from=date_from, db_path=DB_PATH)
    all_insights = res.get("insights", {})

    con = sqlite3.connect(DB_PATH, timeout=10)
    already_sent = _get_dispatched_keys(con)
    asin_meta = _load_asin_meta(con)

    sent_count = 0
    categories = [category_filter] if category_filter else list(all_insights.keys())

    for cat in categories:
        if cat in _SUMMARY_CATEGORIES:
            continue
        items = all_insights.get(cat, [])
        if not items:
            continue

        header = CATEGORY_HEADERS.get(cat, f"💡 *{cat.upper().replace('_', ' ')}*")

        for item in items:
            ref_key = item.get("ref_key")
            if not ref_key or ref_key in already_sent:
                continue

            # Only new alerts, never historical backlogs. Item dates are ISO; the window is a few
            # days because an order's Amazon date runs behind the clock here.
            d_str = str(item.get("date") or "")[:10]
            if date_from and d_str and d_str < date_from:
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

            # Clean up redundant ASIN and Account prefixes from message body if present
            clean_msg = msg_body
            if clean_msg.startswith("ASIN "):
                parts = clean_msg.split(":", 1)
                if len(parts) == 2:
                    clean_msg = parts[1].strip()

            lines = [header, ""]
            lines.append(f"• Title: *{title[:80] if title else 'Product Listing'}*")
            lines.append(f"• SKU: `{sku if sku else 'N/A'}`")
            if asin:
                lines.append(f"• ASIN: `{asin}`")
            if order_id:
                lines.append(f"• Order: `{order_id}`")
            if d_str:
                lines.append(f"• Date: {_pretty_day(d_str)}")

            lines.append("")
            lines.append(f"• Alert: {clean_msg}")
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
    if summaries and not category_filter:
        try:
            sent_count += dispatch_summaries(account, dry_run=dry_run)
        except Exception as e:
            print(f"  ⚠️ Summary error: {e}")
    print(f"  ✅ Finished {account}: {sent_count} alert(s) dispatched to WhatsApp.")
    return sent_count


def seed_baseline(account: str, days: int = SUMMARY_LOOKBACK_DAYS) -> int:
    """Mark everything already known as reported, without sending, so a change to how alerts are
    keyed cannot re-announce old events. The weekly review is left out: it is about this week."""
    date_from = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    all_insights = insights.compute(account=account, date_from=date_from, db_path=DB_PATH).get("insights", {})
    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        already = _get_dispatched_keys(con)
        rows = [(i["ref_key"], account, cat, BASELINE_MESSAGE)
                for cat, items in all_insights.items() if cat != "handling_review"
                for i in items if i.get("ref_key") and i["ref_key"] not in already]
        _record_many(con, rows)
    finally:
        con.close()
    print(f"  ✅ {account}: {len(rows)} existing item(s) marked as already reported.")
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description="Dispatch operational insights to WhatsApp groups.")
    parser.add_argument("--account", choices=list(ACCOUNTS) + ["all"], default="all", help="Target store account")
    parser.add_argument("--days", type=int, default=0, help="Lookback window in days (default: 0 = today only)")
    parser.add_argument("--all-time", action="store_true", help="Process all-time insights without date cutoff")
    parser.add_argument("--limit", type=int, default=25, help="Max alerts to send per account per run (default: 25, 0 = unlimited)")
    parser.add_argument("--category", type=str, default=None, help="Filter by specific category key (e.g. oos_alert)")
    parser.add_argument("--dry-run", action="store_true", help="Preview alerts without sending")
    parser.add_argument("--summaries", action="store_true", help="Also send the daily / weekly summaries when due")
    parser.add_argument("--baseline", action="store_true", help="Mark what is already known as reported, send nothing")

    args = parser.parse_args()
    accounts = list(ACCOUNTS) if args.account == "all" else [args.account]

    if args.baseline:
        total = sum(seed_baseline(acc, days=args.days or SUMMARY_LOOKBACK_DAYS) for acc in accounts)
        print(f"\n🎉 Baseline: {total} item(s)")
        return

    total_sent = 0
    for acc in accounts:
        total_sent += dispatch_account_insights(
            account=acc,
            days=args.days,
            limit=args.limit,
            category_filter=args.category,
            dry_run=args.dry_run,
            all_time=args.all_time,
            summaries=args.summaries,
        )

    print(f"\n🎉 Total insights dispatched: {total_sent}")


if __name__ == "__main__":
    main()
