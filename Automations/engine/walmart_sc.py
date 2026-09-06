#!/usr/bin/env python3
"""
walmart_sc.py — Walmart Seller Center (seller.walmart.com) scraper.

Read/scrape + one write action (Start processing) against the SECOND dedicated
Chrome (attach-only, port 9362 by default — accounts.json walmart.sc_port).
This module NEVER launches a browser; callers attach via the same
engine.amazon_scraper._connect_chrome pattern the rest of the stack uses.

Design rules (mirrors the stack's fail-safe conventions):
  • Every scrape returns PARTIAL data plus a '_missing' list instead of
    raising — a half-scraped row is still useful and never kills the run.
  • If the SC Chrome is sitting on a login page, functions return the
    LOGIN_REQUIRED sentinel so callers can print launch/login instructions
    and exit 2 (never a crash, never a blind retry).
  • click_start_processing makes exactly ONE click attempt — placement money
    has already been spent by the time it is called, so a failed click is a
    warning, never an error, and is NEVER retried blindly.

Walmart SC is a React SPA: waits anchor on text ("Unshipped", "Order details",
"Shipping address") after document.readyState=complete plus a hydration pause,
and row scraping is header-driven (column order changes don't break it).

Pure helpers (zoro_code_from_walmart_sku, parsers) import no selenium so the
unit tests in walmart_orders.py --test-units run without a browser.
"""

import re
import time

LOGIN_REQUIRED = "LOGIN_REQUIRED"

SC_HOME = "https://seller.walmart.com"
# The orders route has moved before; try candidates in order and verify by the
# Unshipped/Processing/Shipped tab strip instead of trusting any one URL.
ORDERS_URL_CANDIDATES = (
    SC_HOME + "/orders-management",
    SC_HOME + "/order-management",
    SC_HOME + "/orders",
)
# PO detail route candidates ({po} substituted); the generic fallback is
# clicking the PO link on the Unshipped table itself.
ORDER_DETAIL_URL_CANDIDATES = (
    SC_HOME + "/orders-management/details?poNumber={po}",
    SC_HOME + "/order-management/details?poNumber={po}",
    SC_HOME + "/orders-management/order/{po}",
)

_HYDRATION_WAIT_S = 3.0     # extra settle time after readyState=complete (SPA)
_PAGE_TIMEOUT_S = 30
_MAX_PAGES = 40             # pagination hard stop

# ══════════════════════════════════════════════════════════════════
#  PURE HELPERS (no selenium — unit-testable)
# ══════════════════════════════════════════════════════════════════

# Zoro G-code embedded in a Walmart seller SKU. Samples:
#   'HOA- G1967707 -1'  -> G1967707
#   'HON-G4302365'      -> G4302365
#   'HO- G416487430-6'  -> G416487430   (LONGEST match wins)
_ZORO_CODE_RE = re.compile(r'G\d{6,}')
_MONEY_RE = re.compile(r'\$?\s*([\d,]+\.?\d*)')


def zoro_code_from_walmart_sku(walmart_sku):
    """Extract the Zoro G-code (G + 6+ digits) from a Walmart seller SKU.
    Returns the LONGEST match ('' when none). NOTE: any numeric suffix in the
    SKU (the '-1' / '-6' tails) is NEVER trusted as a qty multiple — the
    Verified_SKU_Dataset sheet is the only multiple authority."""
    matches = _ZORO_CODE_RE.findall(str(walmart_sku or "").upper())
    if not matches:
        return ""
    return max(matches, key=len)


def _money(text):
    """'$28.99' / '28.99' / '$1,028.99' -> float, or None."""
    m = _MONEY_RE.search(str(text or ""))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _labeled_money(body_text, *labels):
    """First '$x.xx' following any of the labels ('Item price', ...) in the
    page text. Case-insensitive, tolerates ':' and whitespace/newlines."""
    for label in labels:
        m = re.search(re.escape(label) + r'\s*:?\s*\$?\s*([\d,]+\.\d{2})',
                      body_text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                continue
    return None


def _labeled_text(body_text, *labels):
    """Value on the same line (or the next line) after 'Label:' in page text."""
    for label in labels:
        m = re.search(re.escape(label) + r'\s*:?[ \t]*([^\n]+)', body_text,
                      re.IGNORECASE)
        if m and m.group(1).strip():
            val = m.group(1).strip().lstrip(':').strip()
            if val:
                return val
        # label alone on its line, value on the next line
        m = re.search(re.escape(label) + r'\s*:?\s*\n[ \t]*([^\n]+)', body_text,
                      re.IGNORECASE)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return ""


_ADDR_STOP_RE = re.compile(
    r'^(subtotal|item price|shipping fee|taxes|tax|total|order details|payment'
    r'|items?|start processing|ship now|qty|fulfillment|delivery)\b',
    re.IGNORECASE)
_CITY_LINE_RE = re.compile(r',\s*[A-Za-z]{2}\s+\d{5}(-\d{4})?\s*$')
_PHONEISH_RE = re.compile(r'^[\d\s()*.+\-]{7,}$')


def extract_shipping_address_block(body_text):
    """Pull the multi-line shipping address that follows the 'Shipping address'
    heading in the PO detail page text. Returns the raw block (name\\nstreet\\n
    City, ST ZIP\\nphone) ready for walmart_orders.parse_walmart_address, or ''.

    Sample section:
        Shipping address
        Andrea Alcorn
        810 south Flower St, 316
        Los Angeles, CA 90017
        (213) 915-2963*
    """
    text = str(body_text or "")
    m = re.search(r'Shipping\s+address\s*:?\s*\n', text, re.IGNORECASE)
    if not m:
        return ""
    lines = []
    saw_city = False
    for raw_line in text[m.end():].splitlines():
        line = raw_line.strip()
        if not line:
            if lines and saw_city:
                break           # blank after the city line = end of block
            if lines:
                continue        # blank inside the block — tolerate one
            continue            # leading blank
        if _ADDR_STOP_RE.match(line):
            break               # next section heading reached
        if saw_city:
            # only a phone-ish line may follow City, ST ZIP
            if _PHONEISH_RE.match(line):
                lines.append(line)
            break
        lines.append(line)
        if _CITY_LINE_RE.search(line):
            saw_city = True
        if len(lines) >= 8:     # runaway guard
            break
    return "\n".join(lines) if len(lines) >= 3 else ""


def parse_unshipped_cells(header_texts, cell_texts):
    """Map one Unshipped-table row (header-driven) to an order dict.
    Pure function so table-shape parsing is unit-testable. Returns the order
    dict with a '_missing' list of fields that came out empty."""
    def col(*keys):
        for i, h in enumerate(header_texts):
            hl = h.lower()
            if any(k in hl for k in keys):
                if i < len(cell_texts):
                    return cell_texts[i].strip()
        return ""

    po_cell = col("po#", "po #", "po number", "purchase order")
    po_number = ""
    if po_cell:
        m = re.search(r'\d{6,}', po_cell)
        po_number = m.group(0) if m else po_cell.splitlines()[0].strip()

    item_cell = col("item", "sku")
    walmart_sku = ""
    qty = None
    if item_cell:
        m = re.search(r'SKU\s*:?\s*([^\n|]+)', item_cell, re.IGNORECASE)
        if m:
            walmart_sku = m.group(1).strip()
        m = re.search(r'Qty\s*:?\s*(\d+)', item_cell, re.IGNORECASE)
        if m:
            qty = int(m.group(1))
    if not walmart_sku and item_cell:
        # item cell without an explicit 'SKU:' label — take a G-code line
        for ln in item_cell.splitlines():
            if _ZORO_CODE_RE.search(ln.upper()):
                walmart_sku = ln.strip()
                break

    order_no_cell = col("order#", "order #", "order number")
    order_number = ""
    if order_no_cell:
        m = re.search(r'[\d-]{6,}', order_no_cell)
        order_number = m.group(0) if m else order_no_cell.strip()

    order = {
        "po_number": po_number,
        "ship_by": col("ship by"),
        "walmart_sku": walmart_sku,
        "qty": qty if qty is not None else 1,
        "order_total": _money(col("order total", "total")),
        "customer_name": col("customer name", "customer"),
        "customer_address": col("customer address", "address"),
        "order_date": col("order date", "date"),
        "order_number": order_number,
    }
    order["zoro_code"] = zoro_code_from_walmart_sku(order["walmart_sku"])
    order["_missing"] = [k for k, v in order.items()
                         if k not in ("_missing",) and (v in ("", None))]
    if qty is None:
        order["_missing"].append("qty")
    return order


# ══════════════════════════════════════════════════════════════════
#  SELENIUM HELPERS (lazy imports so pure helpers need no browser)
# ══════════════════════════════════════════════════════════════════

def _by():
    from selenium.webdriver.common.by import By
    return By


def _wait_ready(driver, timeout=_PAGE_TIMEOUT_S, hydrate=_HYDRATION_WAIT_S):
    """document.readyState complete + SPA hydration pause. Never raises."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if driver.execute_script("return document.readyState") == "complete":
                break
        except Exception:
            pass
        time.sleep(0.5)
    time.sleep(hydrate)


def _body_text(driver):
    try:
        return driver.find_element(_by().TAG_NAME, "body").text or ""
    except Exception:
        return ""


def is_login_page(driver):
    """True when the SC Chrome needs a HUMAN: login/identity page, or
    Walmart's 'Robot or human?' verification wall (seller.walmart.com/blocked).
    Both mean the same thing operationally — the owner must open that Chrome,
    complete the login/verification by hand, and re-run. This code never
    attempts to interact with the verification page."""
    try:
        url = (driver.current_url or "").lower()
    except Exception:
        return True
    if any(k in url for k in ("login", "signin", "sign-in", "sign_in",
                              "authn", "identity", "sso", "account/verify",
                              "/blocked", "captcha")):
        return True
    try:
        By = _by()
        for el in driver.find_elements(By.CSS_SELECTOR, "input[type='password']"):
            if el.is_displayed():
                return True
        title = (driver.title or "").lower()
        if "robot or human" in title:
            return True
    except Exception:
        pass
    return False


def _first_visible(driver, xpath):
    try:
        for el in driver.find_elements(_by().XPATH, xpath):
            try:
                if el.is_displayed():
                    return el
            except Exception:
                continue
    except Exception:
        pass
    return None


def _click(el, driver):
    """Normal click, JS-click fallback. ONE attempt each — never loops."""
    try:
        el.click()
        return True
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", el)
            return True
        except Exception:
            return False


def _goto_orders_page(driver):
    """Navigate to the Orders page and verify by the tab strip. Returns
    ('ok'|'login'|'notfound')."""
    for url in ORDERS_URL_CANDIDATES:
        try:
            driver.get(url)
        except Exception as e:
            print(f"   [SC] nav to {url} failed: {e}")
            continue
        _wait_ready(driver)
        if is_login_page(driver):
            return "login"
        body = _body_text(driver)
        if "Unshipped" in body:
            return "ok"
    # last chance: SC home → maybe an Orders link exists
    try:
        driver.get(SC_HOME)
        _wait_ready(driver)
        if is_login_page(driver):
            return "login"
        link = _first_visible(
            driver, "//a[contains(normalize-space(.), 'Order')]")
        if link and _click(link, driver):
            _wait_ready(driver)
            if is_login_page(driver):
                return "login"
            if "Unshipped" in _body_text(driver):
                return "ok"
    except Exception:
        pass
    return "notfound"


def _click_unshipped_tab(driver):
    tab = _first_visible(
        driver,
        "//*[self::button or self::a or @role='tab']"
        "[starts-with(normalize-space(.), 'Unshipped')]")
    if tab is not None:
        _click(tab, driver)
        _wait_ready(driver, timeout=10, hydrate=2.0)
        return True
    # 'Unshipped (12)' counter renders inside a child span sometimes
    tab = _first_visible(
        driver, "//*[contains(normalize-space(text()), 'Unshipped')]")
    if tab is not None:
        _click(tab, driver)
        _wait_ready(driver, timeout=10, hydrate=2.0)
        return True
    return False


def _bump_items_per_page(driver):
    """Best-effort: raise 'Items per page' to the max option so pagination is
    rarely needed. Silent no-op on any failure."""
    try:
        from selenium.webdriver.support.ui import Select
        By = _by()
        for sel in driver.find_elements(By.TAG_NAME, "select"):
            try:
                if not sel.is_displayed():
                    continue
                s = Select(sel)
                opts = [o.text.strip() for o in s.options]
                numeric = [int(o) for o in opts if o.isdigit()]
                if numeric and any(int(o) >= 25 for o in opts if o.isdigit()):
                    s.select_by_visible_text(str(max(numeric)))
                    _wait_ready(driver, timeout=10, hydrate=2.0)
                    return
            except Exception:
                continue
    except Exception:
        pass


def _scrape_table_page(driver):
    """Scrape every data row of the visible orders table (header-driven).
    Returns (orders, header_texts)."""
    By = _by()
    orders = []
    headers = []
    try:
        tables = [t for t in driver.find_elements(By.TAG_NAME, "table")
                  if t.is_displayed()]
    except Exception:
        tables = []
    for table in tables:
        try:
            ths = table.find_elements(By.XPATH, ".//th")
            hdr = [th.text.strip() for th in ths]
            if not any("po" in h.lower() for h in hdr):
                continue  # not the orders table
            headers = hdr
            for tr in table.find_elements(By.XPATH, ".//tbody//tr"):
                try:
                    cells = [td.text for td in tr.find_elements(By.XPATH, "./td")]
                    if not cells or not any(c.strip() for c in cells):
                        continue
                    order = parse_unshipped_cells(headers, cells)
                    if order["po_number"]:
                        orders.append(order)
                except Exception:
                    continue
            if orders:
                return orders, headers
        except Exception:
            continue
    # role-based grid fallback (React table without <table>)
    try:
        grid_hdr = [el.text.strip() for el in
                    driver.find_elements(By.XPATH, "//*[@role='columnheader']")]
        if any("po" in h.lower() for h in grid_hdr):
            for row in driver.find_elements(
                    By.XPATH, "//*[@role='row'][.//*[@role='cell' or @role='gridcell']]"):
                cells = [c.text for c in row.find_elements(
                    By.XPATH, ".//*[@role='cell' or @role='gridcell']")]
                if not cells or not any(c.strip() for c in cells):
                    continue
                order = parse_unshipped_cells(grid_hdr, cells)
                if order["po_number"]:
                    orders.append(order)
            return orders, grid_hdr
    except Exception:
        pass
    return orders, headers


def _next_page(driver):
    """Click the pagination 'next' control. Returns True when it advanced."""
    btn = _first_visible(
        driver,
        "//button[not(@disabled)][@aria-label='Next page' or @aria-label='next page'"
        " or @aria-label='Next' or contains(normalize-space(.), 'Next')]")
    if btn is None:
        return False
    try:
        if (btn.get_attribute("disabled") or
                (btn.get_attribute("aria-disabled") or "").lower() == "true"):
            return False
    except Exception:
        pass
    if not _click(btn, driver):
        return False
    _wait_ready(driver, timeout=15, hydrate=2.0)
    return True


def scrape_unshipped(driver):
    """Scrape the Orders page Unshipped tab.

    Returns a list of order dicts (each with po_number, ship_by, walmart_sku,
    qty, order_total, customer_name, customer_address, order_date,
    order_number, zoro_code, _missing) — or the LOGIN_REQUIRED sentinel when
    the SC Chrome isn't logged in. Never raises; a partial page yields partial
    rows.
    """
    nav = _goto_orders_page(driver)
    if nav == "login":
        return LOGIN_REQUIRED
    if nav == "notfound":
        print("   [SC] ⚠️ could not find the Orders page (tried "
              f"{len(ORDERS_URL_CANDIDATES)} routes + home link) — 0 rows")
        return []

    if not _click_unshipped_tab(driver):
        print("   [SC] ⚠️ Unshipped tab not clickable — scraping current view")
    _bump_items_per_page(driver)

    all_orders, seen_pos = [], set()
    for page in range(1, _MAX_PAGES + 1):
        orders, headers = _scrape_table_page(driver)
        new = 0
        for o in orders:
            if o["po_number"] in seen_pos:
                continue
            seen_pos.add(o["po_number"])
            all_orders.append(o)
            new += 1
        print(f"   [SC] page {page}: {new} new row(s)"
              + (f" (headers: {', '.join(h for h in headers if h)})"
                 if page == 1 and headers else ""))
        if new == 0 and page > 1:
            break  # pagination looped back / empty page
        if not _next_page(driver):
            break
    return all_orders


def _goto_order_detail(driver, po_number):
    """Open the PO detail page. Returns ('ok'|'login'|'notfound')."""
    po = str(po_number).strip()
    for tpl in ORDER_DETAIL_URL_CANDIDATES:
        try:
            driver.get(tpl.format(po=po))
        except Exception:
            continue
        _wait_ready(driver)
        if is_login_page(driver):
            return "login"
        body = _body_text(driver)
        if po in body and re.search(r'Order\s+details|Shipping\s+address',
                                    body, re.IGNORECASE):
            return "ok"
    # Generic fallback: orders page → click the PO link
    nav = _goto_orders_page(driver)
    if nav == "login":
        return "login"
    if nav == "ok":
        _click_unshipped_tab(driver)
        link = _first_visible(
            driver, f"//a[contains(normalize-space(.), '{po}')]")
        if link is None:
            link = _first_visible(
                driver, f"//*[self::button or self::span]"
                        f"[normalize-space(text())='{po}']")
        if link is not None and _click(link, driver):
            _wait_ready(driver)
            if is_login_page(driver):
                return "login"
            body = _body_text(driver)
            if po in body and re.search(r'Order\s+details|Shipping\s+address',
                                        body, re.IGNORECASE):
                return "ok"
    return "notfound"


def scrape_order_detail(driver, po_number):
    """Scrape one PO detail page.

    Sample sections (user-pasted):
        Order details:  Order# / Ship by / Deliver by / Ordered
        Shipping address:  Name \\n Street \\n City, ST ZIP \\n (phone)*
        Subtotal block:  Item price: $28.99 / Shipping fee: $0.00 /
                         Taxes: $1.74 / Total: $30.73

    Returns dict {po_number, order_number, order_date, ship_by, deliver_by,
    title, walmart_sku, qty, item_price, shipping_fee, tax, order_total,
    address_raw, zoro_code, _missing} — partial on any miss — or
    LOGIN_REQUIRED. address_raw feeds walmart_orders.parse_walmart_address
    unchanged.
    """
    po = str(po_number).strip()
    out = {
        "po_number": po, "order_number": "", "order_date": "",
        "ship_by": "", "deliver_by": "", "title": "", "walmart_sku": "",
        "qty": None, "item_price": None, "shipping_fee": None,
        "tax": None, "order_total": None, "address_raw": "",
        "zoro_code": "", "_missing": [],
    }
    nav = _goto_order_detail(driver, po)
    if nav == "login":
        return LOGIN_REQUIRED
    if nav == "notfound":
        print(f"   [SC] ⚠️ PO {po}: detail page not found")
        out["_missing"] = [k for k in out if k not in ("po_number", "_missing")]
        return out

    body = _body_text(driver)

    out["order_number"] = _labeled_text(body, "Order#", "Order #", "Order number")
    # strip trailing words if the line grabbed extra ("Order#: 123... Ship by ...")
    m = re.search(r'[\d-]{6,}', out["order_number"] or "")
    out["order_number"] = m.group(0) if m else out["order_number"]
    out["ship_by"] = _labeled_text(body, "Ship by")
    out["deliver_by"] = _labeled_text(body, "Deliver by", "Delivery by")
    out["order_date"] = _labeled_text(body, "Ordered", "Order date")

    out["item_price"] = _labeled_money(body, "Item price")
    out["shipping_fee"] = _labeled_money(body, "Shipping fee", "Shipping")
    out["tax"] = _labeled_money(body, "Taxes", "Tax")
    out["order_total"] = _labeled_money(body, "Total")

    sku = _labeled_text(body, "SKU")
    out["walmart_sku"] = sku
    out["zoro_code"] = zoro_code_from_walmart_sku(sku)
    m = re.search(r'Qty\s*:?\s*(\d+)', body, re.IGNORECASE)
    if m:
        out["qty"] = int(m.group(1))

    # Title: the non-label line right above the SKU line, best-effort.
    if sku:
        m = re.search(r'([^\n]{10,})\n[^\n]*SKU\s*:?', body, re.IGNORECASE)
        if m and not _ADDR_STOP_RE.match(m.group(1).strip()):
            out["title"] = m.group(1).strip()

    out["address_raw"] = extract_shipping_address_block(body)

    out["_missing"] = [k for k, v in out.items()
                       if k not in ("_missing", "title") and v in ("", None)]
    return out


def click_start_processing(driver, po_number, qty=None):
    """Press 'Start processing' on the PO page (flow: Ship now → Qty to ship
    pre-filled → 'Start processing'). Returns True/False.

    ONE click attempt on the final button — NEVER retried: the Zoro order is
    already placed when this runs, so the worst case of a missed click is a
    manual 'Start processing' in Seller Center, while a double click could
    double-process. Verifies the status text flips to Processing when
    detectable, but a click that landed without detectable confirmation still
    returns True (the click is the action; verification is best-effort).
    """
    po = str(po_number).strip()
    nav = _goto_order_detail(driver, po)
    if nav == "login":
        print(f"   [SC] ⚠️ PO {po}: login required — Start processing NOT clicked")
        return False
    if nav == "notfound":
        print(f"   [SC] ⚠️ PO {po}: detail page not found — Start processing NOT clicked")
        return False

    start_btn = _first_visible(
        driver, "//button[contains(normalize-space(.), 'Start processing')"
                " or contains(normalize-space(.), 'Start Processing')]")
    if start_btn is None:
        # 'Ship now' step first, then the Start processing button appears
        ship_now = _first_visible(
            driver, "//button[contains(normalize-space(.), 'Ship now')"
                    " or contains(normalize-space(.), 'Ship Now')]")
        if ship_now is not None and _click(ship_now, driver):
            _wait_ready(driver, timeout=10, hydrate=2.0)
            start_btn = _first_visible(
                driver, "//button[contains(normalize-space(.), 'Start processing')"
                        " or contains(normalize-space(.), 'Start Processing')]")
    if start_btn is None:
        print(f"   [SC] ⚠️ PO {po}: no 'Start processing' button found")
        return False

    if not _click(start_btn, driver):       # THE one and only attempt
        print(f"   [SC] ⚠️ PO {po}: 'Start processing' click failed")
        return False
    _wait_ready(driver, timeout=10, hydrate=2.0)

    body = _body_text(driver)
    if re.search(r'\bProcessing\b', body):
        print(f"   [SC] ✅ PO {po}: Start processing clicked (status shows Processing)")
    else:
        print(f"   [SC] ✅ PO {po}: Start processing clicked "
              "(status change not detectable — verify in Seller Center)")
    return True
