#!/usr/bin/env python3
"""
live_gmail_per_order.py — Search live Gmail for a SPECIFIC Amazon order ID.

Falls back to live Gmail API when archive_* tables are missing data.

The caller can pass `needed` — a set of field groups — so we only run the
searches that are actually missing and stop as soon as everything is found:

    "earnings"  → earnings, amazon_fees, shipping_amt   (generic scan)
    "zoro"      → zoro_order_total, zoro_tax, zoro_sku, coupon_savings
    "tracking"  → tracking_ids, carrier
    "returns"   → return_requested, refund_done, refund_amount  (targeted subject query)
    "messages"  → customer_messaged, customer_inquiry_count     (targeted subject query)

Usage:
    from engine.live_gmail_per_order import search_order_in_gmail
    result = search_order_in_gmail("112-8246579-0124267", needed={"earnings", "returns"})
"""

from __future__ import annotations

import os
import sys
import re
import json
import base64

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from platforms.gmail import authenticate_gmail_account
from engine import closing_signals as _CS

# Account lists come from config/accounts.json (single source of truth — a
# per-file hardcoded list is how salesmercentile/abecomllc02 went missing and
# left whole months with no Zoro cost). Edit the JSON, not this file.
from utils.accounts_config import gmail_accounts as _cfg_gmail, zoro_accounts as _cfg_zoro
GMAIL_ACCOUNTS = _cfg_gmail()   # Amazon-side (earnings/messages/returns, keyed by Amazon id)
ZORO_ACCOUNTS = _cfg_zoro()     # Zoro-side (order confirmation/cost, keyed by WB id)

ALL_GROUPS = {"earnings", "zoro", "tracking", "returns", "messages"}

# Amazon sends inbound buyer messages under BOTH subject lines — searching
# only the "Order inquiry…" variant is what made order 111-1105746-6925831
# show "customer never messaged" while an inquiry sat in the inbox.
# All real inbound-message subject variants (verified by scanning the
# amazon-customer-messages-received label): "Order inquiry from Amazon customer",
# "Inquiry from Amazon customer <name> (Order: ...)", and the one that was being
# MISSED — "Unexpected problem with completing your order <order#>". The label
# itself is OR'd last so any future variant the user files there is still caught.
INQUIRY_QUERY = (
    '(subject:"Order inquiry from Amazon customer"'
    ' OR subject:"Inquiry from Amazon customer"'
    ' OR subject:"Message from Amazon customer"'
    ' OR subject:"Unexpected problem with completing your order"'
    # Language-INDEPENDENT catch: ANY message from the Amazon buyer proxy domain is a
    # buyer↔seller message for this order. Subject lines are localized (e.g. Spanish
    # "Consulta sobre un pedido…" / "Actualización del vendedor…") so subject-matching
    # alone misses non-English buyers; the sender domain never changes.
    ' OR from:marketplace.amazon.com'
    ' OR label:amazon-customer-messages-received)'
)
RETURN_QUERY = 'subject:"Return authorization notification"'
REFUND_QUERY = 'subject:"Refund Initiated"'


def _get_message_body(service, msg_id: str) -> str:
    """Fetch a Gmail message body. Walks ALL nested parts and prefers text/plain;
    falls back to the FULL text/html (needed for structured emails like the
    return-authorization table) and finally the snippet."""
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()

    plain, html = "", ""

    def _walk(part):
        nonlocal plain, html
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data", "")
        if data and mime == "text/plain" and not plain:
            plain = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        elif data and mime == "text/html" and not html:
            html = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        for sub in part.get("parts", []) or []:
            _walk(sub)

    _walk(msg.get("payload", {}))
    return plain or html or msg.get("snippet", "")


def _list_messages(service, query: str, max_results: int = 5) -> list[dict]:
    try:
        resp = service.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()
        return resp.get("messages", [])
    except Exception:
        return []


def _extract_earnings_from_body(body: str) -> dict | None:
    """Parse a 'Sold, ship now' email. Same HTML caveat as the Zoro parser: the label
    and amount sit in separate table cells, so strip tags + collapse whitespace first,
    else 'Your earnings:</td><td>$36.97' never matches and earnings comes back empty."""
    result = {"earnings": None, "amazon_fees": None, "shipping_amt": None}
    txt = re.sub(r"<[^>]+>", " ", body or "")
    txt = txt.replace("&nbsp;", " ").replace("&amp;", "&")
    txt = re.sub(r"\s+", " ", txt)

    # Your earnings: $X.XX (sum multiple if present)
    earning_matches = re.findall(r"Your earnings?:?\s*\$?([\d,]+\.\d{2})", txt, re.IGNORECASE)
    if earning_matches:
        result["earnings"] = round(sum(float(m.replace(",", "")) for m in earning_matches), 2)

    # Amazon fees: -$X.XX
    fee_match = re.search(r"Amazon fees?:?\s*[-$]+([\d,]+\.\d{2})", txt, re.IGNORECASE)
    if fee_match:
        result["amazon_fees"] = float(fee_match.group(1).replace(",", ""))

    # Shipping: $X.XX
    ship_match = re.search(r"Shipping:?\s*\$?([\d,]+\.\d{2})", txt, re.IGNORECASE)
    if ship_match:
        result["shipping_amt"] = float(ship_match.group(1).replace(",", ""))

    # Ship by: MM/DD/YYYY  — Amazon's promised ship date IS in this email (no scrape needed)
    sb = re.search(r"Ship by:?\s*(\d{1,2}/\d{1,2}/\d{4})", txt, re.IGNORECASE)
    if sb:
        result["ship_by_date"] = sb.group(1)

    return result if any(v is not None for v in result.values()) else None


def _extract_zoro_from_body(body: str) -> dict | None:
    """Parse a Zoro order-confirmation email. The email is HTML with each label and
    its amount in SEPARATE table cells ('Order Total</td><td>$31.39'), so the raw
    body never matches 'Order Total\\s*$31.39'. Strip the tags + collapse whitespace
    FIRST, then parse — running the regex on raw HTML is what made every live cost
    lookup silently return None (orders stuck Incomplete with no zoro_order_total)."""
    result = {}
    txt = re.sub(r"<[^>]+>", " ", body or "")
    txt = txt.replace("&nbsp;", " ").replace("&amp;", "&")
    txt = re.sub(r"\s+", " ", txt)

    m = re.search(r"Order Total[:\s]*\$?([\d,]+\.\d{2})", txt, re.I)
    if m:
        result["zoro_order_total"] = float(m.group(1).replace(",", ""))
    m = re.search(r"\bSubtotal[:\s]*\$?([\d,]+\.\d{2})", txt, re.I)
    if m:
        result["zoro_subtotal"] = float(m.group(1).replace(",", ""))
    m = re.search(r"\bShipping[:\s]*\$?([\d,]+\.\d{2})", txt, re.I)
    if m:
        result["zoro_shipping"] = float(m.group(1).replace(",", ""))
    m = re.search(r"\bTax[:\s]*\$?([\d,]+\.\d{2})", txt, re.I)
    if m:
        result["zoro_tax"] = float(m.group(1).replace(",", ""))
    m = (re.search(r"Zoro\s*#[:\s]*([A-Za-z0-9\-_]+)", txt, re.I)
         or re.search(r"\bSKU[:#\s]*([A-Za-z0-9\-_]+)", txt, re.I))
    if m:
        result["zoro_sku"] = m.group(1)
    m = re.search(r"Coupon [Ss]avings[:\s]*[-$]?([\d,]+\.\d{2})", txt, re.I)
    if m:
        result["coupon_savings"] = float(m.group(1).replace(",", ""))

    return result if result else None


def _extract_tracking_from_body(body: str) -> dict | None:
    """Parse tracking email body."""
    result = {}

    tracking_match = re.search(r"\b(\d{12,14})\b", body)
    if tracking_match:
        result["tracking_ids"] = tracking_match.group(1)

    if "FedEx" in body:
        result["carrier"] = "FedEx"
    elif "UPS" in body:
        result["carrier"] = "UPS"
    elif "USPS" in body:
        result["carrier"] = "USPS"

    return result if result else None


def _scan_generic(service, amazon_order_id: str, needed: set, result: dict) -> None:
    """One generic '"<order-id>"' scan parsing earnings/zoro/tracking emails."""
    messages = _list_messages(service, f'"{amazon_order_id}"', max_results=20)
    for msg_meta in messages:
        if not ({"earnings", "zoro", "tracking"} & needed):
            return
        try:
            body = _get_message_body(service, msg_meta["id"])
            body_lower = body.lower()

            if "earnings" in needed and ("sold, ship now" in body_lower or "your earnings" in body_lower):
                earnings = _extract_earnings_from_body(body)
                if earnings and earnings.get("earnings"):
                    result.update({k: v for k, v in earnings.items() if v is not None})
                    needed.discard("earnings")

            elif "zoro" in needed and "zoro" in body_lower and (
                "order confirmation" in body_lower or "has been received" in body_lower
            ):
                zoro = _extract_zoro_from_body(body)
                if zoro:
                    result.update(zoro)
                    needed.discard("zoro")

            elif "tracking" in needed and ("shipped" in body_lower or "tracking" in body_lower):
                tracking = _extract_tracking_from_body(body)
                if tracking and tracking.get("tracking_ids"):
                    result.update(tracking)
                    needed.discard("tracking")
        except Exception:
            continue


def _check_returns(service, amazon_order_id: str, needed: set, result: dict) -> None:
    """Return authorization + refund initiated → flags, amount, AND reasons."""
    ret_msgs = _list_messages(service, f'{RETURN_QUERY} "{amazon_order_id}"', max_results=1)
    if ret_msgs:
        result["return_requested"] = "Yes"
        try:
            body = _get_message_body(service, ret_msgs[0]["id"])
            reason, comment = _CS.extract_return_reason(body)
            if reason and not result.get("return_reason"):
                result["return_reason"] = reason
            if comment and not result.get("return_customer_comment"):
                result["return_customer_comment"] = comment
        except Exception:
            pass

    refund_msgs = _list_messages(service, f'{REFUND_QUERY} "{amazon_order_id}"', max_results=1)
    if refund_msgs:
        result["refund_done"] = "Yes"
        try:
            body = _get_message_body(service, refund_msgs[0]["id"])
            amt_match = re.search(
                r"refund of\s+(?:USD\s+)?\$?([\d,]+\.\d{2})", body, re.IGNORECASE
            )
            if amt_match:
                result["refund_amount"] = float(amt_match.group(1).replace(",", ""))
        except Exception:
            pass

    # Refund reason: mirror the RELIABLE return reason (exact table-cell match).
    # The refund email's own reason cell is table-positional → false positives,
    # so we don't scrape it; an unmatched refund simply leaves refund_reason blank.
    if result.get("refund_done") == "Yes" and not result.get("refund_reason") \
            and result.get("return_reason"):
        result["refund_reason"] = result["return_reason"]

    if result.get("return_requested") == "Yes" and result.get("refund_done") == "Yes":
        needed.discard("returns")


def _check_messages(service, amazon_order_id: str, needed: set, result: dict) -> None:
    """Inbound customer inquiries (both subject variants) → flag, count, message text."""
    msgs = _list_messages(service, f'{INQUIRY_QUERY} "{amazon_order_id}"', max_results=5)
    if msgs:
        result["customer_messaged"] = "Yes"
        result["customer_inquiry_count"] = len(msgs)
        if not result.get("customer_message_text"):
            try:
                body = _get_message_body(service, msgs[0]["id"])
                txt = _CS.clean_customer_message(body)
                if txt:
                    result["customer_message_text"] = txt
            except Exception:
                pass
        needed.discard("messages")


def _ordered(accounts: list, preferred: list | None) -> list:
    """PREFERRED inboxes first (the ones this store actually uses), then the
    rest — so the common lookup costs 1-2 searches instead of 16, but every
    inbox is still reached when the preferred ones miss."""
    pref = [a for a in (preferred or []) if a]
    return pref + [a for a in accounts if a not in pref]


def _msg_pkt_time(service, msg_id: str) -> str:
    """Message internalDate → 'YYYY-MM-DD HH:MM' in PKT (matches how the
    archive stores email_timestamp, so speed KPIs compute identically)."""
    try:
        meta = service.users().messages().get(
            userId="me", id=msg_id, format="minimal").execute()
        ms = int(meta.get("internalDate", 0))
        if not ms:
            return ""
        from datetime import datetime, timezone, timedelta
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo("Asia/Karachi")
        except Exception:
            tz = timezone(timedelta(hours=5))     # PKT has no DST
        return datetime.fromtimestamp(ms / 1000, tz).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def search_zoro_tracking(wb_order_id: str, preferred: list | None = None) -> dict:
    """WB-keyed live search for Zoro shipment emails → tracking_ids, carrier AND
    shipped_email_time (message time, PKT). Keyed by WB (NOT the Amazon id), so
    the generic amazon-id scan can never find these.

    Zoro doesn't always send a 'has shipped' email — some orders only ever get
    'Your Package Has Been Delivered!'. So: plain WB search, classify by subject.
    A shipped email is the best answer (tracking + shipped time); a delivered
    notice still recovers tracking/carrier (delivered time is NOT a ship time,
    so shipped_email_time is honestly left empty)."""
    if not wb_order_id:
        return {}
    q = f'"{wb_order_id}"'
    fallback = {}
    for account in _ordered(ZORO_ACCOUNTS, preferred):
        try:
            service = authenticate_gmail_account(account)
            if not service:
                continue
            for m in _list_messages(service, q, max_results=8):
                try:
                    meta = service.users().messages().get(
                        userId="me", id=m["id"], format="metadata",
                        metadataHeaders=["Subject"]).execute()
                    subj = next((h["value"] for h in meta.get("payload", {}).get("headers", [])
                                 if h["name"] == "Subject"), "").lower()
                    is_shipped = any(k in subj for k in ("shipped", "on its way", "shipping"))
                    is_delivered = "delivered" in subj
                    if not (is_shipped or is_delivered):
                        continue
                    body = _get_message_body(service, m["id"])
                    t = _extract_tracking_from_body(body) or {}
                    if not t.get("tracking_ids"):
                        continue
                    if is_shipped:
                        ts = _msg_pkt_time(service, m["id"])
                        if ts:
                            t["shipped_email_time"] = ts
                        return t                      # best possible answer
                    if not fallback:
                        fallback = t                  # delivered notice: tracking only
                except Exception:
                    continue
        except Exception:
            continue
    return fallback


def search_earnings(amazon_order_id: str, preferred: list | None = None) -> dict:
    """Amazon-id-keyed live search for the 'Sold, ship now' email → earnings,
    fees, shipping, ship_by AND sold_email_time (message time, PKT — feeds
    placement_speed). Preferred = the store's earnings inbox, tried first."""
    if not amazon_order_id:
        return {}
    q = f'subject:"Sold, ship now" "{amazon_order_id}"'
    for account in _ordered(GMAIL_ACCOUNTS, preferred):
        try:
            service = authenticate_gmail_account(account)
            if not service:
                continue
            for m in _list_messages(service, q, max_results=3):
                try:
                    body = _get_message_body(service, m["id"])
                    e = _extract_earnings_from_body(body) or {}
                    if e.get("earnings") is not None:
                        out = {k: v for k, v in e.items() if v is not None}
                        ts = _msg_pkt_time(service, m["id"])
                        if ts:
                            out["sold_email_time"] = ts
                        return out
                except Exception:
                    continue
        except Exception:
            continue
    return {}


def search_zoro_issue(wb_order_id: str, specific_account: str | None = None) -> dict:
    """WB-keyed search for Zoro support / replacement / refund (the seller-side
    recovery). CASE# is reliably in the subject; RA#/refund amount are best-effort
    (often only in the PDF receipt). Returns {zoro_case_number, zoro_ra_number,
    zoro_refund_amount, zoro_refund_status}."""
    if not wb_order_id:
        return {}
    result = {}
    q = (f'"{wb_order_id}" (CASE OR "Replacement Request" OR "Refund" OR '
         f'"Return Authorization" OR "Credit" OR issue)')
    for account in ([specific_account] if specific_account else GMAIL_ACCOUNTS):
        if not account:
            continue
        try:
            service = authenticate_gmail_account(account)
            if not service:
                continue
            for m in _list_messages(service, q, max_results=5):
                try:
                    full = service.users().messages().get(
                        userId="me", id=m["id"], format="metadata",
                        metadataHeaders=["Subject"]).execute()
                    subj = ""
                    for h in full.get("payload", {}).get("headers", []):
                        if h["name"] == "Subject":
                            subj = h["value"]
                    body = _get_message_body(service, m["id"])
                    case = _CS.extract_zoro_case(subj, body)
                    ra = _CS.extract_zoro_ra(body)
                    if case and not result.get("zoro_case_number"):
                        result["zoro_case_number"] = case
                        result["zoro_refund_status"] = "Issue/CASE opened"
                    if ra and not result.get("zoro_ra_number"):
                        result["zoro_ra_number"] = ra
                    amt = re.search(r"(?:credit|refund)\D{0,20}\$([\d,]+\.\d{2})", body, re.I)
                    if amt and not result.get("zoro_refund_amount"):
                        result["zoro_refund_amount"] = float(amt.group(1).replace(",", ""))
                except Exception:
                    continue
            if result.get("zoro_case_number"):
                break
        except Exception:
            continue
    return result


def search_zoro_cost(wb_order_id: str, preferred: list | None = None) -> dict:
    """WB-keyed search across Zoro accounts (preferred inboxes first) for the
    order-confirmation email, to recover the Zoro cost when the archive is
    missing it. Returns zoro_order_total / subtotal / shipping / tax / coupon /
    item + zoro_email_ts (the 'received' time, PKT — feeds placement_speed)."""
    if not wb_order_id:
        return {}
    out = {}
    q = f'"{wb_order_id}" (subject:received OR subject:receipt OR "Order Total")'
    for account in _ordered(ZORO_ACCOUNTS, preferred):
        try:
            service = authenticate_gmail_account(account)
            if not service:
                continue
            for m in _list_messages(service, q, max_results=5):
                try:
                    body = _get_message_body(service, m["id"])
                    z = _extract_zoro_from_body(body) or {}
                    if z.get("zoro_order_total"):
                        out.update({k: v for k, v in z.items() if v not in (None, "")})
                        cs, lbl = _CS.extract_zoro_promo(body)
                        if cs:
                            out["coupon_savings"] = cs
                            out["coupon_used"] = lbl
                        item = _CS.extract_zoro_item(body)
                        if item:
                            out["zoro_item_desc"] = item
                        sub = re.search(r"Subtotal[:\s]+\$?([\d,]+\.\d{2})", body)
                        if sub:
                            out["zoro_subtotal"] = float(sub.group(1).replace(",", ""))
                        ship = re.search(r"Shipping[:\s]+\$?([\d,]+\.\d{2})", body)
                        if ship:
                            out["zoro_shipping"] = float(ship.group(1).replace(",", ""))
                        out["zoro_gmail_account"] = account
                        ts = _msg_pkt_time(service, m["id"])
                        if ts:
                            out["zoro_email_ts"] = ts
                        return out
                except Exception:
                    continue
        except Exception:
            continue
    return out


def search_order_in_gmail(
    amazon_order_id: str,
    specific_account: str | None = None,
    needed: set | None = None,
    preferred: list | None = None,
) -> dict:
    """
    Search live Gmail for a specific Amazon order ID.
    Tries accounts in priority order (the store's preferred inboxes first),
    stops once every needed group is found.

    Returns a dict with only data fields (safe to merge into closing_orders);
    keys starting with "_" are metadata and must be skipped by callers.
    """
    needed = set(needed) if needed else set(ALL_GROUPS)
    result = {}
    accounts_to_check = [specific_account] if specific_account else _ordered(GMAIL_ACCOUNTS, preferred)

    for account in accounts_to_check:
        if not account or not needed:
            break

        try:
            service = authenticate_gmail_account(account)
            if not service:
                continue

            if {"earnings", "zoro", "tracking"} & needed:
                _scan_generic(service, amazon_order_id, needed, result)
            if "returns" in needed:
                _check_returns(service, amazon_order_id, needed, result)
            if "messages" in needed:
                _check_messages(service, amazon_order_id, needed, result)

            if result:
                result["_source_account"] = account
        except Exception:
            continue

    return result


if __name__ == "__main__":
    if len(sys.argv) > 1:
        oid = sys.argv[1]
        groups = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else None
        print(f"Searching Gmail for {oid} (groups: {groups or 'all'})...")
        print(json.dumps(search_order_in_gmail(oid, needed=groups), indent=2, default=str))
    else:
        print("Usage: python live_gmail_per_order.py <amazon-order-id> [earnings,zoro,...]")
