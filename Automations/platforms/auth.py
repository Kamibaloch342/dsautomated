"""
login_helper.py — Auto-login for Amazon Seller Central.

Detects the Seller Central login page, enters credentials, and handles
TOTP-based two-factor authentication automatically.

Credentials are read from All_Tokens/seller_central_creds.json.
"""
import functools
import os
import json
import time
import random

print = functools.partial(print, flush=True)

try:
    import pyotp
except ImportError:
    pyotp = None

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# ── paths ────────────────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CREDS_FILE = os.path.join(os.path.dirname(_SCRIPT_DIR), "data", "All_Tokens", "seller_central_creds.json")

# ── selectors (Amazon Seller Central login pages) ────────────────────
# Amazon A/B tests login pages frequently — keep multiple fallbacks.
_SEL_EMAIL_OPTS   = ["#ap_email", "input[name='email']", "#email", "input[type='email']"]
_SEL_PASSWORD_OPTS= ["#ap_password", "input[name='password']", "#password", "input[type='password']"]
_SEL_CONTINUE_OPTS= ["#continue", "#signInSubmit", "input[type='submit']"]
_SEL_SIGNIN_OPTS  = ["#signInSubmit", "#auth-signin-button", "input[type='submit']"]
_SEL_OTP_FIELD    = "#auth-mfa-otpcode"
_SEL_OTP_SUBMIT   = "#auth-signin-button"
_SEL_REMEMBER_CB  = "#auth-mfa-remember-device"

# ── keywords that identify a login / OTP page ────────────────────────
_LOGIN_URL_MARKERS = ["ap/signin", "ap/mfa"]


def _load_creds(account_key):
    """
    Reads credentials for *account_key* ("hoank" or "hathoro")
    from the JSON file.  Returns dict with email, password, totp_secret
    or None if unavailable.
    """
    if not os.path.exists(_CREDS_FILE):
        print(f"  ⚠️  Credentials file not found: {_CREDS_FILE}")
        return None
    try:
        with open(_CREDS_FILE, "r") as f:
            data = json.load(f)
        acct = data.get("accounts", {}).get(account_key)
        if not acct:
            print(f"  ⚠️  No credentials entry for account '{account_key}'.")
            return None
        # Sanity: don't use placeholder values
        if "FILL_IN" in acct.get("email", "") or "FILL_IN" in acct.get("password", ""):
            print(f"  ⚠️  Credentials for '{account_key}' still contain placeholder values. Fill them in first.")
            return None
        return acct
    except Exception as e:
        print(f"  ⚠️  Error reading credentials file: {e}")
        return None


def _generate_totp(secret):
    """Generate a 6-digit TOTP code from the base32 secret."""
    if pyotp is None:
        print("  ❌  pyotp is not installed. Run: pip install pyotp")
        return None
    try:
        totp = pyotp.TOTP(secret)
        code = totp.now()
        return code
    except Exception as e:
        print(f"  ❌  TOTP generation failed: {e}")
        return None


def _type_slowly(element, text, min_delay=0.04, max_delay=0.10):
    """Human-like character-by-character typing."""
    for ch in text:
        element.send_keys(ch)
        time.sleep(random.uniform(min_delay, max_delay))


def _find_with_fallbacks(driver, selectors, timeout=8, visible=True):
    """Try multiple CSS selectors; return the first matching element.
    If *visible* is True, waits for visibility, otherwise presence."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException

    for sel in selectors:
        try:
            if visible:
                el = WebDriverWait(driver, timeout).until(
                    EC.visibility_of_element_located((By.CSS_SELECTOR, sel))
                )
            else:
                el = WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, sel))
                )
            if el:
                return el
        except TimeoutException:
            continue
    return None


def is_login_page(driver):
    """Returns True if the current page is an Amazon login or MFA page."""
    try:
        url = driver.current_url.lower()
        return any(marker in url for marker in _LOGIN_URL_MARKERS)
    except Exception:
        return False


def resolve_account_key(identifier):
    """
    Maps various profile/account identifiers to a credential key
    ("hoank" or "hathoro").

    Accepts:
      - auto_tracking account_config dicts  (has "key")
      - auto_orders profile names           ("HS_COUPON", "DM_RESELLER", …)
      - direct keys                         ("hoank", "hathoro")
    """
    if isinstance(identifier, dict):
        return identifier.get("key", "").lower()

    s = str(identifier).lower().strip()
    if s in ("hoank", "hathoro"):
        return s
    if s.startswith("hs"):
        return "hoank"
    if s.startswith("dm"):
        return "hathoro"
    return s


def auto_login(driver, account_identifier, max_attempts=2):
    """
    Detects and handles Amazon Seller Central login + OTP automatically.

    Parameters
    ----------
    driver : selenium.webdriver.Chrome
        Active browser session.
    account_identifier : str | dict
        An account key ("hoank"), profile name ("HS_COUPON"),
        or auto_tracking account_config dict.
    max_attempts : int
        Number of login retries before giving up.

    Returns
    -------
    bool
        True  – login succeeded (or was not needed).
        False – login failed and manual intervention is required.
    """
    if not is_login_page(driver):
        return True  # already logged in

    account_key = resolve_account_key(account_identifier)
    creds = _load_creds(account_key)
    if not creds:
        print(f"  ⚠️  Auto-login unavailable for '{account_key}'. Manual login required.")
        return False

    email    = creds["email"]
    password = creds["password"]
    totp_secret = creds.get("totp_secret", "")

    for attempt in range(1, max_attempts + 1):
        print(f"  🔐  Auto-login attempt {attempt}/{max_attempts} for '{account_key}'...")

        try:
            # ── Step 1: Email ─────────────────────────────────────
            email_field = _find_with_fallbacks(driver, _SEL_EMAIL_OPTS, timeout=8)
            if email_field:
                email_field.clear()
                _type_slowly(email_field, email)
                time.sleep(random.uniform(0.3, 0.6))

            # ── Step 1b: Click "Continue" if present ──────────────
            # Amazon's two-step flow: email → Continue → password page
            # IMPORTANT: Only click #continue on the email-step page.
            # On the password-step page, #continue is the "Sign in with
            # a passkey" button — clicking it triggers errors.
            continue_btn = _find_with_fallbacks(driver, _SEL_CONTINUE_OPTS, timeout=3)
            if continue_btn:
                btn_text = continue_btn.text.strip().lower() if continue_btn.text else ""
                password_already_visible = _find_with_fallbacks(driver, _SEL_PASSWORD_OPTS, timeout=2) is not None

                if (continue_btn.is_displayed()
                        and "passkey" not in btn_text
                        and not password_already_visible):
                    continue_btn.click()
                    time.sleep(random.uniform(2.0, 3.5))

            # ── Step 2: Password ──────────────────────────────────
            pw_field = _find_with_fallbacks(driver, _SEL_PASSWORD_OPTS, timeout=10)
            if not pw_field:
                print(f"  ⚠️  Password field not found. Page may have changed.")
                continue
            pw_field.clear()
            _type_slowly(pw_field, password)
            time.sleep(random.uniform(0.3, 0.6))

            # ── Step 3: Click Sign-In ─────────────────────────────
            btn = _find_with_fallbacks(driver, _SEL_SIGNIN_OPTS, timeout=5)
            if not btn:
                print(f"  ⚠️  Sign-in button not found.")
                continue
            btn.click()
            time.sleep(random.uniform(2.0, 3.5))

            # ── Step 4: OTP / MFA ─────────────────────────────────
            # Check if we landed on an OTP page
            if "ap/mfa" in driver.current_url.lower() or _otp_field_present(driver):
                if not totp_secret or "FILL_IN" in totp_secret:
                    print("  ⚠️  OTP page detected but no TOTP secret configured. Manual OTP required.")
                    return False

                code = _generate_totp(totp_secret)
                if not code:
                    return False

                print(f"  🔑  Entering TOTP code...")
                otp_field = WebDriverWait(driver, 8).until(
                    EC.visibility_of_element_located((By.CSS_SELECTOR, _SEL_OTP_FIELD))
                )
                otp_field.clear()
                _type_slowly(otp_field, code, 0.05, 0.12)
                time.sleep(0.5)

                # Tick "Remember this device" if present
                try:
                    remember = driver.find_element(By.CSS_SELECTOR, _SEL_REMEMBER_CB)
                    if not remember.is_selected():
                        remember.click()
                except Exception:
                    pass

                try:
                    otp_btn = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, _SEL_OTP_SUBMIT))
                    )
                    otp_btn.click()
                    time.sleep(random.uniform(3.0, 5.0))
                except TimeoutException:
                    print("  ⚠️  OTP submit button not found.")
                    continue

            # ── Step 5: Verify success ────────────────────────────
            if not is_login_page(driver):
                print(f"  ✅  Auto-login successful for '{account_key}'.")
                return True
            else:
                print(f"  ⚠️  Still on login page after attempt {attempt}.")

        except Exception as e:
            print(f"  ❌  Login error on attempt {attempt}: {e}")

    print(f"  ❌  Auto-login failed after {max_attempts} attempts for '{account_key}'.")
    return False


def ensure_logged_in(driver, account_identifier, target_url=None):
    """
    Convenience wrapper:  if on a login page, attempts auto-login,
    then navigates to *target_url* (if provided).

    Can be called defensively after any driver.get() that might
    redirect to the sign-in page.
    """
    if is_login_page(driver):
        success = auto_login(driver, account_identifier)
        if not success:
            return False
    if target_url:
        driver.get(target_url)
        time.sleep(2)
        # Double-check after navigation
        if is_login_page(driver):
            return auto_login(driver, account_identifier)
    return True


def _otp_field_present(driver):
    """Quick check: is the OTP input visible?"""
    try:
        driver.find_element(By.CSS_SELECTOR, _SEL_OTP_FIELD)
        return True
    except Exception:
        return False
