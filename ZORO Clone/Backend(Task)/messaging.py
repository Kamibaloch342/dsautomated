"""
Messaging — order emails + in-app notifications
────────────────────────────────────────────────
Every customer-facing message the store sends lives here:

  • Order confirmation emails      (single checkout + each bulk order)
  • Bulk batch summary email       (one thread listing every order ID)
  • Processing / shipment emails   (sent by the fulfillment worker)
  • In-app notifications           (header bell)

Emails are really sent when SMTP_* env vars are configured; otherwise
they are recorded in order_emails with status 'simulated' so the whole
flow — including the visible email thread — works out of the box.

.env keys (all optional):
  SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD,
  SMTP_FROM (default SMTP_USER), SMTP_USE_TLS (default true)
"""

import os
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from database import db
from models import OrderEmail, Notification

log = logging.getLogger(__name__)

STORE_NAME = "ZoroStore"


# ─────────────────────────────────────────────
#  SMTP transport
# ─────────────────────────────────────────────

def _smtp_config():
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        return None
    return {
        "host": host,
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": os.getenv("SMTP_USER", "").strip(),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "sender": os.getenv("SMTP_FROM", "").strip()
                  or os.getenv("SMTP_USER", "").strip(),
        "use_tls": os.getenv("SMTP_USE_TLS", "true").lower() != "false",
    }


def _send_smtp(recipient, subject, body_text):
    cfg = _smtp_config()
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{STORE_NAME} <{cfg['sender']}>"
    msg["To"] = recipient
    msg.attach(MIMEText(body_text, "plain", "utf-8"))

    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as server:
        if cfg["use_tls"]:
            server.starttls()
        if cfg["user"]:
            server.login(cfg["user"], cfg["password"])
        server.sendmail(cfg["sender"], [recipient], msg.as_string())


def record_email(recipient, subject, body_text, email_type,
                 order_id=None, bulk_batch_id=""):
    """Send (when SMTP is configured) and always log to order_emails."""
    status = "simulated"
    if _smtp_config():
        try:
            _send_smtp(recipient, subject, body_text)
            status = "sent"
        except Exception:
            log.exception("SMTP send failed for %s", recipient)
            status = "failed"

    row = OrderEmail(
        order_id=order_id,
        bulk_batch_id=bulk_batch_id,
        email_type=email_type,
        recipient=recipient,
        subject=subject,
        body_text=body_text,
        status=status,
    )
    db.session.add(row)
    db.session.commit()
    log.info("Email [%s] %s → %s (%s)", email_type, subject, recipient, status)
    return row


# ─────────────────────────────────────────────
#  Notifications (header bell)
# ─────────────────────────────────────────────

def push_notification(user_id, title, body="", notif_type="order",
                      order_id=None, bulk_batch_id=""):
    """Create an in-app notification. No-op for guest (user_id None)."""
    if user_id is None:
        return None
    n = Notification(
        user_id=user_id,
        order_id=order_id,
        bulk_batch_id=bulk_batch_id,
        notif_type=notif_type,
        title=title,
        body=body,
    )
    db.session.add(n)
    db.session.commit()
    return n


# ─────────────────────────────────────────────
#  Email bodies
# ─────────────────────────────────────────────

def _money(v):
    return f"${float(v):,.2f}"


def _order_lines(order):
    lines = []
    for item in order.items:
        lines.append(
            f"  • {item.title}\n"
            f"    SKU {item.zoro_code}  |  Qty {item.quantity}  |  "
            f"{_money(item.unit_price)} each"
        )
    return "\n".join(lines)


def _address_block(first, last, addr1, addr2, city, state, zip_code):
    parts = [f"{first} {last}", addr1]
    if addr2:
        parts.append(addr2)
    parts.append(f"{city}, {state} {zip_code}")
    return "\n".join(parts)


def confirmation_email(order):
    subject = f"{STORE_NAME} order #{order.order_id} confirmed"
    body = (
        f"Hi {order.ship_first_name},\n\n"
        f"Thanks for your order! Order #{order.order_id} has been received "
        f"and is confirmed.\n\n"
        f"ITEMS\n{_order_lines(order)}\n\n"
        f"Subtotal:  {_money(order.subtotal)}\n"
        f"Shipping:  {'FREE' if float(order.shipping_cost) == 0 else _money(order.shipping_cost)}\n"
        f"Tax:       {_money(order.tax)}\n"
        f"Total:     {_money(order.total)}\n\n"
        f"SHIPPING TO\n"
        f"{_address_block(order.ship_first_name, order.ship_last_name, order.ship_address1, order.ship_address2, order.ship_city, order.ship_state, order.ship_zip)}\n\n"
        f"We'll email you again when your order ships, with tracking details.\n\n"
        f"— {STORE_NAME}"
    )
    return subject, body


def processing_email(order):
    subject = f"{STORE_NAME} order #{order.order_id} is being processed"
    body = (
        f"Hi {order.ship_first_name},\n\n"
        f"Good news — order #{order.order_id} is now being processed at our "
        f"warehouse and will ship shortly.\n\n"
        f"ITEMS\n{_order_lines(order)}\n\n"
        f"You'll receive a shipment email with a tracking number as soon as "
        f"it leaves the dock.\n\n"
        f"— {STORE_NAME}"
    )
    return subject, body


def shipment_email(order):
    subject = (
        f"{STORE_NAME} order #{order.order_id} has shipped — "
        f"tracking {order.tracking_number}"
    )
    body = (
        f"Hi {order.ship_first_name},\n\n"
        f"Order #{order.order_id} is on its way!\n\n"
        f"CARRIER:   {order.carrier}\n"
        f"TRACKING:  {order.tracking_number}\n\n"
        f"ITEMS\n{_order_lines(order)}\n\n"
        f"SHIPPING TO\n"
        f"{_address_block(order.ship_first_name, order.ship_last_name, order.ship_address1, order.ship_address2, order.ship_city, order.ship_state, order.ship_zip)}\n\n"
        f"Thanks for shopping with us.\n\n"
        f"— {STORE_NAME}"
    )
    return subject, body


def invoice_email(order):
    due = order.net30_due_date.strftime("%B %d, %Y") if order.net30_due_date else "in 30 days"
    subject = f"{STORE_NAME} invoice {order.invoice_number} — {_money(order.total)} due {due}"
    body = (
        f"Hi {order.ship_first_name},\n\n"
        f"Order #{order.order_id} was placed on your Net 30 terms.\n\n"
        f"INVOICE:   {order.invoice_number}\n"
        f"AMOUNT:    {_money(order.total)}\n"
        f"DUE DATE:  {due}\n\n"
        f"ITEMS\n{_order_lines(order)}\n\n"
        f"You can view all open invoices any time under My Account → "
        f"Net 30 & Invoices.\n\n"
        f"— {STORE_NAME}"
    )
    return subject, body


def net30_decision_email(user, approved, limit=0.0):
    if approved:
        subject = f"{STORE_NAME} — your Net 30 account is approved"
        body = (
            f"Hi {user.first_name},\n\n"
            f"Great news — your Net 30 credit application"
            f"{' for ' + user.company if user.company else ''} is approved.\n\n"
            f"CREDIT LIMIT:  {_money(limit)}\n"
            f"TERMS:         Net 30 days from order date\n\n"
            f"Pick \"Net 30 (invoice)\" as the payment method at checkout or "
            f"in the Bulk Order Pad.\n\n— {STORE_NAME}"
        )
    else:
        subject = f"{STORE_NAME} — Net 30 application update"
        body = (
            f"Hi {user.first_name},\n\n"
            f"We could not approve your Net 30 application at this time. "
            f"You can reply to this email or open a support case if you'd "
            f"like a review.\n\n— {STORE_NAME}"
        )
    return subject, body


def return_status_email(ret, order):
    status_lines = {
        "requested": "Your return request has been received — our team will review it shortly.",
        "approved": (f"Your return is APPROVED. RMA number: {ret.rma_number}.\n"
                     f"Ship the item(s) back with the RMA number written on the box:\n"
                     f"  ZoroStore Returns, 123 Industrial Blvd, Suite 100, Chicago, IL 60601"),
        "denied": "After review we're unable to accept this return." +
                  (f"\nNote from our team: {ret.admin_note}" if ret.admin_note else ""),
        "received": "We've received your returned item(s) — your refund is being processed.",
        "refunded": f"Your refund of {_money(ret.refund_amount or order.total)} has been issued.",
    }
    subject = (f"{STORE_NAME} return {ret.rma_number or ''} for order "
               f"#{order.order_id} — {ret.status}").replace("  ", " ")
    body = (
        f"Hi {order.ship_first_name},\n\n"
        f"{status_lines.get(ret.status, ret.status)}\n\n"
        f"ORDER:   #{order.order_id}\n"
        f"REASON:  {ret.reason.replace('_', ' ')}\n"
        + (f"RMA:     {ret.rma_number}\n" if ret.rma_number else "")
        + f"\n— {STORE_NAME}"
    )
    return subject, body


def batch_summary_email(batch_id, orders, failed_rows, recipient):
    subject = (
        f"{STORE_NAME} bulk batch {batch_id} — "
        f"{len(orders)} order{'s' if len(orders) != 1 else ''} placed"
    )
    lines = []
    for o in orders:
        item = o.items[0] if o.items else None
        lines.append(
            f"  • Order #{o.order_id} — {item.title if item else ''}\n"
            f"    SKU {item.zoro_code if item else ''}  |  Qty {item.quantity if item else ''}  |  "
            f"Total {_money(o.total)}\n"
            f"    Ship to: {o.ship_first_name} {o.ship_last_name}, "
            f"{o.ship_city}, {o.ship_state} {o.ship_zip}"
        )
    failed_txt = ""
    if failed_rows:
        failed_txt = "\n\nROWS THAT COULD NOT BE PLACED\n" + "\n".join(
            f"  ✗ Row {r['row']} — SKU {r.get('sku', '?')}: {r.get('error', 'unknown error')}"
            for r in failed_rows
        )
    grand = sum(float(o.total) for o in orders)
    body = (
        f"Your bulk batch {batch_id} has been placed.\n\n"
        f"ORDERS CREATED ({len(orders)})\n" + "\n".join(lines) +
        f"\n\nBatch total: {_money(grand)}"
        f"{failed_txt}\n\n"
        f"Each order will get its own shipment email with tracking as it "
        f"ships. You can also watch live status from the notification bell "
        f"and your account's order history.\n\n"
        f"— {STORE_NAME}"
    )
    return subject, body
