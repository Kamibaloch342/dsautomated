#!/usr/bin/env python3
"""repair_multibox_tracking.py — re-extract ALL tracking numbers from archived
Zoro 'has shipped' emails.

WHY: Zoro lists a multi-box shipment's tracking numbers one PER LINE. The old
parser's regex terminated at the first newline, so only the FIRST number was
stored — a 2-box order then uploaded a single tracking id to Amazon (spotted on
113-0036200-6022655). The parser is fixed in gmail_fetch/gmail_archive.py; this
repairs rows already archived, using each row's own stored body (no Gmail refetch).

Only ADDS ids (the stored value must remain a subset) — never removes or reorders.

    python3 scripts/repair_multibox_tracking.py            # dry run
    python3 scripts/repair_multibox_tracking.py --apply    # write
"""
import json, os, re, sqlite3, sys, time

AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(AUTO, "data", "gmail_archive.db")
UNIFIED = os.path.join(AUTO, "data", "automations_unified.db")
AUDIT = os.path.join(AUTO, "data", f"multibox_tracking_repair_{time.strftime('%Y%m%d_%H%M%S')}.json")
APPLY = "--apply" in sys.argv

BLOCK = re.compile(
    r"Tracking Numbers?:\s*(.{0,400}?)"
    r"(?:Items in this|Need to Return|Delivery Method|Recommended Products|Order Details|\Z)",
    re.IGNORECASE | re.DOTALL)
TID = re.compile(r"\b(\d{12,22})\b")


def ids_from_body(body):
    m = BLOCK.search(body or "")
    if not m:
        return []
    out = []
    for t in TID.findall(m.group(1)):
        if t not in out:
            out.append(t)
    return out


def main():
    a = sqlite3.connect(ARCHIVE); a.row_factory = sqlite3.Row
    fixes = []
    for r in a.execute("SELECT id, wb_order_id, tracking_ids, email_timestamp, gmail_account,"
                       " raw_body_snippet FROM tracking_emails WHERE COALESCE(raw_body_snippet,'')!=''"):
        stored = [t.strip() for t in (r["tracking_ids"] or "").split(",") if t.strip()]
        full = ids_from_body(r["raw_body_snippet"])
        # strictly additive: only accept when the stored ids all survive
        if len(full) > len(stored) and all(s in full for s in stored):
            fixes.append({"row_id": r["id"], "wb": r["wb_order_id"], "ts": r["email_timestamp"],
                          "acct": r["gmail_account"], "old": stored, "new": full})

    print(f"rows needing repair: {len(fixes)}")
    extra = sum(len(f["new"]) - len(f["old"]) for f in fixes)
    print(f"tracking ids recovered: {extra}")
    for f in fixes[:5]:
        print(f"  {f['wb']}: {f['old']} → {f['new']}")
    if not APPLY:
        print("\nDRY RUN — re-run with --apply to write.")
        a.close(); return

    json.dump(fixes, open(AUDIT, "w"), indent=1)
    print(f"audit written: {AUDIT}")

    n_arch = 0
    for f in fixes:
        a.execute("UPDATE tracking_emails SET tracking_ids=? WHERE id=?",
                  (",".join(f["new"]), f["row_id"]))
        n_arch += 1
    a.commit(); a.close()
    print(f"gmail_archive.tracking_emails updated: {n_arch}")

    # propagate into the unified archive + closing_orders (additive only)
    u = sqlite3.connect(UNIFIED, timeout=60)
    u.execute("PRAGMA busy_timeout=60000")
    n_uni = n_close = 0
    for f in fixes:
        joined = ",".join(f["new"])
        cur = u.execute(
            "UPDATE archive_tracking SET tracking_ids=? "
            "WHERE wb_order_id=? AND email_timestamp=? AND LENGTH(COALESCE(tracking_ids,''))<?",
            (joined, f["wb"], f["ts"], len(joined)))
        n_uni += cur.rowcount
        cur = u.execute(
            "UPDATE closing_orders SET tracking_ids=? "
            "WHERE wb_order_id=? AND LENGTH(COALESCE(tracking_ids,''))<?",
            (", ".join(f["new"]), f["wb"], len(joined)))
        n_close += cur.rowcount
    u.commit(); u.close()
    print(f"unified archive_tracking updated: {n_uni}")
    print(f"closing_orders updated:           {n_close}")


if __name__ == "__main__":
    main()
