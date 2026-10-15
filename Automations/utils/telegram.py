"""
telegram.py — Mobile Alerts & Order Insights for ZUHRA Software via Telegram Bot API.

Delivers real-time notifications to Kamran's phone (@kamrandsbot):
  • Low-margin orders (< 8%): Full Amazon Seller Central page screenshot + "Copy for boss" text
  • Mega-profit orders (> 50%): Celebration alert with profit dollar, %, ASIN, and coupon
  • Normal orders (8% - 50%): Order placement confirmation
  • Low-profit rejections: Alert when an order is skipped due to thin/negative margin
  • Out of stock (OOS): Alert with item title, ASIN, SKU, Zoro code, and Amazon order ID

All notifications run in background daemon threads so the placer is never blocked,
and screenshot bytes are immediately wiped from RAM once transmitted.
"""

import os
import json
import time
import requests
import html
import threading
import gc

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_FILE = os.path.join(_SCRIPT_DIR, "bot_config.json")


def get_telegram_config() -> dict:
    """Read fresh telegram configuration from bot_config.json."""
    try:
        if os.path.exists(_CONFIG_FILE):
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                return cfg.get("telegram") or {}
    except Exception as e:
        print(f"  [Telegram] ⚠️ Config read error: {e}")
    return {}


def is_telegram_enabled() -> bool:
    """Check if telegram notifications are enabled."""
    cfg = get_telegram_config()
    return bool(cfg.get("enabled", False))


def _get_api_url(endpoint: str) -> str:
    """Build URL using Cloudflare worker proxy if configured, or direct telegram api."""
    cfg = get_telegram_config()
    token = cfg.get("bot_token", "").strip()
    worker = cfg.get("worker_url", "").strip().rstrip("/")
    base = worker if worker else "https://api.telegram.org"
    return f"{base}/bot{token}/{endpoint}"


def _do_send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Worker function to perform HTTP post for sendMessage."""
    if not is_telegram_enabled():
        return False
    cfg = get_telegram_config()
    chat_id = cfg.get("chat_id")
    if not chat_id:
        return False

    url = _get_api_url("sendMessage")
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=12)
        return resp.status_code == 200
    except Exception as e:
        print(f"  [Telegram] ⚠️ Send message error: {e}")
        return False


def send_telegram_message(text: str, parse_mode: str = "HTML", background: bool = True) -> bool:
    """Send a formatted text message to Telegram.
    Runs asynchronously in the background so the placer is never delayed."""
    if not is_telegram_enabled():
        return False
    if background:
        threading.Thread(target=_do_send_message, args=(text, parse_mode), daemon=True).start()
        return True
    return _do_send_message(text, parse_mode)


def _do_send_photo(photo_bytes: bytes, caption: str = "", parse_mode: str = "HTML", followup_message: str = None) -> bool:
    """Worker function to upload photo and immediately wipe memory."""
    if not is_telegram_enabled():
        return False
    cfg = get_telegram_config()
    chat_id = cfg.get("chat_id")
    if not chat_id or not photo_bytes:
        return False

    url = _get_api_url("sendPhoto")

    files = {"photo": ("amazon_order_details.png", photo_bytes, "image/png")}
    data = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": parse_mode,
    }
    try:
        resp = requests.post(url, data=data, files=files, timeout=25)
        ok = (resp.status_code == 200)
        if ok and followup_message:
            _do_send_message(followup_message, parse_mode=parse_mode)
        elif not ok:
            print(f"  [Telegram] ⚠️ Send photo returned HTTP {resp.status_code}: {resp.text}")
            full_text = f"{caption}\n\n{followup_message}" if followup_message else caption
            _do_send_message(full_text, parse_mode=parse_mode)
        return ok
    except Exception as e:
        print(f"  [Telegram] ⚠️ Send photo error: {e}")
        full_text = f"{caption}\n\n{followup_message}" if followup_message else caption
        if full_text:
            _do_send_message(full_text, parse_mode=parse_mode)
        return False
    finally:
        # Wipe image bytes and buffers from memory immediately once sent
        try:
            del files
            del data
            del photo_bytes
            gc.collect()
        except Exception:
            pass


def send_telegram_photo(photo_bytes: bytes, caption: str = "", parse_mode: str = "HTML", followup_message: str = None, background: bool = True) -> bool:
    """Send an image photo to Telegram in the background.
    Automatically deletes the photo bytes from local memory once sent."""
    if not is_telegram_enabled():
        return False
    if background:
        threading.Thread(target=_do_send_photo, args=(photo_bytes, caption, parse_mode, followup_message), daemon=True).start()
        return True
    return _do_send_photo(photo_bytes, caption, parse_mode, followup_message)


def _lookup_title_and_sku(asin: str) -> tuple:
    """Fallback lookup for product Title and SKU by ASIN from automations_unified.db."""
    if not asin or asin in ("N/A", "None", ""):
        return ("", "")
    try:
        import sqlite3
        auto_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db_path = os.path.join(auto_dir, "data", "automations_unified.db")
        if os.path.exists(db_path):
            with sqlite3.connect(db_path, timeout=5) as c:
                row = c.execute(
                    "SELECT title, sku FROM order_placer_orders WHERE UPPER(asin) = ? AND title IS NOT NULL ORDER BY id DESC LIMIT 1",
                    (asin.upper(),)
                ).fetchone()
                if row and (row[0] or row[1]):
                    return (str(row[0] or "").strip(), str(row[1] or "").strip())
                row = c.execute(
                    "SELECT title, sku FROM closing_orders WHERE UPPER(asin) = ? AND title IS NOT NULL ORDER BY id DESC LIMIT 1",
                    (asin.upper(),)
                ).fetchone()
                if row and (row[0] or row[1]):
                    return (str(row[0] or "").strip(), str(row[1] or "").strip())
    except Exception:
        pass
    return ("", "")


def _short_day(v):
    """'Thu, Oct 15, 2026 PDT' / '2026-10-15' / 'Oct 15, 2026| ZoroDeliverBy:' → 'Oct 15' ('' if unreadable)."""
    import re as _re
    from datetime import date as _date
    t = str(v or "").split("|")[0].strip()
    m = _re.search(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", t)
    if m:
        try:
            return _date(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%b %-d")
        except ValueError:
            return ""
    m = _re.search(r"([A-Z][a-z]{2})[a-z]*\.?\s+(\d{1,2})\b", t)
    return f"{m.group(1)} {int(m.group(2))}" if m else ""


def _money(v):
    try:
        return f"${float(v):.2f}" if v is not None and str(v).strip() != "" else "N/A"
    except (TypeError, ValueError):
        return "N/A"


def order_card(data: dict, markup: str = "plain", note: bool = False) -> str:
    """THE order layout — locked by the owner on 15 Oct 2026 for every order alert (WhatsApp,
    Telegram) and the Amazon seller note. scripts/test_alert_format.py holds the owner's exact
    sample; change the layout only together with that test.

        *🟢 PROFIT ORDER (+52.3%)*                     ← header, added by the alert senders
        114-5760875-7672210 { WB5894251624 }
        Flat Corrugated Cardboard Shipping Boxes 20"

        *SKU*: Hath_G1606970 | *ASIN*: B0GD8Y19SD
        *Zoro Code:* K1606970 (Qty: 1)

        *Zoro_Cost*: $29.30 (Tax $0.00) | *Amz_Earnings:* $44.62 (Shipping: $13.00)
        Profit: $15.32 (52.3%) | Coupon: None

        *Amazon:* [Ship by Oct 15 | Deliver by Oct 17 ] {*Second Day*} |
        *Zoro_Delivery*: Oct 19

    markup='wa' → WhatsApp bold (*…*); 'plain' → no asterisks (Telegram's copy block). note=True →
    the seller note: plain, no order-id line (the order page shows it), no blank lines, title cut to
    whatever Amazon's 256-character limit leaves."""
    wa = markup == "wa" and not note
    B = (lambda t: f"*{t}*") if wa else (lambda t: t)

    title = str(data.get("title") or "").strip()
    asin = str(data.get("asin") or "N/A").strip()
    sku = str(data.get("sku") or "").strip()
    code = str(data.get("code") or data.get("zoro_sku") or "N/A").strip()
    if code.startswith("G"):
        code = "K" + code[1:]
    if data.get("alt_used") and " - ALT" not in code:
        code += " - ALT"
    if not title or title in ("N/A", "None") or not sku or sku in ("N/A", "None"):
        try:
            db_title, db_sku = _lookup_title_and_sku(asin)
        except Exception:
            db_title, db_sku = "", ""
        title = title if title and title not in ("N/A", "None") else (db_title or "")
        sku = sku if sku and sku not in ("N/A", "None") else (db_sku or code)
    qty = data.get("qty", 1)

    tc = data.get("tc") if data.get("tc") is not None else data.get("zoro_price")
    tax = data.get("tax") if data.get("tax") is not None else data.get("zoro_tax")
    tax_str = _money(tax) if tax is not None and str(tax).strip() != "" else "$0.00"
    earn = data.get("earnings")
    pm = data.get("pm") if data.get("pm") is not None else data.get("profit")
    pp = data.get("pp")
    if pp is None and tc and pm is not None:
        try:
            pp = float(pm) / float(tc) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            pp = None
    pp_str = f"{float(pp):.1f}%" if pp is not None else "N/A"
    try:
        ship_amt = float(data.get("shipping_amt") or 0)
    except (TypeError, ValueError):
        ship_amt = 0.0

    cu = str(data.get("coupon_code") or (data.get("coupon") if isinstance(data.get("coupon"), str) else "") or "").strip()
    c_off = data.get("coupon_off")
    if cu and cu not in ("None", "N/A", "False", "0"):
        coupon = cu.upper() if cu.upper() != "AUTO_APPLIED" else "Auto"
        try:
            coupon += f" (-${float(c_off):.2f})" if c_off else ""
        except (TypeError, ValueError):
            pass
    else:
        coupon = "None"

    svc = str(data.get("ship_service") or "").strip() or "—"
    if svc != "—" and "standard" not in svc.lower():
        svc = B(svc) if wa else (svc.upper() if not note else svc)
    sb = _short_day(data.get("ship_by_date")) or "—"
    dl = _short_day(data.get("deliver_by_date")) or "—"
    zd = _short_day(data.get("zoro_deliver_by")) or "—"

    amazon_id = str(data.get("amazon_id") or data.get("amazon_order_id") or "").strip()
    wb_id = str(data.get("wb_id") or data.get("result_id") or "").strip()
    if wb_id and not wb_id.upper().startswith("WB"):
        wb_id = f"WB{wb_id}"
    head = (f"{amazon_id} {{ {wb_id} }}" if amazon_id and wb_id else amazon_id or (f"{{ {wb_id} }}" if wb_id else ""))
    short_title = (title or "Product Listing").split(" - ")[0].split(",")[0].strip()[:44].rstrip()

    body = [
        f"{B('SKU')}: {sku or 'N/A'} | {B('ASIN')}: {asin}",
        f"{B('Zoro Code:')} {code} (Qty: {qty})",
        "",
        f"{B('Zoro_Cost')}: {_money(tc)} (Tax {tax_str}) | {B('Amz_Earnings:')} {_money(earn)}"
        + (f" (Shipping: ${ship_amt:.2f})" if ship_amt > 0 else ""),
        f"Profit: {_money(pm)} ({pp_str}) | Coupon: {coupon}",
        "",
        f"{B('Amazon:')} [Ship by {sb} | Deliver by {dl} ] {{{svc}}} |",
        f"{B('Zoro_Delivery')}: {zd}",
    ]
    if not note:
        return "\n".join(([head] if head else []) + [short_title, ""] + body)

    # Seller note: Amazon keeps 256 characters and the layout above runs ~260 without the title.
    # Same lines, shortened step by step until it fits — the title (the order page shows it) goes
    # first, the dates line last: zero tax, the ASIN (also on the page), Zoro's date onto the
    # Amazon line, "Shipping:" → "Ship".
    lines = [x for x in body if x]
    steps = [
        lambda L: [x.replace(" (Tax $0.00)", "") for x in L],
        lambda L: [x.split(" | ASIN:")[0] if x.startswith("SKU:") else x for x in L],
        lambda L: L[:-2] + [L[-2] + " " + L[-1]],
        lambda L: [x.replace("(Shipping: ", "(Ship ") for x in L],
    ]
    for step in [None] + steps:
        if step:
            lines = step(lines)
        text = "\n".join(lines)
        if len(text) <= 256:
            room = 256 - len(text) - 1
            return (short_title[:room].rstrip() + "\n" + text) if room >= 20 else text
    return text[:256]


def format_boss_note_text(data: dict, markup: str = "plain") -> str:
    """Alert body (WhatsApp markup='wa', Telegram 'plain') — the locked order_card layout."""
    return order_card(data, markup=markup)


def notify_order_placed(order_data: dict, driver=None, account: str = ""):
    """Capture Amazon SC Order Details page screenshot and send with profit + 'Copy for boss' text.
    Dispatches in background thread and immediately cleans up memory once sent."""
    amazon_id = order_data.get("amazon_id", "")
    wb_id = order_data.get("wb_id", "WB Pending")
    asin = order_data.get("asin", "N/A")
    sku = order_data.get("sku", "N/A")
    cost = order_data.get("tc") or order_data.get("zoro_price")
    earnings = order_data.get("earnings")
    profit = order_data.get("pm") or order_data.get("profit")

    # If title is missing and driver is on Amazon page, attempt quick scrape
    if not order_data.get("title") and driver:
        try:
            dom_title = driver.execute_script(
                "var el = document.querySelector('a[href*=\"/dp/\"], a[href*=\"product\"], [data-testid*=\"title\" i]');"
                "return el ? el.innerText.trim() : '';"
            )
            if dom_title:
                order_data["title"] = dom_title
        except Exception:
            pass

    # Calculate margin % (ROI: profit / cost)
    pp = order_data.get("pp")
    if pp is None:
        try:
            if cost and profit is not None and float(cost) > 0:
                pp = (float(profit) / float(cost)) * 100.0
        except Exception:
            pp = None
    else:
        try:
            pp = float(pp)
        except (ValueError, TypeError):
            pp = None

    margin_decimal = (pp / 100.0) if pp is not None else 0.0

    pfx = "+" if profit and float(profit) >= 0 else ""
    pp_display = f"{pp:.1f}%" if pp is not None else "N/A"
    profit_display = f"{pfx}${float(profit):.2f}" if profit is not None and str(profit).strip() != "" else "N/A"
    cost_display = f"${float(cost):.2f}" if cost is not None and str(cost).strip() != "" else "N/A"
    earnings_display = f"${float(earnings):.2f}" if earnings is not None and str(earnings).strip() != "" else "N/A"

    boss_text = format_boss_note_text(order_data)

    tax_val = float(order_data.get("tax") or order_data.get("zoro_tax") or 0.0)
    state = str(
        order_data.get("state")
        or order_data.get("ship_state")
        or (order_data.get("address", {}).get("state") if isinstance(order_data.get("address"), dict) else "")
        or ""
    ).strip().upper()
    state_str = f"({state})" if state else ""

    if pp is not None and margin_decimal < 0.08:
        if tax_val > 0:
            status_header = f"🔴 <b>Taxed{state_str} Low Margin Order (&lt; 8%): {pp_display}</b>"
            log_msg = f"  📱 [Telegram] Taxed{state_str} low margin order (< 8%: {pp_display}) — sending Amazon screenshot + Copy for Boss in background..."
        else:
            status_header = f"🔴 <b>LOW MARGIN ORDER (&lt; 8%): {pp_display}</b>"
            log_msg = f"  📱 [Telegram] Low margin order (< 8%: {pp_display}) — sending Amazon screenshot + Copy for Boss in background..."
    elif pp is not None and margin_decimal <= 0.20:
        status_header = f"<b>🟡 ORDER PLACED ({pfx}{pp_display})</b>"
        log_msg = f"  📱 [Telegram] Order placed ({pfx}{pp_display}) — sending Amazon screenshot + order details in background..."
    else:
        status_header = f"<b>🟢 PROFIT ORDER (+{pp_display})</b>"
        log_msg = f"  📱 [Telegram] Profit order (+{pp_display}) — sending Amazon screenshot + details in background..."

    if not driver:      # rush mode passes no driver: the alert goes without a screenshot
        log_msg = log_msg.replace("Amazon screenshot + ", "").replace("in background...", "in background (text only)...")
    print(log_msg)

    # Status header + 1-tap copyable preformatted note block
    caption = f"{status_header}\n\n<pre>{html.escape(boss_text)}</pre>"
    followup = None

    screenshot_bytes = None
    if driver:
        try:
            # Scroll to top so the header (Order ID, WB label), Order summary, and Seller Notes are in full view
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(0.35)
            screenshot_bytes = driver.get_screenshot_as_png()
        except Exception as se:
            print(f"  [Telegram] ⚠️ Could not capture screenshot: {se}")

    # Dispatch to Telegram if enabled
    if is_telegram_enabled():
        if screenshot_bytes:
            send_telegram_photo(screenshot_bytes, caption=caption, followup_message=followup, background=True)
        else:
            full_msg = f"{caption}\n\n{followup}" if followup else caption
            send_telegram_message(full_msg, background=True)

    # Dispatch to WhatsApp if enabled (ALL orders, routed to HOANK vs Hathoro Orders Alerts group)
    try:
        from utils.whatsapp import is_whatsapp_enabled, notify_order_placed_wa
        if is_whatsapp_enabled():
            notify_order_placed_wa(order_data, screenshot_bytes=screenshot_bytes, account=account)
    except Exception as _wae:
        print(f"  [WhatsApp] ⚠️ Notification error: {_wae}")

    if screenshot_bytes:
        del screenshot_bytes


def notify_low_profit_rejected(amazon_id: str, title: str, asin: str, sku: str,
                               cost: float, earnings: float, min_margin: float,
                               reason: str = "", account: str = ""):
    """Send immediate notification when an order is skipped/marked Loss-Kamran."""
    diff = earnings - cost
    pct = (diff / cost) * 100.0 if cost > 0 else 0.0
    pfx = "+" if diff >= 0 else ""
    margin_pct = f"{pfx}{pct:.1f}%"
    dollar_diff = abs(diff)
    thresh_pct = f"{min_margin * 100.0:.0f}%"

    title = str(title or "").strip()
    sku = str(sku or "").strip()
    if (not title or not sku or sku in ("N/A", "None", "")) and asin:
        db_t, db_s = _lookup_title_and_sku(asin)
        title = title or db_t
        sku = sku or db_s

    if is_telegram_enabled():
        msg = (
            f"⛔ <b>ORDER REJECTED — LOW PROFIT</b>\n\n"
            f"<b>Title:</b> {html.escape(title[:80]) if title else 'Product Listing'}\n"
            f"<b>SKU:</b> <code>{sku if sku else 'N/A'}</code>\n"
            f"<b>ASIN:</b> <code>{asin}</code>\n"
            f"<b>Amazon ID:</b> <code>{amazon_id}</code>\n\n"
            f"<b>Earnings:</b> ${earnings:.2f} | <b>Cost:</b> ${cost:.2f}\n"
            f"<b>Projected Profit:</b> {pfx}${dollar_diff:.2f} ({margin_pct})\n"
            f"<b>Threshold:</b> Min required {thresh_pct}\n"
            f"<b>Status:</b> Skipped / Marked {reason or 'LOSS'}"
        )
        send_telegram_message(msg, background=True)

    try:
        from utils.whatsapp import is_whatsapp_enabled, get_whatsapp_group, send_whatsapp_message
        if is_whatsapp_enabled():
            target_wa_group = get_whatsapp_group(account, kind="orders")
            wa_msg = (
                f"⛔ *ORDER REJECTED — LOW PROFIT*\n\n"
                f"• Title: *{title[:80] if title else 'Product Listing'}*\n"
                f"• SKU: `{sku if sku else 'N/A'}`\n"
                f"• ASIN: `{asin}`\n"
                f"• Order: `{amazon_id}`\n\n"
                f"• Projected Profit: *{pfx}${dollar_diff:.2f}* ({margin_pct})\n"
                f"• Earnings / Cost:  ${earnings:.2f} / ${cost:.2f}\n"
                f"• Threshold:        Min required {thresh_pct}\n"
                f"• Status:           Skipped / Marked {reason or 'LOSS'}"
            )
            send_whatsapp_message(wa_msg, to=target_wa_group, background=True)
    except Exception:
        pass


def notify_oos(amazon_id: str, title: str, asin: str, sku: str,
               zoro_code: str, account: str = "", driver=None, screenshot_bytes: bytes = None):
    """Alert when an item is genuine Out of Stock and marked on Amazon, with screenshot."""
    title = str(title or "").strip()
    sku = str(sku or "").strip()
    if (not title or not sku or sku in ("N/A", "None", "")) and asin:
        db_t, db_s = _lookup_title_and_sku(asin)
        title = title or db_t
        sku = sku or db_s or zoro_code

    if screenshot_bytes is None and driver:
        try:
            screenshot_bytes = driver.get_screenshot_as_png()
        except Exception:
            pass

    if is_telegram_enabled():
        msg = (
            f"🚨 <b>OUT OF STOCK (OOS) ALERT</b>\n\n"
            f"<b>Title:</b> {html.escape(title[:80]) if title else 'Product Listing'}\n"
            f"<b>SKU:</b> <code>{sku if sku else 'N/A'}</code>\n"
            f"<b>ASIN:</b> <code>{asin}</code>\n"
            f"<b>Zoro Code:</b> <code>{zoro_code}</code>\n"
            f"<b>Amazon Order:</b> <code>{amazon_id}</code>\n\n"
            f"<b>Action:</b> Marked 'OOS' on Amazon Seller Central"
        )
        if screenshot_bytes:
            send_telegram_photo(screenshot_bytes, caption=msg, background=True)
        else:
            send_telegram_message(msg, background=True)

    try:
        from utils.whatsapp import is_whatsapp_enabled, get_whatsapp_group, send_whatsapp_photo, send_whatsapp_message
        if is_whatsapp_enabled():
            target_wa_group = get_whatsapp_group(account, kind="orders")
            wa_msg = (
                f"🚨 *OUT OF STOCK (OOS) ALERT*\n\n"
                f"• Title: *{title[:80] if title else 'Product Listing'}*\n"
                f"• SKU: `{sku if sku else 'N/A'}`\n"
                f"• ASIN: `{asin}`\n"
                f"• Zoro Code: `{zoro_code}`\n"
                f"• Order: `{amazon_id}`\n\n"
                f"• Action: Marked 'OOS' on Amazon Seller Central"
            )
            if screenshot_bytes:
                send_whatsapp_photo(screenshot_bytes, caption=wa_msg, to=target_wa_group, background=True)
            else:
                send_whatsapp_message(wa_msg, to=target_wa_group, background=True)
    except Exception:
        pass

