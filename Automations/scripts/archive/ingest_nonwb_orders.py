"""
ingest_nonwb_orders.py — bring the NON-WB report orders (the ones closing skips
because they have no WB# to place) into closing_orders, tagged with a reason, so
they show up in analysis.

Categories (from merchant-order-id):
  WW…           → 'WW (Grainger)'   label=Manual    (fulfilled via Grainger, you review)
  SR/OOS/Loss/Discontinued/Dont place/MR/NO/… → reason  label=NotPlaced (received, will cancel)
  blank         → 'blank'           label=NotPlaced / Cancelled (per status)
  (anything Cancelled → label=Cancelled regardless of reason)

Stores report fields only (no Zoro/FedEx enrichment — these were never placed).
manual_review = the reason. DRY-RUN by default.

Usage:
  ../.venv/bin/python scripts/ingest_nonwb_orders.py Hathoro "<May.txt>" "<June.txt>"        # preview
  ../.venv/bin/python scripts/ingest_nonwb_orders.py Hathoro "<May.txt>" "<June.txt>" --go   # apply
"""
import os, sys, re, csv, sqlite3
csv.field_size_limit(10**7)
WB = re.compile(r"WB\d+")

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "Hathoro"
FILES = [a for a in sys.argv[2:] if not a.startswith("--")]
GO = "--go" in sys.argv
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "automations_unified.db")


def classify(moid):
    mu = (moid or "").upper().strip()
    if not mu:
        return "blank"
    if re.search(r"WW\d*", mu):
        return "WW (Grainger)"
    if re.search(r"\bSR\b", mu):
        return "SR"
    if re.search(r"\bOOS\b", mu):
        return "OOS"
    if "LOSS" in mu:
        return "Loss"
    if "DISCONTINUED" in mu:
        return "Discontinued"
    if "DONT PLACE" in mu or "DON'T PLACE" in mu or "DONT" in mu:
        return "Dont place"
    return f"other:{(moid or '').strip()[:24]}"


def label_for(status, reason):
    if status.lower() == "cancelled":
        return "Cancelled"
    if reason == "WW (Grainger)":
        return "Manual"
    return "NotPlaced"


# parse + consolidate non-WB orders
orders = {}
for f in FILES:
    with open(f, newline="", encoding="utf-8", errors="replace") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            m = (r.get("merchant-order-id") or "")
            if WB.search(m.upper()):
                continue
            aid = (r.get("amazon-order-id") or "").strip()
            o = orders.get(aid)
            if not o:
                orders[aid] = o = {"moid": m.strip(), "status": (r.get("order-status") or "").strip(),
                                   "sku": (r.get("sku") or "").strip(), "asin": (r.get("asin") or "").strip(),
                                   "qty": 0, "title": (r.get("product-name") or "").strip(),
                                   "price": 0.0, "city": (r.get("ship-city") or "").strip(),
                                   "state": (r.get("ship-state") or "").strip(),
                                   "zip": (r.get("ship-postal-code") or "").strip(),
                                   "pdate": (r.get("purchase-date") or "")[:10]}
            try: o["qty"] += int(float(r.get("quantity") or 0))
            except: pass
            try: o["price"] += float(r.get("item-price") or 0)
            except: pass

con = sqlite3.connect(DB, timeout=60); con.execute("PRAGMA busy_timeout=60000")
existing = {r[0] for r in con.execute("SELECT amazon_order_id FROM closing_orders")}
if "manual_review" not in {r[1] for r in con.execute("PRAGMA table_info(closing_orders)")}:
    if GO:
        con.execute("ALTER TABLE closing_orders ADD COLUMN manual_review TEXT"); con.commit()

from collections import Counter
by_reason = Counter(); by_label = Counter(); to_insert = []
for aid, o in orders.items():
    if aid in existing:
        continue
    reason = classify(o["moid"]); label = label_for(o["status"], reason)
    by_reason[reason] += 1; by_label[label] += 1
    to_insert.append((aid, o, reason, label))

print(f"account={ACCOUNT}  non-WB orders to ingest: {len(to_insert)}  ({'APPLYING' if GO else 'dry-run'})")
print("  by reason :", dict(by_reason.most_common()))
print("  by label  :", dict(by_label.most_common()))

if GO:
    for aid, o, reason, label in to_insert:
        con.execute("""INSERT OR IGNORE INTO closing_orders
            (amazon_order_id, account, merchant_order_id, order_status, sku, asin, quantity,
             title, item_price, ship_city, ship_state, ship_postal_code, purchase_date,
             order_label, manual_review)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (aid, ACCOUNT, o["moid"], o["status"], o["sku"], o["asin"], o["qty"], o["title"],
             round(o["price"], 2), o["city"], o["state"], o["zip"], o["pdate"], label, reason))
    con.commit()
    print(f"\n✅ ingested {len(to_insert)} non-WB orders")
else:
    print("\nDRY-RUN — nothing written. Re-run with --go to apply.")
con.close()
