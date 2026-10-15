"""
order_zoro.py — Zoro.com API layer: inventory, cart, address CRUD, restriction handling.
All operations use in-browser fetch() via Selenium — no direct HTTP requests.
"""

import re
import json
import time
import random
import functools

from selenium.webdriver.common.by import By

from utils.config import RESTRICTION_QUICK_WAITS
from utils.helpers import reapply_stealth, safe_get, simulate_human

print = functools.partial(print, flush=True)


# ==============================================================================
#  EXCEPTIONS
# ==============================================================================

class ZoroAccountRestricted(Exception):
    """Raised when Zoro account shows 'Access is temporarily restricted'."""
    pass


# ==============================================================================
#  CORE API
# ==============================================================================

def zoro_api(driver, method, endpoint, body=None, content_type='application/json'):
    """Execute Zoro API call via in-browser fetch(). Driver must be on zoro.com."""
    result = driver.execute_script("""
        var url=arguments[0],method=arguments[1],bodyStr=arguments[2],ct=arguments[3];
        var token;
        try{token=JSON.parse(localStorage.getItem('okta-token-storage')).accessToken.accessToken;}
        catch(e){return{s:-1,d:'no_token'};}
        return new Promise(function(resolve){
            var h={'Accept':'application/json,text/plain,*/*','Authorization':'Bearer '+token,
                   'apikey':'924526ffbdad25e5923b'};
            if(ct)h['Content-Type']=ct;
            var opts={method:method,headers:h};
            if(bodyStr)opts.body=bodyStr;
            fetch(url,opts).then(function(r){
                var st=r.status;
                return r.text().then(function(t){
                    try{return{s:st,d:JSON.parse(t)};}catch(e){return{s:st,d:t.substring(0,1500)};}
                });
            }).then(resolve).catch(function(e){resolve({s:-1,d:e.message});});
        });
    """, f"https://api.prod.zoro.com{endpoint}", method,
         json.dumps(body) if body else None, content_type)
    status = result.get('s', -1) if isinstance(result, dict) else -1
    # DEBUG: log non-200 responses so we can diagnose API failures
    if status != 200:
        preview = str(result.get('d', ''))[:300] if isinstance(result, dict) else str(result)[:300]
        print(f"   [ZORO API] ⚠️  {method} {endpoint} → HTTP {status} | preview: {preview}")
    return result


# ==============================================================================
#  RESTRICTION DETECTION & RECOVERY
# ==============================================================================

def is_restricted(driver):
    """Check if current page shows restriction/unusual-activity text."""
    try:
        page_text = driver.execute_script("return document.body.innerText || '';")
        restricted = ('temporarily restricted' in page_text.lower() or
                      'unusual activity' in page_text.lower())
        return restricted
    except Exception:
        return False


def clear_datadome(driver):
    """Clear DataDome tracking cookies to reset session fingerprint."""
    try:
        if 'zoro.com' not in driver.current_url:
            driver.get("https://www.zoro.com")
            time.sleep(3)
        # We no longer clear DataDome/tracking cookies because clearing
        # cookies risks invalidating the shared Amazon/Zoro Okta sessions.
        print("   [RESET] Skipped clearing DataDome cookies to preserve sessions.")
        return True
    except Exception as e:
        print(f"   [RESET] Cookie clear error: {e}")
        return False


def reset_restriction(driver):
    """Try to recover from restriction with escalating resets.
    Returns True if restriction cleared, False otherwise."""
    for attempt, wait_time in enumerate(RESTRICTION_QUICK_WAITS):
        if attempt == 0:
            print(f"   [RESET] Attempt {attempt+1}: Clearing DataDome cookies, waiting {wait_time}s...")
            clear_datadome(driver)
        else:
            print(f"   [RESET] Attempt {attempt+1}: Clearing ALL cookies, waiting {wait_time}s...")
            # Previously called driver.delete_all_cookies() here.
            # We skip this now to avoid wiping out Amazon SC sessions.
            pass

        reapply_stealth(driver)
        time.sleep(wait_time)

        driver.get("https://www.zoro.com")
        time.sleep(3)
        simulate_human(driver, intensity="full")

        driver.get("https://www.zoro.com")
        time.sleep(4)
        reapply_stealth(driver)

        if not is_restricted(driver):
            print(f"   [RESET] ✅ Restriction cleared after attempt {attempt+1}!")
            return True
        print(f"   [RESET] ❌ Still restricted after attempt {attempt+1}")
    return False


# ==============================================================================
#  SESSION MANAGEMENT
# ==============================================================================

def ensure_on_site(driver):
    """Ensure driver is on a clean zoro.com page for API calls.
    Re-applies stealth on every navigation. Self-healing on restriction."""
    url = driver.current_url
    if 'zoro.com' not in url or 'checkout' in url or 'confirmation' in url:
        safe_get(driver, "https://www.zoro.com")      # read-only; a page that never finishes loading is still used
        time.sleep(4)
        reapply_stealth(driver)
    try:
        if is_restricted(driver):
            print("   [ZORO] ⚠ Restriction detected — attempting session reset...")
            if reset_restriction(driver):
                print("   [ZORO] ✅ Session recovered — continuing...")
                return
            raise ZoroAccountRestricted("Zoro account restricted — all quick resets failed")
    except ZoroAccountRestricted:
        raise
    except Exception:
        pass


def ensure_token(driver):
    """Refresh Okta token if near expiry."""
    ttl = driver.execute_script("""
        try{var s=JSON.parse(localStorage.getItem('okta-token-storage'));
        return s.accessToken.expiresAt-Math.floor(Date.now()/1000);}catch(e){return -1;}
    """)
    if ttl < 60:
        print("   [ZORO] Refreshing token...")
        driver.execute_script("""
            var app=document.querySelector('#app');
            if(app&&app.__vue_app__)
                app.__vue_app__.config.globalProperties.$store.dispatch('auth/checkIfAuthenticated');
        """)
        time.sleep(4)


# ==============================================================================
#  INVENTORY
# ==============================================================================

_LAST_AVAIL = {}   # sku -> last availability dict (so callers can read stock without re-calling)


def check_inventory(driver, sku, qty):
    """Check stock via API. Returns (is_cartable, status_text)."""
    r = zoro_api(driver, "PUT", "/scm/v1/inventory/availability",
                 [{"zoroNo": sku, "requestedQuantity": qty}])
    if r.get('s') != 200:
        status = r.get('s', 'unknown')
        detail = r.get('d', '')
        print(f"   [ZORO INV] check_inventory failed for {sku} — HTTP {status}, detail: {str(detail)[:200]}")
        return False, "API_ERROR"
    payload = r['d'].get('payload', [])
    if not payload:
        return False, "NO_DATA"
    avail = payload[0]
    try:
        _LAST_AVAIL[sku] = avail          # cache for last_inventory_qty()
    except Exception:
        pass
    # One-shot field discovery: with ZORO_INV_DEBUG=1, dump the FULL availability object
    # so we can see Zoro's lead-time / ship-estimate / stock-quantity field names and then
    # wire them permanently. Default off → zero behavior change.
    if __import__("os").environ.get("ZORO_INV_DEBUG") == "1":
        try:
            print(f"   [ZORO INV RAW] {sku}: {__import__('json').dumps(avail, default=str)}")
        except Exception:
            print(f"   [ZORO INV RAW] {sku}: {avail}")
    return avail.get('isCartable', False), avail.get('availabilityStatus', 'Unknown')


def last_inventory_qty(sku):
    """Zoro inventoryQuantity from the most recent check_inventory(sku). None when ample
    (Zoro reports a number only when stock is low) → that's exactly when we want to flag it."""
    return (_LAST_AVAIL.get(sku) or {}).get('inventoryQuantity')


_DELIV_RE1 = re.compile(r"font-weight-cart[^>]*>\s*Delivery (?:on or before|by) (?:tomorrow,?\s*)?([A-Z][a-z]{2,9}\.?\s+\d{1,2})")
_DELIV_RE2 = re.compile(r"Delivery on or before (?:tomorrow,?\s*)?([A-Z][a-z]{2,9}\.?\s+\d{1,2})")
# Sep 2026 wording: "Delivery by Thursday, September 24" — a weekday before the month. The two
# patterns above never matched it, so only 257 of 5,382 orders in the week to 21 Sep carried a
# Zoro delivery date and the late-delivery alert could not fire.
_DELIV_RE3 = re.compile(r"Delivery (?:on or before|by) (?:tomorrow,?\s*)?"
                        r"(?:(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s+)?([A-Z][a-z]{2,9}\.?\s+\d{1,2})")


def _parse_deliv(s):
    import datetime
    today = datetime.date.today()
    for fmt in ("%B %d", "%b %d"):
        try:
            d = datetime.datetime.strptime(s.strip().rstrip('.'), fmt).date().replace(year=today.year)
            if d < today - datetime.timedelta(days=2):   # month rolled into next year
                d = d.replace(year=today.year + 1)
            return d.isoformat()
        except ValueError:
            continue
    return ""


def get_zoro_delivery_date(driver, sku):
    """Best-effort: load the Zoro PDP for `sku` and scrape 'Delivery on or before <date>'
    → 'YYYY-MM-DD'. Fully fail-safe (returns '' on any error). NAVIGATES the driver, so the
    caller must only call this where moving the page is safe (before the cart flow)."""
    try:
        safe_get(driver, f"https://www.zoro.com/search?q={sku}")
        time.sleep(4)
        if "/i/" not in (driver.current_url or ""):
            links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/i/']")
            if links:
                safe_get(driver, links[0].get_attribute("href"))
                time.sleep(4)
        # The delivery line is rendered client-side, and on a slow Chrome it is not there after
        # 4 s: from 14 Oct 2026 every HS_COUPON / HS_RESELLER order (652) was placed with no Zoro
        # date, so the delivery check never ran. Wait for it (up to 15 s more), then read.
        html, m = "", None
        deadline = time.time() + 15
        while True:
            html = driver.page_source or ""
            m = _DELIV_RE1.search(html) or _DELIV_RE2.search(html) or _DELIV_RE3.search(html)
            if m or time.time() >= deadline:
                break
            time.sleep(1)
        _record_pdp_promo(sku, html)                 # free: same rendered page
        if not m:
            print(f"  ⚠️ Zoro product page for {sku}: no delivery date after waiting")
        return _parse_deliv(m.group(1)) if m else ""
    except Exception as e:
        print(f"  ⚠️ get_zoro_delivery_date({sku}) failed: {e}")
        return ""


# ── PDP promo eligibility ────────────────────────────────────────────────────
# The product page shows "Zoro's best price: not eligible for any other offers or
# promotions" on items no coupon will ever touch. The badge is rendered client-side
# (it is NOT in the server HTML), so it can only be read from a RENDERED page.
# Result is cached per SKU on disk → one page load per SKU, ever, across placers.
PROMO_INELIGIBLE_PHRASE = "not eligible for any other offers or promotions"
_PDP_PROMO_MAX_AGE_DAYS = 30


def _pdp_promo_path():
    import os
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "dataset", "pdp_promo.json")


def _load_pdp_promo():
    try:
        with open(_pdp_promo_path(), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


_pdp_promo = _load_pdp_promo()      # sku -> {"ineligible": bool, "at": "YYYY-MM-DD HH:MM"}


def _save_pdp_promo():
    """Merge this process's badge memory into the shared file under a cross-process
    lock (four placers write it; a plain merge-and-replace lost the others' entries)."""
    try:
        from utils.jsonstore import mutate_json
        with mutate_json(_pdp_promo_path(), {}) as merged:
            merged.update(_pdp_promo)
    except Exception:
        pass


def _record_pdp_promo(sku, html):
    """Read the badge from a rendered PDP. Only trusts a fully rendered page (the
    buy box must be there), so a half-loaded page never records a false 'eligible'."""
    if not sku or not html:
        return None
    low = html.lower()
    if "add to cart" not in low and "subtotal" not in low:
        return None
    flag = PROMO_INELIGIBLE_PHRASE in low
    _pdp_promo[sku] = {"ineligible": bool(flag), "at": time.strftime("%Y-%m-%d %H:%M")}
    _save_pdp_promo()
    if flag:
        print(f"   [ZORO] 🚫 {sku}: Zoro's best price — not eligible for any offers or promotions")
    return flag


def pdp_promo_ineligible(sku):
    """Cached verdict for a SKU: True / False, or None when unknown or stale."""
    rec = _pdp_promo.get(sku) or _load_pdp_promo().get(sku)
    if not rec:
        return None
    try:
        age = time.time() - time.mktime(time.strptime(rec.get("at", "")[:16], "%Y-%m-%d %H:%M"))
        if age > _PDP_PROMO_MAX_AGE_DAYS * 86400:
            return None
    except Exception:
        pass
    return bool(rec.get("ineligible"))


def check_pdp_promo(driver, sku):
    """True if the PDP carries the 'not eligible for any other offers or promotions'
    badge, False if not, None if the page could not be read. NAVIGATES the driver
    (like get_zoro_delivery_date) unless the SKU is already cached, so call it only
    where moving the page is safe (before the cart flow)."""
    cached = pdp_promo_ineligible(sku)
    if cached is not None:
        return cached
    try:
        safe_get(driver, f"https://www.zoro.com/search?q={sku}")
        time.sleep(3)
        if "/i/" not in (driver.current_url or ""):
            links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/i/']")
            if links:
                safe_get(driver, links[0].get_attribute("href"))
                time.sleep(3)
        html = ""
        for _ in range(8):                        # wait for the buy box / badge to render
            html = driver.page_source or ""
            if "add to cart" in html.lower():
                break
            time.sleep(1)
        time.sleep(1.5)                           # the promo block renders just after the price
        return _record_pdp_promo(sku, driver.page_source or html)
    except Exception as e:
        print(f"  ⚠️ check_pdp_promo({sku}) failed: {e}")
        return None


def _brand_cache_path():
    import os
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "dataset", "zoro_brands.json")


def _load_brand_cache():
    try:
        with open(_brand_cache_path(), encoding="utf-8") as f:
            return {k: v for k, v in json.load(f).items() if v}
    except Exception:
        return {}


# SKU → brand string. Loaded from disk so each SKU is fetched once EVER —
# across runs and across all placer profiles (the sheet 'brand' column was
# never populated, which made every unknown SKU cost a PDP page load).
_brand_cache = _load_brand_cache()


def _save_brand_cache():
    import os
    try:
        merged = _load_brand_cache()                       # merge with other placers' finds
        merged.update({k: v for k, v in _brand_cache.items() if v})
        os.makedirs(os.path.dirname(_brand_cache_path()), exist_ok=True)
        tmp = _brand_cache_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=0, sort_keys=True)
        os.replace(tmp, _brand_cache_path())
    except Exception:
        pass


def get_product_brand(driver, sku):
    """Detect product brand. Order of attack:
    1. persistent cache (data/dataset/zoro_brands.json — one lookup per SKU, ever)
    2. in-page fetch of the PDP HTML (NO navigation — the browser stays on the
       current zoro.com page; the microdata is parsed out of the raw HTML)
    3. classic PDP navigation (only when 1+2 can't answer, e.g. first order of
       a batch while the browser is still on Amazon)
    Returns brand string (e.g. 'ZORO SELECT', 'DEWALT') or '' if not found."""
    if sku in _brand_cache:
        return _brand_cache[sku]

    brand = ""
    # ── No-navigation path: fetch the PDP HTML from the page we're already on
    if 'zoro.com' in (driver.current_url or ''):
        try:
            html = driver.execute_async_script("""
                var sku = arguments[0], cb = arguments[arguments.length - 1];
                fetch('https://www.zoro.com/i/' + sku + '/', {credentials: 'include'})
                    .then(function(r){ return r.text(); })
                    .then(function(t){ cb(t.slice(0, 900000)); })
                    .catch(function(e){ cb(''); });
            """, sku) or ""
            m = re.search(r'data-za="product-microdata"[^>]*>\s*(\{.*?\})\s*</script>',
                          html, re.DOTALL)
            if m:
                try:
                    md = json.loads(m.group(1))
                    b = md.get('brand', '')
                    brand = (b.get('name', '') if isinstance(b, dict) else str(b or '')).strip()
                    if not brand:
                        brand = str(md.get('brandName', '') or md.get('manufacturer', '')).strip()
                except Exception:
                    brand = ""
            if not brand:
                m2 = re.search(r'"brand"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html)
                if m2:
                    brand = m2.group(1).strip()
            if brand:
                print(f"   [ZORO] Brand via fetch (no page load) for {sku}: {brand}")
                _brand_cache[sku] = brand
                _save_brand_cache()
                return brand
        except Exception as e:
            print(f"   [ZORO] No-nav brand fetch failed for {sku}: {e} — falling back to PDP")

    try:
        safe_get(driver, f"https://www.zoro.com/i/{sku}")
        time.sleep(2.5)

        # 1. Primary: read brand from product microdata JSON
        microdata = driver.execute_script("""
            var el = document.querySelector('script[data-za="product-microdata"]');
            if (!el) return null;
            try { return JSON.parse(el.textContent); } catch(e) { return null; }
        """)

        if microdata and isinstance(microdata, dict):
            brand_obj = microdata.get('brand', {})
            if isinstance(brand_obj, dict):
                brand = brand_obj.get('name', '')
            elif isinstance(brand_obj, str):
                brand = brand_obj
            if not brand:
                brand = microdata.get('brandName', '') or microdata.get('manufacturer', '')

        # 2. Fallback: look for Zoro Select badge in product detail area
        if not brand:
            try:
                zs_badge = driver.find_element(
                    By.CSS_SELECTOR, "[data-za='product-zoro-select']")
                if zs_badge and zs_badge.is_displayed():
                    brand = "ZORO SELECT"
            except Exception:
                pass

        # 3. Fallback: check product title for "Zoro Select"
        if not brand:
            try:
                h1 = driver.find_element(By.CSS_SELECTOR, "h1")
                if h1 and 'zoro select' in h1.text.lower():
                    brand = "ZORO SELECT"
            except Exception:
                pass

        brand = str(brand).strip()
        if brand:
            print(f"   [ZORO] Brand detected for {sku}: {brand}")
        else:
            print(f"   [ZORO] No brand found for {sku}")

    except Exception as e:
        print(f"   [ZORO] Brand detection error for {sku}: {e}")

    _brand_cache[sku] = brand      # "" stays session-only; non-empty persists
    if brand:
        _save_brand_cache()
    return brand


def _coerce_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _find_cart_item(items, sku):
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if item.get('zoroNo') == sku or item.get('sku') == sku:
            return item
        nested = item.get('item')
        if isinstance(nested, dict) and (nested.get('zoroNo') == sku or nested.get('sku') == sku):
            return item
    return None


def _extract_cart_item_qty(item):
    candidates = [item]
    if isinstance(item, dict):
        nested = item.get('item')
        if isinstance(nested, dict):
            candidates.append(nested)

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ('qty', 'quantity', 'requestedQuantity', 'cartQuantity'):
            qty = _coerce_int(candidate.get(key))
            if qty is not None:
                return qty
    return None


def _extract_cart_item_price(item):
    candidates = [item]
    if isinstance(item, dict):
        nested = item.get('item')
        if isinstance(nested, dict):
            candidates.append(nested)

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ('price', 'unitPrice'):
            try:
                return float(candidate.get(key, 0) or 0)
            except (TypeError, ValueError):
                continue
    return 0.0


def _resolve_cart_item_state(driver, sku, cart_response):
    items = []
    if isinstance(cart_response, dict):
        items = cart_response.get('d', {}).get('items', []) or []

    item = _find_cart_item(items, sku)
    if item is not None:
        return item

    verify = zoro_api(driver, "GET", "/v1/cart")
    if verify.get('s') != 200:
        return None
    return _find_cart_item(verify.get('d', {}).get('items', []) or [], sku)


# ==============================================================================
#  CART
# ==============================================================================

def clear_cart_selenium(driver, timeout=90):
    """Clear all cart items via Selenium (API DELETE is unreliable). Bounded.

    21 Sep 2026: Zoro answered DELETE /v1/cart/<id>/items/<zoroNo> with HTTP 500, so the item
    never left the cart. This was `while True` with a two-second sleep, and the Remove button it
    clicked went to the same failing backend — DM_RESELLER clicked it for thirteen minutes and
    placed nothing, twice in one evening, with no line in the log after the first click.

    It now stops when the cart stops shrinking, or when `timeout` seconds have passed, and
    returns False so the caller abandons the order before any money is spent instead of the
    whole placer standing still."""
    try:
        safe_get(driver, "https://www.zoro.com/cart")   # read-only: the Remove clicks come after
        time.sleep(3)
        deadline = time.time() + timeout
        last, stalls = None, 0
        while time.time() < deadline:
            btns = driver.find_elements(By.CSS_SELECTOR, 'button[data-za*="-remove-cta"]')
            n = len(btns)
            if not n:
                return True
            if last is not None and n >= last:
                stalls += 1
                if stalls >= 3:
                    print(f"   [ZORO] cart still holds {n} item(s) after {stalls} remove clicks "
                          f"— Zoro is refusing the removal, giving up")
                    return False
            else:
                stalls = 0
            last = n
            driver.execute_script("arguments[0].click();", btns[0])
            time.sleep(2)
        print(f"   [ZORO] cart clear gave up after {timeout}s — {last} item(s) still there")
        return False
    except Exception as e:
        print(f"   [ZORO API] Selenium cart clear failed: {e}")
        return False


def _clear_cart_items_api(driver, cart_id, items):
    """DELETE each cart line by zoroNo, then re-read the cart. Returns (emptied, cart_id).

    12 Oct 2026: one leftover item sat in HS_COUPON's cart and every clear went straight to the
    /cart page, which the headless Chrome never finished loading ("Timed out receiving message
    from renderer", 60 s each) — 57 priority re-checks failed one after another, and every
    order would have too. The API is tried first now; the page is only the fallback."""
    for it in items or []:
        zno = (it or {}).get("zoroNo")
        if zno:
            zoro_api(driver, "DELETE", f"/v1/cart/{cart_id}/items/{zno}")
    time.sleep(1.0)
    r = zoro_api(driver, "GET", "/v1/cart")
    d = r.get('d') if isinstance(r, dict) else None
    if r.get('s') == 200 and isinstance(d, dict) and not d.get('items'):
        return True, d.get('cartId', cart_id)
    return False, cart_id


def clear_cart_and_add(driver, sku, qty):
    """Clear cart + add item via API. Returns (success, unit_price)."""
    r = zoro_api(driver, "GET", "/v1/cart")
    if r.get('s') != 200:
        print(f"   [ZORO API] Cart GET failed: {r.get('s')}")
        ensure_token(driver)
        r = zoro_api(driver, "GET", "/v1/cart")
        if r.get('s') != 200:
            return False, 0

    cart_id = r['d'].get('cartId', '')
    items = r['d'].get('items', [])

    if items:
        print(f"   [ZORO API] Cart has {len(items)} items — clearing via API")
        emptied, cart_id = _clear_cart_items_api(driver, cart_id, items)
        if emptied:
            items = []
        else:
            print("   [ZORO API] API clear left items behind — clearing via Selenium")
    if items:
        if not clear_cart_selenium(driver):
            return False, 0
        r = zoro_api(driver, "GET", "/v1/cart")
        cart_id = r['d'].get('cartId', '')
        if r['d'].get('items', []):
            print("   [ZORO API] Cart not empty even after Selenium clear")
            return False, 0

    # Add item
    r = zoro_api(driver, "POST", f"/v1/cart/{cart_id}/items",
                 {"item": {"zoroNo": sku, "qty": qty}})
    if r.get('s') != 200:
        print(f"   [ZORO API] Add failed: {r.get('s')}")
        return False, 0

    item = _resolve_cart_item_state(driver, sku, r)
    if item is None:
        print(f"   [ZORO API] Added item {sku} not found in cart")
        return False, 0

    actual_qty = _extract_cart_item_qty(item)
    if actual_qty is None:
        print(f"   [ZORO API] ⚠ Could not verify cart qty for {sku}; continuing")
    elif actual_qty != qty:
        print(f"   [ZORO API] Qty mismatch for {sku}: requested {qty}, cart shows {actual_qty}")
        return False, 0

    price = _extract_cart_item_price(item)
    return True, price


def add_second_item(driver, sku, qty):
    """Add a second item to the existing cart (no clear). Returns (success, price)."""
    r = zoro_api(driver, "GET", "/v1/cart")
    if r.get('s') != 200:
        print(f"   [ZORO API] Cart GET failed for 2nd item: {r.get('s')}")
        return False, 0

    cart_id = r['d'].get('cartId', '')

    # Check if this sku is already in cart to calculate expected total quantity
    cart_items = r.get('d', {}).get('items', [])
    existing_item = _find_cart_item(cart_items, sku)
    prev_qty = _extract_cart_item_qty(existing_item) if existing_item else 0
    expected_qty = (prev_qty or 0) + qty

    r = zoro_api(driver, "POST", f"/v1/cart/{cart_id}/items",
                 {"item": {"zoroNo": sku, "qty": qty}})
    if r.get('s') != 200:
        print(f"   [ZORO API] Add 2nd item failed: {r.get('s')}")
        return False, 0

    item = _resolve_cart_item_state(driver, sku, r)
    if item is None:
        print(f"   [ZORO API] Added 2nd item {sku} not found in cart")
        return False, 0

    actual_qty = _extract_cart_item_qty(item)
    if actual_qty is None:
        print(f"   [ZORO API] ⚠ Could not verify cart qty for {sku}; continuing")
    elif actual_qty != expected_qty:
        print(f"   [ZORO API] Qty mismatch for {sku}: requested +{qty} (expected total {expected_qty}), cart shows {actual_qty}")
        return False, 0

    price = _extract_cart_item_price(item)
    print(f"   [ZORO API] 2nd item added: {sku} x{qty} @ ${price:.2f}" + (f" (cart total: {actual_qty})" if prev_qty else ""))
    return True, price


# ==============================================================================
#  ADDRESS MANAGEMENT
# ==============================================================================

# Module-level cache for last known address ID
_last_addr_id = None


def get_default_shipping_id(driver):
    """GET address list, return the default-shipping address ID (or first)."""
    global _last_addr_id
    for endpoint in ["/customer/v1/account/address", "/customer/v1/account/addresses"]:
        r = zoro_api(driver, "GET", endpoint)
        if r.get('s') == 200:
            break

    if r.get('s') != 200:
        if _last_addr_id:
            print(f"   [ZORO API] Address list failed ({r.get('s')}), using cached ID: {_last_addr_id}")
            return _last_addr_id
        print(f"   [ZORO API] Address list failed: {r.get('s')}")
        return None

    addrs = r.get('d', [])
    if isinstance(addrs, dict):
        addrs = addrs.get('addresses', addrs.get('items', []))

    for a in addrs:
        if a.get('isDefaultShipping'):
            return a.get('id')
    if addrs:
        return addrs[0].get('id')
    return None


def _fit_address_lines(line1, line2, limit=35):
    """Ensure address1 and address2 each fit within the Zoro API character limit.
    Spills overflow from line1 into line2, and truncates line2 if still too long."""
    line1, line2 = line1.strip(), line2.strip()

    if len(line1) <= limit and len(line2) <= limit:
        return line1, line2

    # Combine both lines, then split at a word boundary near the limit
    combined = f"{line1} {line2}".strip() if line2 else line1
    if len(combined) <= limit:
        return combined, ""

    # Find last space at or before the limit for a clean break
    cut = combined.rfind(" ", 0, limit + 1)
    if cut <= 0:
        cut = limit  # no space found — hard cut
    new1 = combined[:cut].strip()
    new2 = combined[cut:].strip()

    # Truncate line2 if it still exceeds the limit
    if len(new2) > limit:
        new2 = new2[:limit].strip()

    return new1, new2


def _build_address_body(address):
    """Build the API request body from an address dict."""
    name_parts = address["name"].split(" ", 1)
    first_name = name_parts[0] if name_parts else "Customer"
    last_name = name_parts[1] if len(name_parts) > 1 else first_name

    addr1, addr2 = _fit_address_lines(
        address["street_address"],
        address.get("street_address_2", ""),
    )

    return {
        "firstName": first_name, "lastName": last_name,
        "companyName": "",
        "address1": addr1,
        "address2": addr2,
        "city": address["city"],
        "state": address["state_abbr"],
        "zip": address["zip_code"].replace("-", ""),
        "phoneNumber": re.sub(r'\D', '', address["phone"]),
        "isDefaultShipping": True, "isDefaultBilling": False
    }


def update_default_address(driver, address):
    """Overwrite the default shipping address. Returns address ID or None."""
    global _last_addr_id
    body = _build_address_body(address)

    addr_id = get_default_shipping_id(driver)
    if addr_id:
        r = zoro_api(driver, "PUT", f"/customer/v1/account/address/{addr_id}", body)
        if r.get('s') == 200:
            _last_addr_id = addr_id
            return addr_id
        print(f"   [ZORO API] PUT update failed ({r.get('s')}), creating new address")

    addr_id = create_address(driver, address)
    if addr_id:
        _last_addr_id = addr_id
    return addr_id


def create_address(driver, address):
    """Create a new address. Returns addr_id or None."""
    body = _build_address_body(address)
    r = zoro_api(driver, "POST", "/customer/v1/account/address", body)
    if r.get('s') == 200:
        return r['d'].get('id')
    print(f"   [ZORO API] Address create failed: {r.get('s')} — {str(r.get('d',''))[:300]}")
    return None


def delete_address(driver, addr_id):
    """Delete temp address after order. Best-effort."""
    if addr_id:
        zoro_api(driver, "DELETE", f"/customer/v1/account/address/{addr_id}", content_type=None)
