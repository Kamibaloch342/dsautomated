"""
order_checkout.py — Zoro checkout placement logic.
Handles: address verification, old/new layout recovery, coupon application,
payment switching, PO number, and order confirmation.
"""

import os
import re
import json
import time
import functools
from datetime import datetime

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import platforms.zoro as zoro
from utils.helpers import load_coupon_metadata, update_coupon_status
from utils.config import FULL_STATE_NAME_MAP
from utils.logger import bot_log

print = functools.partial(print, flush=True)


# ==============================================================================
#  SMART POLLING HELPER
# ==============================================================================

def _wait_until(driver, condition_js, timeout=10, poll=0.3):
    """Poll a JS condition, return True as soon as it evaluates to truthy.
    Returns False if timeout expires. Same max wait as the sleep it replaces,
    but returns early when the condition is met."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if driver.execute_script(condition_js):
                return True
        except Exception:
            pass
        time.sleep(poll)
    return False


# ==============================================================================
#  INTERNAL HELPERS
# ==============================================================================

def _is_old_edit_no_shipping(driver):
    """True if old layout in edit mode AND shipping methods not loaded."""
    return driver.execute_script("""
        if (document.querySelector('[data-za="shipping-address-step"]')) return false;
        var formInputs = document.querySelectorAll(
            '[data-za="address-form"] input, .ac-address input, form input');
        var vis = 0;
        for (var i of formInputs) { if (i.offsetParent && i.type !== 'hidden') vis++; }
        if (vis < 3) return false;
        var t = document.body.innerText;
        return t.includes('Please specify a shipping method') ||
               t.includes('Please enter a valid shipping address');
    """)


def _recover_via_renavigate(driver):
    """Recovery strategy: navigate cart → checkout to force a clean page load.
    When the address is already set via API, the old layout loads in read-only
    mode with shipping methods populated — no form manipulation needed."""
    for attempt in range(3):
        driver.get("https://www.zoro.com/cart")
        time.sleep(3)
        driver.get("https://www.zoro.com/checkout")
        time.sleep(8)
        if not _is_old_edit_no_shipping(driver):
            return True
    return False


# ==============================================================================
#  CHECKOUT STAGES
# ==============================================================================

def _handle_stale_checkout(driver):
    """Detect and recover from stale checkout state (stuck address forms).
    Returns 'ok', 'STATE_RESTRICTED', or 'failed'."""
    for _recovery in range(3):
        checkout_state = driver.execute_script("""
            var buttons = document.querySelectorAll('button');
            var hasSave = false, hasCancel = false;
            for (var b of buttons) {
                if (!b.offsetParent) continue;
                var text = b.textContent.trim();
                if (text === 'Save') {
                    var section = b.closest('section, [class*="shipping"], [class*="address"], form, [class*="checkout"]');
                    if (section) hasSave = true;
                }
                if (text === 'Cancel') {
                    var section = b.closest('section, [class*="shipping"], [class*="address"], form, [class*="checkout"]');
                    if (section) hasCancel = true;
                }
            }
            var hasRestrictionErr = document.body.innerText.includes('cannot be shipped to the state specified');
            var hasAddressList = document.body.innerText.includes('+ Add a new shipping address');
            return {save: hasSave, cancel: hasCancel, restricted: hasRestrictionErr, addressList: hasAddressList};
        """)

        if (not checkout_state.get('restricted') and
            not checkout_state.get('addressList') and
            not checkout_state.get('save')):
            return 'ok'

        if checkout_state.get('restricted'):
            print("  ⚠️  State restriction detected at checkout")
            return 'STATE_RESTRICTED'

        if checkout_state.get('save') or checkout_state.get('addressList'):
            # Re-navigate cart → checkout to get a clean read-only state
            # (address is already set via API, so fresh load shows it correctly)
            driver.get("https://www.zoro.com/cart")
            time.sleep(3)
            driver.get("https://www.zoro.com/checkout")
            time.sleep(6)
            continue

    return 'ok'  # best effort


def _recover_old_layout(driver, address):
    """Handle old layout edit mode where address form is stuck open.
    Since address is already set via API, re-navigating cart → checkout
    forces the page to load in read-only mode with shipping populated.
    Returns True if recovered or not needed, False if all attempts failed."""
    if not _is_old_edit_no_shipping(driver):
        return True

    print("  ⚙️  Old layout stuck in edit mode — re-navigating...")
    if _recover_via_renavigate(driver):
        print("  ✅ Layout recovered (re-navigate)")
        return True

    print("  ⚠️  Re-navigate didn't fix edit mode — will try modal flow")
    return False


# ==============================================================================
#  ADDRESS VIA CHECKOUT MODAL (for Rajar Enterprise / multi-address accounts)
#  NOTE: Vuetify overlays use position:fixed, so offsetParent is null.
#        All visibility checks use getBoundingClientRect instead.
# ==============================================================================

def _get_checkout_state(driver):
    """Inspect checkout page state: address cards, shipping, buttons."""
    return driver.execute_script("""
        function isVis(el) {
            var r = el.getBoundingClientRect();
            return r.height > 0 && r.width > 0;
        }
        var t = document.body.innerText;
        var result = {
            has_shipping_radio: false,
            shipping_selected: false,
            has_ship_to_btn: false,
            has_add_new_btn: false,
            has_change_addr: false,
            has_remove_btn: false,
            has_shipping_error: t.includes('Please specify a shipping method'),
            has_addr_error: t.includes('Please enter a valid shipping address'),
            has_modal: false,
            has_place_order: false,
            shipping_text_visible: false,
            address_ok: false,
        };

        // Check for shipping radios (old layout)
        var radios = document.querySelectorAll('input[type="radio"]');
        for (var r of radios) {
            var container = r.closest('.v-selection-control, .v-radio, label');
            var lbl = container ? container.textContent : '';
            if (lbl.includes('Standard Ground') || lbl.includes('Ground')) {
                result.has_shipping_radio = true;
                result.shipping_selected = r.checked;
            }
        }

        // New layout: shipping method shown as text (no radio)
        var shipSteps = document.querySelectorAll(
            '[data-za="shipping-method-step"], [data-za="shipping-step"],'
            + ' [class*="shipping-method"], [class*="shippingMethod"]');
        for (var ss of shipSteps) {
            if (isVis(ss) && (ss.textContent.includes('Ground') ||
                ss.textContent.includes('Standard') ||
                ss.textContent.includes('Shipping Method'))) {
                result.shipping_text_visible = true;
            }
        }
        // Also check if page text shows a shipping cost line (e.g. "$5.00" near "Shipping")
        if (!result.shipping_text_visible && !result.has_shipping_error) {
            var summaryEls = document.querySelectorAll(
                '[data-za*="shipping"], [data-za*="delivery"]');
            for (var se of summaryEls) {
                if (isVis(se) && /[$]\\d/.test(se.textContent)) {
                    result.shipping_text_visible = true;
                }
            }
        }

        // Check for Place Order button (strong signal checkout is ready)
        var placeOrderSels = [
            '[data-za="checkout-place-order-button"]',
            '[data-za="checkout-main-action-top"]'];
        for (var ps of placeOrderSels) {
            var poBtn = document.querySelector(ps);
            if (poBtn && isVis(poBtn)) { result.has_place_order = true; break; }
        }

        // Check for buttons/links (use getBoundingClientRect, not offsetParent)
        var allEls = document.querySelectorAll('button, a');
        for (var el of allEls) {
            if (!isVis(el)) continue;
            var text = el.textContent.trim();
            if (text === 'Ship to This Address') result.has_ship_to_btn = true;
            if (text === 'Add New Address' || text === 'Add a new shipping address') result.has_add_new_btn = true;
            if (text === 'Change Address') result.has_change_addr = true;
            if (text === 'Remove from Address book') result.has_remove_btn = true;
        }

        // Check for open modal
        var overlays = document.querySelectorAll('.v-overlay--active');
        for (var ov of overlays) {
            if (ov.textContent.includes('Add New Address') && ov.textContent.includes('Full Name')) {
                result.has_modal = true;
            }
        }

        // Checkout is ready when: no errors AND (shipping radio visible OR
        // shipping text visible OR Place Order button is visible)
        var shippingReady = result.has_shipping_radio || result.shipping_text_visible || result.has_place_order;
        result.address_ok = !result.has_shipping_error && !result.has_addr_error && shippingReady;

        return result;
    """)


def _click_ship_to_address(driver, address):
    """Click 'Ship to This Address' on the card matching our address.
    Returns True if clicked."""
    street_word = address.get("street_address", "").split()[0] if address.get("street_address") else ""
    clicked = driver.execute_script("""
        function isVis(el) { var r = el.getBoundingClientRect(); return r.height > 0 && r.width > 0; }
        var street = arguments[0];
        var btns = document.querySelectorAll('button');
        // First try matching by street
        for (var b of btns) {
            if (b.textContent.trim() !== 'Ship to This Address' || !isVis(b)) continue;
            var card = b.closest('[class*="card"], [class*="address"], div.v-col, div');
            if (card && street && card.textContent.includes(street)) {
                b.click();
                return 'matched';
            }
        }
        // Fallback: click the first visible one
        for (var b of btns) {
            if (b.textContent.trim() !== 'Ship to This Address' || !isVis(b)) continue;
            b.click();
            return 'first';
        }
        return 'none';
    """, street_word)
    return clicked != 'none'


def _remove_non_billing_address(driver):
    """Remove the non-billing address card to clear stale shipping address.
    Clicks 'Remove from Address book' and confirms the dialog.
    Returns True if an address was removed."""
    remove_result = driver.execute_script("""
        function isVis(el) { var r = el.getBoundingClientRect(); return r.height > 0 && r.width > 0; }
        var allBtns = document.querySelectorAll('button');
        for (var btn of allBtns) {
            if (!isVis(btn) || btn.textContent.trim() !== 'Remove from Address book') continue;
            // Walk up to card boundary (stop before v-row/v-container)
            var card = btn;
            var p = btn.parentElement;
            for (var i = 0; i < 5; i++) {
                if (!p) break;
                var cl = p.className ? p.className.toString() : '';
                if (cl.includes('v-row') || cl.includes('v-container') || cl.includes('checkout')) break;
                card = p;
                p = p.parentElement;
            }
            // Skip billing-only cards
            var cardText = card.textContent;
            if (cardText.includes('Default Billing') && !cardText.includes('Default Shipping')) continue;
            btn.click();
            return 'clicked';
        }
        // Fallback: click first visible remove
        for (var btn of allBtns) {
            if (isVis(btn) && btn.textContent.trim() === 'Remove from Address book') {
                btn.click();
                return 'fallback';
            }
        }
        return 'none';
    """)
    if remove_result == 'none':
        return False

    time.sleep(2)

    # Handle confirmation dialog: "Are you sure you want to delete this address?" → OK
    for _ in range(3):
        confirmed = driver.execute_script("""
            var overlays = document.querySelectorAll('.v-overlay--active');
            for (var ov of overlays) {
                if (ov.textContent.includes('Are you sure') || ov.textContent.includes('delete this address')) {
                    var btns = ov.querySelectorAll('button');
                    for (var b of btns) {
                        var t = b.textContent.trim();
                        if (t === 'OK' || t === 'Yes' || t === 'Confirm' || t === 'Delete') {
                            b.click();
                            return true;
                        }
                    }
                }
            }
            return false;
        """)
        if confirmed:
            break
        time.sleep(1)

    time.sleep(3)
    return True


def _add_address_via_modal(driver, address):
    """Open 'Add New Address' modal on checkout, fill form, save.
    Handles Vuetify combobox State field (type + dropdown click).
    Returns True if address was added successfully."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys

    name = address.get("name", "")
    street = address.get("street_address", "")
    street2 = address.get("street_address_2", "")
    city = address.get("city", "")
    state_abbr = address.get("state_abbr", "")
    state_full = address.get("state_full_name") or FULL_STATE_NAME_MAP.get(state_abbr, "")
    zipcode = address.get("zip_code", "")
    phone = address.get("phone", "4044619909")

    # Click "Add New Address"
    clicked = driver.execute_script("""
        function isVis(el) { var r = el.getBoundingClientRect(); return r.height > 0 && r.width > 0; }
        var els = document.querySelectorAll('a, button');
        for (var el of els) {
            var text = el.textContent.trim();
            if ((text === 'Add New Address' || text === 'Add a new shipping address') && isVis(el)) {
                el.click();
                return true;
            }
        }
        return false;
    """)
    if not clicked:
        print("  ⚠️  'Add New Address' not found")
        return False

    time.sleep(2)

    # Fill text fields in the modal (use getBoundingClientRect for visibility)
    filled = driver.execute_script("""
        var name = arguments[0], street = arguments[1], street2 = arguments[2],
            city = arguments[3], zip = arguments[4], phone = arguments[5];

        function isVis(el) { var r = el.getBoundingClientRect(); return r.height > 0 && r.width > 0; }

        function setVal(input, val) {
            if (!input || !val) return false;
            input.focus();
            var nativeSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            nativeSetter.call(input, '');
            input.dispatchEvent(new Event('input', {bubbles: true}));
            nativeSetter.call(input, val);
            input.dispatchEvent(new Event('input', {bubbles: true}));
            input.dispatchEvent(new Event('change', {bubbles: true}));
            input.dispatchEvent(new Event('blur', {bubbles: true}));
            return true;
        }

        // Find the modal overlay
        var modal = null;
        document.querySelectorAll('.v-overlay--active').forEach(function(ov) {
            if (ov.textContent.includes('Add New Address') && ov.textContent.includes('Full Name')) {
                modal = ov;
            }
        });
        if (!modal) return -1;

        // Build label map for visible inputs
        var entries = [];
        var allInputs = modal.querySelectorAll('input');
        for (var inp of allInputs) {
            if (inp.type === 'hidden' || inp.type === 'radio' || inp.type === 'checkbox') continue;
            if (!isVis(inp)) continue;
            var vInput = inp.closest('.v-input');
            if (vInput && vInput.classList.contains('d-sr-only')) continue;
            var label = '';
            if (vInput) {
                var lbl = vInput.querySelector('label');
                if (lbl) label = lbl.textContent.trim().toLowerCase();
            }
            entries.push({ input: inp, label: label, id: inp.id });
        }

        var filled = 0;
        var used = {};
        var fieldDefs = [
            { match: 'full name', value: name },
            { match: 'address 2', value: street2 },
            { match: 'address', value: street, exclude: 'address 2' },
            { match: 'city', value: city },
            { match: 'zip', value: zip },
            { match: 'phone', value: phone },
        ];

        for (var fd of fieldDefs) {
            if (!fd.value) continue;
            for (var e of entries) {
                if (used[e.id]) continue;
                if (!e.label.includes(fd.match)) continue;
                if (fd.exclude && e.label.includes(fd.exclude)) continue;
                if (setVal(e.input, fd.value)) { filled++; used[e.id] = true; }
                break;
            }
        }
        return filled;
    """, name, street, street2, city, zipcode, phone)

    print(f"  📝 Modal: filled {filled} text fields")

    # Handle State combobox: type the state name, then click the dropdown option
    if state_full:
        state_input_id = driver.execute_script("""
            var stateName = arguments[0];
            var modal = null;
            document.querySelectorAll('.v-overlay--active').forEach(function(ov) {
                if (ov.textContent.includes('Add New Address')) modal = ov;
            });
            if (!modal) return null;
            var allInputs = modal.querySelectorAll('input[type="text"]');
            for (var inp of allInputs) {
                var r = inp.getBoundingClientRect();
                if (r.height === 0) continue;
                var vInput = inp.closest('.v-input');
                if (vInput && vInput.classList.contains('d-sr-only')) continue;
                var lbl = vInput ? vInput.querySelector('label') : null;
                if (lbl && lbl.textContent.trim().toLowerCase().includes('state')) return inp.id;
            }
            return null;
        """, state_full)

        if state_input_id:
            state_el = driver.find_element(By.ID, state_input_id)
            state_el.click()
            time.sleep(0.3)
            state_el.clear()
            state_el.send_keys(state_full)
            time.sleep(1.5)

            # Click the dropdown option
            opt_clicked = driver.execute_script("""
                var target = arguments[0];
                var items = document.querySelectorAll('.v-list-item, [role="option"]');
                for (var item of items) {
                    var r = item.getBoundingClientRect();
                    if (r.height > 0 && item.textContent.trim() === target) {
                        item.click(); return true;
                    }
                }
                return false;
            """, state_full)

            if not opt_clicked:
                state_el.send_keys(Keys.RETURN)
            filled += 1
        else:
            print(f"  ⚠️  State input not found in modal")

    if filled < 4:
        print(f"  ⚠️  Only {filled} fields filled — closing modal")
        driver.execute_script("""
            document.querySelectorAll('.v-overlay--active button').forEach(function(b) {
                if (b.textContent.trim() === 'Cancel') b.click();
            });
        """)
        time.sleep(1)
        return False

    time.sleep(1)

    # Check "Make this my default shipping address"
    driver.execute_script("""
        var modal = null;
        document.querySelectorAll('.v-overlay--active').forEach(function(ov) {
            if (ov.textContent.includes('Add New Address')) modal = ov;
        });
        if (!modal) return;
        var checkboxes = modal.querySelectorAll('input[type="checkbox"]');
        for (var cb of checkboxes) {
            var parent = cb.closest('.v-input, .v-checkbox, .v-selection-control');
            if (parent && parent.textContent.includes('default shipping') && !cb.checked) {
                var lbl = parent.querySelector('label');
                if (lbl) lbl.click();
                else cb.click();
            }
        }
    """)
    time.sleep(0.5)

    # Click "Save Address"
    saved = driver.execute_script("""
        var modal = null;
        document.querySelectorAll('.v-overlay--active').forEach(function(ov) {
            if (ov.textContent.includes('Add New Address')) modal = ov;
        });
        if (!modal) return false;
        var btns = modal.querySelectorAll('button');
        for (var b of btns) {
            if (b.textContent.trim() === 'Save Address') { b.click(); return true; }
        }
        return false;
    """)
    if not saved:
        print("  ⚠️  'Save Address' button not found")
        return False

    print("  💾 Saving address...")
    time.sleep(6)

    # Check result — after saving, may need "Ship to This Address"
    state = _get_checkout_state(driver)
    if state['address_ok']:
        print("  ✅ Address saved — shipping methods loaded")
        return True

    if state['has_ship_to_btn']:
        print("  ✅ Address saved — clicking 'Ship to This Address'...")
        if _click_ship_to_address(driver, address):
            time.sleep(5)
            return True

    print("  ⚠️  Address save result unclear — proceeding")
    return True


def _retype_state_to_trigger_shipping(driver, address):
    """Quick trick: re-select the State value on the checkout address form
    to trigger Zoro's shipping method lookup — avoids the heavy
    remove-address-and-add-new flow.

    Works on both:
      - Old layout: standard <select> dropdown for State
      - New layout: Vuetify combobox for State

    Returns True if the state was successfully re-selected.
    """
    from selenium.webdriver.common.keys import Keys

    state_abbr = address.get("state_abbr", "")
    state_full = address.get("state_full_name") or FULL_STATE_NAME_MAP.get(state_abbr, "")

    if not state_abbr:
        return False

    print(f"  🔄 Trying state re-select trick ({state_abbr})...")

    # --- Attempt A: Old layout <select> dropdown ---
    reselected = driver.execute_script("""
        var abbr = arguments[0];
        // Find visible <select> whose current value or label contains a state
        var selects = document.querySelectorAll('select');
        for (var sel of selects) {
            if (sel.offsetParent === null && sel.getBoundingClientRect().height === 0) continue;
            // Check if this looks like a State select (has state options)
            var hasStates = false;
            for (var opt of sel.options) {
                if (opt.value === abbr || opt.textContent.trim() === abbr) {
                    hasStates = true; break;
                }
            }
            if (!hasStates) continue;

            // Temporarily set to empty/placeholder to force a change event
            var origVal = sel.value;
            sel.value = '';
            sel.dispatchEvent(new Event('change', {bubbles: true}));

            // Small delay simulated by re-setting after a tick
            setTimeout(function() {
                // Re-select the correct state
                for (var opt of sel.options) {
                    if (opt.value === abbr || opt.textContent.trim() === abbr) {
                        sel.value = opt.value;
                        break;
                    }
                }
                sel.dispatchEvent(new Event('change', {bubbles: true}));
                sel.dispatchEvent(new Event('input', {bubbles: true}));
            }, 300);
            return 'select';
        }
        return 'none';
    """, state_abbr)

    if reselected == 'select':
        print(f"  📝 Re-selected state '{state_abbr}' in <select> dropdown")
        time.sleep(2)
        return True

    # --- Attempt B: Vuetify combobox (new layout) or plain text input ---
    state_input_info = driver.execute_script("""
        var inputs = document.querySelectorAll('input[type="text"]');
        for (var inp of inputs) {
            var r = inp.getBoundingClientRect();
            if (r.height === 0) continue;
            var vInput = inp.closest('.v-input');
            // Vuetify combobox
            if (vInput) {
                if (vInput.classList.contains('d-sr-only')) continue;
                var lbl = vInput.querySelector('label');
                if (lbl && lbl.textContent.trim().toLowerCase().includes('state')) {
                    return {id: inp.id, tag: 'vuetify'};
                }
            }
            // Plain input — check nearest label or placeholder
            var prev = inp.previousElementSibling;
            var parentLabel = inp.closest('label, .form-group, fieldset');
            var context = '';
            if (prev) context += prev.textContent;
            if (parentLabel) context += parentLabel.textContent;
            if (inp.placeholder) context += inp.placeholder;
            if (inp.name) context += inp.name;
            if (context.toLowerCase().includes('state')) {
                return {id: inp.id, tag: 'plain'};
            }
        }
        return null;
    """)

    if state_input_info and state_full:
        try:
            from selenium.webdriver.common.by import By
            state_el = driver.find_element(By.ID, state_input_info['id'])
            state_el.click()
            time.sleep(0.3)
            # Select all existing text via JS then backspace (works on Mac + Windows)
            driver.execute_script("arguments[0].select();", state_el)
            time.sleep(0.1)
            state_el.send_keys(Keys.BACKSPACE)
            time.sleep(0.3)
            # Type state name character by character (human simulation)
            for ch in state_full:
                state_el.send_keys(ch)
                time.sleep(0.05)
            time.sleep(1)
            # Click the dropdown option
            opt_clicked = driver.execute_script("""
                var target = arguments[0];
                var items = document.querySelectorAll('.v-list-item, [role="option"]');
                for (var item of items) {
                    var r = item.getBoundingClientRect();
                    if (r.height > 0 && item.textContent.trim() === target) {
                        item.click(); return true;
                    }
                }
                return false;
            """, state_full)
            if not opt_clicked:
                state_el.send_keys(Keys.RETURN)
            print(f"  📝 Re-typed state '{state_full}' in Vuetify combobox")
            time.sleep(2)
            return True
        except Exception as e:
            print(f"  ⚠️  Vuetify state re-type failed: {e}")

    print("  ⚠️  No State field found to re-select")
    return False


def _fill_old_layout_form(driver, address):
    """Fill empty fields in the old-layout checkout address form."""
    name = address.get("name", "")
    street = address.get("street_address", "")
    street2 = address.get("street_address_2", "")
    city = address.get("city", "")
    state_abbr = address.get("state_abbr", "")
    zipcode = address.get("zip_code", "")
    phone = address.get("phone", "4044619909")

    filled = driver.execute_script("""
        var name=arguments[0], street=arguments[1], street2=arguments[2],
            city=arguments[3], stAbbr=arguments[4], zip=arguments[5], phone=arguments[6];

        function setVal(inp, val) {
            if (!inp || !val) return false;
            var s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
            s.call(inp, val);
            inp.dispatchEvent(new Event('input',{bubbles:true}));
            inp.dispatchEvent(new Event('change',{bubbles:true}));
            inp.dispatchEvent(new Event('blur',{bubbles:true}));
            return true;
        }

        function getCtx(inp) {
            var c = (inp.name||'')+'|'+(inp.id||'')+'|'+(inp.placeholder||'');
            var lbl = null;
            if (inp.id) lbl = document.querySelector('label[for="'+inp.id+'"]');
            if (!lbl) { var p = inp.closest('.form-group,.field,label'); if (p) lbl = p; }
            if (lbl) c += '|' + lbl.textContent;
            return c.toLowerCase();
        }

        var inputs = document.querySelectorAll(
            'form input, [data-za="address-form"] input, .ac-address input');
        var filled = 0;
        var parts = name.split(' ');
        var first = parts[0] || name;
        var last = parts.slice(1).join(' ') || '';

        for (var inp of inputs) {
            if (/hidden|radio|checkbox/.test(inp.type)) continue;
            if (!inp.offsetParent && inp.getBoundingClientRect().height===0) continue;
            if (inp.value && inp.value.trim()) continue;  // skip pre-filled
            var c = getCtx(inp);
            if ((c.includes('first') && c.includes('name')) || c.includes('firstname')) {
                if (setVal(inp, first)) filled++;
            } else if ((c.includes('last') && c.includes('name')) || c.includes('lastname')) {
                if (setVal(inp, last || first)) filled++;
            } else if (c.includes('full') && c.includes('name')) {
                if (setVal(inp, name)) filled++;
            } else if (c.includes('address') && (c.includes('2') || c.includes('apt') || c.includes('suite'))) {
                if (street2 && setVal(inp, street2)) filled++;
            } else if (c.includes('address') || c.includes('street')) {
                if (setVal(inp, street)) filled++;
            } else if (c.includes('city')) {
                if (setVal(inp, city)) filled++;
            } else if (c.includes('zip') || c.includes('postal')) {
                if (setVal(inp, zip)) filled++;
            } else if (c.includes('phone') || c.includes('tel')) {
                if (setVal(inp, phone)) filled++;
            }
        }

        // State <select>
        var selects = document.querySelectorAll('form select, [data-za="address-form"] select');
        for (var sel of selects) {
            if (!sel.offsetParent && sel.getBoundingClientRect().height===0) continue;
            for (var opt of sel.options) {
                if (opt.value === stAbbr || opt.textContent.trim() === stAbbr) {
                    sel.value = opt.value;
                    sel.dispatchEvent(new Event('change',{bubbles:true}));
                    filled++;
                    break;
                }
            }
        }
        return filled;
    """, name, street, street2, city, state_abbr, zipcode, phone)
    print(f"  \U0001f4dd Filled {filled} form fields")
    return filled


def _wait_for_checkout_ready(driver, address, max_attempts=3):
    """Single unified function to get checkout into a ready state.

    Replaces the tangled chain of _ensure_address_and_shipping +
    _recover_old_layout + _verify_address_on_checkout.

    Decision tree per attempt:
      1. Shipping loaded?  -> done (soft address verify)
      2. State restriction? -> abort
      3. Old edit mode + Save btn -> fill-if-empty + click Save -> recheck
      4. Ship to This Address btn -> click -> recheck
      5. New layout Vuetify flow  -> modal -> recheck
      6. Edit mode, no Save       -> state re-type -> recheck
      7. Last resort              -> cart -> checkout -> recheck

    Returns True, False, or 'STATE_RESTRICTED'.
    """
    street_word = (address.get("street_address") or "").split()[0] if address.get("street_address") else ""
    zip_check = (address.get("zip_code") or "").replace("-", "")[:5]

    for attempt in range(max_attempts):
        # ── Comprehensive page-state read ──
        info = driver.execute_script("""
            function isVis(el) {
                if (!el) return false;
                var r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            }
            var t = document.body.innerText;
            var R = {
                is_new:    !!document.querySelector('[data-za="shipping-address-step"]'),
                err_ship:  t.includes('Please specify a shipping method'),
                err_addr:  t.includes('Please enter a valid shipping address'),
                err_state: t.includes('cannot be shipped to the state specified'),
                ship_radio:  false,
                place_order: false,
                save_btn:     false,
                ship_to_btn:  false,
                change_addr:  false,
                add_new_btn:  false,
                remove_btn:   false,
                edit_mode:    false,
                form_filled:  false,
                page_snippet: t.substring(0, 4000)
            };

            // shipping radios or text labels
            var radios = document.querySelectorAll('input[type="radio"]');
            for (var r of radios) {
                var c = r.closest('label,.v-selection-control,.v-radio');
                if (c && /Ground|Standard/i.test(c.textContent)) R.ship_radio = true;
            }
            ['[data-za="shipping-method-step"]','[data-za*="shipping-method"]',
             '[class*="shipping-method"]','[class*="shippingMethod"]'].forEach(function(sel){
                document.querySelectorAll(sel).forEach(function(el){
                    if (isVis(el) && /Ground|Standard/i.test(el.textContent)) R.ship_radio = true;
                });
            });

            // Place Order button
            ['[data-za="checkout-place-order-button"]',
             '[data-za="checkout-main-action-top"]'].forEach(function(sel){
                var b = document.querySelector(sel);
                if (b && isVis(b)) R.place_order = true;
            });
            if (!R.place_order) {
                document.querySelectorAll('button,input[type="submit"]').forEach(function(b){
                    if (isVis(b) && /^Place Order$/.test((b.textContent||b.value||'').trim()))
                        R.place_order = true;
                });
            }

            // edit mode (>= 3 visible form inputs)
            var vis = 0, hasVal = 0;
            document.querySelectorAll(
                'form input,[data-za="address-form"] input,.ac-address input'
            ).forEach(function(inp){
                if (/hidden|radio|checkbox/.test(inp.type)) return;
                if (!inp.offsetParent && inp.getBoundingClientRect().height===0) return;
                vis++;
                if (inp.value && inp.value.trim()) hasVal++;
            });
            R.edit_mode   = vis >= 3;
            R.form_filled = hasVal >= 3;

            // buttons / links
            document.querySelectorAll('button,a').forEach(function(el){
                if (!isVis(el)) return;
                var tx = el.textContent.trim();
                if (tx==='Save') {
                    var sec = el.closest('section,[class*="shipping"],[class*="address"],form,[class*="checkout"]');
                    if (sec) R.save_btn = true;
                }
                if (tx==='Ship to This Address')        R.ship_to_btn  = true;
                if (tx==='Change Address')               R.change_addr  = true;
                if (/^Add (New|a new)/i.test(tx))        R.add_new_btn  = true;
                if (tx==='Remove from Address book')     R.remove_btn   = true;
            });
            return R;
        """)

        shipping_ok = (info['ship_radio'] or info['place_order']) and not info['err_ship']

        _ship_icon = '\u2705' if shipping_ok else '\u274c'
        _err = ' err_ship!' if info['err_ship'] else ''
        print(f"  \U0001f4cb Checkout (try {attempt+1}/{max_attempts}): "
              f"ship={_ship_icon}{_err} "
              f"edit={'Y' if info['edit_mode'] else 'N'} "
              f"save={'Y' if info['save_btn'] else 'N'} "
              f"ship_to={'Y' if info['ship_to_btn'] else 'N'} "
              f"place={'Y' if info['place_order'] else 'N'}")

        # ── 1. READY ──
        if shipping_ok and not info['err_state']:
            addr_found = (street_word and street_word in info['page_snippet']
                          and zip_check and zip_check in info['page_snippet'])
            if addr_found:
                print("  \u2705 Checkout ready \u2014 address + shipping confirmed")
                return True

            # Shipping loaded but address text not in page — do a deeper check
            # Read actual address values from the checkout DOM
            addr_detail = driver.execute_script("""
                var parts = [];
                // New layout: data-za fields
                ['full-name','address1','address2','city-state-zip','city-state',
                 'company-name','phone-number'].forEach(function(za) {
                    document.querySelectorAll('[data-za="'+za+'"]').forEach(function(el) {
                        if (el.textContent.trim()) parts.push(el.textContent.trim());
                    });
                });
                // Shipping address step
                var step = document.querySelector('[data-za="shipping-address-step"]');
                if (step) parts.push(step.textContent.trim().substring(0, 500));
                // Input values (old layout)
                document.querySelectorAll('form input, [data-za="address-form"] input').forEach(function(inp) {
                    if (/hidden|radio|checkbox/.test(inp.type)) return;
                    if (inp.value && inp.value.trim()) parts.push(inp.value.trim());
                });
                document.querySelectorAll('form select, [data-za="address-form"] select').forEach(function(sel) {
                    var opt = sel.options[sel.selectedIndex];
                    if (opt && opt.text.trim()) parts.push(opt.text.trim());
                });
                return parts.join(' | ');
            """) or ""
            city_check = (address.get("city") or "").lower()
            deep_match = (street_word in addr_detail
                          and city_check in addr_detail.lower()
                          and zip_check in addr_detail)
            if deep_match:
                print(f"  \u2705 Checkout ready \u2014 address verified (deep check)")
                return True

            # Last chance: address might be in the broader page text
            # but formatted differently (e.g. "1165 Island Park Rd" vs "1165 ISLAND PARK RD")
            page_lower = info['page_snippet'].lower()
            if (street_word.lower() in page_lower
                    and city_check in page_lower
                    and zip_check in info['page_snippet']):
                print(f"  \u2705 Checkout ready \u2014 address verified (case-insensitive)")
                return True

            # Address truly doesn't match — this is the WRONG address!
            print(f"  \u274c ADDRESS MISMATCH on checkout!")
            print(f"       Expected: {street_word} / {city_check} / {zip_check}")
            print(f"       Found: {addr_detail[:200]}")
            # Don't proceed — try to fix it on next attempt
            if attempt < max_attempts - 1:
                print(f"  \u2699\ufe0f  Re-setting address via API and retrying...")
                zoro.update_default_address(driver, address)
                driver.get("https://www.zoro.com/cart")
                time.sleep(2)
                driver.execute_script("""
                    var all = document.querySelectorAll('button, a');
                    for (var el of all) {
                        var r = el.getBoundingClientRect();
                        if (r.width === 0) continue;
                        var t = el.textContent.trim();
                        if (/proceed to checkout|checkout/i.test(t) && !/sign|log|cart/i.test(t)) {
                            el.click(); return;
                        }
                    }
                    document.querySelector('[data-za="cart-checkout-main-action-top"]')?.click();
                """)
                time.sleep(8)
                continue
            print(f"  \u274c BLOCKING ORDER \u2014 wrong address on checkout after {max_attempts} attempts")
            return False

        # ── 2. STATE RESTRICTION ──
        if info['err_state']:
            print("  \u26a0\ufe0f  State restriction \u2014 cannot ship here")
            return 'STATE_RESTRICTED'

        # ── 3. OLD LAYOUT EDIT MODE + SAVE ──
        if info['edit_mode'] and info['save_btn'] and not info['is_new']:
            if info['form_filled']:
                print("  \U0001f4dd Form pre-filled \u2014 clicking Save...")
            else:
                print("  \U0001f4dd Form empty \u2014 filling address + clicking Save...")
                _fill_old_layout_form(driver, address)
                time.sleep(1)
            driver.execute_script("""
                var btns = document.querySelectorAll('button');
                for (var b of btns) {
                    if (b.textContent.trim()==='Save' && b.offsetParent) {
                        var sec = b.closest('section,[class*="shipping"],[class*="address"],form,[class*="checkout"]');
                        if (sec) { b.click(); return; }
                    }
                }
            """)
            time.sleep(8)
            continue

        # ── 4. "Ship to This Address" ──
        if info['ship_to_btn']:
            print("  \U0001f4cd Clicking 'Ship to This Address'...")
            _click_ship_to_address(driver, address)
            time.sleep(5)
            continue

        # ── 5. NEW LAYOUT VUETIFY FLOW ──
        if info['change_addr'] or info['add_new_btn']:
            if info['change_addr'] and not info['add_new_btn']:
                driver.execute_script("""
                    function isVis(el){var r=el.getBoundingClientRect();return r.height>0;}
                    document.querySelectorAll('button,a').forEach(function(el){
                        if(isVis(el)&&el.textContent.trim()==='Change Address') el.click();
                    });
                """)
                print("  \U0001f4cd Clicked 'Change Address'")
                time.sleep(3)
                post = _get_checkout_state(driver)
                if post.get('has_remove_btn'):
                    _remove_non_billing_address(driver)
                    time.sleep(2)
                post = _get_checkout_state(driver)
                if post.get('has_add_new_btn'):
                    _add_address_via_modal(driver, address)
                    time.sleep(3)
                continue
            if info['add_new_btn']:
                if info['remove_btn']:
                    _remove_non_billing_address(driver)
                    time.sleep(2)
                _add_address_via_modal(driver, address)
                time.sleep(3)
                continue

        # ── 6. EDIT MODE, NO SAVE → state re-type ──
        if info['edit_mode']:
            print("  \U0001f504 Edit mode, no Save found \u2014 trying state re-select...")
            if _retype_state_to_trigger_shipping(driver, address):
                time.sleep(4)
                continue

        # ── 7. LAST RESORT: re-navigate ──
        if attempt < max_attempts - 1:
            print(f"  \u2699\ufe0f  Re-navigating cart \u2192 checkout...")
            driver.get("https://www.zoro.com/cart")
            time.sleep(3)
            driver.get("https://www.zoro.com/checkout")
            time.sleep(8)

    print("  \u274c Checkout not ready after all attempts")
    return False


def _ensure_address_and_shipping(driver, address):
    """Ensure correct shipping address is active AND shipping methods loaded.

    For multi-address accounts (Rajar Enterprise), the checkout's Vuetify UI
    requires: Change Address → Remove old → Add New Address → Save → Ship to.

    Returns True if address is ready and shipping visible, False otherwise.
    """
    from selenium.webdriver.common.keys import Keys

    for attempt in range(4):
        state = _get_checkout_state(driver)

        # DONE: shipping radio visible and no errors
        if state['address_ok']:
            return True

        # TRY 1: If "Ship to This Address" visible, click it
        if state['has_ship_to_btn']:
            print(f"  📍 'Ship to This Address' visible — clicking...")
            if _click_ship_to_address(driver, address):
                time.sleep(5)
                if _get_checkout_state(driver)['address_ok']:
                    return True

        # TRY 1.5: Quick state re-type trick — enter edit mode, re-type
        # the state to trigger Zoro's shipping lookup without removing the
        # address and adding it again.
        if attempt < 2 and not state['address_ok']:
            if _retype_state_to_trigger_shipping(driver, address):
                time.sleep(3)
                if _get_checkout_state(driver)['address_ok']:
                    print("  ✅ State re-type trick worked — shipping loaded")
                    return True
                # Might still need "Ship to This Address"
                new_state = _get_checkout_state(driver)
                if new_state['has_ship_to_btn']:
                    if _click_ship_to_address(driver, address):
                        time.sleep(5)
                        if _get_checkout_state(driver)['address_ok']:
                            print("  ✅ State re-type + Ship-to worked")
                            return True
                print("  ⚠️  State re-type didn't load shipping — falling back")

        # TRY 2: Full modal flow — Change Address → Remove → Add New → Save
        if state['has_change_addr'] or state['has_add_new_btn'] or state['has_remove_btn']:
            # Step A: Click "Change Address" if needed
            if state['has_change_addr'] and not state['has_add_new_btn']:
                driver.execute_script("""
                    function isVis(el) { var r = el.getBoundingClientRect(); return r.height > 0; }
                    document.querySelectorAll('button, a').forEach(function(el) {
                        if (isVis(el) && el.textContent.trim() === 'Change Address') el.click();
                    });
                """)
                print("  📍 Clicked 'Change Address'")
                time.sleep(3)
                state = _get_checkout_state(driver)

            # Step B: Remove non-billing address (clears stale shipping)
            if state['has_remove_btn']:
                print("  🗑️  Removing old address...")
                _remove_non_billing_address(driver)
                time.sleep(2)
                state = _get_checkout_state(driver)

            # Step C: Add new address via modal
            if state['has_add_new_btn']:
                print("  📝 Adding address via checkout modal...")
                if _add_address_via_modal(driver, address):
                    time.sleep(2)
                    state = _get_checkout_state(driver)
                    if state['address_ok']:
                        return True
                    # May need "Ship to This Address" after save
                    if state['has_ship_to_btn']:
                        if _click_ship_to_address(driver, address):
                            time.sleep(5)
                            if _get_checkout_state(driver)['address_ok']:
                                return True
                else:
                    print("  ⚠️  Modal flow failed")

        # TRY 3: Re-navigate cart → checkout (old layout recovery)
        print(f"  ⚙️  Checkout stuck (attempt {attempt+1}) — re-navigating...")
        driver.get("https://www.zoro.com/cart")
        time.sleep(3)
        driver.get("https://www.zoro.com/checkout")
        time.sleep(6)

    return False


def _verify_address_on_checkout(driver, address, retries=2):
    """Verify that the correct address is showing on checkout. Returns True/False."""
    street_word = address["street_address"].split()[0]
    city_check = address["city"].lower()
    zip_check = address["zip_code"].replace("-", "")[:5]

    for attempt in range(retries + 1):
        # Quick check: if shipping is already loaded, read address from input fields
        has_shipping = driver.execute_script("""
            var t = document.body.innerText;
            var hasRadio = !!document.querySelector('input[type="radio"]');
            var hasGround = t.includes('Standard Ground') || t.includes('$8.00') || /\\$5\\.\\d\\d/.test(t);
            var hasPlaceOrder = !!document.querySelector('button[data-za="checkout-place-order"], input[value="Place Order"], button.place-order');
            // Also check for Place Order button by text
            if (!hasPlaceOrder) {
                var btns = document.querySelectorAll('button, input[type="submit"]');
                for (var b of btns) { if (b.textContent.trim() === 'Place Order' || b.value === 'Place Order') { hasPlaceOrder = true; break; } }
            }
            return hasGround || hasPlaceOrder;
        """)

        ship_text = driver.execute_script("""
            // 1. Dedicated shipping address step (Vuetify new layout)
            var el = document.querySelector('[data-za="shipping-address-step"]');
            if (el && el.textContent.trim().length > 10) return el.textContent;

            // 2. Collapsed/completed step — look for address in step summary
            var steps = document.querySelectorAll('.v-stepper-item, [class*="step"], [class*="checkout-step"], [data-za*="shipping"]');
            for (var i = 0; i < steps.length; i++) {
                var t = steps[i].textContent || '';
                if (t.length > 10 && t.length < 2000) {
                    if (/\\d{5}/.test(t) || /ship/i.test(t)) return t;
                }
            }

            // 3. Form inputs (old layout: address in edit mode with values filled)
            var inputs = document.querySelectorAll('input');
            var vals = [];
            for (var i = 0; i < inputs.length; i++) {
                var inp = inputs[i];
                if (inp.type === 'hidden' || inp.type === 'radio' || inp.type === 'checkbox') continue;
                if (inp.value && inp.value.trim()) vals.push(inp.value.trim());
            }
            var selects = document.querySelectorAll('select');
            for (var i = 0; i < selects.length; i++) {
                var opt = selects[i].options[selects[i].selectedIndex];
                if (opt && opt.text.trim()) vals.push(opt.text.trim());
            }
            if (vals.length >= 3) return vals.join(' | ');

            // 4. Data-za attribute fields
            var parts = [];
            ['full-name','company-name','address1','address2','city-state-zip','city-state','phone-number'].forEach(function(za){
                var found = document.querySelectorAll('[data-za="'+za+'"]');
                if (found.length > 0) parts.push(found[0].textContent);
            });
            if (parts.length > 0) return parts.join(' ');

            // 5. Broader body scan
            return document.body.innerText.substring(0, 5000);
        """)

        confirmed = (street_word in ship_text and city_check in ship_text.lower() and zip_check in ship_text)
        if confirmed:
            print(f"  ✅ Address verified on checkout")
            return True

        # If shipping is loaded (Place Order visible / Standard Ground showing),
        # NEVER refresh — that kills the shipping state.
        if has_shipping:
            # Shipping is loaded — address verify text didn't match but the
            # checkout is in a workable state.  Accept it and proceed.
            print(f"  ⚠️  Shipping loaded but address text mismatch — accepting anyway")
            print(f"       Expected: {street_word} / {city_check} / {zip_check}")
            print(f"       Got text[:200]: {(ship_text or '')[:200]}")
            return True

        if attempt < retries:
            # Check if page is in edit mode (form fields showing)
            is_edit_mode = driver.execute_script("""
                var labels = document.body.innerText;
                return labels.includes('Full Name') || labels.includes('First Name*') || labels.includes('Address*');
            """)
            if is_edit_mode:
                # Edit mode with NO shipping — try the state re-type trick first
                print(f"  ⚠️  Checkout in edit mode, no shipping (attempt {attempt + 1}) — trying state re-select...")
                # Import needed for state re-type
                _retype_state_to_trigger_shipping(driver, address)
                time.sleep(3)
                # Re-check if shipping appeared
                recheck_shipping = driver.execute_script("""
                    var t = document.body.innerText;
                    return t.includes('Standard Ground') || t.includes('$8.00') || /\\$5\\.\\d\\d/.test(t);
                """)
                if recheck_shipping:
                    print(f"  ✅ State re-select triggered shipping — accepting")
                    return True
                # Still no shipping — now refresh as last resort
                print(f"  ⚠️  State re-select didn't help — refreshing...")
                driver.get("https://www.zoro.com/checkout")
                time.sleep(6)
            else:
                print(f"  ⚠️  Address not found on checkout (attempt {attempt + 1}) — waiting for page transition...")
                print(f"       Expected: {street_word} / {city_check} / {zip_check}")
                print(f"       Got text[:200]: {(ship_text or '')[:200]}")
                # Page might still be transitioning after address modal close
                time.sleep(4)
                # Quick re-check before full refresh
                quick_text = driver.execute_script("return document.body.innerText.substring(0, 5000);") or ""
                if street_word in quick_text and city_check in quick_text.lower() and zip_check in quick_text:
                    print(f"  ✅ Address verified on checkout (after transition wait)")
                    return True
                driver.get("https://www.zoro.com/checkout")
                time.sleep(6)
        else:
            print(f"  ❌ Address mismatch on checkout page")
            print(f"       Expected: {street_word} / {city_check} / {zip_check}")
            print(f"       Got text[:200]: {(ship_text or '')[:200]}")
    return False


def _apply_coupons(driver, coupon_list, priority_coupons, asin, asin_coupon_cache, skip_priority, zoro_code=None):
    """Smart coupon application with per-zoro-code cache intelligence.
    Returns (success, used_coupon_code) or raises special return values as strings."""
    cache_key = zoro_code or asin or ""
    _cache = asin_coupon_cache or {}
    working_pairs = _cache.get("working_pairs", {})
    metadata = load_coupon_metadata()

    # Check if a coupon is already applied (leftover from a previous failed order)
    try:
        savings_el = driver.find_element(
            By.CSS_SELECTOR,
            "[data-za='order-summary-savings'], span[data-za='discount-total']")
        savings_text = savings_el.text
        if savings_text and "$0.00" not in savings_text:
            print(f"  ✅ Coupon already applied  |  Savings: {savings_text} — skipping")
            return "SUCCESS", None
    except Exception:
        pass

    # Build ordered coupon list: known working → priority → regular
    ordered_coupons = []

    # Step 1: Fast-track known working coupon for this zoro_code
    if cache_key in working_pairs:
        known_code = working_pairs[cache_key]
        is_known_priority = known_code in priority_coupons
        if is_known_priority or metadata.get(known_code) not in ("USED", "INVALID"):
            ordered_coupons.append(known_code)

    # Step 2: Priority coupons — reusable
    if not skip_priority:
        for code in priority_coupons:
            if code not in ordered_coupons:
                ordered_coupons.append(code)

    # Step 3: Regular coupons — single-use, skip USED/INVALID globally
    for code in coupon_list:
        if code in priority_coupons:
            continue
        if metadata.get(code) in ("USED", "INVALID"):
            continue
        if code not in ordered_coupons:
            ordered_coupons.append(code)

    if not ordered_coupons:
        return "NO_COUPONS_SKIP", None

    print(f"  🏷️  Trying {len(ordered_coupons)} coupons (filtered from {len(coupon_list)} total)")

    # Open promo section — scroll it into view first
    driver.execute_script("""
        var btn = document.querySelector("button[data-za='promo-dropdown']");
        if (btn) {
            btn.scrollIntoView({block: 'center'});
            if (btn.getAttribute('aria-expanded') !== 'true') { btn.click(); return 'opened'; }
            return 'already_open';
        }
        var btns = document.querySelectorAll('button, summary, [role="button"]');
        for (var i = 0; i < btns.length; i++) {
            var t = btns[i].textContent.trim().toLowerCase();
            if (t.includes('rules') || t.includes('exclusion')) continue;
            if ((t.includes('promo') || t.includes('coupon')) && btns[i].offsetParent !== null) {
                btns[i].scrollIntoView({block: 'center'});
                btns[i].click();
                return 'opened_fallback';
            }
        }
        return 'not_found';
    """)
    time.sleep(1.5)

    # Find coupon input — wait for it to appear after promo section expands
    coupon_input = None
    for sel in ["input[aria-label='Add a promotional code']", "input[aria-label='Promo Input']",
                "input[name='promoCode']", "input[placeholder*='romo']", "input[data-za*='promo']"]:
        try:
            coupon_input = WebDriverWait(driver, 4).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
            break
        except Exception:
            continue

    if not coupon_input:
        return "COUPON FAIL", None

    for code in ordered_coupons:
        code = str(code).strip()
        is_priority = code in priority_coupons
        # Clear the coupon input via JS
        driver.execute_script("""
            var input = document.querySelector("input[aria-label='Add a promotional code']") ||
                        document.querySelector("input[aria-label='Promo Input']");
            if (input) {
                var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                setter.call(input, '');
                input.dispatchEvent(new Event('input', { bubbles: true }));
            }
        """)
        time.sleep(0.5)
        # Re-find coupon input, scroll into view, and wait for interactability
        coupon_input = None
        for _ci_sel in ["input[aria-label='Add a promotional code']", "input[aria-label='Promo Input']",
                        "input[name='promoCode']", "input[placeholder*='romo']", "input[data-za*='promo']"]:
            try:
                coupon_input = WebDriverWait(driver, 3).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, _ci_sel)))
                break
            except Exception:
                continue
        if not coupon_input:
            print(f"  ⚠️  Coupon input not found — skipping coupons")
            return "COUPON FAIL", None
        # Scroll into view and attempt send_keys; fall back to JS injection
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", coupon_input)
        time.sleep(0.3)
        try:
            coupon_input.clear()
            coupon_input.send_keys(code)
        except Exception:
            # Fallback: inject value via JS native setter
            driver.execute_script("""
                var inp = arguments[0], val = arguments[1];
                var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                setter.call(inp, val);
                inp.dispatchEvent(new Event('input', {bubbles: true}));
                inp.dispatchEvent(new Event('change', {bubbles: true}));
            """, coupon_input, code)
        time.sleep(0.5)
        # Click "Apply" button
        try:
            apply_btn = driver.find_element(By.CSS_SELECTOR, "button[data-za='promo-cta']")
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", apply_btn)
            time.sleep(0.2)
            try:
                apply_btn.click()
            except Exception:
                driver.execute_script("arguments[0].click();", apply_btn)
        except Exception as _apply_err:
            print(f"  ⚠️  Apply button click failed: {_apply_err}")
        # Wait for savings or error to appear (smart poll instead of fixed 3s sleep)
        _wait_until(driver,
            "return !!document.querySelector('[data-za=\"order-summary-savings\"]') "
            "|| !!document.querySelector('span[data-za=\"discount-total\"]') "
            "|| !!document.querySelector('div.v-messages__message')",
            timeout=5, poll=0.3)

        # Check savings
        try:
            savings_el = driver.find_element(
                By.CSS_SELECTOR,
                "[data-za='order-summary-savings'], span[data-za='discount-total']")
            savings_text = savings_el.text
            if savings_text and "$0.00" not in savings_text:
                print(f"  ✅ Coupon: {code}  |  Savings: {savings_text}")
                if cache_key:
                    working_pairs[cache_key] = code
                return "SUCCESS", code
        except Exception:
            pass

        # Check error and classify
        try:
            error_el = driver.find_element(By.CSS_SELECTOR, "div.v-messages__message")
            error_text = error_el.text.lower()
            if "not valid for the item" in error_text:
                if is_priority:
                    print(f"  ❌ {code}: not valid for {cache_key} (category mismatch)")
                else:
                    print(f"  ❌ {code}: not valid for this item")
                continue
            elif "expired" in error_text or "no longer valid" in error_text:
                # Only regular coupons get globally invalidated.
                # Priority coupons are reusable; treat as category mismatch.
                if not is_priority:
                    update_coupon_status(code, "INVALID")
                    print(f"  ❌ {code}: expired/invalid — marked INVALID")
                else:
                    print(f"  ❌ {code}: expired for {cache_key} (priority, not globally invalidated)")
                continue
            else:
                # Unknown error — print it for debugging
                print(f"  ⚠️  {code}: {error_text}")
        except Exception:
            pass

    return "COUPON FAIL", None


def _dump_checkout_debug(driver, tag="net30"):
    """Save the full checkout page for debugging: raw HTML, rendered text, and a
    structured inventory of every payment-related element (radios, tabs, buttons,
    iframes) read from the LIVE DOM — so we can pin exact selectors even when the
    raw HTML misses late-rendered content. Files land in data/checkout_debug/.
    """
    try:
        base_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "data", "checkout_debug")
        os.makedirs(base_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = os.path.join(base_dir, f"{tag}_{ts}")

        with open(prefix + ".html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)

        inventory = driver.execute_script("""
            function vis(el) {
                if (!el) return false;
                var r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            }
            function labText(r) {
                var lab = r.closest('label') || (r.id ? document.querySelector('label[for="' + r.id + '"]') : null);
                return lab ? (lab.textContent || '').replace(/\\s+/g, ' ').trim().substring(0, 120) : '';
            }
            var out = {radios: [], tabs: [], buttons: [], payment_sections: [], iframes: []};
            document.querySelectorAll('input[type="radio"]').forEach(function(r) {
                out.radios.push({value: r.value || '', checked: !!r.checked, id: r.id || '',
                                 name: r.name || '', data_za: r.getAttribute('data-za') || '',
                                 label: labText(r), visible: vis(r)});
            });
            document.querySelectorAll('[role="tab"], [data-za*="Tab"], [data-za*="tab"]').forEach(function(t) {
                out.tabs.push({text: (t.textContent || '').replace(/\\s+/g, ' ').trim().substring(0, 120),
                               data_za: t.getAttribute('data-za') || '',
                               aria_selected: t.getAttribute('aria-selected') || '',
                               cls: (t.className || '').toString().substring(0, 150), visible: vis(t)});
            });
            document.querySelectorAll('button').forEach(function(b) {
                var txt = (b.textContent || '').replace(/\\s+/g, ' ').trim();
                var za = b.getAttribute('data-za') || '';
                if (/pay|card|net|invoice|terms|place|order/i.test(txt + ' ' + za)) {
                    out.buttons.push({text: txt.substring(0, 120), data_za: za,
                                      disabled: !!b.disabled, visible: vis(b)});
                }
            });
            document.querySelectorAll('[data-za*="payment" i], [class*="payment" i], [id*="payment" i]').forEach(function(s) {
                out.payment_sections.push({tag: s.tagName, data_za: s.getAttribute('data-za') || '',
                                           id: s.id || '', cls: (s.className || '').toString().substring(0, 150),
                                           text: (s.textContent || '').replace(/\\s+/g, ' ').trim().substring(0, 300)});
            });
            document.querySelectorAll('iframe').forEach(function(fr) {
                out.iframes.push({src: (fr.src || '').substring(0, 200), id: fr.id || ''});
            });
            return out;
        """)

        page_text = driver.execute_script("return document.body.innerText || '';")
        with open(prefix + ".txt", "w", encoding="utf-8") as f:
            f.write(f"URL: {driver.current_url}\n")
            f.write(f"Saved: {ts} | Tag: {tag}\n")
            f.write("=" * 60 + "\n\n")
            f.write("=== PAYMENT ELEMENT INVENTORY (live DOM) ===\n")
            f.write(json.dumps(inventory, indent=2))
            f.write("\n\n=== RENDERED PAGE TEXT ===\n")
            f.write(page_text)

        print(f"  🐞 Checkout debug dump saved:")
        print(f"     HTML: {prefix}.html")
        print(f"     TEXT: {prefix}.txt")
        return prefix
    except Exception as e:
        print(f"  ⚠️  Debug dump failed: {e}")
        return None


def _ensure_net30_payment(driver):
    """Ensure Net30 terms is the selected payment (accounts with terms default
    to it — this verifies, and selects it if a card was pre-selected).

    Returns True only when Net30 is CONFIRMED selected. Caller must fail closed
    on False: the user explicitly configured Net30, so placing on the credit
    card instead is never acceptable.
    """
    # Always capture the payment UI as the script sees it BEFORE touching it —
    # if this attempt fails in any way, this dump is the debugging source.
    _dump_checkout_debug(driver, tag="net30_before")

    result = driver.execute_script("""
        function radioText(r) {
            var lab = r.closest('label') || (r.id ? document.querySelector('label[for="' + r.id + '"]') : null);
            return ((lab ? lab.textContent : '') + ' ' + (r.value || '') + ' '
                    + (r.getAttribute('data-za') || '')).toUpperCase();
        }
        function isNet30(t) {
            return t.indexOf('NET') !== -1 || t.indexOf('INVOICE') !== -1 || t.indexOf('TERMS') !== -1;
        }
        var radios = document.querySelectorAll('input[type="radio"]');
        for (var r of radios) {
            if (isNet30(radioText(r))) {
                if (r.checked) return 'already';
                r.click(); r.dispatchEvent(new Event('change', {bubbles: true}));
                return 'switched';
            }
        }
        // Tab layout fallback (mirrors the creditCardTabRef layout)
        var tabs = document.querySelectorAll('[role="tab"], [data-za*="Tab"], [data-za*="tab"]');
        for (var tab of tabs) {
            var t = ((tab.textContent || '') + ' ' + (tab.getAttribute('data-za') || '')).toUpperCase();
            if (isNet30(t)) {
                var active = tab.getAttribute('aria-selected') === 'true' || tab.classList.contains('v-tab--selected');
                if (active) return 'already';
                tab.click(); return 'switched';
            }
        }
        return 'not_found';
    """)

    if result == 'switched':
        print("  🧾 Payment: Net30 terms (switched)")
        time.sleep(3)
    elif result == 'already':
        print("  🧾 Payment: Net30 terms (already selected)")
    else:
        print("  ⚠️  Net30 payment option not found on checkout page")
        _dump_checkout_debug(driver, tag="net30_notfound")
        return False

    # Belt-and-braces: confirm the credit card radio is NOT selected before
    # the caller clicks Place Order.
    cc_checked = driver.execute_script("""
        var radios = document.querySelectorAll('input[type="radio"]');
        for (var r of radios) { if (r.value === 'CREDIT_CARD' && r.checked) return true; }
        return false;
    """)
    if cc_checked:
        print("  ❌ Credit card is still the selected payment — refusing to place on Net30 mode")
        _dump_checkout_debug(driver, tag="net30_cc_stuck")
        return False
    return True


def _switch_payment_to_credit_card(driver):
    """Switch payment method to Credit Card for RESELLER profiles."""
    switched = driver.execute_script("""
        var radios = document.querySelectorAll('input[type="radio"]');
        for (var r of radios) {
            if (r.value === 'CREDIT_CARD' && !r.checked) {
                r.click(); r.dispatchEvent(new Event('change', {bubbles: true})); return 'radio_switched';
            } else if (r.value === 'CREDIT_CARD' && r.checked) { return 'radio_already'; }
        }
        var tab = document.querySelector('[data-za="creditCardTabRef"]');
        if (tab) {
            var isActive = tab.getAttribute('aria-selected') === 'true' || tab.classList.contains('v-tab--selected');
            if (isActive) return 'tab_already';
            tab.click(); return 'tab_switched';
        }
        return 'not_found';
    """)

    def _confirm_credit_card_selection():
        confirmed = driver.execute_script("""
            function isVis(el) {
                if (!el) return false;
                var r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            }
            var selectors = [
                'button[data-za="credit-card-panel-submit"]',
                'button[data-za="payment-method-submit"]',
                'button[data-za="select-payment-method"]'
            ];
            for (var selector of selectors) {
                var btn = document.querySelector(selector);
                if (btn && isVis(btn) && !btn.disabled) {
                    btn.click();
                    return btn.textContent.trim() || selector;
                }
            }

            var wanted = ['Save & Continue', 'Select Payment Method', 'Use This Card'];
            var buttons = document.querySelectorAll('button');
            for (var btn of buttons) {
                if (!isVis(btn) || btn.disabled) continue;
                var text = (btn.textContent || '').replace(/\\s+/g, ' ').trim();
                if (wanted.indexOf(text) !== -1) {
                    btn.click();
                    return text;
                }
            }
            return '';
        """)
        if confirmed:
            print(f"  💳 Payment confirm: {confirmed}")
            time.sleep(1)
            return True
        return False

    if switched in ("radio_switched", "tab_switched"):
        print(f"  💳 Payment: Credit Card")
        time.sleep(3)
        _confirm_credit_card_selection()
    elif switched in ("radio_already", "tab_already"):
        _confirm_credit_card_selection()
    else:
        print("  ⚠️  Credit Card option not found")


def _set_po_number(driver, po_number):
    """Set PO number on checkout."""
    driver.execute_script("""
        var inputs = document.querySelectorAll('input');
        for (var i = 0; i < inputs.length; i++) {
            var parent = inputs[i].closest('[data-za="purchase-order-number"]');
            if (parent) {
                var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                setter.call(inputs[i], arguments[0]);
                inputs[i].dispatchEvent(new Event('input', {bubbles: true}));
                inputs[i].dispatchEvent(new Event('change', {bubbles: true}));
                return;
            }
        }
    """, po_number)
    time.sleep(1)


def _select_shipping_method(driver):
    """Select '$5.00 Standard Ground' shipping if not already chosen."""
    selected = driver.execute_script("""
        // Try 1: Traditional radio buttons (old layout)
        var radios = document.querySelectorAll('input[type="radio"]');
        for (var i = 0; i < radios.length; i++) {
            var r = radios[i];
            var label = r.closest('label') || document.querySelector('label[for="'+r.id+'"]');
            if (!label) continue;
            var text = label.textContent;
            if (text.includes('Standard Ground') || text.includes('Ground')) {
                if (r.checked) return 'already';
                label.click();
                return 'clicked';
            }
        }
        // Try 2: Vuetify selection controls (new layout)
        var controls = document.querySelectorAll('.v-selection-control, .v-radio');
        for (var c of controls) {
            var txt = c.textContent;
            if (txt.includes('Standard Ground') || txt.includes('Ground')) {
                var inp = c.querySelector('input');
                if (inp && inp.checked) return 'already';
                c.click();
                return 'clicked';
            }
        }
        // Try 3: Shipping already shown as text (new layout, pre-selected)
        var shipEls = document.querySelectorAll(
            '[data-za="shipping-method-step"], [data-za*="shipping"]');
        for (var el of shipEls) {
            if (el.textContent.includes('Ground') || el.textContent.includes('Standard')) {
                return 'already';
            }
        }
        return 'not_found';
    """)
    if selected == 'already':
        print("  📦 Shipping: Standard Ground (already selected)")
    elif selected == 'clicked':
        print("  📦 Shipping: Standard Ground selected")
        _wait_until(driver, "return !!document.querySelector('input[type=\"radio\"]:checked')", timeout=3, poll=0.3)
    else:
        print("  ⚠️  Standard Ground shipping option not found")
    return selected != 'not_found'


def _place_order_button(driver):
    """The Place Order button, or None. Cheap and non-blocking — the caller does the waiting."""
    for sel in ('[data-za="checkout-place-order-button"]', '[data-za="checkout-main-action-top"]'):
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                return els[0]
        except Exception:
            continue
    try:
        for b in driver.find_elements(By.CSS_SELECTOR, 'button, input[type="submit"]'):
            if (b.text or b.get_attribute('value') or '').strip() == 'Place Order':
                return b
    except Exception:
        pass
    return None


# Everything that decides whether a click will actually land, read in one round-trip: is the
# document done, is the button on screen, enabled and uncovered, and has it stopped moving.
# Headless has no OS focus and no compositor pacing, so a click fired a moment too early hits a
# button whose Vue handler is not bound yet and nothing happens. That is what the old retry was
# papering over — the first click missed on 4 of 4 headless orders and 6 of 29 headed ones.
_PLACE_BTN_STATE_JS = """
var el = arguments[0];
if (!el || !el.isConnected) return {gone: true};
var r = el.getBoundingClientRect();
var cx = r.left + r.width / 2, cy = r.top + r.height / 2;
var scrim = document.querySelector('.v-overlay__scrim');
var hit = null;
try { hit = document.elementFromPoint(cx, cy); } catch (e) {}
return {
    ready: document.readyState === 'complete',
    w: r.width, h: r.height, x: Math.round(r.left), y: Math.round(r.top), cx: cx, cy: cy,
    inView: r.top >= 0 && r.left >= 0 &&
            r.bottom <= (window.innerHeight || 0) && r.right <= (window.innerWidth || 0),
    visible: el.offsetParent !== null && r.width > 0 && r.height > 0,
    enabled: !el.disabled && !el.classList.contains('v-btn--disabled') &&
             el.getAttribute('aria-disabled') !== 'true',
    loading: el.classList.contains('v-btn--loading') ||
             !!document.querySelector('.v-btn--loading'),
    scrim: !!(scrim && scrim.offsetParent !== null),
    covered: !!(hit && hit !== el && !el.contains(hit) && !hit.contains(el))
};
"""


def _why_not_clickable(state):
    """Turn the state blob into one readable reason, so a failure says what it was waiting on."""
    if not state:
        return "button not found"
    if state.get("gone"):
        return "button left the page"
    if not state.get("ready"):
        return "page still loading"
    if not state.get("visible"):
        return "button not visible"
    if not state.get("enabled"):
        return "button disabled"
    if state.get("loading"):
        return "still spinning"
    if state.get("scrim"):
        return "an overlay is covering the page"
    if state.get("covered"):
        return "something is drawn over the button"
    if not state.get("inView"):
        return "button off screen"
    return "button never stopped moving"


def _wait_place_order_clickable(driver, timeout=25):
    """Block until a click on Place Order can genuinely register, then hand back the button.

    Waits for each thing the old code only assumed: the document finished, no overlay or
    spinner is up, the button is visible, enabled and has nothing drawn over its centre, and
    its rectangle is identical across consecutive samples (a button still moving is a page
    still rendering). Returns (button, state), or (None, last_state) on timeout.
    """
    deadline = time.time() + timeout
    last, at, since = None, None, 0.0
    while time.time() < deadline:
        btn = _place_order_button(driver)
        if btn is None:
            at = None
            time.sleep(0.3)
            continue
        try:
            state = driver.execute_script(_PLACE_BTN_STATE_JS, btn)
        except Exception:
            at = None
            time.sleep(0.3)
            continue
        last = state
        if not state or state.get("gone"):
            at = None
            time.sleep(0.3)
            continue
        if not state.get("inView"):
            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center', inline:'center'});", btn)
            except Exception:
                pass
            at = None
            time.sleep(0.3)
            continue
        if (not state.get("ready") or not state.get("visible") or not state.get("enabled")
                or state.get("loading") or state.get("scrim") or state.get("covered")):
            at = None
            time.sleep(0.3)
            continue
        here = (state.get("x"), state.get("y"), round(state.get("w") or 0), round(state.get("h") or 0))
        if here != at:
            at, since = here, time.time()
            time.sleep(0.25)
            continue
        if time.time() - since >= 0.4:
            return btn, state
        time.sleep(0.25)
    return None, last


def _deliver_click(driver, btn, state):
    """Click the button for real. Returns how it was delivered, or None.

    CDP dispatches the same trusted mouse event Chrome makes for a human, at the coordinates
    we just verified are the button's — no window focus needed, which is what makes it work
    headless (the same reason buyer messages needed Input.insertText). Selenium's own click
    and a dispatched MouseEvent stay as fallbacks.
    """
    cx, cy = state.get("cx"), state.get("cy")
    if cx is not None and cy is not None:
        try:
            for ev, buttons, clicks in (("mouseMoved", 0, 0), ("mousePressed", 1, 1),
                                        ("mouseReleased", 0, 1)):
                driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                    "type": ev, "x": float(cx), "y": float(cy),
                    "button": "left", "buttons": buttons, "clickCount": clicks})
            return "cdp"
        except Exception:
            pass
    try:
        btn.click()
        return "selenium"
    except Exception:
        pass
    try:
        driver.execute_script(
            "arguments[0].click();"
            "arguments[0].dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));",
            btn)
        return "js"
    except Exception:
        return None


def _click_registered(driver, btn, timeout=5.0):
    """Did the page react to the click at all?

    A Place Order click that lands disables the button and puts it into Vuetify's loading
    state, or navigates outright. Checking that here means a dead click is caught in a second
    or two instead of after the full order-number poll.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if '/checkout' not in (driver.current_url or ''):
                return True
        except Exception:
            pass
        try:
            state = driver.execute_script(_PLACE_BTN_STATE_JS, btn)
        except Exception:
            return True            # element detached — the page is navigating
        if (not state or state.get("gone") or state.get("loading")
                or not state.get("enabled") or state.get("scrim")):
            return True
        time.sleep(0.25)
    return False


def _await_order_number(driver, timeout=25.0):
    """Wait for the WB number, in the URL first and then in the page text."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            url = driver.current_url or ''
            m = re.search(r'orderNo=(WB\d{8,})', url)
            if m:
                return m.group(1)
            if '/confirmation' in url:
                break
        except Exception:
            pass
        time.sleep(0.5)
    time.sleep(2)
    try:
        m = re.search(r'(WB\d{8,})', driver.execute_script("return document.body.innerText;") or "")
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _click_place_order(driver):
    """Click Place Order and wait for WB confirmation. Returns WB ID or None."""
    print("  📝 Placing order...")

    zoro_id = None
    for attempt in range(3):
        btn, state = _wait_place_order_clickable(driver)
        if btn is None:
            # It never became perfectly clickable. If the button is at least present, visible
            # and enabled, click it anyway — that is what the old code did unconditionally, so
            # failing here instead would be a step backwards. Drop the cached coordinates: the
            # reason we are here is that they are covered or still moving, so go through the
            # element itself, which also lets Selenium report an intercepted click.
            why = _why_not_clickable(state)
            btn = _place_order_button(driver)
            if btn is None or not (state and state.get("visible") and state.get("enabled")):
                print(f"  ❌ Place Order never became clickable ({why})")
                return None
            print(f"  ⚠️  Place Order never fully settled ({why}) — clicking it anyway")
            state = {k: v for k, v in state.items() if k not in ("cx", "cy")}

        how = _deliver_click(driver, btn, state)
        if how is None:
            print("  ❌ Could not deliver the click to Place Order")
            return None

        if not _click_registered(driver, btn):
            # Nothing moved: no spinner, no disable, no navigation. Nothing was submitted, so
            # clicking again cannot duplicate an order.
            print(f"  ⚠️  Click {attempt + 1} ({how}) did not register — retrying")
            continue
        print(f"  🖱️  Place Order clicked ({how}, attempt {attempt + 1})")

        zoro_id = _await_order_number(driver)
        if zoro_id:
            break

        # The click DID register but no order number came back. Only click again if the button
        # has returned to its idle state on the checkout page, which means the submission was
        # abandoned client-side. While it is still spinning or disabled an order may be in
        # flight, and a second click would place a duplicate — stop and let the caller flag it.
        try:
            after = driver.execute_script(_PLACE_BTN_STATE_JS, _place_order_button(driver))
        except Exception:
            after = None
        idle = bool(after and not after.get("gone") and after.get("enabled")
                    and not after.get("loading") and '/checkout' in (driver.current_url or ''))
        if not idle:
            print("  ⏸️  No order number, and the page has not returned to an idle checkout — "
                  "not clicking again (a second click could duplicate the order)")
            break
        if attempt < 2:
            print("  ⚠️  Submission came back with no order number — trying once more")

    if not zoro_id:
        print(f"  ❌ Order may have failed. URL: {driver.current_url[:80]}")

    return zoro_id


def _extract_checkout_total(driver, attempts=5):
    """Read the final checkout total from the order summary."""
    for _ in range(max(1, attempts)):
        try:
            total_text = driver.execute_script("""
                var selectors = [
                    '[data-za="order-summary-total"]',
                    '[data-za="total-price"]',
                    '[data-za="checkout-order-total"]'
                ];
                for (var selector of selectors) {
                    var el = document.querySelector(selector);
                    if (el && el.textContent) {
                        return el.textContent.trim();
                    }
                }
                var text = document.body.innerText || '';
                var lines = text.split('\\n');
                for (var i = 0; i < lines.length; i++) {
                    var line = lines[i].trim();
                    if (/^total$/i.test(line) && lines[i + 1]) {
                        return lines[i + 1].trim();
                    }
                    if (/order summary total/i.test(line)) {
                        return line;
                    }
                }
                return null;
            """)
        except Exception:
            total_text = None
        if total_text:
            total_match = re.search(r'\$([\d,]+\.\d{2})', total_text)
            if total_match:
                return float(total_match.group(1).replace(',', ''))
        time.sleep(1)
    return None


# The order-summary lines on Zoro's checkout page, read as separate numbers. Every field is
# None when it is not on the page; nothing is derived from anything else.
#
# This exists because the page-checkout path has no API order response to read a summary
# from. Before it, "Cost" fell back to the cart total (which excludes tax and shipping) and
# "Tax" was N/A on every attributed order — 97% tax capture until 17 Sep, 0% from the 19th.
_SUMMARY_JS = r"""
var out = {subtotal: null, shipping: null, tax: null, discount: null, total: null};
var money = function (s) {
    var m = /-?\$\s*([\d,]+\.\d{2})/.exec(s || '');
    if (!m) return null;
    var v = parseFloat(m[1].replace(/,/g, ''));
    return (s.indexOf('-') !== -1 && s.indexOf('-') < s.indexOf('$')) ? -v : v;
};
// "Free" is an amount: Zoro charged nothing for that line.
var amount = function (s) {
    var v = money(s);
    if (v === null && /^\s*[:\-]?\s*(free|no charge|\$?0(\.00)?)\s*$/i.test(s || '')) v = 0;
    return v;
};
// 1) Zoro's own hooks, when present.
var za = {subtotal: ['order-summary-subtotal'], shipping: ['order-summary-shipping'],
          tax: ['order-summary-tax', 'order-summary-estimated-tax'],
          discount: ['order-summary-discount', 'order-summary-promo'],
          total: ['order-summary-total', 'total-price', 'checkout-order-total']};
Object.keys(za).forEach(function (k) {
    za[k].forEach(function (sel) {
        if (out[k] !== null) return;
        var el = document.querySelector('[data-za="' + sel + '"]');
        if (el && el.textContent) out[k] = amount(el.textContent);
    });
});
// 2) Fall back to the visible summary text: a short label line, then the amount on the same
//    line (after the label) or on the next one. Labels as Zoro has written them over time:
//    "Estimated Tax", "Est. Sales Tax", "Taxes", "Merchandise Subtotal", "Shipping  Free".
var lines = (document.body.innerText || '').split('\n').map(function (l) { return l.trim(); });
var labels = {subtotal: /^(merchandise\s+)?subtotal\b/i,
              shipping: /^(shipping|delivery|freight)\b/i,
              tax: /^((est\.?|estimated|sales)\s+){0,2}tax(es)?\b/i,
              discount: /^(discount|promo(tion)?|coupon|savings)\b/i,
              total: /^(order\s+|grand\s+)?total\b/i};
for (var i = 0; i < lines.length; i++) {
    if (lines[i].length > 60) continue;
    Object.keys(labels).forEach(function (k) {
        if (out[k] !== null || !labels[k].test(lines[i])) return;
        var v = amount(lines[i].replace(labels[k], ''));
        if (v === null && lines[i + 1]) v = amount(lines[i + 1]);
        if (v !== null) out[k] = v;
    });
}
return out;
"""


_SUMMARY_KEYS = ("subtotal", "shipping", "tax", "discount", "total")


def _derive_tax(s):
    """Tax as the remainder of Zoro's own total, for a page that shows no tax line.

    Zoro's summary adds up as total = subtotal - discount + shipping + tax, so once the other
    four are read the tax is what is left (20 Sep: subtotal 29.70 + shipping 5.99 = 35.69,
    total 37.90 → tax 2.21, and the page had no line saying so). A remainder that is negative
    or absurd — more than a quarter of the subtotal — means a line was misread, so None."""
    if s["total"] is None or s["subtotal"] is None or s["shipping"] is None:
        return None, False
    rem = round(s["total"] - (s["subtotal"] - (s["discount"] or 0.0) + s["shipping"]), 2)
    if rem < 0 or rem > max(1.0, 0.25 * s["subtotal"]):
        return None, False
    return (0.0 if rem < 0.005 else rem), True


def _extract_checkout_summary(driver):
    """{subtotal, shipping, tax, discount, total, tax_derived} from the checkout page. Never raises.

    Amounts are None when the page did not show them. tax_derived is True when tax was not
    on the page and was taken as the remainder of the total (see _derive_tax)."""
    empty = {k: None for k in _SUMMARY_KEYS}
    empty["tax_derived"] = False
    try:
        got = driver.execute_script(_SUMMARY_JS) or {}
    except Exception:
        return empty
    out = dict(empty)
    for k in _SUMMARY_KEYS:
        v = got.get(k)
        try:
            out[k] = None if v is None else round(float(v), 2)
        except (TypeError, ValueError):
            out[k] = None
    if out["discount"] is not None:
        out["discount"] = abs(out["discount"])
    if out["tax"] is None:
        out["tax"], out["tax_derived"] = _derive_tax(out)
    return out


def _format_checkout_result(status, *, wb_id=None, checkout_total=None,
                            profit=None, margin=None, return_details=False,
                            used_coupon_code=None, split_plan=None, tax=None,
                            discount=None, subtotal=None, shipping=None):
    """Return either legacy string output or structured checkout details."""
    if return_details:
        return {
            "status": status,
            "wb_id": wb_id,
            "checkout_total": checkout_total,
            "tax": tax,
            "discount": discount,
            # Raw facts as Zoro states them — recorded separately, never derived from each other.
            "subtotal": subtotal,
            "shipping": shipping,
            "profit": profit,
            "margin": margin,
            "used_coupon_code": used_coupon_code,
            "split_plan": split_plan,
        }
    if status == "SUCCESS":
        return wb_id
    if status == "FAILED":
        return None
    return status


# ==============================================================================
#  MAIN ENTRY POINT
# ==============================================================================

def place_order_fast(driver, sku, qty, address, po_number, active_profile,
                     earnings=None, pre_price=None, coupon_list=None,
                     asin=None, asin_coupon_cache=None,
                     skip_priority_coupons=False, priority_coupons=None,
                     min_margin=None, return_details=False, zoro_code=None,
                     payment_method="CREDIT_CARD"):
    """Fast hybrid order for ALL profiles.
    API: inventory + cart + address (no page loads, no DataDome triggers)
    Selenium: 1 checkout page load → select address → (coupon) → PO → Place Order

    Returns: WB order ID, 'OOS', 'STATE_RESTRICTED', 'COUPON FAIL',
             'NET30_RESTRICTED', 'NO_COUPONS_SKIP', 'LOW_PROFIT',
             or None on failure.
    """
    print(f"  🛒 Checkout: {sku} x{qty}")
    bot_log("info", "Checkout started", sku=sku, qty=qty)
    addr_id = None
    priority_coupons = priority_coupons or []

    try:
        zoro.ensure_on_site(driver)
        zoro.ensure_token(driver)

        if pre_price is not None:
            price = pre_price
        else:
            # Inventory
            cartable, status = zoro.check_inventory(driver, sku, qty)
            if not cartable:
                return _format_checkout_result("OOS", return_details=return_details)

            # Cart
            ok, price = zoro.clear_cart_and_add(driver, sku, qty)
            if not ok:
                return _format_checkout_result("FAILED", return_details=return_details)

        # Update shipping address
        addr_id = zoro.update_default_address(driver, address)
        if not addr_id:
            return _format_checkout_result("FAILED", return_details=return_details)
        print(f"  📍 Address set: {addr_id[:8]}")

        # ── Go to checkout via CART button (proper session init) ──
        # Direct /checkout navigation gets stale state. Going through
        # the cart page initializes the checkout session correctly —
        # address + shipping are loaded in one go.
        driver.get("https://www.zoro.com/cart")
        _wait_until(driver, "return document.readyState === 'complete' && (!!document.querySelector('[data-za=\"cart-checkout-main-action-top\"]') || !!document.querySelector('[data-za=\"cart-checkout-main-action-bottom\"]') || document.body.innerText.includes('Your cart is empty'))", timeout=6, poll=0.3)

        # Click the real "Proceed to Checkout" button
        clicked_checkout = driver.execute_script("""
            function isVis(el) {
                if (!el) return false;
                var r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            }
            // Try data-za selectors first (most reliable)
            var sels = [
                '[data-za="cart-checkout-main-action-top"]',
                '[data-za="cart-checkout-main-action-bottom"]',
                '[data-za="checkout-button"]',
                'a[href*="checkout"]'
            ];
            for (var s of sels) {
                var el = document.querySelector(s);
                if (el && isVis(el)) { el.click(); return 'da:' + s; }
            }
            // Fallback: find button/link with "Checkout" text
            var all = document.querySelectorAll('button, a');
            for (var el of all) {
                if (!isVis(el)) continue;
                var t = el.textContent.trim();
                if (/proceed to checkout|checkout/i.test(t) &&
                    !/sign|log|cart/i.test(t)) {
                    el.click(); return 'text:' + t;
                }
            }
            return '';
        """)

        if clicked_checkout:
            print(f"  🛒 Cart → Checkout ({clicked_checkout})")
            _wait_until(driver, "return document.readyState === 'complete' && (!!document.querySelector('[data-za=\"checkout-place-order-button\"]') || !!document.querySelector('[data-za=\"shipping-address-step\"]') || document.querySelectorAll('input[type=\"radio\"]').length > 0 || document.body.innerText.includes('Place Order'))", timeout=12, poll=0.4)
        else:
            # Fallback: direct navigation (cart might be empty display issue)
            print(f"  ⚠️  No checkout button on cart — navigating directly")
            driver.get("https://www.zoro.com/checkout")
            _wait_until(driver, "return document.readyState === 'complete' && (!!document.querySelector('[data-za=\"checkout-place-order-button\"]') || document.querySelectorAll('input[type=\"radio\"]').length > 0)", timeout=10, poll=0.4)

        # Wait for loading overlay
        for _w in range(20):
            scrim = driver.execute_script("""
                var el = document.querySelector('.v-overlay__scrim');
                return el && el.offsetParent !== null;
            """)
            if not scrim:
                break
            time.sleep(1)

        # ── Try to select shipping via Zoro's Vue store (fastest path) ──
        # The SPA keeps checkout state in Vuex. If we can dispatch the
        # shipping method action directly, no DOM clicking needed.
        api_shipping = driver.execute_script("""
            try {
                var app = document.querySelector('#app');
                if (!app || !app.__vue_app__) return 'no_vue';
                var store = app.__vue_app__.config.globalProperties.$store;
                if (!store) return 'no_store';
                var state = store.state;

                // Look for checkout module in Vuex state
                var co = state.checkout || state.Checkout || null;
                if (!co) {
                    // Try to find it in nested modules
                    for (var key in state) {
                        if (key.toLowerCase().includes('checkout') || key.toLowerCase().includes('order')) {
                            co = state[key];
                            break;
                        }
                    }
                }
                if (!co) return 'no_checkout_state';

                // Report what we found for debugging
                var keys = Object.keys(co);
                var shippingKeys = keys.filter(function(k) {
                    return k.toLowerCase().includes('ship');
                });
                return 'found:' + keys.length + ' keys, shipping: [' + shippingKeys.join(',') + ']';
            } catch(e) { return 'error:' + e.message; }
        """)
        if api_shipping and not api_shipping.startswith('no_') and not api_shipping.startswith('error:'):
            print(f"  🔍 Vue store checkout: {api_shipping}")

        # Check state restriction early
        restricted = driver.execute_script("""
            return document.body.innerText.includes('cannot be shipped to the state specified');
        """)
        if restricted:
            bot_log("warn", "State restriction at checkout", sku=sku)
            return _format_checkout_result("STATE_RESTRICTED", return_details=return_details)

        # Unified checkout readiness: address + shipping + verification
        ready = _wait_for_checkout_ready(driver, address)
        if ready == 'STATE_RESTRICTED':
            bot_log("warn", "Checkout blocked: state restricted", sku=sku)
            return _format_checkout_result("STATE_RESTRICTED", return_details=return_details)
        if not ready:
            bot_log("error", "Checkout not ready", sku=sku)
            return _format_checkout_result("FAILED", return_details=return_details)

        # Apply coupons (COUPON profiles)
        used_coupon_code = None
        if coupon_list:
            coupon_result, used_coupon_code = _apply_coupons(
                driver, coupon_list, priority_coupons,
                asin, asin_coupon_cache, skip_priority_coupons, zoro_code=zoro_code)
            if coupon_result in ("COUPON FAIL", "NET30_RESTRICTED", "NO_COUPONS_SKIP"):
                return _format_checkout_result(coupon_result, return_details=return_details)

        # PO number — skip for coupon profiles
        if "COUPON" not in active_profile:
            _set_po_number(driver, po_number)

        # Payment for RESELLER profiles: credit card by default; Net30 terms
        # when configured (fail closed — never silently fall back to the card).
        if "RESELLER" in active_profile:
            if str(payment_method or "").upper() == "NET30":
                if not _ensure_net30_payment(driver):
                    print("  ❌ Net30 configured but could not be confirmed at checkout — NOT placing")
                    return _format_checkout_result("FAILED", return_details=return_details)
            else:
                _switch_payment_to_credit_card(driver)

        # Select shipping method (Standard Ground $5)
        _select_shipping_method(driver)

        checkout_total = _extract_checkout_total(driver)
        profit = None
        margin = None
        if checkout_total is not None:
            print(f"  💰 Checkout total: ${checkout_total:.2f}")
            if earnings is not None:
                profit = round(earnings - checkout_total, 2)
                margin = (earnings - checkout_total) / checkout_total if checkout_total > 0 else 0.0
                icon = "✅" if profit >= 0 else "⚠️"
                print(f"  {icon} Final Profit: ${profit:.2f}")
                print(f"  📊 Final Margin: {margin:.1%}")
                if min_margin is not None and margin < min_margin:
                    print(f"  🛑 LOW PROFIT — checkout gate ({margin:.1%} < {min_margin:.0%})")
                    return _format_checkout_result(
                        "LOW_PROFIT",
                        checkout_total=checkout_total,
                        profit=profit,
                        margin=margin,
                        return_details=return_details,
                    )
        else:
            print("  ⚠️  Checkout total not found")

        # Place order
        zoro_id = _click_place_order(driver)
        if zoro_id:
            bot_log("info", "ORDER PLACED", wb_id=zoro_id, sku=sku, qty=qty,
                    total=checkout_total, profit=profit, margin=margin)
        else:
            bot_log("error", "Place order FAILED", sku=sku)
        if zoro_id and used_coupon_code and used_coupon_code != "AUTO_APPLIED":
            update_coupon_status(used_coupon_code, "USED")

        return _format_checkout_result(
            "SUCCESS" if zoro_id else "FAILED",
            wb_id=zoro_id,
            checkout_total=checkout_total,
            profit=profit,
            margin=margin,
            return_details=return_details,
            used_coupon_code=used_coupon_code,
        )

    except Exception as e:
        bot_log("error", "Checkout exception", sku=sku, err=str(e)[:300])
        print(f"  ❌ Checkout error: {e}")
        return _format_checkout_result("FAILED", return_details=return_details)
