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

DEFAULT_GROUPS = {
    "hoank_orders": "120363410664445868@g.us",
    "hoank_insights": "120363429340767368@g.us",
    "hathoro_orders": "120363412233188303@g.us",
    "hathoro_insights": "120363413450875187@g.us",
}


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


def get_whatsapp_group(account_or_profile: str = "", kind: str = "orders") -> str:
    """Resolve target WhatsApp group JID based on store account and channel ('orders' vs 'insights').

    Channels:
      • HOANK Orders Alerts   -> 120363410664445868@g.us
      • HOANK Insights        -> 120363429340767368@g.us
      • Hathoro Orders Alerts -> 120363412233188303@g.us
      • Hathoro Insights      -> 120363413450875187@g.us
    """
    cfg = get_whatsapp_config()
    groups = cfg.get("groups") or DEFAULT_GROUPS

    acc = str(account_or_profile or "").strip().lower()
    if any(k in acc for k in ("hathoro", "dm_", "dm-", "abecom", "abe")):
        key = f"hathoro_{kind}"
    else:
        key = f"hoank_{kind}"

    return groups.get(key) or DEFAULT_GROUPS.get(key) or cfg.get("recipient_phone") or ""


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

def notify_order_placed_wa(order_data: dict, screenshot_bytes: bytes = None, account: str = "") -> bool:
    """Send live order placement alert to WhatsApp (ALL orders, no margin suppression).

    Routes directly to:
      • HOANK Orders Alerts   (120363410664445868@g.us) for HOANK orders
      • Hathoro Orders Alerts (120363412233188303@g.us) for Hathoro orders
    """
    if not is_whatsapp_enabled():
        return False

    acc = account or order_data.get("account") or order_data.get("profile") or order_data.get("profile_name") or ""
    target_group = get_whatsapp_group(acc, kind="orders")

    cost = order_data.get("tc") or order_data.get("zoro_price") or order_data.get("cost")
    earnings = order_data.get("earnings")
    profit = order_data.get("pm") or order_data.get("profit")

    try:
        cost_val = float(cost) if cost is not None and str(cost).strip() != "" else 0.0
    except (ValueError, TypeError):
        cost_val = 0.0

    try:
        earn_val = float(earnings) if earnings is not None and str(earnings).strip() != "" else 0.0
    except (ValueError, TypeError):
        earn_val = 0.0

    if profit is not None and str(profit).strip() != "":
        try:
            profit_val = float(profit)
        except (ValueError, TypeError):
            profit_val = earn_val - cost_val
    else:
        profit_val = earn_val - cost_val

    tax_val = float(order_data.get("tax") or order_data.get("zoro_tax") or 0.0)
    cost_no_tax = max(0.01, cost_val - tax_val)
    margin_no_tax = ((earn_val - cost_no_tax) / cost_no_tax) if (earn_val and cost_no_tax > 0) else None

    pp = order_data.get("pp")
    if pp is None and cost_val > 0:
        pp = (profit_val / cost_val) * 100.0
    elif pp is not None:
        try:
            pp = float(pp)
        except (ValueError, TypeError):
            pp = 0.0

    margin_decimal = (pp / 100.0) if pp is not None else 0.0
    pp_display = f"{pp:.1f}%" if pp is not None else "N/A"
    pfx = "+" if pp is not None and pp >= 0 else ""

    is_low_margin = (margin_decimal < 0.08) or (margin_no_tax is not None and margin_no_tax < 0.08)

    state = str(
        order_data.get("state")
        or order_data.get("ship_state")
        or (order_data.get("address", {}).get("state") if isinstance(order_data.get("address"), dict) else "")
        or ""
    ).strip().upper()
    state_str = f"({state})" if state else ""

    if is_low_margin:
        if tax_val > 0:
            wa_header = f"🔴 *Taxed{state_str} Low Margin Order (< 8%): {pp_display}*"
        else:
            wa_header = f"🔴 *LOW MARGIN ORDER (< 8%): {pp_display}*"
    elif margin_decimal <= 0.20:
        wa_header = f"🟡 *ORDER PLACED ({pfx}{pp_display})*"
    else:
        wa_header = f"🟢 *PROFIT ORDER (+{pp_display})*"

    try:
        from utils.telegram import format_boss_note_text
        boss_text = format_boss_note_text(order_data)
    except Exception:
        asin_val = order_data.get("asin", "N/A")
        try:
            from utils.telegram import _lookup_title_and_sku
            db_title, db_sku = _lookup_title_and_sku(asin_val)
        except Exception:
            db_title, db_sku = "", ""
        order_line = f"{order_data.get('amazon_id', 'N/A')} {{ {order_data.get('wb_id', 'N/A')} }}"
        boss_text = (
            f"{order_line}\n"
            f"Title: {order_data.get('title') or db_title or 'Product Listing'}\n"
            f"SKU: {order_data.get('sku') or db_sku or 'N/A'} | ASIN: {asin_val}\n"
            f"Earnings: ${earn_val:.2f} | Cost: ${cost_val:.2f} | Profit: ${profit_val:.2f} ({pp_display})"
        )

    caption = f"{wa_header}\n{boss_text}"

    if screenshot_bytes:
        return send_whatsapp_photo(screenshot_bytes, caption=caption, to=target_group, background=True)
    return send_whatsapp_message(caption, to=target_group, background=True)


def notify_order_oos(order: dict, screenshot_bytes: bytes = None, account: str = "") -> bool:
    """Send out of stock alert with product photo/screenshot to the store's Orders Alerts group."""
    amz_id = order.get("amazon_id") or order.get("amazon_order_id") or "N/A"
    asin = order.get("asin") or "N/A"
    sku = order.get("sku") or order.get("zoro_code") or ""
    qty = order.get("qty") or order.get("quantity") or 1
    title = str(order.get("title") or order.get("item_title") or "").strip()
    profile = account or order.get("profile") or order.get("profile_name") or ""
    buyer = order.get("buyer") or ""
    target_group = get_whatsapp_group(profile, kind="orders")

    if (not title or not sku or sku in ("N/A", "None", "")) and asin:
        try:
            from utils.telegram import _lookup_title_and_sku
            db_t, db_s = _lookup_title_and_sku(asin)
            title = title or db_t
            sku = sku or db_s
        except Exception:
            pass

    caption = (
        f"🚨 *OUT OF STOCK ALERT (OOS)*\n\n"
        f"• Title: *{title[:80] if title else 'Product Listing'}*\n"
        f"• SKU: `{sku if sku else 'N/A'}`\n"
        f"• ASIN: `{asin}`\n"
        f"• Zoro Code: `{order.get('zoro_code') or sku}`\n"
        f"• Order: `{amz_id}`\n\n"
        f"• Quantity: {qty}\n"
        f"• Buyer: {buyer}\n"
        f"• Action: Supplier is OOS — marked on Amazon SC, manual review required"
    )
    if screenshot_bytes:
        return send_whatsapp_photo(screenshot_bytes, caption=caption, to=target_group)
    return send_whatsapp_message(caption, to=target_group)


def notify_low_margin(order: dict, margin_pct: float, screenshot_bytes: bytes = None, account: str = "") -> bool:
    """Send low-margin warning alert (< 8.0% pre-tax) with order screenshot to Orders Alerts group."""
    amz_id = order.get("amazon_id") or order.get("amazon_order_id") or "N/A"
    asin = order.get("asin") or "N/A"
    sku = order.get("sku") or order.get("zoro_code") or ""
    title = str(order.get("title") or order.get("item_title") or "").strip()
    profile = account or order.get("profile") or order.get("profile_name") or ""
    target_group = get_whatsapp_group(profile, kind="orders")

    if (not title or not sku or sku in ("N/A", "None", "")) and asin:
        try:
            from utils.telegram import _lookup_title_and_sku
            db_t, db_s = _lookup_title_and_sku(asin)
            title = title or db_t
            sku = sku or db_s
        except Exception:
            pass

    payout = float(order.get("earnings") or 0.0)
    cost = float(order.get("cost") or order.get("zoro_price") or 0.0)
    profit = float(order.get("profit") or (payout - cost))
    coupon = order.get("coupon_used") or order.get("coupon") or "None"

    caption = (
        f"⚠️ *LOW PROFIT MARGIN WARNING (< 8%)*\n\n"
        f"• Title: *{title[:80] if title else 'Product Listing'}*\n"
        f"• SKU: `{sku if sku else 'N/A'}`\n"
        f"• ASIN: `{asin}`\n"
        f"• Order: `{amz_id}`\n\n"
        f"• Profit Margin: *{margin_pct:.1f}%* (Threshold: 8.0%)\n"
        f"• Net Profit:    *+${profit:.2f}*\n"
        f"• Payout / Cost: ${payout:.2f} / ${cost:.2f}\n"
        f"• Coupon:        {coupon}\n"
        f"• Status:        Placed (review pricing)"
    )
    if screenshot_bytes:
        return send_whatsapp_photo(screenshot_bytes, caption=caption, to=target_group)
    return send_whatsapp_message(caption, to=target_group)


def notify_high_profit(order: dict, margin_pct: float, screenshot_bytes: bytes = None, account: str = "") -> bool:
    """Send high-profit celebration alert (> 20.0%) with order screenshot to Orders Alerts group."""
    amz_id = order.get("amazon_id") or order.get("amazon_order_id") or "N/A"
    asin = order.get("asin") or "N/A"
    sku = order.get("sku") or order.get("zoro_code") or ""
    title = str(order.get("title") or order.get("item_title") or "").strip()
    profile = account or order.get("profile") or order.get("profile_name") or ""
    target_group = get_whatsapp_group(profile, kind="orders")

    if (not title or not sku or sku in ("N/A", "None", "")) and asin:
        try:
            from utils.telegram import _lookup_title_and_sku
            db_t, db_s = _lookup_title_and_sku(asin)
            title = title or db_t
            sku = sku or db_s
        except Exception:
            pass

    payout = float(order.get("earnings") or 0.0)
    cost = float(order.get("cost") or order.get("zoro_price") or 0.0)
    profit = float(order.get("profit") or (payout - cost))
    coupon = order.get("coupon_used") or order.get("coupon") or "None"

    caption = (
        f"🎉 *HIGH PROFIT ORDER (> 20%)* 🔥\n\n"
        f"• Title: *{title[:80] if title else 'Product Listing'}*\n"
        f"• SKU: `{sku if sku else 'N/A'}`\n"
        f"• ASIN: `{asin}`\n"
        f"• Order: `{amz_id}`\n\n"
        f"• Profit Margin: *+{margin_pct:.1f}%* 🔥\n"
        f"• Net Profit:    *+${profit:.2f}* 💵\n"
        f"• Payout / Cost: ${payout:.2f} / ${cost:.2f}\n"
        f"• Coupon Used:   {coupon}"
    )
    if screenshot_bytes:
        return send_whatsapp_photo(screenshot_bytes, caption=caption, to=target_group)
    return send_whatsapp_message(caption, to=target_group)


def notify_insight(insight: dict, account: str = "") -> bool:
    """Send an operational insight alert text to WhatsApp, routed to the store's Insights group.

    Routes directly to:
      • HOANK Insights   (120363429340767368@g.us) for HOANK
      • Hathoro Insights (120363413450875187@g.us) for Hathoro
    """
    summary = insight.get("summary") or insight.get("message") or ""
    if not summary:
        return False
    acct = account or insight.get("account") or ""
    target_group = get_whatsapp_group(acct, kind="insights")

    # Ensure Title and SKU are in summary if not already present
    asin = str(insight.get("asin") or "").strip().upper()
    title = str(insight.get("title") or insight.get("product_title") or "").strip()
    sku = str(insight.get("sku") or insight.get("main_sku") or "").strip()

    if (not title or not sku) and asin:
        try:
            from utils.telegram import _lookup_title_and_sku
            db_t, db_s = _lookup_title_and_sku(asin)
            title = title or db_t
            sku = sku or db_s
        except Exception:
            pass

    if "• Title:" not in summary and (title or sku):
        meta_lines = []
        if title:
            meta_lines.append(f"• Title: *{title[:80]}*")
        if sku:
            meta_lines.append(f"• SKU: `{sku}`")
        if meta_lines:
            parts = summary.split("\n", 1)
            if len(parts) == 2:
                summary = f"{parts[0]}\n" + "\n".join(meta_lines) + f"\n{parts[1]}"
            else:
                summary = summary + "\n" + "\n".join(meta_lines)

    return send_whatsapp_message(summary, to=target_group, background=True)


def notify_hijack(details: dict, screenshot_bytes: bytes = None) -> bool:
    """Send listing hijack alert when our rank is #2 or #3 to Insights group."""
    asin = str(details.get("asin") or "N/A").strip().upper()
    sku = str(details.get("sku") or "").strip()
    title = str(details.get("title") or "").strip()
    store = details.get("store") or ""

    if (not title or not sku) and asin and asin != "N/A":
        try:
            from utils.telegram import _lookup_title_and_sku
            db_t, db_s = _lookup_title_and_sku(asin)
            title = title or db_t
            sku = sku or db_s
        except Exception:
            pass

    our_rank = details.get("our_rank") or "2"
    winner_name = details.get("winner_name") or "Competitor"
    winner_price = details.get("winner_price") or "N/A"
    our_price = details.get("our_price") or "N/A"
    target_group = get_whatsapp_group(store, kind="insights")

    caption = (
        f"🚨 *LISTING HIJACK ALERT: Position Dropped!*\n"
        f"• Title       : *{title[:80] if title else 'Tracked Product'}*\n"
        f"• SKU         : `{sku if sku else 'N/A'}`\n"
        f"• ASIN        : `{asin}`\n"
        f"• Our Rank    : *#{our_rank}* (Lost Buy Box!)\n"
        f"• Buy Box Won : {winner_name} ({winner_price})\n"
        f"• Our Price   : {our_price}\n"
        f"• Action      : Check Repricer / Inventory immediately."
    )
    if screenshot_bytes:
        return send_whatsapp_photo(screenshot_bytes, caption=caption, to=target_group)
    return send_whatsapp_message(caption, to=target_group)

