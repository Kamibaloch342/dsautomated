"""
engine/post_delivery.py — Post-delivery lifecycle actions for Automations/

Ports the following from Final Automation/unified_tracker.py:
  • Delivery check-in messages (6+ days after delivery)
  • Review requests (12+ days after delivery, both accounts)
  • Return / refund detection from Gmail
  • Customer inquiry detection from Gmail
  • "Already sent" confirmation detection from Gmail

Usage:
    from engine.post_delivery import run_post_delivery
    run_post_delivery(account_config)   # processes all eligible orders
"""

from __future__ import annotations

import os
import sys
import re
import time
import random
import logging
import html
from datetime import datetime, date as _date
from typing import Dict, List, Optional, Tuple

# Ensure project root on path
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException

from utils.unified_db import (
    get_conn,
    update_order,
)
from engine.tracking import (
    get_gmail_service,
    get_email_body,
    clean_html,
    _click_radio_and_type,
    TOKEN_DIRECTORY,
    SCOPES,
)
from engine.live_gmail_per_order import INQUIRY_QUERY

# ── Constants ────────────────────────────────────────────────────────────────
AMAZON_CONTACT_URL = (
    "https://sellercentral.amazon.com/messaging/contact?orderID={oid}&marketplaceID=ATVPDKIKX0DER"
)
AMAZON_REVIEW_URL = (
    "https://sellercentral.amazon.com/messaging/reviews?orderID={oid}&marketplaceID=ATVPDKIKX0DER"
)

# ── Timing (owner, 27 Sep 2026) ──────────────────────────────────────────────
# The delivery check-in goes out as soon as Zoro's tracking shows the package delivered, the
# review request 6 days after delivery, and a buyer whose package has not moved for 3 days
# hears from us before they have to ask. Every number comes from bot_config.json →
# "messaging" (the launcher's file) with these defaults; the day windows are inclusive.
_MSG_DEFAULTS = {
    "delivery_message_days": 0,        # days after delivery before the check-in (0 = same day)
    "delivery_message_max_days": 7,    # older deliveries are no longer "did it arrive?" material
    "review_days": 6,                  # days after delivery before the review request
    "review_max_days": 30,             # Amazon's own request window closes at 30 days
    "delay_notice_enabled": True,
    "delay_notice_stalled_days": 3,    # no carrier movement for this long → tell the buyer
    "delay_notice_max_stalled_days": 10,  # a stall older than this is a case for a person, not a template
    "delay_notice_max_days": 45,       # purchase age beyond which we do not start a delay thread
    "delay_notice_fresh_hours": 36,    # the tracking answer must be at most this old
}


def messaging_cfg() -> dict:
    cfg = dict(_MSG_DEFAULTS)
    try:
        from utils.config import load_gui_config
        raw = (load_gui_config().get("messaging") or {})
        for k, d in _MSG_DEFAULTS.items():
            v = raw.get(k)
            if v in (None, ""):
                continue
            if isinstance(d, bool):
                cfg[k] = v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")
            else:
                cfg[k] = int(float(v))
    except Exception:
        pass
    return cfg


_MCFG = messaging_cfg()
DAYS_AFTER_DELIVERY_TO_MESSAGE = _MCFG["delivery_message_days"]
DELIVERY_MESSAGE_MAX_DAYS = _MCFG["delivery_message_max_days"]
DAYS_AFTER_DELIVERY_TO_REVIEW = _MCFG["review_days"]
REVIEW_MAX_DAYS = _MCFG["review_max_days"]
DELAY_NOTICE_ENABLED = _MCFG["delay_notice_enabled"]
DELAY_NOTICE_STALLED_DAYS = _MCFG["delay_notice_stalled_days"]
DELAY_NOTICE_MAX_STALLED_DAYS = _MCFG["delay_notice_max_stalled_days"]
DELAY_NOTICE_MAX_DAYS = _MCFG["delay_notice_max_days"]
DELAY_NOTICE_FRESH_HOURS = _MCFG["delay_notice_fresh_hours"]
HIGH_VALUE_EARNINGS_THRESHOLD = 100.0

RETURN_SUBJECT_PATTERN = "Return authorization notification"
REFUND_SUBJECT_PATTERN = "Refund Initiated"

ACCOUNT_CONFIGS = {
    "hoank": {
        "key": "hoank",
        "name": "HOANK™️",
        "profile_dir": "hoank_tracking_ids",
        "send_messages": True,
        "can_request_review": True,
    },
    "hathoro": {
        "key": "hathoro",
        "name": "Hathoro™️",
        "profile_dir": "hathoro_tracking_ids",
        "send_messages": True,
        "can_request_review": True,
    },
}

logger = logging.getLogger("post_delivery")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s │ %(levelname)s │ %(message)s", "%H:%M:%S"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)

# ── Gmail helpers ────────────────────────────────────────────────────────────

def _search_gmail(service, query: str, max_results: int = 5) -> List[dict]:
    """Thin wrapper around Gmail API list, with retry on transient connection
    errors. httplib2 keeps a persistent socket that goes stale between batches
    (FedEx phase / idle gaps) → "[Errno 32] Broken pipe" / "[Errno 54]
    Connection reset". A silent [] there would make a return/refund check look
    'clean' and wrongly message the buyer, so retry before giving up."""
    last = None
    for attempt in range(3):
        try:
            results = service.users().messages().list(
                userId="me", q=query, maxResults=max_results).execute()
            return results.get("messages", [])
        except Exception as e:
            es = str(e).lower()
            transient = any(k in es for k in (
                "broken pipe", "connection reset", "connection aborted",
                "errno 32", "errno 54", "timed out", "remotedisconnected"))
            if transient and attempt < 2:
                last = e
                time.sleep(1.0 + attempt)      # let the stale socket drop, reconnect
                continue
            logger.warning("Gmail search error (%s): %s", query[:60], e)
            return []
    logger.warning("Gmail search FAILED after retries (%s): %s", query[:60], last)
    return []


_PIC_EXT = (".jpg", ".jpeg", ".png", ".gif", ".heic", ".webp", ".bmp", ".tif", ".tiff", ".pdf")

def _msg_has_media(svc, msg_id: str) -> bool:
    """True if the message carries an image/PDF attachment (customer sent a photo)."""
    try:
        full = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
    except Exception:
        return False
    def walk(p):
        fn = (p.get("filename") or "").lower()
        mt = (p.get("mimeType") or "").lower()
        if (fn and fn.endswith(_PIC_EXT)) or mt.startswith("image/") or mt == "application/pdf":
            return True
        return any(walk(sp) for sp in (p.get("parts") or []))
    return walk(full.get("payload", {}))


def customer_inquiry_status(gmail_services: Dict[str, object], order_id: str) -> str:
    """Classify inbound customer messages for this order across ALL variants
    (Order inquiry / Inquiry / Message from / Unexpected problem / labeled).
    Returns "" (none) | "Customer inquiry received" |
    "Customer inquiry received with Pictures" (any message had an image/PDF)."""
    found = media = False
    for svc in gmail_services.values():
        msgs = _search_gmail(svc, f'{INQUIRY_QUERY} "{order_id}"', max_results=10)
        if msgs:
            found = True
            for m in msgs:
                if _msg_has_media(svc, m["id"]):
                    media = True
                    break
        if media:
            break
    if not found:
        return ""
    return "Customer inquiry received with Pictures" if media else "Customer inquiry received"


def check_customer_messaged(gmail_services: Dict[str, object], order_id: str) -> bool:
    """Return True if the customer sent any inbound message for this order
    (all subject variants — see INQUIRY_QUERY)."""
    for svc in gmail_services.values():
        if _search_gmail(svc, f'{INQUIRY_QUERY} "{order_id}"', max_results=1):
            return True
    return False


def check_returns_refunds(gmail_services: Dict[str, object], order_id: str) -> Tuple[bool, bool]:
    """
    Check Gmail for return and/or refund emails for this order.
    Returns (return_found, refund_found).
    """
    return_found = False
    refund_found = False
    for svc in gmail_services.values():
        for pattern, kind in [(RETURN_SUBJECT_PATTERN, "return"), (REFUND_SUBJECT_PATTERN, "refund")]:
            msgs = _search_gmail(svc, f'subject:"{pattern}" "{order_id}"', max_results=3)
            if msgs:
                if kind == "return":
                    return_found = True
                else:
                    refund_found = True
        if return_found and refund_found:
            break
    return return_found, refund_found


def check_cancellation_requested(gmail_services: Dict[str, object], order_id: str) -> bool:
    """Return True if a cancellation request email exists for this order."""
    for svc in gmail_services.values():
        q = f'subject:"Cancellation" "{order_id}"'
        if _search_gmail(svc, q, max_results=1):
            return True
    return False


def check_msg_already_sent(gmail_services: Dict[str, object], order_id: str) -> bool:
    """
    Check if we already sent a *delivery* message for this order.
    Uses a scored fingerprint on Amazon's confirmation email body to avoid
    confusing shipped/cancelled messages with delivery check-ins.
    """
    SHIPPED_STRONG = (
        "your product has already been shipped",
        "has already been shipped",
        "shipped your order with",
        "track the progress of your shipment",
        "you can track the progress of your shipment",
        "processed and dispatched without delay",
        "we pride ourselves on having a very quick turnaround time",
        "shipment appears to be taking longer",
        "unable to cancel",
        "we received your cancellation request",
        "your order has already been cancelled",
    )
    DELIVERY_STRONG = (
        "arrived safely",
        "make sure your order arrived",
        "hope everything arrived",
        "confirm everything arrived safely",
        "follow up on your order",
        "make sure everything arrived",
        "did your order arrive",
        "want to make sure everything arrived",
        "if anything doesn't look right",
        "if there are any issues",
    )
    DELIVERY_WEAK = (
        "delivered via fedex",
        "delivered on",
        "left at",
        "signed by",
        "tracking id(s)",
        "delivery date",
        "fedex tracking",
    )

    for svc in gmail_services.values():
        q = f'subject:"Your e-mail to" "{order_id}"'
        msgs = _search_gmail(svc, q, max_results=5)
        for msg_meta in msgs:
            try:
                full = svc.users().messages().get(userId="me", id=msg_meta["id"], format="full").execute()
                body = clean_html(get_email_body(full)).lower()
            except Exception:
                continue

            # Immediate reject: strong shipped phrases
            if any(phrase in body for phrase in SHIPPED_STRONG):
                continue

            # Must have at least one strong delivery indicator, or two weak ones
            strong_hits = sum(1 for phrase in DELIVERY_STRONG if phrase in body)
            weak_hits = sum(1 for phrase in DELIVERY_WEAK if phrase in body)

            if strong_hits >= 1 or weak_hits >= 2:
                logger.info("  [MSG-Check] %s — found prior delivery message in Gmail", order_id)
                return True

    return False


# ── Batched guard prefetch (26 Sep 2026) ─────────────────────────────────────
_GUARD_QUERIES = (
    ("inquiry", INQUIRY_QUERY),
    ("returns", f'subject:"{RETURN_SUBJECT_PATTERN}"'),
    ("refunds", f'subject:"{REFUND_SUBJECT_PATTERN}"'),
    ("cancel", 'subject:"Cancellation"'),
    ("sent", 'subject:"Your e-mail to"'),
)


def _ids_in_message(svc, msg_id: str, candidates: List[str]):
    """Which of `candidates` a Gmail message is about: the subject first (cheap metadata
    call), then the body. None when the fetch failed. If Gmail matched the message but the
    id is nowhere we can see, every candidate counts — the safe side."""
    try:
        meta = svc.users().messages().get(userId="me", id=msg_id, format="metadata",
                                          metadataHeaders=["Subject"]).execute()
        subj = " ".join(h.get("value", "") for h in (meta.get("payload", {}).get("headers") or [])
                        if h.get("name") == "Subject")
        hit = {c for c in candidates if c in subj}
        if hit:
            return hit
        full = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
        text = clean_html(get_email_body(full) or "")
        hit = {c for c in candidates if c in text}
        return hit or set(candidates)
    except Exception:
        return None


def prefetch_guards(gmail_services: Dict[str, object], order_ids: List[str], chunk: int = 20) -> dict:
    """One batched Gmail pass for the guard questions of many orders — buyer inquiry,
    return, refund, cancellation, and a prior 'Your e-mail to' copy — instead of five
    searches per order per inbox (≈ 25–50 calls and 30–40 s per order before this).

    Returns {"complete": bool, "inquiry": set, "returns": set, "refunds": set, "cancel": set,
    "sent": set}. A set names the orders that HAVE such mail, so a per-order check is only
    needed for those. complete=False means a query failed or paginated for some chunk; the
    affected chunk is then marked as hits across the board, and process_single_order falls
    back to the full per-order checks for it — never to "no mail found"."""
    ids = [str(i).strip() for i in dict.fromkeys(order_ids or []) if str(i).strip()]
    out = {"complete": True, "inquiry": set(), "returns": set(), "refunds": set(), "cancel": set(), "sent": set()}
    if not ids or not gmail_services:
        out["complete"] = False
        return out
    n_calls = 0
    for svc in gmail_services.values():
        if not svc:
            continue
        for i in range(0, len(ids), chunk):
            part = ids[i:i + chunk]
            or_block = " OR ".join(f'"{x}"' for x in part)
            for key, q in _GUARD_QUERIES:
                try:
                    resp = svc.users().messages().list(userId="me", q=f"{q} ({or_block})",
                                                       maxResults=100).execute()
                    n_calls += 1
                except Exception as e:
                    logger.warning("guard prefetch (%s) failed: %s", key, str(e)[:80])
                    out["complete"] = False
                    out[key].update(part)
                    continue
                if resp.get("nextPageToken"):
                    out[key].update(part)          # too many to attribute — check each order
                    continue
                for m in resp.get("messages", []) or []:
                    hit = _ids_in_message(svc, m["id"], part)
                    n_calls += 1
                    if hit is None:
                        out["complete"] = False
                        out[key].update(part)
                        break
                    out[key].update(hit)
    logger.info("  [guards] %d orders prefetched in %d Gmail calls — inquiry %d, return %d, refund %d, "
                "cancel %d, prior mail %d%s", len(ids), n_calls, len(out["inquiry"]), len(out["returns"]),
                len(out["refunds"]), len(out["cancel"]), len(out["sent"]),
                "" if out["complete"] else " (incomplete → per-order checks)")
    return out


# ── Date helpers ─────────────────────────────────────────────────────────────

def _delivery_days_ago(delivery_date_str: str) -> int:
    """Return calendar days since delivery. -1 if unparseable."""
    if not delivery_date_str:
        return -1
    s = delivery_date_str.strip()
    for fmt in (
        "%d %B %Y, %I:%M %p",
        "%d %B %Y",
        "%A, %m/%d/%y at %I:%M %p",
        "%A, %m/%d/%Y at %I:%M %p",
        "%A, %m/%d/%Y %I:%M %p",
        "%A, %m/%d/%y %I:%M %p",
        "%A, %m/%d/%y",
        "%A, %m/%d/%Y",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %I:%M%p",
        "%m/%d/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            return (_date.today() - dt.date()).days
        except ValueError:
            continue
    return -1


def _parse_date_loose(date_str: str) -> Optional[datetime]:
    """Parse a date string loosely; return None on failure."""
    if not date_str:
        return None
    # The placer stores deliver-by as 'Thu, Sep 24, 2026| ZoroDeliverBy:2026-09-22' (Amazon's date
    # first); before 28 Sep 2026 nothing here parsed it, so no check-in ever used the late/early wording.
    s = str(date_str).split("|", 1)[0].strip()
    s = re.sub(r"\s+(?:PST|PDT|EST|EDT|CST|CDT|MST|MDT|UTC|GMT)\b.*$", "", s).strip()
    for fmt in (
        "%a, %b %d, %Y",
        "%b %d, %Y",
        "%d %B %Y, %I:%M %p",
        "%d %B %Y",
        "%A, %m/%d/%y at %I:%M %p",
        "%A, %m/%d/%Y at %I:%M %p",
        "%A, %m/%d/%Y %I:%M %p",
        "%A, %m/%d/%y %I:%M %p",
        "%A, %m/%d/%y",
        "%A, %m/%d/%Y",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %I:%M%p",
        "%m/%d/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ── Amazon SC interaction ────────────────────────────────────────────────────

def send_amazon_message(driver, order_id: str, message_text: str, store_name: str = "",
                        fallback_name: str = "") -> str:
    """
    Navigate to Amazon contact-buyer page and send message_text.
    fallback_name: buyer/ship name from the DB, used only if the page scrape
                   can't find the name.
    Returns: "Yes" | "No Permission" | "No"
    """
    url = AMAZON_CONTACT_URL.format(oid=order_id)
    driver.get(url)
    time.sleep(random.uniform(3, 5))

    page_src_lower = driver.page_source.lower()
    if (
        "home" in driver.current_url.lower()
        or "not authorized" in page_src_lower
        or "opted out" in page_src_lower
        or "non-critical messages" in page_src_lower
        or "buyer has opted" in page_src_lower
    ):
        logger.warning("  [MSG] %s — buyer opted out / not accessible", order_id)
        return "No Permission"

    # Scrape the buyer's first name. The page has TWO headings:
    #   <h1>Contact Buyer</h1>      ← generic, NOT a name
    #   "Send message to <Name>"    ← the actual buyer name
    # The old code matched /Contact (.+)/ on the h1 and captured the literal
    # word "Buyer" → "Hello Buyer,". Read the "Send message to" heading instead.
    _NAME_JS = r"""
      var el = document.querySelector('span.buyer-name, .buyer-name, [data-test-id="buyer-name"]');
      if (el && el.textContent.trim()) return el.textContent.trim();
      // Find the SHORTEST element whose text is "Send message to <Name>".
      // A wrapping container would read "Send message to David1. Select..." —
      // the leaf heading "Send message to David" is always shorter, so it wins.
      var best = '';
      var nodes = document.querySelectorAll('h1,h2,h3,h4,h5,div,span,p,b,strong');
      for (var i = 0; i < nodes.length; i++) {
        var t = (nodes[i].textContent || '').replace(/\s+/g, ' ').trim();
        var m = t.match(/^Send message to\s+(.+)$/i);
        if (m) {
          var cand = m[1].trim();
          if (cand && cand.length < 40 && (best === '' || cand.length < best.length)) best = cand;
        }
      }
      return best;
    """
    buyer_name = ""
    try:
        buyer_name = driver.execute_script(_NAME_JS) or ""
    except Exception:
        pass
    if not buyer_name and fallback_name:
        buyer_name = fallback_name          # DB ship-name fallback if scrape misses
    if buyer_name:
        buyer_name = buyer_name.split()[0].strip().title()
    message_text = message_text.replace("Hello {buyer_name}", f"Hello {buyer_name}" if buyer_name else "Hello")
    message_text = message_text.replace("{buyer_name}", buyer_name)

    # Use tracking.py's battle-tested helper.
    #
    # 'contactBuyer' ("Other"), not 'unexpectedProblem'. Amazon renders a DIFFERENT form per
    # reason: under "Notify of a problem with shipping your order" the buttons are Cancel and
    # Submit — there is no Send at all, which is why every delivery message failed on 19 Sep
    # with "Send button(s) found=0". "Other" renders Add attachment / Send, which is the form
    # the helper drives and the one the tracker has always used.
    #
    # It is also the honest category. These are delivery check-ins on orders that arrived;
    # filing them as a shipping problem would tell the buyer something untrue. No fallback
    # here for that reason: if "Other" is not offered, the message is skipped rather than
    # sent under a misleading heading.
    wait = WebDriverWait(driver, 15)
    success = _click_radio_and_type(driver, wait, order_id, message_text,
                                     radio_priority=['contactBuyer'])
    if not success:
        # Distinguish between "no permission" and outright failure
        page_src_lower = driver.page_source.lower()
        if any(x in page_src_lower for x in ("opted out", "non-critical messages", "buyer has opted", "not authorized")):
            return "No Permission"
        return "No"

    # Post-send verification
    time.sleep(random.uniform(1, 2))
    page_src_lower = driver.page_source.lower()
    current_url = driver.current_url.lower()
    on_contact_page = "/contact-buyer" in current_url or "/contactbuyer" in current_url
    has_error = any(
        phrase in page_src_lower
        for phrase in (
            "unable to send",
            "message could not be sent",
            "you are not allowed",
            "buyer has chosen not to receive",
            "contact buyer is not available",
            "messaging is not available",
            "opted out",
            "non-critical messages",
            "buyer has opted",
        )
    )
    has_success = any(
        phrase in page_src_lower
        for phrase in (
            "your message has been sent",
            "message sent",
            "we have sent your message",
        )
    )
    if has_error:
        logger.warning("  [MSG] %s — Amazon returned error after send", order_id)
        return "No Permission"
    if not on_contact_page:
        logger.info("  [MSG] Message sent for %s (redirected)", order_id)
        return "Yes"
    if has_success:
        logger.info("  [MSG] Message sent for %s (success banner)", order_id)
        return "Yes"
    logger.warning("  [MSG] %s — still on contact page, no success indicator", order_id)
    return "No"


def request_amazon_review(driver, order_id: str) -> str:
    """
    Navigate to Request a Review page and click Yes.
    Returns: "Yes" | "Already Requested" | "Expired" | "No Permission" | "No"
    """
    url = AMAZON_REVIEW_URL.format(oid=order_id)
    driver.get(url)
    time.sleep(random.uniform(3, 5))

    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
        current_url = driver.current_url.lower()

        if "you are not eligible" in body_text or "not eligible for review" in body_text:
            return "No Permission"
        if "cannot request a review" in body_text:
            return "No Permission"
        if "already requested a review" in body_text and ("request a review" in current_url or "review" in current_url):
            return "Already Requested"
        if "5-30 day" in body_text or "outside the" in body_text:
            return "Expired"

        # Only a button that is actually the review confirmation. The old selector also
        # accepted variant="primary", i.e. ANY primary button on the page — so when Amazon
        # served plain Seller Central instead of the review dialog (seen repeatedly on
        # 19 Sep: "buttons on the page: ['Products', 'Supply chain', 'Orders', ...]") it
        # clicked whichever primary control happened to be there and then reported on it.
        # Clicking an unidentified button on a live Seller Central page is not worth any
        # number of review requests.
        js_click_yes = """
        const box = document.querySelector('.ayb-reviews-button-container');
        if (box) {
            const inner = box.querySelector('button');
            if (inner) { inner.click(); return true; }
            const kb = box.querySelector('kat-button');
            const kin = kb && kb.shadowRoot ? kb.shadowRoot.querySelector('button') : null;
            if (kin) { kin.click(); return true; }
        }
        const btns = document.querySelectorAll('kat-button');
        for (let b of btns) {
            const label = (b.getAttribute('label') || '').trim().toLowerCase();
            if (label !== 'yes' && label !== 'request a review') continue;
            const inner = b.shadowRoot && b.shadowRoot.querySelector('button');
            if (inner) { inner.click(); return true; }
        }
        return false;
        """
        try:
            WebDriverWait(driver, 10).until(lambda d: d.execute_script(js_click_yes))
        except TimeoutException:
            # A bare TimeoutException here said nothing, and every failure came back as a
            # plain "No" — indistinguishable from Amazon declining. Report what the page
            # actually offered, the same way the Send-button failure does.
            try:
                seen = driver.execute_script("""
                    var out = [];
                    document.querySelectorAll('kat-button, button').forEach(function (b) {
                        var t = (b.getAttribute('label') || b.textContent || '').trim();
                        if (t) out.push(t.slice(0, 24));
                    });
                    return out.slice(0, 8);
                """) or []
            except Exception:
                seen = []
            logger.warning("  [REVIEW] %s — no 'Yes' button appeared in 10s "
                           "(buttons on the page: %s)", order_id, seen or "none")
            return "No"

        time.sleep(random.uniform(2, 4))

        # Read the page INCLUDING shadow roots. Seller Central confirms in a Katal component
        # (kat-alert / kat-toast), whose text never appears in body.text — so this saw only the
        # order-details header and called a request that HAD been sent "No". The cost was one
        # wasted visit per order and a false record: on 30 Sep 2026 the table held 6,123
        # "Already Requested" against 2,653 "Yes", which is the same orders coming back a
        # second time to be recognised.
        try:
            body_after = (driver.execute_script("""
                function grab(root, out, depth) {
                    if (!root || depth > 6) return;
                    (root.querySelectorAll ? root.querySelectorAll('*') : []).forEach(function (el) {
                        if (el.shadowRoot) {
                            var t = (el.shadowRoot.textContent || '').trim();
                            if (t) out.push(t);
                            grab(el.shadowRoot, out, depth + 1);
                        }
                    });
                }
                var out = [document.body ? document.body.innerText : ""];
                grab(document, out, 0);
                return out.join(" ");
            """) or "").lower()
        except Exception:
            body_after = driver.find_element(By.TAG_NAME, "body").text.lower()

        if "already requested a review" in body_after:
            return "Already Requested"
        # A bare "thank you" is not evidence: it appears all over Seller Central chrome, so it
        # reported "Yes" for orders where nothing had been requested. Require wording that only
        # the review confirmation uses.
        if any(x in body_after for x in ("review will be requested",
                                         "review request sent",
                                         "we'll send a review request",
                                         "your review request has been sent",
                                         "review request has been submitted",
                                         "requested a review for this order")):
            return "Yes"
        # Still nothing: print what any alert/toast on the page says, so the wording Amazon
        # actually uses can be added rather than guessed at.
        try:
            alerts = driver.execute_script(r"""
                var out = [];
                document.querySelectorAll('kat-alert, kat-toast, [role="alert"], [role="status"]').forEach(function (a) {
                    var t = ((a.getAttribute('description') || '') + ' ' + (a.getAttribute('header') || '') + ' '
                             + (a.shadowRoot ? a.shadowRoot.textContent : '') + ' ' + (a.textContent || '')).trim();
                    if (t) out.push(t.replace(/\s+/g, ' ').slice(0, 90));
                });
                return out.slice(0, 3);
            """) or []
        except Exception:
            alerts = []
        logger.warning("  [REVIEW] %s — clicked Yes but saw no confirmation "
                       "(alerts: %s | page: %s)", order_id, alerts or "none",
                       " ".join(body_after.split())[:110])
        return "No"
    except Exception as e:
        logger.warning("  [REVIEW] Failed for %s: %s: %s", order_id, type(e).__name__, str(e)[:120])
        return "No"


# ── Message builders ─────────────────────────────────────────────────────────

def _build_tracking_lines(tracking_ids_str: str) -> Tuple[str, str]:
    """Return (formatted tracking lines, multi-package note)."""
    # De-duplicate, keeping first-seen order. closing_orders.tracking_ids can hold the same
    # number more than once — 360 orders on 19 Sep, every one of them an exact doubling
    # (4 ids stored as 8). Listing them raw told a buyer their order shipped in 8 separate
    # packages and gave each number twice; 111-1549917-2638602 went out like that. Whatever
    # writes the column, the message must never repeat a tracking number to a customer.
    seen, tids = set(), []
    for t in str(tracking_ids_str or "").split(","):
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            tids.append(t)
    lines = "\n".join(f"  {i}) {tid}" for i, tid in enumerate(tids, 1)) or "  (see order details)"
    multi_note = ""
    if len(tids) > 1:
        multi_note = (
            f"Please note: your order was shipped in {len(tids)} separate packages, "
            f"so you'll see multiple tracking numbers below.\n\n"
        )
    return lines, multi_note


def build_delivery_message(order: dict, store_name: str) -> str:
    """Select and build the appropriate delivery check-in message."""
    oid = order["amazon_order_id"]
    delivery_date = order.get("delivery_date", "")
    tracking_ids_str = order.get("tracking_ids", "")
    tracking_lines, multi_note = _build_tracking_lines(tracking_ids_str)

    left_at = order.get("left_at", "")
    signed_by = order.get("signed_by", "")
    delivery_info_parts = [f"Delivered: {delivery_date}"]
    if left_at:
        delivery_info_parts.append(f"Location: {left_at}")
    if signed_by:
        delivery_info_parts.append(f"Signed by: {signed_by}")
    delivery_info = "\n".join(delivery_info_parts)

    # Determine template variant
    had_cancellation = order.get("had_cancellation", False)
    is_high_value = False
    try:
        earnings_val = float(order.get("earnings", 0) or 0)
        if earnings_val >= HIGH_VALUE_EARNINGS_THRESHOLD:
            is_high_value = True
    except (ValueError, TypeError):
        pass

    # Late delivery check
    is_late = False
    early_note = ""
    deliver_by = order.get("deliver_by_date", "")
    if deliver_by and delivery_date:
        due = _parse_date_loose(deliver_by)
        actual = _parse_date_loose(delivery_date)
        if due and actual and actual > due:
            is_late = True
        elif due and actual and actual <= due:
            days_early = (due - actual).days
            if days_early >= 2:
                early_note = f"Great news — your order was delivered {days_early} days ahead of schedule!\n\n"
            elif days_early == 1:
                early_note = "Great news — your order was delivered a day ahead of schedule!\n\n"
            else:
                early_note = "Your order was delivered right on time!\n\n"

    if had_cancellation:
        return (
            "Hello {buyer_name},\n\n"
            f"Hope you're doing well! We wanted to reach out about your order ({oid}).\n\n"
            f"We noticed you had submitted a cancellation request for this order. "
            f"Unfortunately, because we try to ship as quickly as possible, the order "
            f"had already been packed and dispatched before we could catch it in time.\n\n"
            f"It has since been delivered via FedEx:\n\n"
            f"{multi_note}"
            f"Tracking ID(s):\n{tracking_lines}\n"
            f"{delivery_info}\n\n"
            f"If you'd still like to return it, just let us know — we'll send you a "
            f"prepaid return label so you can ship it back for free and get a full refund.\n\n"
            f"If you're happy with the product and would like to keep it, no action needed "
            f"on your end!\n\n"
            f"Sorry for the mix-up, and thanks for your patience — we really appreciate it.\n\n"
            f"Kind regards,\nCustomer Support Team\n{store_name}"
        )

    if is_late:
        return (
            "Hello {buyer_name},\n\n"
            f"We wanted to reach out regarding your order ({oid}). "
            f"We sincerely apologize that your delivery arrived later than expected — "
            f"we understand how frustrating that can be and we're sorry for the delay.\n\n"
            f"{multi_note}"
            f"Tracking ID(s):\n{tracking_lines}\n"
            f"{delivery_info}\n\n"
            f"We want to make sure everything arrived in perfect condition. "
            f"If there are any issues at all with your order, please don't hesitate "
            f"to let us know and we will resolve it promptly.\n\n"
            f"Kind regards,\nCustomer Support Team\n{store_name}"
        )

    if is_high_value:
        return (
            "Hello {buyer_name},\n\n"
            f"We wanted to follow up on your order ({oid}) to make sure "
            f"everything arrived safely and in perfect condition via FedEx.\n\n"
            f"{multi_note}"
            f"Tracking ID(s):\n{tracking_lines}\n"
            f"{delivery_info}\n\n"
            f"{early_note}"
            f"If you have any questions, concerns, or need any assistance "
            f"at all, please don't hesitate to reach out — we will respond "
            f"as quickly as possible to help resolve any issues.\n\n"
            f"Kind regards,\nCustomer Support Team\n{store_name}"
        )

    # Standard template. Since 27 Sep 2026 this goes out the day Zoro's tracking shows the
    # delivery, so a same-day note says so instead of "following up".
    if _delivery_days_ago(str(delivery_date)) == 0:
        opening = (f"Hope you're doing well! Our tracking shows your order ({oid}) was "
                   f"delivered today via FedEx — we just wanted to make sure it arrived safely.\n\n")
    else:
        opening = (f"Hope you're doing well! Just wanted to follow up and make sure your "
                   f"order ({oid}) arrived safely via FedEx.\n\n")
    return (
        "Hello {buyer_name},\n\n"
        f"{opening}"
        f"{multi_note}"
        f"Tracking ID(s):\n{tracking_lines}\n"
        f"{delivery_info}\n\n"
        f"If anything doesn't look right or you need any help, just let us know — "
        f"we're happy to help!\n\n"
        f"Thanks again for your order — we really appreciate it.\n\n"
        f"Kind regards,\nCustomer Support Team\n{store_name}"
    )


# ── Delay notice (27 Sep 2026) ───────────────────────────────────────────────
# A shipped order whose carrier scans stopped for DELAY_NOTICE_STALLED_DAYS (from Zoro's own
# tracking — scripts/zoro_delivery_check.py writes stalled_days / last_movement_at) gets ONE
# honest heads-up before the buyer has to ask. Same guards as the check-in: no open return or
# refund, no buyer thread (a human answers those), no cancellation request, zip match, and
# never twice (delay_notice_sent + the "Your e-mail to" copy in Gmail).
_DELAY_STRONG = (
    "has not had a carrier scan",
    "no carrier scan",
    "appears to be delayed",
    "delayed in transit",
    "has not been delivered yet",
    "days without movement",
)


def _stalled_days(order: dict) -> Optional[int]:
    """Days since the last physical carrier movement, from the sweep's column or its timestamp."""
    v = order.get("stalled_days")
    try:
        if v not in (None, ""):
            return int(float(v))
    except (TypeError, ValueError):
        pass
    lm = _parse_date_loose(str(order.get("last_movement_at") or ""))
    if lm:
        return max(0, (datetime.now() - lm).days)
    return None


def check_delay_notice_already_sent(gmail_services: Dict[str, object], order_id: str) -> bool:
    """Our own copy of an earlier delay notice for this order ('Your e-mail to …' in Gmail)."""
    for svc in gmail_services.values():
        for msg_meta in _search_gmail(svc, f'subject:"Your e-mail to" "{order_id}"', max_results=5):
            try:
                full = svc.users().messages().get(userId="me", id=msg_meta["id"], format="full").execute()
                body = clean_html(get_email_body(full)).lower()
            except Exception:
                continue
            if any(p in body for p in _DELAY_STRONG):
                logger.info("  [DELAY-Check] %s — found a prior delay notice in Gmail", order_id)
                return True
    return False


def build_delay_message(order: dict, store_name: str) -> str:
    oid = order["amazon_order_id"]
    tracking_lines, multi_note = _build_tracking_lines(order.get("tracking_ids", ""))
    days = _stalled_days(order)
    last_at = _parse_date_loose(str(order.get("last_movement_at") or order.get("last_scan_time") or ""))
    last_txt = last_at.strftime("%A, %B %d") if last_at else "several days ago"
    scan = str(order.get("delivery_note") or "").strip()
    where = str(order.get("last_scan_location") or "").strip()
    scan_line = ""
    if scan or where:
        scan_line = "Last carrier scan: " + " — ".join(x for x in (scan, where) if x) + "\n"
    since = f"since {last_txt}" + (f" ({days} days without movement)" if days else "")
    return (
        "Hello {buyer_name},\n\n"
        f"We're reaching out about your order ({oid}). Our carrier tracking shows the package "
        f"has not had a carrier scan {since}, and it has not been delivered yet. "
        f"We're sorry about the delay.\n\n"
        f"{multi_note}"
        f"Tracking ID(s):\n{tracking_lines}\n"
        f"{scan_line}\n"
        f"We are monitoring the shipment closely. If it does not start moving again within the "
        f"next couple of days, we will make it right with a replacement or a full refund — "
        f"whichever you prefer; just reply to this message and let us know. If the package "
        f"arrives in the meantime, no action is needed on your end.\n\n"
        f"Thank you for your patience, and again, our apologies for the inconvenience.\n\n"
        f"Kind regards,\nCustomer Support Team\n{store_name}"
    )


def process_delay_notice(
    driver,
    order: dict,
    account_config: dict,
    gmail_services: Dict[str, object],
    closing_table: bool = True,
    dry_run: bool = False,
    guards: Optional[dict] = None,
) -> dict:
    """One delay notice for a stalled, undelivered order. Modifies `order` in place and
    returns it; order['delay_notice_sent'] ends as Yes | No Permission | No | '' (not due)."""
    store_name = account_config.get("name", "HOANK")
    oid = order.get("amazon_order_id", "")
    if not oid or not DELAY_NOTICE_ENABLED or not account_config.get("send_messages", True):
        return order
    original = dict(order)
    if (order.get("delay_notice_sent") or "") in ("Yes", "No Permission"):
        return order
    if order.get("delivery_status") in ("Delivered", "AllDelivered") or not order.get("delivery_status"):
        return order                     # delivered, or nothing trackable (freight / not found)
    stalled = _stalled_days(order)
    if stalled is None or stalled < DELAY_NOTICE_STALLED_DAYS:
        return order
    if stalled > DELAY_NOTICE_MAX_STALLED_DAYS:
        # Weeks without movement is not "a heads-up": the order sits in Attention → Delivery
        # for a person to resolve (refund / replacement), and no template goes out.
        return order

    g = guards if (guards and guards.get("complete")) else None
    if g is not None and oid not in g["inquiry"]:
        inquiry = ""
    else:
        inquiry = customer_inquiry_status(gmail_services, oid) if gmail_services else ""
    if inquiry:
        logger.info("  [%s] %s — buyer already wrote (%s); a person answers, no delay notice", store_name, oid, inquiry)
        order["customer_messaged"] = "Yes"
        order["customer_inquiry"] = inquiry
        _persist(oid, {"customer_messaged": "Yes", "customer_inquiry": inquiry}, closing_table, dry_run, existing=original)
        return order
    if g is not None and oid not in g["returns"] and oid not in g["refunds"]:
        return_found, refund_found = False, False
    else:
        return_found, refund_found = check_returns_refunds(gmail_services, oid) if gmail_services else (False, False)
    if return_found or refund_found:
        updates = {}
        if return_found:
            updates["return_requested"] = "Yes"
        if refund_found:
            updates["refund_done"] = "Yes"
        logger.info("  [%s] %s — return/refund in progress, no delay notice", store_name, oid)
        _persist(oid, updates, closing_table, dry_run, existing=original)
        order.update(updates)
        return order
    if gmail_services and (g is None or oid in g["cancel"]) and check_cancellation_requested(gmail_services, oid):
        logger.info("  [%s] %s — buyer asked to cancel; a person handles it, no delay notice", store_name, oid)
        order["delay_notice_sent"] = "No"
        _persist(oid, {"delay_notice_sent": "No"}, closing_table, dry_run, existing=original)
        return order
    ship_zip = (order.get("ship_postal_code") or "").replace("-", "").strip()
    zoro_zip = (order.get("zoro_buyer_zip") or "").replace("-", "").strip()
    if ship_zip and zoro_zip and ship_zip != zoro_zip:
        logger.info("  [%s] %s — zip mismatch (%s vs %s), no delay notice", store_name, oid, ship_zip, zoro_zip)
        order["delay_notice_sent"] = "No"
        _persist(oid, {"delay_notice_sent": "No"}, closing_table, dry_run, existing=original)
        return order
    if gmail_services and (g is None or oid in g["sent"]) and check_delay_notice_already_sent(gmail_services, oid):
        order["delay_notice_sent"] = "Yes"
        _persist(oid, {"delay_notice_sent": "Yes"}, closing_table, dry_run, existing=original)
        return order
    if dry_run:
        logger.info("  [DRY] [%s] %s — would send the delay notice (%s days without movement)", store_name, oid, stalled)
        return order
    text = build_delay_message(order, store_name)
    fallback = (order.get("ship_name") or order.get("buyer_name") or "").strip()
    result = send_amazon_message(driver, oid, text, store_name, fallback_name=fallback)
    order["delay_notice_sent"] = result
    updates = {"delay_notice_sent": result}
    if result == "Yes":
        order["delay_notice_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        updates["delay_notice_at"] = order["delay_notice_at"]
    logger.info("  [%s] %s — delay notice result: %s", store_name, oid, result)
    _persist(oid, updates, closing_table, dry_run, existing=original)
    time.sleep(random.uniform(3, 6))
    return order


# ── Main orchestrator ────────────────────────────────────────────────────────

def _get_gmail_services(token_names: List[str]) -> Dict[str, object]:
    """Authenticate Gmail for each token name; return {name: service}."""
    services = {}
    for name in token_names:
        creds_path = os.path.join(TOKEN_DIRECTORY, "AB ECOM Credentials.json")
        token_path = os.path.join(TOKEN_DIRECTORY, f"{name}_token.json")
        if not os.path.exists(token_path):
            continue
        try:
            svc = get_gmail_service(creds_path, token_path)
            services[name] = svc
        except Exception as e:
            logger.warning("Could not auth Gmail %s: %s", name, e)
    return services


def _is_driver_alive(driver) -> bool:
    try:
        driver.current_url
        return True
    except Exception:
        return False


_TERMINAL_STATUSES = ("Yes", "No Permission", "Already Requested", "Expired")
_GUARDED_FIELDS = (
    "delivered_msg_sent", "review_requested", "return_requested",
    "refund_done", "customer_messaged", "customer_inquiry", "delay_notice_sent",
)


def _persist(
    oid: str,
    updates: dict,
    closing_table: bool,
    dry_run: bool,
    existing: Optional[dict] = None,
) -> None:
    """
    Write post-delivery results to the correct table.

    - closing_orders when closing_table=True (update_order targets the legacy
      `orders` table, which is why earlier runs never showed results).
    - Never downgrade a terminal status ('Yes', 'No Permission', ...) to 'No'.
    """
    if existing:
        for field in _GUARDED_FIELDS:
            if field in updates and updates[field] in ("No", "", None):
                if (existing.get(field) or "") in _TERMINAL_STATUSES:
                    updates.pop(field)
    updates = {k: v for k, v in updates.items() if v is not None}
    if not updates:
        return
    if dry_run:
        logger.info("  [DRY] %s — would persist: %s", oid, updates)
        return
    if closing_table:
        with get_conn() as conn:
            set_clause = ", ".join(f"{k}=?" for k in updates)
            conn.execute(
                f"UPDATE closing_orders SET {set_clause}, updated_at=datetime('now') "
                f"WHERE amazon_order_id=?",
                list(updates.values()) + [oid],
            )
    else:
        update_order(oid, updates, changed_by="post_delivery")


def process_single_order(
    driver,
    order: dict,
    account_config: dict,
    gmail_services: Dict[str, object],
    closing_table: bool = False,
    dry_run: bool = False,
    guards: Optional[dict] = None,
) -> dict:
    """
    Run post-delivery logic for a single order.
    Modifies order dict in-place with results.
    Returns updated order dict.

    guards: the result of prefetch_guards() for a batch that includes this order. When
    present and complete, an order with no mail of a kind skips that per-order search;
    an order WITH such mail still runs the exact same per-order check as before (so the
    already-sent fingerprint, the return/refund confirmation and the inquiry classification
    are unchanged — only the "nothing there" answers are batched).
    """
    store_name = account_config.get("name", "HOANK")
    can_message = account_config.get("send_messages", True)
    can_review = account_config.get("can_request_review", False)
    oid = order.get("amazon_order_id", "")
    if not oid:
        return order

    # Snapshot DB state before any mutation — the downgrade guard in _persist
    # must compare against what was stored, not what this run just set.
    original = dict(order)

    delivery_status = order.get("delivery_status", "")
    delivery_date = order.get("delivery_date", "")
    msg_sent = order.get("delivered_msg_sent", "") or ""
    review_sent = order.get("review_requested", "") or ""

    if delivery_status not in ("AllDelivered", "Delivered") or not delivery_date:
        return order

    days_since = _delivery_days_ago(str(delivery_date))
    if days_since < 0:
        return order

    # ── Gmail checks ──
    g = guards if (guards and guards.get("complete")) else None
    if g is not None and oid not in g["inquiry"]:
        inquiry = ""
    else:
        inquiry = customer_inquiry_status(gmail_services, oid) if gmail_services else ""
    cust_messaged = bool(inquiry)
    order["customer_messaged"] = "Yes" if cust_messaged else "No"
    order["customer_inquiry"] = inquiry          # "" | received | received with Pictures
    if cust_messaged:
        logger.info("  [%s] %s — %s, skipping", store_name, oid, inquiry)
        _persist(oid, {"customer_messaged": "Yes", "customer_inquiry": inquiry},
                 closing_table, dry_run, existing=original)
        return order

    if g is not None and oid not in g["returns"] and oid not in g["refunds"]:
        return_found, refund_found = False, False
    else:
        return_found, refund_found = check_returns_refunds(gmail_services, oid) if gmail_services else (False, False)
    if return_found or refund_found:
        logger.info("  [%s] %s — return/refund found (return=%s refund=%s), skipping",
                    store_name, oid, return_found, refund_found)
        updates = {}
        if return_found:
            updates["return_requested"] = "Yes"
        if refund_found:
            updates["refund_done"] = "Yes"
        _persist(oid, updates, closing_table, dry_run, existing=original)
        order.update(updates)
        return order

    # Zip mismatch guard
    ship_zip = (order.get("ship_postal_code") or "").replace("-", "").strip()
    zoro_zip = (order.get("zoro_buyer_zip") or "").replace("-", "").strip()

    # ── Delivery message ──
    if can_message and msg_sent not in ("Yes", "No Permission") \
            and DAYS_AFTER_DELIVERY_TO_MESSAGE <= days_since <= DELIVERY_MESSAGE_MAX_DAYS:
        if ship_zip and zoro_zip and ship_zip != zoro_zip:
            logger.info("  [%s] %s — zip mismatch (%s vs %s), skipping msg", store_name, oid, ship_zip, zoro_zip)
            order["delivered_msg_sent"] = "No"
        elif (g is None or oid in g["sent"]) and check_msg_already_sent(gmail_services, oid):
            order["delivered_msg_sent"] = "Yes"
            logger.info("  [%s] %s — delivery msg already sent (Gmail confirm)", store_name, oid)
        elif dry_run:
            logger.info("  [DRY] [%s] %s — would send delivery check-in message", store_name, oid)
        else:
            # Build cancellation flag for message template
            had_cancellation = (check_cancellation_requested(gmail_services, oid)
                                if (gmail_services and (g is None or oid in g["cancel"])) else False)
            order["had_cancellation"] = had_cancellation
            message_text = build_delivery_message(order, store_name)
            _fallback_name = (order.get("ship_name") or order.get("buyer_name") or "").strip()
            result = send_amazon_message(driver, oid, message_text, store_name,
                                         fallback_name=_fallback_name)
            order["delivered_msg_sent"] = result
            logger.info("  [%s] %s — delivery msg result: %s", store_name, oid, result)
            time.sleep(random.uniform(3, 6))

    # Mark as No if never attempted and too old to avoid re-checking forever
    elif msg_sent in ("", None):
        order["delivered_msg_sent"] = "No"

    # ── Review request ──
    if can_review and review_sent not in ("Yes", "Already Requested", "Expired") and days_since >= DAYS_AFTER_DELIVERY_TO_REVIEW:
        if dry_run:
            logger.info("  [DRY] [%s] %s — would request review", store_name, oid)
        else:
            review_result = request_amazon_review(driver, oid)
            order["review_requested"] = review_result
            logger.info("  [%s] %s — review result: %s", store_name, oid, review_result)
            time.sleep(random.uniform(3, 6))
    elif review_sent in ("", None):
        order["review_requested"] = "No"

    # Persist to DB
    _persist(oid, {
        "customer_messaged": order.get("customer_messaged"),
        "customer_inquiry": order.get("customer_inquiry"),
        "delivered_msg_sent": order.get("delivered_msg_sent"),
        "review_requested": order.get("review_requested"),
    }, closing_table, dry_run, existing=original)

    return order


def run_post_delivery(
    account_config: dict,
    driver=None,
    token_names: Optional[List[str]] = None,
    closing_table: bool = False,
    dry_run: bool = False,
    account: Optional[str] = None,
) -> List[dict]:
    """
    Main entry point: run post-delivery actions for one Amazon account.

    Args:
        account_config: dict with keys: name, send_messages, can_request_review,
                        profile_dir (for driver creation if driver not provided)
        driver: optional existing Selenium driver
        token_names: list of Gmail token names to auth (defaults to common accounts)

    Returns:
        List of processed order dicts with updated flags.
    """
    store_name = account_config.get("name", "Store")
    logger.info("=" * 60)
    logger.info("Post-delivery run: %s", store_name)
    logger.info("=" * 60)

    # Gmail services (default token list lives in config/accounts.json)
    if token_names is None:
        from utils.accounts_config import gmail_tokens
        token_names = gmail_tokens()
    gmail_services = _get_gmail_services(token_names)
    if not gmail_services:
        logger.warning("No Gmail services available — Gmail checks will be skipped.")

    # Fetch eligible orders from unified DB. ALWAYS account-filtered when the
    # table carries an account column — without it, a HOANK run would try to
    # message Hathoro's buyers through the HOANK Seller Central window.
    table = "closing_orders" if closing_table else "orders"
    if account is None and closing_table:
        key = (account_config.get("key") or "").lower()
        account = {"hoank": "HOANK", "hathoro": "Hathoro"}.get(key)
    acct_clause = "AND account = ?" if (account and closing_table) else ""
    with get_conn() as conn:
        rows = conn.execute(f"""
            SELECT * FROM {table}
            WHERE delivery_status IN ('AllDelivered', 'Delivered')
              AND delivery_date != ''
              AND delivery_date IS NOT NULL
              AND (
                  (delivered_msg_sent IS NULL OR delivered_msg_sent NOT IN ('Yes', 'No Permission'))
                  OR (review_requested IS NULL OR review_requested NOT IN ('Yes', 'Already Requested', 'Expired'))
              )
              {acct_clause}
        """, ([account] if (account and closing_table) else [])).fetchall()
    orders = [dict(r) for r in rows]
    logger.info("Found %d potentially eligible orders", len(orders))

    # Filter by age
    eligible = []
    for o in orders:
        days = _delivery_days_ago(o.get("delivery_date", ""))
        if days >= DAYS_AFTER_DELIVERY_TO_MESSAGE:
            eligible.append(o)
    if not eligible:
        logger.info("No orders are %d+ days old yet.", DAYS_AFTER_DELIVERY_TO_MESSAGE)
        return []
    logger.info("%d orders eligible for post-delivery actions", len(eligible))

    # Driver — not needed in dry-run (no messages are sent)
    driver_owned = driver is None and not dry_run
    if driver_owned:
        from utils.helpers import setup_driver
        from utils.config import PROFILES
        # Find debugging port for this account
        profile_dir = account_config.get("profile_dir", "")
        port = None
        is_hathoro = "hathoro" in store_name.lower()
        for prof_name, prof_cfg in PROFILES.items():
            prof_account = "Hathoro" if prof_name.startswith("DM_") else "HOANK"
            if prof_account == ("Hathoro" if is_hathoro else "HOANK"):
                port = prof_cfg.get("port")
                profile_dir = prof_cfg.get("profile_folder_name", profile_dir)
                break
        if port is None:
            port = 9221 if is_hathoro else 9223
        # Review requests are part of closing, not a placement run, so they follow the
        # closing headless switch rather than the placers'.
        driver, _chrome_proc = setup_driver(port, profile_dir, component="closing")
        if driver is None:
            logger.error("Failed to create driver for %s", store_name)
            return []

    results = []
    try:
        for order in eligible:
            if not dry_run and not _is_driver_alive(driver):
                logger.warning("Driver died — restarting...")
                if driver_owned:
                    from utils.helpers import setup_driver
                    driver, _ = setup_driver(port, profile_dir, component="closing")
                if driver is None:
                    break
            updated = process_single_order(
                driver, order, account_config, gmail_services,
                closing_table=closing_table, dry_run=dry_run,
            )
            results.append(updated)
    finally:
        if driver_owned and driver:
            try:
                driver.quit()
            except Exception:
                pass

    logger.info("Post-delivery run complete: %d orders processed", len(results))
    return results


# ── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run post-delivery actions")
    parser.add_argument("--account", choices=["hoank", "hathoro"], default="hoank",
                        help="Which account to process")
    parser.add_argument("--closing", action="store_true",
                        help="Use closing_orders table instead of legacy orders")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without sending messages")
    args = parser.parse_args()

    cfg = ACCOUNT_CONFIGS[args.account]
    if args.dry_run:
        logger.info("DRY RUN mode — no messages will be sent, no DB writes")
    run_post_delivery(cfg, closing_table=args.closing, dry_run=args.dry_run)
