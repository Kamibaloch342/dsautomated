"""
engine/closing_signals.py — small, fixture-verified extractors for the closing
signals that live in raw emails / the SC page (return/refund reasons, Zoro
support cases, seller feedback). Pure functions: take decoded text, return data.
Verified against Final_Testing_Logs/debug/<run>/ fixtures.
"""

from __future__ import annotations

import re

# Amazon's fixed return-reason vocabulary (the value shown in the return-auth
# email table). Matching against a known set is far more robust than positional
# table parsing. Order longest-first so specific phrases win.
KNOWN_RETURN_REASONS = sorted([
    "Missing parts or accessories",
    "Item defective or doesn't work",
    "Product and shipping box both damaged",
    "Product damaged but shipping box OK",
    "Inaccurate website description",
    "Item not as described",
    "Bought by mistake",
    "Better price available",
    "No longer needed",
    "Wrong item was sent",
    "Item arrived too late",
    "Missing or broken parts",
    "Received wrong item",
    "Defective",
    "Not as described",
    "Damaged",
], key=len, reverse=True)


def _strip_noise(html: str) -> str:
    """Remove <style>/<script>/<head> blocks whose inner text is not real content."""
    h = html or ""
    h = re.sub(r"(?is)<style.*?</style>", " ", h)
    h = re.sub(r"(?is)<script.*?</script>", " ", h)
    h = re.sub(r"(?is)<head.*?</head>", " ", h)
    return h


def _cells(html: str) -> list[str]:
    """Split HTML into visible cell-ish tokens (tags → delimiters)."""
    b = re.sub(r"<[^>]+>", "|", _strip_noise(html))
    b = re.sub(r"\|+", "|", b)
    out = []
    for c in b.split("|"):
        c = re.sub(r"\s+", " ", c).replace("&amp;", "&").strip()
        if c:
            out.append(c)
    return out


def extract_return_reason(html: str) -> tuple[str, str]:
    """From a 'Return authorization notification' email → (reason, customer_comment).
    Reason is matched to Amazon's known vocabulary; the comment is the cell after it."""
    cells = _cells(html)
    # 1) HTML table form: a cell == the reason, next cell == the comment
    for i, c in enumerate(cells):
        if c in KNOWN_RETURN_REASONS:
            comment = cells[i + 1] if i + 1 < len(cells) else ""
            if comment in ("", "View return", "Print label") or comment in KNOWN_RETURN_REASONS:
                comment = ""
            return c, comment
    # 2) plain-text / flattened fallback: substring scan
    flat = " ".join(cells)
    for reason in KNOWN_RETURN_REASONS:
        j = flat.find(reason)
        if j >= 0:
            comment = flat[j + len(reason):].strip(" :-|")[:200]
            return reason, comment
    return "", ""


def extract_refund_reason(html: str) -> str:
    """From a 'Refund Initiated' email → the refund reason. The email is a table
    ('Refund Reason' header then a product cell then the reason), so match only
    Amazon's known reason vocabulary — never the free text right after the header
    (that's the product name)."""
    txt = re.sub(r"<[^>]+>", " ", _strip_noise(html))
    txt = re.sub(r"\s+", " ", txt)
    for reason in KNOWN_RETURN_REASONS:           # refund emails reuse the return vocab
        if reason.lower() in txt.lower():
            return reason
    return ""


def extract_zoro_promo(raw: str) -> tuple:
    """Zoro promo/coupon from the order email → (coupon_savings, coupon_label).
    Matches 'Promo applied: -$4.95' (the archive Zoro parser never captured it).
    Specific pattern avoids false hits like 'PROMONTORY PKWY' in addresses."""
    if not raw:
        return None, ""
    m = re.search(r"Promo(?:\s+code)?\s+applied\s*:?\s*-?\$\s*([\d,]+\.\d{2})", raw, re.I)
    if not m:
        m = re.search(r"(?:Coupon|Discount)\s*:?\s*-?\$\s*([\d,]+\.\d{2})", raw, re.I)
        if not m:
            return None, ""
    return float(m.group(1).replace(",", "")), "Promo applied"


_ETA_RX = re.compile(
    # the weekday is optional and may be abbreviated: "Monday," "Thu," "Thurs."
    r"Estimated Delivery\s+(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\.?,?\s+)?"
    r"([A-Z][a-z]{2,9})\.?\s+(\d{1,2})", re.I)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def extract_zoro_eta(raw: str, email_timestamp: str = "") -> str:
    """Zoro's own delivery estimate from a shipping email → 'YYYY-MM-DD', or ''.

    Every Zoro shipping confirmation says "Estimated Delivery Monday, September 21" — 73% of
    them carry it, covering 96% of shipped orders, and nothing used it. It is what Zoro expects,
    not proof of delivery, so it belongs in zoro_deliver_by and never in delivery_status.

    The email states no year; it is taken from the email's own timestamp, rolling to the next
    year when the estimate falls in an earlier month than the email (a December email promising
    January). Returns '' when the line is absent or the date is not real (30 February)."""
    if not raw:
        return ""
    m = _ETA_RX.search(raw)
    if not m:
        return ""
    month = _MONTHS.get(m.group(1)[:3].lower())
    if not month:
        return ""
    day = int(m.group(2))
    import datetime
    year = None
    ts = str(email_timestamp or "").strip()
    if len(ts) >= 4 and ts[:4].isdigit():
        year = int(ts[:4])
        try:
            sent_month = int(ts[5:7])
        except ValueError:
            sent_month = month
        if month < sent_month - 6:          # e.g. sent in December, arriving in January
            year += 1
    if year is None:
        year = datetime.date.today().year
    try:
        return datetime.date(year, month, day).isoformat()
    except ValueError:
        return ""


def extract_zoro_item(raw: str) -> str:
    """Zoro's own product description (the line just before 'Zoro #:') — lets you
    catch SKU-mapping mistakes vs the Amazon title. e.g. '1 gal Jug Arocep Ultra
    Bleach, Ready to Use, Chlorine, 6 PK'."""
    if not raw:
        return ""
    i = raw.find("Zoro #:")
    if i < 0:
        return ""
    lines = [l.strip() for l in raw[:i].split("\n") if l.strip()]
    _hdr = {"item", "unit price", "qty", "total price", "em", "your order", "price"}
    for l in reversed(lines):
        if l.lower() not in _hdr and len(l) > 8 and not l.startswith("$"):
            return l[:200]
    return ""


def extract_zoro_case(subject: str, body: str = "") -> str:
    """Zoro support CASE id, e.g. 'CASE6040774' (usually in the subject)."""
    m = re.search(r"CASE\s*#?\s*(\d{5,})", (subject or "") + " " + (body or ""), re.I)
    return f"CASE{m.group(1)}" if m else ""


def extract_zoro_ra(body: str) -> str:
    """Zoro Return Authorization number, e.g. 'RA2142282', if present in text."""
    m = re.search(r"\bRA\s*#?\s*(\d{5,})", body or "", re.I)
    return f"RA{m.group(1)}" if m else ""


def extract_feedback(page_source: str) -> dict:
    """Seller feedback from the SC order page HTML. The star rating is an icon
    class 'a-star-N' (NOT visible text), so this must run on page_source."""
    out = {"feedback_rating": None, "feedback_text": "", "feedback_date": ""}
    if not page_source:
        return out
    m = re.search(r"a-star-(\d)\b", page_source)
    if m:
        out["feedback_rating"] = int(m.group(1))
    # comment: the cell labelled "Comments" → next text node
    cm = re.search(r"Comments?\s*</[^>]+>\s*<[^>]+>([^<]{2,300})", page_source)
    if cm:
        out["feedback_text"] = cm.group(1).strip()
    dm = re.search(r"(?:Feedback date|Date)\s*</[^>]+>\s*<[^>]+>\s*([A-Z][a-z]{2,8},?\s+\w+\s+\d{1,2},?\s+\d{4})",
                   page_source)
    if dm:
        out["feedback_date"] = dm.group(1).strip()
    return out


def clean_customer_message(body_html: str) -> str:
    """Best-effort: the buyer's actual message text, stripped of Amazon's wrapper
    boilerplate. Returns up to ~300 chars; may be imperfect for threaded messages."""
    txt = re.sub(r"<[^>]+>", " ", _strip_noise(body_html))
    txt = re.sub(r"\s+", " ", txt).strip()
    # drop leading Amazon header boilerplate up to the message marker
    for lead in ("the following message", "sent you the following", "wrote:"):
        i = txt.lower().find(lead)
        if i >= 0:
            txt = txt[i + len(lead):].lstrip(" :")
            break
    # cut Amazon's trailing UI boilerplate
    for marker in ("View Message", "Resolve Case", "Report questionable",
                   "This service is provided", "Reply to", "To respond",
                   "Additional Information", "Marketplace"):
        i = txt.find(marker)
        if i > 0:
            txt = txt[:i]
    return txt.strip()[:300]
