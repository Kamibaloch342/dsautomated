"""
order_checkout_true_api.py — True REST-API-first Zoro checkout.

Uses the exact endpoints discovered via CDP capture:
  • Inventory     PUT /scm/v1/inventory/availability
  • Cart CRUD     GET|POST /v1/cart/...
  • Address       GET|PUT|POST /customer/v1/account/address
  • Cart Address  POST /v1/cart/{cartId}/shippingaddress
  • Shipping      PUT /v1/cart/{cartId}/shippingmethods
                  PUT /v1/cart/{cartId}/shippingmethod
  • Coupon        PUT|DELETE /v1/cart/{cartId}/promo
  • Payment       GET /customer/v1/account/creditcard
  • Order Submit  POST /v1/cart/{cartId}/order

For RESELLER profiles: full API end-to-end (paymentMethod included).
For COUPON  profiles: API for cart/address/shipping/coupon, then DOM
  fallback for Place Order if Net-30 payment payload isn't known.

Interface: place_order_fast_true_api(...) — drop-in replacement for
order_checkout.place_order_fast().
"""

import os
import re
import json
import time
import uuid
import math
import functools
from datetime import datetime

from utils.sheet_sync import sync_order_log_async

import platforms.zoro as zoro
from checkout.dom import _format_checkout_result, _click_place_order
from utils.helpers import update_coupon_status
from utils.logger import bot_log

print = functools.partial(print, flush=True)


# ==============================================================================
#  RAW API LOG  (order submission payload / response dumps)
# ==============================================================================

_RAW_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "rajar_logs", "raw_api_log.txt"
)


def _write_raw_api_log(label, data_dict):
    """Append a raw JSON block to raw_api_log.txt instead of console."""
    try:
        os.makedirs(os.path.dirname(_RAW_LOG_PATH), exist_ok=True)
        with open(_RAW_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"{datetime.now().isoformat()}  {label}\n")
            f.write(f"{'='*60}\n")
            f.write(json.dumps(data_dict, indent=2, default=str))
            f.write("\n")
    except Exception as e:
        print(f"   [DEBUG] Raw log write failed: {e}")


# ==============================================================================
#  TIMING HELPERS
# ==============================================================================

def _time(label, start):
    """Print elapsed time since start."""
    elapsed = time.perf_counter() - start
    print(f"   ⏱️  [TIME] {label}: {elapsed:.3f}s")
    return elapsed


# ==============================================================================
#  LOW-LEVEL API HELPERS  (with heavy debug)
# ==============================================================================

def _api(driver, method, endpoint, body=None):
    """Execute Zoro API via in-browser fetch. Same as zoro.zoro_api but
    returns a cleaner dict: {ok, status, data, error}."""
    t0 = time.perf_counter()
    print(f"   [DEBUG _api] >>> {method} {endpoint}")
    if body:
        print(f"   [DEBUG _api] BODY: {json.dumps(body)[:300]}")
    raw = zoro.zoro_api(driver, method, endpoint, body)
    status = raw.get("s", -1) if isinstance(raw, dict) else -1
    _time(f"API {method} {endpoint[:50]}", t0)
    print(f"   [DEBUG _api] <<< HTTP {status} | raw type={type(raw)}")
    if isinstance(raw, dict) and raw.get("d") is not None:
        preview = str(raw["d"])[:400]
        print(f"   [DEBUG _api] RESPONSE preview: {preview}")
    if status == 200:
        return {"ok": True, "status": status, "data": raw.get("d"), "error": None}
    return {
        "ok": False,
        "status": status,
        "data": raw.get("d") if isinstance(raw, dict) else None,
        "error": str(raw.get("d", raw))[:300],
    }


def _get_cart(driver):
    """Get current cart. Returns cart dict or None."""
    print("   [DEBUG] _get_cart() calling GET /v1/cart")
    r = _api(driver, "GET", "/v1/cart")
    if r["ok"] and isinstance(r["data"], dict):
        cart = r["data"]
        print(f"   [DEBUG] Cart ID={cart.get('cartId')}, items={len(cart.get('items',[]))}, total=${cart.get('total')}")
        return cart
    print(f"   [DEBUG] _get_cart FAILED: {r['error']}")
    return None


def _get_cart_id(driver):
    """Get or create a cartId."""
    cart = _get_cart(driver)
    if cart and cart.get("cartId"):
        return cart["cartId"]
    print("   [DEBUG] WARNING: no cartId found!")
    return None


# ==============================================================================
#  CART OPERATIONS
# ==============================================================================

def _clear_cart_api(driver):
    """Clear all items from cart via API + Selenium fallback."""
    t0 = time.perf_counter()
    print("   [DEBUG] _clear_cart_api() starting...")
    cart = _get_cart(driver)
    if not cart:
        _time("_clear_cart_api", t0)
        print("   [DEBUG] No cart to clear")
        return True
    items = cart.get("items", [])
    if not items:
        _time("_clear_cart_api", t0)
        print("   [DEBUG] Cart already empty")
        return True

    cart_id = cart["cartId"]
    print(f"   [DEBUG] Clearing {len(items)} item(s) from cart {cart_id}")

    # Working strategy: DELETE by zoroNo
    for item in items:
        zno = item.get("zoroNo")
        if zno:
            _api(driver, "DELETE", f"/v1/cart/{cart_id}/items/{zno}")

    time.sleep(1.0)
    cart = _get_cart(driver)
    if cart and not cart.get("items"):
        _time("_clear_cart_api", t0)
        print("   [DEBUG] Cart cleared via API")
        return True

    _time("_clear_cart_api (fallback)", t0)
    print("   [DEBUG] API cart clear incomplete — Selenium fallback")
    return zoro.clear_cart_selenium(driver)


def _add_item_api(driver, sku, qty):
    """Add item to cart. Returns (ok, unit_price)."""
    t0 = time.perf_counter()
    cart_id = _get_cart_id(driver)
    if not cart_id:
        _time("_add_item_api", t0)
        print("   [DEBUG] _add_item_api: no cart_id!")
        return False, 0.0

    # Read existing cart items to determine if this SKU was already present
    prev_qty = 0
    prev_cart = _get_cart(driver)
    if prev_cart:
        for it in prev_cart.get("items", []):
            zno = it.get("zoroNo") or it.get("sku")
            if zno == sku:
                for k in ("qty", "quantity", "requestedQuantity", "cartQuantity"):
                    if k in it:
                        try:
                            prev_qty = int(float(it[k]))
                            break
                        except (TypeError, ValueError):
                            pass
                break
    expected_qty = prev_qty + qty

    print(f"   [DEBUG] POST /v1/cart/{cart_id}/items  sku={sku} qty={qty}")
    r = _api(driver, "POST", f"/v1/cart/{cart_id}/items",
             {"item": {"zoroNo": sku, "qty": qty}})
    if not r["ok"]:
        _time("_add_item_api", t0)
        print(f"   [DEBUG] Add item failed: {r['error']}")
        return False, 0.0

    # Verify via cart GET
    time.sleep(0.3)
    cart = _get_cart(driver)
    if not cart:
        _time("_add_item_api", t0)
        print("   [DEBUG] Add item OK but cart GET failed on verify")
        return False, 0.0

    items = cart.get("items", [])
    print(f"   [DEBUG] Cart now has {len(items)} item(s)")
    for item in items:
        zno = item.get("zoroNo") or item.get("sku")
        if zno == sku:
            price = 0.0
            nested = item.get("item", item)
            try:
                price = float(nested.get("price", nested.get("unitPrice", 0)) or 0)
            except (TypeError, ValueError):
                pass
            actual_qty = None
            for k in ("qty", "quantity", "requestedQuantity", "cartQuantity"):
                if k in item:
                    try:
                        actual_qty = int(float(item[k]))
                        break
                    except (TypeError, ValueError):
                        pass
            if actual_qty is None:
                actual_qty = expected_qty
            if actual_qty != expected_qty:
                _time("_add_item_api", t0)
                print(f"   [DEBUG] Qty mismatch: req +{qty} (expected total {expected_qty}), got {actual_qty}")
                return False, 0.0
            _time("_add_item_api", t0)
            print(f"   [DEBUG] Item verified: {sku} x{actual_qty} @ ${price}")
            return True, price

    _time("_add_item_api", t0)
    print("   [DEBUG] Added item not found in cart verify")
    return False, 0.0


# ==============================================================================
#  ADDRESS OPERATIONS
# ==============================================================================

def _get_addresses(driver):
    """Return list of address dicts."""
    print("   [DEBUG] GET /customer/v1/account/address")
    r = _api(driver, "GET", "/customer/v1/account/address")
    if not r["ok"]:
        print(f"   [DEBUG] Address list failed: {r['error']}")
        return []
    data = r["data"]
    if isinstance(data, dict):
        return data.get("addresses", data.get("items", []))
    if isinstance(data, list):
        return data
    return []


def _get_default_billing_addr(driver):
    """Return default billing address dict or first address."""
    addrs = _get_addresses(driver)
    print(f"   [DEBUG] Found {len(addrs)} address(es)")
    for a in addrs:
        if a.get("isDefaultBilling"):
            print(f"   [DEBUG] Default billing: {a.get('id')}")
            return a
    for a in addrs:
        if a.get("isDefaultShipping"):
            print(f"   [DEBUG] Default shipping (fallback billing): {a.get('id')}")
            return a
    if addrs:
        print(f"   [DEBUG] Using first address as fallback: {addrs[0].get('id')}")
        return addrs[0]
    print("   [DEBUG] NO ADDRESSES FOUND")
    return None


def _get_default_shipping_addr_id(driver):
    """Return default shipping address ID."""
    addrs = _get_addresses(driver)
    for a in addrs:
        if a.get("isDefaultShipping"):
            return a.get("id")
    return addrs[0].get("id") if addrs else None


def _update_or_create_address(driver, address):
    """Update/create address in address book. Returns address dict or None."""
    t0 = time.perf_counter()
    first_name = address.get("firstName", address.get("name", "").split()[0] if address.get("name") else "Customer")
    last_name = " ".join(address.get("name", "").split()[1:]) or first_name
    # Zoro API enforces 35-char limit on address lines.
    # Strategy: keep address1 ≤ 35 by moving last word(s) to address2,
    # then if address2 > 35 move excess to companyName (or truncate).
    addr1 = address.get("street_address", address.get("address1", ""))
    addr2 = address.get("street_address_2", address.get("address2", ""))
    company = address.get("companyName", "")

    def _split_at_last_word(text, limit):
        """Return (first_part, remainder) splitting at the last space before limit."""
        if len(text) <= limit:
            return text, ""
        # Find last space before limit
        cutoff = text.rfind(" ", 0, limit + 1)
        if cutoff <= 0:
            cutoff = limit  # no space found, hard cut
        return text[:cutoff], text[cutoff:].strip()

    # 1. Trim address1 — overflow goes to address2
    if len(addr1) > 35:
        kept, overflow = _split_at_last_word(addr1, 35)
        print(f"   [DEBUG] Splitting address1: kept '{kept}' ({len(kept)}), overflow '{overflow}'")
        addr1 = kept
        if overflow:
            addr2 = (overflow + " " + addr2).strip() if addr2 else overflow

    # 2. Trim address2 — if too long, move to companyName or truncate
    if len(addr2) > 35:
        if not company:
            print(f"   [DEBUG] Moving long address2 ({len(addr2)} chars) to companyName")
            company = addr2
            addr2 = ""
        else:
            kept, overflow = _split_at_last_word(addr2, 35)
            print(f"   [DEBUG] Splitting address2: kept '{kept}', overflow to companyName")
            addr2 = kept
            company = (company + " " + overflow).strip() if company else overflow

    body = {
        "firstName": first_name,
        "lastName": last_name,
        "companyName": company,
        "address1": addr1,
        "address2": addr2,
        "city": address.get("city", ""),
        "state": address.get("state_abbr", address.get("state", "")),
        "zip": address.get("zip_code", address.get("zip", "")).replace("-", ""),
        "phoneNumber": re.sub(r"\D", "", address.get("phone", "")),
        "isDefaultShipping": True,
        "isDefaultBilling": False,
    }
    print(f"   [DEBUG] Address body: {json.dumps(body)[:300]}")

    addr_id = _get_default_shipping_addr_id(driver)
    if addr_id:
        print(f"   [DEBUG] PUT /customer/v1/account/address/{addr_id}")
        r = _api(driver, "PUT", f"/customer/v1/account/address/{addr_id}", body)
        if r["ok"]:
            _time("_update_or_create_address", t0)
            print(f"  ✅ Address updated: {addr_id[:8]}")
            result = body.copy()
            result["id"] = addr_id
            return result
        print(f"   [DEBUG] Address PUT failed ({r['status']}), trying CREATE")

    print("   [DEBUG] POST /customer/v1/account/address")
    r = _api(driver, "POST", "/customer/v1/account/address", body)
    if r["ok"] and isinstance(r["data"], dict):
        new_id = r["data"].get("id")
        _time("_update_or_create_address", t0)
        print(f"  ✅ Address created: {new_id[:8] if new_id else '?'}")
        result = body.copy()
        result["id"] = new_id
        return result

    _time("_update_or_create_address", t0)
    print(f"   [DEBUG] Address create failed: {r['error']}")
    return None


# ==============================================================================
#  CART SHIPPING
# ==============================================================================

def _set_cart_shipping_address(driver, cart_id, address_dict):
    """POST shipping address directly to cart.
    Returns True on success, False on generic failure, or the string
    'PO_BOX' when Zoro rejects the address as a PO Box."""
    t0 = time.perf_counter()

    def _build_payload(addr):
        full_name = f"{addr.get('firstName','')} {addr.get('lastName','')}".strip()
        # Zoro API enforces max 35 chars on fullName
        if len(full_name) > 35:
            full_name = full_name[:35].strip()
        return {
            "shippingAddress": {
                "address1": addr.get("address1", ""),
                "address2": addr.get("address2", ""),
                "companyName": addr.get("companyName", ""),
                "city": addr.get("city", ""),
                "fullName": full_name,
                "firstName": addr.get("firstName", ""),
                "lastName": addr.get("lastName", ""),
                "isDefaultBilling": False,
                "isDefaultShipping": True,
                "phoneNumber": addr.get("phoneNumber", ""),
                "state": addr.get("state", ""),
                "zip": addr.get("zip", "").replace("-", ""),
            }
        }

    payload = _build_payload(address_dict)
    print(f"   [DEBUG] POST /v1/cart/{cart_id}/shippingaddress")
    print(f"   [DEBUG] payload: {json.dumps(payload)[:400]}")
    r = _api(driver, "POST", f"/v1/cart/{cart_id}/shippingaddress", payload)

    # Detect PO Box rejection and sanitize + retry once
    if not r["ok"]:
        err_str = str(r.get("error") or r.get("data") or "").lower()
        if "poboxnotallowed" in err_str or "po box is not allowed" in err_str:
            print("   ⚠️  PO Box detected by Zoro — sanitizing address and retrying...")
            import re
            sanitized = dict(address_dict)
            # Remove "box NNN" patterns (case-insensitive) from address lines
            for key in ("address1", "address2"):
                if sanitized.get(key):
                    sanitized[key] = re.sub(r'\bbox\s+\d+\b', '', sanitized[key], flags=re.IGNORECASE).strip()
                    sanitized[key] = re.sub(r'\s+', ' ', sanitized[key]).strip()
            # If address1 is now empty but address2 has content, swap them
            if not sanitized.get("address1") and sanitized.get("address2"):
                sanitized["address1"] = sanitized["address2"]
                sanitized["address2"] = ""
            payload2 = _build_payload(sanitized)
            print(f"   [DEBUG] Retrying with sanitized payload: {json.dumps(payload2)[:400]}")
            r = _api(driver, "POST", f"/v1/cart/{cart_id}/shippingaddress", payload2)
            if r["ok"]:
                print("  ✅ Cart shipping address set (sanitized)")
                _time("_set_cart_shipping_address", t0)
                return True
            # Still failed — return PO_BOX sentinel so caller can label properly
            print(f"   [DEBUG] Sanitized address also rejected: {r['error']}")
            _time("_set_cart_shipping_address", t0)
            return "PO_BOX"

    _time("_set_cart_shipping_address", t0)
    if r["ok"]:
        print("  ✅ Cart shipping address set")
        return True
    print(f"   [DEBUG] Cart shipping address failed: {r['error']}")
    return False


def _get_shipping_methods(driver, cart_id, address_dict):
    """PUT shipTo to get available shipping methods. Returns list of methods."""
    t0 = time.perf_counter()
    full_name = f"{address_dict.get('firstName','')} {address_dict.get('lastName','')}".strip()
    if len(full_name) > 35:
        full_name = full_name[:35].strip()
    payload = {
        "shipTo": {
            "id": address_dict.get("id", ""),
            "address1": address_dict.get("address1", ""),
            "address2": address_dict.get("address2", ""),
            "companyName": address_dict.get("companyName", ""),
            "city": address_dict.get("city", ""),
            "fullName": full_name,
            "firstName": address_dict.get("firstName", ""),
            "lastName": address_dict.get("lastName", ""),
            "isDefaultBilling": False,
            "isDefaultShipping": True,
            "phoneNumber": address_dict.get("phoneNumber", ""),
            "state": address_dict.get("state", ""),
            "zip": address_dict.get("zip", "").replace("-", ""),
        }
    }
    print(f"   [DEBUG] PUT /v1/cart/{cart_id}/shippingmethods")
    print(f"   [DEBUG] payload: {json.dumps(payload)[:400]}")
    r = _api(driver, "PUT", f"/v1/cart/{cart_id}/shippingmethods", payload)
    if r["ok"] and isinstance(r["data"], dict):
        methods = r["data"].get("shippingMethods", [])
        if methods:
            _time("_get_shipping_methods", t0)
            print(f"  📦 {len(methods)} shipping method(s) returned")
            for i, m in enumerate(methods[:3]):
                print(f"      [{i}] {json.dumps(m)[:200]}")
            return methods
    # Detect state restriction directly from the failed response
    err_str = str(r.get("error") or r.get("data") or "")
    if "restrictedSkus" in err_str:
        _time("_get_shipping_methods", t0)
        print(f"   [DEBUG] restrictedSkus detected in shipping methods response")
        return None  # sentinel for STATE_RESTRICTED
    # Try reading from cart directly
    cart = _get_cart(driver)
    if cart and isinstance(cart.get("shippingMethod"), list):
        _time("_get_shipping_methods", t0)
        return cart["shippingMethod"]
    _time("_get_shipping_methods", t0)
    print(f"   [DEBUG] No shipping methods returned: {r['error']}")
    return []


def _select_shipping_method_api(driver, cart_id, methods):
    """Select Standard Ground. Returns True/False."""
    t0 = time.perf_counter()
    ground = None
    for m in methods:
        name = ""
        if isinstance(m, dict):
            name = (m.get("name") or m.get("description") or "").lower()
        if "ground" in name or "standard" in name:
            ground = m
            break

    if not ground:
        _time("_select_shipping_method_api", t0)
        print("   [DEBUG] Standard Ground not found in methods")
        return False

    if isinstance(ground, dict):
        payload = {
            "rate": ground.get("rate", ground.get("price", 0)),
            "name": ground.get("name", "Standard Ground"),
            "id": ground.get("id", "03"),
            "promotionDiscount": ground.get("promotionDiscount", 0),
        }
    else:
        payload = {"rate": 0, "name": "Standard Ground", "id": "03", "promotionDiscount": 0}

    print(f"   [DEBUG] PUT /v1/cart/{cart_id}/shippingmethod  payload={json.dumps(payload)}")
    r = _api(driver, "PUT", f"/v1/cart/{cart_id}/shippingmethod", payload)
    _time("_select_shipping_method_api", t0)
    if r["ok"]:
        print(f"  📦 Shipping selected: {payload['name']} (${payload['rate']})")
        return True
    print(f"   [DEBUG] Shipping select failed: {r['error']}")
    return False


def _revalidate_shipping_after_coupon(driver, cart_id, address_dict):
    """After coupon application the cart total may change (free-shipping
    thresholds, order minimums, etc.) which can invalidate the previously
    selected shipping method.  Re-check the cart; if the shipping method is
    missing or looks invalid, re-fetch methods and re-select Standard Ground.
    Returns True if shipping is valid, False on failure."""
    t0 = time.perf_counter()
    cart = _get_cart(driver)
    if not cart:
        _time("_revalidate_shipping_after_coupon", t0)
        print("   [DEBUG] Re-validate: cart missing after coupon")
        return False

    # Check whether a shipping method is still attached
    sm = cart.get("shippingMethod")
    shipping_valid = False
    if isinstance(sm, dict) and sm.get("id"):
        shipping_valid = True
    elif isinstance(sm, list) and len(sm) > 0:
        shipping_valid = True

    if shipping_valid:
        _time("_revalidate_shipping_after_coupon", t0)
        print("  ✅ Shipping still valid after coupon")
        return True

    print("  ⚠️  Shipping method invalidated by coupon — re-fetching...")
    methods = _get_shipping_methods(driver, cart_id, address_dict)
    if methods is None:
        _time("_revalidate_shipping_after_coupon", t0)
        print("   [DEBUG] Re-validate: restrictedSkus detected")
        return False
    if not methods:
        _time("_revalidate_shipping_after_coupon", t0)
        print("   [DEBUG] Re-validate: no shipping methods available")
        return False

    ok = _select_shipping_method_api(driver, cart_id, methods)
    _time("_revalidate_shipping_after_coupon", t0)
    if ok:
        print("  ✅ Shipping re-selected after coupon")
        return True
    print("   [DEBUG] Re-validate: failed to re-select shipping")
    return False


# ==============================================================================
#  COUPON OPERATIONS
# ==============================================================================

def _is_transient_coupon_error(err) -> bool:
    """Zoro API errors that mean 'retry me', NOT 'this coupon is invalid for the
    item'. Treating these as rejections is what blacklisted G306004662 as
    coupon-impossible (no_coupon_codes) while SUPPLIES626 demonstrably worked on
    it — all 34 codes 'failed' inside one bad 90s window with 'conflict while
    modifying resource, try again' and the item was poisoned for the whole run."""
    import re as _re
    e = str(err or "").lower()
    return ("conflict while modifying" in e or "try again" in e
            or "timed out" in e or "timeout" in e
            or "'status': 429" in e or "'status': 5" in e
            or "too many requests" in e or "gateway timeout" in e
            or bool(_re.search(r"status['\"]?\s*[:=]\s*['\"]?(429|5\d\d)\b", e))
            or "service unavailable" in e or "internal server error" in e)


def classify_promo_error(err):
    """Turn Zoro's promo-API error text into a verdict (kind, extra):
      ("token", None)      session problem — Invalid Bearer Token / no_token / Jwt expired:
                           refresh the token and retry; NEVER a verdict about the coupon
      ("transient", None)  conflict / timeout / 429 / 5xx / failed to fetch: retry, never a verdict
      ("dead", None)       the coupon is dead everywhere — "no longer available", "has expired",
                           "Invalid Promo Code": retire it
      ("item", None)       the coupon never applies to THIS item — "not valid for the item(s)",
                           "needs to include at least one eligible <Category> item": definitive
      ("min_order", 50.0)  cart minimum not met — "needs to include at least $50 of eligible …":
                           it WOULD apply on a bigger cart (a Deciding signal, not ineligibility)
      ("unknown", None)
    Seen in production logs, most → least frequent: not valid for the item(s) (5,700),
    no longer available (2,700), Invalid Bearer Token (780), category minimums, $ minimums,
    Invalid Promo Code, has expired, session/fetch failures."""
    import re as _re
    e = str(err or "").lower()
    if not e:
        return ("unknown", None)
    if any(s in e for s in ("invalid bearer token", "no_token", "invalid_token", "jwt is expired",
                            "unauthorized", "'status': 401", "session has expired", "session expired",
                            "token has expired", "token expired", "bearer")):
        return ("token", None)
    if _is_transient_coupon_error(e) or "failed to fetch" in e or "bad gateway" in e:
        return ("transient", None)
    if "no longer available" in e or "has expired" in e or "invalid promo code" in e or "invalidpromocode" in e:
        return ("dead", None)
    if "needs to include" in e:
        m = _re.search(r"at least \$\s*([0-9][0-9,]*(?:\.[0-9]+)?)", e)
        if m:
            try:
                return ("min_order", float(m.group(1).replace(",", "")))
            except ValueError:
                return ("min_order", None)
        return ("item", None)                     # "…at least one eligible <Category> item"
    if "not valid for" in e or "not applicable" in e or "not eligible" in e:
        return ("item", None)
    if "expired" in e:
        return ("dead", None)
    # A bare "invalid" used to land here as an item rejection — but `invalidItems` is a FIELD
    # NAME in Zoro's cart response, present on replies that say nothing about this item. That
    # misread is half of why 12 coupon-able items were called promo-ineligible on 28 Sep 2026.
    # Anything we cannot actually read stays "unknown": no verdict, retried later.
    if "not valid" in e:
        return ("item", None)
    return ("unknown", None)


PRIORITY_DEAD_RETIRE_AT = 3      # dead replies on distinct carts before a priority code is retired


def _note_reject(code, result, cache=None, priority=False):
    """Log one rejected coupon by kind and act on it (retire dead REGULAR coupons,
    remember cart minimums). Returns the kind so callers can decide what counts.
    priority=True: the weekly 15–20 % codes are managed by people in the Coupons tab
    and are NEVER retired automatically — one odd reply must not cost every later
    order its priority coupon. The reply is counted and shown instead."""
    kind, extra = classify_promo_error(result)
    if kind == "token":
        print(f"  ⏭️  {code}: session/token error — not counted")
    elif kind == "transient":
        print(f"  ⏭️  {code}: transient API error — not counted")
    elif kind == "dead":
        if priority:
            n = 1
            if cache is not None:
                d = cache.setdefault("priority_dead_replies", {})
                d[code] = int(d.get(code, 0) or 0) + 1
                n = d[code]
            if n >= PRIORITY_DEAD_RETIRE_AT:
                # Three separate carts said the code no longer exists — it has expired. Keeping
                # it in the list only costs every later order a wasted attempt (owner, 14 Sep).
                update_coupon_status(code, "INVALID")
                print(f"  🪦 {code}: PRIORITY coupon declared dead by Zoro on {n} different carts "
                      f"('{str(result)[:50]}') — retired (marked INVALID); remove it from the Coupons tab")
                try:
                    from utils import decision_log as _dl
                    _dl.log("learn", profile="", asin="", sku="", action="priority_coupon_retired", coupon=code,
                            reason=f"dead reply on {n} carts: {str(result)[:120]}")
                except Exception:
                    pass
            else:
                print(f"  ⚠️  {code}: PRIORITY coupon answered '{str(result)[:60]}' (dead reply #{n} of "
                      f"{PRIORITY_DEAD_RETIRE_AT} before it is retired)")
        else:
            update_coupon_status(code, "INVALID")          # dead everywhere → never tried again
            print(f"  ⏭️  {code}: coupon is dead (no longer available / expired) — marked INVALID")
    elif kind == "min_order":
        if extra:
            print(f"  ⏭️  {code}: cart minimum ${extra:.2f} not met (would apply on a bigger cart)")
            if cache is not None:
                cache.setdefault("coupon_min_order", {})[code] = extra
        else:
            print(f"  ⏭️  {code}: cart minimum not met")
    elif kind == "item":
        print(f"  ⏭️  {code}: not valid for this item")
    else:
        print(f"  ⏭️  {code}: {str(result)[:80]}")
    return kind


def _apply_coupon_api(driver, cart_id, coupon_code):
    """Apply a single coupon via API. Returns (ok, savings_or_error).
    Transient API conflicts are retried 3× with backoff; if still failing, the
    error comes back with a 'TRANSIENT:' prefix so callers never cache it as a
    real per-item rejection."""
    t0 = time.perf_counter()
    print(f"   [DEBUG] _apply_coupon_api cart={cart_id} code={coupon_code}")
    # First remove any existing promo (give the cart a beat — DELETE→PUT racing
    # is the likeliest source of the 'conflict while modifying resource' 409s)
    print(f"   [DEBUG] DELETE existing promo first")
    _api(driver, "DELETE", f"/v1/cart/{cart_id}/promo")
    time.sleep(0.5)

    err = ""
    for attempt in range(3):
        print(f"   [DEBUG] PUT /v1/cart/{cart_id}/promo  body={{'promo': '{coupon_code}'}}")
        r = _api(driver, "PUT", f"/v1/cart/{cart_id}/promo", {"promo": coupon_code})
        if r["ok"]:
            break
        err = str(r["data"]) if r["data"] else r["error"]
        if classify_promo_error(err)[0] == "token":
            # Session problem, not a verdict about the coupon (780 of these were once
            # counted as "invalid for item"). Refresh the token and retry.
            print(f"   [DEBUG] Coupon API token error (attempt {attempt+1}/3) — refreshing token: {str(err)[:80]}")
            try:
                zoro.ensure_token(driver)
            except Exception:
                pass
            time.sleep(1.0)
            continue
        if _is_transient_coupon_error(err):
            print(f"   [DEBUG] Transient coupon API error (attempt {attempt+1}/3) — retrying: {str(err)[:90]}")
            time.sleep(1.2 * (attempt + 1))
            continue
        _time("_apply_coupon_api", t0)
        print(f"   [DEBUG] Coupon API error: {err}")
        return False, err
    else:
        # 3 transient failures in a row — the cart/API is busy, NOT a bad coupon
        _time("_apply_coupon_api", t0)
        print(f"   [DEBUG] Coupon API still transient after 3 tries: {str(err)[:120]}")
        return False, "TRANSIENT: " + str(err)

    # Verify via cart GET
    time.sleep(0.3)
    cart = _get_cart(driver)
    if cart:
        promo = cart.get("promotion", {})
        applied = cart.get("promotionApplied", False)
        discount = cart.get("discountTotal", 0)
        print(f"   [DEBUG] Cart after coupon: promotionApplied={applied}, discountTotal={discount}, promotion={json.dumps(promo)[:200]}")
        if applied or discount > 0:
            _time("_apply_coupon_api", t0)
            print(f"  ✅ Coupon applied: {coupon_code} (discount: ${discount})")
            return True, discount

    _time("_apply_coupon_api", t0)
    print(f"  ⚠️  Coupon {coupon_code}: API OK but cart shows no discount — not counted as applied")
    return False, "NO_DISCOUNT: API accepted the code but the cart shows no discount"


def _apply_coupons_api(driver, cart_id, coupon_list, priority_coupons,
                        asin, asin_coupon_cache, zoro_code=None,
                        preferred_coupon=None):
    """Coupon selection on the live cart. The ORDER is a reliability rule:
      1. every PRIORITY coupon (the weekly 15–20 % codes; the loader sorts them
         best-% first) — the first one that applies wins;
      2. then the preferred / remembered REGULAR code (if any), then the other regulars.
    A remembered code jumps the queue ONLY when it is itself the top priority coupon,
    so a cached 10 % regular can never pre-empt a 15–20 % priority code.
    If EVERY priority attempt failed for session/transient reasons the result is
    COUPON_RETRY (the order waits for the next cycle) — never a silent fallback to a
    regular coupon during a bad API window.
    Returns ("SUCCESS", code) | ("COUPON_RETRY", None) | ("COUPON FAIL", None)."""
    t0 = time.perf_counter()
    cache_key = zoro_code or asin or ""
    _cache = asin_coupon_cache if asin_coupon_cache is not None else {}   # an empty dict is still THE cache
    working_pairs = _cache.setdefault("working_pairs", {})
    pri = []
    for c in (priority_coupons or []):
        c = str(c or "").strip()
        if c and c not in pri:
            pri.append(c)
    pri_set = set(pri)
    reg = []
    for c in (coupon_list or []):
        c = str(c or "").strip()
        if c and c not in pri_set and c not in reg:
            reg.append(c)
    # The coupon sheet is read once per cycle, so a regular code that dies or gets used DURING the
    # cycle stayed in the list: 12 Oct 2026, ~40 dead EMZM codes sat ahead of the live ones and
    # every DM_COUPON order re-tried all of them (~80 s each order) before one worked.
    # The metadata file is the record (update_coupon_status writes it on every death / use).
    try:
        from utils.helpers import load_coupon_metadata
        _meta = load_coupon_metadata()
    except Exception:
        _meta = {}
    _gone = {c for c in reg if _meta.get(c) in ("USED", "INVALID")}
    if _gone:
        reg = [c for c in reg if c not in _gone]
        print(f"  ⏭️  {len(_gone)} regular coupon(s) used or dead since the sheet was loaded — skipped")

    def _win(code, how):
        cart = _get_cart(driver)
        discount = float(cart.get("discountTotal", 0) or 0) if cart else 0.0
        print(f"  ✅ {how}: {code} (discount: ${discount:.2f})")
        if cache_key:
            working_pairs[cache_key] = code
        _cache.pop("last_fail", None)
        _time("_apply_coupons_api (total)", t0)
        return "SUCCESS", code

    known = working_pairs.get(cache_key) if cache_key else None
    tried = set()
    kinds_all = []                      # (tier, kind) of every rejection, so the placer can decide what a FAIL means

    # ── 0. remembered PRIORITY code — only when it is already the best one ──
    if known and pri and known == pri[0]:
        print(f"   [DEBUG] Fast-track remembered priority coupon: {known}")
        ok, res = _apply_coupon_api(driver, cart_id, known)
        tried.add(known)
        if ok:
            return _win(known, "Remembered priority coupon worked")
        kinds_all.append(("priority", _note_reject(known, res, _cache, priority=True)))

    # ── 1. priority coupons, best % first ──
    if pri:
        print(f"  🏷️  Trying {len(pri)} priority coupon(s) (best % first)...")
        kinds = []
        for code in pri:
            if code in tried:
                continue
            ok, res = _apply_coupon_api(driver, cart_id, code)
            if ok:
                return _win(code, "Priority coupon worked")
            kinds.append(_note_reject(code, res, _cache, priority=True))
            kinds_all.append(("priority", kinds[-1]))
        if kinds and all(k in ("token", "transient") for k in kinds):
            print("  ⏸️  Every priority coupon failed on a session/transient error — NOT falling back "
                  "to regular coupons; the order waits for the next cycle")
            _time("_apply_coupons_api (total)", t0)
            return "COUPON_RETRY", None

    # ── 2. regular coupons: preferred / remembered first, then the rest ──
    order = []
    for c in (preferred_coupon, known):
        if c and c in reg and c not in order:
            order.append(c)
    order += [c for c in reg if c not in order]
    if preferred_coupon and preferred_coupon not in pri_set and preferred_coupon not in reg:
        print(f"   [DEBUG] preferred coupon {preferred_coupon} is not in today's pool — skipped")
    if order:
        print(f"  🏷️  Trying {len(order)} regular coupon(s)...")
        for code in order:
            ok, res = _apply_coupon_api(driver, cart_id, code)
            if ok:
                return _win(code, "Regular coupon worked")
            kinds_all.append(("regular", _note_reject(code, res, _cache)))

    from collections import Counter as _Counter
    _cnt = _Counter(k for _, k in kinds_all)
    def _tier(t, k):
        return sum(1 for tt, kk in kinds_all if tt == t and (kk == k or (k == "transient" and kk == "token")))
    # Why nothing applied — read by engine/orders.py to decide between 'this item can never
    # take a coupon → reseller', 'cart too small → reseller if its margin is fine' and 'wait'.
    # Per-tier counters let it ignore DEAD codes: an expired code is not a verdict on the item.
    _cache["last_fail"] = {"total": len(kinds_all), "item": _cnt.get("item", 0), "min_order": _cnt.get("min_order", 0),
                           "dead": _cnt.get("dead", 0), "transient": _cnt.get("transient", 0) + _cnt.get("token", 0),
                           "unknown": _cnt.get("unknown", 0), "priority": len(pri), "regular_available": len(reg),
                           "regular_item": _tier("regular", "item"), "regular_dead": _tier("regular", "dead"),
                           "regular_min_order": _tier("regular", "min_order"), "regular_transient": _tier("regular", "transient"),
                           "priority_item": _tier("priority", "item"), "priority_dead": _tier("priority", "dead"),
                           "priority_min_order": _tier("priority", "min_order"), "priority_transient": _tier("priority", "transient")}
    _time("_apply_coupons_api (total)", t0)
    print(f"  ❌ No coupons worked ({_cnt.get('item', 0)} not-for-item, {_cnt.get('min_order', 0)} cart-minimum, "
          f"{_cnt.get('transient', 0) + _cnt.get('token', 0)} transient; {len(reg)} regular in the pool)")
    return "COUPON FAIL", None


# ==============================================================================
#  PAYMENT & ORDER SUBMISSION
# ==============================================================================

def _get_credit_cards(driver):
    """Get saved credit cards. Returns list."""
    t0 = time.perf_counter()
    print("   [DEBUG] GET /customer/v1/account/creditcard")
    r = _api(driver, "GET", "/customer/v1/account/creditcard")
    _time("_get_credit_cards", t0)
    if r["ok"]:
        data = r["data"]
        cards = []
        if isinstance(data, list):
            cards = data
        elif isinstance(data, dict):
            cards = data.get("cards", data.get("items", []))
        print(f"   [DEBUG] Found {len(cards)} saved card(s)")
        for c in cards:
            print(f"      Card: {c.get('creditCardProvider')} ****{c.get('number')} expires {c.get('expiryMonth')}/{c.get('expiryYear')} id={c.get('id')[:8]}")
        return cards
    print(f"   [DEBUG] Credit card fetch failed: {r['error']}")
    return []


def _build_invoice_payment():
    """Build the Net30/terms paymentMethod payload.

    Zoro's checkout calls Net30 the "invoice" tab (data-za="invoiceTab", seen in
    the net30_before debug dump of order WB4311978118), and the order API's
    accepted payment types are CREDIT_CARD / PAYPAL / INVOICE — so Net30 terms
    submit as type INVOICE.
    """
    payment = {
        "type": "INVOICE",
        "deviceFingerprintId": str(uuid.uuid4()),
    }
    print(f"   [DEBUG] Built payment method: {json.dumps(payment)}")
    return payment


def _build_credit_card_payment(cards):
    """Build paymentMethod payload from saved cards."""
    if not cards:
        print("   [DEBUG] _build_credit_card_payment: no cards!")
        return None
    card = cards[0]
    for c in cards:
        if c.get("isDefault"):
            card = c
            break

    card_type = (card.get("creditCardProvider") or "MASTERCARD").upper()
    card_type = card_type.replace(" ", "_").replace("-", "_")
    mapping = {
        "MASTER_CARD": "MASTERCARD",
        "MASTER": "MASTERCARD",
        "VISA": "VISA",
        "AMERICAN_EXPRESS": "AMEX",
        "AMEX": "AMEX",
        "DISCOVER": "DISCOVER",
    }
    card_type = mapping.get(card_type, card_type)

    expiry = str(card.get("expiryYear", ""))
    if len(expiry) == 4:
        expiry = expiry[2:]

    payment = {
        "type": "CREDIT_CARD",
        "cardType": card_type,
        "expirationMonth": str(card.get("expiryMonth", "")),
        "expirationYear": expiry,
        "deviceFingerprintId": str(uuid.uuid4()),
        "selectedPaymentId": card.get("id", ""),
        "tokenType": "permanent",
    }
    print(f"   [DEBUG] Built payment method: {json.dumps(payment)}")
    return payment


def _submit_order_api(driver, cart_id, payment_method, billing_addr, po_number=""):
    """Submit order via API. Returns (wb_order_id, response_data) or (None, None).
    billing_addr is the hardcoded account default billing address."""
    t0 = time.perf_counter()
    payload = {
        "email": "",
        "extraInfo": {
            "emailSubscription": True,
            "intendedForExport": False,
            "purchaseNumber": po_number or "",
        },
        "billingAddress": billing_addr,
    }
    if payment_method:
        payload["paymentMethod"] = payment_method

    print(f"\n   [DEBUG] >>> ORDER SUBMISSION <<<")
    print(f"   [DEBUG] POST /v1/cart/{cart_id}/order")
    _write_raw_api_log("ORDER SUBMISSION PAYLOAD", payload)
    print(f"   [DEBUG] Full payload written to raw_api_log.txt")

    r = _api(driver, "POST", f"/v1/cart/{cart_id}/order", payload)
    print(f"\n   [DEBUG] Order submit response status: {r['status']}")
    if isinstance(r['data'], dict):
        print(f"   [DEBUG] Order response keys: {list(r['data'].keys())}")
        _write_raw_api_log("ORDER SUBMISSION RESPONSE", r['data'])
        print(f"   [DEBUG] Full response written to raw_api_log.txt")

    if not r["ok"]:
        _time("_submit_order_api", t0)
        print(f"   [DEBUG] Order submit FAILED ({r['status']}): {r['error']}")
        return None, None

    data = r["data"]
    wb_id = None
    if isinstance(data, dict):
        wb_id = (data.get("orderNumber") or data.get("orderNo")
                 or data.get("orderId") or data.get("wbOrderId"))
        if not wb_id:
            for v in data.values():
                if isinstance(v, str) and v.startswith("WB"):
                    wb_id = v
                    break
    _time("_submit_order_api", t0)
    if wb_id:
        print(f"  ✅ Order placed via API: {wb_id}")
        return wb_id, data

    print(f"  ⚠️  Order API succeeded but no WB ID in response")
    return None, data


# ==============================================================================
#  ORDER AUDIT LOG
# ==============================================================================

def _ship_from_address(address):
    """The shipping address in the shape Zoro's order response uses, from the buyer address we
    prepared (platforms.amazon.validate_and_prepare_address). Empty dict when there is none."""
    if not isinstance(address, dict):
        return {}
    parts = str(address.get("name") or address.get("original_name") or "").split()
    out = {"firstName": parts[0] if parts else "",
           "lastName": " ".join(parts[1:]),
           "address1": address.get("street_address") or "",
           "city": address.get("city") or "",
           "state": address.get("state_abbr") or "",
           "zip": address.get("zip_code") or ""}
    return out if any(v for v in out.values()) else {}


def _save_order_audit(wb_id, zoro_response, amazon_id, sku, qty, asin,
                       earnings, checkout_total, profit, margin,
                       used_coupon, profile, address, timing_seconds,
                       merchant_id="", amazon_order_qty=None,
                       zoro_multiple=None, qty_multiplier=None,
                       item_title="", est_shipping=None,
                       est_profit=None, est_margin=None,
                       original_sku=None, shipping_amt=None,
                       amazon_fees=None, alt_of=None, zoro_select=None,
                       tax=None, discount=None, list_price=None, page_summary=None):
    """Append a detailed multi-line order audit block to a single master log file.
    Cost falls back to the cart total we computed at checkout when the order was placed by
    the DOM fallback (no API order response): 16–36 rows/day were landing in the DB with
    zoro_order_total NULL because this line said Cost:N/A (17 Sep 2026).
    alt_of = the MAIN Zoro code when this order was placed with its ALTERNATIVE
    (main was OOS); None for a normal placement.
    zoro_select = True/False when known — whether the Zoro Select discount is
    already baked into this order's cost (reseller profiles only). Recorded so
    the seller note can state whether the margin includes it; None = unknown."""
    try:
        log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "rajar_logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "order_log.txt")

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # ── Extract from Zoro response ──
        summary = zoro_response.get("orderSummary", {}) if isinstance(zoro_response, dict) else {}
        line_items = zoro_response.get("lineItems", []) if isinstance(zoro_response, dict) else []
        payments = zoro_response.get("payments", []) if isinstance(zoro_response, dict) else []
        ship = zoro_response.get("shippingAddress", {}) if isinstance(zoro_response, dict) else {}
        bill = zoro_response.get("billingAddress", {}) if isinstance(zoro_response, dict) else {}
        # Page checkout has no order response, so Zoro's copy of the shipping address is absent
        # and this line logged an empty ShipName / ShipStreet / ShipCity / ShipState / ShipZIP —
        # about a fifth of every order, which is exactly what reached order_placer_orders.
        # It is the same address we sent: use ours when Zoro's is missing.
        if not ship:
            ship = _ship_from_address(address)

        # ── Format helpers ──
        def fmt_money(v):
            return f"${v:.2f}" if v is not None else "N/A"
        def fmt_pct(v):
            return f"{v*100:.1f}%" if v is not None else "N/A"
        def fmt_int(v):
            return str(v) if v is not None else "N/A"
        def fmt_str(v):
            return str(v) if v else "N/A"

        # ── Build items table ──
        items_lines = []
        for idx, item in enumerate(line_items, start=1):
            if isinstance(item, dict):
                items_lines.append(
                    f"  [{idx}] {item.get('zoroProductId','?')} x{item.get('quantityPurchased',1)}"
                    f"  @ ${item.get('subTotal',0):.2f}  ({item.get('name','')[:50]})"
                )
        items_block = "\n".join(items_lines) if items_lines else f"  [1] {sku} x{qty}"

        # ── Payment info ──
        payment_str = "N/A"
        if payments and isinstance(payments[0], dict):
            p = payments[0]
            if (p.get("paymentType") or "").upper() == "INVOICE":
                payment_str = "INVOICE (Net30)"
            else:
                payment_str = f"{p.get('cardType','?')} ****{p.get('cardNumber','?')} (exp {p.get('cardExpiration','?')})"

        # ── Address blocks ──
        ship_name = f"{ship.get('firstName','')} {ship.get('lastName','')}".strip()
        bill_name = f"{bill.get('firstName','')} {bill.get('lastName','')}".strip()

        # ── Sanitize helper for one-liner (remove pipes and newlines) ──
        def s(v):
            return str(v).replace("|", "-").replace("\n", " ").strip()

        # ── Compact one-liner (machine-readable, ALL fields) ──
        # API order response first; the checkout page's own summary when there is none (page
        # checkout). Both are Zoro's numbers. Nothing here is computed from anything else.
        _pg = page_summary if isinstance(page_summary, dict) else {}
        _subtotal = summary.get('subTotal') if summary.get('subTotal') is not None else _pg.get('subtotal')
        _discount = (summary.get('discountTotal') if summary.get('discountTotal') is not None
                     else (discount if discount is not None else _pg.get('discount')))
        _shipping = summary.get('shippingTotal') if summary.get('shippingTotal') is not None else _pg.get('shipping')
        _tax = (summary.get('taxTotal') if summary.get('taxTotal') is not None
                else (tax if tax is not None else _pg.get('tax')))
        # What Zoro charged: order response total, else the page total, else the cart total.
        _charged = (summary.get('total') if summary.get('total') is not None
                    else (_pg.get('total') if _pg.get('total') else checkout_total))

        one_liner = (
            f"[{now}] | WB:{s(wb_id)} | Profile:{s(profile)} | Amazon:{s(amazon_id)}"
            f" | ASIN:{s(asin)} | Title:{s(item_title)} | AQty:{s(amazon_order_qty)}"
            f" | SKU:{s(sku)} | ZMultiple:{s(zoro_multiple)} | ZMultiplier:{s(qty_multiplier)}"
            f" | ZQty:{s(qty)} | Earnings:{s(fmt_money(earnings))} | Cost:{s(fmt_money(_charged))} | ListPrice:{s(fmt_money(list_price))}"
            f" | Profit:{s(fmt_money(profit))} | FinalMargin:{s(fmt_pct(margin))} | EstMargin:{s(fmt_pct(est_margin))}"
            f" | Coupon:{s(used_coupon or 'None')} | Status:{s(zoro_response.get('orderStatus') if isinstance(zoro_response, dict) else 'N/A')}"
            f" | Subtotal:{s(fmt_money(_subtotal))} | Discount:{s(fmt_money(_discount))}"
            f" | Shipping:{s(fmt_money(_shipping))} | Tax:{s(fmt_money(_tax))}"
            f" | Payment:{s(payment_str)} | ShipName:{s(ship_name)} | ShipStreet:{s(ship.get('address1',''))}"
            f" | ShipCity:{s(ship.get('city',''))} | ShipState:{s(ship.get('state',''))} | ShipZIP:{s(ship.get('zip',''))}"
            f" | ShipPhone:{s(address.get('phone',''))}"
            f" | BillName:{s(bill_name)} | Time:{s(f'{timing_seconds:.1f}s' if timing_seconds else 'N/A')}"
            f" | OriginalSKU:{s(original_sku)} | ShippingAmt:{s(fmt_money(shipping_amt))} | AmazonFees:{s(fmt_money(amazon_fees))}"
            f" | PurchaseDate:{s(address.get('_purchase_date',''))} | ShipBy:{s(address.get('_ship_by_date',''))} | DeliverBy:{s(address.get('_deliver_by_date',''))}"
            f" | TrackingIDs:{s(address.get('_tracking_ids',''))} | ShippedEmailTime:{s(address.get('_shipped_email_time',''))}"
            f" | ZoroDeliverBy:{s(address.get('_zoro_deliver_by',''))} | ZoroStock:{s(address.get('_zoro_stock',''))}"
            f" | AltUsed:{'Yes' if alt_of else 'No'} | MainSKU:{s(alt_of) if alt_of else 'N/A'}"
            f" | ZSelect:{'Yes' if zoro_select else ('No' if zoro_select is not None else 'Unknown')}\n"
        )

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(one_liner)

        print(f"   📋 Order audit logged: data/rajar_logs/order_log.txt")

        # Sync to Google Sheets in background (non-blocking)
        sync_order_log_async()
    except Exception as e:
        print(f"   ⚠️  Failed to save order audit: {e}")


def _maybe_add_shipping_filler(driver, cart_id):
    """If the cart's LIST subtotal is close to $50 but under, add a cheap filler SKU to unlock
    free shipping. Returns (added, filler_qty, filler_price) or (False, 0, 0).

    Zoro measures the $50 on the price before discounts. The cart's priceTotal is after the
    Zoro Select discount, so it is the wrong number: WB1228729288 listed at $54.75, showed
    $49.27 after Select, and got 2 filler units it did not need. discountTotal is what the
    Select discount took off, so priceTotal + discountTotal is the figure Zoro checks."""
    from utils.config import (
        SHIPPING_FILLER_SKU, SHIPPING_FILLER_PRICE,
        FREE_SHIPPING_THRESHOLD, FILLER_TRIGGER_MIN, FILLER_MAX_QTY
    )
    if not SHIPPING_FILLER_SKU:
        return False, 0, 0

    cart = _get_cart(driver)
    if not cart:
        return False, 0, 0

    # priceTotal is after the Select discount; add the discount back to get the list subtotal.
    # The filler runs before any coupon is applied, so discountTotal is only the Select discount.
    subtotal = float(cart.get("priceTotal", 0) or cart.get("subtotal", 0) or 0)
    discount = float(cart.get("discountTotal", 0) or 0)
    list_subtotal = subtotal + discount
    if list_subtotal >= FREE_SHIPPING_THRESHOLD:
        if subtotal < FREE_SHIPPING_THRESHOLD:
            print(f"   [FILLER] Not needed: ${subtotal:.2f} after discount, but ${list_subtotal:.2f} "
                  f"list — Zoro ships free on the list price")
        return False, 0, 0
    if list_subtotal < FILLER_TRIGGER_MIN:
        return False, 0, 0

    gap = FREE_SHIPPING_THRESHOLD - list_subtotal
    filler_qty = min(math.ceil(gap / SHIPPING_FILLER_PRICE), FILLER_MAX_QTY)
    if filler_qty < 1:
        return False, 0, 0

    # Check inventory
    cartable, status = zoro.check_inventory(driver, SHIPPING_FILLER_SKU, filler_qty)
    if not cartable:
        print(f"   [FILLER] Filler OOS ({SHIPPING_FILLER_SKU} x{filler_qty}): {status}")
        return False, 0, 0

    # Add filler to cart
    ok, actual_price = _add_item_api(driver, SHIPPING_FILLER_SKU, filler_qty)
    if ok:
        print(f"   [FILLER] Added {filler_qty}x {SHIPPING_FILLER_SKU} @ ${actual_price:.2f} "
              f"(list subtotal ${list_subtotal:.2f} → ${list_subtotal + actual_price * filler_qty:.2f}, "
              f"shipping now FREE)")
        return True, filler_qty, actual_price
    else:
        print(f"   [FILLER] Failed to add filler {SHIPPING_FILLER_SKU}")
        return False, 0, 0


# ==============================================================================
#  RATIO-COUPON SPLIT HELPERS
# ==============================================================================

def _compute_ratio_split(qty, unit_price, tiers):
    """DP: find optimal qty partition for a single ratio coupon's tiers.
    Returns (max_total_discount, [group_sizes]).
    """
    dp = [(0, [])]  # dp[0]
    for i in range(1, qty + 1):
        best_disc = 0
        best_parts = None
        for g in range(1, i + 1):
            subtotal = unit_price * g
            disc = 0
            for tier in tiers:
                if subtotal >= tier.get("min_order", 0):
                    disc = max(disc, tier["fixed_amount"])
            prev_disc, prev_parts = dp[i - g]
            total_disc = prev_disc + disc
            if total_disc > best_disc:
                best_disc = total_disc
                best_parts = prev_parts + [g]
        dp.append((best_disc, best_parts))
    return dp[qty]


# ==============================================================================
#  MAIN ENTRY POINT
# ==============================================================================

def place_order_fast_true_api(driver, sku, qty, address, po_number, active_profile,
                               earnings=None, pre_price=None, coupon_list=None,
                              list_price=None,
                               asin=None, asin_coupon_cache=None,
                               priority_coupons=None,
                               skip_priority_coupons=False,
                               min_margin=None, return_details=False, zoro_code=None,
                               second_sku=None, extra_skus=None,
                               amazon_id="", merchant_id="",
                               amazon_order_qty=None, zoro_multiple=None,
                               qty_multiplier=None, item_title="",
                               est_shipping=None, est_profit=None,
                               est_margin=None,
                               original_sku=None, shipping_amt=None,
                               amazon_fees=None, preferred_coupon=None,
                               coupon_meta_by_code=None, alt_of=None,
                               payment_method="CREDIT_CARD",
                               split_coupon_only=None, zoro_select=None):
    """Fast checkout using true REST APIs for all steps.
    
    Splitting is decided AT CHECKOUT TIME: after a ratio/fixed-amount coupon
    is applied, we compute whether splitting the order would increase the
    total discount.  Percentage coupons never trigger splits.
    """
    t_start = time.perf_counter()
    print(f"\n{'='*60}")
    print(f"  ⚡ True API Checkout: {sku} x{qty}")
    print(f"  Profile: {active_profile}")
    print(f"{'='*60}")
    bot_log("info", "True API checkout started", sku=sku, qty=qty)
    priority_coupons = priority_coupons or []
    coupon_meta_by_code = coupon_meta_by_code or {}

    try:
        print("\n[1] ensure_on_site + ensure_token")
        zoro.ensure_on_site(driver)
        zoro.ensure_token(driver)

        # ── 1. Price / Inventory ──
        if pre_price is not None:
            price = pre_price
            print(f"\n[2] Using pre_price=${price}")
        else:
            print(f"\n[2] check_inventory({sku}, {qty})")
            cartable, status = zoro.check_inventory(driver, sku, qty)
            print(f"   inventory result: cartable={cartable}, status={status}")
            if not cartable:
                return _format_checkout_result("OOS", return_details=return_details)
            print(f"\n[2b] clear_cart_and_add (legacy for pre_price)")
            ok, price = zoro.clear_cart_and_add(driver, sku, qty)
            print(f"   legacy add result: ok={ok}, price={price}")
            if not ok:
                return _format_checkout_result("FAILED", return_details=return_details)

        # ── 2. Clear cart & add via API ──
        print(f"\n[3] _clear_cart_api()")
        if not _clear_cart_api(driver):
            return _format_checkout_result("FAILED", return_details=return_details)

        print(f"\n[4] _add_item_api({sku}, {qty})")
        ok, price = _add_item_api(driver, sku, qty)
        if not ok:
            return _format_checkout_result("FAILED", return_details=return_details)

        if second_sku:
            print(f"\n[4b] _add_item_api(second_sku={second_sku}, {qty})")
            s_ok, s_price = _add_item_api(driver, second_sku, qty)
            if s_ok:
                price += s_price
                print(f"   2nd item added ok, price=${s_price}")
            else:
                print(f"   ⚠️ 2nd item failed to add, continuing with primary only")

        if extra_skus:
            for extra_idx, (extra_sku, extra_qty) in enumerate(extra_skus, start=1):
                print(f"\n[4c] _add_item_api(extra_item {extra_idx}: {extra_sku}, {extra_qty})")
                e_ok, e_price = _add_item_api(driver, extra_sku, extra_qty)
                if e_ok:
                    price += e_price
                    print(f"   extra item added ok, price=${e_price}")
                else:
                    print(f"   ⚠️ extra item failed to add, continuing")

        cart_id = _get_cart_id(driver)
        print(f"\n[5] Cart ID = {cart_id}")
        if not cart_id:
            return _format_checkout_result("FAILED", return_details=return_details)

        # ── 2b. Shipping filler (if cart is close to $50 threshold) ──
        filler_added, filler_qty, filler_price = _maybe_add_shipping_filler(driver, cart_id)
        if filler_added:
            price += filler_price * filler_qty

        # ── 3. Address ──
        print(f"\n[6] _update_or_create_address()")
        addr_dict = _update_or_create_address(driver, address)
        print(f"   addr_dict id={addr_dict.get('id') if addr_dict else 'NONE'}")
        if not addr_dict:
            return _format_checkout_result("FAILED", return_details=return_details)

        print(f"\n[7] _set_cart_shipping_address(cart={cart_id})")
        ship_result = _set_cart_shipping_address(driver, cart_id, addr_dict)
        if ship_result == "PO_BOX":
            return _format_checkout_result("PO_BOX_RESTRICTED", return_details=return_details)
        if not ship_result:
            return _format_checkout_result("FAILED", return_details=return_details)

        # ── 4. Shipping ──
        print(f"\n[8] _get_shipping_methods()")
        methods = _get_shipping_methods(driver, cart_id, addr_dict)
        if methods is None:
            state = address.get("state", address.get("state_abbr", "?"))
            print(f"   [DEBUG] restrictedSkus detected for state {state}")
            return _format_checkout_result("STATE_RESTRICTED", return_details=return_details)
        print(f"   methods count={len(methods)}")
        if not methods:
            return _format_checkout_result("FAILED", return_details=return_details)

        print(f"\n[9] _select_shipping_method_api()")
        if not _select_shipping_method_api(driver, cart_id, methods):
            return _format_checkout_result("FAILED", return_details=return_details)

        # ── 5. Coupon ──
        used_coupon_code = None
        if split_coupon_only:
            # Split sub-order: ONLY the coupon that justified the split may be
            # used. The remainder split often can't meet that coupon's minimum;
            # placing it couponless always beats burning a single-use signup
            # code for pocket change (a $1.84 discount once cost a whole
            # hand-generated coupon). The coupon caches are also left alone —
            # one restricted try proves nothing about the item.
            print(f"\n[10] Split mode: trying ONLY the split coupon {split_coupon_only}")
            _ok, _res = _apply_coupon_api(driver, cart_id, split_coupon_only)
            if _ok:
                _cart = _get_cart(driver)
                _disc = float(_cart.get("discountTotal", 0) or 0) if _cart else 0.0
                print(f"  ✅ Split coupon applied: {split_coupon_only} (discount: ${_disc:.2f})")
                used_coupon_code = split_coupon_only
            else:
                print(f"  ⏭️  Split coupon didn't apply ({str(_res)[:60]}) — "
                      f"placing WITHOUT coupon (signup codes stay reserved for full carts)")
        elif coupon_list:
            print(f"\n[10] _apply_coupons_api({len(coupon_list)} coupons)")
            print(f"   [DEBUG] coupon_list len={len(coupon_list)}, first 10={coupon_list[:10]}")
            print(f"   [DEBUG] priority_coupons len={len(priority_coupons)}, vals={priority_coupons}")
            coupon_result, used_coupon_code = _apply_coupons_api(
                driver, cart_id, coupon_list, priority_coupons,
                asin, asin_coupon_cache, zoro_code=zoro_code,
                preferred_coupon=preferred_coupon)
            print(f"   coupon_result={coupon_result}, used={used_coupon_code}")
            if coupon_result in ("COUPON FAIL", "COUPON_RETRY", "NET30_RESTRICTED", "NO_COUPONS_SKIP"):
                return _format_checkout_result(coupon_result, return_details=return_details)
        else:
            print(f"\n[10] No coupons to apply")

        # ── 5b. Ratio-coupon split check (AT CHECKOUT TIME) ──
        # If a ratio/fixed-amount coupon just worked, check whether splitting
        # the order would increase the total discount with the SAME coupon.
        # Percentage coupons NEVER trigger splits. Never re-triggers inside a
        # split sub-order (split_coupon_only set) — the split loop would treat
        # a nested SPLIT_RETRY as a failed split and abort the remaining parts.
        if used_coupon_code and qty > 1 and not split_coupon_only:
            meta = coupon_meta_by_code.get(used_coupon_code)
            if meta and isinstance(meta, list) and any("fixed_amount" in m for m in meta):
                tiers = [m for m in meta if "fixed_amount" in m]
                cart = _get_cart(driver)
                current_discount = float(cart.get("discountTotal", 0) or 0) if cart else 0.0
                # Use priceTotal / qty as unit price (Zoro Select already applied)
                price_total = float(cart.get("priceTotal", 0) or 0) if cart else 0.0
                unit_price = price_total / qty if qty > 0 else 0.0
                if unit_price > 0 and tiers:
                    best_split_disc, split_parts = _compute_ratio_split(qty, unit_price, tiers)
                    if best_split_disc > current_discount + 0.01:
                        gap = best_split_disc - current_discount
                        print(f"\n  ⚠️  RATIO SPLIT SAVES ${gap:.2f}!")
                        print(f"     Coupon: {used_coupon_code}")
                        print(f"     Single discount: ${current_discount:.2f}")
                        print(f"     Split discount:  ${best_split_disc:.2f}  (plan: {split_parts})")
                        print(f"  🔄 Aborting single order — will retry as split...")
                        _clear_cart_api(driver)
                        return _format_checkout_result(
                            "SPLIT_RETRY",
                            split_plan=[{"qty": q} for q in split_parts],
                            used_coupon_code=used_coupon_code,
                            return_details=return_details)

        # ── 5c. Re-validate shipping after coupon ──
        print(f"\n[10b] _revalidate_shipping_after_coupon()")
        if not _revalidate_shipping_after_coupon(driver, cart_id, addr_dict):
            return _format_checkout_result("FAILED", return_details=return_details)

        # ── 6. Checkout total from cart API ──
        print(f"\n[11] _get_cart() for final total")
        time.sleep(0.5)
        cart = _get_cart(driver)
        checkout_total = None
        checkout_pre_tax = None
        if cart:
            checkout_total = float(cart.get("total", 0) or 0)
            checkout_pre_tax = float(cart.get("priceTotal", 0) or 0)
            if checkout_total <= 0:
                checkout_total = checkout_pre_tax
            print(f"   cart.total=${cart.get('total')}, cart.priceTotal=${cart.get('priceTotal')}, computed=${checkout_total}")

        profit = None
        margin = None
        if checkout_total is not None and checkout_total > 0:
            print(f"  💰 Checkout total: ${checkout_total:.2f}")
            if earnings is not None:
                profit = round(earnings - checkout_total, 2)
                margin = profit / checkout_total if checkout_total > 0 else 0.0
                icon = "✅" if profit >= 0 else "⚠️"
                print(f"  {icon} Final Profit: ${profit:.2f}  |  Margin (incl tax): {margin:.1%}")
                # ── FINAL-MARGIN GATE on PRE-TAX cost ──────────────────────────
                # Sales tax is refunded on the resale accounts, so the grand total
                # (checkout_total) over-reports true cost and would falsely reject
                # profitable orders. Gate on the pre-tax total (cart priceTotal);
                # fall back to grand total only if pre-tax is unavailable.
                gate_basis = checkout_pre_tax if (checkout_pre_tax and checkout_pre_tax > 0) else checkout_total
                gate_margin = (earnings - gate_basis) / gate_basis if gate_basis > 0 else 0.0
                if gate_basis != checkout_total:
                    print(f"  🧾 Pre-tax cost ${gate_basis:.2f} → final margin {gate_margin:.1%}")
                if min_margin is not None and gate_margin < min_margin:
                    print(f"  🛑 LOW PROFIT — final-margin gate ({gate_margin:.1%} pre-tax < {min_margin:.0%}) — NOT placing")
                    return _format_checkout_result(
                        "LOW_PROFIT", checkout_total=checkout_total,
                        profit=profit, margin=margin, return_details=return_details)
        else:
            print("  ⚠️  Checkout total not found in cart API")

        # ── 7. Place Order ──
        print(f"\n[12] PLACE ORDER — profile={active_profile}")
        zoro_id = None
        zoro_response = None

        # ── Build billing payload from hardcoded profile config ──
        from utils.config import PROFILES
        profile_cfg = PROFILES.get(active_profile, {})
        hardcoded_billing = profile_cfg.get("billing_address", {})
        if hardcoded_billing and hardcoded_billing.get("address1"):
            billing_payload = {
                "address1": hardcoded_billing.get("address1", ""),
                "address2": hardcoded_billing.get("address2", ""),
                "companyName": hardcoded_billing.get("companyName", ""),
                "city": hardcoded_billing.get("city", ""),
                "fullName": f"{hardcoded_billing.get('firstName','')} {hardcoded_billing.get('lastName','')}".strip(),
                "firstName": hardcoded_billing.get("firstName", ""),
                "lastName": hardcoded_billing.get("lastName", ""),
                "isDefaultBilling": True,
                "isDefaultShipping": False,
                "phoneNumber": re.sub(r"\D", "", hardcoded_billing.get("phoneNumber", "")),
                "state": hardcoded_billing.get("state", ""),
                "zip": hardcoded_billing.get("zip", "").replace("-", ""),
            }
            print(f"   [DEBUG] Using hardcoded billing address for {active_profile}")
        else:
            # Fallback: fetch from account (only if not configured yet)
            print("   [DEBUG] No hardcoded billing — fetching default from account...")
            billing = _get_default_billing_addr(driver)
            if not billing:
                billing = addr_dict
            billing_payload = {
                "address1": billing.get("address1", billing.get("street_address", "")),
                "address2": billing.get("address2", billing.get("street_address_2", "")),
                "companyName": billing.get("companyName", ""),
                "city": billing.get("city", ""),
                "fullName": f"{billing.get('firstName','')} {billing.get('lastName','')}".strip(),
                "firstName": billing.get("firstName", ""),
                "lastName": billing.get("lastName", ""),
                "isDefaultBilling": True,
                "isDefaultShipping": False,
                "phoneNumber": re.sub(r"\D", "", billing.get("phoneNumber", billing.get("phone", ""))),
                "state": billing.get("state", billing.get("state_abbr", "")),
                "zip": billing.get("zip", billing.get("zip_code", "")).replace("-", ""),
            }

        # ── ATTRIBUTED CHECKOUT ──────────────────────────────────────────────
        # Finish on Zoro's real checkout page so their GTM/GA4 tags see the sale. Everything
        # before this (cart, address, shipping method, coupons) still went through the API, so
        # the coupon logic is untouched; only the final Place Order click moves to the page.
        # A page order that yields no order id is AMBIGUOUS, so it falls through to the normal
        # "ORDER FAILED" handling, which locks the order for review rather than retrying.
        from utils.helpers import page_checkout_wanted
        if page_checkout_wanted():
            print("   🧾 Attributed checkout ON — placing on Zoro's real /checkout page "
                  "(their analytics records this order)")
            zoro_id = _dom_place_order_fallback(
                driver, sku, qty, address, po_number, active_profile,
                earnings, checkout_total, profit, margin,
                coupon_list, asin, asin_coupon_cache,
                skip_priority_coupons, priority_coupons,
                min_margin, return_details, zoro_code,
                payment_method=payment_method)
        elif "RESELLER" in active_profile:
            if str(payment_method or "").upper() == "NET30":
                # Net30 terms = API type INVOICE (fast path, full response data).
                print("   RESELLER mode (Net30): API submit with INVOICE terms...")
                payment = _build_invoice_payment()
                zoro_id, zoro_response = _submit_order_api(
                    driver, cart_id, payment, billing_payload,
                    po_number=po_number)
                if not zoro_id:
                    if zoro_response is None:
                        # HTTP-level rejection — the order definitively does NOT
                        # exist, so the proven DOM route (Net30 is the account
                        # default there) can safely retry in the same run.
                        print("   Net30 INVOICE submit rejected — DOM fallback on Net30 terms...")
                        zoro_id = _dom_place_order_fallback(
                            driver, sku, qty, address, po_number, active_profile,
                            earnings, checkout_total, profit, margin,
                            coupon_list, asin, asin_coupon_cache,
                            skip_priority_coupons, priority_coupons,
                            min_margin, return_details, zoro_code,
                            payment_method="NET30")
                    else:
                        # HTTP 200 but no order id in the response — an order MAY
                        # exist. FAIL CLOSED: no DOM retry this run (a retry here
                        # is exactly the 2026-07-01 double-buy pattern). Check
                        # raw_api_log.txt / Zoro order history before releasing.
                        print("   ❌ Net30 submit ambiguous (HTTP 200, no order id) — "
                              "FAILING CLOSED, no retry this run. Check Zoro order history!")
            else:
                print("   RESELLER mode: fetching credit cards...")
                cards = _get_credit_cards(driver)
                if not cards:
                    print("   No cards — DOM fallback")
                    zoro_id = _dom_place_order_fallback(
                        driver, sku, qty, address, po_number, active_profile,
                        earnings, checkout_total, profit, margin,
                        coupon_list, asin, asin_coupon_cache,
                        skip_priority_coupons, priority_coupons,
                        min_margin, return_details, zoro_code)
                else:
                    payment = _build_credit_card_payment(cards)
                    zoro_id, zoro_response = _submit_order_api(
                        driver, cart_id, payment, billing_payload,
                        po_number="" if "COUPON" in active_profile else po_number)
        else:
            # COUPON profile — API requires paymentMethod.
            # The backend only accepts CREDIT_CARD, PAYPAL, or INVOICE.
            # NET_30 is NOT a valid API payment type.
            print("   COUPON mode: using saved credit card for API order...")
            cards = _get_credit_cards(driver)
            if cards:
                payment = _build_credit_card_payment(cards)
                zoro_id, zoro_response = _submit_order_api(driver, cart_id, payment, billing_payload, po_number="")
            else:
                print("   No saved credit cards — DOM fallback")

            if not zoro_id:
                print("\n   API order failed — DOM fallback")
                zoro_id = _dom_place_order_fallback(
                    driver, sku, qty, address, po_number, active_profile,
                    earnings, checkout_total, profit, margin,
                    coupon_list, asin, asin_coupon_cache,
                    skip_priority_coupons, priority_coupons,
                    min_margin, return_details, zoro_code)

        _time("True API TOTAL checkout", t_start)
        print(f"\n{'='*60}")
        if zoro_id:
            print(f"  🎉 ORDER RESULT: {zoro_id}")
            bot_log("info", "ORDER PLACED (True API)", wb_id=zoro_id, sku=sku,
                    qty=qty, total=checkout_total, profit=profit, margin=margin)
            if used_coupon_code and used_coupon_code != "AUTO_APPLIED":
                update_coupon_status(used_coupon_code, "USED")
            # Save comprehensive audit
            _save_order_audit(
                wb_id=zoro_id,
                zoro_response=zoro_response,
                amazon_id=amazon_id,
                sku=sku,
                qty=qty,
                asin=asin,
                earnings=earnings,
                checkout_total=checkout_total,
                profit=profit,
                margin=margin,
                used_coupon=used_coupon_code,
                profile=active_profile,
                address=address,
                timing_seconds=time.perf_counter() - t_start,
                merchant_id=merchant_id,
                amazon_order_qty=amazon_order_qty,
                zoro_multiple=zoro_multiple,
                qty_multiplier=qty_multiplier,
                item_title=item_title,
                est_shipping=est_shipping,
                est_profit=est_profit,
                est_margin=est_margin,
                original_sku=original_sku,
                shipping_amt=shipping_amt,
                amazon_fees=amazon_fees,
                alt_of=alt_of,
                zoro_select=zoro_select,
                list_price=list_price,
                page_summary=getattr(driver, "_zuhra_checkout_summary", None),
            )
        else:
            print(f"  ❌ ORDER FAILED")
            bot_log("error", "Place order FAILED (True API + fallback)", sku=sku)
        print(f"{'='*60}")

        # Sales tax Zoro actually charged, read from the order-confirmation
        # summary (authoritative; cart priceTotal excludes both tax and shipping
        # so it can't be derived from the cart). Surfaced into the seller note as
        # "T:" so a thin profit can be traced to tax.
        checkout_tax = None
        checkout_discount = None
        checkout_subtotal = None
        checkout_shipping = None
        try:
            if isinstance(zoro_response, dict):
                _summary = zoro_response.get("orderSummary", {}) or {}
                _tax = _summary.get("taxTotal")
                if _tax is not None:
                    checkout_tax = float(_tax)
                _disc = _summary.get("discountTotal")
                if _disc is not None:
                    checkout_discount = abs(float(_disc))
                if _summary.get("subTotal") is not None:
                    checkout_subtotal = float(_summary.get("subTotal"))
                if _summary.get("shippingTotal") is not None:
                    checkout_shipping = float(_summary.get("shippingTotal"))
        except (TypeError, ValueError):
            pass

        # No API response (page checkout): take the same numbers from the checkout page's
        # order summary, read just before Place Order was clicked. Its total is what Zoro
        # actually charged — tax and shipping included — so it replaces the cart total, which
        # excludes both and understated "Cost" on every attributed order.
        _page = getattr(driver, "_zuhra_checkout_summary", None) or {}
        if not isinstance(zoro_response, dict) and _page:
            if checkout_tax is None and _page.get("tax") is not None:
                checkout_tax = float(_page["tax"])
            if checkout_discount is None and _page.get("discount") is not None:
                checkout_discount = abs(float(_page["discount"]))
            if checkout_subtotal is None and _page.get("subtotal") is not None:
                checkout_subtotal = float(_page["subtotal"])
            if checkout_shipping is None and _page.get("shipping") is not None:
                checkout_shipping = float(_page["shipping"])
            if _page.get("total") and float(_page["total"]) > 0:
                checkout_total = float(_page["total"])

        return _format_checkout_result(
            "SUCCESS" if zoro_id else "FAILED",
            wb_id=zoro_id, checkout_total=checkout_total,
            profit=profit, margin=margin,
            return_details=return_details,
            used_coupon_code=used_coupon_code,
            tax=checkout_tax, discount=checkout_discount,
            subtotal=checkout_subtotal, shipping=checkout_shipping)

    except Exception as e:
        _time("True API TOTAL checkout (exception)", t_start)
        bot_log("error", "True API checkout exception", sku=sku, err=str(e)[:300])
        print(f"\n  ❌ True API checkout error: {e}")
        import traceback
        traceback.print_exc()
        print(f"  ↪️  Falling back to legacy checkout...")
        try:
            from checkout.dom import place_order_fast
            return place_order_fast(
                driver, sku, qty, address, po_number, active_profile,
                earnings=earnings, pre_price=pre_price, coupon_list=coupon_list,
                asin=asin, asin_coupon_cache=asin_coupon_cache,
                skip_priority_coupons=skip_priority_coupons,
                priority_coupons=priority_coupons,
                min_margin=min_margin, return_details=return_details,
                zoro_code=zoro_code, payment_method=payment_method)
        except Exception as fallback_err:
            print(f"  ❌ Fallback also failed: {fallback_err}")
            return _format_checkout_result("FAILED", return_details=return_details)


# ==============================================================================
#  DOM FALLBACK (only for final Place Order step)
# ==============================================================================

def _dom_place_order_fallback(driver, sku, qty, address, po_number, active_profile,
                               earnings, checkout_total, profit, margin,
                               coupon_list, asin, asin_coupon_cache,
                               skip_priority_coupons, priority_coupons,
                               min_margin, return_details, zoro_code,
                               payment_method="CREDIT_CARD"):
    """Minimal DOM fallback: navigate to checkout and click Place Order.
    Assumes cart/address/shipping/coupon are already set via API."""
    print("  🌐 DOM fallback: navigating to checkout for Place Order...")

    driver.get("https://www.zoro.com/checkout")
    # 30 s, not 12: with the "eager" page-load strategy driver.get returns at DOMContentLoaded,
    # before the checkout's own data requests finish (helpers.page_load_strategy). Exits as soon as ready.
    deadline = time.time() + 30
    while time.time() < deadline:
        ready = driver.execute_script("""
            var t = document.body.innerText;
            var hasPO = !!document.querySelector('[data-za="checkout-place-order-button"]');
            var noErr = !t.includes('Please specify a shipping method');
            return hasPO && noErr;
        """)
        if ready:
            print("   DOM fallback: checkout ready")
            break
        time.sleep(0.5)

    restricted = driver.execute_script(
        "return document.body.innerText.includes('cannot be shipped to the state specified');")
    if restricted:
        print("   DOM fallback: STATE_RESTRICTED detected")
        return _format_checkout_result("STATE_RESTRICTED", return_details=return_details)

    if "COUPON" not in active_profile and po_number:
        from checkout.dom import _set_po_number
        _set_po_number(driver, po_number)

    is_net30 = str(payment_method or "").upper() == "NET30"
    if "RESELLER" in active_profile:
        if is_net30:
            from checkout.dom import _ensure_net30_payment
            if not _ensure_net30_payment(driver):
                print("   ❌ Net30 configured but could not be confirmed at checkout — NOT placing")
                return None
        else:
            from checkout.dom import _switch_payment_to_credit_card
            _switch_payment_to_credit_card(driver)

    # Read the order summary NOW, while it is on screen. The page path has no API order
    # response to take tax/subtotal/shipping from afterwards, which is why every attributed
    # order since 18 Sep logged Tax:N/A and a pre-tax "Cost". Stashed on the driver so none of
    # the call sites that read this function's bare return value have to change.
    try:
        from checkout.dom import _extract_checkout_summary
        driver._zuhra_checkout_summary = _extract_checkout_summary(driver)
        _s = driver._zuhra_checkout_summary
        print(f"   🧾 Page summary: subtotal={_s['subtotal']} shipping={_s['shipping']} "
              f"tax={_s['tax']}{' (remainder of the total — the page showed no tax line)' if _s.get('tax_derived') else ''} "
              f"discount={_s['discount']} total={_s['total']}")
    except Exception as _sum_err:
        print(f"   ⚠️ Could not read the page summary ({type(_sum_err).__name__}) — continuing")
        try:
            driver._zuhra_checkout_summary = None
        except Exception:
            pass

    zoro_id = _click_place_order(driver)
    print(f"   DOM fallback result: {zoro_id}")
    if is_net30 and not zoro_id:
        # Net30 Place Order produced no order id — capture the page (error
        # banners, validation messages) so the failure can be debugged.
        from checkout.dom import _dump_checkout_debug
        _dump_checkout_debug(driver, tag="net30_place_fail")
    return zoro_id
