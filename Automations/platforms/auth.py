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
# Amazon's "Switch accounts" page (read from the live page, 10 Nov 2026): one link per remembered
# account, an "Add account" link, and a "Sign out" link per account that must never be pressed.
_SEL_SWITCH_TILES = 'a[data-name="switch_account_request"]'
_SEL_SWITCH_ADD   = "#cvf-account-switcher-add-accounts-link"

# ── keywords that identify a login / OTP page ────────────────────────
# ap/forgotpassword: Amazon's "Password reset required" page (10 Nov 2026) — not signed in either.
_LOGIN_URL_MARKERS = ["ap/signin", "ap/mfa", "ap/forgotpassword"]

# Why the last auto_login() did not get through, in words a person can act on ("" = it did, or it
# has not run). Read by callers that report the failure.
last_problem = ""


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
    if s in ("hoank", "hathoro", "wrenchly"):
        return s
    if s.startswith("hs"):
        return "hoank"
    if s.startswith("dm"):
        return "hathoro"
    if s.startswith("wr"):          # WR_RESELLER / "Wrenchly" (third store, 6 Nov 2026)
        return "wrenchly"
    return s


def _submit_otp(driver, totp_secret):
    """Fill and submit Amazon's 2-step verification code. True if it was submitted.

    One implementation, used both when sign-in leads here and when the browser is already
    sitting on the page.
    """
    if not totp_secret or "FILL_IN" in totp_secret:
        print("  ⚠️  OTP page detected but no TOTP secret configured. Manual OTP required.")
        return False
    code = _generate_totp(totp_secret)
    if not code:
        return False
    print("  🔑  Entering TOTP code...")
    try:
        otp_field = WebDriverWait(driver, 8).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, _SEL_OTP_FIELD)))
    except TimeoutException:
        print("  ⚠️  OTP field did not appear.")
        return False
    otp_field.clear()
    _type_slowly(otp_field, code, 0.05, 0.12)
    time.sleep(0.5)
    # "Remember this device" — so the next headless run is not asked again.
    try:
        remember = driver.find_element(By.CSS_SELECTOR, _SEL_REMEMBER_CB)
        if not remember.is_selected():
            remember.click()
    except Exception:
        pass
    try:
        WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, _SEL_OTP_SUBMIT))).click()
    except TimeoutException:
        print("  ⚠️  OTP submit button not found.")
        return False
    time.sleep(random.uniform(3.0, 5.0))
    return True


def _pick_remembered_account(driver, email):
    """Amazon's "Switch accounts" page: choose the account to sign in with.

    When a session has ended Amazon may show, instead of the e-mail box, a list of the accounts this
    browser remembers ("Hifza Sultan — <e-mail>") with "Add account" under it. There is no e-mail
    and no password field on it, so the sign-in below found nothing and gave up with "Password field
    not found": on 10 Nov 2026 both Hathoro placers stood on this page, failed 11 times in 16 minutes
    and placed nothing. Press the account whose e-mail is the store's saved login; if it is not in the
    list, press "Add account", which leads to the usual e-mail page.

    Returns "account", "add", or "" when this is not that page."""
    try:
        tiles = [t for t in driver.find_elements(By.CSS_SELECTOR, _SEL_SWITCH_TILES) if t.is_displayed()]
    except Exception:
        return ""
    if not tiles:
        return ""
    want = (email or "").strip().lower()
    for tile in tiles:
        try:
            if want and want in (tile.text or "").lower():
                tile.click()
                time.sleep(random.uniform(2.5, 4.0))
                return "account"
        except Exception:
            continue
    try:
        driver.find_element(By.CSS_SELECTOR, _SEL_SWITCH_ADD).click()
        time.sleep(random.uniform(2.5, 4.0))
        return "add"
    except Exception:
        return ""


def _shown(driver, selectors):
    """Is any of these on the page and visible right now? No waiting."""
    for sel in selectors:
        try:
            if any(e.is_displayed() for e in driver.find_elements(By.CSS_SELECTOR, sel)):
                return True
        except Exception:
            continue
    return False


def _where(driver):
    """Which step of Amazon's sign-in the browser is on:
    'in' (signed in), 'reset' (Amazon demands a new password), 'otp', 'password', 'switch'
    (the remembered-accounts list), 'email', or '' while the page is still loading or is unknown.

    "The URL is not a sign-in URL" is NOT signed in. On 10 Nov 2026 pressing Hathoro's account led to
    /ap/forgotpassword/reverification ("Password reset required"); the old test called that a
    success, and the placer went straight back to the sign-in page. Signed in means: on Seller
    Central and outside every /ap/ page."""
    try:
        url = (driver.current_url or "").lower()
    except Exception:
        return ""
    if "ap/forgotpassword" in url:
        return "reset"
    if "sellercentral.amazon.com" in url and "/ap/" not in url:
        return "in"
    if "ap/mfa" in url or _shown(driver, [_SEL_OTP_FIELD]):
        return "otp"
    if _shown(driver, _SEL_PASSWORD_OPTS):
        return "password"
    if _shown(driver, [_SEL_SWITCH_TILES]):
        return "switch"
    if _shown(driver, _SEL_EMAIL_OPTS):
        return "email"
    return ""


def _settle(driver, seconds=12.0):
    """Wait until the page is one _where() knows, and say which. '' if it never becomes one.
    A headless Chrome needs a moment after a click or a load: on 10 Nov the remembered-accounts list
    appeared half a second after the first look, and that attempt spent 80 seconds on fields that
    were never going to be there."""
    end = time.time() + seconds
    while True:
        state = _where(driver)
        if state or time.time() >= end:
            return state
        time.sleep(0.5)


def _reset_required(account_key):
    global last_problem
    last_problem = (f"Amazon requires a NEW PASSWORD for the {account_key} account (\"Password reset required\"). "
                    "Only a person can do that: Amazon sends a one-time code to the account's e-mail or mobile. "
                    f"Then save the new password with: python scripts/set_sc_credentials.py {account_key}")
    print(f"  🛑  {last_problem}")
    return False


def _page_says(driver):
    """The page's own heading, for the log — so a sign-in page this code does not know names itself."""
    try:
        return (driver.execute_script(
            "var h = document.querySelector('h1, h2'); "
            "return ((h && h.innerText) || document.title || '').replace(/\\s+/g, ' ').trim().slice(0, 80);") or "")
    except Exception:
        return ""


def _move_on(driver, from_state, seconds):
    """Wait until Amazon has left the step `from_state` and say where it is now; `from_state` again
    when it never moved. After a click the old page stays up while Amazon answers — looking at once
    reads the page that was just submitted."""
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(0.5)
        state = _where(driver)
        if state and state != from_state:
            return state
    return _where(driver) or from_state


def _page_complaint(driver):
    """What the sign-in page itself says is wrong (a wrong password, a picture puzzle), for the log."""
    try:
        return (driver.execute_script("""
            var out = [];
            document.querySelectorAll('.a-alert-content, [role=alert], #auth-error-message-box, #auth-warning-message-box').forEach(function (e) {
                var r = e.getBoundingClientRect(), t = (e.innerText || '').replace(/\\s+/g, ' ').trim();
                if (r.width > 0 && r.height > 0 && t && out.indexOf(t) < 0) out.push(t);
            });
            if (document.querySelector('#auth-captcha-image, #captchacharacters, img[src*="captcha"], iframe[src*="captcha"]'))
                out.push('Amazon is showing a picture puzzle (CAPTCHA) — a person has to sign in once in this Chrome');
            return out.join(' | ').slice(0, 240);""") or "")
    except Exception:
        return ""


def _sign_in_once(driver, account_key, email, password, totp_secret):
    """One pass through Amazon's sign-in, taking each page as it comes. True when signed in.

    Amazon decides the order of the pages, not this code: the remembered-accounts list may lead to the
    password page, straight to the 2-step code, or to "Password reset required"; the e-mail and the
    password may share a page or not. So this looks where it is (_where), does that one step, waits
    for Amazon to move on (_move_on), and looks again. Written after 10 Nov 2026, when a fixed order
    of steps (e-mail → password → code) met the code page right after the account list and spent 40
    seconds looking for a password box, and a look taken too soon after "Sign in" read the page that
    had just been submitted and called it a failure.

    The password is typed at most once: the password page coming back means Amazon refused it, and
    typing it again only adds failed attempts — auto_login() then stops instead of trying again."""
    global last_problem
    password_sent = False
    deadline = time.time() + 120
    while time.time() < deadline:
        state = _settle(driver)
        if state == "in":
            return True
        if state == "reset":
            return _reset_required(account_key)

        if state == "otp":
            print("  🔐  2-step verification page — entering the code.")
            if not _submit_otp(driver, totp_secret):
                return False
            state = _move_on(driver, "otp", 20)
            if state == "otp":
                print(f"  ⚠️  Still on the 2-step page after the code. {_page_complaint(driver)}")
                return False
            continue

        if state == "switch":
            picked = _pick_remembered_account(driver, email)
            if not picked:
                print("  ⚠️  Amazon's 'Switch accounts' page is up but nothing on it could be pressed.")
                return False
            print("  🔐  Amazon showed its 'Switch accounts' page — "
                  + ("chose this store's account." if picked == "account" else "pressed 'Add account'."))
            if _move_on(driver, "switch", 15) == "switch":
                print("  ⚠️  The 'Switch accounts' page did not move on.")
                return False
            continue

        if state == "email":
            email_field = _find_with_fallbacks(driver, _SEL_EMAIL_OPTS, timeout=3)
            if not email_field:
                return False
            email_field.clear()
            _type_slowly(email_field, email)
            time.sleep(random.uniform(0.3, 0.6))
            # Only here: on the password page "#continue" is the "Sign in with a passkey" button.
            continue_btn = _find_with_fallbacks(driver, _SEL_CONTINUE_OPTS, timeout=3)
            if continue_btn and continue_btn.is_displayed() and "passkey" not in (continue_btn.text or "").lower():
                continue_btn.click()
            if _move_on(driver, "email", 12) == "email":
                print(f"  ⚠️  The e-mail page did not move on. {_page_complaint(driver)}")
                return False
            continue

        if state == "password":
            if password_sent:
                # Not tried a second time: the same password would only be refused again.
                last_problem = (f"Amazon did not accept the saved password for '{account_key}': "
                                f"{_page_complaint(driver) or 'the page gives no reason'}. If the password was changed, "
                                f"save it with: python scripts/set_sc_credentials.py {account_key}")
                print(f"  🛑  {last_problem}")
                return False
            # some sign-in pages carry the e-mail box and the password box together
            for sel in _SEL_EMAIL_OPTS:
                try:
                    boxes = [b for b in driver.find_elements(By.CSS_SELECTOR, sel) if b.is_displayed()]
                except Exception:
                    boxes = []
                if boxes:
                    if not (boxes[0].get_attribute("value") or "").strip():
                        _type_slowly(boxes[0], email)
                        time.sleep(random.uniform(0.3, 0.6))
                    break
            pw_field = _find_with_fallbacks(driver, _SEL_PASSWORD_OPTS, timeout=5)
            if not pw_field:
                print(f"  ⚠️  Password field not found. The page says: \"{_page_says(driver) or 'nothing readable'}\"")
                return False
            pw_field.clear()
            _type_slowly(pw_field, password)
            time.sleep(random.uniform(0.3, 0.6))
            btn = _find_with_fallbacks(driver, _SEL_SIGNIN_OPTS, timeout=5)
            if not btn:
                print("  ⚠️  Sign-in button not found.")
                return False
            btn.click()
            password_sent = True
            _move_on(driver, "password", 25)        # the old page stays up while Amazon answers
            continue

        print(f"  ⚠️  A sign-in page this code does not know. The page says: "
              f"\"{_page_says(driver) or 'nothing readable'}\" {_page_complaint(driver)}")
        return False
    print("  ⚠️  The sign-in took longer than two minutes.")
    return False


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
    global last_problem
    last_problem = ""
    if not is_login_page(driver):
        return True  # already logged in

    account_key = resolve_account_key(account_identifier)
    creds = _load_creds(account_key)
    if not creds:
        print(f"  ⚠️  Auto-login unavailable for '{account_key}'. Manual login required.")
        last_problem = f"no saved Seller Central login for '{account_key}'"
        return False

    email    = creds["email"]
    password = creds["password"]
    totp_secret = creds.get("totp_secret", "")

    for attempt in range(1, max_attempts + 1):
        print(f"  🔐  Auto-login attempt {attempt}/{max_attempts} for '{account_key}'...")
        try:
            if _sign_in_once(driver, account_key, email, password, totp_secret):
                print(f"  ✅  Auto-login successful for '{account_key}'.")
                return True
            if last_problem:                 # Amazon wants a new password: trying again changes nothing
                return False
        except Exception as e:
            print(f"  ❌  Login error on attempt {attempt}: {str(e).splitlines()[0] if str(e) else type(e).__name__}")

    print(f"  ❌  Auto-login failed after {max_attempts} attempts for '{account_key}'.")
    last_problem = last_problem or f"the automatic sign-in for '{account_key}' did not get through (page: {_page_says(driver) or 'unknown'})"
    return False


def on_account_switcher(driver):
    """Is Amazon showing the 'Select an account' page instead of what we asked for?"""
    try:
        return "account-switcher" in (driver.current_url or "").lower()
    except Exception:
        return False


_SWITCHER_PICK_JS = """
var want = (arguments[0] || '').trim().toLowerCase().replace(/\\s+/g, ' ');
var hits = [], all = [];
document.querySelectorAll('button').forEach(function (b) {
    var t = (b.innerText || '').trim().replace(/\\s+/g, ' ');
    if (!t) return;
    all.push(t.slice(0, 40));
    if (t.toLowerCase() === want) hits.push(b);
});
if (hits.length !== 1) return {ok: false, count: hits.length, seen: all.slice(0, 14)};
hits[0].click();
return {ok: true};
"""

# The switcher does not act on the tiles alone — store and marketplace are selections, and a
# "Select account" button commits them. Clicking only the tiles leaves the page exactly where
# it was, which is what made the first version click "United States" over and over.
_SWITCHER_COMMIT_JS = """
var btns = document.querySelectorAll('kat-button, button');
for (var i = 0; i < btns.length; i++) {
    var b = btns[i];
    var t = ((b.getAttribute('label') || '') + ' ' + (b.innerText || '')).trim().toLowerCase();
    if (t.indexOf('select account') === -1) continue;
    var inner = b.shadowRoot ? b.shadowRoot.querySelector('button') : null;
    (inner || b).click();
    return true;
}
return false;
"""


def _click_switcher(driver, want):
    try:
        return driver.execute_script(_SWITCHER_PICK_JS, want) or {}
    except Exception:
        return {}


def pick_account(driver, store_name, marketplace=None, target_url=None, timeout=25):
    """Get past Amazon's 'Select an account' page by choosing OUR store.

    A session that is signed in but has not chosen an account lands here instead of the page
    that was requested, and every selector after it then times out — the closing run on
    19 Sep died this way. Three steps, in order: pick the store, pick its marketplace, then
    press "Select account" to commit.

    Only an exact name match is clicked. These accounts sell different catalogues, so picking
    the wrong one would pull another store's 30-day report into closing; zero matches or more
    than one clicks nothing and says what the page offered.
    """
    if not store_name:
        return False
    if not on_account_switcher(driver):
        return True

    r = _click_switcher(driver, store_name)
    if not r.get("ok"):
        if r.get("count", 0) > 1:
            print(f"  [AUTH] ⛔ {r['count']} accounts are named {store_name!r} — refusing to guess.")
        else:
            print(f"  [AUTH] ⛔ No account named {store_name!r}. Offered: {r.get('seen')}")
        return False
    print(f"  [AUTH] Account switcher: {store_name}")
    time.sleep(1.5)

    if marketplace:
        if _click_switcher(driver, marketplace).get("ok"):
            print(f"  [AUTH] Account switcher: {marketplace}")
            time.sleep(1.0)

    try:
        committed = driver.execute_script(_SWITCHER_COMMIT_JS)
    except Exception:
        committed = False
    if not committed:
        print("  [AUTH] ⛔ Could not find the 'Select account' button to confirm with.")
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not on_account_switcher(driver):
            if target_url:
                driver.get(target_url)
                time.sleep(2)
            return not on_account_switcher(driver)
        time.sleep(1)
    print("  [AUTH] ⛔ Still on the account switcher after confirming.")
    return False


def choose_store_if_asked(driver, account_identifier, target_url=None):
    """If Amazon is showing 'Select an account', choose this store. True unless it stayed there.

    Takes whatever the caller knows the store by: a tracker's account_config, 'wrenchly' or
    'WR_RESELLER'. The page is not a login page, so the tracker and placers never handled it
    and stopped dead on it after a fresh sign-in (Wrenchly tracker, 9 Nov 2026 02:23).
    """
    if not on_account_switcher(driver):
        return True
    account_key = resolve_account_key(account_identifier)
    try:
        from utils.accounts_config import accounts, store_by_key
        cfg = accounts().get(store_by_key(account_key)) or {}
    except Exception:
        cfg = {}
    if pick_account(driver, cfg.get("sc_store_name"), cfg.get("sc_marketplace"), target_url=target_url):
        return True
    print(f"  [AUTH] ⛔ Could not choose the Seller Central account for '{account_key}'.")
    return False


def ensure_sc_session(driver, account_identifier, target_url=None, label=""):
    """Get this browser to a usable, signed-in Seller Central — login AND account switcher.

    Starting a browser is not the same as having a session in it. A headless Chrome presents
    itself as "HeadlessChrome", which Amazon treats as a different device, so the profile's
    cookie is refused and the tab lands on sign-in; and even once signed in, an account that
    has not been chosen yields the switcher instead of the page asked for. Anything that gets
    a driver from ensure_chrome and then drives Seller Central has to clear both, or it works
    on a login page and reports every order as "not available".

    Returns True when the browser is ready to use.
    """
    tag = f"[{label}] " if label else ""
    if target_url:
        try:
            driver.get(target_url)
            time.sleep(2)
        except Exception:
            pass

    if is_login_page(driver):
        print(f"  {tag}🔐 Not signed in — auto-logging in as {account_identifier}...")
        if not auto_login(driver, account_identifier):
            print(f"  {tag}⛔ Auto-login failed for {account_identifier}.")
            return False
        if target_url:
            driver.get(target_url)
            time.sleep(2)

    if on_account_switcher(driver):
        try:
            from utils.accounts_config import accounts as _accts
            cfg = (_accts() or {}).get(account_identifier, {})
        except Exception:
            cfg = {}
        if not pick_account(driver, cfg.get("sc_store_name"),
                            cfg.get("sc_marketplace"), target_url=target_url):
            return False

    if is_login_page(driver):            # bounced straight back — do not pretend otherwise
        print(f"  {tag}⛔ Still on the sign-in page after logging in.")
        return False
    return True


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
