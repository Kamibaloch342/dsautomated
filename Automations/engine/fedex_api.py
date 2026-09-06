"""
fedex_api.py — Bullet-Proof FedEx tracking engine.

Supports:
  1. Official FedEx Developer REST API (apis.fedex.com) — 100% reliable, zero bot-blocking, instant pure REST.
  2. Headless Chrome CDP batch tracking (wtrk/track in-browser fetch) — stealth headless execution.
  3. Resilient Headless DOM Scraper (wtrk/track DOM polling) — fallback for unresolved items.

Usage:
    from engine.fedex_api import FedExTracker
    tracker = FedExTracker()
    results = tracker.track_batch(["525110149347", "479141623818"])
    for r in results:
        print(r["status"], r["delivery_date"])
"""

from __future__ import annotations

import os
import re
import json
import time
import logging
from typing import Dict, List, Optional

import requests

try:
    from utils.accounts_config import fedex_api_credentials
except Exception:
    def fedex_api_credentials():
        return {
            "client_id": os.environ.get("FEDEX_API_KEY", "").strip(),
            "client_secret": os.environ.get("FEDEX_SECRET_KEY", "").strip(),
        }

logger = logging.getLogger("fedex_api")

# FedEx public client ID (used by fedex.com frontend)
_FEDEX_WEB_CLIENT_ID = "l7b8ada987a4544ff7a839c8e1f6548eea"
_FEDEX_CDP_BATCH_SIZE = 30
_FEDEX_COOKIE_REFRESH_INTERVAL = 30 * 60  # 30 minutes


class FedExSessionExpired(Exception):
    """Raised when FedEx cookies/session are no longer valid."""
    pass


class FedExTracker:
    """Bullet-proof multi-tier FedEx tracking engine."""

    def __init__(
        self,
        driver=None,
        client_id: str = None,
        api_key: str = None,
        secret_key: str = None,
        headless: bool = True,
    ):
        """
        driver: Optional existing Selenium WebDriver.
        api_key / secret_key: Optional official FedEx Developer Portal OAuth credentials.
        headless: If True, launches headless Chrome when a driver is needed.
        """
        self.driver = driver
        self.client_id = client_id or _FEDEX_WEB_CLIENT_ID
        self.headless = headless
        self._owns_driver = False

        # Check official API credentials
        creds = fedex_api_credentials()
        self.api_key = (api_key or os.environ.get("FEDEX_API_KEY") or creds.get("client_id", "")).strip()
        self.secret_key = (secret_key or os.environ.get("FEDEX_SECRET_KEY") or creds.get("client_secret", "")).strip()

        self._official_token: str = ""
        self._official_token_expiry: float = 0.0

        self._session: Optional[requests.Session] = None
        self._primed_at: float = 0.0

    @property
    def has_official_api(self) -> bool:
        """True if official FedEx Developer API credentials are configured."""
        return bool(self.api_key and self.secret_key)

    # ── Driver lifecycle ─────────────────────────────────────────────────────
    def _get_or_create_driver(self):
        """Ensure a WebDriver is available. Creates a stealth headless driver if needed."""
        if self.driver is not None:
            return self.driver

        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        opts = Options()
        if self.headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--start-maximized")
        opts.add_argument("--no-first-run")
        opts.add_argument("--no-default-browser-check")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument(
            "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
        )
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)

        system_chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        if os.path.exists(system_chrome):
            opts.binary_location = system_chrome

        profile_dir = os.path.join(os.path.expanduser("~"), ".fedex_headless_profile")
        try:
            from utils.helpers import mark_profile_clean_exit
            mark_profile_clean_exit(profile_dir)
        except Exception:
            pass
        opts.add_argument(f"--user-data-dir={profile_dir}")
        opts.add_argument("--hide-crash-restore-bubble")

        self.driver = webdriver.Chrome(options=opts)
        self._owns_driver = True

        try:
            from utils.helpers import apply_cdp_stealth
            apply_cdp_stealth(self.driver)
        except Exception:
            pass

        logger.info("[FedEx] Created dedicated %s Chrome driver", "headless" if self.headless else "headed")
        return self.driver

    def close(self):
        """Clean up any driver owned by this instance."""
        if self._owns_driver and self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
            self._owns_driver = False

    def __del__(self):
        self.close()

    # ── Tier 1: Official FedEx Developer REST API ───────────────────────────
    def _get_official_oauth_token(self) -> str:
        """Fetch/cache an OAuth Bearer token from official apis.fedex.com."""
        now = time.time()
        if self._official_token and now < (self._official_token_expiry - 60):
            return self._official_token

        url = "https://apis.fedex.com/oauth/token"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            "grant_type": "client_credentials",
            "client_id": self.api_key,
            "client_secret": self.secret_key,
        }
        resp = requests.post(url, headers=headers, data=data, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError(f"Official FedEx OAuth missing token: {payload}")
        expires_in = int(payload.get("expires_in", 3600))
        self._official_token = token
        self._official_token_expiry = now + expires_in
        logger.info("[FedEx] Official OAuth token refreshed (expires in %ds)", expires_in)
        return token

    def track_batch_via_official_api(self, tracking_ids: List[str]) -> List[dict]:
        """Track batch using official FedEx REST API (apis.fedex.com)."""
        if not tracking_ids:
            return []

        all_details: Dict[str, dict] = {}
        empty = self._empty_detail()

        token = self._get_official_oauth_token()
        url = "https://apis.fedex.com/track/v1/trackingnumbers"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

        for i in range(0, len(tracking_ids), _FEDEX_CDP_BATCH_SIZE):
            chunk = tracking_ids[i:i + _FEDEX_CDP_BATCH_SIZE]
            body = {
                "includeDetailedScans": True,
                "trackingInfo": [
                    {"trackingNumberInfo": {"trackingNumber": str(t).strip()}}
                    for t in chunk
                ],
            }
            try:
                resp = requests.post(url, json=body, headers=headers, timeout=20)
                resp.raise_for_status()
                data = resp.json()
                results_list = data.get("output", {}).get("completeTrackResults", [])
                for item in results_list:
                    tn = item.get("trackingNumber") or ""
                    track_results = item.get("trackResults", [])
                    if track_results:
                        all_details[tn] = self._parse_official_track_result(track_results[0])
            except Exception as e:
                logger.warning("[FedEx] Official REST API error on chunk %d-%d: %s", i + 1, i + len(chunk), e)
                for tid in chunk:
                    if tid not in all_details:
                        all_details[tid] = dict(empty)

        return [all_details.get(tid, dict(empty)) for tid in tracking_ids]

    @staticmethod
    def _parse_official_track_result(res: dict) -> dict:
        """Parse official FedEx track/v1 response trackResult."""
        empty = FedExTracker._empty_detail()
        detail = dict(empty)

        latest_status = res.get("latestStatusDetail", {}) or {}
        code = latest_status.get("code", "").upper()
        desc = latest_status.get("description", "") or latest_status.get("statusByLocale", "")

        if code == "DL" or "delivered" in desc.lower():
            status = "Delivered"
        elif code == "IT" or "in transit" in desc.lower():
            status = "InTransit"
        elif code == "OD" or "out for delivery" in desc.lower():
            status = "OutForDelivery"
        elif code in ("OC", "PX", "LP") or "label created" in desc.lower():
            status = "LabelCreated"
        elif code == "PU" or "picked up" in desc.lower():
            status = "PickedUp"
        elif desc:
            status = desc
        else:
            status = "Unknown"
        detail["status"] = status

        # Dates & times
        date_and_times = res.get("dateAndTimes", []) or []
        for dt in date_and_times:
            dt_type = dt.get("type", "").upper()
            dt_val = dt.get("dateTime", "")
            if dt_type == "ACTUAL_DELIVERY" and dt_val:
                detail["delivery_date"] = dt_val.replace("T", " ")[:16]
            elif dt_type == "ESTIMATED_DELIVERY" and dt_val:
                detail["estimated_window"] = dt_val.replace("T", " ")[:16]

        # Delivery details (signed by, left at)
        del_details = res.get("deliveryDetails", {}) or {}
        detail["signed_by"] = del_details.get("signedByName") or del_details.get("receivedByName") or ""
        detail["left_at"] = del_details.get("deliveryLocationDescription") or del_details.get("deliveryAttemptsDescription") or ""

        # Scan events
        scans = res.get("scanEvents", []) or []
        if scans:
            latest = scans[0]
            loc = latest.get("scanLocation", {}) or {}
            city = loc.get("city", "")
            state = loc.get("stateOrProvinceCode", "")
            detail["last_scan_location"] = f"{city}, {state}".strip(", ")
            detail["last_scan_time"] = (latest.get("date") or "").replace("T", " ")[:16]
            detail["delivery_note"] = latest.get("eventDescription") or ""

        # Addresses
        shipper = res.get("shipperInformation", {}).get("address", {}) or {}
        recipient = res.get("recipientInformation", {}).get("address", {}) or {}
        detail["from_loc"] = f"{shipper.get('city', '')}, {shipper.get('stateOrProvinceCode', '')}".strip(", ")
        detail["to_loc"] = f"{recipient.get('city', '')}, {recipient.get('stateOrProvinceCode', '')}".strip(", ")

        return detail

    # ── Session management (for legacy requests & browser sync) ──────────────
    def prime_session(self, driver=None) -> requests.Session:
        """Extract cookies from a Chrome driver and build a requests session."""
        d = driver or self.driver or self._get_or_create_driver()
        cookies = self._extract_cookies(d)
        self._session = self._build_session(cookies)
        self._primed_at = time.time()
        logger.info("[FedEx] Session primed with %d cookies", len(cookies))
        return self._session

    def _extract_cookies(self, driver) -> dict:
        cookies = driver.get_cookies()
        result = {}
        for c in cookies:
            domain = c.get("domain", "")
            if "fedex.com" in domain:
                result[c["name"]] = c["value"]
        return result

    def _build_session(self, cookies: dict) -> requests.Session:
        session = requests.Session()
        for name, value in cookies.items():
            session.cookies.set(name, value, domain=".fedex.com")
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.fedex.com",
            "Referer": "https://www.fedex.com/wtrk/track/",
        })
        return session

    def _is_stale(self) -> bool:
        if self._session is None:
            return True
        return (time.time() - self._primed_at) > _FEDEX_COOKIE_REFRESH_INTERVAL

    # ── Tier 3: In-browser CDP API fetch (Stealth Headless) ───────────────────
    _CDP_FETCH_JS = r"""
    const cb = arguments[arguments.length - 1];
    const clientId = arguments[0];
    const ids = arguments[1];
    (async () => {
      let done = false;
      const safeCb = (val) => {
        if (!done) { done = true; try { cb(val); } catch (e) {} }
      };
      // 5-second hard safety timeout
      setTimeout(() => safeCb(JSON.stringify({ok: false, status: -2, error: "CDP fetch timeout"})), 5000);

      try {
        let bearer = clientId;
        try {
          const tokRes = await fetch("https://api.fedex.com/auth/oauth/v2/token", {
            method: "POST",
            headers: {"Content-Type": "application/x-www-form-urlencoded"},
            credentials: "include",
            body: "client_id=" + encodeURIComponent(clientId) + "&grant_type=client_credentials&scope=oob"
          });
          const t = await tokRes.json();
          if (t && t.access_token) bearer = t.access_token;
        } catch (e) {}

        const body = {
          appDeviceType: "WTRK",
          appType: "WTRK",
          supportHTML: true,
          supportCurrentLocation: true,
          trackingInfo: ids.map(t => ({trackNumberInfo: {trackingCarrier: "", trackingNumber: String(t).trim(), trackingQualifier: ""}})),
          uniqueKey: "",
          guestAuthenticationToken: ""
        };

        const trkRes = await fetch("https://api.fedex.com/track/v2/shipments", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer " + bearer,
            "X-Requested-With": "XMLHttpRequest",
            "X-clientid": "WTRK",
            "X-locale": "en_US",
            "X-version": "1.0.0"
          },
          credentials: "include",
          body: JSON.stringify(body)
        });
        const text = await trkRes.text();
        safeCb(JSON.stringify({ok: trkRes.ok, status: trkRes.status, body: text}));
      } catch (e) {
        safeCb(JSON.stringify({ok: false, status: -1, error: String(e)}));
      }
    })();
    """

    def track_batch_via_cdp(self, tracking_ids: List[str]) -> List[dict]:
        """
        Track via an in-browser fetch inside stealth Chrome.
        Bypasses Akamai bot detection by reusing the browser TLS & session state.
        """
        if not tracking_ids:
            return []

        d = self._get_or_create_driver()

        try:
            cur = d.current_url or ""
        except Exception:
            cur = ""
        if "fedex.com" not in cur:
            try:
                d.get("https://www.fedex.com/wtrk/track/")
                time.sleep(2)
            except Exception as e:
                logger.warning("[FedEx] CDP nav to wtrk/track failed: %s", e)

        try:
            d.set_script_timeout(10)
        except Exception:
            pass

        all_details: Dict[str, dict] = {}
        empty = self._empty_detail()

        for idx, i in enumerate(range(0, len(tracking_ids), _FEDEX_CDP_BATCH_SIZE)):
            if idx and idx % 8 == 0:
                try:
                    d.get("https://www.fedex.com/wtrk/track/")
                    time.sleep(3)
                except Exception:
                    pass
            chunk = tracking_ids[i:i + _FEDEX_CDP_BATCH_SIZE]
            logger.info("[FedEx] CDP fetch batch %d-%d of %d", i + 1, i + len(chunk), len(tracking_ids))
            try:
                raw = d.execute_async_script(self._CDP_FETCH_JS, self.client_id, chunk)
                payload = json.loads(raw) if raw else {}
            except Exception as e:
                logger.warning("[FedEx] CDP fetch error: %s", e)
                for tid in chunk:
                    all_details[tid] = dict(empty)
                continue

            if not payload.get("ok"):
                logger.warning("[FedEx] CDP track blocked (status=%s) — chunk falls back", payload.get("status"))
                for tid in chunk:
                    all_details[tid] = dict(empty)
                continue

            try:
                result = json.loads(payload.get("body") or "{}")
            except Exception:
                result = {}

            packages = (result or {}).get("output", {}).get("packages", [])
            pkg_map: Dict[str, dict] = {}
            for pkg in packages:
                tn = pkg.get("trackingNbr", "")
                if tn:
                    pkg_map[tn] = self._parse_package(pkg)
            for tid in chunk:
                all_details[tid] = pkg_map.get(tid, dict(empty))

        return [all_details.get(tid, dict(empty)) for tid in tracking_ids]

    # ── Tier 4: Resilient Headless DOM Scraper ──────────────────────────────
    _STATUS_WORDS = (
        "Delivered", "In transit", "Out for delivery", "Label created",
        "Arrived at", "Shipment exception", "Picked up", "On FedEx vehicle",
        "Departed FedEx", "At destination", "Delivery exception"
    )

    def track_batch_via_browser(
        self,
        tracking_ids: List[str],
        page_wait: float = 6.0,
        retry_unknown: bool = True
    ) -> List[dict]:
        """
        Fallback DOM scraper navigating directly to /wtrk/track/?trknbr={tid}.
        Extracts delivery status, dates, and signature from rendered DOM.
        """
        if not tracking_ids:
            return []

        d = self._get_or_create_driver()
        all_details: Dict[str, dict] = {}
        empty = self._empty_detail()

        def _scrape_one(tid: str, wait: float) -> dict:
            url = f"https://www.fedex.com/wtrk/track/?trknbr={tid}"
            try:
                d.get(url)
                body = self._wait_for_status(d, tid, wait)
                return self._parse_browser_body(body, tid)
            except Exception as e:
                logger.warning("[FedEx] Browser scrape failed for %s: %s", tid, e)
                return dict(empty)

        for tid in tracking_ids:
            logger.info("[FedEx] Browser scrape: %s", tid)
            all_details[tid] = _scrape_one(tid, page_wait)

        if retry_unknown:
            stragglers = [t for t in tracking_ids if all_details.get(t, {}).get("status") in ("", "Unknown")]
            if stragglers:
                logger.info("[FedEx] Retrying %d Unknown via browser (longer wait)", len(stragglers))
                for tid in stragglers:
                    redo = _scrape_one(tid, page_wait + 4.0)
                    if redo.get("status") not in ("", "Unknown"):
                        all_details[tid] = redo

        return [all_details.get(tid, dict(empty)) for tid in tracking_ids]

    @staticmethod
    def _wait_for_status(driver, tracking_id: str, max_wait: float) -> str:
        """Poll the page body until a status keyword appears or max_wait elapses."""
        from selenium.webdriver.common.by import By
        deadline = time.time() + max_wait
        body = ""
        while time.time() < deadline:
            time.sleep(0.8)
            try:
                body = driver.find_element(By.TAG_NAME, "body").text
            except Exception:
                continue
            if any(w.lower() in body.lower() for w in FedExTracker._STATUS_WORDS):
                return body
        return body

    @staticmethod
    def _parse_browser_body(body: str, tracking_id: str) -> dict:
        """Parse rendered FedEx tracking page body text into our detail schema."""
        empty = FedExTracker._empty_detail()
        detail = dict(empty)
        detail["status"] = "Unknown"

        if not body:
            return detail

        # Status matching
        status_match = re.search(
            r"\b(Delivered|In transit|Out for delivery|Label created|Arrived at|At destination|Shipment exception|Delivery exception|Picked up|On FedEx vehicle for delivery)\b",
            body,
            re.IGNORECASE
        )
        if status_match:
            status = status_match.group(1).capitalize()
            low = status.lower()
            if low == "in transit":
                status = "InTransit"
            elif low in ("out for delivery", "on fedex vehicle for delivery"):
                status = "OutForDelivery"
            elif low == "label created":
                status = "LabelCreated"
            elif "exception" in low:
                status = "ShipmentException"
            detail["status"] = status

        # Delivery date / time - e.g. "Tuesday, 8/30/2026 at 2:20 pm" or "8/30/2026 2:20 PM"
        dt_match = re.search(r"([A-Za-z]+,\s+\d{1,2}/\d{1,2}/\d{4})\s+at\s+(\d{1,2}:\d{2}\s+[ap]m)", body, re.IGNORECASE)
        if dt_match:
            detail["delivery_date"] = f"{dt_match.group(1)} {dt_match.group(2)}"
        else:
            dt_match2 = re.search(r"(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}\s+[AP]M)", body, re.IGNORECASE)
            if dt_match2:
                detail["delivery_date"] = dt_match2.group(1)

        # Left at
        left_match = re.search(r"Left at\s*[:\-]?\s*(.+?)(?:\n|Signed for by:|$)", body, re.IGNORECASE)
        if left_match:
            detail["left_at"] = left_match.group(1).strip()

        # Signed for by
        sign_match = re.search(r"Signed for by\s*[:\-]?\s*(.+?)(?:\n|$)", body, re.IGNORECASE)
        if sign_match:
            detail["signed_by"] = sign_match.group(1).strip()

        # Origin / Destination
        from_match = re.search(r"From\s+([A-Za-z\s]+,\s*[A-Z]{2}\s+US)", body)
        to_match = re.search(r"To\s+([A-Za-z\s]+,\s*[A-Z]{2}\s+US)", body)
        if from_match:
            detail["from_loc"] = from_match.group(1).strip()
        if to_match:
            detail["to_loc"] = to_match.group(1).strip()

        return detail

    # ── Universal Master Tracking Method ─────────────────────────────────────
    def track_batch(self, tracking_ids: List[str]) -> List[dict]:
        """
        Main public interface. Automatically picks the optimal tracking tier:
          1. Official REST API (if credentials configured)
          2. Headless CDP Batch
          3. Headless Browser Scrape (fallback for stragglers)
        """
        if not tracking_ids:
            return []

        clean_ids = [str(t).strip() for t in tracking_ids if str(t).strip()]
        if not clean_ids:
            return []

        # Tier 1: Official FedEx REST API
        if self.has_official_api:
            logger.info("[FedEx] Tracking %d IDs via Official REST API", len(clean_ids))
            try:
                return self.track_batch_via_official_api(clean_ids)
            except Exception as e:
                logger.warning("[FedEx] Official REST API failed (%s) — falling back to headless CDP", e)

        # Tier 3: In-Browser Stealth CDP Batch
        logger.info("[FedEx] Tracking %d IDs via Headless CDP Batch", len(clean_ids))
        results = []
        try:
            results = self.track_batch_via_cdp(clean_ids)
        except Exception as e:
            logger.warning("[FedEx] Headless CDP batch failed (%s)", e)
            results = [self._empty_detail() for _ in clean_ids]

        # Tier 4: Fallback DOM scrape for any still-unknown IDs
        unresolved_indices = [
            idx for idx, r in enumerate(results)
            if r.get("status") in ("", "Unknown")
        ]
        if unresolved_indices:
            unresolved_ids = [clean_ids[i] for i in unresolved_indices]
            logger.info("[FedEx] %d IDs unresolved after CDP — running fallback DOM scrape", len(unresolved_ids))
            try:
                scraped = self.track_batch_via_browser(unresolved_ids)
                for idx, item in zip(unresolved_indices, scraped):
                    if item.get("status") not in ("", "Unknown"):
                        results[idx] = item
            except Exception as e:
                logger.warning("[FedEx] DOM scrape fallback error: %s", e)

        return results

    def track_single(self, tracking_id: str) -> dict:
        """Track a single tracking ID."""
        results = self.track_batch([tracking_id])
        return results[0] if results else self._empty_detail()

    # ── Parsing helper for CDP v2 packages ──────────────────────────────────
    @staticmethod
    def _parse_package(pkg: dict) -> dict:
        """Parse a single FedEx v2 API package dict into our detail schema."""
        key_status = pkg.get("keyStatus", "")
        status_cd = pkg.get("keyStatusCD", "")

        if status_cd == "DL" or key_status == "Delivered":
            status = "Delivered"
        elif key_status in ("In transit", "In Transit"):
            status = "InTransit"
        elif "Out for Delivery" in key_status:
            status = "OutForDelivery"
        elif key_status == "Label created":
            status = "LabelCreated"
        elif key_status:
            status = key_status
        else:
            status = "Unknown"

        delivery_date = pkg.get("displayActDeliveryDt", "") or ""
        delivery_time = pkg.get("displayActDeliveryTm", "") or ""
        if delivery_date and delivery_time:
            delivery_date = f"{delivery_date} {delivery_time}"

        est_date = pkg.get("displayEstDeliveryDt", "") or ""
        est_time = pkg.get("displayEstDeliveryTm", "") or ""
        estimated_window = f"{est_date} {est_time}".strip() if est_date else ""

        shipper = pkg.get("shipperAddress") or pkg.get("originAddress") or {}
        recipient = pkg.get("recipientAddress") or pkg.get("destAddress") or {}
        from_loc = f"{shipper.get('city', '')}, {shipper.get('stateOrProvinceCode', '')}".strip(", ")
        to_loc = f"{recipient.get('city', '')}, {recipient.get('stateOrProvinceCode', '')}".strip(", ")

        scans = pkg.get("scanEventList", [])
        latest_scan = scans[0] if scans else {}
        last_scan_location = latest_scan.get("scanLocation", "")
        last_scan_time = ""
        if latest_scan.get("date") and latest_scan.get("time"):
            last_scan_time = f"{latest_scan['date']} {latest_scan['time']}"

        signed_by = pkg.get("recipientSignName", "") or pkg.get("receivedByNm", "") or ""
        scan_details = latest_scan.get("scanDetails", "") or ""
        sub_status = pkg.get("subStatus", "") or ""
        left_at = ""
        if "left at" in scan_details.lower():
            left_at = re.sub(r'\s*Signature Service not requested\.?', '', scan_details).strip().rstrip('.')
            if left_at.endswith('.'):
                left_at = left_at[:-1].strip()
        elif "left at" in sub_status.lower():
            left_at = sub_status.strip()

        return {
            "status": status,
            "delivery_date": delivery_date,
            "estimated_window": estimated_window,
            "last_scan_location": last_scan_location,
            "last_scan_time": last_scan_time,
            "signed_by": signed_by,
            "left_at": left_at,
            "delivery_note": scan_details,
            "from_loc": from_loc,
            "to_loc": to_loc,
        }

    @staticmethod
    def _empty_detail() -> dict:
        return {
            "status": "Unknown",
            "delivery_date": "",
            "estimated_window": "",
            "last_scan_location": "",
            "last_scan_time": "",
            "signed_by": "",
            "left_at": "",
            "delivery_note": "",
            "from_loc": "",
            "to_loc": "",
        }
