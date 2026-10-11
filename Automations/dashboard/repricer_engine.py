#!/usr/bin/env python3
"""
repricer_engine.py — Core Repricing & Competitor Opportunity Analysis Engine

Calculates optimal repricing opportunities for Hathoro and HOANK:
1. 🚀 RAISE PRICE OPPORTUNITY (Profit Maximization):
   When we are Lowest 1 (or Buy Box winner) and Lowest 2 is higher (e.g., We: $50.94, 2nd: $60.12).
   Target Price = Lowest 2 Price - $0.01 (e.g. $59.99).
   Maximizes profit while staying the lowest seller!

2. 🎯 DROP PRICE OPPORTUNITY (< 10% Gap to Win Buy Box):
   When we are 2nd or lost Buy Box, but gap to Lowest 1 is <= 10% and lowering price
   to (Lowest 1 - $0.01) is still profitable above Zoro cost without tax.
   Target Price = Lowest 1 Price - $0.01.

3. 🚨 PL HIJACKED ALERT:
   When Private Label SKU (Hathoro: 'hath', HOANK: 'hoa', 'ho-', 'ho_', 'hon', 'hoank')
   has unauthorized sellers sitting on it (Total Sellers > 1).

4. 🛡️ PL PROTECTED:
   Private Label listing with only 1 seller (us).

5. 🛑 PRICE FLOOR / COMPETITOR BELOW COST:
   When Lowest 1 is below Zoro cost or negative margin.
"""

from __future__ import annotations

import os
import sys
import json
import sqlite3
import re
from typing import Any, Dict, List, Optional
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "automations_unified.db")
HATHORO_CACHE = os.path.join(BASE_DIR, "data", "hathoro_august_offers_cache.json")
HOANK_CACHE = os.path.join(BASE_DIR, "data", "hoank_august_offers_cache.json")


def parse_price(val: Any) -> Optional[float]:
    """Parse numeric price from string or float."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val) if val > 0 else None
    s = str(val).replace("$", "").replace(",", "").strip()
    m = re.search(r"(\d+\.?\d*)", s)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


# Authorized sister/partner accounts allowed on our Private Label listings
AUTHORIZED_PL_SELLERS = ("hathoro", "hoank", "wrenchly", "qurtdis", "hypersale", "abecomllc26")


def is_authorized_pl_seller(seller_name: Optional[str]) -> bool:
    """Check if seller is authorized (e.g. Hathoro, HOANK, or Wrenchly LLC)."""
    if not seller_name:
        return False
    s = seller_name.strip().lower()
    return any(auth in s for auth in AUTHORIZED_PL_SELLERS)


def get_unauthorized_sellers(all_offers: List[Dict[str, Any]]) -> List[str]:
    """Find any unauthorized sellers sitting on a Private Label listing."""
    unauth = []
    seen = set()
    for off in all_offers:
        s_name = (off.get("seller_name") or "").strip()
        if s_name and not is_authorized_pl_seller(s_name):
            if s_name not in seen:
                unauth.append(s_name)
                seen.add(s_name)
    return unauth


def is_pl_sku(sku: Optional[str], account: str = "Hathoro") -> bool:
    """Check if SKU is Private Label based on account brand conventions."""
    if not sku:
        return False
    s = sku.strip().lower()
    acct = account.upper() if account else "HATHORO"

    if "HATHORO" in acct:
        return "hath" in s or s.startswith("ht_") or s.startswith("ht-") or s.startswith("ht ") or "ht_" in s or "ht-" in s

    else:  # HOANK
        return (
            s.startswith("hoa")
            or s.startswith("ho-")
            or s.startswith("ho_")
            or s.startswith("ho ")
            or s.startswith("hon")
            or "hoank" in s
        )



def load_account_cache(account: str = "Hathoro") -> Dict[str, Any]:
    """Load cached scraper results for the requested account."""
    acct = account.upper() if account else "HATHORO"
    c_path = HATHORO_CACHE if "HATHORO" in acct else HOANK_CACHE
    if os.path.exists(c_path):
        try:
            with open(c_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


# The repricer used to check only ASINs with orders in AUGUST 2026 (purchase_date LIKE
# '2026-08%'), so its list froze at 443 Hathoro / 245 HOANK ASINs and every listing that started
# selling after August — 129 Hathoro and 65 HOANK ASINs with orders in the last 30 days on
# 11 Oct 2026 — was never checked. It now takes every ASIN with an order in the last
# REPRICER_WINDOW_DAYS, so a new listing joins the loop as soon as it sells and one that stopped
# selling drops out. (Key names such as total_august_orders are kept: the launcher and the legacy
# dashboard page read them; they now mean "orders in the window".)
REPRICER_WINDOW_DAYS = 30


def _window_since(days=None):
    from datetime import date, timedelta
    return (date.today() - timedelta(days=int(days or REPRICER_WINDOW_DAYS))).isoformat()


def get_account_orders_df(account: str = "Hathoro", db_path: str = DB_PATH) -> pd.DataFrame:
    """Orders in the last REPRICER_WINDOW_DAYS, prices, and latest Zoro costs without tax for the account."""
    acct = "Hathoro" if "HATHORO" in (account or "").upper() else "HOANK"
    conn = sqlite3.connect(db_path)
    query = f"""
    WITH august_orders AS (
        SELECT 
            asin,
            sku,
            title,
            quantity,
            item_price,
            wb_order_id,
            zoro_subtotal,
            zoro_shipping,
            zoro_tax,
            zoro_order_total,
            purchase_date
        FROM closing_orders
        WHERE account = '{acct}' 
          AND purchase_date >= ?
    ),
    asin_metrics AS (
        SELECT 
            asin,
            COUNT(*) as total_august_orders,
            SUM(COALESCE(quantity, 1)) as total_august_units,
            MAX(purchase_date) as max_purchase_date
        FROM august_orders
        GROUP BY asin
    ),
    latest_orders AS (
        SELECT 
            ao.asin,
            ao.sku,
            ao.title,
            ao.item_price as latest_sale_price,
            ao.purchase_date,
            ROW_NUMBER() OVER (PARTITION BY ao.asin ORDER BY ao.purchase_date DESC) as rn
        FROM august_orders ao
    ),
    latest_placed_orders AS (
        SELECT 
            co.asin,
            co.wb_order_id,
            co.zoro_account_name,
            co.zoro_subtotal,
            co.zoro_shipping,
            co.zoro_tax,
            co.zoro_order_total,
            CASE 
                WHEN co.zoro_subtotal > 0 THEN ROUND(co.zoro_subtotal + COALESCE(co.zoro_shipping, 0.0), 2)
                WHEN co.zoro_order_total > 0 THEN ROUND(co.zoro_order_total - COALESCE(co.zoro_tax, 0.0), 2)
                ELSE NULL 
            END as latest_zoro_cost_notax,
            co.purchase_date,
            ROW_NUMBER() OVER (
                PARTITION BY co.asin 
                ORDER BY 
                    CASE WHEN (co.zoro_tax IS NULL OR co.zoro_tax = 0) THEN 1 ELSE 2 END,
                    co.purchase_date DESC
            ) as rn_placed
        FROM closing_orders co
        WHERE co.account = '{acct}'
          AND (co.wb_order_id LIKE 'WB%' OR co.zoro_order_total > 0)
    )
    SELECT 
        m.asin,
        lo.sku,
        lo.title,
        m.total_august_orders,
        m.total_august_units,
        lo.latest_sale_price,
        lpo.latest_zoro_cost_notax,
        lpo.wb_order_id,
        lpo.zoro_account_name
    FROM asin_metrics m
    JOIN latest_orders lo ON m.asin = lo.asin AND lo.rn = 1
    LEFT JOIN latest_placed_orders lpo ON m.asin = lpo.asin AND lpo.rn_placed = 1
    ORDER BY m.total_august_orders DESC;
    """
    df = pd.read_sql_query(query, conn, params=[_window_since()])
    conn.close()
    return df


def analyze_repricing_for_account(account: str = "Hathoro", db_path: str = DB_PATH) -> Dict[str, Any]:
    """Run full repricing opportunity analysis across all ASINs of the given account."""
    acct = "Hathoro" if "HATHORO" in (account or "").upper() else "HOANK"
    df = get_account_orders_df(account=acct, db_path=db_path)
    cache = load_account_cache(account=acct)

    items = []
    total_orders = int(df["total_august_orders"].sum()) if not df.empty else 0
    total_units = int(df["total_august_units"].sum()) if not df.empty else 0
    total_asins = len(df)

    bb_wins_count = 0
    total_scraped_count = 0
    raise_price_count = 0
    raise_price_total_potential_monthly = 0.0
    drop_price_count = 0
    pl_total_count = 0
    pl_hijacked_count = 0

    for _, r in df.iterrows():
        asin = str(r["asin"])
        sku = str(r["sku"] or "")
        title = str(r["title"] or "")
        aug_orders = int(r["total_august_orders"]) if pd.notnull(r["total_august_orders"]) else 0
        aug_units = int(r["total_august_units"]) if pd.notnull(r["total_august_units"]) else 0
        sale_price = float(r["latest_sale_price"]) if pd.notnull(r["latest_sale_price"]) else None
        zoro_cost = float(r["latest_zoro_cost_notax"]) if pd.notnull(r["latest_zoro_cost_notax"]) else None

        gross_profit = round(sale_price - zoro_cost, 2) if (sale_price and zoro_cost) else None
        profit_margin_pct = round((gross_profit / sale_price * 100), 1) if (gross_profit and sale_price) else None

        is_pl = is_pl_sku(sku, account=acct)
        if is_pl:
            pl_total_count += 1

        c_data = cache.get(asin, {})
        scraped = bool(c_data.get("scraped_successfully"))
        total_sellers = int(c_data.get("total_sellers_count", 0)) if scraped else 1
        bb_seller = c_data.get("buy_box_seller", "")
        bb_price = parse_price(c_data.get("buy_box_price"))
        is_bb = c_data.get("is_hathoro_in_buy_box") if acct == "Hathoro" else c_data.get("is_hoank_in_buy_box")

        if scraped:
            total_scraped_count += 1
            if is_bb:
                bb_wins_count += 1

        # Extract Top 3 lowest offers
        all_offers = c_data.get("all_offers", [])
        low1_seller = c_data.get("lowest_1_seller", c_data.get("lowest_price_seller", ""))
        low1_price = parse_price(c_data.get("lowest_1_price", c_data.get("lowest_price")))

        low2_seller = c_data.get("lowest_2_seller", "")
        low2_price = parse_price(c_data.get("lowest_2_price"))

        low3_seller = c_data.get("lowest_3_seller", "")
        low3_price = parse_price(c_data.get("lowest_3_price"))

        if all_offers and (not low1_price or not low2_price):
            sorted_offers = []
            for off in all_offers:
                p = parse_price(off.get("total_price")) or parse_price(off.get("item_price"))
                if p and p > 0:
                    sorted_offers.append((p, off))
            sorted_offers.sort(key=lambda x: x[0])
            if len(sorted_offers) > 0 and not low1_price:
                low1_seller = sorted_offers[0][1].get("seller_name", "")
                low1_price = sorted_offers[0][0]
            if len(sorted_offers) > 1 and not low2_price:
                low2_seller = sorted_offers[1][1].get("seller_name", "")
                low2_price = sorted_offers[1][0]
            if len(sorted_offers) > 2 and not low3_price:
                low3_seller = sorted_offers[2][1].get("seller_name", "")
                low3_price = sorted_offers[2][0]

        # Determine Our Current Position / Price
        effective_our_price = sale_price or low1_price or 0.0

        # Check PL Hijacked Condition (Wrenchly LLC is allowed as an exception!)
        unauth_sellers = get_unauthorized_sellers(all_offers) if all_offers else []
        if not unauth_sellers and not all_offers and total_sellers > 1:
            if bb_seller and not is_authorized_pl_seller(bb_seller):
                unauth_sellers = [bb_seller]

        is_hijacked = is_pl and len(unauth_sellers) > 0
        if is_hijacked:
            pl_hijacked_count += 1

        # -------------------------------------------------------------
        # REPRICING INTELLIGENCE & RECOMMENDATION ENGINE
        # -------------------------------------------------------------
        strategy = "OPTIMAL"
        strategy_badge = "✅ OPTIMAL"
        recommended_price = effective_our_price
        profit_delta_per_unit = 0.0
        potential_monthly_gain = 0.0
        reason = "Currently optimized at competitive price."

        # Scenario 1: PL Hijacked Listing (unauthorized sellers other than Wrenchly/Hathoro/HOANK)
        if is_hijacked:
            strategy = "PL_HIJACKED"
            unauth_str = ", ".join(unauth_sellers[:2])
            strategy_badge = f"🚨 PL HIJACKED ({len(unauth_sellers)} Unauth)"
            recommended_price = effective_our_price
            reason = f"Unauthorized seller(s) [{unauth_str}] detected on Private Label listing ({sku}). Wrenchly LLC is allowed. Take action to remove unauthorized sellers!"

        # Scenario 2: PL Protected Listing (Exclusive or shared with Wrenchly LLC)
        elif is_pl:
            strategy = "PL_PROTECTED"
            has_wrenchly = any("wrenchly" in (o.get("seller_name") or "").lower() for o in all_offers)
            strategy_badge = "🛡️ PL PROTECTED (Shared: Wrenchly)" if has_wrenchly else "🛡️ PL PROTECTED"
            recommended_price = effective_our_price
            reason = "Private Label listing with authorized sellers only (Wrenchly LLC is allowed)."


        # Scenario 3: Raise Price Opportunity (We are Lowest 1 and 2nd seller is significantly higher)
        # e.g., We are at $50.94, Lowest 2 is at $60.12 -> Target: $59.99
        elif (
            low2_price
            and low2_price > effective_our_price
            and (is_bb is True or acct.lower() in (low1_seller or "").lower())
        ):
            target_price = round(low2_price - 0.01, 2)
            if target_price > effective_our_price:
                strategy = "RAISE_PRICE"
                price_gap = round(target_price - effective_our_price, 2)
                pct_increase = round((price_gap / effective_our_price * 100), 1)
                strategy_badge = f"🚀 RAISE PRICE (+${price_gap:.2f})"
                recommended_price = target_price
                profit_delta_per_unit = price_gap
                potential_monthly_gain = round(price_gap * aug_units, 2)
                reason = (
                    f"We are Lowest 1 at ${effective_our_price:.2f}. "
                    f"2nd seller is at ${low2_price:.2f} ({low2_seller}). "
                    f"Can safely raise price to ${target_price:.2f} (+{pct_increase}%) while remaining the lowest seller!"
                )
                raise_price_count += 1
                raise_price_total_potential_monthly += potential_monthly_gain

        # Scenario 4: Drop Price Opportunity (< 10% Gap to Lowest 1 to Reclaim Buy Box)
        # e.g., We are 2nd/Lost BB, but gap to Lowest 1 is <= 10% and target is still profitable vs Zoro cost
        elif (
            low1_price
            and effective_our_price > low1_price
            and ((effective_our_price - low1_price) / effective_our_price) <= 0.10
        ):
            gap_pct = round(((effective_our_price - low1_price) / effective_our_price * 100), 1)
            target_undercut = round(low1_price - 0.01, 2)

            # Check profitability against Zoro cost without tax
            if zoro_cost and target_undercut > (zoro_cost * 1.05):  # At least 5% gross margin
                strategy = "DROP_PRICE_10PCT"
                strategy_badge = f"🎯 DROP PRICE ({gap_pct}% Gap)"
                recommended_price = target_undercut
                new_margin = round(target_undercut - zoro_cost, 2)
                reason = (
                    f"Lost Buy Box to {low1_seller} at ${low1_price:.2f} (Gap: {gap_pct}%). "
                    f"Reduce price to ${target_undercut:.2f} to reclaim Buy Box with ${new_margin:.2f} gross profit!"
                )
                drop_price_count += 1
            else:
                strategy = "PRICE_FLOOR"
                strategy_badge = "⚠️ PRICE FLOOR"
                recommended_price = effective_our_price
                reason = (
                    f"Competitor {low1_seller} is priced at ${low1_price:.2f}, near or below our Zoro cost (${zoro_cost}). "
                    f"Do not lower price further."
                )

        # Scenario 5: Large Gap (> 10% Gap Lost Buy Box)
        elif low1_price and effective_our_price > low1_price:
            gap_pct = round(((effective_our_price - low1_price) / effective_our_price * 100), 1)
            strategy = "LARGE_GAP"
            strategy_badge = f"📉 LARGE GAP ({gap_pct}%)"
            recommended_price = effective_our_price
            reason = f"Competitor {low1_seller} is {gap_pct}% below our price. Monitor stock or supplier costs."

        # Scenario 6: Sole Seller / No Other Offers
        elif total_sellers <= 1:
            strategy = "SOLE_SELLER"
            strategy_badge = "👑 SOLE SELLER"
            recommended_price = effective_our_price
            reason = "Only seller on the listing. Fully controlling Buy Box."

        item = {
            "asin": asin,
            "sku": sku,
            "title": title,
            "is_pl": is_pl,
            "is_hijacked": is_hijacked,
            "august_orders": aug_orders,
            "august_units": aug_units,
            "our_sale_price": sale_price,
            "zoro_cost_notax": zoro_cost,
            "gross_profit": gross_profit,
            "profit_margin_pct": profit_margin_pct,
            "total_sellers": total_sellers,
            "buy_box_seller": bb_seller,
            "buy_box_price": bb_price,
            "is_our_buy_box": is_bb,
            "lowest_1_seller": low1_seller,
            "lowest_1_price": low1_price,
            "lowest_2_seller": low2_seller,
            "lowest_2_price": low2_price,
            "lowest_3_seller": low3_seller,
            "lowest_3_price": low3_price,
            # Repricing output
            "strategy": strategy,
            "strategy_badge": strategy_badge,
            "recommended_price": recommended_price,
            "profit_delta_per_unit": profit_delta_per_unit,
            "potential_monthly_gain": potential_monthly_gain,
            "reason": reason,
            "amazon_url": f"https://www.amazon.com/dp/{asin}",
        }
        items.append(item)

    bb_win_rate = round((bb_wins_count / total_scraped_count * 100), 1) if total_scraped_count > 0 else 0.0

    return {
        "account": acct,
        "total_asins": total_asins,
        "total_august_orders": total_orders,
        "total_august_units": total_units,
        "total_scraped": total_scraped_count,
        "buy_box_win_rate": bb_win_rate,
        "raise_price_count": raise_price_count,
        "raise_price_total_potential_monthly": round(raise_price_total_potential_monthly, 2),
        "drop_price_count": drop_price_count,
        "pl_total_count": pl_total_count,
        "pl_hijacked_count": pl_hijacked_count,
        "items": items,
    }
