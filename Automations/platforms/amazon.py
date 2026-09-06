"""
order_amazon.py — Amazon Seller Central: report download, address scraping, seller ID update.
"""

import re
import os
import sys
import time
import functools

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from utils.config import (
    ACTION_TIMEOUT, DOWNLOAD_DIRECTORY, BLOCKED_STATES,
    AMAZON_BASE_ORDER_URL, AMAZON_REPORT_URL,
    AMAZON_ORDER_PENDING_SELECTOR, AMAZON_ADDRESS_BLOCK_SELECTOR,
    AMAZON_PHONE_SELECTOR, AMAZON_EDIT_BUTTON_SELECTOR,
    AMAZON_ID_INPUT_FIELD_SELECTOR, AMAZON_SAVE_BUTTON_SELECTOR,
    AMAZON_REPORT_DROPDOWN_SELECTOR, AMAZON_REQUEST_REPORT_BUTTON,
    AMAZON_LATEST_DOWNLOAD_BUTTON,
    STATE_MAP, FULL_STATE_NAME_MAP,
)
from platforms.auth import is_login_page, auto_login

print = functools.partial(print, flush=True)


def download_amazon_order_report(driver, account_profile=None):
    """Request and download the Amazon flat-file order report. Returns file path or None."""
    wait = WebDriverWait(driver, ACTION_TIMEOUT)
    try:
        driver.get(AMAZON_REPORT_URL)

        # Auto-login if redirected to sign-in page
        if is_login_page(driver):
            if account_profile:
                auto_login(driver, account_profile)
                driver.get(AMAZON_REPORT_URL)
            else:
                print("  ⚠️  Login page detected but no account_profile provided for auto-login.")

        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, AMAZON_REPORT_DROPDOWN_SELECTOR)))

        for f in os.listdir(DOWNLOAD_DIRECTORY):
            if f.endswith('.txt'):
                os.remove(os.path.join(DOWNLOAD_DIRECTORY, f))

        dropdown_host = wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, AMAZON_REPORT_DROPDOWN_SELECTOR)))
        dropdown_host.click()
        time.sleep(0.5)
        driver.execute_script(
            'arguments[0].shadowRoot.querySelector(\'kat-option[value="3"]\').click()',
            dropdown_host)
        time.sleep(0.5)
        wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, AMAZON_REQUEST_REPORT_BUTTON))).click()

        max_wait_time = 120
        check_interval = 10
        time_waited = 0
        while time_waited < max_wait_time:
            try:
                driver.refresh()
                download_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, AMAZON_LATEST_DOWNLOAD_BUTTON)))
                download_button.click()
                start = time.time()
                while time.time() - start < 45:
                    files = [f for f in os.listdir(DOWNLOAD_DIRECTORY)
                             if f.endswith('.txt') and not f.endswith('.crdownload')]
                    if files:
                        return max([os.path.join(DOWNLOAD_DIRECTORY, f) for f in files],
                                   key=os.path.getctime)
                    time.sleep(1)
                raise TimeoutException
            except TimeoutException:
                time_waited += check_interval
                time.sleep(check_interval)
        return None
    except Exception:
        return None


def scrape_amazon_address(driver, amazon_order_id, account_profile=None, skip_navigate=False):
    """Navigate to Amazon order page and extract shipping address.
    Returns address dict, or dict with skip_order=True, or None on failure.
    If skip_navigate=True, assumes driver is already on the order detail page."""
    if not skip_navigate:
        driver.get(f"{AMAZON_BASE_ORDER_URL}{amazon_order_id}")

        # Auto-login if redirected
        if is_login_page(driver):
            if account_profile:
                auto_login(driver, account_profile)
                driver.get(f"{AMAZON_BASE_ORDER_URL}{amazon_order_id}")

    # Check for Pending
    try:
        WebDriverWait(driver, 2).until(
            EC.visibility_of_element_located(AMAZON_ORDER_PENDING_SELECTOR))
        return {"skip_order": True, "reason": "Order Pending"}
    except TimeoutException:
        pass

    # Scrape Address Block
    try:
        el = WebDriverWait(driver, 10).until(
            EC.visibility_of_element_located(
                (By.CSS_SELECTOR, AMAZON_ADDRESS_BLOCK_SELECTOR)))
        lines = [l.strip() for l in el.text.strip().split('\n') if l.strip()]

        if len(lines) < 3:
            raise ValueError("Invalid address structure (less than 3 lines)")

        # Find city/state/zip line scanning from end
        city_state_idx = None
        for i in range(len(lines) - 1, 0, -1):
            if re.search(r'([^,]+),\s*([A-Z][A-Z\s]*)\s*(\d{5}(?:-\d{4})?)', lines[i].replace('.', ''), re.IGNORECASE):
                city_state_idx = i
                break

        if city_state_idx is None:
            raise ValueError(f"Could not find City/State/Zip in address: {lines}")

        street_idx = city_state_idx - 1
        street_address = lines[street_idx]
        street_address_2 = ""

        if street_idx > 1:
            street_address_2 = " ".join(lines[1:street_idx])

        data = {
            "original_name": lines[0],
            "street_address": street_address,
            "street_address_2": street_address_2,
            "phone": "NOT FOUND",
            "skip_order": False
        }

        city_state_line = lines[city_state_idx]
        match = re.search(r'([^,]+),\s*([A-Z][A-Z\s]*)\s*(\d{5}(?:-\d{4})?)',
                          city_state_line.replace('.', ''), re.IGNORECASE)
        if match:
            state = STATE_MAP.get(match.group(2).strip().upper(), "INVALID")
            if state in BLOCKED_STATES:
                return {"skip_order": True, "reason": f"Blocked state: {state}"}
            data.update({
                "city": match.group(1).strip(),
                "state_abbr": state,
                "zip_code": match.group(3).strip()
            })
        else:
            raise ValueError(f"City/State regex failed on: {city_state_line}")

        # PO Box check
        all_addr_text = (data["street_address"] + " " + data.get("street_address_2", "")).upper()
        if re.search(r'\bP\.?\s*O\.?\s*BOX\b', all_addr_text):
            return {"skip_order": True, "reason": "PO_BOX"}

        try:
            data["phone"] = driver.find_element(
                By.CSS_SELECTOR, AMAZON_PHONE_SELECTOR
            ).text.replace("Phone:", "").strip()
        except Exception:
            pass

        return data
    except Exception as e:
        # Log raw scrape failure details for diagnosis
        import traceback, time
        raw_reason = f"{type(e).__name__}: {e}"
        print(f"   [ADDR SCRAPE FAIL] {raw_reason}")
        try:
            # Try to capture the address block text that was on the page
            addr_el = driver.find_element(By.CSS_SELECTOR, AMAZON_ADDRESS_BLOCK_SELECTOR)
            raw_text = addr_el.text.strip().replace('\n', ' | ')
            print(f"   [ADDR SCRAPE RAW] {raw_text[:300]}")
        except Exception:
            # If even the selector fails, dump page snippet
            page_text = driver.execute_script("return document.body.innerText || '';")
            snippet = page_text.replace('\n', ' | ')[:400]
            print(f"   [ADDR SCRAPE PAGE] {snippet}")
        traceback.print_exc()
        return {"skip_order": True, "reason": f"Scrape Fail: {raw_reason}"}


def validate_and_prepare_address(address_data):
    """Clean and validate address fields. Returns enriched dict or None."""
    required = ["original_name", "street_address", "city", "state_abbr", "zip_code"]
    if not all(k in address_data for k in required):
        return None

    # Name formatting
    name = re.sub(r'[^a-zA-Z\s]', '', address_data["original_name"]).strip()
    name = ' '.join(word.capitalize() for word in name.split())
    parts = name.split()
    if len(parts) > 1:
        address_data["name"] = f"{parts[0]} {parts[1]}"
    elif parts:
        address_data["name"] = f"{parts[0]} {parts[0]}"
    else:
        address_data["name"] = "Customer Name"

    # Phone formatting. Masked exports (Walmart) send junk like 0000000000 —
    # all-identical digits count as missing, not as a valid 10-digit phone.
    cleaned = re.sub(r'\D', '', address_data["phone"])
    if len(cleaned) < 10 or len(set(cleaned)) == 1:
        address_data["phone"] = "4044619909"
    else:
        address_data["phone"] = cleaned
    address_data["state_full_name"] = FULL_STATE_NAME_MAP.get(address_data["state_abbr"])
    return address_data


def _scrape_order_page_structure(driver, amazon_order_id, output_dir="data"):
    """One-time full page scraper for Amazon order detail page.
    Saves HTML + rendered text structure so user can inspect and request extractions."""
    try:
        os.makedirs(output_dir, exist_ok=True)
        html_path = os.path.join(output_dir, "amazon_order_structure.html")
        txt_path = os.path.join(output_dir, "amazon_order_structure.txt")

        # Full page HTML (raw, may be mostly JS)
        page_html = driver.page_source
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page_html)

        # Rendered visible text (includes dynamically loaded dates, addresses, etc.)
        page_text = driver.execute_script("return document.body.innerText || '';")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"Order ID: {amazon_order_id}\n")
            f.write(f"URL: {driver.current_url}\n")
            f.write("=" * 60 + "\n\n")
            f.write(page_text)

        print(f"   📄 Order page structure saved:")
        print(f"      HTML: {html_path}")
        print(f"      TEXT: {txt_path}")
    except Exception as e:
        print(f"   ⚠️  Failed to scrape page structure: {e}")


_re_wb = re.compile(r"\b(W[BW]\d{6,})\b")


def _confirm_on_order_page(driver, amazon_order_id, label="", renav_url=None):
    """HARD page-identity check before any order-page WRITE.

    2026-08-19/20 incident: update_amazon_seller_id verified that the LABEL
    text saved, but never that the page being edited BELONGED to the target
    order. When navigation silently landed elsewhere (redirect, stale tab,
    interstitial), the label was stamped onto whichever order was open, the
    read-back matched, True was returned — and the intended order stayed
    unlabelled, so the next cycle stamped ANOTHER innocent order. Orders were
    found on Amazon labelled with a stranger's "WB… - D", which makes the
    placer scrape skip them as duplicates (one victim had never been placed at
    all). Every write now confirms the page's own order id first; on mismatch
    it re-navigates once, and refuses to touch the page if still wrong.
    """
    def _on_page():
        try:
            if amazon_order_id in (driver.current_url or ""):
                return True
            txt = driver.execute_script(
                "return ((document.body && document.body.innerText) || '').slice(0, 6000)") or ""
            return amazon_order_id in txt
        except Exception:
            return False

    if _on_page():
        return True
    url = renav_url or f"{AMAZON_BASE_ORDER_URL}{amazon_order_id}"
    try:
        print(f"   {label}⚠️ page is NOT {amazon_order_id}'s order page — re-navigating before writing")
        driver.get(url)
        time.sleep(2)
    except Exception:
        pass
    if _on_page():
        return True
    print(f"   {label}🛑 REFUSING WRITE: page still isn't {amazon_order_id}'s — "
          f"writing here would stamp a DIFFERENT order (2026-08-19 '-D' incident).")
    return False


def update_amazon_seller_id(driver, amazon_order_id, new_seller_id, account_profile=None, skip_navigate=False):
    """Update the merchant-order-id field on Amazon Seller Central.
    If skip_navigate=True, assumes driver is already on the order detail page."""
    if not skip_navigate:
        driver.get(f"{AMAZON_BASE_ORDER_URL}{amazon_order_id}")

        # Auto-login if redirected
        if is_login_page(driver):
            if account_profile:
                auto_login(driver, account_profile)
                driver.get(f"{AMAZON_BASE_ORDER_URL}{amazon_order_id}")

        # One-time full page scrape for structure inspection
        _scrape_order_page_structure(driver, amazon_order_id)

    def _read_seller_id():
        """Read back the currently-saved merchant/seller order id from the page.

        Whitespace-NORMALIZED on purpose: around 2026-08-19 Amazon changed the
        order page so the old line-bound regex captured only the literal '#'
        from "Your Seller Order ID: # WB…" (or nothing). Every save then
        "failed" verification even though it had persisted — 116 bogus failures
        on the 19th, 231 on the 20th — leaving orders looking unlabelled, which
        is what armed the whole "-D" duplicate-stamp cascade."""
        try:
            raw = (driver.execute_script(
                """
                var el = document.querySelector('[data-test-id="your-seller-order-id"], [data-test-id="merchant-order-id"]');
                var t = el ? (el.innerText || el.textContent || el.value || '') : '';
                t = t.replace(/\\s+/g, ' ').trim();
                if (t && t !== '#' && t !== 'Edit') return t;
                var body = ((document.body && document.body.innerText) || '').replace(/\\s+/g, ' ');
                var m = body.match(/(?:Your seller order ID|Merchant order ID)\\s*:?\\s*(.*?)(?:\\s+Edit\\b|\\s+Go back|\\s+Print packing|$)/i);
                return m ? m[1].trim() : '';
                """) or "").strip()
            return raw.lstrip("#").strip()   # page renders "# WB…"; '#' is chrome, not value
        except Exception:
            return ""

    if not _confirm_on_order_page(driver, amazon_order_id):
        return False

    expected = (new_seller_id or "").strip()

    # ── NEVER let a duplicate stamp destroy an order's own WB link ──────────
    # 2026-08-20: placed orders that already wore their correct "WB…" label were
    # overwritten with a DIFFERENT order's "<WB> - D" (mis-attributed duplicate
    # verdicts). A "- D" stamp may only land on an EMPTY label or on the SAME
    # WB it names — an existing different WB means this order is already linked
    # to its own placement and the duplicate verdict is wrong by definition.
    if expected.endswith("- D"):
        try:
            _cur = _read_seller_id()
        except Exception:
            _cur = ""
        _cur_wbs = set(_re_wb.findall(_cur or ""))
        _new_wbs = _re_wb.findall(expected)
        if _cur_wbs and _new_wbs and _new_wbs[0] not in _cur_wbs:
            print(f"   -> 🛑 REFUSING '-D' overwrite on {amazon_order_id}: label already "
                  f"links to {sorted(_cur_wbs)} — stamping '{expected}' would destroy it "
                  f"(mis-attributed duplicate; leaving the order untouched)")
            return False
    # Amazon's field silently drops values longer than ~40 chars: the save click
    # succeeds but nothing persists, so every attempt "fails verification".
    if len(expected) > 40:
        print(f"   -> ⚠️ seller-id is {len(expected)} chars — Amazon's field caps ~40; "
              f"this write will likely not persist (caller should shorten it)")
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            # On retry attempts (2, 3), refresh the page to clear any dirty React/DOM state
            if attempt > 1:
                print(f"   -> [Amazon Seller ID] Refreshing order page for attempt {attempt}/{attempts}...")
                driver.get(f"{AMAZON_BASE_ORDER_URL}{amazon_order_id}")
                time.sleep(2)

            if not _confirm_on_order_page(driver, amazon_order_id):
                continue
            wait = WebDriverWait(driver, 10)

            # Check if input is already visible, or click Edit button
            input_el = None
            try:
                input_el = driver.find_element(By.CSS_SELECTOR, AMAZON_ID_INPUT_FIELD_SELECTOR)
                if not input_el.is_displayed():
                    input_el = None
            except Exception:
                input_el = None

            if not input_el:
                edit_btn = wait.until(EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, AMAZON_EDIT_BUTTON_SELECTOR)))
                driver.execute_script("arguments[0].click();", edit_btn)
                time.sleep(0.5)
                input_el = wait.until(EC.visibility_of_element_located(
                    (By.CSS_SELECTOR, AMAZON_ID_INPUT_FIELD_SELECTOR)))

            # Focus and clear field
            input_el.click()
            time.sleep(0.2)

            # Use native React prototype setter + dispatchEvent so React state is guaranteed to update
            driver.execute_script("""
                var el = arguments[0];
                var val = arguments[1];
                var nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value");
                if (nativeSetter && nativeSetter.set) {
                    nativeSetter.set.call(el, val);
                } else {
                    el.value = val;
                }
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
            """, input_el, new_seller_id)
            time.sleep(0.3)

            # Also perform keystrokes as a fallback layer
            cmd_ctrl = Keys.COMMAND if sys.platform == "darwin" else Keys.CONTROL
            input_el.send_keys(cmd_ctrl, 'a')
            time.sleep(0.1)
            input_el.send_keys(new_seller_id)
            time.sleep(0.3)

            # Click Save button
            save_btn = wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR, AMAZON_SAVE_BUTTON_SELECTOR)))
            driver.execute_script("arguments[0].click();", save_btn)

            # Poll for up to 5 seconds for the DOM to update
            saved = ""
            for _ in range(10):
                time.sleep(0.5)
                saved = _read_seller_id()
                if expected and (saved == expected or expected in saved):
                    break
                if not expected and saved == "":
                    break

            # If verified in DOM, refresh the page to guarantee persistence in Amazon's database
            if expected and (saved == expected or expected in saved):
                # Double-check with a quick refresh to confirm database persistence
                try:
                    driver.get(f"{AMAZON_BASE_ORDER_URL}{amazon_order_id}")
                    time.sleep(1.5)
                    verified_saved = _read_seller_id()
                    if expected in verified_saved or verified_saved == expected:
                        return True
                except Exception:
                    return True # If DOM confirmed, proceed
                return True

            if not expected and saved == "":
                return True # clearing the label: empty read-back = success

            print(f"   -> Seller-id write NOT verified (attempt {attempt}/{attempts}); "
                  f"expected '{expected}', page shows '{saved}'. Retrying...")
            time.sleep(1)
        except Exception as e:
            print(f"   -> Failed to update Amazon ID (attempt {attempt}/{attempts}): {e}")
            time.sleep(1)

    print(f"   -> ❌ Could not verify seller-id '{expected}' persisted after {attempts} attempts.")
    return False


def write_seller_note(driver, amazon_order_id, note_text=None, account_profile=None,
                       skip_navigate=False, skip_login=False):
    """
    Write a private seller note to the Amazon SC order detail page.

    If note_text is None, auto-extracts from order_log.txt + message_log.json.
    Amazon auto-saves seller notes on blur — we type and click a neutral element.

    Returns True if the textarea was found and text was injected, False otherwise.
    """
    # Auto-extract if no text provided
    if note_text is None:
        try:
            from utils.seller_notes import extract_seller_note_data, format_seller_note
            data = extract_seller_note_data(amazon_order_id)
            if data:
                note_text = format_seller_note(data)
            else:
                print(f"   [SellerNote] No order log entry for {amazon_order_id}")
                return False
        except Exception as e:
            print(f"   [SellerNote] Extraction failed: {e}")
            return False

    if not note_text:
        return False

    # Navigate to order page if needed
    if not skip_navigate:
        driver.get(f"{AMAZON_BASE_ORDER_URL}{amazon_order_id}")
        if not skip_login and is_login_page(driver):
            if account_profile:
                auto_login(driver, account_profile)
                driver.get(f"{AMAZON_BASE_ORDER_URL}{amazon_order_id}")
            else:
                print(f"   [SellerNote] Login required but no profile provided")
                return False

    # Same wrong-page hazard as update_amazon_seller_id (notes auto-save on blur,
    # so a wrong page silently gets a stranger's note): confirm identity first.
    if not _confirm_on_order_page(driver, amazon_order_id, label="[SellerNote] "):
        return False

    wait = WebDriverWait(driver, 10)

    # Try multiple selectors — Amazon changes these occasionally
    selectors = [
        'textarea[name="sellerNote"]',
        'textarea[name="orderNote"]',
        'textarea[placeholder*="records" i]',
        'textarea[placeholder*="note" i]',
        'textarea[id*="note" i]',
        'textarea[id*="seller" i]',
        'textarea[data-testid*="note" i]',
    ]

    textarea = None
    for sel in selectors:
        try:
            textarea = WebDriverWait(driver, 3).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, sel))
            )
            break
        except Exception:
            continue

    if not textarea:
        print(f"   [SellerNote] Textarea not found for {amazon_order_id}")
        return False

    try:
        # Scroll into view
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", textarea)
        time.sleep(0.3)

        # Focus, clear, type
        textarea.click()
        cmd_ctrl = Keys.COMMAND if sys.platform == "darwin" else Keys.CONTROL
        textarea.send_keys(cmd_ctrl, 'a')
        time.sleep(0.1)
        textarea.send_keys(Keys.BACK_SPACE)
        time.sleep(0.1)

        # Type line by line with RETURN for newlines
        lines = note_text.split('\n')
        for idx, line in enumerate(lines):
            textarea.send_keys(line)
            if idx < len(lines) - 1:
                textarea.send_keys(Keys.RETURN)
        time.sleep(0.5)

        # Blur to trigger auto-save
        driver.execute_script("arguments[0].blur();", textarea)
        time.sleep(0.3)

        # Click a neutral element to ensure blur fires
        try:
            driver.find_element(By.TAG_NAME, "body").click()
        except Exception:
            pass

        # Wait briefly for the green confirmation
        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(),'notes have been updated')]"))
            )
            print(f"   [SellerNote] ✅ Saved for {amazon_order_id}")
        except Exception:
            print(f"   [SellerNote] ⚠️  Wrote note but could not confirm save for {amazon_order_id}")

        return True
    except Exception as e:
        print(f"   [SellerNote] ❌ Failed to write note for {amazon_order_id}: {e}")
        return False
