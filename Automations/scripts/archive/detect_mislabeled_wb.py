#!/usr/bin/env python3
"""detect_mislabeled_wb.py — find Amazon orders wearing a WB label that belongs
to a DIFFERENT order.

Background (2026-08-19/20 "-D flop"): update_amazon_seller_id could stamp its
label onto whichever order page happened to be open, so duplicate markers like
"WB8143932412 - D" landed on unrelated orders. A foreign "-D" label makes the
placer skip that order as already-handled — for a never-placed order that means
it silently never ships. The writer is fixed (page-identity guard in
platforms/amazon.py); this finds the damage already done.

Reads the LAST INGESTED labels (closing_orders.merchant_order_id), so re-run it
after each closing report ingest — labels written after the last ingest are not
visible yet.

    python3 scripts/detect_mislabeled_wb.py
"""
import os, re, sqlite3

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "data", "automations_unified.db")
WB = re.compile(r"\b(W[BW]\d{6,})\b")

c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); c.row_factory = sqlite3.Row

# who OWNS each WB — PLACEMENT-TIME records ONLY. closing_orders.wb_order_id is
# NOT usable here: closing ingests the Amazon label, so a wrong-page stamp gets
# laundered into that column and the corruption looks self-consistent (this
# exact blindness let the first version miss 113-1784888-0141062, whose stamped
# WB "belonged" to it per closing_orders while the placer log proved it was
# placed for 111-8340813-1809044 three days before this order even existed).
owner = {}
for r in c.execute("SELECT amazon_order_id, wb_order_id FROM placer_claims "
                   "WHERE status='CONFIRMED' AND COALESCE(wb_order_id,'')!=''"):
    for wb in WB.findall(r["wb_order_id"]):
        owner.setdefault(wb, r["amazon_order_id"])
for r in c.execute("SELECT amazon_order_id, wb_order_id FROM order_placer_orders "
                   "WHERE COALESCE(wb_order_id,'')!=''"):
    for wb in WB.findall(r["wb_order_id"]):
        owner.setdefault(wb, r["amazon_order_id"])
LOG = os.path.join(os.path.dirname(DB), "rajar_logs", "order_log.txt")
if os.path.exists(LOG):
    line_rx = re.compile(r"WB:(W[BW]\d+).*?Amazon:(\d{3}-\d{7}-\d{7})")
    with open(LOG, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            m = line_rx.search(line)
            if m:
                owner.setdefault(m.group(1), m.group(2))

bad = []
for r in c.execute("SELECT amazon_order_id, merchant_order_id, wb_order_id, account, "
                   "order_label, order_status FROM closing_orders "
                   "WHERE COALESCE(merchant_order_id,'')!=''"):
    # trust only placement records for what this order rightfully owns
    own = {wb for wb, o in owner.items() if o == r["amazon_order_id"]}
    for wb in WB.findall(r["merchant_order_id"]):
        o = owner.get(wb)
        if o and o != r["amazon_order_id"] and wb not in own:
            # true WB from PLACEMENT records only — r["wb_order_id"] is the
            # label-derived (tainted) column and must not be echoed as truth
            true_wb = ", ".join(sorted(own)) if own else "NEVER PLACED — CLEAR LABEL & PLACE"
            bad.append((r["amazon_order_id"], r["merchant_order_id"], wb, o,
                        true_wb, r["account"], r["order_status"] or ""))
            break
c.close()

if not bad:
    print("no mislabeled orders in the last ingested report ✅")
else:
    print(f"{len(bad)} order(s) wearing a FOREIGN WB label:")
    print(f"{'order':22}{'label on Amazon':26}{'label belongs to':22}{'set label to (placement truth)':44}{'status'}")
    for amz, label, wb, own_amz, true_wb, acct, st in bad:
        print(f"{amz:22}{label:26}{own_amz:22}{true_wb:44}[{acct}] {st}")
    print("\nfix: Seller Central → each order → Edit seller order ID → set 'true WB'"
          "\n     (orders with NO true WB were never placed: CLEAR the label so the placer sees them)")
