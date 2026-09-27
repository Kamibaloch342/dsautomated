#!/usr/bin/env python3
"""
amazon_listing_offers.py — Amazon Listing Offers & Competitor Scraper

Automates:
1. Opening an Amazon product listing page (via URL or ASIN).
2. Setting / verifying the delivery ZIP code (default: 10001 / New York).
3. Opening the "New (X) from $XX.XX" / All Offers Display (AOD) side panel.
4. Extracting and printing all sellers sitting on the listing:
   - Seller Name & Store Link
   - Item Price
   - Shipping Cost / Free Shipping
   - Shipping Time / Delivery Promise (e.g. Friday, September 4)
   - Total Landed Price (Item Price + Shipping)
   - Ships From & Sold By
   - Seller Feedback Ratings & Positive %
   - Condition & Prime Status

Usage:
    python scripts/amazon_listing_offers.py "https://www.amazon.com/gp/product/B0DQ6CPTW3?th=1"
    python scripts/amazon_listing_offers.py --asin B0DQ6CPTW3 --zipcode 10001
    python scripts/amazon_listing_offers.py "https://www.amazon.com/dp/B0DQ6CPTW3" --headless
    python scripts/amazon_listing_offers.py "https://www.amazon.com/dp/B0DQ6CPTW3" --json
"""

from __future__ import annotations

import os
import sys

# Auto re-exec in project .venv if run with system python
_VENV_PY = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".venv", "bin", "python"))
if os.path.exists(_VENV_PY) and sys.executable != _VENV_PY:
    try:
        import bs4  # if already installed in current env, proceed
    except ImportError:
        os.execv(_VENV_PY, [_VENV_PY] + sys.argv)

import argparse
import json
import re
import time
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


def get_driver(headless: bool = True) -> webdriver.Chrome:
    """Initialize a hardened Chrome WebDriver instance with stealth settings."""
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--window-size=1600,1200")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
    opts.add_argument("--lang=en-US,en")

    driver = webdriver.Chrome(options=opts)

    # CDP stealth scripts to evade basic bot detection
    stealth_js = """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.navigator.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    """
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": stealth_js})
    except Exception:
        pass

    driver.set_page_load_timeout(60)
    driver.set_script_timeout(30)
    return driver


def clean_text(text: Optional[str]) -> str:
    """Helper to remove excessive whitespace and clean string."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def parse_price_value(price_str: Optional[str]) -> Optional[float]:
    """Extract numeric float from price string (e.g. '$54.99' -> 54.99)."""
    if not price_str:
        return None
    m = re.search(r"\$([0-9]+(?:\.[0-9]{2})?)", price_str)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def set_amazon_zipcode(driver: webdriver.Chrome, zipcode: str = "10001") -> bool:
    """
    Set delivery location on Amazon to the specified ZIP code.
    Handles standard location flyout and international redirection modal.
    """
    print(f"[*] Checking / setting delivery ZIP code to {zipcode}...")

    # 1. Check if location is already set
    try:
        loc_elem = driver.find_element(By.ID, "glow-ingress-block")
        curr_text = clean_text(loc_elem.text)
        if zipcode in curr_text:
            print(f"    ✓ Delivery location already active: {curr_text}")
            return True
    except Exception:
        pass

    try:
        # 2. Click location selector button
        try:
            loc_btn = WebDriverWait(driver, 6).until(
                EC.element_to_be_clickable((By.ID, "nav-global-location-popover-link"))
            )
            loc_btn.click()
        except Exception:
            driver.find_element(By.ID, "glow-ingress-block").click()

        time.sleep(1.5)

        # 3. Enter ZIP code into modal input
        zip_input = WebDriverWait(driver, 6).until(
            EC.presence_of_element_located((By.ID, "GLUXZipUpdateInput"))
        )
        zip_input.clear()
        time.sleep(0.3)
        zip_input.send_keys(zipcode)
        time.sleep(0.4)

        # 4. Click Apply button
        apply_btn = WebDriverWait(driver, 6).until(
            EC.element_to_be_clickable(
                (
                    By.CSS_SELECTOR,
                    "#GLUXZipUpdate input, #GLUXZipUpdate-announce, input[aria-labelledby='GLUXZipUpdate-announce']",
                )
            )
        )
        apply_btn.click()
        time.sleep(2)

        # 5. Handle 'Continue' / 'Done' confirmation modal
        continue_selectors = [
            "#GLUXConfirmClose",
            "#GLUXConfirmClose-announce",
            "*[data-action='GLUXConfirmAction']",
            ".a-popover-footer #GLUXConfirmClose",
            "input[aria-labelledby='GLUXConfirmClose-announce']",
            "button[name='glowDoneButton']",
        ]
        for c_sel in continue_selectors:
            btns = driver.find_elements(By.CSS_SELECTOR, c_sel)
            for b in btns:
                try:
                    if b.is_displayed():
                        driver.execute_script("arguments[0].click();", b)
                        time.sleep(2.5)
                        break
                except Exception:
                    pass

        time.sleep(2)

        # 6. Verify location
        try:
            loc_elem_after = driver.find_element(By.ID, "glow-ingress-block")
            print(f"    ✓ Delivery location updated to: {clean_text(loc_elem_after.text)}")
        except Exception:
            pass

        return True
    except Exception as e:
        print(f"    ⚠️ Notice while updating ZIP code: {e}")
        return False


def click_all_offers_button(driver: webdriver.Chrome) -> bool:
    """Click on the 'New (X) from $XX.XX' or 'See All Buying Choices' link to open AOD drawer."""
    print("[*] Opening 'All Offers' / 'Other Sellers' side panel...")

    offer_selectors = [
        "#olpLinkWidget_feature_div a",
        "#olp-upd-new a",
        "#olp-upd-new-used a",
        "#dynamic-aod-ingress-box a",
        "#aod-ingress-link",
        "a[href*='olp_all_']",
        "a[href*='offer-listing']",
        "a[aria-label*='See all buying choices']",
        "#buybox-see-all-buying-choices a",
        "span[data-action='show-all-offers-side-sheet'] a",
    ]

    for sel in offer_selectors:
        links = driver.find_elements(By.CSS_SELECTOR, sel)
        for link in links:
            try:
                if link.is_displayed() or link.text:
                    print(f"    ✓ Clicking offer link: '{clean_text(link.text)}'")
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
                    time.sleep(0.5)
                    driver.execute_script("arguments[0].click();", link)
                    return True
            except Exception:
                continue

    # Fallback search by text/XPath
    xpath_selectors = [
        "//a[contains(., 'New (')]",
        "//a[contains(., 'Other sellers on Amazon')]",
        "//a[contains(., 'from $')]",
        "//span[contains(text(), 'New (')]/parent::a",
    ]
    for xp in xpath_selectors:
        elements = driver.find_elements(By.XPATH, xp)
        for elem in elements:
            try:
                print(f"    ✓ Clicking offer link via XPath: '{clean_text(elem.text)}'")
                driver.execute_script("arguments[0].click();", elem)
                return True
            except Exception:
                continue

    print("    ⚠️ Could not find explicit 'Other sellers' button (listing may only have 1 seller / Buy Box).")
    return False


def extract_single_offer(offer_soup: BeautifulSoup, is_pinned: bool = False) -> Optional[Dict[str, Any]]:
    """Parse single offer element (Buy Box pinned offer or side-sheet offer card)."""
    # 1. Price extraction
    raw_price = ""
    # Method A: accessibility label
    label_span = offer_soup.find("span", class_=re.compile(r"apex-pricetopay-accessibility-label"))
    if label_span and label_span.text.strip():
        m = re.search(r"\$[0-9]+(?:\.[0-9]{2})?", label_span.text)
        if m:
            raw_price = m.group(0)

    # Method B: whole + fraction
    if not raw_price:
        price_holder = offer_soup.find("span", id=re.compile(r"aod-price-\d+"))
        if price_holder:
            whole = price_holder.find("span", class_="a-price-whole")
            fraction = price_holder.find("span", class_="a-price-fraction")
            if whole:
                raw_price = f"${whole.text.replace('.', '').strip()}.{fraction.text.strip() if fraction else '00'}"
            else:
                offscreen = price_holder.find("span", class_="a-offscreen")
                if offscreen and offscreen.text.strip():
                    m = re.search(r"\$[0-9]+(?:\.[0-9]{2})?", offscreen.text)
                    if m:
                        raw_price = m.group(0)

    # Method C: generic a-price
    if not raw_price:
        price_elem = offer_soup.find("span", class_="a-price")
        if price_elem:
            whole = price_elem.find("span", class_="a-price-whole")
            fraction = price_elem.find("span", class_="a-price-fraction")
            if whole:
                raw_price = f"${whole.text.replace('.', '').strip()}.{fraction.text.strip() if fraction else '00'}"
            else:
                offscreen = price_elem.find("span", class_="a-offscreen")
                if offscreen:
                    m = re.search(r"\$[0-9]+(?:\.[0-9]{2})?", offscreen.text)
                    if m:
                        raw_price = m.group(0)

    # 2. Ships from
    ships_from = ""
    ships_elem = offer_soup.find("div", id="aod-offer-shipsFrom")
    if ships_elem:
        col_right = ships_elem.find("div", class_="a-col-right")
        if col_right:
            ships_from = clean_text(col_right.text)
        else:
            ships_from = clean_text(ships_elem.text.replace("Ships from", ""))

    # 3. Sold by
    sold_by = ""
    seller_link = ""
    seller_elem = offer_soup.find("div", id="aod-offer-soldBy")
    if seller_elem:
        col_right = seller_elem.find("div", class_="a-col-right")
        a_tag = seller_elem.find("a")
        if a_tag:
            sold_by = clean_text(a_tag.text)
            href = a_tag.get("href", "")
            if href:
                seller_link = "https://www.amazon.com" + href if href.startswith("/") else href
        elif col_right:
            sold_by = clean_text(col_right.text)
        else:
            sold_by = clean_text(seller_elem.text.replace("Sold by", ""))

    # Fallback for Sold by from Add to Cart button label
    if not sold_by:
        atc = offer_soup.find("input", attrs={"name": "submit.addToCart"})
        if atc and atc.get("aria-label"):
            label = atc.get("aria-label")
            m = re.search(r"seller\s+([^\s]+(?:\s+[^\s]+)*?)\s+and\s+price", label, re.IGNORECASE)
            if m:
                sold_by = m.group(1).strip()

    if not sold_by:
        return None

    # 4. Seller rating & reviews count
    rating_text = ""
    rating_elem = offer_soup.find("div", id="aod-offer-seller-rating")
    if rating_elem:
        rating_text = clean_text(rating_elem.text)

    # 5. Delivery info & shipping time
    delivery_text = ""
    delivery_elem = offer_soup.find("div", id="mir-layout-DELIVERY_BLOCK")
    if delivery_elem:
        del_span = delivery_elem.find("span", attrs={"data-csa-c-delivery-time": True})
        if del_span:
            del_time = del_span.get("data-csa-c-delivery-time", "")
            del_price = del_span.get("data-csa-c-delivery-price", "")
            delivery_text = clean_text(f"{del_price} delivery {del_time}".strip())
        else:
            delivery_text = clean_text(delivery_elem.text)
    else:
        del_promise = offer_soup.find("div", class_="aod-delivery-promise")
        if del_promise:
            delivery_text = clean_text(del_promise.text)

    # Clean details link suffix if present
    delivery_text = re.sub(r"\s+Details.*$", "", delivery_text, flags=re.IGNORECASE).strip()

    # 6. Separate shipping fee (if applicable)
    shipping_charge = 0.0
    ship_charge_elem = offer_soup.find("div", id="aod_ship_charge_row")
    if ship_charge_elem:
        m_ship = re.search(r"\$([0-9]+(?:\.[0-9]{2})?)", ship_charge_elem.text)
        if m_ship:
            shipping_charge = float(m_ship.group(1))

    # Also check if delivery text contains shipping cost
    m_del_cost = re.search(r"\$([0-9]+(?:\.[0-9]{2})?)\s+delivery", delivery_text, re.IGNORECASE)
    if m_del_cost and shipping_charge == 0.0:
        shipping_charge = float(m_del_cost.group(1))

    # 7. Condition
    condition = "New"
    heading = offer_soup.find("div", id="aod-offer-heading")
    if heading:
        condition = clean_text(heading.text)

    # 8. Unit Price
    unit_price = ""
    unit_elem = offer_soup.find("span", class_=re.compile(r"apex-priceperunit-accessibility-label"))
    if unit_elem:
        unit_price = clean_text(unit_elem.text)

    item_price_num = parse_price_value(raw_price) or 0.0
    total_price_num = round(item_price_num + shipping_charge, 2)

    return {
        "is_pinned": is_pinned,
        "seller_name": sold_by,
        "item_price": f"${item_price_num:.2f}" if item_price_num > 0 else raw_price,
        "shipping_cost": f"${shipping_charge:.2f}" if shipping_charge > 0 else "FREE",
        "shipping_time": delivery_text if delivery_text else "Standard",
        "total_price": f"${total_price_num:.2f}" if total_price_num > 0 else raw_price,
        "ships_from": ships_from if ships_from else sold_by,
        "rating_info": rating_text,
        "condition": condition,
        "unit_price": unit_price,
        "seller_link": seller_link,
    }


def parse_all_offers_from_page(html_content: str) -> List[Dict[str, Any]]:
    """Extract all offers from the All Offers Display (AOD) panel and main page Buy Box."""
    soup = BeautifulSoup(html_content, "html.parser")
    offers: List[Dict[str, Any]] = []
    seen_sellers = set()

    # 1. Main Page Buy Box - always capture so Amazon Retail or lowest Buy Box is never missed
    bb_seller_elem = soup.find(id="sellerProfileTriggerId") or soup.find(id="merchant-info")
    bb_price_elem = (
        soup.find("div", id="corePriceDisplay_desktop_feature_div")
        or soup.find("div", id="corePrice_feature_div")
        or soup.find("span", class_="a-price")
    )
    if bb_seller_elem and bb_price_elem:
        price_val = clean_text(bb_price_elem.text)
        m = re.search(r"\$[0-9]+(?:\.[0-9]{2})?", price_val)
        price_str = m.group(0) if m else price_val
        seller_raw = clean_text(bb_seller_elem.text)
        seller_name = seller_raw
        if "sold by" in seller_raw.lower():
            parts = re.split(r"sold by:?\s*", seller_raw, flags=re.I)
            if len(parts) > 1 and parts[1].strip():
                seller_name = parts[1].strip()
        if seller_name and price_str:
            offers.append(
                {
                    "is_pinned": True,
                    "seller_name": seller_name,
                    "item_price": price_str,
                    "shipping_cost": "FREE",
                    "shipping_time": "Buy Box Delivery",
                    "total_price": price_str,
                    "ships_from": "Amazon / Seller",
                    "rating_info": "",
                    "condition": "New",
                    "unit_price": "",
                    "seller_link": "",
                }
            )
            seen_sellers.add((seller_name.lower(), price_str))

    # 2. Pinned Offer (Buy Box in AOD drawer)
    pinned = soup.find("div", id="aod-pinned-offer")
    if pinned:
        offer = extract_single_offer(pinned, is_pinned=True)
        if offer and offer["seller_name"]:
            key = (offer["seller_name"].lower(), offer["item_price"])
            if key not in seen_sellers:
                offers.append(offer)
                seen_sellers.add(key)

    # 3. Offers in list (AOD side drawer)
    offer_list = soup.find("div", id="aod-offer-list")
    if offer_list:
        offer_divs = offer_list.find_all("div", id="aod-offer")
        for div in offer_divs:
            offer = extract_single_offer(div, is_pinned=False)
            if offer and offer["seller_name"]:
                key = (offer["seller_name"].lower(), offer["item_price"])
                if key not in seen_sellers:
                    offers.append(offer)
                    seen_sellers.add(key)

    return offers


def scrape_amazon_sellers(
    url_or_asin: str,
    zipcode: str = "10001",
    headless: bool = True,
) -> List[Dict[str, Any]]:
    """
    Main function to open Amazon product page, set ZIP code, open offers panel,
    and return list of all active sellers with pricing and shipping details.
    """
    if url_or_asin.startswith("http"):
        url = url_or_asin
    else:
        url = f"https://www.amazon.com/dp/{url_or_asin}"

    print(f"[*] Starting Amazon Listing Scraper...")
    print(f"[*] Target URL: {url}")
    print(f"[*] Target ZIP: {zipcode}")
    print(f"[*] Headless Mode: {headless}")

    driver = get_driver(headless=headless)
    try:
        print("[*] Navigating to listing page...")
        driver.get(url)
        time.sleep(3)

        # Handle captcha if any
        if "validateCaptcha" in driver.page_source:
            print("⚠️ CAPTCHA detected by Amazon! Retrying with fresh delay...")
            time.sleep(5)
            driver.get(url)
            time.sleep(3)

        # Set ZIP code
        set_amazon_zipcode(driver, zipcode)
        time.sleep(2.5)

        # Click All Offers / Other Sellers
        click_all_offers_button(driver)

        # Wait for AOD panel to load
        print("[*] Waiting for offers data to populate...")
        time.sleep(4)

        # Parse offers
        html = driver.page_source
        offers = parse_all_offers_from_page(html)
        return offers
    finally:
        driver.quit()


def print_offers_report(offers: List[Dict[str, Any]], url_or_asin: str, zipcode: str = "10001") -> None:
    """Print formatted terminal report of all sellers sitting on listing."""
    print("\n" + "=" * 100)
    print(f"  AMAZON LISTING SELLERS REPORT  |  ZIP: {zipcode}  |  TOTAL SELLERS: {len(offers)}")
    print(f"  URL/ASIN: {url_or_asin}")
    print("=" * 100)

    if not offers:
        print("  ⚠️ No sellers found on this listing.")
        print("=" * 100 + "\n")
        return

    # Table Header
    header = f"{'#':<3} {'SELLER NAME':<24} {'ITEM PRICE':<12} {'SHIPPING':<10} {'TOTAL PRICE':<13} {'DELIVERY / SHIPPING TIME':<32}"
    print(header)
    print("-" * 100)

    for idx, off in enumerate(offers, 1):
        is_bb = " [BUY BOX]" if off.get("is_pinned") else ""
        seller_label = f"{off['seller_name']}{is_bb}"
        if len(seller_label) > 23:
            seller_label = seller_label[:21] + ".."

        row = (
            f"{idx:<3} "
            f"{seller_label:<24} "
            f"{off['item_price']:<12} "
            f"{off['shipping_cost']:<10} "
            f"{off['total_price']:<13} "
            f"{off['shipping_time']:<32}"
        )
        print(row)
        if off.get("rating_info"):
            print(f"    ↳ Feedback: {off['rating_info']}")
        if off.get("ships_from") and off.get("ships_from") != off.get("seller_name"):
            print(f"    ↳ Ships From: {off['ships_from']}")
        print()

    print("=" * 100 + "\n")


def run_interactive():
    """Interactive CLI mode when run without arguments."""
    print("\n" + "=" * 80)
    print("      🛒 AMAZON LISTING OFFERS & COMPETITOR SCRAPER")
    print("=" * 80)
    print("  Fetches all sellers sitting on an Amazon listing, their prices,")
    print("  shipping rates, and delivery estimates for any US ZIP code.\n")

    while True:
        try:
            url_input = input("👉 Enter Amazon Product URL or ASIN [or press Enter for sample B0DQ6CPTW3]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break

        if url_input.lower() in ("q", "quit", "exit"):
            print("Goodbye!")
            break

        if not url_input:
            url_input = "https://www.amazon.com/gp/product/B0DQ6CPTW3?th=1"

        try:
            zip_input = input("👉 Enter Delivery ZIP code [press Enter for 10001]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break

        zipcode = zip_input if zip_input else "10001"

        print("\n" + "-" * 80)
        offers = scrape_amazon_sellers(url_input, zipcode=zipcode, headless=True)
        print_offers_report(offers, url_input, zipcode=zipcode)

        try:
            again = input("🔄 Check another product listing? (y/N): ").strip().lower()
            if again not in ("y", "yes"):
                print("\nAll done. Exiting!")
                break
            print("\n")
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break


def main():
    parser = argparse.ArgumentParser(
        description="Scrape all sellers, prices, and shipping times on an Amazon listing."
    )
    parser.add_argument(
        "url",
        nargs="?",
        default=None,
        help="Amazon Product URL or ASIN",
    )
    parser.add_argument("--asin", dest="asin", default=None, help="Amazon ASIN code")
    parser.add_argument(
        "--zipcode", "-z", default="10001", help="ZIP code for delivery rates (default: 10001)"
    )
    parser.add_argument(
        "--visible", action="store_true", help="Run browser in visible (non-headless) mode"
    )
    parser.add_argument("--json", action="store_true", help="Output results as JSON")

    args = parser.parse_args()

    # If no URL or ASIN provided via CLI, launch interactive prompt mode
    if not args.url and not args.asin:
        run_interactive()
        return

    target = args.asin if args.asin else args.url
    headless = not args.visible

    offers = scrape_amazon_sellers(target, zipcode=args.zipcode, headless=headless)

    if args.json:
        print(json.dumps(offers, indent=2))
    else:
        print_offers_report(offers, target, zipcode=args.zipcode)


if __name__ == "__main__":
    main()

