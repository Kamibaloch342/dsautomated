"""
utils.py — Shared utility functions for the HOANK STORE Unified Tracker.

Provides:
  - Gmail OAuth2 authentication with auto-refresh
  - Paginated Gmail message search
  - Email body extraction (recursive HTML decoder)
  - HTML cleaning
  - Selenium Chrome driver setup with anti-detection
  - Tracking ID extraction from text & PDFs
"""

from __future__ import annotations

import os
import io
import re
import sys
import base64
import html
import time
import subprocess
import logging

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

import pdfplumber

from config import (
    MASTER_CREDENTIALS_FILE,
    TOKEN_DIRECTORY,
    GMAIL_SCOPES,
    CHROME_PROFILE_PATH,
    CHROME_DEBUGGING_PORT,
    CHROME_BINARY_PATH,
    TRACKING_ID_REGEX,
    UC_PROFILE_DIR,
    DOWNLOAD_DIRECTORY,
)

logger = logging.getLogger("unified_tracker")

# ═══════════════════════════════════════════════════════════════════════════
# Gmail helpers
# ═══════════════════════════════════════════════════════════════════════════

def get_gmail_service(account_name: str):
    """
    Authenticate a Gmail account and return an API service object.
    Token files live in TOKEN_DIRECTORY as ``<account_name>_token.json``.
    """
    token_path = os.path.join(TOKEN_DIRECTORY, f"{account_name}_token.json")
    creds_path = str(MASTER_CREDENTIALS_FILE)

    if not os.path.exists(creds_path):
        raise FileNotFoundError(f"Master credentials file not found: {creds_path}")

    creds = None
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, GMAIL_SCOPES)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired token for %s", account_name)
            try:
                creds.refresh(Request())
                with open(token_path, "w") as f:
                    f.write(creds.to_json())
            except Exception as e:
                logger.warning("Token refresh failed for %s: %s. Skipping without stopping.", account_name, e)
                return None
        else:
            logger.warning("No valid token for %s — skipping account without stopping.", account_name)
            return None

    try:
        return build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception as e:
        logger.warning("Failed to build Gmail service for %s: %s", account_name, e)
        return None


def search_gmail(service, query: str, max_results: int | None = None):
    """
    Paginated Gmail message search.  Returns a list of ``{'id': ..., 'threadId': ...}`` dicts.
    """
    all_messages = []
    page_token = None
    while True:
        resp = (
            service.users()
            .messages()
            .list(userId="me", q=query, pageToken=page_token, maxResults=500)
            .execute()
        )
        messages = resp.get("messages", [])
        all_messages.extend(messages)
        if max_results and len(all_messages) >= max_results:
            return all_messages[:max_results]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return all_messages


def get_full_message(service, msg_id: str):
    """Fetch full message payload from Gmail."""
    return service.users().messages().get(userId="me", id=msg_id).execute()


def _draw_progress(current: int, total: int, label: str = "", width: int = 40):
    """Print an in-place ASCII progress bar to stdout."""
    if total <= 0:
        return
    pct = current / total
    filled = int(width * pct)
    bar = "█" * filled + "░" * (width - filled)
    print(f"\r{label}[{bar}] {pct * 100:5.1f}% ({current:,}/{total:,})", end="", flush=True)


def batch_get_messages(
    service,
    message_ids: list[str],
    batch_size: int = 20,
    progress_label: str = "",
    show_progress: bool = True,
    fallback_individual: bool = True,
) -> list[dict]:
    """
    Fetch full message payloads in batches using Gmail's batch HTTP request.
    Cuts API round-trips by ~20× compared to individual get() calls.
    Automatically retries on 429 rate-limit with exponential backoff.
    Falls back to individual fetches for messages that fail repeatedly in batch.
    Shows a live progress bar for fetches > 50 messages.
    """
    from googleapiclient.http import BatchHttpRequest
    from googleapiclient.errors import HttpError
    import time
    results: dict[str, dict] = {}
    failed_ids: list[str] = []

    def _callback(request_id, response, exception):
        if exception is None and response:
            results[response.get("id", request_id)] = response
        elif exception:
            failed_ids.append(request_id)

    total = len(message_ids)
    show_bar = show_progress and total > 50 and progress_label
    permanently_failed: list[str] = []

    for i in range(0, total, batch_size):
        chunk = message_ids[i:i + batch_size]
        for attempt in range(1, 4):
            batch_failed: list[str] = []
            failed_ids = batch_failed  # reset per-batch
            batch = service.new_batch_http_request(callback=_callback)
            for msg_id in chunk:
                batch.add(service.users().messages().get(userId="me", id=msg_id))
            try:
                batch.execute()
                if batch_failed:
                    if attempt < 3:
                        logger.debug("[GmailBatch] Batch %d-%d: %d/%d failed, retrying…",
                                     i + 1, min(i + batch_size, total), len(batch_failed), len(chunk))
                        chunk = batch_failed
                        time.sleep(2 ** attempt)
                        continue
                    else:
                        permanently_failed.extend(batch_failed)
                break  # success (or exhausted retries)
            except HttpError as e:
                if e.resp.status == 429:
                    wait = 2 ** attempt + 1
                    logger.warning("[GmailBatch] 429 rate limit — retrying batch %d-%d in %ds (attempt %d/3)",
                                   i + 1, min(i + batch_size, total), wait, attempt)
                    time.sleep(wait)
                else:
                    logger.warning("[GmailBatch] Batch %d-%d failed: %s", i + 1, min(i + batch_size, total), e)
                    permanently_failed.extend(chunk)
                    break
        if show_bar:
            _draw_progress(min(i + batch_size, total), total, progress_label)
        time.sleep(0.3)

    # Fallback: try individually fetching any permanently failed messages
    if fallback_individual and permanently_failed:
        logger.info("[GmailBatch] Attempting individual fallback for %d failed messages…", len(permanently_failed))
        for msg_id in permanently_failed:
            try:
                resp = service.users().messages().get(userId="me", id=msg_id).execute()
                results[resp.get("id", msg_id)] = resp
                time.sleep(0.2)
            except Exception as e:
                logger.debug("[GmailBatch] Individual fallback failed for %s: %s", msg_id, e)

    if show_bar:
        print()  # newline after progress bar
    logger.info("[GmailBatch] Fetched %d/%d messages", len(results), total)
    return list(results.values())


def get_email_body_html(payload: dict) -> str:
    """
    Recursively extract HTML body from a Gmail message payload.
    Falls back to plaintext if HTML is not available.
    """
    if payload.get("mimeType") == "text/html":
        data = payload.get("body", {}).get("data")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

    for part in payload.get("parts", []):
        result = get_email_body_html(part)
        if result:
            return result

    # Fallback: top-level body
    data = payload.get("body", {}).get("data")
    if data:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

    return ""


def clean_html(raw_html: str) -> str:
    """Strip HTML tags, decode entities, collapse whitespace."""
    clean = re.sub(r"<(style|script).*?</\1>", "", raw_html, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r"<[^>]+>", " ", clean)
    clean = html.unescape(clean)
    return re.sub(r"\s+", " ", clean).strip()


def extract_with_regex(pattern: str, text: str, group: int = 1) -> str:
    """Extract a single regex match group; returns '' if no match."""
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return match.group(group).strip().replace(",", "")


def clean_currency(value) -> float:
    """Convert a currency string like '$1,234.56' or 'USD 100.00' to float."""
    if value is None:
        return 0.0
    s = str(value)
    s = re.sub(r"[^\d.\-]", "", s)
    try:
        return float(s)
    except ValueError:
        return 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Tracking‐ID extraction
# ═══════════════════════════════════════════════════════════════════════════

def extract_tracking_ids_from_text(text: str) -> set[str]:
    """
    Extract 12-digit tracking numbers from a text blob.
    Prefers the block between 'Tracking Number' and 'Items in this Shipment'.
    """
    tracking_ids: set[str] = set()

    block_match = re.search(
        r"Tracking Number(?:s)?:?\s*(.*?)Items in this Shipment",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    search_text = block_match.group(1) if block_match else text

    for tid in re.findall(TRACKING_ID_REGEX, search_text):
        tracking_ids.add(tid)
    return tracking_ids


def extract_tracking_ids_from_pdf(file_data: bytes) -> set[str]:
    """Extract 12-digit tracking numbers from PDF binary data."""
    tracking_ids: set[str] = set()
    try:
        with pdfplumber.open(io.BytesIO(file_data)) as pdf:
            full_text = ""
            for page in pdf.pages:
                full_text += page.extract_text() or ""
            if re.search(r"Tracking Number", full_text, re.IGNORECASE):
                for tid in re.findall(TRACKING_ID_REGEX, full_text):
                    tracking_ids.add(tid)
    except Exception as e:
        logger.warning("Could not parse PDF: %s", e)
    return tracking_ids


# ═══════════════════════════════════════════════════════════════════════════
# Selenium helpers  (undetected_chromedriver — no profile conflicts)
# ═══════════════════════════════════════════════════════════════════════════

def get_report_driver(download_dir: str | None = None, headless: bool = False, profile_dir: str | None = None, uc_port: int | None = None) -> "webdriver.Chrome":
    """
    Create a fresh undetected_chromedriver for Amazon report downloads.

    Key features:
      - user_data_dir passed as uc.Chrome() parameter (NOT as --user-data-dir
        argument) so UC manages profile locking correctly — no crashes.
      - Persistent profile so login session survives across runs.
      - undetected_chromedriver patches navigator.webdriver automatically.
      - headless=True forces headless mode on any platform (macOS or Linux).
      - profile_dir overrides the default UC_PROFILE_DIR (for multi-account).
      - uc_port forces a fixed remote-debugging-port (for multi-account).
    """
    import undetected_chromedriver as uc

    dl_dir = download_dir or str(DOWNLOAD_DIRECTORY)
    os.makedirs(dl_dir, exist_ok=True)
    profile_dir = profile_dir or str(UC_PROFILE_DIR)
    os.makedirs(profile_dir, exist_ok=True)
    use_standard_chrome = sys.platform == "darwin" and hasattr(os, "uname") and os.uname().machine == "arm64"

    options = uc.ChromeOptions()
    options.binary_location = CHROME_BINARY_PATH
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--window-size=1400,900")
    options.add_argument("--start-maximized")
    if uc_port:
        options.add_argument(f"--remote-debugging-port={uc_port}")

    # Download directory via prefs
    prefs = {
        "download.default_directory": dl_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
    }
    options.add_experimental_option("prefs", prefs)

    # Headless: forced explicitly OR auto-enabled on Linux (no DISPLAY)
    _force_headless = headless or (os.environ.get("DISPLAY") is None and not os.path.exists("/Applications"))
    if _force_headless:
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        logger.info("Running Chrome in HEADLESS mode")
    else:
        logger.info("Running Chrome in HEADED (visible) mode")

    # Detect Chrome version to avoid chromedriver mismatch
    chrome_ver = None
    try:
        import subprocess as _sp
        _out = _sp.check_output(
            [CHROME_BINARY_PATH, "--version"], stderr=_sp.DEVNULL, text=True
        )
        chrome_ver = int(_out.strip().split()[-1].split(".")[0])
        logger.info("Detected Chrome version: %s", chrome_ver)
    except Exception:
        logger.info("Could not detect Chrome version — letting UC auto-detect")

    if use_standard_chrome:
        logger.info("Apple Silicon detected — using standard Selenium Chrome for report downloads.")
        fallback_options = ChromeOptions()
        fallback_options.binary_location = CHROME_BINARY_PATH
        fallback_options.add_argument("--no-first-run")
        fallback_options.add_argument("--no-default-browser-check")
        fallback_options.add_argument("--disable-popup-blocking")
        fallback_options.add_argument("--window-size=1400,900")
        fallback_options.add_argument("--start-maximized")
        if uc_port:
            fallback_options.add_argument(f"--remote-debugging-port={uc_port}")
        fallback_options.add_argument(f"--user-data-dir={profile_dir}")
        if _force_headless:
            fallback_options.add_argument("--headless=new")
            fallback_options.add_argument("--disable-gpu")
            fallback_options.add_argument("--no-sandbox")
            fallback_options.add_argument("--disable-dev-shm-usage")
        fallback_options.add_experimental_option("prefs", prefs)
        driver = webdriver.Chrome(options=fallback_options)

        try:
            driver.execute_cdp_cmd(
                "Browser.setDownloadBehavior",
                {"behavior": "allow", "downloadPath": dl_dir},
            )
        except Exception:
            pass

        logger.info("Chrome ready — window: %s", driver.title or "(loading)")
        return driver

    # CRITICAL: pass user_data_dir as a parameter to uc.Chrome(), NOT as
    # an --argument. UC needs to manage the SingletonLock itself to avoid crashes.
    logger.info("Launching undetected Chrome (profile: %s)...", profile_dir)
    # Only pass headless=True when explicitly needed — passing headless=False
    # to uc.Chrome() in newer UC versions can still trigger headless behavior.
    chrome_kwargs = dict(
        options=options,
        user_data_dir=profile_dir,
        version_main=chrome_ver,
    )
    if _force_headless:
        chrome_kwargs["headless"] = True
    try:
        driver = uc.Chrome(**chrome_kwargs)
    except OSError as exc:
        if getattr(exc, "errno", None) != 86 and "Bad CPU type" not in str(exc):
            raise

        logger.warning(
            "UC driver is incompatible with this Mac CPU; falling back to standard Selenium Chrome."
        )

        fallback_options = ChromeOptions()
        fallback_options.binary_location = CHROME_BINARY_PATH
        fallback_options.add_argument("--no-first-run")
        fallback_options.add_argument("--no-default-browser-check")
        fallback_options.add_argument("--disable-popup-blocking")
        fallback_options.add_argument("--window-size=1400,900")
        fallback_options.add_argument("--start-maximized")
        fallback_options.add_argument(f"--user-data-dir={profile_dir}")
        if _force_headless:
            fallback_options.add_argument("--headless=new")
            fallback_options.add_argument("--disable-gpu")
            fallback_options.add_argument("--no-sandbox")
            fallback_options.add_argument("--disable-dev-shm-usage")
        fallback_options.add_experimental_option("prefs", prefs)

        driver = webdriver.Chrome(options=fallback_options)

    # Force download path via CDP as well (belt + suspenders)
    try:
        driver.execute_cdp_cmd(
            "Browser.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": dl_dir},
        )
    except Exception:
        pass

    logger.info("Chrome ready — window: %s", driver.title or "(loading)")
    return driver


def kill_report_driver(driver):
    """Cleanly close undetected_chromedriver — no leftover processes."""
    try:
        driver.quit()
        logger.info("Chrome closed cleanly.")
    except Exception:
        pass
    # Belt + suspenders: kill any leftover chromedriver processes
    try:
        subprocess.run(["pkill", "-f", "chromedriver"], capture_output=True, timeout=5)
    except Exception:
        pass


# Legacy helpers kept for FedEx + Amazon order-dates scraping
def _kill_chrome_on_port(port: int):
    """Kill any Chrome process using the specified debugging port."""
    try:
        # Find process using the port
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True, timeout=5
        )
        pids = result.stdout.strip().split('\n')
        for pid in pids:
            if pid:
                subprocess.run(["kill", "-9", pid], capture_output=True, timeout=5)
                logger.info("Killed existing Chrome on port %s (PID %s)", port, pid)
    except Exception as e:
        logger.debug("Port cleanup (expected on first run): %s", e)


def _kill_chrome_with_profile(profile_path: str):
    """Kill any Chrome process using the specified profile directory."""
    try:
        # pkill any Chrome using this user-data-dir
        subprocess.run(
            ["pkill", "-f", f"user-data-dir={profile_path}"],
            capture_output=True, timeout=5
        )
        logger.info("Killed existing Chrome with profile: %s", os.path.basename(profile_path))
    except Exception:
        pass
    
    # Also try killing by profile folder name
    try:
        profile_name = os.path.basename(profile_path)
        subprocess.run(
            ["pkill", "-f", profile_name],
            capture_output=True, timeout=5
        )
    except Exception:
        pass


def _wait_for_chrome_ready(port: int, timeout: int = 15) -> bool:
    """Wait until Chrome's debugging port is ready to accept connections."""
    import socket
    start = time.time()
    while time.time() - start < timeout:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()
            if result == 0:
                logger.info("Chrome debugging port %s is ready", port)
                return True
        except Exception:
            pass
        time.sleep(0.5)
    logger.error("Chrome debugging port %s did not become ready in %ds", port, timeout)
    return False


def launch_chrome_with_debugging(
    profile_path: str | None = None,
    port: int | None = None,
    start_url: str | None = None,
) -> None:
    """
    Launch Chrome in remote-debugging mode.
    FIRST kills any existing Chrome using the same profile or port.
    Uses platform-appropriate Chrome binary from config.
    """
    p = profile_path or str(CHROME_PROFILE_PATH)
    pt = port or CHROME_DEBUGGING_PORT

    chrome_path = CHROME_BINARY_PATH  # Platform-agnostic from config
    
    # Check if Chrome binary exists
    if not os.path.exists(chrome_path):
        logger.error("Chrome binary not found at: %s", chrome_path)
        raise FileNotFoundError(f"Chrome not found at {chrome_path}")
    
    # ═══ FORCE QUIT existing Chrome using this profile/port ═══
    logger.info("Cleaning up any existing Chrome instances...")
    _kill_chrome_on_port(pt)
    _kill_chrome_with_profile(p)
    time.sleep(3)  # Give Chrome time to fully exit
    
    cmd = [
        chrome_path,
        f"--remote-debugging-port={pt}",
        f"--user-data-dir={p}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-client-side-phishing-detection",
        "--disable-hang-monitor",
        "--disable-popup-blocking",
        "--disable-prompt-on-repost",
        "--disable-sync",
        "--disable-translate",
        "--metrics-recording-only",
        "--safebrowsing-disable-auto-update",
    ]
    
    # Add start URL if provided, otherwise about:blank
    cmd.append(start_url or "about:blank")
    
    # On Linux without display, add headless flags
    if os.environ.get("DISPLAY") is None and not os.path.exists("/Applications"):
        cmd.extend([
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ])
        logger.info("Running Chrome in headless mode (no DISPLAY)")
    
    logger.info("Launching Chrome: %s", " ".join(cmd[:5]) + " ...")
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Wait for Chrome to be ready
    if not _wait_for_chrome_ready(pt, timeout=15):
        raise RuntimeError(f"Chrome failed to start on port {pt}")
    
    logger.info("Chrome launched on port %s with profile: %s", pt, os.path.basename(p))


def get_selenium_driver(
    profile_path: str | None = None,
    port: int | None = None,
    download_dir: str | None = None,
) -> webdriver.Chrome:
    """
    Connect Selenium to an already-running Chrome (remote debugging).
    Applies anti-detection patches.
    """
    pt = port or CHROME_DEBUGGING_PORT

    options = ChromeOptions()
    options.add_experimental_option("debuggerAddress", f"127.0.0.1:{pt}")

    if download_dir:
        prefs = {"download.default_directory": str(download_dir)}
        options.add_experimental_option("prefs", prefs)

    service = ChromeService(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    # Anti-detection
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.navigator.chrome = {runtime: {}};
            """
        },
    )

    # Force download directory via CDP — this works with remote debugging
    # (unlike the prefs approach which is ignored for already-running Chrome).
    if download_dir:
        driver.execute_cdp_cmd(
            "Browser.setDownloadBehavior",
            {
                "behavior": "allow",
                "downloadPath": str(download_dir),
            },
        )
        logger.info("CDP download path set to: %s", download_dir)

    return driver


def get_fresh_selenium_driver(
    profile_path: str | None = None,
    port: int | None = None,
    download_dir: str | None = None,
    start_url: str | None = None,
) -> webdriver.Chrome:
    """
    Convenience: launch Chrome + connect.
    """
    launch_chrome_with_debugging(profile_path, port, start_url=start_url)
    return get_selenium_driver(profile_path, port, download_dir)
