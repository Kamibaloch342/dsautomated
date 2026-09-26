"""
engine/zoro_tracking.py — delivery status from Zoro's own order tracking (27 Sep 2026).

Why: FedEx put its public tracking behind Akamai on 19 Sep 2026 and refused a production
Track API key to a non-shipper, so nothing could confirm a delivery. Zoro's guest "Track My
Order" page shows, for any WB/SO order plus the e-mail of the Zoro account that placed it,
the carrier scan history of every package — the same data FedEx used to give, served by
Zoro, on the user agent Zoro whitelisted for the reseller (no captcha, no limit).

The contract, captured from the page itself on 27 Sep 2026 (verified live on five orders):

  1. GET https://api.prod.zoro.com/v1/order/<ORDER>?email=<sha256 of the lower-cased e-mail>
     headers: apikey (Zoro's public front-end key — the one platforms/zoro.py has always
     sent), Accept json. 401 without the hash, 404 {"type":"orderNotFound"} for a wrong
     order/e-mail pair. Payload: orderNumber, salesOrder, email, createdAt, orderStatus,
     shippingAddress{…}, orderSummary{subTotal, freightTotal, shippingTotal, discountTotal,
     taxTotal, total, promoCode}, shippingSummary{shippingMethod, deliveries:[{fulfillDate,
     parcels:[{trackingNumber, carrier}], items:[{zoroProductId, quantity}]}]},
     lineItems:[{zoroProductId, name, quantityPurchased, total, expectedShipDate,
     latestExpectedDeliveryDate, lineItemStatus}]. Carrier codes seen: FDEG,
     "FDX GROUND", "FDX GROUND TMS".
  2. GET https://api.prod.zoro.com/customer/v1/shipment-tracking/package/<TRACKING>?carrier=<code>
     Payload: trackingNumber, carrier ("FedEx"), url, estimatedDeliveryDay,
     stepTimestamps:[{stepName:"shipped", timestamp}, {stepName:"delivered", timestamp?}],
     latestLocation{city, stateOrProvinceCode, …} | null, delayed,
     scanEventsDetails:[{date, eventDescription, scanLocation{city, stateOrProvinceCode,
     postalCode}, exceptionDescription}] (newest first). A "Delivered" scan carries the
     drop-off note in exceptionDescription ("Left at front door. Signature Service not
     requested."); a real exception reads e.g. "Unable to deliver - Damaged package".

Two transports: `http` (requests with the reseller user agent — the default, hundreds of
lookups a minute) and `browser` (a headless Chrome carrying the reseller identity performs
the same fetch from zoro.com — the placers' pattern), for the day the gateway starts
refusing plain HTTP. `auto` starts with http and switches after two refusals in a row.

A package is Delivered only when its delivered step has a timestamp or a delivered scan
exists; everything else keeps its latest scan, normalised into the vocabulary
engine/fedex_api.py uses, so labels, messaging and the dashboard need no new words.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from engine.fedex_api import STATUS_RANK, normalize_status

ZORO_ORDER_ENDPOINT = "https://api.prod.zoro.com/v1/order"
ZORO_PACKAGE_ENDPOINT = "https://api.prod.zoro.com/customer/v1/shipment-tracking/package"
ZORO_TRACK_PAGE = "https://www.zoro.com/customer-service/track-my-order/"
# Zoro's public front-end key (in every visitor's page bootstrap; platforms/zoro.py has sent
# it since the API checkout was built). The browser transport reads the live one from the
# page and only falls back to this constant.
ZORO_PUBLIC_API_KEY = "924526ffbdad25e5923b"

_ORDER_RX = re.compile(r"^(WB\d{10}|SO\d{8})$")
_TRACKING_KEYS = ("trackingNumber", "tracking_number", "trackingNo", "trackingId")
# Scan wordings that are not physical movement of the parcel.
_NO_MOVEMENT_RX = re.compile(r"information sent|label created|shipment information|order processed|"
                             r"picked up by carrier pending|electronic", re.I)
_DELIVERED_RX = re.compile(r"\bdelivered\b", re.I)
_EXCEPTION_RX = re.compile(r"exception|unable to deliver|delivery attempt|returned to shipper|"
                           r"return to sender|damaged|refused|held at", re.I)
_LEFT_AT_RX = re.compile(r"left at\s+([^.]*)", re.I)
_UNSUPPORTED_RX = re.compile(r"carrier not support|unsupported carrier", re.I)
_FEDEX_NUMBER_RX = re.compile(r"^\d{12}$|^\d{15}$|^\d{20}$|^\d{22}$")
NOT_INFORMATIVE = ("", "Unknown", "Untracked", "Blocked")
_SIGNED_RX = re.compile(r"signed for by[:\s]*([^.]+)", re.I)


def fmt_delivery(d: Optional[datetime]) -> str:
    """closing_orders.delivery_date keeps the FedEx page's wording — 'Wednesday, 6/03/2026 3:07 pm'
    — which closing's _parse_dt, messaging's _delivery_days_ago and the buyer's check-in all read."""
    if not d:
        return ""
    return f"{d:%A}, {d.month}/{d:%d}/{d:%Y} {(d.hour % 12) or 12}:{d:%M} {'am' if d.hour < 12 else 'pm'}"


def email_hash(email: str) -> str:
    """What the page sends instead of the address: sha256 of the trimmed, lower-cased e-mail."""
    return hashlib.sha256(str(email or "").strip().lower().encode("utf-8")).hexdigest()


class ZoroTrackingError(Exception):
    pass


class ZoroRefused(ZoroTrackingError):
    """The gateway is refusing this transport (403 / challenge page / no JSON)."""


# ── small helpers ─────────────────────────────────────────────────────────────
def parse_ts(v) -> Optional[datetime]:
    """ISO strings ('2026-08-27T11:43:29-05:00', '2026-08-24T00:00:00', '…Z'), epoch seconds
    or milliseconds → naive datetime holding the WALL-CLOCK time as the carrier states it
    (the scan's own zone, i.e. local to the parcel — what the buyer's "Delivered 11:43 AM"
    means and what FedEx used to show). The offset is dropped, never converted: closing
    compares dates, messaging counts days, and the buyer reads the time."""
    if v in (None, "", 0):
        return None
    try:
        if isinstance(v, (int, float)):
            n = float(v)
            if n > 1e12:
                n /= 1000.0
            return datetime.utcfromtimestamp(n)
        s = str(v).strip()
        if s.isdigit():
            return parse_ts(int(s))
        s2 = s.replace("Z", "+00:00")
        try:
            d = datetime.fromisoformat(s2)
        except ValueError:
            d = None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%m/%d/%Y %I:%M %p",
                        "%m/%d/%Y", "%b %d, %Y %I:%M %p", "%b %d, %Y", "%a, %b %d, %Y"):
                try:
                    d = datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
            if d is None:
                return None
        if d.tzinfo is not None:
            d = d.replace(tzinfo=None)
        return d
    except Exception:
        return None


def fmt_ts(d: Optional[datetime], seconds: bool = False) -> str:
    if not d:
        return ""
    if seconds:
        return d.strftime("%Y-%m-%d %H:%M:%S")
    return d.strftime("%Y-%m-%d %H:%M") if (d.hour or d.minute) else d.strftime("%Y-%m-%d")


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _first(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d.get(k)
    return default


def _loc_text(loc) -> str:
    """{'city': 'Niles', 'stateOrProvinceCode': 'IL', 'postalCode': '60714'} → 'Niles, IL 60714'."""
    if not loc:
        return ""
    if isinstance(loc, str):
        return loc.strip()
    if isinstance(loc, dict):
        city = _first(loc, "city", "cityName", default="") or ""
        state = _first(loc, "stateOrProvinceCode", "state", "stateProvince", "region", default="") or ""
        zipc = _first(loc, "postalCode", "zip", "zipCode", default="") or ""
        country = _first(loc, "countryCode", "country", default="") or ""
        s = ", ".join(x for x in (str(city).strip().title() if str(city).isupper() else str(city).strip(),
                                  f"{state} {zipc}".strip()) if x)
        if not s and country:
            s = str(country)
        return s.strip()
    return str(loc)


# ── parsing ───────────────────────────────────────────────────────────────────
def parse_package(t: dict, carrier_code: str = "") -> dict:
    """One shipment-tracking payload → normalised package record."""
    number = str(_first(t, *_TRACKING_KEYS, default="") or "").strip()
    carrier = str(_first(t, "carrier", "carrierName", default="") or "").strip()
    url = str(_first(t, "url", "trackingUrl", default="") or "").strip()
    steps = {}
    for s in (t.get("stepTimestamps") or []):
        if isinstance(s, dict):
            name = str(_first(s, "stepName", "name", "step", default="") or "").strip().lower()
            ts = parse_ts(_first(s, "timestamp", "date", "time"))
            if name and ts:
                steps[name] = ts
    events = []
    for e in (t.get("scanEventsDetails") or t.get("events") or t.get("scanEvents") or []):
        if not isinstance(e, dict):
            continue
        at = parse_ts(_first(e, "date", "timestamp", "eventDate", "time"))
        desc = str(_first(e, "eventDescription", "description", "status", "event", default="") or "").strip()
        exc = str(_first(e, "exceptionDescription", "exception", default="") or "").strip()
        loc = _loc_text(_first(e, "scanLocation", "location", "city", default=""))
        if not (at or desc):
            continue
        events.append({"at": at, "desc": desc, "exception": exc, "location": loc})
    events.sort(key=lambda x: x["at"] or datetime.min, reverse=True)

    delivered_at = steps.get("delivered")
    delivered_event = next((e for e in events if _DELIVERED_RX.search(e["desc"]) and not _EXCEPTION_RX.search(e["desc"])), None)
    if not delivered_at and delivered_event:
        delivered_at = delivered_event["at"]
    shipped_at = steps.get("shipped")
    latest = events[0] if events else None
    left_at = signed_by = ""
    if delivered_event and delivered_event["exception"]:
        note = delivered_event["exception"]
        m = _LEFT_AT_RX.search(note)
        if m:
            left_at = m.group(1).strip().rstrip(".")
        m = _SIGNED_RX.search(note)
        if m:
            signed_by = m.group(1).strip()
        if not left_at and not signed_by and not _EXCEPTION_RX.search(note):
            left_at = note.split(".")[0].strip()
    if delivered_at:
        status = "Delivered"
    elif latest:
        status = normalize_status(latest["desc"])
        exc_text = latest["exception"] or ""
        if _EXCEPTION_RX.search(latest["desc"]) or (exc_text and _EXCEPTION_RX.search(exc_text)):
            status = "DeliveryException"
        elif status == "Unknown" and shipped_at:
            status = "InTransit"
    elif shipped_at:
        status = "LabelCreated"
    else:
        status = "Unknown"
    movement = [e for e in events if e["at"] and not _NO_MOVEMENT_RX.search(e["desc"])]
    last_movement_at = movement[0]["at"] if movement else None
    last_event_at = latest["at"] if latest else None
    latest_loc = (latest["location"] if latest else "") or _loc_text(t.get("latestLocation"))
    estimated = _first(t, "estimatedDeliveryDay", "estimatedDeliveryDate", "estimatedDelivery")
    err = t.get("error")
    return {
        "tracking_number": number, "carrier": carrier or carrier_code, "carrier_code": carrier_code or carrier,
        "url": url, "status": status,
        "delivered_at": delivered_at, "shipped_at": shipped_at,
        "left_at": left_at, "signed_by": signed_by,
        "last_event": latest["desc"] if latest else "", "last_event_at": last_event_at,
        "last_exception": (latest["exception"] if latest and latest is not delivered_event else "") or "",
        "last_location": latest_loc, "last_movement_at": last_movement_at,
        "estimated_delivery": fmt_ts(parse_ts(estimated)) if estimated else "",
        "delayed": bool(t.get("delayed")) if t.get("delayed") is not None else False,
        "events": [{"at": fmt_ts(e["at"]), "desc": e["desc"], "exception": e["exception"],
                    "location": e["location"]} for e in events],
        "n_events": len(events),
        "error": (str(err)[:200] if err not in (None, "", {}, []) else ""),
    }


def aggregate(packages: list, now: Optional[datetime] = None) -> dict:
    """Order-level delivery view from its packages, in closing's vocabulary. An order is
    delivered only when EVERY package is (the page's 'Delivered' + 'Shipped' buckets)."""
    now = now or datetime.now()
    out = {"delivery_status": "", "delivery_date": "", "last_scan_time": "", "last_scan_location": "",
           "delivery_note": "", "estimated_window": "", "left_at": "", "signed_by": "",
           "last_movement_at": "", "stalled_days": None, "delivered": 0, "packages": len(packages),
           "untracked": sum(1 for p in packages if p["status"] == "Untracked")}
    if not packages:
        return out
    if out["untracked"] == len(packages):
        # No scans exist for these, so no movement and no stall can be computed either.
        out["delivery_note"] = "Freight carrier — not trackable via Zoro: " + \
            ", ".join(sorted({p["carrier_code"] for p in packages if p["carrier_code"]}))
        return out
    delivered = [p for p in packages if p["status"] == "Delivered"]
    out["delivered"] = len(delivered)
    if len(delivered) == len(packages):
        out["delivery_status"] = "Delivered" if len(packages) == 1 else "AllDelivered"
        pick = max(delivered, key=lambda p: p["delivered_at"] or datetime.min)
        out["delivery_date"] = fmt_delivery(pick["delivered_at"])
        out["left_at"] = pick["left_at"]
        out["signed_by"] = pick["signed_by"]
    elif delivered:
        out["delivery_status"] = "PartialDelivered"
        pick = max((p for p in packages if p["status"] != "Delivered"),
                   key=lambda p: p["last_event_at"] or datetime.min)
    else:
        informative = [p for p in packages if p["status"] not in NOT_INFORMATIVE]
        if not informative:
            return out
        pick = max(informative, key=lambda p: (STATUS_RANK.get(p["status"], 0), p["last_event_at"] or datetime.min))
        out["delivery_status"] = pick["status"]
    out["last_scan_time"] = fmt_ts(pick["last_event_at"], seconds=True)
    out["last_scan_location"] = pick["last_location"]
    out["delivery_note"] = ((pick["last_exception"] + " — ") if pick["last_exception"] else "") + pick["last_event"]
    out["estimated_window"] = pick.get("estimated_delivery") or ""
    moves = [p["last_movement_at"] for p in packages if p["last_movement_at"]]
    if moves:
        lm = max(moves)
        out["last_movement_at"] = fmt_ts(lm, seconds=True)
        if out["delivery_status"] not in ("Delivered", "AllDelivered"):
            out["stalled_days"] = max(0, (now - lm).days)
    elif out["delivery_status"] not in ("Delivered", "AllDelivered"):
        ships = [p["shipped_at"] for p in packages if p["shipped_at"]]
        if ships:
            out["stalled_days"] = max(0, (now - max(ships)).days)
    return out


def parse_order(payload, order_no: str = "") -> dict:
    """The order payload → what closing can use. Parcels (tracking + carrier code) come from
    shippingSummary.deliveries; the scan history is fetched per parcel (see ZoroTracker.track_order)."""
    if not isinstance(payload, dict):
        return {"order_no": order_no, "parcels": [], "warnings": ["payload is not an object"]}
    summ = payload.get("orderSummary") or {}
    ship = payload.get("shippingSummary") or {}
    addr = payload.get("shippingAddress") or {}
    parcels, deliveries = [], []
    for d in (ship.get("deliveries") or []):
        if not isinstance(d, dict):
            continue
        fulfilled = parse_ts(d.get("fulfillDate"))
        items = [(str(i.get("zoroProductId") or ""), i.get("quantity")) for i in (d.get("items") or []) if isinstance(i, dict)]
        deliveries.append({"fulfilled_at": fmt_ts(fulfilled), "items": items,
                           "parcels": [str(p.get("trackingNumber") or "").strip() for p in (d.get("parcels") or []) if isinstance(p, dict)]})
        for p in (d.get("parcels") or []):
            if isinstance(p, dict) and p.get("trackingNumber"):
                parcels.append({"tracking_number": str(p["trackingNumber"]).strip(),
                                "carrier_code": str(p.get("carrier") or "").strip(),
                                "fulfilled_at": fulfilled})
    if not parcels:                                  # unexpected shape: look anywhere
        for d in _walk(payload):
            num = _first(d, *_TRACKING_KEYS)
            if num and "carrier" in d:
                parcels.append({"tracking_number": str(num).strip(), "carrier_code": str(d.get("carrier") or ""),
                                "fulfilled_at": None})
    seen, uniq = set(), []
    for p in parcels:
        if p["tracking_number"] and p["tracking_number"] not in seen:
            seen.add(p["tracking_number"])
            uniq.append(p)
    line_items, expected = [], []
    for li in (payload.get("lineItems") or []):
        if not isinstance(li, dict):
            continue
        line_items.append({"zoro_no": str(li.get("zoroProductId") or ""), "name": li.get("name") or "",
                           "qty": li.get("quantityPurchased"), "total": li.get("total"),
                           "expected_ship": li.get("expectedShipDate") or "",
                           "expected_delivery": li.get("latestExpectedDeliveryDate") or "",
                           "status": li.get("lineItemStatus")})
        if li.get("latestExpectedDeliveryDate"):
            expected.append(str(li["latestExpectedDeliveryDate"])[:10])
    warnings = []
    if not uniq and not line_items:
        warnings.append("no parcels and no line items — unexpected payload shape")
    return {
        "order_no": str(payload.get("orderNumber") or order_no or ""),
        "sales_order": str(payload.get("salesOrder") or ""),
        "email": str(payload.get("email") or ""),
        "order_status": str(payload.get("orderStatus") or ""),
        "created_at": fmt_ts(parse_ts(payload.get("createdAt"))),
        "ship_to": {"name": " ".join(x for x in (addr.get("firstName"), addr.get("lastName")) if x),
                    "street": " ".join(x for x in (addr.get("address1"), addr.get("address2")) if x),
                    "city": addr.get("city") or "", "state": addr.get("state") or "", "zip": addr.get("zip") or ""},
        "totals": {"subtotal": summ.get("subTotal"), "shipping": summ.get("shippingTotal"),
                   "freight": summ.get("freightTotal"), "discount": summ.get("discountTotal"),
                   "tax": summ.get("taxTotal"), "total": summ.get("total"), "promo_code": summ.get("promoCode") or ""},
        "shipping_method": ship.get("shippingMethod") or "",
        "deliveries": deliveries, "parcels": uniq, "line_items": line_items,
        "expected_delivery": max(expected) if expected else "",
        "warnings": warnings,
    }


# ── transport ─────────────────────────────────────────────────────────────────
class ZoroTracker:
    """fetch_order(order_no, email) / fetch_package(tracking, carrier_code) → (outcome, payload)
    with outcome ∈ ok | not_found | invalid | refused | rate_limited | error, and
    track_order(order_no, email) → the whole normalised picture. Refusals are counted;
    after `max_refusals` in a row the http transport hands over to the browser ('auto'),
    and if that is refused too, ZoroRefused is raised so callers stop instead of hammering."""

    def __init__(self, transport: str = "auto", headless: bool = True, port: int = 9225,
                 profile: str = "zoro_tracking_guest", delay_s: float = 0.4, max_refusals: int = 2,
                 dump_dir: str = ""):
        self.transport = (transport or "auto").strip().lower()
        self.headless = headless
        self.port = int(port)
        self.profile = profile
        self.delay_s = float(delay_s)
        self.max_refusals = int(max_refusals)
        self.dump_dir = dump_dir
        self._session = None
        self._driver = None
        self._chrome_proc = None
        self._refusals = 0
        self._next_at = 0.0
        self._lock = threading.Lock()          # pacing + refusal count (workers share one tracker)
        self._browser_lock = threading.Lock()  # one Selenium call at a time
        self.calls = 0
        self.mode = "http" if self.transport in ("auto", "http") else "browser"
        self.blocked = False

    # -- http
    def _http_session(self):
        if self._session is None:
            import requests
            from utils.helpers import zoro_user_agent
            s = requests.Session()
            s.headers.update({"User-Agent": zoro_user_agent() or "", "Accept": "application/json, text/plain, */*",
                              "apikey": ZORO_PUBLIC_API_KEY, "Referer": ZORO_TRACK_PAGE})
            self._session = s
        return self._session

    def _http_get(self, url: str, params: dict):
        r = self._http_session().get(url, params=params, timeout=40)
        ct = (r.headers.get("content-type") or "").lower()
        text = r.text or ""
        body = None
        if "json" in ct or text[:1] in ("{", "["):
            try:
                body = json.loads(text)
            except Exception:
                body = None
        return self._classify(r.status_code, body, text)

    # -- browser
    def _browser(self):
        if self._driver is None:
            from utils.helpers import setup_driver, apply_zoro_identity, reapply_stealth
            if self.headless:
                # setup_driver reads the closing switch; this process wants no window.
                os.environ.setdefault("ZUHRA_HEADLESS_CLOSING", "1")
            driver, proc = setup_driver(self.port, self.profile, component="closing")
            if driver is None:
                raise ZoroTrackingError(f"could not start the tracking Chrome on port {self.port}")
            apply_zoro_identity(driver)
            driver.get(ZORO_TRACK_PAGE)
            time.sleep(3)
            try:
                reapply_stealth(driver)
            except Exception:
                pass
            driver.set_script_timeout(60)
            self._driver, self._chrome_proc = driver, proc
        return self._driver

    _FETCH_JS = r"""
      var url = arguments[0], fallbackKey = arguments[1], cb = arguments[arguments.length - 1];
      var key = fallbackKey;
      try { var b = JSON.parse(window.APP_BOOTSTRAP || '{}'); if (b.apiGateway && b.apiGateway.key) key = b.apiGateway.key; } catch (e) {}
      fetch(url, {headers: {'Accept': 'application/json, text/plain, */*', 'apikey': key}, credentials: 'include'})
        .then(function (r) { return r.text().then(function (t) { cb({s: r.status, ct: r.headers.get('content-type') || '', d: t.substring(0, 600000)}); }); })
        .catch(function (e) { cb({s: -1, ct: '', d: String(e)}); });
    """

    def _browser_get(self, url: str, params: dict):
        with self._browser_lock:
            d = self._browser()
            if "zoro.com" not in (d.current_url or ""):
                d.get(ZORO_TRACK_PAGE)
                time.sleep(2)
            q = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())
            res = d.execute_async_script(self._FETCH_JS, f"{url}?{q}" if q else url, ZORO_PUBLIC_API_KEY) or {}
        status = int(res.get("s", -1))
        text = res.get("d") or ""
        ct = (res.get("ct") or "").lower()
        body = None
        if "json" in ct or text[:1] in ("{", "["):
            try:
                body = json.loads(text)
            except Exception:
                body = None
        return self._classify(status, body, text)

    # -- common
    @staticmethod
    def _classify(status: int, body, text: str):
        low = (text or "")[:3000].lower()
        if status in (401, 403) or "datadome" in low or "captcha" in low or (status == 200 and body is None):
            return "refused", body if body is not None else {"http": status, "preview": (text or "")[:300]}
        if status == 404:
            return "not_found", body
        if status == 429:
            return "rate_limited", body
        if status == 400:
            return "invalid", body
        if status == 200 and isinstance(body, (dict, list)):
            if isinstance(body, dict):
                code = str(_first(body, "type", "code", "errorCode", default="") or "").upper()
                if "NOTFOUND" in code.replace("_", "") :
                    return "not_found", body
                if code in ("INVALID_REQUEST", "ORDER_EMAIL_NOT_SPECIFIED", "ORDER_ID_NOT_SPECIFIED"):
                    return "invalid", body
            return "ok", body
        if status < 0:
            return "error", {"error": text[:300]}
        return "error", {"http": status, "preview": (text or "")[:300]}

    def _pace(self):
        """Global spacing of delay_s between call starts, however many worker threads share
        the tracker (parallelism hides latency; the rate stays what the config says)."""
        with self._lock:
            now = time.time()
            t = max(now, self._next_at)
            self._next_at = t + self.delay_s
            self.calls += 1
        if t > now:
            time.sleep(t - now)

    def _get(self, url: str, params: dict, dump_name: str = ""):
        if self.blocked:
            raise ZoroRefused("tracker already blocked")
        for attempt in range(3):
            self._pace()
            try:
                outcome, body = (self._http_get if self.mode == "http" else self._browser_get)(url, params)
            except ZoroTrackingError:
                raise
            except Exception as e:
                outcome, body = "error", {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            if outcome == "rate_limited":
                with self._lock:
                    self._next_at = max(self._next_at, time.time() + 30)   # everyone backs off
                continue
            if outcome == "refused":
                with self._lock:
                    self._refusals += 1
                    n = self._refusals
                    if n >= self.max_refusals:
                        if self.mode == "http" and self.transport == "auto":
                            print(f"     ↪ Zoro refused plain HTTP {n}× — switching to the browser transport", flush=True)
                            self.mode = "browser"
                            self._refusals = 0
                            n = 0
                        else:
                            self.blocked = True
                if self.blocked:
                    raise ZoroRefused(f"Zoro refused the {self.mode} transport {n}× in a row: {str(body)[:200]}")
                continue
            if outcome == "error" and attempt < 2:
                time.sleep(2)
                continue
            with self._lock:
                self._refusals = 0
            if self.dump_dir and outcome == "ok" and dump_name:
                try:
                    os.makedirs(self.dump_dir, exist_ok=True)
                    with open(os.path.join(self.dump_dir, dump_name), "w", encoding="utf-8") as f:
                        json.dump(body, f, indent=1, default=str)
                except Exception:
                    pass
            return outcome, body
        return "error", {"error": "gave up after retries"}

    def fetch_order(self, order_no: str, email: str):
        order_no = str(order_no or "").strip().upper()
        email = str(email or "").strip()
        if not _ORDER_RX.match(order_no):
            return "invalid", {"error": f"not a Zoro order number: {order_no}"}
        if not email:
            return "invalid", {"error": "no account e-mail for the lookup"}
        return self._get(f"{ZORO_ORDER_ENDPOINT}/{order_no}", {"email": email_hash(email)}, f"{order_no}.order.json")

    def fetch_package(self, tracking_number: str, carrier_code: str):
        tid = str(tracking_number or "").strip()
        if not tid:
            return "invalid", {"error": "no tracking number"}
        return self._get(f"{ZORO_PACKAGE_ENDPOINT}/{quote(tid, safe='')}", {"carrier": str(carrier_code or "")},
                         f"{tid}.tracking.json")

    def track_order(self, order_no: str, email: str, known_parcels: Optional[list] = None,
                    fallback_tracking: Optional[list] = None) -> dict:
        """Order lookup + one tracking lookup per parcel → {outcome, order, packages, aggregate}.
        known_parcels=[{tracking_number, carrier_code}] skips the order call (a re-check of an
        order whose parcels are already recorded). fallback_tracking = the tracking numbers
        closing already holds from Zoro's shipping e-mail: the order payload's deliveries[]
        lags that e-mail by a day or more (WB7476127627, 27 Sep 2026 — shipped and delivered
        while Zoro's order still listed no parcel), so FedEx-shaped numbers are looked up
        directly with the FedEx code; one that FedEx does not know is simply dropped."""
        order = None
        if known_parcels:
            parcels = [{"tracking_number": p["tracking_number"], "carrier_code": p.get("carrier_code", ""), "fulfilled_at": None}
                       for p in known_parcels if p.get("tracking_number")]
            outcome = "ok"
        else:
            outcome, body = self.fetch_order(order_no, email)
            if outcome != "ok":
                return {"outcome": outcome, "error": body, "order": None, "packages": [], "aggregate": aggregate([])}
            order = parse_order(body, order_no)
            parcels = order["parcels"]
        if not parcels and fallback_tracking and not (order and order.get("order_status", "").lower() == "cancelled"):
            parcels = [{"tracking_number": t, "carrier_code": "FDEG", "fulfilled_at": None, "guessed": True}
                       for t in fallback_tracking if _FEDEX_NUMBER_RX.match(str(t or "").strip())]
        packages, failures = [], []
        for p in parcels:
            oc, tb = self.fetch_package(p["tracking_number"], p["carrier_code"])
            if oc == "ok" and isinstance(tb, dict):
                pk = parse_package(tb, p["carrier_code"])
                if not pk["shipped_at"] and p.get("fulfilled_at"):
                    pk["shipped_at"] = p["fulfilled_at"]
                packages.append(pk)
                continue
            if p.get("guessed"):
                continue                      # not a FedEx number after all — nothing to record
            # Freight / LTL parcels (WARD TRUCKING, DAYTON FREIGHT, US CARGO, DELIVERY SOL OF
            # AMERICA…): Zoro's tracking answers 400 "Carrier not support". ~1 order in 8. They
            # are recorded as Untracked — never late, never stalled, never messaged — not as an
            # error, and the freight carrier's name goes on the order.
            untracked = oc == "invalid" and _UNSUPPORTED_RX.search(str(tb)) is not None
            if not untracked:
                failures.append({"tracking_number": p["tracking_number"], "outcome": oc, "error": str(tb)[:200]})
            packages.append({"tracking_number": p["tracking_number"], "carrier": p["carrier_code"],
                             "carrier_code": p["carrier_code"], "url": "", "status": "Untracked" if untracked else "Unknown",
                             "delivered_at": None, "shipped_at": p.get("fulfilled_at"), "left_at": "", "signed_by": "",
                             "last_event": "", "last_event_at": None,
                             "last_exception": f"Carrier not trackable via Zoro: {p['carrier_code']}" if untracked else "",
                             "last_location": "", "last_movement_at": None, "estimated_delivery": "", "delayed": False,
                             "events": [], "n_events": 0, "error": "" if untracked else f"{oc}: {str(tb)[:120]}"})
        return {"outcome": "ok", "order": order, "packages": packages, "failures": failures,
                "aggregate": aggregate(packages)}

    def close(self):
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None
            try:
                from utils.helpers import close_owned_chromes
                close_owned_chromes(timeout=8.0)      # the Chrome itself (launched via `open -a`)
            except Exception:
                pass
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None
