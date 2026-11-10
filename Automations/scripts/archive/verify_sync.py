#!/usr/bin/env python3
"""Verify the order_placer_orders sync."""
import sys
sys.path.insert(0, '/Users/kamranbaloch342/Desktop/Dropshipping/Development_Unit/Automations')
from utils.unified_db import get_conn

with get_conn() as conn:
    total = conn.execute('SELECT COUNT(*) FROM order_placer_orders').fetchone()[0]
    print(f'order_placer_orders: {total} rows')
    
    has_earnings = conn.execute('SELECT COUNT(*) FROM order_placer_orders WHERE earnings IS NOT NULL').fetchone()[0]
    has_ship_by = conn.execute("SELECT COUNT(*) FROM order_placer_orders WHERE ship_by_date IS NOT NULL AND ship_by_date != ''").fetchone()[0]
    has_deliver_by = conn.execute("SELECT COUNT(*) FROM order_placer_orders WHERE deliver_by_date IS NOT NULL AND deliver_by_date != ''").fetchone()[0]
    has_address = conn.execute("SELECT COUNT(*) FROM order_placer_orders WHERE ship_city IS NOT NULL AND ship_city != ''").fetchone()[0]
    print(f'  With earnings: {has_earnings}')
    print(f'  With ship_by_date: {has_ship_by}')
    print(f'  With deliver_by_date: {has_deliver_by}')
    print(f'  With address: {has_address}')
    
    # Check if tracking columns exist
    cols = [r[1] for r in conn.execute('PRAGMA table_info(order_placer_orders)')]
    if 'tracking_ids' in cols:
        has_tracking = conn.execute("SELECT COUNT(*) FROM order_placer_orders WHERE tracking_ids IS NOT NULL AND tracking_ids != ''").fetchone()[0]
        print(f'  With tracking_ids: {has_tracking}')
    if 'shipped_email_time' in cols:
        has_shipped_time = conn.execute("SELECT COUNT(*) FROM order_placer_orders WHERE shipped_email_time IS NOT NULL AND shipped_email_time != ''").fetchone()[0]
        print(f'  With shipped_email_time: {has_shipped_time}')
    
    # Check profile distribution
    cur = conn.execute('SELECT profile_name, COUNT(*) FROM order_placer_orders GROUP BY profile_name')
    print(f'\nBy profile:')
    for row in cur:
        print(f'  {row[0]}: {row[1]}')
    
    # Sample
    row = conn.execute('SELECT * FROM order_placer_orders WHERE ship_city IS NOT NULL AND ship_city != "" LIMIT 1').fetchone()
    print(f'\nSample: {row["amazon_order_id"]} | ASIN:{row["asin"]} | Earn:${row["earnings"]} | ShipBy:{row["ship_by_date"]}')
    print(f'  City:{row["ship_city"]} | State:{row["ship_state"]} | Zip:{row["ship_postal_code"]}')
