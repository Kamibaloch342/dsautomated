"""
fill_ww_earnings.py — gap-fill WW (Grainger) orders' earnings from archive_earnings
(by amazon_order_id) and compute profit = earnings - grainger cost. Keeps order_label
as-is (WW stay 'Manual'). Run after fetch_account_earnings + sync_gmail_archive.

Usage: ../.venv/bin/python scripts/fill_ww_earnings.py
"""
import os, sqlite3
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "automations_unified.db")
c = sqlite3.connect(DB, timeout=60); c.execute("PRAGMA busy_timeout=60000")
ww = c.execute("SELECT amazon_order_id, zoro_order_total, earnings FROM closing_orders WHERE merchant_order_id LIKE 'WW%'").fetchall()
filled = profited = 0
for aid, cost, cur_earn in ww:
    if cur_earn:
        continue  # already has earnings
    e = c.execute("SELECT total_your_earnings, total_amazon_fees, total_shipping FROM archive_earnings WHERE amazon_order_id=?", (aid,)).fetchone()
    if not e or not e[0]:
        continue
    earn = float(e[0])
    sets, vals = ["earnings=?", "amazon_fees=?", "shipping_amt=?"], [earn, e[1], e[2]]
    if cost and cost > 0:
        profit = round(earn - float(cost), 2); pct = round(profit / earn * 100, 2) if earn else None
        sets += ["profit_dollars=?", "profit_percent=?", "net_profit_after_refund=?"]; vals += [profit, pct, profit]
        profited += 1
    vals.append(aid)
    c.execute(f"UPDATE closing_orders SET {', '.join(sets)} WHERE amazon_order_id=?", vals)
    filled += 1
c.commit()
c.execute("PRAGMA query_only=ON")
tot = c.execute("SELECT COUNT(*), ROUND(SUM(profit_dollars),2) FROM closing_orders WHERE merchant_order_id LIKE 'WW%' AND profit_dollars IS NOT NULL").fetchone()
still = c.execute("SELECT COUNT(*) FROM closing_orders WHERE merchant_order_id LIKE 'WW%' AND earnings IS NULL").fetchone()[0]
print(f"newly filled earnings: {filled} | profit computed: {profited}")
print(f"WW with profit now: {tot[0]} orders, total profit ${tot[1]}")
print(f"WW still missing earnings: {still}")
