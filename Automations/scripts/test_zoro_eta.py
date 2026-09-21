#!/usr/bin/env python3
"""
test_zoro_eta.py — Zoro's own delivery estimate, taken from the shipping email.

21 Sep 2026: FedEx put its public tracking behind Akamai bot defence, so it answers an
automated session with a decoy "we can't find that tracking number". Amazon does not give a
merchant-fulfilled seller the carrier's scans either — not on the order page, not in the
all-orders report. What is left, already archived and never used, is the line Zoro puts in
every shipping confirmation: "Estimated Delivery Monday, September 21". 73% of the emails
carry it and they cover 96% of shipped orders.

It is an expectation, not proof of delivery, so it belongs in zoro_deliver_by and must never
be written to delivery_status — the whole point of the 20 Sep FedEx work was that a status
nobody established is not written at all.

Run:  python scripts/test_zoro_eta.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail and not cond else ""))


def src(rel):
    return open(os.path.join(AUTO, rel), encoding="utf-8").read()


REAL = ("96 Zoro Shipping confirmation inside. Most Orders Delivered in 1 - 2 Business Days.* "
        "My Account Deals Hand Tools Your Order Is on Its Way! Estimated Delivery Monday, "
        "September 21 Ordered Fri, Sep 18 Shipped Mon, Sep 21 Delivery Mon, Sep 21 View Order "
        "Status Order Number: WB2585999811 Delivery Method: Fed Ex Ground Tracking Number: 539601215406")


def main():
    from engine.closing_signals import extract_zoro_eta as eta

    print("\n── the line Zoro actually sends ──")
    check("a real shipping email yields the estimate", eta(REAL, "2026-09-21 15:02:11") == "2026-09-21",
          eta(REAL, "2026-09-21 15:02:11"))
    check("the weekday is optional", eta("Estimated Delivery September 24", "2026-09-21") == "2026-09-24")
    check("an abbreviated month works", eta("Estimated Delivery Thu, Sep. 24", "2026-09-21") == "2026-09-24")
    check("case does not matter", eta("ESTIMATED DELIVERY MONDAY, SEPTEMBER 21", "2026-09-21") == "2026-09-21")

    print("\n── the year, which the email never states ──")
    check("the year comes from the email, not from today",
          eta("Estimated Delivery Tuesday, June 3", "2024-05-30 09:00:00") == "2024-06-03")
    check("a December email promising January rolls to the next year",
          eta("Estimated Delivery Thursday, January 2", "2025-12-28 18:00:00") == "2026-01-02")
    check("a January email about January stays in the same year",
          eta("Estimated Delivery Friday, January 9", "2026-01-05 08:00:00") == "2026-01-09")
    check("a one-month-back estimate is not treated as next year",
          eta("Estimated Delivery Monday, August 31", "2026-09-01 08:00:00") == "2026-08-31")

    print("\n── nothing is invented ──")
    check("an email without the line yields nothing",
          eta("Your order has shipped. Tracking Number: 539601215406", "2026-09-21") == "")
    check("empty input yields nothing", eta("", "2026-09-21") == "" and eta(None, "") == "")
    check("a date that does not exist yields nothing",
          eta("Estimated Delivery Monday, February 30", "2026-02-20") == "")
    check("a word that is not a month yields nothing",
          eta("Estimated Delivery Someday, Smarch 4", "2026-09-21") == "")
    check("a missing timestamp still parses, using this year",
          len(eta("Estimated Delivery September 24", "")) == 10)

    print("\n── where closing puts it ──")
    cl = src("closing.py")
    check("closing stores it in zoro_deliver_by", 'enriched["zoro_deliver_by"] = eta' in cl)
    check("...and never in delivery_status",
          'enriched["delivery_status"] = eta' not in cl and "delivery_status\"] = eta" not in cl)
    check("it is read from the archived Zoro shipping email",
          '_CS.extract_zoro_eta(row["raw_body_snippet"], row["email_timestamp"])' in cl)

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
