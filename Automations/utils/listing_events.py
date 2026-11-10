"""
listing_events.py — one row per thing that happened to an Amazon listing, for the dashboard's Insights.

Two writers (10 Nov 2026):
  • engine/listing_guard.py — a listing taken off sale or given a longer handling time because Zoro
    ran out, and the same listing put back when Zoro had the item again;
  • utils/listing_watch.py  — on an own-brand listing: a seller who is none of our stores, the star
    rating, the number of ratings, the category.
One reader: dashboard/insights.py, which shows each row as an Insights item (Insights tab, and
/api/insights). The WhatsApp message for each is sent by its writer at once, to the store's Insights
group — scripts/send_insights_alerts.py therefore skips these kinds (SENT_AT_SOURCE), or every one
would arrive twice.

A row is small and written in its own short transaction; nothing here ever raises.
"""
from __future__ import annotations

import os
import sqlite3
import time

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(_AUTO, "data", "automations_unified.db")

# kind → (severity, what the Insights tab calls it). The key is the Insights category name.
KINDS = {
    "listing_off_sale": ("high", "Listing off sale / slower: Zoro is out"),
    "listing_back_on_sale": ("low", "Listing back on sale: Zoro restocked"),
    "own_brand_other_seller": ("high", "Own brand: another seller on the listing"),
    "own_brand_rating": ("med", "Own brand: star rating moved"),
    "own_brand_reviews": ("low", "Own brand: number of ratings changed"),
    "own_brand_category": ("med", "Own brand: category changed"),
}
SENT_AT_SOURCE = frozenset(KINDS)

_DDL = """
CREATE TABLE IF NOT EXISTS listing_events (
    ref_key   TEXT PRIMARY KEY,      -- store:asin:kind:YYYY-MM-DD HH:MM — also the Insights item's key
    at        TEXT NOT NULL,         -- local time, 'YYYY-MM-DD HH:MM:SS'
    store     TEXT, asin TEXT, sku TEXT, title TEXT,
    kind      TEXT, severity TEXT, message TEXT
)"""


def record(store: str, asin: str, kind: str, message: str, sku: str = "", title: str = "", db_path: str = "") -> bool:
    """Keep one event. False when it could not be written (database busy) — the caller goes on."""
    try:
        if kind not in KINDS:
            return False
        at = time.strftime("%Y-%m-%d %H:%M:%S")
        c = sqlite3.connect(db_path or DB, timeout=10)
        try:
            c.execute("PRAGMA busy_timeout=10000")
            c.execute(_DDL)
            with c:
                c.execute("INSERT OR REPLACE INTO listing_events (ref_key, at, store, asin, sku, title, kind, severity, message) "
                          "VALUES (?,?,?,?,?,?,?,?,?)",
                          (f"{store}:{asin}:{kind}:{at[:16]}", at, store, str(asin or "").upper(), sku or "",
                           (title or "")[:160], kind, KINDS[kind][0], (message or "")[:500]))
        finally:
            c.close()
        return True
    except Exception as e:
        print(f"  ⚠️  listing event not kept ({type(e).__name__}: {e})")
        return False
