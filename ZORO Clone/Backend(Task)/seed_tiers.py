"""
Seed quantity-break price tiers
───────────────────────────────
Adds zoro-style volume pricing to the catalog: ~5% off at the first
break and ~12% off at the second. Thresholds scale with min_order_qty.
Idempotent: products that already have tiers are skipped.

Run:  ./venv_mac/bin/python seed_tiers.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app import app
from database import db
from models import Product


def build_tiers(price, min_qty):
    base = max(min_qty or 1, 1)
    t1_qty = max(base * 5, 10)
    t2_qty = max(base * 20, 50)
    return [
        {"min_qty": t1_qty, "price": round(price * 0.95, 2)},
        {"min_qty": t2_qty, "price": round(price * 0.88, 2)},
    ]


def run():
    added = 0
    with app.app_context():
        for p in Product.query.all():
            if p.price_tiers:                      # keep existing tiers
                continue
            if p.unit_price is None or float(p.unit_price) <= 0:
                continue
            p.price_tiers = build_tiers(float(p.unit_price), p.min_order_qty)
            added += 1
            t = p.price_tiers
            print(f"  {p.zoro_code}: base ${float(p.unit_price):<8} "
                  f"{t[0]['min_qty']}+ → ${t[0]['price']}   "
                  f"{t[1]['min_qty']}+ → ${t[1]['price']}")
        db.session.commit()
    print(f"\nDone — tiers added to {added} product(s).")


if __name__ == "__main__":
    run()
