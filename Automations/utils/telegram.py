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


def format_boss_note_text(data: dict) -> str:
    """Format the exact 'Copy for boss' template."""
    title = str(data.get("title") or "").strip()
    asin = str(data.get("asin") or "N/A").strip()
    sku = str(data.get("sku") or "N/A").strip()
    code = str(data.get("code") or data.get("zoro_sku") or "N/A").strip()
    if code.startswith("G"):
        code = "K" + code[1:]
    if data.get("alt_used") and " - ALT" not in code:
        code += " - ALT"
        
    qty = data.get("qty", 1)
    tracking = data.get("tracking_sent", "No")
    
    tc = data.get("tc") or data.get("zoro_price")
    tc_str = f"${float(tc):.2f}" if tc is not None and str(tc).strip() != "" else "N/A"
    
    tax = data.get("tax") or data.get("zoro_tax")
    tax_str = f"${float(tax):.2f}" if tax is not None and str(tax).strip() != "" else "$0.00"
    
    earn = data.get("earnings")
    earn_str = f"${float(earn):.2f}" if earn is not None and str(earn).strip() != "" else "N/A"
    
    pm = data.get("pm") or data.get("profit")
    pm_str = f"${float(pm):.2f}" if pm is not None and str(pm).strip() != "" else "N/A"
    
    pp = data.get("pp")
    if pp is None and tc and pm:
        try:
            pp = (float(pm) / float(tc)) * 100.0
        except ZeroDivisionError:
            pp = None
    pp_str = f"{float(pp):.1f}%" if pp is not None else "N/A"
    
    # Coupon string
    cu = str(data.get("coupon_code") or data.get("coupon") or "").strip()
    c_off = data.get("coupon_off")
    if cu and cu not in ("None", "N/A", "False", ""):
        sfx = f" - {cu[:3].upper()}" if cu.upper() != "AUTO_APPLIED" else ""
        if c_off:
            c_str = f"Used ( ${float(c_off):.2f} ){sfx}"
        else:
            c_str = f"Used{sfx}"
    else:
        c_str = "Not Used"

    lines = []
    if title and title not in ("N/A", "None"):
        lines.append(f"Title: {title[:60]}")
    lines.append(f"ASIN: {asin}")
    if sku and sku not in ("N/A", "None"):
        lines.append(f"SKU: {sku}")
    lines.append(f"Zoro Code Used: {code}")
    lines.append(f"Qty: {qty}")
    lines.append(f"Tracking msg sent: {tracking}")
    lines.append(f"Total Zoro Cost: {tc_str}")
    lines.append(f"Zoro Tax: {tax_str}")
    lines.append(f"Amazon Earnings: {earn_str}")
    lines.append(f"Profit Margin: {pm_str}")
    lines.append(f"Profit Percentage: {pp_str}")
    lines.append(f"Coupon: {c_str}")
    lines.append("\nComments: ")
    
    return "\n".join(lines)


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

    if pp is not None and margin_decimal < 0.08:
        status_header = f"⚠️ <b>LOW MARGIN ORDER (&lt; 8%): {pp_display}</b>"
        log_msg = f"  📱 [Telegram] Low margin order (< 8%: {pp_display}) — sending Amazon screenshot + Copy for Boss in background..."
    elif pp is not None and margin_decimal >= 0.50:
        status_header = f"🚀 <b>MEGA PROFIT ORDER (&gt; 50%): +{pp_display}!</b> 💰"
        log_msg = f"  📱 [Telegram] Mega profit order (> 50%: {pp_display}) — sending Amazon screenshot + celebration details in background..."
    else:
        status_header = f"✅ <b>ORDER PLACED: +{pp_display}</b> 🟢"
        log_msg = f"  📱 [Telegram] Order placed (+{pp_display}) — sending Amazon screenshot + order details in background..."

    print(log_msg)

    # Pure note text block only
    caption = f"<pre>{html.escape(boss_text)}</pre>"
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
                               earnings: float, cost: float, margin: float,
                               reason: str = "", min_margin: float = None,
                               account: str = ""):
    """Alert when an order is skipped/rejected due to negative or thin margin."""
    pfx = "+" if (earnings - cost) >= 0 else ""
    dollar_diff = earnings - cost if (earnings is not None and cost is not None) else 0.0
    margin_pct = f"{margin * 100:.1f}%" if margin is not None else "N/A"
    thresh_pct = f"{min_margin * 100:.1f}%" if min_margin is not None else "N/A"

    if is_telegram_enabled():
        msg = (
            f"⛔ <b>ORDER REJECTED — LOW PROFIT</b>\n\n"
            f"<b>Title:</b> {html.escape(title[:70]) if title else 'N/A'}\n"
            f"<b>Amazon ID:</b> <code>{amazon_id}</code>\n"
            f"<b>ASIN:</b> <code>{asin}</code> | <b>SKU:</b> <code>{sku}</code>\n"
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
                f"*Account:* {account or 'ZUHRA Store'}\n"
                f"*Title:* {title[:70] if title else 'N/A'}\n"
                f"*Amazon ID:* {amazon_id}\n"
                f"*ASIN:* {asin} | *SKU:* {sku}\n"
                f"*Earnings:* ${earnings:.2f} | *Cost:* ${cost:.2f}\n"
                f"*Projected Profit:* {pfx}${dollar_diff:.2f} ({margin_pct})\n"
                f"*Threshold:* Min required {thresh_pct}\n"
                f"*Status:* Skipped / Marked {reason or 'LOSS'}"
            )
            send_whatsapp_message(wa_msg, to=target_wa_group, background=True)
    except Exception:
        pass


def notify_oos(amazon_id: str, title: str, asin: str, sku: str,
               zoro_code: str, account: str = "", driver=None, screenshot_bytes: bytes = None):
    """Alert when an item is genuine Out of Stock and marked on Amazon, with screenshot."""
    if screenshot_bytes is None and driver:
        try:
            screenshot_bytes = driver.get_screenshot_as_png()
        except Exception:
            pass

    if is_telegram_enabled():
        msg = (
            f"🚨 <b>OUT OF STOCK (OOS) ALERT</b>\n\n"
            f"<b>Title:</b> {html.escape(title[:80]) if title else 'N/A'}\n"
            f"<b>ASIN:</b> <code>{asin}</code>\n"
            f"<b>SKU:</b> <code>{sku}</code>\n"
            f"<b>Zoro Code:</b> <code>{zoro_code}</code>\n"
            f"<b>Amazon Order:</b> <code>{amazon_id}</code>\n"
            f"<b>Account:</b> {account or 'ZUHRA'}\n"
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
                f"*Store:* {account or 'ZUHRA'}\n"
                f"*Title:* {title[:80] if title else 'N/A'}\n"
                f"*ASIN:* {asin}\n"
                f"*SKU:* {sku}\n"
                f"*Zoro Code:* {zoro_code}\n"
                f"*Amazon Order:* {amazon_id}\n"
                f"*Action:* Marked 'OOS' on Amazon Seller Central"
            )
            if screenshot_bytes:
                send_whatsapp_photo(screenshot_bytes, caption=wa_msg, to=target_wa_group, background=True)
            else:
                send_whatsapp_message(wa_msg, to=target_wa_group, background=True)
    except Exception:
        pass

