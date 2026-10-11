#!/usr/bin/env python3
"""
test_store_reports.py — the month files are filled, never damaged.

Built on throw-away folders; never touches the owner's "1. AB ECOM" / "2. Hypersale". Pins down:
  * an address is never blanked: Amazon strips the ship-to from orders older than ~30 days, and
    taking a later report's line whole erased it (90 of 105 July "updates" on 11 Oct were that);
  * a status that moved still comes through (Pending → Cancelled);
  * an older report never undoes a newer line;
  * a month is found by its NAME word ("2026-05-August.txt" is August), months are Pacific;
  * a new month copies the owner's header (HOANK's downloads add is-prime; the owner's files don't);
  * CRLF and a BOM survive; the previous file is backed up before its first change.

Run:  python scripts/test_store_reports.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, AUTO)
import refresh_store_reports as R

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   ({detail})" if detail and not ok else ""))


OWN = ["amazon-order-id", "merchant-order-id", "purchase-date", "last-updated-date", "order-status",
       "sku", "asin", "quantity", "ship-city", "ship-state", "order-item-id"]
RPT = OWN + ["is-prime"]                              # HOANK's downloads carry one more column


def line(cols, **v):
    return "\t".join(v.get(c.replace("-", "_"), "") for c in cols)


def main():
    print("\n── month from the file name, months are Pacific ──")
    check("'2026-05-August.txt' is August", R.month_of_name("2026-05-August.txt") == (2026, 8))
    check("'2026-02-Febuary.txt' (sic) is February", R.month_of_name("2026-02-Febuary.txt") == (2026, 2))
    check("'2026-09-September.txt' is September", R.month_of_name("2026-09-September.txt") == (2026, 9))
    check("a stray file is not a month", R.month_of_name("notes.txt") is None)
    check("03:12 UTC on 1 Aug is still July in Seattle", R.pacific_month("2026-08-01T03:12:09+00:00") == (2026, 7))
    check("08:00 UTC on 1 Aug is August", R.pacific_month("2026-08-01T08:00:00+00:00") == (2026, 8))

    tmp = tempfile.mkdtemp()
    R.BACKUPS = os.path.join(tmp, "backups")
    folder = os.path.join(tmp, "Order_Reports"); os.makedirs(folder)
    july = os.path.join(folder, "2026-05-July.txt")          # misnamed on purpose, like the owner's
    with open(july, "wb") as fh:
        fh.write(("\r\n".join([
            "\t".join(OWN),
            line(OWN, amazon_order_id="111-PEND", order_item_id="I-PEND", purchase_date="2026-07-10T10:00:00+00:00",
                 last_updated_date="2026-07-10T11:00:00+00:00", order_status="Pending", sku="S1",
                 asin="B1", quantity="1", ship_city="RENO", ship_state="NV"),
            line(OWN, amazon_order_id="111-SHIP", purchase_date="2026-07-12T10:00:00+00:00",
                 last_updated_date="2026-09-01T00:00:00+00:00", order_status="Shipped", sku="S2",
                 asin="B2", quantity="2", ship_city="AUSTIN", ship_state="TX", order_item_id="I-SHIP"),
            # one order, the same SKU on TWO line items — both are real
            line(OWN, amazon_order_id="111-TWO", purchase_date="2026-07-15T10:00:00+00:00",
                 last_updated_date="2026-07-16T00:00:00+00:00", order_status="Shipped", sku="S9",
                 asin="B9", quantity="1", ship_city="TAMPA", ship_state="FL", order_item_id="I-A"),
            line(OWN, amazon_order_id="111-TWO", purchase_date="2026-07-15T10:00:00+00:00",
                 last_updated_date="2026-07-16T00:00:00+00:00", order_status="Shipped", sku="S9",
                 asin="B9", quantity="3", ship_city="TAMPA", ship_state="FL", order_item_id="I-B"),
        ]) + "\r\n").encode())
    rpt = os.path.join(tmp, "report.txt")
    with open(rpt, "w") as fh:
        fh.write("\r\n".join([
            "\t".join(RPT),
            # the Pending order later cancelled; Amazon has since blanked its address
            line(RPT, amazon_order_id="111-PEND", order_item_id="I-PEND", purchase_date="2026-07-10T10:00:00+00:00",
                 last_updated_date="2026-09-20T00:00:00+00:00", order_status="Cancelled", sku="S1",
                 asin="B1", quantity="0", is_prime="false"),
            # an OLDER copy of the shipped order — must not undo the newer line
            line(RPT, amazon_order_id="111-SHIP", purchase_date="2026-07-12T10:00:00+00:00",
                 last_updated_date="2026-07-12T12:00:00+00:00", order_status="Pending", sku="S2",
                 asin="B2", quantity="2", ship_city="AUSTIN", ship_state="TX", is_prime="false",
                 order_item_id="I-SHIP"),
            # a July order the owner's file never had
            line(RPT, amazon_order_id="111-NEW", order_item_id="I-NEW", purchase_date="2026-07-20T10:00:00+00:00",
                 last_updated_date="2026-07-21T00:00:00+00:00", order_status="Shipped", sku="S3",
                 asin="B3", quantity="1", ship_city="BOISE", ship_state="ID", is_prime="true"),
            # an August order: there is no August file yet
            line(RPT, amazon_order_id="111-AUG", order_item_id="I-AUG", purchase_date="2026-08-05T10:00:00+00:00",
                 last_updated_date="2026-08-06T00:00:00+00:00", order_status="Shipped", sku="S4",
                 asin="B4", quantity="1", ship_city="MIAMI", ship_state="FL", is_prime="true"),
        ]) + "\r\n")

    s = R.merge_orders({"store": "TEST", "reports": folder}, [(rpt, os.path.getmtime(rpt))], verbose=False)

    print("\n── July: merged in place ──")
    h, cols, rows, _ = R.read_tsv(july)
    ix = {c: i for i, c in enumerate(cols)}
    byid = {r[ix["amazon-order-id"]]: r for r in rows}
    p = byid["111-PEND"]
    check("the status moved: Pending → Cancelled", p[ix["order-status"]] == "Cancelled", p[ix["order-status"]])
    check("…and the address was NOT blanked", p[ix["ship-city"]] == "RENO" and p[ix["ship-state"]] == "NV", str(p))
    sh = byid["111-SHIP"]
    check("an older report did not undo the newer line", sh[ix["order-status"]] == "Shipped", sh[ix["order-status"]])
    check("the missing July order was added", "111-NEW" in byid)
    two = [r for r in rows if r[ix["amazon-order-id"]] == "111-TWO"]
    check("the same SKU on two line items of one order: both kept", len(two) == 2 and
          sorted(r[ix["quantity"]] for r in two) == ["1", "3"], str(two))
    check("the misnamed file kept its name", os.path.exists(july) and s.get("2026-07", {}).get("file") == "2026-05-July.txt")
    check("the owner's header is kept — no is-prime column", "is-prime" not in cols and len(cols) == len(OWN))
    check("CRLF line ends survive", b"\r\n" in open(july, "rb").read())
    check("rows stay in purchase-date order",
          [r[ix["purchase-date"]] for r in rows] == sorted(r[ix["purchase-date"]] for r in rows))
    bak = [os.path.join(dp, f) for dp, _d, fs in os.walk(R.BACKUPS) for f in fs]
    check("the previous July file was backed up first", len(bak) == 1 and b"Pending" in open(bak[0], "rb").read())

    print("\n── August: a new month file ──")
    aug = os.path.join(folder, "2026-08-August.txt")
    check("created as 2026-08-August.txt", os.path.exists(aug))
    ha, ca, ra, _ = R.read_tsv(aug)
    check("…with the owner's header, not the report's", ca == OWN, str(ca))
    check("…holding the August order", len(ra) == 1 and ra[0][0] == "111-AUG")

    print("\n── the second run changes nothing ──")
    s2 = R.merge_orders({"store": "TEST", "reports": folder}, [(rpt, os.path.getmtime(rpt))], verbose=False)
    check("no month is rewritten when nothing is new", s2 == {}, str(s2))

    print("\n── BOM ──")
    b = os.path.join(tmp, "bom.txt")
    R.write_tsv(b, "a\tb", [["1", "2"]], bom=True)
    check("a BOM is written back when the file had one", open(b, "rb").read().startswith(b"\xef\xbb\xbf"))
    check("…and read past", R.read_tsv(b)[1] == ["a", "b"])

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL)); sys.exit(1)


if __name__ == "__main__":
    main()
