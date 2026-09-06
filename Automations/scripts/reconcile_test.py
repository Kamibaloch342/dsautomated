"""
Reconciliation TEST: does unified.db (closing_orders) exactly account for an
account's order report(s)?  Read-only. Treats CANCELLED and PENDING (no tracking
received) as EXPECTED, not failures. Fails only on genuine gaps.

Usage:
  ../.venv/bin/python scripts/reconcile_test.py <ACCOUNT> <report1.txt> [report2.txt ...]
Example:
  ../.venv/bin/python scripts/reconcile_test.py Hathoro \
     "/Users/.../Hathoro May 2026.txt" "/Users/.../Hathoro June 2026.txt"
"""
import csv, sqlite3, re, sys, os
csv.field_size_limit(10**7)
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "data", "automations_unified.db")
WB = re.compile(r"WB\d+")

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else "Hathoro"
FILES = sys.argv[2:]
if not FILES:
    print("need report file path(s)"); sys.exit(1)


def empty(v):
    return v is None or str(v).strip().lower() in ("", "none")


def yn(ok):
    return "PASS ✅" if ok else "FAIL ❌"


# ── parse reports → unique WB orders {aid: {status, month}} ──
orders, months = {}, set()
for f in FILES:
    with open(f, newline="", encoding="utf-8", errors="replace") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            moid = (r.get("merchant-order-id") or "")
            if not WB.search(moid.upper()):
                continue
            aid = (r.get("amazon-order-id") or "").strip()
            st = (r.get("order-status") or "").strip()
            mon = (r.get("purchase-date") or "")[:7]
            o = orders.get(aid)
            if not o:
                orders[aid] = {"status": st, "month": mon}
            elif o["status"].lower() == "cancelled" and st.lower() != "cancelled":
                o["status"] = st
report_aids = set(orders)
cancelled = {a for a in report_aids if orders[a]["status"].lower() == "cancelled"}
active = report_aids - cancelled
# report month range (for the reverse-diff: db-only rows OUTSIDE this range are fine)
rep_months = {orders[a]["month"] for a in report_aids if orders[a]["month"]}

# ── load DB ──
c = sqlite3.connect(DB); c.execute("PRAGMA query_only=ON")
db = {r[0]: r for r in c.execute(
    "SELECT amazon_order_id, order_label, tracking_ids, tracking_id_uploaded, "
    "deliver_by_date, delivery_status, earnings, zoro_order_total, substr(purchase_date,1,7) "
    "FROM closing_orders WHERE account=?", (ACCOUNT,))}
db_aids = set(db)

# ── checks ──
missing_active = active - db_aids
missing_cancelled = cancelled - db_aids
present_active = [a for a in active if a in db]
db_only = db_aids - report_aids
db_only_inrange = {a for a in db_only if db[a][8] in rep_months}   # same months but not in report = suspicious
db_only_other = db_only - db_only_inrange

# present-active field coverage / pending
no_track = [a for a in present_active if empty(db[a][2])]                 # no tracking_ids = pending
no_track_fixable = [a for a in no_track if not empty(db[a][3])]           # but have scraped tracking_id_uploaded
truly_pending = [a for a in no_track if empty(db[a][3])]
miss_deliver = [a for a in present_active if empty(db[a][4])]
labels = {}
for a in present_active:
    labels[db[a][1]] = labels.get(db[a][1], 0) + 1

print("=" * 72)
print(f"RECONCILIATION TEST — {ACCOUNT}   (months in report: {sorted(rep_months)})")
print("=" * 72)
print(f"Report WB orders        : {len(report_aids)}  (active {len(active)}, cancelled {len(cancelled)})")
print(f"unified.db rows ({ACCOUNT}) : {len(db_aids)}")
print("-" * 72)
t1 = len(missing_active) == 0
print(f"[1] Every ACTIVE report order is in unified.db .......... {yn(t1)}")
if not t1:
    sh = sum(1 for a in missing_active if orders[a]['status'].lower() != 'pending')
    pe = len(missing_active) - sh
    print(f"      → {len(missing_active)} missing  (Shipped {sh}, Pending {pe})  ← run closing on these reports")
# a report-cancelled order is OK if it's absent, labeled Cancelled, or actually
# delivered (delivered-despite-cancel → legitimately Completed/Refunded, not a miss)
bad_cancel = [a for a in (cancelled & db_aids)
              if db[a][1] != 'Cancelled' and db[a][5] not in ('Delivered', 'AllDelivered')]
t2 = len(bad_cancel) == 0
print(f"[2] Cancelled handled (Cancelled / absent / delivered) . {yn(t2)}   "
      f"({len(cancelled)} cancelled, {len(bad_cancel)} mislabeled)")
t3 = len(db_only_inrange) == 0
print(f"[3] No unexplained db-only rows in report months ....... {yn(t3)}")
print(f"      → db-only total {len(db_only)}: in-range {len(db_only_inrange)} (bad), other-month {len(db_only_other)} (ok)")
cov = 100 * len(present_active) / max(len(active), 1)
print(f"[4] Active coverage ..................................... {cov:.1f}%  ({len(present_active)}/{len(active)})")
print(f"[5] deliver_by_date present (active) ................... {100*(len(present_active)-len(miss_deliver))/max(len(present_active),1):.1f}%  ({len(miss_deliver)} missing → SC sweep)")
print("-" * 72)
print(f"PENDING (no tracking received, EXPECTED): {len(no_track)}")
print(f"      → fixable now (have scraped tracking_id_uploaded → closing re-run merges): {len(no_track_fixable)}")
print(f"      → truly unshipped (no tracking anywhere): {len(truly_pending)}")
print(f"label spread (present active): {dict(sorted(labels.items(), key=lambda x:-x[1]))}")
print("=" * 72)
allpass = t1 and t2 and t3
print("VERDICT:", "✅ unified.db fully accounts for the report (only expected pending/cancelled remain)."
      if allpass else "❌ NOT yet exact — see FAILs above (run closing on the new reports, then re-test).")
