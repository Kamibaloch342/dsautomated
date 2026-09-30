#!/usr/bin/env python3
"""
pl_rescore.py — score every mined candidate as a PRIVATE-LABEL opportunity.

The hunter's verdicts answer an arbitrage question: "can I win the buy box on someone else's
listing?" HOANK and Hathoro mostly do something different — you create your own ASIN under your
own brand, with your own images, and fulfil from Zoro. Three of the hunter's gates are therefore
meaningless for that business and throw away most of what it finds:

  AMAZON_ON_LISTING   3,656 (52.6%)  Amazon selling the generic version elsewhere does not stop
                                     you creating your own listing — it proves demand.
  UNPROFITABLE_*        196          asks whether the CURRENT Amazon price leaves room. On your
                                     own listing you set the price.
  PACK_MISMATCH / SIZE   ~90         these exist to match somebody else's listing. You define the
                                     pack to be whatever Zoro ships.

What genuinely matters for PL is only: real demand, Zoro actually sells it and has it, and the
price you would have to charge is one the market will bear.

Read-only on hunter.db, so it is safe to run while the hunter is cycling. No Keepa tokens are
spent — the Zoro match is a local lookup against the 2M-row index.

    python3 pl_rescore.py                      # summary + pl_candidates.csv
    python3 pl_rescore.py --min-sold 500       # stricter demand
    python3 pl_rescore.py --target-margin 30   # price for a fatter margin
"""
import argparse
import csv
import importlib.util
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB = os.path.join(HERE, "hunter.db")
FEED = os.path.join(ROOT, "Automations", "TSV Files", "combined_reports.db")
OUT = os.path.join(HERE, "pl_candidates.csv")

# Zoro's small-order fee and the cart value that avoids it (utils/config.py in Automations).
SMALL_ORDER_FEE = 5.99
FREE_SHIP_OVER = 50.0
DEFAULT_REFERRAL = 0.15          # Amazon referral, most industrial/consumable categories

# Zoro brands that belong to nobody, so a HOANK label on them is honest.
GENERIC_BRANDS = {"", "ZORO SELECT", "ZORO"}


def blind_ship_set(path=FEED):
    """The only reliable answer to 'whose product is this really?'

    Zoro leaves zoro_brand_name empty on 293,863 rows, and it is tempting to read that as
    "unbranded". It is not: 175,939 of them are somebody else's goods with the field simply left
    blank — the binders that look generic carry MPN AVE79199, i.e. Avery. The feed splits those
    two cases perfectly on its own flags:

        is_dropship=YES  <->  is_blind_ship=False   175,939   ships from the MANUFACTURER'S
                                                              warehouse, in the manufacturer's
                                                              box. A HOANK listing would arrive
                                                              in an Avery carton.
        is_dropship=NO   <->  is_blind_ship=True    117,924   sits in ZORO'S warehouse and goes
                                                              out in a plain box.

    Only the second set can carry our label. Every product HOANK already private-labels — the
    bleach G7646065, the vinegar G3964829, the drum G5550632, the shipping box G6442406 — is in
    it, which is what makes this the rule rather than a guess.
    """
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = conn.execute("SELECT zoro_no FROM zoro_data WHERE is_blind_ship='True'").fetchall()
    conn.close()
    return {r[0] for r in rows}


def load_hunter():
    """Import hunter.py for its matcher without running it."""
    spec = importlib.util.spec_from_file_location("hunter_mod", os.path.join(HERE, "hunter.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def landed_cost(zoro_price, surcharge):
    """What one unit actually costs you delivered, including Zoro's small-order fee."""
    cost = float(zoro_price or 0) + float(surcharge or 0)
    if cost <= 0:
        return 0.0
    if cost < FREE_SHIP_OVER:
        cost += SMALL_ORDER_FEE
    return round(cost, 2)


def price_for_margin(cost, referral, margin):
    """Retail price that leaves `margin` of revenue after Amazon's referral fee.
       price*(1-referral) - cost = margin*price  ->  price = cost / (1 - referral - margin)"""
    denom = 1.0 - referral - margin
    if denom <= 0.05 or cost <= 0:
        return None
    return round(cost / denom, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-sold", type=int, default=300, help="monthly units the ASIN must show (default 300)")
    ap.add_argument("--target-margin", type=float, default=25.0, help="margin %% to price for (default 25)")
    ap.add_argument("--max-over-market", type=float, default=1.6,
                    help="flag when the needed price exceeds the known market price by more than this (default 1.6x)")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    target = a.target_margin / 100.0

    mod = load_hunter()
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("SELECT * FROM candidates").fetchall()
    blind = blind_ship_set()
    counts = {"total": len(rows), "already_listed": 0, "low_demand": 0, "no_zoro": 0,
              "out_of_stock": 0, "not_blind": 0, "branded": 0, "no_price": 0, "over_market": 0,
              "PL": 0}
    out = []

    for r in rows:
        if r["verdict"] == "ALREADY_LISTED":
            counts["already_listed"] += 1
            continue
        sold = r["monthly_sold"] or 0
        if sold < a.min_sold:
            counts["low_demand"] += 1
            continue

        # Reuse the match it already has; otherwise run the local lookup it never got to.
        z, how = None, r["match_method"] or ""
        if (r["zoro_no"] or "").strip():
            z = conn.execute("SELECT * FROM zoro_index WHERE zoro_no=?", (r["zoro_no"],)).fetchone()
            how = how or "stored"
        if z is None:
            codes = [c for c in (r["codes"] or "").split(",") if c]
            z, how = mod.zoro_lookup(conn, codes, r["mpn"] or "", r["brand"] or "",
                                     r["title"] or "", r["amz_size"] or "")
        if z is None:
            counts["no_zoro"] += 1
            continue
        if (z["status"] or "") in ("Out of Stock", "Backordered"):
            counts["out_of_stock"] += 1
            continue

        # Ships in somebody else's carton -> cannot carry our label, however generic it looks.
        if z["zoro_no"] not in blind:
            counts["not_blind"] += 1
            continue

        # Only goods nobody owns a brand on. Every product HOANK actually private-labels —
        # the bleach G7646065, the vinegar G3964829, the water barrel G5550632, the shipping
        # box G6442406 — carries NO brand in Zoro's feed, and 293,863 rows are like that.
        # Relabelling a Klein Tools stripper or a 3M tape as HOANK is a trademark complaint and
        # a removed listing, so those are arbitrage-only however well they sell.
        zbrand = (z["brand"] or "").strip().upper()
        ztitle = z["title"] or ""
        abrand = (r["brand"] or "").strip()
        branded = bool(zbrand and zbrand not in GENERIC_BRANDS)
        # Zoro leaves the brand field empty on plenty of rows whose TITLE still names the owner
        # ("Avery(R) Durable View 3 Ring Binder", "Pendaflex Expanding File Pockets"). A (R)/(TM)
        # in the title, or the Amazon brand appearing in it, means Zoro ships that company's
        # product — you would be relabelling someone else's goods.
        if not branded:
            if "\u00ae" in ztitle or "\u2122" in ztitle:
                branded = True
            elif abrand and len(abrand) >= 3 and abrand.lower() in ztitle.lower():
                branded = True
        if branded:
            counts["branded"] += 1
            continue

        cost = landed_cost(z["price"], z["surcharge"])
        if cost <= 0:
            counts["no_price"] += 1
            continue

        referral = float(r["referral_pct"] or 0) / 100.0 or DEFAULT_REFERRAL
        need = price_for_margin(cost, referral, target)
        if need is None:
            counts["no_price"] += 1
            continue

        market = float(r["keepa_new_price"] or 0) or None
        over = round(need / market, 2) if market else None
        if over and over > a.max_over_market:
            counts["over_market"] += 1
            continue

        counts["PL"] += 1
        out.append({
            "asin": r["asin"], "monthly_sold": sold, "brand": r["brand"] or "",
            "title": (r["title"] or "")[:90],
            "zoro_no": z["zoro_no"], "zoro_title": (z["title"] or "")[:70],
            "zoro_brand": z["brand"] or "", "mpn": z["mpn"] or "",
            "zoro_pack": mod.parse_pack(z["title"]),
            "zoro_price": round(float(z["price"] or 0), 2),
            "landed_cost": cost, "price_for_margin": need,
            "market_price": market or "", "price_vs_market": over or "",
            "match": how, "hunter_verdict": r["verdict"] or "",
        })

    conn.close()
    out.sort(key=lambda x: -x["monthly_sold"])
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys()) if out else ["asin"])
        w.writeheader()
        w.writerows(out)

    print(f"\n  candidates examined              {counts['total']:6d}")
    print(f"  already on HOANK/Hathoro         {counts['already_listed']:6d}")
    print(f"  under {a.min_sold}/month                   {counts['low_demand']:6d}")
    print(f"  no Zoro match                    {counts['no_zoro']:6d}")
    print(f"  Zoro out of stock                {counts['out_of_stock']:6d}")
    print(f"  ships in the maker's own carton  {counts['not_blind']:6d}")
    print(f"  brand-owned (arbitrage only)     {counts['branded']:6d}")
    print(f"  no usable Zoro price             {counts['no_price']:6d}")
    print(f"  needs > {a.max_over_market}x the market price     {counts['over_market']:6d}")
    print(f"  ── PRIVATE-LABEL CANDIDATES      {counts['PL']:6d}  → {os.path.relpath(a.out, HERE)}")
    if out:
        print(f"\n  by the hunter's old verdict:")
        by = {}
        for x in out:
            by[x["hunter_verdict"]] = by.get(x["hunter_verdict"], 0) + 1
        for k, n in sorted(by.items(), key=lambda kv: -kv[1]):
            print(f"     {k or '(unevaluated)':22s} {n:5d}")
        print(f"\n  top 10 by demand:")
        for x in out[:10]:
            print(f"     {x['asin']} {x['monthly_sold']:>6}/mo  {x['zoro_no']:>11s} "
                  f"cost ${x['landed_cost']:>7.2f} → list ${x['price_for_margin']:>7.2f}  {x['title'][:46]}")


if __name__ == "__main__":
    main()
