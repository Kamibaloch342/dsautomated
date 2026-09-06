#!/usr/bin/env python3
"""Idempotent schema migration for the CRM dashboard.

Adds (a) the order-script columns closing_orders was missing (name/street/phone/
profile) so script-placed orders can be stored fully, and (b) the CRM layer:
per-order case workflow + a notes thread + an insight-resolution log.
Safe to run repeatedly."""
import os, sqlite3

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "automations_unified.db")

# column -> type, added to closing_orders if absent
NEW_COLS = {
    # order-script fields closing_orders lacked
    "ship_name": "TEXT", "ship_street": "TEXT", "ship_phone": "TEXT", "profile_name": "TEXT",
    # CRM case workflow (one case per order)
    "case_status": "TEXT",        # '' | Open | Awaiting Zoro Credit | Awaiting Return | Resolved
    "case_type": "TEXT",          # amazon_refund | customer_return | manual
    "case_resolution": "TEXT",    # free text outcome
    "resolved_at": "TEXT",
    "reminder_at": "TEXT",        # ISO date for follow-up
    "reminder_note": "TEXT",
    "flag_color": "TEXT",         # red | yellow | green | blue | (none)
    "manual_delivered": "TEXT",   # 'Yes' when user marks a non-FedEx Zoro shipment delivered
}

NOTES_TABLE = """
CREATE TABLE IF NOT EXISTS crm_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    amazon_order_id TEXT NOT NULL,
    author TEXT DEFAULT 'me',
    note TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
)"""
NOTES_IDX = "CREATE INDEX IF NOT EXISTS ix_crm_notes_oid ON crm_notes(amazon_order_id)"

# one row per (insight_type, ref_key) the user has resolved/acknowledged
RES_TABLE = """
CREATE TABLE IF NOT EXISTS insight_resolutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    insight_type TEXT NOT NULL,
    ref_key TEXT NOT NULL,
    note TEXT DEFAULT '',
    resolved_at TEXT DEFAULT (datetime('now')),
    UNIQUE(insight_type, ref_key)
)"""

def main():
    con = sqlite3.connect(DB, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    have = {r[1] for r in con.execute("PRAGMA table_info(closing_orders)")}
    added = []
    for col, typ in NEW_COLS.items():
        if col not in have:
            con.execute(f"ALTER TABLE closing_orders ADD COLUMN {col} {typ}")
            added.append(col)
    con.execute(NOTES_TABLE); con.execute(NOTES_IDX); con.execute(RES_TABLE)
    con.commit()
    print(f"✓ closing_orders columns added: {added or '(all already present)'}")
    print("✓ crm_notes, insight_resolutions ready")
    # backfill name/street/phone/profile from order_placer_orders where missing
    n = con.execute("""
        UPDATE closing_orders AS c SET
          ship_name    = COALESCE(NULLIF(c.ship_name,''),    (SELECT p.ship_name    FROM order_placer_orders p WHERE p.amazon_order_id=c.amazon_order_id)),
          ship_street  = COALESCE(NULLIF(c.ship_street,''),  (SELECT p.ship_street  FROM order_placer_orders p WHERE p.amazon_order_id=c.amazon_order_id)),
          ship_phone   = COALESCE(NULLIF(c.ship_phone,''),   (SELECT p.ship_phone   FROM order_placer_orders p WHERE p.amazon_order_id=c.amazon_order_id)),
          profile_name = COALESCE(NULLIF(c.profile_name,''), (SELECT p.profile_name FROM order_placer_orders p WHERE p.amazon_order_id=c.amazon_order_id))
        WHERE EXISTS (SELECT 1 FROM order_placer_orders p WHERE p.amazon_order_id=c.amazon_order_id)
    """).rowcount
    con.commit()
    print(f"✓ backfilled name/street/phone/profile from placer for {n} orders")
    con.close()

if __name__ == "__main__":
    main()
