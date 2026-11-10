"""
order_config.py — All configuration, profiles, selectors, and constants.
Single source of truth for the auto_orders bot.
"""

import os
import sys
import json
import functools

# Force all print() to flush immediately (critical for piped output)
print = functools.partial(print, flush=True)

# ==============================================================================
#  PATHS & ENVIRONMENT
# ==============================================================================
USER_HOME = os.path.expanduser("~")
DOWNLOAD_DIRECTORY = os.path.join(USER_HOME, "Downloads")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUTOMATIONS_DIR = os.path.dirname(BASE_DIR)
CONFIG_PATH = os.path.join(AUTOMATIONS_DIR, "bot_config.json")

# Token directory — moved to data/All_Tokens after directory reorganization
TOKEN_DIRECTORY = os.path.join(AUTOMATIONS_DIR, "data", "All_Tokens")
MASTER_CREDENTIALS_FILE = os.path.join(TOKEN_DIRECTORY, "AB ECOM Credentials.json")
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Coupon metadata
COUPON_METADATA_FILE = os.path.join(AUTOMATIONS_DIR, "data", "dataset", "coupon_metadata.json")

# ==============================================================================
#  SYSTEM SETTINGS
# ==============================================================================
ACTION_TIMEOUT = 45
CLEAR_CACHE_INTERVAL = 100
ORDER_LIMIT = 0              # Max orders per cycle (0 = unlimited)

# Anti-restriction velocity  (stealth bypass handles detection — keep fast)
ORDER_DELAY_MIN = 0
ORDER_DELAY_MAX = 0
WARMUP_ORDERS = 0
API_JITTER_MIN = 0
API_JITTER_MAX = 0
SLOWDOWN_AFTER_ERRORS = 999
MAX_ORDERS_PER_CYCLE = 0       # 0 = unlimited

# Restriction recovery  (escalating: 30s → 60s → 120s)
RESTRICTION_QUICK_WAITS = [30, 60, 120]
RESTRICTION_LONG_COOLDOWN = 300

# States
BLOCKED_STATES = ['PR', 'HI', 'AK']

# ==============================================================================
#  ACCOUNT SETTINGS
# ==============================================================================
DM_EARNINGS_EMAIL = "abecomllc26"
HS_EARNINGS_EMAIL = "qurtdis"
WR_EARNINGS_EMAIL = "wrenchlyllc"     # Wrenchly's 'Sold, ship now' mail (owner, 6 Nov 2026)
# Duplicate-check Gmail accounts.
# LESSON FROM THE 2026-06/07 DUPLICATE INCIDENT: the per-profile lists used to be just 2
# inboxes each and DID NOT include where confirmations actually land — DM orders' Zoro
# "Has Been Received" emails go to `salesmercentile`, which was in NEITHER list, so ~200
# duplicates went completely unseen. The lists now cover EVERY inbox that receives Zoro/
# Grainger confirmations. The hard local placement_lock is the authoritative guard now;
# this Gmail search is defense-in-depth (and fails CLOSED on error).
#
# The list is loaded from config/accounts.json (the declared single source of truth) —
# union of "zoro_accounts" + "gmail_accounts". The frozen list below is a fallback used
# ONLY if the JSON is unreadable or its lists come back empty (FAIL-CLOSED: an empty
# result must never shrink the duplicate check).
_FALLBACK_CONFIRMATION_INBOXES = [
    "abecomllc26", "abecomllc02", "salesmercentile", "salesmercentile1",
    "qurtdis", "asad_amazon_us", "inhur_llc", "wrenchlyllc", "toolzsolutionsllc220",
]


def _load_confirmation_inboxes():
    """Union of zoro_accounts + gmail_accounts from config/accounts.json
    (deduplicated, order-preserving). Falls back to the frozen 2026-07 list
    if the JSON cannot be read or the lists are empty."""
    try:
        from utils.accounts_config import zoro_accounts, gmail_accounts
        seen, merged = set(), []
        for inbox in list(zoro_accounts()) + list(gmail_accounts()):
            if inbox and inbox not in seen:
                seen.add(inbox)
                merged.append(inbox)
        if merged:
            return merged
        print("[CONFIG] ⚠️ accounts.json inbox lists are empty — using built-in fallback")
    except Exception as e:
        print(f"[CONFIG] ⚠️ could not load inboxes from accounts.json ({e}) — using built-in fallback")
    return list(_FALLBACK_CONFIRMATION_INBOXES)


ALL_CONFIRMATION_INBOXES = _load_confirmation_inboxes()

# Both profile families now check the full set (a too-narrow list is what caused the incident).
HS_DUPLICATE_CHECK_LIST = list(ALL_CONFIRMATION_INBOXES)
DM_DUPLICATE_CHECK_LIST = list(ALL_CONFIRMATION_INBOXES)
WR_DUPLICATE_CHECK_LIST = list(ALL_CONFIRMATION_INBOXES)

# Backwards compatibility — all accounts (used by legacy code)
DUPLICATE_CHECK_LIST = list(ALL_CONFIRMATION_INBOXES)

# Profit margins
COUPON_MARGIN_LIMIT = -0.20
RESELLER_MARGIN_LIMIT = -0.20
ZORO_SELECT_MARGIN_LIMIT = -0.20

# Zoro Select reseller discount — reseller accounts get 10% off Zoro Select branded products
ZORO_SELECT_DISCOUNT_RATE = 0.10


def coupon_beats_select(coupon_discount, cart_subtotal, rate=None):
    """Owner's rule (28 Sep 2026): on a Zoro Select item the reseller already saves `rate`
    (ZORO_SELECT_DISCOUNT_RATE, 10 %) of the cart, so a coupon is worth the coupon profile only
    when it saves MORE than that — by more than a cent. A 10 % priority code (ORGANIZE…, FALL…)
    saves the same → the reseller places it; a 15–20 % code saves more → the coupon profile.
    Pure; used by the supervisor (engine/orders.py) and the checker (engine/priority_check.py).
    Numbers that cannot be read keep the coupon, i.e. the behaviour before this rule."""
    r = ZORO_SELECT_DISCOUNT_RATE if rate is None else float(rate)
    try:
        return float(coupon_discount or 0) > round(float(cart_subtotal or 0) * r, 2) + 0.01
    except (TypeError, ValueError):
        return True
# Zoro small-order fee — $5.99 since June 2026 (was $5.00). Charged when the
# PRE-coupon subtotal is under the $50 free-shipping threshold.
ZORO_SMALL_ORDER_FEE = 5.99
ZORO_SELECT_SHIPPING_FLAT = ZORO_SMALL_ORDER_FEE  # legacy alias (still imported by engine/orders.py)

# Shipping filler — cheap SKU to add when cart is close to $50 free shipping threshold
# Adding this item costs less than the small-order fee it saves
SHIPPING_FILLER_SKU = "G500699916"       # e.g. $0.71 coupling
SHIPPING_FILLER_PRICE = 0.78             # Expected unit price (for qty calc) — Zoro charged 0.78 on 6 Oct 2026
FREE_SHIPPING_THRESHOLD = 50.0           # Zoro free shipping threshold
FILLER_TRIGGER_MIN = 47.0                # Only add filler if cart is above this
FILLER_MAX_QTY = 5                       # Safety cap

# ==============================================================================
#  BILLING ADDRESSES (account default — never changes)
# ==============================================================================
# These are sent in the order payload so Zoro never changes your default.
# HS_RESELLER is left empty — Zoro locked that account's address, so we
# fetch it dynamically (fallback). All others use hardcoded values.

DM_RESELLER_BILLING = {
    "firstName": "Qurat",
    "lastName": "Samoon",
    "companyName": "AB ECOM LLC",
    "address1": "7901 4TH ST N",
    "address2": "STE 15419",
    "city": "Saint Petersburg",
    "state": "FL",
    "zip": "33702",
    "phoneNumber": "4044619909",
}

DM_COUPON_BILLING = {
    "firstName": "Imran-Ullah",
    "lastName": "Rajar",
    "companyName": "Wrenchly Solutions LLC",
    "address1": "5900 Balcones Dr",
    "address2": "Ste 23247",
    "city": "Austin",
    "state": "TX",
    "zip": "78731",
    "phoneNumber": "4044619909",
}

HS_COUPON_BILLING = {
    "firstName": "INUHR",
    "lastName": "ECOM",
    "companyName": "INUHR LLC",
    "address1": "FDLE HQ 813 LAKE BRADFORD RD",
    "address2": "",
    "city": "Tallahassee",
    "state": "FL",
    "zip": "323044712",
    "phoneNumber": "4044619909",
}


# Deciding-ASIN routing: Zoro cart total (undiscounted, $) at/above which an
# order for a "Deciding" ASIN is labelled for the coupon profile instead of
# being placed by the reseller profile. Matches the coupon cart minimum.
# GUI-overridable via bot_config.json key "deciding_coupon_min".
DECIDING_COUPON_MIN_DEFAULT = 50.0

# Target profit margin used by the seller note's PI (price-increase) suggestion:
# the Amazon price a listing would need to reach this margin. Decimal (0.10 = 10%).
# GUI-overridable via bot_config.json key "pi_target_margin".
PI_TARGET_MARGIN_DEFAULT = 0.10


# ==============================================================================
#  PROFILE DEFINITIONS
# ==============================================================================
PROFILES = {
    "DM_COUPON": {
        "port": 9221,
        "profile_folder_name": "hathoro_coupon_orders",
        "po_number": "HATHORO",
        "target_label": "MR",
        "min_profit_margin": COUPON_MARGIN_LIMIT,
        "gmail_earnings_account": DM_EARNINGS_EMAIL,
        "gmail_duplicates_list": DM_DUPLICATE_CHECK_LIST,
        "blocked_order_ids": [],
        "billing_address": DM_COUPON_BILLING,
        "focus_priority_coupon": "FACILITY8M26",
        "focus_priority_coupon_enabled": False,
    },
    "DM_RESELLER": {
        "port": 9222,
        "profile_folder_name": "hathoro_reseller_orders2",
        "po_number": "Hathoro",
        "target_label": "AB ECOM",
        "min_profit_margin": RESELLER_MARGIN_LIMIT,
        "gmail_earnings_account": DM_EARNINGS_EMAIL,
        "gmail_duplicates_list": DM_DUPLICATE_CHECK_LIST,
        "blocked_order_ids": [],
        "billing_address": DM_RESELLER_BILLING,
    },
    "HS_COUPON": {
        "port": 9223,
        "profile_folder_name": "hypersale_coupon_orders",
        "po_number": "HOANK",
        "target_label": "MR",
        "min_profit_margin": COUPON_MARGIN_LIMIT,
        "gmail_earnings_account": HS_EARNINGS_EMAIL,
        "gmail_duplicates_list": HS_DUPLICATE_CHECK_LIST,
        "blocked_order_ids": [],
        "billing_address": HS_COUPON_BILLING,
        "focus_priority_coupon": "FACILITY8M26",
        "focus_priority_coupon_enabled": False,
    },
    "HS_RESELLER": {
        "port": 9224,
        "profile_folder_name": "hypersale_reseller_orders",
        "po_number": "HOANK",
        "target_label": "RAJAR",
        "min_profit_margin": RESELLER_MARGIN_LIMIT,
        "gmail_earnings_account": HS_EARNINGS_EMAIL,
        "gmail_duplicates_list": HS_DUPLICATE_CHECK_LIST,
        "blocked_order_ids": [],
        "billing_address": {},
        "payment_method": "NET30",     # always — see NET30_PROFILES below
    },
    # Wrenchly (third store, 6 Nov 2026) is RESELLER-ONLY: there is no WR_COUPON, so nothing is
    # ever routed to a coupon sibling (no Deciding column, no late Coupon-column hand-off, no
    # coupon-sweep hold). place_all_mapped: every unlabelled Wrenchly order whose ASIN is in
    # Verified_SKU_Dataset is placed here, without a Priority_ASIN_Control column (owner's choice).
    # Billing is left empty like HS_RESELLER: checkout reads the Zoro account's default address.
    "WR_RESELLER": {
        "port": 9226,
        "profile_folder_name": "wrenchly_reseller_orders",
        "po_number": "WRENCHLY",
        "target_label": "WR",
        # 0 %, like HS_RESELLER's setting — not the -20 % code default: a new store starts by
        # placing nothing at a loss. The launcher's Min Margin box overrides it.
        "min_profit_margin": 0.0,
        "gmail_earnings_account": WR_EARNINGS_EMAIL,
        "gmail_duplicates_list": WR_DUPLICATE_CHECK_LIST,
        "blocked_order_ids": [],
        "billing_address": {},
        "place_all_mapped": True,
    },
}


# Profiles whose Zoro account pays on Net 30 terms — ALWAYS, whatever bot_config.json or the
# launcher says (owner, 8 Nov 2026). HS_RESELLER's account (Rajar Enterprise LLC) is the only one
# with Net 30, and its checkout has Net 30 selected by default: the placer sets the address,
# checks the order and places it on Net 30, and never picks a card. From 15 Oct to 7 Nov 2026 the
# launcher's payment choice had fallen back to "Credit Card" and every HS_RESELLER order was put
# on the AMEX ****6723 instead. Walmart orders (WALMART_RESELLER) use the same Zoro account and
# pay the same way (owner, 8 Nov 2026).
NET30_PROFILES = ("HS_RESELLER", "WALMART_RESELLER")


def profile_payment_method(profile_name, configured=None):
    """'NET30' or 'CREDIT_CARD' for this profile's Zoro checkout. A Net 30 profile gets 'NET30'
    no matter what is passed in; any other profile keeps its configured value."""
    if str(profile_name or "").strip().upper() in NET30_PROFILES:
        return "NET30"
    _pm = str(configured or "").upper().replace(" ", "").replace("-", "").replace("_", "")
    return "NET30" if _pm == "NET30" else "CREDIT_CARD"


def has_coupon_sibling(profile_name):
    """True when this store also has the other profile (HS/DM have both; Wrenchly has only
    WR_RESELLER). Every hand-off to a sibling checks this first."""
    p = str(profile_name or "").upper()
    sib = p.replace("_RESELLER", "_COUPON") if p.endswith("_RESELLER") else p.replace("_COUPON", "_RESELLER")
    return sib != p and sib in PROFILES

# ==============================================================================
#  URLS
# ==============================================================================
SKU_SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSa0wkioFPaXSpXdEmiMLhhYGCSRsXnzhjajOUjCjIU-rBeLyDWw6DPEFKnQpM-oPVv2pKQ3ICuKm7M/pub?gid=1358226720&single=true&output=csv"
COUPON_SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSa0wkioFPaXSpXdEmiMLhhYGCSRsXnzhjajOUjCjIU-rBeLyDWw6DPEFKnQpM-oPVv2pKQ3ICuKm7M/pub?gid=1693413738&single=true&output=csv"

# Google Sheets API (direct, fast — no CSV download delay)
import pathlib as _pathlib
GOOGLE_SHEET_ID = "1aNMSPkBjmUUgZNO45KXtMd88ddeBYJbqDxOknaEVooM"
GOOGLE_SHEET_SKU_TAB = "Verified_SKU_Dataset"
GOOGLE_SHEET_COUPON_TAB = "Coupons"
GOOGLE_SHEET_PRIORITY_ASIN_TAB = "Priority_ASIN_Control"
# Walmart SKU map — keyed by the FULL Walmart SKU (Zoro codes repeat across pack
# sizes, so the Walmart SKU is the only unique key). Cols: Walmart_SKU | Zoro Code | Qty to Place
GOOGLE_SHEET_WALMART_TAB = "Walmart_SKUs_Verified"
GOOGLE_SHEET_ORDER_LOG_TAB = "Order_Log"
_SA_NAME = "gen-lang-client-0493912626-fbfdbabc0441.json"
_SA_PATHS = [
    # Same directory as this config file
    _pathlib.Path(__file__).resolve().parent / _SA_NAME,
    # Inside the Automations root (where user likely placed it after copy)
    _pathlib.Path(AUTOMATIONS_DIR) / _SA_NAME,
    # Parent of Automations (e.g. Development_Unit/)
    _pathlib.Path(AUTOMATIONS_DIR).parent / _SA_NAME,
    # Legacy macOS locations with "Final Automation" subfolder
    _pathlib.Path(__file__).resolve().parent.parent / "Final Automation" / _SA_NAME,
    _pathlib.Path(__file__).resolve().parent.parent / _SA_NAME,
    _pathlib.Path(__file__).resolve().parent.parent.parent / "Final Automation" / _SA_NAME,
    _pathlib.Path(AUTOMATIONS_DIR).parent / "Final Automation" / _SA_NAME,
]
# Pick first path that exists; if none exist, default to Automations root so the
# error message on Windows is intuitive ("file not found in Automations folder").
GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE = str(
    next((p for p in _SA_PATHS if p.exists()), _pathlib.Path(AUTOMATIONS_DIR) / _SA_NAME)
)
AMAZON_BASE_ORDER_URL = "https://sellercentral.amazon.com/orders-v3/order/"
AMAZON_REPORT_URL = "https://sellercentral.amazon.com/reportcentral/FlatFileAllOrdersReport/1"
ZORO_BASE_SEARCH_URL = "https://www.zoro.com/search?q="
ZORO_CART_URL = "https://www.zoro.com/cart"

# ==============================================================================
#  SELECTORS
# ==============================================================================
from selenium.webdriver.common.by import By

# Amazon
AMAZON_ORDER_PENDING_SELECTOR = (By.XPATH, "//h4[contains(text(), 'Order is Pending')]")
AMAZON_ADDRESS_BLOCK_SELECTOR = '[data-test-id="shipping-section-buyer-address"]'
AMAZON_PHONE_SELECTOR = '[data-test-id="shipping-section-phone"]'
AMAZON_EDIT_BUTTON_SELECTOR = '[data-test-id="your-seller-order-edit-button"]'
AMAZON_ID_INPUT_FIELD_SELECTOR = 'input[data-test-id="your-seller-order-edit"]'
AMAZON_SAVE_BUTTON_SELECTOR = '[data-test-id="your-seller-order-save"]'
AMAZON_REPORT_DROPDOWN_SELECTOR = 'kat-dropdown.daily-time-picker-kat-dropdown-normal'
AMAZON_REQUEST_REPORT_BUTTON = 'kat-button[label="Request Download"]'
AMAZON_LATEST_DOWNLOAD_BUTTON = 'kat-table-row[role="row"]:first-of-type kat-button[label="Download"]'

# Zoro
ZORO_OOS_SELECTOR = (By.XPATH, "//*[contains(text(), 'Notify me when this product is back in stock')]")
ZORO_ADD_TO_CART_BUTTON_SELECTOR = (By.XPATH, '//div[@id="pdpBuyBox"]//button[@data-za="add-to-cart"]')
ZORO_ORDER_ID_SELECTOR = (By.CSS_SELECTOR, '[data-za="order-number"]')
ZORO_REMOVE_ITEM_BUTTON_SELECTOR = (By.CSS_SELECTOR, 'button[data-za*="-remove-cta"]')
ZORO_EMPTY_CART_MESSAGE_SELECTOR = (By.XPATH, "//*[contains(text(), 'Your cart is empty')]")
ZORO_PRODUCT_JSON_SELECTOR = (By.CSS_SELECTOR, 'script[data-za="product-microdata"]')

# Coupon / checkout (dual layout)
OLD_COUPON_INPUT = (By.CSS_SELECTOR, "input[aria-label='Promo Input']")
OLD_YOU_SAVED_MSG = (By.CSS_SELECTOR, "span[data-za='discount-total']")
OLD_PLACE_ORDER_BTN = (By.CSS_SELECTOR, 'button[data-za="checkout-main-action-top"]')
NEW_PROMO_DROPDOWN = (By.CSS_SELECTOR, "button[data-za='promo-dropdown']")
NEW_COUPON_INPUT = (By.CSS_SELECTOR, "input[aria-label='Add a promotional code']")
NEW_YOU_SAVED_MSG = (By.CSS_SELECTOR, "[data-za='order-summary-savings']")
NEW_PLACE_ORDER_BTN = (By.CSS_SELECTOR, 'button[data-za="checkout-place-order-button"]')
ZORO_APPLY_BTN = (By.CSS_SELECTOR, "button[data-za='promo-cta']")
ZORO_INVALID_MSG = (By.CSS_SELECTOR, "div.v-messages__message")

# ==============================================================================
#  STATE MAPS
# ==============================================================================
STATE_MAP = {'ALABAMA': 'AL', 'AL': 'AL', 'ALASKA': 'AK', 'AK': 'AK', 'ARIZONA': 'AZ', 'AZ': 'AZ', 'ARKANSAS': 'AR', 'AR': 'AR', 'CALIFORNIA': 'CA', 'CA': 'CA', 'COLORADO': 'CO', 'CO': 'CO', 'CONNECTICUT': 'CT', 'CT': 'CT', 'DELAWARE': 'DE', 'DE': 'DE', 'FLORIDA': 'FL', 'FL': 'FL', 'GEORGIA': 'GA', 'GA': 'GA', 'HAWAII': 'HI', 'HI': 'HI', 'IDAHO': 'ID', 'ID': 'ID', 'ILLINOIS': 'IL', 'IL': 'IL', 'INDIANA': 'IN', 'IN': 'IN', 'IOWA': 'IA', 'IA': 'IA', 'KANSAS': 'KS', 'KS': 'KS', 'KENTUCKY': 'KY', 'KY': 'KY', 'LOUISIANA': 'LA', 'LA': 'LA', 'MAINE': 'ME', 'ME': 'ME', 'MARYLAND': 'MD', 'MD': 'MD', 'MASSACHUSETTS': 'MA', 'MA': 'MA', 'MICHIGAN': 'MI', 'MI': 'MI', 'MINNESOTA': 'MN', 'MN': 'MN', 'MISSISSIPPI': 'MS', 'MS': 'MS', 'MISSOURI': 'MO', 'MO': 'MO', 'MONTANA': 'MT', 'MT': 'MT', 'NEBRASKA': 'NE', 'NE': 'NE', 'NEVADA': 'NV', 'NV': 'NV', 'NEW HAMPSHIRE': 'NH', 'NH': 'NH', 'NEW JERSEY': 'NJ', 'NJ': 'NJ', 'NEW MEXICO': 'NM', 'NM': 'NM', 'NEW YORK': 'NY', 'NY': 'NY', 'NORTH CAROLINA': 'NC', 'NC': 'NC', 'NORTH DAKOTA': 'ND', 'ND': 'ND', 'OHIO': 'OH', 'OH': 'OH', 'OKLAHOMA': 'OK', 'OK': 'OK', 'OREGON': 'OR', 'OR': 'OR', 'PENNSYLVANIA': 'PA', 'PA': 'PA', 'RHODE ISLAND': 'RI', 'RI': 'RI', 'SOUTH CAROLINA': 'SC', 'SC': 'SC', 'SOUTH DAKOTA': 'SD', 'SD': 'SD', 'TENNESSEE': 'TN', 'TN': 'TN', 'TEXAS': 'TX', 'TX': 'TX', 'UTAH': 'UT', 'UT': 'UT', 'VERMONT': 'VT', 'VT': 'VT', 'VIRGINIA': 'VA', 'VA': 'VA', 'WASHINGTON': 'WA', 'WA': 'WA', 'WEST VIRGINIA': 'WV', 'WV': 'WV', 'WISCONSIN': 'WI', 'WI': 'WI', 'WYOMING': 'WY', 'WY': 'WY', 'DISTRICT OF COLUMBIA': 'DC', 'DC': 'DC', 'PUERTO RICO': 'PR', 'PR': 'PR'}
FULL_STATE_NAME_MAP = {'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas', 'CA': 'California', 'CO': 'Colorado', 'CT': 'Connecticut', 'DE': 'Delaware', 'FL': 'Florida', 'GA': 'Georgia', 'HI': 'Hawaii', 'ID': 'Idaho', 'IL': 'Illinois', 'IN': 'Indiana', 'IA': 'Iowa', 'KS': 'Kansas', 'KY': 'Kentucky', 'LA': 'Louisiana', 'ME': 'Maine', 'MD': 'Maryland', 'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota', 'MS': 'Mississippi', 'MO': 'Missouri', 'MT': 'Montana', 'NE': 'Nebraska', 'NV': 'Nevada', 'NH': 'New Hampshire', 'NJ': 'New Jersey', 'NM': 'New Mexico', 'NY': 'New York', 'NC': 'North Carolina', 'ND': 'North Dakota', 'OH': 'Ohio', 'OK': 'Oklahoma', 'OR': 'Oregon', 'PA': 'Pennsylvania', 'RI': 'Rhode Island', 'SC': 'South Carolina', 'SD': 'South Dakota', 'TN': 'Tennessee', 'TX': 'Texas', 'UT': 'Utah', 'VT': 'Vermont', 'VA': 'Virginia', 'WA': 'Washington', 'WV': 'West Virginia', 'WI': 'Wisconsin', 'WY': 'Wyoming', 'DC': 'District of Columbia', 'PR': 'Puerto Rico'}

# ==============================================================================
#  CONFIG LOADING & PROFILE SELECTION
# ==============================================================================

def load_gui_config():
    """Load bot_config.json overrides written by the GUI launcher."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print("[CONFIG] bot_config.json not found — using defaults")
        return {}
    except Exception as e:
        print(f"[CONFIG] Error reading bot_config.json: {e} — using defaults")
        return {}


def apply_gui_overrides(gui_cfg):
    """Apply GUI config overrides to PROFILES dict and global settings."""
    for pname, pvals in gui_cfg.get("profiles", {}).items():
        if pname in PROFILES:
            if "target_label" in pvals:
                PROFILES[pname]["target_label"] = pvals["target_label"]
            if "min_margin" in pvals:
                # GUI stores min_margin as integer percent (e.g. 5 = 5%)
                # Engine expects min_profit_margin as decimal (e.g. 0.05)
                try:
                    PROFILES[pname]["min_profit_margin"] = int(pvals["min_margin"]) / 100.0
                except (ValueError, TypeError):
                    pass

            if "est_margin_only" in pvals:
                PROFILES[pname]["margin_check_est_only"] = bool(pvals["est_margin_only"])

            if "labels_only" in pvals:
                # Labels-only mode: process only already-labelled orders; skip
                # Priority-ASIN discovery and Deciding-ASIN routing entirely.
                PROFILES[pname]["labels_only"] = bool(pvals["labels_only"])

            if "rush_mode" in pvals:
                # Rush mode: journal the per-order Amazon write-backs (WB/label
                # + seller notes) and flush them in batches so placements run
                # back-to-back. All checks stay per-order.
                PROFILES[pname]["rush_mode"] = bool(pvals["rush_mode"])

            if "ultra_mode" in pvals:
                # Ultra mode: rush, with the Amazon scraping and write-backs running on windows of
                # their own beside the placing (engine/ultra_crew.py).
                PROFILES[pname]["ultra_mode"] = bool(pvals["ultra_mode"])

            if "rush_batch" in pvals:
                try:
                    PROFILES[pname]["rush_batch"] = max(1, int(pvals["rush_batch"]))
                except (ValueError, TypeError):
                    pass
                    
            if "target_label_2" in pvals:
                PROFILES[pname]["target_label_2"] = pvals["target_label_2"]
                
            if "min_margin_2" in pvals:
                try:
                    PROFILES[pname]["min_profit_margin_2"] = int(pvals["min_margin_2"]) / 100.0
                except (ValueError, TypeError):
                    pass
                    
            if "order_limit" in pvals:
                try:
                    PROFILES[pname]["order_limit"] = int(pvals["order_limit"])
                except (ValueError, TypeError):
                    pass
            if "payment_method" in pvals:
                # Normalized to "NET30" / "CREDIT_CARD"; a NET30_PROFILES profile stays NET30.
                PROFILES[pname]["payment_method"] = profile_payment_method(pname, pvals["payment_method"])
            if "focus_priority_coupon" in pvals:
                PROFILES[pname]["focus_priority_coupon"] = str(pvals["focus_priority_coupon"] or "").strip()
            if "focus_priority_coupon_enabled" in pvals:
                PROFILES[pname]["focus_priority_coupon_enabled"] = bool(pvals["focus_priority_coupon_enabled"])
            if "blocked_order_ids" in pvals:
                PROFILES[pname]["blocked_order_ids"] = pvals["blocked_order_ids"]
    # Apply global blocked_order_ids from GUI
    global_blocked = gui_cfg.get("blocked_order_ids", [])
    if global_blocked:
        for pname in PROFILES:
            existing = PROFILES[pname].get("blocked_order_ids", [])
            merged = list(set(existing + global_blocked))
            PROFILES[pname]["blocked_order_ids"] = merged
    # Global Deciding-ASIN routing threshold (stamped into every profile so the
    # engine can read it from CONFIG like any other per-profile setting).
    if "deciding_coupon_min" in gui_cfg:
        try:
            _dmin = float(gui_cfg["deciding_coupon_min"])
            for pname in PROFILES:
                PROFILES[pname]["deciding_coupon_min"] = _dmin
        except (ValueError, TypeError):
            pass
    # Routing rules (bot_config.json "routing": promo-ineligible ASINs, deciding
    # minimum margin, supervisor mode for unlabelled orders). Stamped into every
    # profile so the engine reads them from CONFIG like any other setting.
    _routing = gui_cfg.get("routing") or {}
    for pname in PROFILES:
        PROFILES[pname]["routing"] = dict(_routing)
    print("[CONFIG] Loaded overrides from bot_config.json")


def select_profile(cli_arg=None):
    """Interactive profile selection. Returns (profile_name, profile_config).
    Accepts optional CLI arg as either profile number or exact profile key."""
    profile_keys = list(PROFILES.keys())

    print("=" * 50)
    print("  SELECT BOT PROFILE")
    print("=" * 50)
    for i, key in enumerate(profile_keys, 1):
        print(f"   {i}. {key}")

    if cli_arg:
        choice = str(cli_arg).strip()
    elif sys.stdin.isatty():
        choice = input("\n Enter profile [1-4 or key] (default 1): ").strip() or "1"
    else:
        # Spawned as subprocess (no TTY) — default to profile 1
        choice = "1"

    normalized = choice.upper()
    if normalized in PROFILES:
        name = normalized
    else:
        idx = int(choice) - 1 if choice.isdigit() else 0
        if idx < 0 or idx >= len(profile_keys):
            idx = 0
        name = profile_keys[idx]

    config = PROFILES[name]

    print(f"\n  ACTIVATING: {name}\n")
    print(f"   -> Port: {config['port']}")
    print(f"   -> PO Number: {config['po_number']}")
    print(f"   -> Target Label: {config['target_label']}")

    return name, config


def get_chrome_paths(profile_folder_name):
    """Return (profile_path, chrome_executable) based on OS."""
    if sys.platform == "darwin":
        profile_path = os.path.join(USER_HOME, "Library", "Application Support",
                                    "Google", "Chrome", profile_folder_name)
        chrome_exe = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    elif sys.platform == "win32":
        profile_path = os.path.join(USER_HOME, "AppData", "Local", "Google",
                                    "Chrome", "User Data", profile_folder_name)
        chrome_exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        if not os.path.exists(chrome_exe):
            chrome_exe = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    else:
        profile_path = os.path.join(USER_HOME, ".config", "google-chrome", profile_folder_name)
        chrome_exe = "google-chrome"
    return profile_path, chrome_exe


def get_priority_coupons(gui_cfg):
    """DEPRECATED: Priority coupons now come from the Google Sheets 'Coupons'
    tab (rows with a Percentage Off value). This returns the legacy list
    from bot_config.json as a fallback only."""
    return gui_cfg.get("priority_coupons", [])
