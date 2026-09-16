"""
whatsapp.py — Self-Hosted WhatsApp Alerts & Insights for ZUHRA Software.

Connects to local Baileys WhatsApp Gateway (http://127.0.0.1:3005):
  • Dispatches live Amazon Seller Central page screenshots + "Copy for boss" text
  • Sends OOS alerts with full item title, ASIN, and Zoro code
  • Sends low-margin warnings & mega-profit insights directly to your WhatsApp

All network operations run in background daemon threads, and image bytes are
immediately deleted from RAM upon transmission with zero disk files saved.
"""

import os
import json
import base64
import requests
import threading
import subprocess
import time
import gc

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_FILE = os.path.join(_SCRIPT_DIR, "bot_config.json")


def get_whatsapp_config() -> dict:
    """Read fresh WhatsApp configuration from bot_config.json."""
    try:
        if os.path.exists(_CONFIG_FILE):
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                return cfg.get("whatsapp") or {}
    except Exception as e:
        print(f"  [WhatsApp] ⚠️ Config read error: {e}")
    return {}


def is_whatsapp_enabled() -> bool:
    """Check if WhatsApp notifications are enabled."""
    cfg = get_whatsapp_config()
    return bool(cfg.get("enabled", False))


def ensure_gateway_running() -> bool:
    """Ensure local Baileys gateway is alive. Spawns it if stopped."""
    cfg = get_whatsapp_config()
    base_url = cfg.get("server_url", "http://127.0.0.1:3005").rstrip("/")
    try:
        resp = requests.get(f"{base_url}/status", timeout=1.5)
        if resp.status_code == 200:
            return True
    except Exception:
        pass

    # Gateway process is down — launch in background
    gw_dir = os.path.join(_SCRIPT_DIR, "whatsapp_gateway")
    server_js = os.path.join(gw_dir, "server.js")
    if os.path.exists(server_js):
        try:
            subprocess.Popen(
                ["node", server_js],
                cwd=gw_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            time.sleep(1.5)
            return True
        except Exception as e:
            print(f"  [WhatsApp] ⚠️ Auto-start gateway error: {e}")
    return False


def get_gateway_status() -> dict:
    """Check connection status of local WhatsApp gateway."""
    cfg = get_whatsapp_config()
    base_url = cfg.get("server_url", "http://127.0.0.1:3005").rstrip("/")
    try:
        resp = requests.get(f"{base_url}/status", timeout=2)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        if ensure_gateway_running():
            try:
                resp = requests.get(f"{base_url}/status", timeout=2)
                if resp.status_code == 200:
                    return resp.json()
            except Exception:
                pass
    return {"connected": False, "phone": None, "has_qr": False}


def _do_send_message(text: str, to: str = None) -> bool:
    """Worker function to send text message via local gateway."""
    if not is_whatsapp_enabled():
        return False
    cfg = get_whatsapp_config()
    base_url = cfg.get("server_url", "http://127.0.0.1:3005").rstrip("/")
    target = to or cfg.get("recipient_phone") or ""

    payload = {
        "to": target,
        "text": text,
    }
    try:
        resp = requests.post(f"{base_url}/send", json=payload, timeout=15)
        return resp.status_code == 200
    except requests.exceptions.ConnectionError:
        if ensure_gateway_running():
            try:
                resp = requests.post(f"{base_url}/send", json=payload, timeout=15)
                return resp.status_code == 200
            except Exception:
                pass
        return False
    except Exception as e:
        print(f"  [WhatsApp] ⚠️ Send text error: {e}")
        return False


def send_whatsapp_message(text: str, to: str = None, background: bool = True) -> bool:
    """Send a text message to WhatsApp asynchronously in the background."""
    if not is_whatsapp_enabled():
        return False
    if background:
        threading.Thread(target=_do_send_message, args=(text, to), daemon=True).start()
        return True
    return _do_send_message(text, to)


def _do_send_photo(photo_bytes: bytes, caption: str = "", to: str = None) -> bool:
    """Worker function to send image photo and immediately wipe memory."""
    if not is_whatsapp_enabled() or not photo_bytes:
        return False
    cfg = get_whatsapp_config()
    base_url = cfg.get("server_url", "http://127.0.0.1:3005").rstrip("/")
    target = to or cfg.get("recipient_phone") or ""

    try:
        b64_str = base64.b64encode(photo_bytes).decode("utf-8")
        payload = {
            "to": target,
            "image_base64": b64_str,
            "caption": caption,
        }
        try:
            resp = requests.post(f"{base_url}/send", json=payload, timeout=25)
            status_ok = (resp.status_code == 200)
        except requests.exceptions.ConnectionError:
            if ensure_gateway_running():
                resp = requests.post(f"{base_url}/send", json=payload, timeout=25)
                status_ok = (resp.status_code == 200)
            else:
                status_ok = False
        del b64_str
        del payload
        return status_ok
    except Exception as e:
        print(f"  [WhatsApp] ⚠️ Send photo error: {e}")
        if caption:
            _do_send_message(caption, to=to)
        return False
    finally:
        try:
            del photo_bytes
            gc.collect()
        except Exception:
            pass


def send_whatsapp_photo(photo_bytes: bytes, caption: str = "", to: str = None, background: bool = True) -> bool:
    """Send an image photo to WhatsApp in the background.
    Automatically deletes the photo bytes from local memory once sent."""
    if not is_whatsapp_enabled():
        return False
    if background:
        threading.Thread(target=_do_send_photo, args=(photo_bytes, caption, to), daemon=True).start()
        return True
    return _do_send_photo(photo_bytes, caption, to)


# ══════════════════════════════════════════════════════════════════════
#  HIGH-LEVEL BUSINESS ALERTS & NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════════

def notify_order_oos(order: dict, screenshot_bytes: bytes = None) -> bool:
    """Send out of stock alert with product photo/screenshot."""
    amz_id = order.get("amazon_id") or order.get("amazon_order_id") or "N/A"
    asin = order.get("asin") or "N/A"
    sku = order.get("sku") or order.get("zoro_code") or "N/A"
    qty = order.get("qty") or order.get("quantity") or 1
    title = (order.get("title") or order.get("item_title") or asin)[:60]
    profile = order.get("profile") or order.get("profile_name") or ""
    buyer = order.get("buyer") or ""

    caption = (
        f"🚨 *OUT OF STOCK ALERT (OOS)*\n"
        f"• Store Profile : {profile or 'Active Store'}\n"
        f"• Amazon Order  : `{amz_id}`\n"
        f"• ASIN          : `{asin}`\n"
        f"• Zoro Code     : `{sku}`\n"
        f"• Title         : {title}...\n"
        f"• Order Qty     : {qty}\n"
        f"• Buyer         : {buyer}\n"
        f"• Action        : Supplier is OOS — check alternatives or notify customer."
    )
    if screenshot_bytes:
        return send_whatsapp_photo(screenshot_bytes, caption=caption)
    return send_whatsapp_message(caption)


def notify_low_margin(order: dict, margin_pct: float, screenshot_bytes: bytes = None) -> bool:
    """Send low-margin warning alert (< 8.0% pre-tax) with order screenshot."""
    amz_id = order.get("amazon_id") or order.get("amazon_order_id") or "N/A"
    asin = order.get("asin") or "N/A"
    sku = order.get("sku") or "N/A"
    title = (order.get("title") or asin)[:60]
    profile = order.get("profile") or ""
    payout = float(order.get("earnings") or 0.0)
    cost = float(order.get("cost") or order.get("zoro_price") or 0.0)
    profit = float(order.get("profit") or (payout - cost))
    coupon = order.get("coupon_used") or order.get("coupon") or "None"

    caption = (
        f"⚠️ *LOW PROFIT MARGIN WARNING (< 8%)*\n"
        f"• Store Profile : {profile or 'Active Store'}\n"
        f"• Amazon Order  : `{amz_id}`\n"
        f"• ASIN          : `{asin}`\n"
        f"• Zoro Code     : `{sku}`\n"
        f"• Title         : {title}...\n"
        f"• Margin        : *{margin_pct:.1f}%* (Pre-tax verified)\n"
        f"• Profit        : *+${profit:.2f}*\n"
        f"• Payout        : ${payout:.2f} | Zoro Cost: ${cost:.2f}\n"
        f"• Coupon        : {coupon}\n"
        f"• Note          : Thin profit margin — review selling price."
    )
    if screenshot_bytes:
        return send_whatsapp_photo(screenshot_bytes, caption=caption)
    return send_whatsapp_message(caption)


def notify_high_profit(order: dict, margin_pct: float, screenshot_bytes: bytes = None) -> bool:
    """Send high-profit celebration alert (> 30.0%) with order screenshot."""
    amz_id = order.get("amazon_id") or order.get("amazon_order_id") or "N/A"
    asin = order.get("asin") or "N/A"
    sku = order.get("sku") or "N/A"
    title = (order.get("title") or asin)[:60]
    profile = order.get("profile") or ""
    payout = float(order.get("earnings") or 0.0)
    cost = float(order.get("cost") or order.get("zoro_price") or 0.0)
    profit = float(order.get("profit") or (payout - cost))
    coupon = order.get("coupon_used") or order.get("coupon") or "None"

    caption = (
        f"🎉 *MEGA PROFIT ORDER (> 30%)*\n"
        f"• Store Profile : {profile or 'Active Store'}\n"
        f"• Amazon Order  : `{amz_id}`\n"
        f"• ASIN          : `{asin}`\n"
        f"• Zoro Code     : `{sku}`\n"
        f"• Title         : {title}...\n"
        f"• Margin        : *+{margin_pct:.1f}%* 🔥\n"
        f"• Profit        : *+${profit:.2f}* 💵\n"
        f"• Payout        : ${payout:.2f} | Cost: ${cost:.2f}\n"
        f"• Coupon Used   : {coupon}"
    )
    if screenshot_bytes:
        return send_whatsapp_photo(screenshot_bytes, caption=caption)
    return send_whatsapp_message(caption)


def notify_insight(insight: dict) -> bool:
    """Send an operational insight alert text to WhatsApp."""
    summary = insight.get("summary") or ""
    if not summary:
        return False
    return send_whatsapp_message(summary)


def notify_hijack(details: dict, screenshot_bytes: bytes = None) -> bool:
    """Send listing hijack alert when our rank is #2 or #3."""
    store = details.get("store") or "Amazon Store"
    asin = details.get("asin") or "N/A"
    title = (details.get("title") or "Tracked Product")[:60]
    our_rank = details.get("our_rank") or "2"
    winner_name = details.get("winner_name") or "Competitor"
    winner_price = details.get("winner_price") or "N/A"
    our_price = details.get("our_price") or "N/A"

    caption = (
        f"🚨 *LISTING HIJACK ALERT: Position Dropped!*\n"
        f"• Store       : {store}\n"
        f"• ASIN        : `{asin}`\n"
        f"• Title       : {title}...\n"
        f"• Our Rank    : *#{our_rank}* (Lost Buy Box!)\n"
        f"• Buy Box Won : {winner_name} ({winner_price})\n"
        f"• Our Price   : {our_price}\n"
        f"• Action      : Check Repricer / Inventory immediately."
    )
    if screenshot_bytes:
        return send_whatsapp_photo(screenshot_bytes, caption=caption)
    return send_whatsapp_message(caption)

