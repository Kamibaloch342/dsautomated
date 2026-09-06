#!/usr/bin/env python3
"""
amazon_scraper.py -- Scrape rich order detail from Amazon Seller Central.

Extracts from order page:
  -- closing_orders fields --
  purchase_date (with time + timezone)
  ship_by_date
  deliver_by_date
  asin, sku

  -- amazon_order_details fields --
  buyer_name, buyer_email
  ship_to_name, ship_to_street, ship_to_city, ship_to_state, ship_to_zip, ship_to_country
  bill_to_name, bill_to_street, bill_to_city, bill_to_state, bill_to_zip, bill_to_country
  shipping_service, fulfillment_channel, sales_channel, payment_method
  is_gift, gift_message
  order_subtotal, shipping_cost, tax_amount, discount_amount, order_total, currency
  feedback_rating, feedback_text, feedback_date
  page_body_snippet

Usage:
    from engine.amazon_scraper import AmazonScraper
    scraper = AmazonScraper(port=9224)  # Use correct account profile
    data = scraper.scrape_order("112-5802763-4772250")
    scraper.save_to_db(data)
"""

from __future__ import annotations

import os
import sys
import re
import time
import logging
from typing import Dict

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

logger = logging.getLogger("amazon_scraper")

AMAZON_ORDER_URL = "https://sellercentral.amazon.com/orders-v3/order/{}"

# Set CLOSING_DEBUG_RAW=1 to dump every scraped page (full HTML + visible
# text) to Final_Testing_Logs/raw_pages/<date>/<order-id>.{html,txt}
RAW_DUMP_ROOT = os.path.join(_SCRIPT_DIR, "Final_Testing_Logs", "raw_pages")


# -- Selenium helpers ---------------------------------------------------------

def _harden_driver(driver):
    try:
        from utils.helpers import apply_cdp_stealth
        apply_cdp_stealth(driver)          # replaces the banner-painting launch flag
    except Exception:
        pass
    return __harden_timeouts(driver)


def __harden_timeouts(driver):
    """One stalled page load must not wedge the session: fail the navigation at
    60s instead of every later command eating the client's 120s read timeout."""
    try:
        driver.set_page_load_timeout(60)
        driver.set_script_timeout(30)
    except Exception:
        pass
    return driver


def _connect_chrome(port: int = 9221):
    """Connect to an existing Chrome instance on the given debugging port."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"localhost:{port}")

    # Try 1: system chromedriver
    try:
        driver = webdriver.Chrome(options=opts)
        logger.info("Connected to Chrome on port %d -- %s", port, driver.current_url)
        return _harden_driver(driver)
    except Exception as e:
        logger.warning("System chromedriver failed on port %d: %s", port, e)

    # Try 2: webdriver-manager auto-download
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=opts)
        logger.info("Connected via webdriver-manager on port %d -- %s", port, driver.current_url)
        return _harden_driver(driver)
    except Exception as e2:
        logger.warning("webdriver-manager also failed on port %d: %s", port, e2)

    # Try 3: undetected_chromedriver
    try:
        import undetected_chromedriver as uc
        driver = uc.Chrome(options=opts, driver_executable_path=None)
        logger.info("Connected via uc on port %d -- %s", port, driver.current_url)
        return _harden_driver(driver)
    except Exception as e3:
        logger.warning("uc chromedriver also failed on port %d: %s", port, e3)
        return None


def _launch_chrome(profile_dir: str | None = None):
    """Launch a new Chrome instance. Optional profile dir for persistence."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    opts = Options()
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    system_chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if os.path.exists(system_chrome):
        opts.binary_location = system_chrome

    if profile_dir:
        opts.add_argument(f"user-data-dir={profile_dir}")

    driver = webdriver.Chrome(options=opts)
    logger.info("Launched new Chrome instance")
    return _harden_driver(driver)


# -- Scraper class ------------------------------------------------------------

class AmazonScraper:
    """Scrape Amazon SC order detail pages for missing fields."""

    def __init__(self, driver=None, port: int | None = None, profile_dir: str | None = None):
        self.driver = driver
        self._own_driver = False
        if self.driver is None:
            if port is not None:
                self.driver = _connect_chrome(port)
            if self.driver is None and profile_dir is not None:
                self.driver = _launch_chrome(profile_dir)
            if self.driver is None:
                self.driver = _launch_chrome()
            self._own_driver = True

    # -- Core scrape ------------------------------------------------------------
    def _dump_raw_page(self, amazon_order_id: str, body_text: str) -> None:
        """When CLOSING_DEBUG_RAW=1, save the raw page for offline debugging."""
        if os.environ.get("CLOSING_DEBUG_RAW") != "1":
            return
        try:
            from datetime import datetime
            dump_dir = os.path.join(RAW_DUMP_ROOT, datetime.now().strftime("%Y%m%d"))
            os.makedirs(dump_dir, exist_ok=True)
            with open(os.path.join(dump_dir, f"{amazon_order_id}.html"), "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            with open(os.path.join(dump_dir, f"{amazon_order_id}.txt"), "w", encoding="utf-8") as f:
                f.write(body_text)
            logger.info("  [RAW] dumped page for %s → %s", amazon_order_id, dump_dir)
        except Exception as e:
            logger.warning("  [RAW] dump failed for %s: %s", amazon_order_id, e)

    def scrape_order(self, amazon_order_id: str) -> dict:
        """
        Scrape one order detail page.
        Returns dict with all extracted fields.
        """
        url = AMAZON_ORDER_URL.format(amazon_order_id)
        result = self._empty_result()
        result["amazon_order_id"] = amazon_order_id

        try:
            logger.info("Scraping Amazon SC for %s", amazon_order_id)
            self.driver.get(url)
            time.sleep(3)

            # -- Verify we landed on the right page --
            current_url = self.driver.current_url
            logger.debug("  Current URL: %s", current_url)
            if amazon_order_id not in current_url:
                logger.warning("  URL mismatch for %s: got %s", amazon_order_id, current_url)
                # One retry after longer wait
                time.sleep(5)
                current_url = self.driver.current_url
                if amazon_order_id not in current_url:
                    logger.error("  Still on wrong page for %s: %s", amazon_order_id, current_url)
                    result["page_body_snippet"] = self.driver.find_element("tag name", "body").text[:3000]
                    return result

            body_text = self.driver.find_element("tag name", "body").text
            result["page_body_snippet"] = body_text[:3000]

            # If page looks like an unhydrated skeleton, wait and retry once
            if len(body_text) < 500 or "New Seller Central" in body_text and "Order details" not in body_text:
                logger.warning("  Page looks unhydrated for %s, waiting 5s...", amazon_order_id)
                time.sleep(5)
                body_text = self.driver.find_element("tag name", "body").text
                result["page_body_snippet"] = body_text[:3000]

            self._dump_raw_page(amazon_order_id, body_text)
            # Full HTML kept only when debug capture is on, so dump_order_debug
            # reuses THIS page load instead of opening the order a second time.
            if os.environ.get("CLOSING_DEBUG_EML") == "1":
                try:
                    result["page_html"] = self.driver.page_source
                except Exception:
                    pass

            # -- closing_orders fields --
            result["purchase_date"] = self._extract_purchase_date(body_text)
            result["ship_by_date"] = self._extract_date(body_text, "Ship by")
            result["deliver_by_date"] = self._extract_date(body_text, "Deliver by")
            result["asin"] = self._extract_asin(body_text)
            result["sku"] = self._extract_sku(body_text)

            # -- buyer info --
            result["buyer_name"] = self._extract_buyer_name(body_text)
            result["buyer_email"] = self._extract_buyer_email(body_text)

            # -- addresses --
            ship = self._extract_address(body_text, "Ship to")
            result.update({f"ship_to_{k}": v for k, v in ship.items()})

            bill = self._extract_address(body_text, "Bill to")
            result.update({f"bill_to_{k}": v for k, v in bill.items()})

            # -- order attributes --
            result["shipping_service"] = self._extract_label(body_text, "Shipping service")
            result["fulfillment_channel"] = self._extract_label(body_text, "Fulfillment")
            result["sales_channel"] = self._extract_label(body_text, "Sales channel")
            result["payment_method"] = self._extract_label(body_text, "Payment method")

            # -- gift --
            result["is_gift"] = 1 if re.search(r"\b[Gg]ift\b", body_text) and re.search(r"[Gg]ift message", body_text) else 0
            result["gift_message"] = self._extract_gift_message(body_text)

            # -- financials --
            result["order_subtotal"] = self._extract_money(body_text, r"Item\s*\(?s?\)?\s*subtotal")
            if result["order_subtotal"] is None:
                result["order_subtotal"] = self._extract_money(body_text, "Subtotal")
            result["shipping_cost"] = self._extract_money(body_text, "Shipping")
            result["tax_amount"] = self._extract_money(body_text, "Tax")
            result["discount_amount"] = self._extract_money(body_text, "Discount|Promotion")
            result["order_total"] = self._extract_money(body_text, "Grand total|Order total|Total")
            result["currency"] = "USD"

            # -- feedback -- (the star rating is an icon class in the HTML, not
            # visible text, so parse page_source; fall back to the old text parse)
            feedback = self._extract_feedback(body_text)
            result["feedback_rating"] = feedback.get("rating")
            result["feedback_text"] = feedback.get("text")
            result["feedback_date"] = feedback.get("date")
            try:
                from engine.closing_signals import extract_feedback as _ef
                fb = _ef(self.driver.page_source)
                if fb.get("feedback_rating") is not None:
                    result["feedback_rating"] = fb["feedback_rating"]
                if fb.get("feedback_text"):
                    result["feedback_text"] = fb["feedback_text"]
                if fb.get("feedback_date"):
                    result["feedback_date"] = fb["feedback_date"]
            except Exception:
                pass

            # -- tracking uploaded to Amazon --
            tracking_up = self._extract_tracking_uploaded(body_text)
            result["tracking_id_uploaded"] = tracking_up.get("tracking_id_uploaded", "")
            result["tracking_carrier_uploaded"] = tracking_up.get("tracking_carrier_uploaded", "")
            result["tracking_upload_date"] = tracking_up.get("tracking_upload_date", "")

            # -- WB / merchant order id (the seller order id we set at fulfillment) --
            # Look in full page source so backfill/explicit-id closing can resolve
            # the WB without the report, then key Zoro enrichment off it.
            try:
                wb = re.findall(r"WB\d{6,}", self.driver.page_source)
            except Exception:
                wb = re.findall(r"WB\d{6,}", body_text or "")
            result["wb_order_id_scraped"] = ",".join(dict.fromkeys(wb))   # unique, order-preserving

            result["scraped_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

            logger.info(
                "  %s -> purchase=%s ship=%s deliver=%s buyer=%s asin=%s sku=%s feedback=%s tracking_up=%s",
                amazon_order_id,
                result["purchase_date"] or "N/A",
                result["ship_by_date"] or "N/A",
                result["deliver_by_date"] or "N/A",
                result["buyer_name"] or "N/A",
                result["asin"] or "N/A",
                result["sku"] or "N/A",
                result["feedback_rating"] or "N/A",
                result["tracking_id_uploaded"] or "N/A",
            )

        except Exception as e:
            logger.warning("  Failed to scrape %s: %s", amazon_order_id, e)

        return result

    # -- Extraction helpers -----------------------------------------------------
    @staticmethod
    def _empty_result() -> dict:
        return {
            "purchase_date": "",
            "ship_by_date": "",
            "deliver_by_date": "",
            "asin": "",
            "sku": "",
            "buyer_name": "",
            "buyer_email": "",
            "ship_to_name": "",
            "ship_to_street": "",
            "ship_to_city": "",
            "ship_to_state": "",
            "ship_to_zip": "",
            "ship_to_country": "",
            "bill_to_name": "",
            "bill_to_street": "",
            "bill_to_city": "",
            "bill_to_state": "",
            "bill_to_zip": "",
            "bill_to_country": "",
            "shipping_service": "",
            "fulfillment_channel": "",
            "sales_channel": "",
            "payment_method": "",
            "is_gift": 0,
            "gift_message": "",
            "order_subtotal": None,
            "shipping_cost": None,
            "tax_amount": None,
            "discount_amount": None,
            "order_total": None,
            "currency": "USD",
            "feedback_rating": None,
            "feedback_text": "",
            "feedback_date": "",
            "page_body_snippet": "",
            "scraped_at": "",
        }

    @staticmethod
    def _extract_date(text: str, label: str) -> str:
        # e.g. "Ship by: Mon, Jun 8, 2026 PDT" or "Ship by: Mon, Jun 8, 2026"
        pattern = rf"{re.escape(label)}[\s:]+([A-Za-z]+,\s+[A-Za-z]+\s+\d{{1,2}},\s+\d{{4}}(?:\s+[A-Z]{{2,4}})?)"
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return ""

    @staticmethod
    def _extract_purchase_date(text: str) -> str:
        # e.g. "Purchase date: Sun, Jun 7, 2026, 5:09 PM PDT"
        # or "Purchase date: Sunday, January 12, 2026, 3:45 PM PKT"
        patterns = [
            r"Purchase date[\s:]+([A-Za-z]+,\s+[A-Za-z]+\s+\d{1,2},\s+\d{4},?\s+\d{1,2}:\d{2}\s+[AP]M\s+[A-Z]{2,4})",
            r"Purchase date[\s:]+([A-Za-z]+,\s+[A-Za-z]+\s+\d{1,2},\s+\d{4})",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return ""

    @staticmethod
    def _extract_asin(text: str) -> str:
        m = re.search(r"ASIN[\s:]+([A-Z0-9]{10})", text, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_sku(text: str) -> str:
        m = re.search(r"SKU[\s:]+([A-Z0-9\-]+)", text, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_buyer_name(text: str) -> str:
        # Look for "Contact Buyer: Name" which is reliable on SC order pages
        m = re.search(r"Contact\s+[Bb]uyer[:\s]+([A-Za-z\s\.\-]+?)(?:\n|Address\s+Type|$)", text)
        if m:
            name = m.group(1).strip()
            # Reject if it looks like a status message
            if "feedback" in name.lower() or "submitted" in name.lower() or "not" in name.lower():
                return ""
            return name
        # Fallback: "Ship to" first line
        ship = AmazonScraper._extract_address(text, "Ship to")
        if ship.get("name"):
            return ship["name"]
        return ""

    @staticmethod
    def _extract_buyer_email(text: str) -> str:
        # Amazon masks emails like j***@gmail.com
        m = re.search(r"[a-zA-Z]\*{2,}[a-zA-Z0-9]*@[a-zA-Z0-9\-.]+\.[a-zA-Z]{2,}", text)
        return m.group(0).strip() if m else ""

    @staticmethod
    def _extract_address(text: str, label: str) -> dict:
        """Extract full address block after a label like 'Ship to:' or 'Bill to:'."""
        result = {"name": "", "street": "", "city": "", "state": "", "zip": "", "country": "US"}

        # Find the label and capture lines until a stop marker
        stop_markers = r"Address Type:|Contact Buyer:|Shipping Service|Carrier:|Order contents|Tax Collection"
        pattern = rf"{re.escape(label)}\s*\n\s*\n?(.*?)(?:{stop_markers}|\n\s*\n[A-Z]|$)"
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if not m:
            return result

        block = m.group(1).strip()
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        if not lines:
            return result

        # First line is usually name
        if len(lines) >= 1:
            result["name"] = lines[0]

        # Find the line with City, State ZIP pattern (e.g. "WISCONSIN RAPIDS, WI 54494-5906")
        city_state_zip_idx = -1
        for i, line in enumerate(lines):
            cm = re.match(r"([^,]+),\s*([A-Za-z\s]+)\s+([\d\-]+)", line)
            if cm:
                city_state_zip_idx = i
                result["city"] = cm.group(1).strip()
                result["state"] = cm.group(2).strip()
                result["zip"] = cm.group(3).strip()
                break

        # Lines between name and city/state are street
        if city_state_zip_idx > 1:
            result["street"] = ", ".join(lines[1:city_state_zip_idx])

        return result

    @staticmethod
    def _extract_label(text: str, label: str) -> str:
        # Handle "Payment methods: Standard" (plural) and other variations
        pattern = rf"{re.escape(label)}s?[\s:]+(.+?)(?:\n|$)"
        m = re.search(pattern, text, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_gift_message(text: str) -> str:
        m = re.search(r"[Gg]ift message[\s:]+(.+?)(?:\n\n|$)", text, re.DOTALL)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_money(text: str, label: str) -> float | None:
        # Handle both "$127.36" and "US$127.36"
        pattern = rf"{label}[\s:]+(?:US)?\$?([\d,]+\.\d{{2}})"
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return float(m.group(1).replace(",", ""))
        return None

    @staticmethod
    def _extract_feedback(text: str) -> dict:
        """Look for feedback section on the order page."""
        result = {"rating": None, "text": "", "date": ""}

        # Look for star rating patterns: "5 out of 5 stars" or "5 stars"
        rating_match = re.search(r"(\d)\s*(?:out of\s*5\s*)?[Ss]tars?", text)
        if rating_match:
            result["rating"] = int(rating_match.group(1))

        # Feedback text usually near rating
        if result["rating"]:
            # Look for a sentence after the rating
            text_after = text[text.find(rating_match.group(0)) + len(rating_match.group(0)):]
            lines = [l.strip() for l in text_after.split("\n") if l.strip()]
            for line in lines[:5]:
                if len(line) > 10 and not re.match(r"^\d+\s*stars?$", line, re.IGNORECASE):
                    result["text"] = line
                    break

        # Feedback date
        date_match = re.search(r"[Ff]eedback\s+(?:date|left)[:\s]+([A-Za-z]+,\s+[A-Za-z]+\s+\d{1,2},\s+\d{4})", text)
        if date_match:
            result["date"] = date_match.group(1).strip()

        return result

    @staticmethod
    def _extract_tracking_uploaded(text: str) -> dict:
        """Extract tracking ID that was uploaded to Amazon (not from Zoro email)."""
        result = {"tracking_id_uploaded": "", "tracking_carrier_uploaded": "", "tracking_upload_date": ""}
        
        # Look for "Tracking ID" near shipment confirmation
        # Pattern: "Tracking ID" followed by number
        tid_match = re.search(r"[Tt]racking\s*[Ii][Dd]?[:\s]+([A-Z0-9\-]{8,30})", text)
        if tid_match:
            result["tracking_id_uploaded"] = tid_match.group(1).strip()
        
        # Alternative: look for carrier + tracking in shipment block
        if not result["tracking_id_uploaded"]:
            # FedEx pattern on SC page
            fedex_match = re.search(r"FedEx[:\s]+(\d{12,14})", text)
            if fedex_match:
                result["tracking_id_uploaded"] = fedex_match.group(1)
                result["tracking_carrier_uploaded"] = "FedEx"
        
        # UPS pattern
        if not result["tracking_id_uploaded"]:
            ups_match = re.search(r"UPS[:\s]+(1Z[A-Z0-9]{16})", text)
            if ups_match:
                result["tracking_id_uploaded"] = ups_match.group(1)
                result["tracking_carrier_uploaded"] = "UPS"
        
        # USPS pattern
        if not result["tracking_id_uploaded"]:
            usps_match = re.search(r"USPS[:\s]+(\d{20,22})", text)
            if usps_match:
                result["tracking_id_uploaded"] = usps_match.group(1)
                result["tracking_carrier_uploaded"] = "USPS"
        
        # Carrier detection if not already set
        if result["tracking_id_uploaded"] and not result["tracking_carrier_uploaded"]:
            if re.search(r"\bFedEx\b", text):
                result["tracking_carrier_uploaded"] = "FedEx"
            elif re.search(r"\bUPS\b", text):
                result["tracking_carrier_uploaded"] = "UPS"
            elif re.search(r"\bUSPS\b", text):
                result["tracking_carrier_uploaded"] = "USPS"
        
        # Upload date
        date_match = re.search(r"(?:Ship|Tracking)\s*(?:date|confirmed)[:\s]+([A-Za-z]+,\s+[A-Za-z]+\s+\d{1,2},?\s+\d{4})", text, re.IGNORECASE)
        if date_match:
            result["tracking_upload_date"] = date_match.group(1).strip()
        
        return result

    # -- Batch scrape -----------------------------------------------------------
    def scrape_orders(self, amazon_ids: list[str], delay: float = 2.0) -> dict[str, dict]:
        results = {}
        for oid in amazon_ids:
            results[oid] = self.scrape_order(oid)
            if delay:
                time.sleep(delay)
        return results

    # -- DB save ----------------------------------------------------------------
    def save_to_db(self, data: dict, db_path=None, conn=None):
        """Save scraped data to both closing_orders and amazon_order_details.
        
        If conn is provided, it is used directly (caller must commit).
        """
        from utils.unified_db import upsert_closing_order

        amazon_id = data.get("amazon_order_id", "")
        if not amazon_id:
            logger.warning("save_to_db called without amazon_order_id")
            return

        # Update closing_orders
        closing_updates = {
            "amazon_order_id": amazon_id,
            "scraped_at": data.get("scraped_at", ""),
            "enriched_from_scrape": 1,
        }
        for key in ["purchase_date", "ship_by_date", "deliver_by_date", "asin", "sku",
                       "tracking_id_uploaded", "tracking_carrier_uploaded", "tracking_upload_date"]:
            if data.get(key):
                closing_updates[key] = data[key]
        if len(closing_updates) > 3:
            upsert_closing_order(closing_updates, db_path=db_path, conn=conn)

        # Insert/Update amazon_order_details
        sql = """
            INSERT INTO amazon_order_details (
                amazon_order_id, buyer_name, buyer_email,
                ship_to_name, ship_to_street, ship_to_city, ship_to_state, ship_to_zip, ship_to_country,
                bill_to_name, bill_to_street, bill_to_city, bill_to_state, bill_to_zip, bill_to_country,
                shipping_service, fulfillment_channel, sales_channel, payment_method,
                is_gift, gift_message, order_subtotal, shipping_cost, tax_amount, discount_amount, order_total, currency,
                page_body_snippet, scraped_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(amazon_order_id) DO UPDATE SET
                buyer_name=excluded.buyer_name,
                buyer_email=excluded.buyer_email,
                ship_to_name=excluded.ship_to_name,
                ship_to_street=excluded.ship_to_street,
                ship_to_city=excluded.ship_to_city,
                ship_to_state=excluded.ship_to_state,
                ship_to_zip=excluded.ship_to_zip,
                ship_to_country=excluded.ship_to_country,
                bill_to_name=excluded.bill_to_name,
                bill_to_street=excluded.bill_to_street,
                bill_to_city=excluded.bill_to_city,
                bill_to_state=excluded.bill_to_state,
                bill_to_zip=excluded.bill_to_zip,
                bill_to_country=excluded.bill_to_country,
                shipping_service=excluded.shipping_service,
                fulfillment_channel=excluded.fulfillment_channel,
                sales_channel=excluded.sales_channel,
                payment_method=excluded.payment_method,
                is_gift=excluded.is_gift,
                gift_message=excluded.gift_message,
                order_subtotal=excluded.order_subtotal,
                shipping_cost=excluded.shipping_cost,
                tax_amount=excluded.tax_amount,
                discount_amount=excluded.discount_amount,
                order_total=excluded.order_total,
                currency=excluded.currency,
                page_body_snippet=excluded.page_body_snippet,
                scraped_at=excluded.scraped_at,
                updated_at=CURRENT_TIMESTAMP
        """
        params = (
            amazon_id,
            data.get("buyer_name", ""),
            data.get("buyer_email", ""),
            data.get("ship_to_name", ""),
            data.get("ship_to_street", ""),
            data.get("ship_to_city", ""),
            data.get("ship_to_state", ""),
            data.get("ship_to_zip", ""),
            data.get("ship_to_country", "US"),
            data.get("bill_to_name", ""),
            data.get("bill_to_street", ""),
            data.get("bill_to_city", ""),
            data.get("bill_to_state", ""),
            data.get("bill_to_zip", ""),
            data.get("bill_to_country", "US"),
            data.get("shipping_service", ""),
            data.get("fulfillment_channel", ""),
            data.get("sales_channel", ""),
            data.get("payment_method", ""),
            data.get("is_gift", 0),
            data.get("gift_message", ""),
            data.get("order_subtotal"),
            data.get("shipping_cost"),
            data.get("tax_amount"),
            data.get("discount_amount"),
            data.get("order_total"),
            data.get("currency", "USD"),
            data.get("page_body_snippet", ""),
            data.get("scraped_at", ""),
        )

        if conn is not None:
            conn.execute(sql, params)
            # Feedback
            if data.get("feedback_rating"):
                conn.execute("""
                    INSERT INTO order_feedback (
                        amazon_order_id, feedback_rating, feedback_text, feedback_date, source, scraped_at
                    ) VALUES (?,?,?,?,?,?)
                    ON CONFLICT DO NOTHING
                """, (
                    amazon_id,
                    data.get("feedback_rating"),
                    data.get("feedback_text", ""),
                    data.get("feedback_date", ""),
                    "scrape",
                    data.get("scraped_at", ""),
                ))
        else:
            from utils.unified_db import get_conn
            with get_conn(db_path) as conn:
                conn.execute(sql, params)
                if data.get("feedback_rating"):
                    conn.execute("""
                        INSERT INTO order_feedback (
                            amazon_order_id, feedback_rating, feedback_text, feedback_date, source, scraped_at
                        ) VALUES (?,?,?,?,?,?)
                        ON CONFLICT DO NOTHING
                    """, (
                        amazon_id,
                        data.get("feedback_rating"),
                        data.get("feedback_text", ""),
                        data.get("feedback_date", ""),
                        "scrape",
                        data.get("scraped_at", ""),
                    ))

    # -- Cleanup ----------------------------------------------------------------
    def close(self):
        if self._own_driver and self.driver:
            self.driver.quit()
            logger.info("Closed Chrome driver")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# -- Cache layer --------------------------------------------------------------

def get_cached_scrape(amazon_order_id: str, db_path=None) -> dict | None:
    """Check if we already scraped this order recently (last 7 days)."""
    from utils.unified_db import get_conn
    with get_conn(db_path) as conn:
        row = conn.execute("""
            SELECT scraped_at FROM closing_orders
            WHERE amazon_order_id = ?
              AND scraped_at IS NOT NULL
              AND scraped_at > datetime('now', '-7 days')
        """, (amazon_order_id,)).fetchone()
        if row:
            return {"from_cache": True, "scraped_at": row["scraped_at"]}
    return None


# -- CLI ----------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scrape Amazon SC order detail pages")
    parser.add_argument("order_id", help="Amazon order ID to scrape")
    parser.add_argument("--port", type=int, help="Chrome debugging port")
    parser.add_argument("--profile", help="Chrome profile directory")
    parser.add_argument("--save", action="store_true", help="Save to database")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    with AmazonScraper(port=args.port, profile_dir=args.profile) as scraper:
        result = scraper.scrape_order(args.order_id)
        import json
        print(json.dumps(result, indent=2, default=str))
        if args.save:
            result["amazon_order_id"] = args.order_id
            scraper.save_to_db(result)
            print("Saved to database.")


if __name__ == "__main__":
    sys.exit(main())
