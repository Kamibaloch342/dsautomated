#!/usr/bin/env python3
"""
hunter_cli.py — Interactive Command-Line Control for the 24/7 Hunting Daemon

Usage:
  python3 hunter_cli.py --status            # View live token balance, queue, and profitable count
  python3 hunter_cli.py --add-brand "3M"    # Push a specific Zoro brand to front of queue (Priority 1)
  python3 hunter_cli.py --once              # Run a single brand hunting cycle and exit
  python3 hunter_cli.py --run               # Start continuous 24/7 hunting daemon
  python3 hunter_cli.py --list-profitable   # Print top profitable listings ready for Amazon
"""

import os
import sys
import sqlite3
import argparse

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)

from autonomous_hunter_daemon import (
    HUNTER_DB,
    check_keepa_tokens,
    init_hunter_db,
    seed_top_zoro_brands,
    run_daemon_cycle,
    OUTPUT_EXCEL,
)


def show_status():
    init_hunter_db()
    seed_top_zoro_brands()

    tokens = check_keepa_tokens()
    conn = sqlite3.connect(HUNTER_DB)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM brands_queue WHERE status = 'COMPLETED'")
    brands_completed = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM brands_queue WHERE status = 'PENDING'")
    brands_pending = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM hunting_asins")
    total_asins = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM matched_listings WHERE is_pack_match = 1")
    exact_pack_matches = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM profitable_listings")
    profitable_count = cur.fetchone()[0]

    print("\n================================================================================")
    print("  ⭐ AUTONOMOUS KEEPA & ZORO HUNTING ENGINE — LIVE STATUS DASHBOARD")
    print("================================================================================")
    print(f"[*] Keepa Token Balance : {tokens['tokens_left']} Tokens available")
    print(f"[*] Refill Rate         : {tokens['refill_rate']} token/min (Next in {tokens['refill_in_sec']}s)")
    print(f"[*] Brands Completed    : {brands_completed}")
    print(f"[*] Brands Queued       : {brands_pending}")
    print(f"[*] Total ASINs Scanned : {total_asins}")
    print(f"[*] 1:1 Pack Matches    : {exact_pack_matches}")
    print(f"[*] Profitable Listings : {profitable_count} Listings (Live in Tab 1)")
    print(f"[*] Master Excel File   : {OUTPUT_EXCEL}")
    print("--------------------------------------------------------------------------------")

    # Next 5 brands in queue
    cur.execute("""
        SELECT brand_name, priority, items_in_zoro, status
        FROM brands_queue
        ORDER BY priority ASC, items_in_zoro DESC
        LIMIT 5
    """)
    rows = cur.fetchall()
    print("\n[*] Next Brands Up in Hunting Queue:")
    for idx, (b, prio, cnt, st) in enumerate(rows, 1):
        print(f"    {idx}. {b:25} | Priority: {prio:2d} | Zoro Catalog: {cnt:5d} items | Status: {st}")

    conn.close()
    print("================================================================================\n")


def add_brand(brand_name: str):
    init_hunter_db()
    conn = sqlite3.connect(HUNTER_DB)
    cur = conn.cursor()
    b_clean = brand_name.strip().upper()
    cur.execute("""
        INSERT INTO brands_queue (brand_name, priority, status)
        VALUES (?, 1, 'PENDING')
        ON CONFLICT(brand_name) DO UPDATE SET priority = 1, status = 'PENDING'
    """, (b_clean,))
    conn.commit()
    conn.close()
    print(f"\n[✓] Successfully prioritized brand '{b_clean}' to Priority 1 (Next in queue).")


def list_profitable():
    init_hunter_db()
    conn = sqlite3.connect(HUNTER_DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT alert_badge, asin, brand, title, monthly_sales, zoro_price, lowest_mfn_price,
               std_net_profit, std_net_margin
        FROM profitable_listings
        WHERE std_net_margin >= 1.0
        ORDER BY std_net_margin DESC, monthly_sales DESC
        LIMIT 30
    """)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print("\n[*] No strictly profitable listings recorded yet (Standard Margin >= 1.0%).")
        return

    print(f"\n{'='*120}")
    print(f"  ⭐ TOP PROFITABLE DROPSHIPPING LISTINGS (STANDARD PROFIT ≥ 1.0% | ZERO COUPONS)")
    print(f"  Shipping Rule: $0.00 if Zoro Wholesale >= $50.00, else $5.99 | Evaluated at Lowest Competitor Price")
    print(f"{'='*120}")
    print(f"{'Badge':16} | {'ASIN':10} | {'Brand':12} | {'Sales':5} | {'Zoro':6} | {'Ship':5} | {'Low Price':9} | {'Ref Fee':7} | {'Net Profit':10} | {'Margin':7} | Title")
    print(f"{'-'*120}")
    for r in rows:
        badge, asin, brand, title, sales, zp, lp, np_, nm = r
        z_ship = 0.0 if zp >= 50.0 else 5.99
        ref_fee = round(lp * 0.15, 2)
        tot_cost = round(zp + z_ship + ref_fee, 2)
        calc_np = round(lp - tot_cost, 2)
        calc_nm = round((calc_np / lp) * 100.0, 2)
        badge_clean = "HIGH PROFIT" if calc_nm >= 5.0 else "PROFITABLE"
        print(f"{badge_clean:16} | {asin:10} | {str(brand)[:12]:12} | {sales or 0:5d} | ${zp:5.2f} | ${z_ship:4.2f} | ${lp or 0:8.2f} | ${ref_fee:6.2f} | +${calc_np:6.2f}   | {calc_nm:5.1f}% | {title[:28]}")
    print(f"{'='*120}\n")


def main():
    parser = argparse.ArgumentParser(description="Autonomous Keepa & Zoro Dropshipping Hunter CLI")
    parser.add_argument("--status", action="store_true", help="View live hunting queue & token status")
    parser.add_argument("--add-brand", type=str, help="Add brand to priority 1 in queue")
    parser.add_argument("--once", action="store_true", help="Run a single hunting cycle and exit")
    parser.add_argument("--run", action="store_true", help="Start continuous 24/7 hunting daemon")
    parser.add_argument("--list-profitable", action="store_true", help="Print top profitable listings")

    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.add_brand:
        add_brand(args.add_brand)
    elif args.list_profitable:
        list_profitable()
    elif args.once:
        run_daemon_cycle()
    elif args.run:
        from autonomous_hunter_daemon import main as daemon_main
        daemon_main()
    else:
        show_status()


if __name__ == "__main__":
    main()
