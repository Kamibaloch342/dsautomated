"""
Fulfillment worker
──────────────────
Background thread that advances orders through their lifecycle and sends
the matching emails / notifications:

    confirmed ──(45 s)──▶ processing ──(2.5 min)──▶ shipped

On "processing"  → processing email + bell notification
On "shipped"     → tracking number is generated, shipment email +
                   bell notification

Timings are intentionally short so the full journey — including the
shipment email thread — can be watched live in a dev session. Age is
measured with the database clock (created_at uses the DB's now()), so
Python/DB timezone differences can't skew the schedule.
"""

import logging
import random
import threading
import time
from datetime import datetime

from sqlalchemy import text

from database import db
from models import Order
from messaging import (
    processing_email,
    shipment_email,
    record_email,
    push_notification,
)

log = logging.getLogger(__name__)

POLL_SECONDS = 20
PROCESSING_AFTER_S = 45     # confirmed → processing
SHIPPED_AFTER_S = 150       # confirmed → shipped

CARRIERS = ["UPS", "FedEx", "USPS"]


def _gen_tracking(carrier):
    digits = lambda n: "".join(random.choices("0123456789", k=n))
    if carrier == "UPS":
        return "1Z999AA" + digits(10)
    if carrier == "FedEx":
        return digits(12)
    return "9400" + digits(18)  # USPS


def _orders_older_than(status, seconds):
    """Orders in `status` created more than `seconds` ago (DB clock)."""
    return (
        Order.query
        .filter(Order.status == status)
        .filter(Order.created_at <= text(f"now() - interval '{int(seconds)} seconds'"))
        .all()
    )


def _advance_once(app):
    with app.app_context():
        # confirmed → processing
        for order in _orders_older_than("confirmed", PROCESSING_AFTER_S):
            order.status = "processing"
            db.session.commit()
            subject, body = processing_email(order)
            recipient = order.notification_email or order.ship_email
            record_email(recipient, subject, body, "processing",
                         order_id=order.order_id,
                         bulk_batch_id=order.bulk_batch_id or "")
            push_notification(
                order.user_id,
                f"Order #{order.order_id} is being processed",
                "Your order is at the warehouse and will ship shortly.",
                notif_type="order_processing",
                order_id=order.order_id,
                bulk_batch_id=order.bulk_batch_id or "",
            )
            log.info("Order #%s → processing", order.order_id)

        # processing → shipped
        for order in _orders_older_than("processing", SHIPPED_AFTER_S):
            order.status = "shipped"
            order.carrier = random.choice(CARRIERS)
            order.tracking_number = _gen_tracking(order.carrier)
            order.shipped_at = datetime.now()
            db.session.commit()
            subject, body = shipment_email(order)
            recipient = order.notification_email or order.ship_email
            record_email(recipient, subject, body, "shipment",
                         order_id=order.order_id,
                         bulk_batch_id=order.bulk_batch_id or "")
            push_notification(
                order.user_id,
                f"Order #{order.order_id} has shipped",
                f"{order.carrier} tracking {order.tracking_number}",
                notif_type="order_shipped",
                order_id=order.order_id,
                bulk_batch_id=order.bulk_batch_id or "",
            )
            log.info("Order #%s → shipped (%s %s)",
                     order.order_id, order.carrier, order.tracking_number)


def start_fulfillment_worker(app):
    """Start the daemon loop (call once per process)."""
    def loop():
        log.info("Fulfillment worker started (poll every %ss)", POLL_SECONDS)
        while True:
            try:
                _advance_once(app)
            except Exception:
                log.exception("Fulfillment pass failed")
            time.sleep(POLL_SECONDS)

    thread = threading.Thread(target=loop, daemon=True, name="fulfillment")
    thread.start()
    return thread
