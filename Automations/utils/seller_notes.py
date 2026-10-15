"""
seller_notes.py — Extract order data and write formatted seller notes to Amazon SC.

Two entry points:
  • extract_seller_note_data(amazon_id) → dict
  • format_seller_note(data) → str
"""

import os
import re
import json
import math

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_FILE = os.path.join(_SCRIPT_DIR, "data", "rajar_logs", "order_log.txt")
_MESSAGE_LOG_FILE = os.path.join(_SCRIPT_DIR, "data", "message_log.json")

# Amazon caps the seller note at 256 characters; we stay under that with a 230
# safety margin. The title is truncated to the space the other (data) lines
# leave, so the data is never lost to a long title.
_MAX_NOTE_CHARS = 230


def _pi_target_margin():
    """Target margin for the PI (price-increase) suggestion, as a decimal.

    Read fresh per call from bot_config.json so the launcher's value applies
    without restarting whatever is writing notes. Default 10%.
    """
    try:
        from utils.config import PI_TARGET_MARGIN_DEFAULT
        default = PI_TARGET_MARGIN_DEFAULT
    except Exception:
        default = 0.10
    try:
        with open(os.path.join(_SCRIPT_DIR, "bot_config.json"), "r", encoding="utf-8") as f:
            val = json.load(f).get("pi_target_margin")
        if val is None:
            return default
        pct = float(val)
        # Accept both 10 (percent) and 0.10 (decimal).
        return pct / 100.0 if pct > 1 else pct
    except (FileNotFoundError, ValueError, OSError, TypeError):
        return default


def _price_for_target_margin(total_cost, item_price, shipping_charged, referral_rate, target):
    """Amazon item price needed to reach `target` margin on this order.

    Margin is (earnings - cost) / cost, matching how the engine reports PP.
    Amazon's referral fee applies to item price + buyer-paid shipping, so:

        earnings          = (price + shipping) * (1 - referral_rate)
        want earnings     = cost * (1 + target)
        =>        price   = cost * (1 + target) / (1 - referral_rate) - shipping

    Rounded UP to the next cent — the exact minimum that meets the target. It is
    deliberately NOT rounded to a .99 price point: that overshoots by up to a
    dollar (a 10% target landing at 12.1%), which would make the "| 10%" label
    wrong. Round up to a nicer price point by hand if you want one.
    """
    if not total_cost or referral_rate is None or referral_rate >= 1:
        return None
    raw = total_cost * (1.0 + target) / (1.0 - referral_rate) - (shipping_charged or 0.0)
    if raw <= 0:
        return None
    return math.ceil(raw * 100.0) / 100.0


def _parse_log_entries():
    """Parse order_log.txt one-liners into a list of dicts."""
    if not os.path.exists(_LOG_FILE):
        return []
    with open(_LOG_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    entries = []
    pattern = (
        r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \| WB:.+?"
        r"(?=\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \| WB:|\Z)"
    )
    for raw in re.findall(pattern, content, re.DOTALL):
        raw = raw.strip()
        m = re.match(r"^\[([^\]]+)\]\s+\|\s+(.+)", raw)
        if not m:
            continue
        _, rest = m.groups()
        entry = {}
        for part in rest.split(" | "):
            part = part.strip()
            if ":" in part:
                key, val = part.split(":", 1)
                entry[key.strip().lower()] = val.strip()
        entries.append(entry)
    return entries


def _find_latest_entry(entries, amazon_id):
    """Return the most recent entry matching the Amazon order ID."""
    matches = [e for e in entries if e.get("amazon", "") == amazon_id]
    return matches[-1] if matches else None


def _tracking_message_status(amazon_id):
    """Return the 'Tracking msg sent' label from message_log.json.

    The cancellation-denial message also contains the tracking ID (it tells the
    buyer the package already shipped, with the tracking number), so it counts as
    the tracking having been communicated — just via a different message.
    """
    if not os.path.exists(_MESSAGE_LOG_FILE):
        return "No"
    try:
        with open(_MESSAGE_LOG_FILE, "r", encoding="utf-8") as f:
            log = json.load(f)
        entry = log.get(amazon_id, {})
        if not entry.get("sent"):
            return "No"
        mtype = entry.get("message_type")
        if mtype == "tracking_confirmation":
            return "Yes"
        if mtype == "cancellation_denial":
            return "Yes (cancellation denial)"
        return "No"
    except Exception:
        return "No"


def _mask_zoro_code(sku):
    """Replace first 'G' with 'K' so Amazon can't reverse-search the Zoro SKU."""
    if not sku:
        return "N/A"
    # Only replace the FIRST occurrence of 'G' at the start
    if sku.startswith("G"):
        return "K" + sku[1:]
    return sku


def _clean_money(value):
    """Strip $ and return float or None."""
    if not value or value in ("N/A", "", "None"):
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _clean_pct(value):
    """Strip % and return float or None."""
    if not value or value in ("N/A", "", "None"):
        return None
    try:
        return float(str(value).replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def enrich_note_basis(data, profile_name=None, zoro_select=None,
                      earnings=None, amazon_fees=None, shipping_amt=None,
                      total_cost=None):
    """Add the profit-basis and PI fields to a hand-built note dict.

    The placer builds its note dicts inline at placement time (it has the live
    numbers and no audit-log entry exists yet), so it can't go through
    extract_seller_note_data(). This gives both paths the same PP suffix and PI
    line from one implementation. Mutates and returns `data`.
    """
    if not isinstance(data, dict):
        return data

    p = str(profile_name or "").upper()
    data["profile_kind"] = "R" if "RESELLER" in p else "C" if "COUPON" in p else ""
    data["zoro_select"] = zoro_select

    target = _pi_target_margin()
    data["pi_target"] = target

    tc = total_cost if total_cost is not None else data.get("tc")
    ship = shipping_amt or 0.0
    if earnings is not None and amazon_fees is not None:
        try:
            item_price = round(float(earnings) + float(amazon_fees) - float(ship), 2)
            gross = item_price + float(ship)
            if gross > 0 and tc:
                rate = float(amazon_fees) / gross
                data["item_price"] = item_price
                data["referral_rate"] = rate
                data["pi_price"] = _price_for_target_margin(
                    float(tc), item_price, float(ship), rate, target)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return data


def extract_seller_note_data(amazon_id):
    """
    Read order_log.txt and message_log.json to build seller-note fields.

    Returns dict with keys:
      title, asin, sku, code, qty, tracking_sent, tc, earnings, pm, pp
    Or None if no entry found.
    """
    entries = _parse_log_entries()
    entry = _find_latest_entry(entries, amazon_id)
    if not entry:
        return None

    # ASIN
    asin = entry.get("asin", "N/A")

    # Title — the Amazon listing title (audit one-liner "Title:" field)
    title = entry.get("title", "")
    if title in ("N/A", "None"):
        title = ""

    # Amazon SKU — the real seller SKU from the Sold-Ship-Now email (audit
    # one-liner "OriginalSKU:" field). Distinct from the Zoro code below.
    amazon_sku = entry.get("originalsku", "")
    if amazon_sku in ("N/A", "None"):
        amazon_sku = ""

    # Code: Zoro SKU with first G → K
    sku = entry.get("sku", "")
    code = _mask_zoro_code(sku)

    # Qty: use Zoro Qty (actual qty sent); fallback to qty field
    zqty = entry.get("zqty", "")
    qty = zqty if zqty else entry.get("qty", "N/A")

    # Tracking msg sent
    tracking_sent = _tracking_message_status(amazon_id)

    # TC (Total Cost) — from Zoro response summary total
    tc = _clean_money(entry.get("cost", ""))

    # T (Tax) — sales tax Zoro charged (audit one-liner "Tax:" field)
    tax = _clean_money(entry.get("tax", ""))

    # Earnings
    earnings = _clean_money(entry.get("earnings", ""))

    # PM (Profit $)
    profit = _clean_money(entry.get("profit", ""))

    # PP (Profit Percentage)
    margin = _clean_pct(entry.get("finalmargin", ""))

    # C (Coupon) — audit one-liner "Coupon:" field; "None"/blank means no coupon
    coupon = entry.get("coupon", "").strip() not in ("", "None", "N/A")
    # $ off — audit one-liner "Discount:" field
    coupon_off = _clean_money(entry.get("discount", ""))

    # ALT — order was placed with the alternative Zoro code (main code was OOS).
    # Comes from the audit one-liner "AltUsed:Yes" field.
    alt_used = entry.get("altused", "").strip().lower() == "yes"

    # Which profile placed it (audit "Profile:DM_RESELLER") → R / C on the PP line.
    profile = entry.get("profile", "").strip().upper()
    if "RESELLER" in profile:
        profile_kind = "R"
    elif "COUPON" in profile:
        profile_kind = "C"
    else:
        profile_kind = ""

    # Zoro Select (audit "ZSelect:Yes|No|Unknown") — says whether the 10% Select
    # discount is already inside TC/PM/PP. Orders placed before this field was
    # logged read as "Unknown", which is shown rather than guessed.
    _zs = entry.get("zselect", "").strip().lower()
    zoro_select = True if _zs == "yes" else False if _zs == "no" else None

    # ── PI (price increase to hit the target margin) ────────────────────
    # Derive the referral rate from THIS order's own numbers rather than
    # assuming a category rate: Amazon charges it on item price + buyer-paid
    # shipping, and earnings is what's left after it.
    #     price = earnings + fees - shipping_charged
    #     referral_rate = fees / (price + shipping_charged)
    amazon_fees = _clean_money(entry.get("amazonfees", ""))
    shipping_amt = _clean_money(entry.get("shippingamt", "")) or 0.0
    item_price = None
    referral_rate = None
    pi_price = None
    pi_target = _pi_target_margin()
    if earnings is not None and amazon_fees is not None:
        item_price = round(earnings + amazon_fees - shipping_amt, 2)
        gross = item_price + shipping_amt
        if gross > 0:
            referral_rate = amazon_fees / gross
            if tc is not None:
                pi_price = _price_for_target_margin(
                    tc, item_price, shipping_amt, referral_rate, pi_target)

    return {
        "title": title,
        "asin": asin,
        "sku": amazon_sku,
        "code": code,
        "alt_used": alt_used,
        "qty": qty,
        "tracking_sent": tracking_sent,
        "tc": tc,
        "tax": tax,
        "earnings": earnings,
        "pm": profit,
        "pp": margin,
        "coupon": coupon,
        "coupon_off": coupon_off,
        "coupon_code": entry.get("coupon", ""),
        "profile_kind": profile_kind,
        "zoro_select": zoro_select,
        "item_price": item_price,
        "referral_rate": referral_rate,
        "pi_price": pi_price,
        "pi_target": pi_target,
    }


def format_seller_note(data):
    """The seller note — the owner's locked order layout (utils.telegram.order_card, note=True),
    15 Oct 2026. Notes built from order_log.txt alone (extract_seller_note_data: no dates) fall
    back to the older line-per-field layout below."""
    if not data:
        return ""
    if data.get("deliver_by_date") or data.get("ship_by_date"):
        try:
            from utils.telegram import order_card
            return order_card(data, note=True)
        except Exception:
            pass

    # Build every line EXCEPT the title first. The whole note must fit in 256
    # chars, so the title later gets only whatever budget these lines leave —
    # the data fields are never sacrificed for a long title. ASIN leads; SKU
    # (the real Amazon seller SKU) and Code (the Zoro code, G→K masked) follow.
    lines = []
    lines.append(f"ASIN: {data.get('asin', 'N/A')}")
    amazon_sku = str(data.get("sku") or "").strip()
    if amazon_sku and amazon_sku not in ("N/A", "None"):
        lines.append(f"SKU: {amazon_sku}")
    # "- ALT" marks an order placed with the alternative Zoro code (main was OOS).
    # Guard against double-suffixing when the caller already baked it into code.
    code_line = f"Code: {data.get('code', 'N/A')}"
    if data.get("alt_used") and " - ALT" not in code_line:
        code_line += " - ALT"
    lines.append(code_line)
    lines.append(f"Qty: {data.get('qty', 'N/A')}")
    tracking = data.get('tracking_sent', 'No')
    lines.append(f"Tracking msg sent: {tracking}")

    tc = data.get("tc")
    if tc is not None:
        lines.append(f"TC: ${tc:.2f}")
    else:
        lines.append("TC: N/A")

    # T (Tax) — sales tax Zoro charged at checkout. Lets you tell whether a thin
    # profit was caused by tax. Only shown when we actually captured a tax value.
    tax = data.get("tax")
    if tax is not None:
        lines.append(f"T: ${tax:.2f}")

    earnings = data.get("earnings")
    if earnings is not None:
        lines.append(f"Earnings: ${earnings:.2f}")
    else:
        lines.append("Earnings: N/A")

    pm = data.get("pm")
    if pm is not None:
        lines.append(f"PM: ${pm:.2f}")
    else:
        lines.append("PM: N/A")

    # PP carries the profit basis: which profile placed it (R/C) and whether the
    # Zoro Select 10% is already inside the number. Without this you cannot tell
    # a genuine loss from one that merely hasn't had the Select discount applied.
    #   PP: -8.2% ( R - ZS )      reseller, Select discount included
    #   PP: -8.2% ( R - Not ZS )  reseller, no Select discount on this item
    #   PP: -8.2% ( C - Not ZS )  coupon profile (never gets Select)
    _kind = data.get("profile_kind") or ""
    _zs = data.get("zoro_select")
    if _kind:
        _zs_txt = "ZS" if _zs is True else "Not ZS" if _zs is False else "ZS?"
        basis = f" ( {_kind} - {_zs_txt} )"
    else:
        basis = ""

    pp = data.get("pp")
    if pp is not None:
        lines.append(f"PP: {pp:.1f}%{basis}")
    else:
        lines.append(f"PP: N/A{basis}")

    # PI (Price Increase) — the Amazon price this listing would need to reach the
    # target margin, e.g. "PI: 41.99 -> 44.99 | 10%". Only shown when the price
    # actually has to go UP; an order already at or above target needs no advice.
    _pi = data.get("pi_price")
    _cur = data.get("item_price")
    if _pi is not None and _cur is not None and _pi > _cur:
        lines.append(f"PI: {_cur:.2f} -> {_pi:.2f} | {data.get('pi_target', 0.10):.0%}")

    # C (Coupon) — written for placed orders (which carry the "coupon" key).
    # Used → show the $ off when known: "C: Used ( $2.00 )". Reseller/credit-card
    # orders (no coupon) → "C: Not Used". Non-placed notes (e.g. loss) omit it.
    if "coupon" in data:
        if data.get("coupon"):
            coupon_off = data.get("coupon_off")
            # Tag which coupon was used with its first 3 letters, e.g. "- EMZ" / "- STO".
            code = str(data.get("coupon_code") or "").strip()
            suffix = f" - {code[:3].upper()}" if code and code.upper() != "AUTO_APPLIED" else ""
            if coupon_off:
                lines.append(f"C: Used ( ${coupon_off:.2f} ){suffix}")
            else:
                lines.append(f"C: Used{suffix}")
        else:
            lines.append("C: Not Used")

    body = "\n".join(lines)

    # Prepend the title on top, truncated to the room left under the 256-char cap
    # (after the "Title: " prefix and the newline that joins it to the body).
    title = str(data.get("title") or "").strip()
    if title and title not in ("N/A", "None"):
        prefix = "Title: "
        budget = _MAX_NOTE_CHARS - len(prefix) - len("\n") - len(body)
        if budget > 0:
            title = title[:budget].rstrip()
            if title:
                return f"{prefix}{title}\n{body}"

    return body
