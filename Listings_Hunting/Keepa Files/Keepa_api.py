"""
Keepa API — Fetch Current Buy Box Price for any ASIN
=====================================================

Uses the Keepa REST API (https://keepa.com/#!api) directly via `requests`.
No extra `keepa` library needed — just `pip install requests` if you don't have it.

Key API parameters used:
  - domain=1       → Amazon.com (US)
  - stats=1        → Include the `stats` block with current Buy Box price
  - buybox=1       → Include full Buy Box price history (csv index 18)
  - offers=20      → Include seller offers (needed for buybox data)

Token cost: ~5 tokens per product (with buybox=1 + stats=1 + offers=20).

Usage:
  python Keepa_api.py                          # Uses the default test ASIN
  python Keepa_api.py B0FM2K26BJ               # Single ASIN
  python Keepa_api.py B0FM2K26BJ B08N5WRWNW    # Multiple ASINs (space separated)
"""

import os
import sys
import json
import time
from datetime import datetime, timezone, timedelta

try:
    import requests
except ImportError:
    print("❌ 'requests' library not found. Install it with:")
    print("   pip install requests")
    sys.exit(1)


# ─── Configuration ───────────────────────────────────────────────────────────

# Set your Keepa API key here, or use the KEEPA_API_KEY environment variable
KEEPA_API_KEY = os.environ.get("KEEPA_API_KEY", "b2m5s4sjiv6i5c6tvhrn0ktapi77dmuuuiu841buheto3h3b4q9g0fgpu1bukhkm")

# Amazon domain IDs  (1 = .com,  2 = .co.uk,  3 = .de,  4 = .fr,  5 = .co.jp,
#                      6 = .ca,   8 = .it,     9 = .es,  10 = .in, 11 = .com.mx)
AMAZON_DOMAIN = 1  # Amazon.com (US)

KEEPA_API_BASE = "https://api.keepa.com"

# CSV price type indices  (Keepa stores price history as arrays in product.csv[index])
# NOTE: csv values at indices 0,1,2,7,18 etc. are PRICE + SHIPPING combined (in cents).
#       The stats.buyBoxPrice is the ITEM PRICE only (no shipping).
#       stats.buyBoxShipping is the shipping cost only.
CSV_AMAZON       = 0   # Amazon price (+ shipping)
CSV_NEW          = 1   # Lowest 3rd party New (+ shipping)
CSV_USED         = 2   # Lowest 3rd party Used (+ shipping)
CSV_SALES_RANK   = 3   # Sales rank (not a price)
CSV_LIST_PRICE   = 5   # List price
CSV_NEW_FBM      = 7   # Lowest 3rd party New FBM (+ shipping)
CSV_NEW_FBA      = 10  # Lowest 3rd party New FBA (+ shipping)
CSV_COUNT_NEW    = 11  # Count of New offers
CSV_BUY_BOX      = 18  # Buy Box price (+ shipping)

# Keepa time epoch: minutes since 2011-01-01 00:00:00 UTC
KEEPA_EPOCH = datetime(2011, 1, 1, tzinfo=timezone.utc)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def keepa_minutes_to_datetime(keepa_minutes: int) -> datetime:
    """Convert Keepa proprietary timestamp (minutes since 2011-01-01) to datetime."""
    return KEEPA_EPOCH + timedelta(minutes=keepa_minutes)


def price_to_dollars(price_int: int) -> float | None:
    """Convert Keepa integer price (cents) to dollars. Returns None if -1 or -2 (no data)."""
    if price_int is None or price_int < 0:
        return None
    return price_int / 100.0


def get_latest_from_csv(csv_array: list | None) -> tuple:
    """
    Extract the most recent (timestamp, price) from a Keepa CSV array.
    CSV arrays are structured as [time1, value1, time2, value2, ...].
    Returns (datetime, price_dollars) or (None, None) if empty/invalid.
    """
    if not csv_array or len(csv_array) < 2:
        return None, None

    # The last two elements are always the most recent (time, value)
    price_raw = csv_array[-1]
    time_raw = csv_array[-2]
    price = price_to_dollars(price_raw)
    if price is not None:
        ts = keepa_minutes_to_datetime(time_raw)
        return ts, price

    return None, None


# ─── Core API Call ───────────────────────────────────────────────────────────

def fetch_product(asin: str) -> dict | None:
    """
    Call the Keepa Product API for a single ASIN and return the raw product dict.
    Uses buybox=1 and offers=20 to get the actual Buy Box price.
    
    API docs: https://keepa.com/#!discuss/t/product-api/110
    """
    params = {
        "key":    KEEPA_API_KEY,
        "domain": AMAZON_DOMAIN,
        "asin":   asin,
        "stats":  1,       # Include current stats (buyBoxPrice, etc.)
        "buybox": 1,       # Include Buy Box price history (csv index 18)
        "offers": 20,      # Include offers (needed for buybox data to work)
    }

    url = f"{KEEPA_API_BASE}/product"

    try:
        resp = requests.get(url, params=params, timeout=30)
    except requests.exceptions.RequestException as e:
        print(f"  ❌ Network error: {e}")
        return None

    data = resp.json()

    # If buybox+offers fails (not enough tokens), retry without them
    if resp.status_code != 200:
        error_msg = data.get("error", {}).get("message", "Unknown error")
        tokens_left = data.get("tokensLeft", "?")
        print(f"  ⚠️  Full request failed ({error_msg}). Tokens left: {tokens_left}")
        print(f"  ⏳ Retrying with minimal params (no buybox history)...")
        
        params_minimal = {
            "key":    KEEPA_API_KEY,
            "domain": AMAZON_DOMAIN,
            "asin":   asin,
            "stats":  1,
        }
        try:
            resp = requests.get(url, params=params_minimal, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  ❌ Retry also failed: {e}")
            return None

    # Check for API-level errors
    if "error" in data and data["error"]:
        print(f"  ❌ Keepa API error: {data['error']}")
        return None

    products = data.get("products", [])
    if not products:
        print(f"  ❌ No product found for ASIN {asin}")
        return None

    # Show remaining tokens
    tokens_left = data.get("tokensLeft", "?")
    print(f"  📊 Keepa tokens remaining: {tokens_left}")

    return products[0]


# ─── Extract & Display ───────────────────────────────────────────────────────

def extract_buy_box_price(product: dict, asin: str) -> dict:
    """
    Extract and print pricing info from a Keepa product response.
    
    IMPORTANT KEEPA PRICE DISTINCTION:
      - stats.buyBoxPrice     = ITEM PRICE ONLY (what you see on Amazon)
      - stats.buyBoxShipping  = SHIPPING COST ONLY
      - stats.current[18]     = ITEM + SHIPPING combined
      - csv[18] values        = ITEM + SHIPPING combined
    
    Returns a dict with extracted prices.
    """
    title = product.get("title", "Unknown Product")
    
    print(f"\n{'═' * 72}")
    print(f"  ASIN:   {asin}")
    print(f"  Title:  {title}")
    print(f"{'─' * 72}")

    result = {"asin": asin, "title": title}
    stats = product.get("stats", {})
    
    if not stats:
        print(f"  ⚠️  No stats data returned. Your API plan may not include this.")
        return result

    # ═══ BUY BOX PRICE (the main thing we want) ═══
    raw_bb_price = stats.get("buyBoxPrice", -1)
    raw_bb_shipping = stats.get("buyBoxShipping", -1)
    bb_price = price_to_dollars(raw_bb_price)
    bb_shipping = price_to_dollars(raw_bb_shipping)
    
    if bb_price is not None:
        total = bb_price + (bb_shipping or 0)
        print(f"  💰 Buy Box Item Price:     ${bb_price:.2f}")
        if bb_shipping is not None and bb_shipping > 0:
            print(f"  📦 Buy Box Shipping:       ${bb_shipping:.2f}")
            print(f"  💵 Buy Box Total:          ${total:.2f}")
        else:
            print(f"  📦 Buy Box Shipping:       FREE")
        result["buybox_price"] = bb_price
        result["buybox_shipping"] = bb_shipping or 0
        result["buybox_total"] = total
    else:
        # buyBoxPrice = -2 means it's available but needs buybox=1 param
        if raw_bb_price == -2:
            print(f"  ⚠️  Buy Box price requires buybox=1 param (needs more tokens)")
        else:
            print(f"  ❌ No Buy Box winner currently")
        result["buybox_price"] = None

    # ═══ BUY BOX SELLER ═══
    bb_seller = stats.get("buyBoxSellerId", "")
    if bb_seller:
        is_amazon = bb_seller.upper() in ("ATVPDKIKX0DER", "A2R2RITDJNW1Q6")
        is_fba = stats.get("buyBoxIsFBA", False)
        seller_type = "Amazon" if is_amazon else ("FBA" if is_fba else "FBM")
        print(f"  👤 Buy Box Seller:         {bb_seller} ({seller_type})")
        result["buybox_seller"] = bb_seller
        result["buybox_seller_type"] = seller_type

    # ═══ OTHER CURRENT PRICES ═══
    current = stats.get("current", [])
    
    # Amazon's own price (index 0)
    if len(current) > CSV_AMAZON:
        amz_price = price_to_dollars(current[CSV_AMAZON])
        if amz_price is not None:
            print(f"  🔷 Amazon's Own Price:     ${amz_price:.2f}  (incl. shipping)")
            result["amazon_price"] = amz_price
        else:
            print(f"  🔷 Amazon:                 Not selling this item")
    
    # Lowest New 3P (index 1) — this is price + shipping
    if len(current) > CSV_NEW:
        new_price = price_to_dollars(current[CSV_NEW])
        if new_price is not None:
            print(f"  🏷️  Lowest New (3P):        ${new_price:.2f}  (incl. shipping)")
            result["lowest_new"] = new_price

    # Lowest New FBM (index 7)
    if len(current) > CSV_NEW_FBM:
        fbm_price = price_to_dollars(current[CSV_NEW_FBM])
        if fbm_price is not None:
            print(f"  📬 Lowest New FBM:         ${fbm_price:.2f}  (incl. shipping)")
            result["lowest_fbm"] = fbm_price

    # Lowest New FBA (index 10)
    if len(current) > CSV_NEW_FBA:
        fba_price = price_to_dollars(current[CSV_NEW_FBA])
        if fba_price is not None:
            print(f"  🚀 Lowest New FBA:         ${fba_price:.2f}  (incl. shipping)")
            result["lowest_fba"] = fba_price

    # List price (index 5)
    if len(current) > CSV_LIST_PRICE:
        list_price = price_to_dollars(current[CSV_LIST_PRICE])
        if list_price is not None:
            print(f"  📋 List Price:             ${list_price:.2f}")
            result["list_price"] = list_price

    # Number of New offers (index 11)
    if len(current) > CSV_COUNT_NEW:
        count_new = current[CSV_COUNT_NEW]
        if count_new is not None and count_new >= 0:
            print(f"  🏪 New Offer Count:        {count_new}")
            result["new_offer_count"] = count_new

    # ═══ SALES RANK ═══
    if len(current) > CSV_SALES_RANK:
        rank = current[CSV_SALES_RANK]
        if rank is not None and rank > 0:
            print(f"  📊 Sales Rank:             #{rank:,}")
            result["sales_rank"] = rank

    # ═══ BUY BOX DETAILS ═══
    bb_condition = stats.get("buyBoxCondition", -1)
    is_prime = stats.get("buyBoxIsPrimeEligible", False)
    is_map = stats.get("buyBoxIsMAP", False)
    
    if is_prime:
        print(f"  ✅ Prime Eligible:         Yes")
    if is_map:
        print(f"  🔒 MAP Protected:          Yes")

    # ═══ LAST UPDATE TIME ═══
    last_update = product.get("lastUpdate", 0)
    if last_update and last_update > 0:
        update_dt = keepa_minutes_to_datetime(last_update)
        print(f"  🕐 Last Keepa Update:      {update_dt.strftime('%Y-%m-%d %H:%M UTC')}")
        result["last_update"] = update_dt.isoformat()

    # ═══ CSV HISTORY (Buy Box) ═══
    csv_data = product.get("csv", [])
    if csv_data and len(csv_data) > CSV_BUY_BOX and csv_data[CSV_BUY_BOX]:
        bb_ts, bb_hist_price = get_latest_from_csv(csv_data[CSV_BUY_BOX])
        if bb_hist_price is not None:
            ts_str = bb_ts.strftime('%Y-%m-%d %H:%M UTC') if bb_ts else '?'
            print(f"  📈 Last BB History Point:  ${bb_hist_price:.2f}  (incl. shipping, as of {ts_str})")

    # ═══ ALL SELLER OFFERS ═══
    offers = product.get("offers", [])
    if offers:
        CONDITION_MAP = {
            1: "New",
            2: "Used - Like New",
            3: "Used - Very Good",
            4: "Used - Good",
            5: "Used - Acceptable",
            6: "Refurbished",
            7: "Collectible - Like New",
            8: "Collectible - Very Good",
            9: "Collectible - Good",
            10: "Collectible - Acceptable",
        }
        
        # Parse all offers into a structured list
        parsed_offers = []
        for offer in offers:
            seller_id = offer.get("sellerId", "Unknown")
            condition = offer.get("condition", -1)
            condition_label = CONDITION_MAP.get(condition, f"Condition {condition}")
            is_fba = offer.get("isFBA", False)
            is_prime = offer.get("isPrime", False)
            is_buy_box = offer.get("isBuyBoxWinner", False)
            
            # offerCSV = [time1, price1, shipping1, time2, price2, shipping2, ...]
            # Last 3 values = most recent (time, price, shipping)
            offer_csv = offer.get("offerCSV", [])
            item_price = None
            shipping = None
            if offer_csv and len(offer_csv) >= 3:
                raw_price = offer_csv[-2]
                raw_ship = offer_csv[-1]
                item_price = price_to_dollars(raw_price)
                shipping = price_to_dollars(raw_ship)
            
            if item_price is None:
                continue  # Skip offers with no current price
            
            total = item_price + (shipping or 0)
            
            parsed_offers.append({
                "seller_id": seller_id,
                "condition": condition,
                "condition_label": condition_label,
                "is_fba": is_fba,
                "is_prime": is_prime,
                "is_buy_box": is_buy_box,
                "item_price": item_price,
                "shipping": shipping or 0,
                "total": total,
            })
        
        if parsed_offers:
            # Sort by total price (lowest first)
            parsed_offers.sort(key=lambda x: x["total"])
            
            # Separate New and Used offers
            new_offers = [o for o in parsed_offers if o["condition"] == 1]
            used_offers = [o for o in parsed_offers if o["condition"] != 1]
            
            print(f"\n{'─' * 72}")
            print(f"  🏪 ALL SELLER OFFERS ({len(parsed_offers)} active, sorted by total price)")
            print(f"{'─' * 72}")
            print(f"  {'#':<3} {'Condition':<20} {'FBA':<5} {'Prime':<6} {'Price':>9} {'Ship':>8} {'Total':>10}  {'Seller'}")
            print(f"  {'─'*3} {'─'*20} {'─'*5} {'─'*6} {'─'*9} {'─'*8} {'─'*10}  {'─'*20}")
            
            for i, o in enumerate(parsed_offers, 1):
                fba_str = "✅" if o["is_fba"] else "❌"
                prime_str = "✅" if o["is_prime"] else "❌"
                ship_str = "FREE" if o["shipping"] == 0 else f"${o['shipping']:.2f}"
                
                # Markers
                markers = []
                if i == 1:
                    markers.append("⬇️ LOWEST")
                if o["is_buy_box"]:
                    markers.append("🏆 BUY BOX")
                if o["seller_id"] == bb_seller:
                    markers.append("🏆 BB WINNER")
                marker_str = f"  {'  '.join(markers)}" if markers else ""
                
                print(f"  {i:<3} {o['condition_label']:<20} {fba_str:<5} {prime_str:<6} "
                      f"${o['item_price']:>8.2f} {ship_str:>8} ${o['total']:>9.2f}  "
                      f"{o['seller_id']}{marker_str}")
            
            # ── Lowest NEW offer highlight ──
            if new_offers:
                lowest_new = new_offers[0]
                print(f"\n  {'─' * 68}")
                ship_detail = f" + ${lowest_new['shipping']:.2f} shipping" if lowest_new['shipping'] > 0 else " (FREE shipping)"
                fulfillment = "FBA" if lowest_new["is_fba"] else "FBM"
                print(f"  🔽 Lowest NEW Offer:  ${lowest_new['item_price']:.2f}{ship_detail}"
                      f" = ${lowest_new['total']:.2f}  [{fulfillment}]")
                print(f"     Seller ID:         {lowest_new['seller_id']}")
                result["lowest_new_offer"] = lowest_new
            
            if used_offers:
                lowest_used = used_offers[0]
                ship_detail = f" + ${lowest_used['shipping']:.2f} shipping" if lowest_used['shipping'] > 0 else " (FREE shipping)"
                fulfillment = "FBA" if lowest_used["is_fba"] else "FBM"
                print(f"  🔽 Lowest USED Offer: ${lowest_used['item_price']:.2f}{ship_detail}"
                      f" = ${lowest_used['total']:.2f}  [{fulfillment}] ({lowest_used['condition_label']})")
                print(f"     Seller ID:         {lowest_used['seller_id']}")
                result["lowest_used_offer"] = lowest_used
            
            result["all_offers"] = parsed_offers
    else:
        print(f"\n  ℹ️  No individual offers returned (use offers=20 for seller data)")

    print(f"\n{'═' * 72}\n")
    return result


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    # Validate API key
    if not KEEPA_API_KEY or len(KEEPA_API_KEY) < 20:
        print("=" * 72)
        print("  ❌ ERROR: No Keepa API key configured!")
        print()
        print("  Set it in one of two ways:")
        print("    1. Edit this file and set KEEPA_API_KEY on line ~39")
        print("    2. Set the environment variable:")
        print("       export KEEPA_API_KEY='your_actual_key'")
        print()
        print("  Get your API key at: https://keepa.com/#!api")
        print("=" * 72)
        sys.exit(1)

    # Parse ASINs from command line or use default
    if len(sys.argv) > 1:
        # Support both space-separated and comma-separated ASINs
        asins = []
        for arg in sys.argv[1:]:
            asins.extend(arg.split(","))
        asins = [a.strip() for a in asins if a.strip()]
    else:
        asins = ["B0FM2K26BJ"]  # Default: your barrel product

    print(f"\n🔍 Fetching LIVE Buy Box prices from Keepa API...")
    print(f"   ASINs: {', '.join(asins)}")
    print(f"   Domain: Amazon.com (US)\n")

    results = {}

    for asin in asins:
        print(f"⏳ Querying ASIN: {asin} ...")
        product = fetch_product(asin)
        
        if product:
            price_data = extract_buy_box_price(product, asin)
            results[asin] = price_data
        else:
            results[asin] = None

        # Small delay between requests to avoid rate limiting
        if len(asins) > 1:
            time.sleep(1)

    # ── Summary ──
    if len(asins) > 1:
        print("\n" + "=" * 72)
        print("  SUMMARY")
        print("=" * 72)
        for asin, data in results.items():
            if data and data.get("buybox_price") is not None:
                bb = data["buybox_price"]
                ship = data.get("buybox_shipping", 0)
                total = data.get("buybox_total", bb)
                if ship > 0:
                    print(f"  {asin}  →  ${bb:.2f} + ${ship:.2f} shipping = ${total:.2f}")
                else:
                    print(f"  {asin}  →  ${bb:.2f} (free shipping)")
            else:
                print(f"  {asin}  →  ❌ No Buy Box price available")
        print("=" * 72)

    return results


if __name__ == "__main__":
    main()
