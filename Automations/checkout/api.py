"""
order_checkout_api.py — Fast checkout via Vuex store dispatch.

Instead of clicking DOM elements (coupon input, apply button, place order button),
this dispatches Vuex actions directly — each takes milliseconds vs 3-8 seconds.

Still loads the checkout page once (required to initialize Vue app),
but ALL operations on that page are instant Vuex dispatches.

Falls back to order_checkout.place_order_fast() if any step fails.
"""

import re
import time
import json
import functools

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import platforms.zoro as zoro
from utils.helpers import update_coupon_status
from checkout.dom import (
    _format_checkout_result, _wait_for_checkout_ready,
    _extract_checkout_total, _wait_until,
)
from utils.logger import bot_log

print = functools.partial(print, flush=True)


# ==============================================================================
#  VUEX STORE HELPERS
# ==============================================================================

def _get_store(driver):
    """Get a reference check — returns True if Vue store is accessible."""
    return driver.execute_script("""
        try {
            var app = document.querySelector('#app');
            return !!(app && app.__vue_app__ && app.__vue_app__.config.globalProperties.$store);
        } catch(e) { return false; }
    """)


def _dispatch(driver, action, payload=None):
    """Dispatch a Vuex action and return the result (awaits the Promise).
    Returns {ok: bool, data: any, error: string}."""
    payload_json = json.dumps(payload) if payload is not None else "undefined"
    result = driver.execute_script(f"""
        var store = document.querySelector('#app').__vue_app__.config.globalProperties.$store;
        var payload = {payload_json};
        try {{
            var promise = store.dispatch('{action}', payload);
            // If it returns a promise, we can't await in sync execute_script.
            // Instead, store the result on window and poll.
            window.__vuex_dispatch_done = false;
            window.__vuex_dispatch_result = null;
            window.__vuex_dispatch_error = null;
            Promise.resolve(promise).then(function(result) {{
                window.__vuex_dispatch_done = true;
                window.__vuex_dispatch_result = result;
            }}).catch(function(err) {{
                window.__vuex_dispatch_done = true;
                window.__vuex_dispatch_error = err ? (err.message || String(err)) : 'unknown';
            }});
            return 'dispatched';
        }} catch(e) {{
            return 'error:' + e.message;
        }}
    """)

    if isinstance(result, str) and result.startswith('error:'):
        return {"ok": False, "error": result}

    # Poll for completion (max 15s)
    for _ in range(50):
        done = driver.execute_script("return window.__vuex_dispatch_done;")
        if done:
            error = driver.execute_script("return window.__vuex_dispatch_error;")
            if error:
                return {"ok": False, "error": error}
            data = driver.execute_script("""
                try { return JSON.parse(JSON.stringify(window.__vuex_dispatch_result)); }
                catch(e) { return String(window.__vuex_dispatch_result); }
            """)
            return {"ok": True, "data": data}
        time.sleep(0.3)

    return {"ok": False, "error": "timeout"}


def _get_state(driver, module_path):
    """Read a value from Vuex state. module_path like 'promo' or 'authCheckout'."""
    return driver.execute_script(f"""
        try {{
            var store = document.querySelector('#app').__vue_app__.config.globalProperties.$store;
            var state = store.state.{module_path};
            return JSON.parse(JSON.stringify(state));
        }} catch(e) {{ return null; }}
    """)


# ==============================================================================
#  FAST COUPON APPLICATION VIA VUEX
# ==============================================================================

def _apply_coupons_vuex(driver, coupon_list, priority_coupons, asin,
                         asin_coupon_cache, skip_priority_coupons, zoro_code=None):
    """Apply coupons via promo/setPromo Vuex dispatch.
    ~0.3-0.5s per coupon vs ~4-5s with DOM interaction.
    Returns (status, used_code)."""

    priority_coupons = priority_coupons or []
    asin_coupon_cache = asin_coupon_cache or {}
    working_pairs = asin_coupon_cache.get("working_pairs", {})

    cache_key = zoro_code or asin

    # Check if we already know a working coupon for this zoro_code
    if cache_key and cache_key in working_pairs:
        known_code = working_pairs[cache_key]
        if known_code in coupon_list:
            result = _dispatch(driver, "promo/setPromo", known_code)
            if result["ok"]:
                promo_state = _get_state(driver, "promo")
                if promo_state and promo_state.get("promoApplied"):
                    savings = promo_state.get("promoSavings", 0)
                    if savings and float(savings) > 0:
                        print(f"  ✅ Cached coupon applied: {known_code} (${savings})")
                        return "SUCCESS", known_code

    # Order coupons: priority first, try every available coupon
    ordered = []
    for code in coupon_list:
        is_priority = code in priority_coupons
        if skip_priority_coupons and is_priority:
            continue
        if is_priority:
            ordered.insert(0, code)
        else:
            ordered.append(code)

    if not ordered:
        return "NO_COUPONS_SKIP", None

    print(f"  🏷️  Testing {len(ordered)} coupons via Vuex (fast path)...")

    # ── NON-DESTRUCTIVE COUPON TESTING ──────────────────────────────
    # 1. Snapshot the starting (auto-applied) promo so we can restore it
    baseline_state = _get_state(driver, "promo")
    baseline_savings = 0.0
    baseline_code = None
    if baseline_state:
        try:
            baseline_savings = float(str(baseline_state.get("promoSavings") or baseline_state.get("savings") or 0).replace("$", "").replace(",", ""))
        except (ValueError, TypeError, AttributeError):
            baseline_savings = 0.0
        if baseline_state.get("promoApplied") and baseline_savings > 0:
            baseline_code = baseline_state.get("promoCode") or baseline_state.get("code") or "AUTO_APPLIED"
            print(f"  💾 Baseline promo: {baseline_code} (${baseline_savings:.2f})")

    best_savings = baseline_savings
    best_code = baseline_code
    best_is_auto = True

    for i, code in enumerate(ordered):
        code = str(code).strip()
        is_priority = code in priority_coupons

        # Remove current promo before testing a new one
        _dispatch(driver, "promo/removePromo")
        time.sleep(0.2)

        # Apply the new promo
        result = _dispatch(driver, "promo/setPromo", code)

        if not result["ok"]:
            print(f"  ❌ Vuex dispatch failed for {code}: {result.get('error', '?')}")
            continue

        # Check promo state for result
        time.sleep(0.3)
        promo_state = _get_state(driver, "promo")

        if not promo_state:
            print(f"  ⚠️  Cannot read promo state after {code}")
            continue

        # Evaluate savings
        savings = 0.0
        promo_applied = promo_state.get("promoApplied", False)
        promo_error = promo_state.get("promoError", "") or ""
        promo_savings = promo_state.get("promoSavings") or promo_state.get("savings") or 0

        try:
            savings = float(str(promo_savings).replace("$", "").replace(",", ""))
        except (ValueError, TypeError):
            savings = 0.0

        if promo_applied and savings > 0:
            print(f"  ✅ Coupon tested: {code}  |  Savings: ${savings:.2f}")
            if savings > best_savings:
                best_savings = savings
                best_code = code
                best_is_auto = False
                print(f"     ↑ New best (beats ${baseline_savings:.2f} baseline)")
            # Continue testing in case another coupon is even better
            continue

        # Classify the error
        error_lower = str(promo_error).lower()
        if "not valid for the item" in error_lower or "not valid for" in error_lower:
            if is_priority:
                print(f"  ⏭️  Priority {code}: not valid for item")
            continue
        elif "expired" in error_lower or "invalid" in error_lower:
            update_coupon_status(code, "INVALID")
            continue
        elif "net 30" in error_lower or "net30" in error_lower:
            return "NET30_RESTRICTED", None
        else:
            if promo_error:
                print(f"  ⏭️  {code}: {promo_error[:60]}")
            continue

    # ── RESTORE THE BEST PROMO ───────────────────────────────────────
    if best_code and not best_is_auto:
        # Best is a manual coupon — re-apply it to ensure it's active
        print(f"  🏆 Restoring best coupon: {best_code} (${best_savings:.2f})")
        _dispatch(driver, "promo/setPromo", best_code)
        time.sleep(0.3)
        if cache_key:
            working_pairs[cache_key] = best_code
        return "SUCCESS", best_code
    elif best_is_auto and baseline_code:
        # Best is the auto-applied promo — restore it
        print(f"  🏆 Restoring auto-applied promo: {baseline_code} (${baseline_savings:.2f})")
        # Auto-promos often re-apply themselves; if not, we can't re-apply without a code.
        # Try remove + re-navigate as a gentle nudge.
        _dispatch(driver, "promo/removePromo")
        time.sleep(0.2)
        # Some auto-promos (like Zoro Select) re-appear after remove if still eligible
        post_state = _get_state(driver, "promo")
        post_savings = 0
        if post_state:
            try:
                post_savings = float(str(post_state.get("promoSavings") or post_state.get("savings") or 0).replace("$", "").replace(",", ""))
            except (ValueError, TypeError, AttributeError):
                pass
        if post_savings <= 0:
            # Auto-promo didn't come back — try a page refresh to trigger it
            print("  🔄 Auto-promo didn't restore — refreshing checkout...")
            current_url = driver.current_url
            driver.get(current_url)
            time.sleep(3)
        return "SUCCESS", baseline_code

    # All coupons failed and no baseline promo existed
    print(f"  ❌ All {len(ordered)} coupons failed, no auto-promo to fallback")
    return "COUPON FAIL", None


# ==============================================================================
#  FAST PO NUMBER VIA VUEX
# ==============================================================================

def _set_po_vuex(driver, po_number):
    """Set PO number via authCheckout/setPurchaseOrderNumber."""
    result = _dispatch(driver, "authCheckout/setPurchaseOrderNumber", po_number)
    if result["ok"]:
        print(f"  📋 PO set via Vuex: {po_number}")
    else:
        # Fallback to DOM
        print(f"  ⚠️  Vuex PO failed, trying DOM...")
        try:
            po_input = driver.find_element(By.CSS_SELECTOR,
                "input[data-za='purchase-order-number'], input[name='poNumber']")
            po_input.clear()
            po_input.send_keys(po_number)
        except Exception:
            pass
    return result["ok"]


# ==============================================================================
#  FAST SHIPPING SELECTION VIA VUEX
# ==============================================================================

def _select_shipping_vuex(driver):
    """Select Standard Ground shipping via Vuex dispatch."""
    # First check current state
    checkout_state = _get_state(driver, "authCheckout")
    if not checkout_state:
        return False

    # Try to find the Standard Ground method in available options
    shipping_methods = checkout_state.get("shippingMethods") or []
    current_method = checkout_state.get("currentShippingMethod")

    if current_method:
        method_name = ""
        if isinstance(current_method, dict):
            method_name = current_method.get("name", "") or current_method.get("description", "")
        elif isinstance(current_method, str):
            method_name = current_method
        if "ground" in method_name.lower() or "standard" in method_name.lower():
            print("  📦 Shipping: Standard Ground (already set)")
            return True

    # Find Standard Ground in available methods
    ground_method = None
    for method in shipping_methods:
        if isinstance(method, dict):
            name = (method.get("name", "") or method.get("description", "")).lower()
            if "ground" in name or "standard" in name:
                ground_method = method
                break

    if ground_method:
        result = _dispatch(driver, "authCheckout/setCurrentShippingMethod", ground_method)
        if result["ok"]:
            print("  📦 Shipping: Standard Ground (Vuex)")
            return True

    # Fallback: try the order module
    result = _dispatch(driver, "order/getAvailableShippingMethods")
    if result["ok"]:
        time.sleep(0.5)
        order_state = _get_state(driver, "order")
        methods = (order_state or {}).get("shippingMethods", [])
        for method in methods:
            if isinstance(method, dict):
                name = (method.get("name", "") or method.get("description", "")).lower()
                if "ground" in name or "standard" in name:
                    result2 = _dispatch(driver, "order/setShippingMethod", method)
                    if result2["ok"]:
                        print("  📦 Shipping: Standard Ground (order module)")
                        return True

    print("  ⚠️  Vuex shipping selection failed — will use DOM fallback")
    return False


# ==============================================================================
#  FAST ORDER PLACEMENT VIA VUEX
# ==============================================================================

def _place_order_vuex(driver):
    """Place order via authCheckout/submitAuthOrder. Returns WB ID or None."""
    print("  📝 Placing order via Vuex...")

    result = _dispatch(driver, "authCheckout/submitAuthOrder")

    if result["ok"]:
        # Check for WB ID in the response
        data = result.get("data")
        wb_id = None

        if isinstance(data, dict):
            wb_id = data.get("orderNumber") or data.get("orderNo") or data.get("orderId")
            if not wb_id:
                # Search nested
                for key, val in data.items():
                    if isinstance(val, str) and val.startswith("WB"):
                        wb_id = val
                        break

        # Also check URL
        if not wb_id:
            time.sleep(1)
            url_match = re.search(r'orderNo=(WB\d{8,})', driver.current_url)
            if url_match:
                wb_id = url_match.group(1)

        # Also check checkout store state
        if not wb_id:
            checkout_state = _get_state(driver, "authCheckout")
            if checkout_state:
                wb_id = checkout_state.get("orderNumber") or checkout_state.get("orderNo")

            # Try checkout module too
            if not wb_id:
                co_state = _get_state(driver, "checkout")
                if co_state:
                    success = co_state.get("orderSuccess") or co_state.get("lastOrder") or {}
                    if isinstance(success, dict):
                        wb_id = success.get("orderNumber") or success.get("orderNo")

        # Last resort: page text
        if not wb_id:
            time.sleep(2)
            try:
                page_text = driver.execute_script("return document.body.innerText;") or ""
                wb_match = re.search(r'(WB\d{8,})', page_text)
                if wb_match:
                    wb_id = wb_match.group(1)
            except Exception:
                pass

        if wb_id:
            print(f"  ✅ Order placed via Vuex: {wb_id}")
        else:
            print(f"  ⚠️  Vuex submitAuthOrder succeeded but no WB ID found")
            print(f"       Response: {str(data)[:200]}")

        return wb_id

    else:
        print(f"  ❌ Vuex order placement failed: {result.get('error', '?')}")
        return None


# ==============================================================================
#  MAIN ENTRY POINT — FAST CHECKOUT
# ==============================================================================

def place_order_fast_vuex(driver, sku, qty, address, po_number, active_profile,
                          earnings=None, pre_price=None, coupon_list=None,
                          asin=None, asin_coupon_cache=None,
                          skip_priority_coupons=False, priority_coupons=None,
                          min_margin=None, return_details=False, zoro_code=None):
    """Fast checkout using Vuex store dispatch for all page interactions.

    Flow:
      1. Cart + address via existing API (already fast) — done by caller
      2. Navigate to checkout page ONCE (~3-5s)
      3. Coupons via promo/setPromo dispatch (~0.3s per coupon)
      4. PO via authCheckout/setPurchaseOrderNumber dispatch (instant)
      5. Shipping via authCheckout/setCurrentShippingMethod dispatch (instant)
      6. Place order via authCheckout/submitAuthOrder dispatch (~1-2s)

    Falls back to place_order_fast() if Vuex is unavailable.
    """
    print(f"  ⚡ Fast Checkout (Vuex): {sku} x{qty}")
    bot_log("info", "Vuex checkout started", sku=sku, qty=qty)
    priority_coupons = priority_coupons or []

    try:
        zoro.ensure_on_site(driver)
        zoro.ensure_token(driver)

        if pre_price is not None:
            price = pre_price
        else:
            cartable, status = zoro.check_inventory(driver, sku, qty)
            if not cartable:
                return _format_checkout_result("OOS", return_details=return_details)
            ok, price = zoro.clear_cart_and_add(driver, sku, qty)
            if not ok:
                return _format_checkout_result("FAILED", return_details=return_details)

        # Update shipping address via API
        addr_id = zoro.update_default_address(driver, address)
        if not addr_id:
            return _format_checkout_result("FAILED", return_details=return_details)
        print(f"  📍 Address set: {addr_id[:8]}")

        # ── Navigate to checkout (still needed to initialize Vue app) ──
        driver.get("https://www.zoro.com/cart")
        _wait_until(driver,
            "return document.readyState === 'complete' && "
            "(!!document.querySelector('[data-za=\"cart-checkout-main-action-top\"]') || "
            "!!document.querySelector('[data-za=\"cart-checkout-main-action-bottom\"]') || "
            "document.body.innerText.includes('Your cart is empty'))",
            timeout=6, poll=0.3)

        # Click checkout button
        clicked = driver.execute_script("""
            var sels = [
                '[data-za="cart-checkout-main-action-top"]',
                '[data-za="cart-checkout-main-action-bottom"]',
                '[data-za="checkout-button"]'
            ];
            for (var s of sels) {
                var el = document.querySelector(s);
                if (el && el.offsetParent !== null) { el.click(); return s; }
            }
            return '';
        """)

        if clicked:
            _wait_until(driver,
                "return document.readyState === 'complete' && "
                "(!!document.querySelector('[data-za=\"checkout-place-order-button\"]') || "
                "!!document.querySelector('[data-za=\"shipping-address-step\"]') || "
                "document.body.innerText.includes('Place Order'))",
                timeout=12, poll=0.4)
        else:
            driver.get("https://www.zoro.com/checkout")
            _wait_until(driver,
                "return document.readyState === 'complete' && "
                "!!document.querySelector('#app')",
                timeout=10, poll=0.4)

        # Wait for overlay to clear
        _wait_until(driver,
            "return !document.querySelector('.v-overlay__scrim') || "
            "document.querySelector('.v-overlay__scrim').offsetParent === null",
            timeout=8, poll=0.3)

        # ── Check if Vuex store is accessible ──
        if not _get_store(driver):
            print("  ⚠️  Vuex store not accessible — falling back to Selenium")
            from checkout.dom import place_order_fast
            return place_order_fast(
                driver, sku, qty, address, po_number, active_profile,
                earnings=earnings, pre_price=price, coupon_list=coupon_list,
                asin=asin, asin_coupon_cache=asin_coupon_cache,
                skip_priority_coupons=skip_priority_coupons,
                priority_coupons=priority_coupons,
                min_margin=min_margin, return_details=return_details,
                zoro_code=zoro_code)

        # Check state restriction
        restricted = driver.execute_script(
            "return document.body.innerText.includes('cannot be shipped to the state specified');")
        if restricted:
            return _format_checkout_result("STATE_RESTRICTED", return_details=return_details)

        # Wait for checkout readiness
        ready = _wait_for_checkout_ready(driver, address)
        if ready == 'STATE_RESTRICTED':
            return _format_checkout_result("STATE_RESTRICTED", return_details=return_details)
        if not ready:
            return _format_checkout_result("FAILED", return_details=return_details)

        # ── FAST PATH: All operations via Vuex ──────────────────────

        # 1. Apply coupons via Vuex
        used_coupon_code = None
        if coupon_list:
            coupon_result, used_coupon_code = _apply_coupons_vuex(
                driver, coupon_list, priority_coupons,
                asin, asin_coupon_cache, skip_priority_coupons, zoro_code=zoro_code)
            if coupon_result in ("COUPON FAIL", "NET30_RESTRICTED", "NO_COUPONS_SKIP"):
                return _format_checkout_result(coupon_result, return_details=return_details)

        # 2. PO number via Vuex (skip for coupon profiles)
        if "COUPON" not in active_profile:
            _set_po_vuex(driver, po_number)

        # 3. Shipping via Vuex
        _select_shipping_vuex(driver)

        # 4. Extract checkout total (still DOM-based — it's fast)
        checkout_total = _extract_checkout_total(driver)
        profit = None
        margin = None
        if checkout_total is not None:
            print(f"  💰 Checkout total: ${checkout_total:.2f}")
            if earnings is not None:
                profit = round(earnings - checkout_total, 2)
                margin = (earnings - checkout_total) / checkout_total if checkout_total > 0 else 0.0
                icon = "✅" if profit >= 0 else "⚠️"
                print(f"  {icon} Final Profit: ${profit:.2f}  |  Margin: {margin:.1%}")
                if min_margin is not None and margin < min_margin:
                    print(f"  🛑 LOW PROFIT — checkout gate ({margin:.1%} < {min_margin:.0%})")
                    return _format_checkout_result(
                        "LOW_PROFIT", checkout_total=checkout_total,
                        profit=profit, margin=margin, return_details=return_details)

        # 5. Place order via Vuex
        zoro_id = _place_order_vuex(driver)

        # If Vuex placement failed, try the DOM click as last resort
        if not zoro_id:
            print("  ↪️  Vuex order failed — trying DOM click fallback...")
            from checkout.dom import _click_place_order
            zoro_id = _click_place_order(driver)

        if zoro_id:
            bot_log("info", "ORDER PLACED (Vuex fast)", wb_id=zoro_id, sku=sku,
                    qty=qty, total=checkout_total, profit=profit, margin=margin)
        else:
            bot_log("error", "Place order FAILED (Vuex + DOM)", sku=sku)

        if zoro_id and used_coupon_code and used_coupon_code != "AUTO_APPLIED":
            update_coupon_status(used_coupon_code, "USED")

        return _format_checkout_result(
            "SUCCESS" if zoro_id else "FAILED",
            wb_id=zoro_id, checkout_total=checkout_total,
            profit=profit, margin=margin,
            return_details=return_details,
            used_coupon_code=used_coupon_code)

    except Exception as e:
        bot_log("error", "Vuex checkout exception — falling back", sku=sku, err=str(e)[:300])
        print(f"  ❌ Vuex checkout error: {e}")
        print(f"  ↪️  Falling back to Selenium checkout...")
        try:
            from checkout.dom import place_order_fast
            return place_order_fast(
                driver, sku, qty, address, po_number, active_profile,
                earnings=earnings, pre_price=pre_price, coupon_list=coupon_list,
                asin=asin, asin_coupon_cache=asin_coupon_cache,
                skip_priority_coupons=skip_priority_coupons,
                priority_coupons=priority_coupons,
                min_margin=min_margin, return_details=return_details,
                zoro_code=zoro_code)
        except Exception as fallback_err:
            print(f"  ❌ Fallback also failed: {fallback_err}")
            return _format_checkout_result("FAILED", return_details=return_details)
