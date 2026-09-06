#!/usr/bin/env python3
"""
Test script to send a live test email via Resend API to verify your integration.
Usage:
  python test_resend_email.py [recipient_email] [template_type]
Example:
  python test_resend_email.py kamibaloch342@gmail.com confirmation
"""

import sys
import os

# Add Backend(Task) to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app
from database import db
from models import Order, OrderItem
import messaging

def send_test(recipient="kamibaloch342@gmail.com", etype="confirmation"):
    with app.app_context():
        # Find or mock an order
        order = Order.query.order_by(Order.order_id.desc()).first()
        if not order:
            order = Order(
                order_id=1001,
                status="confirmed",
                payment_method="card_demo",
                ship_first_name="Kami",
                ship_last_name="Baloch",
                ship_email=recipient,
                ship_phone="555-123-4567",
                ship_address1="100 Congress Ave",
                ship_city="Austin",
                ship_state="TX",
                ship_zip="78701",
                subtotal=148.50,
                shipping_cost=0.0,
                tax=12.25,
                total=160.75,
                carrier="FedEx Ground",
                tracking_number="FEDEX-784920194821",
                notification_email=recipient,
            )
            order.items = [
                OrderItem(
                    zoro_code="G4341486",
                    title="6 in x 6 in x 6 in Shipping Box, Kraft, Corrugate, Single Wall, 32 ECT",
                    quantity=50,
                    unit_price=0.49,
                ),
                OrderItem(
                    zoro_code="G4439251",
                    title="Milwaukee Thunderbolt 1/2 in Titanium Coated Drill Bit",
                    quantity=4,
                    unit_price=19.97,
                ),
            ]

        if etype == "otp":
            subject, body_text, html_body = messaging.otp_email(recipient, "592814", headline="Verify Your EQUIPT Account")
            email_type = "otp_verification"
        else:
            builders = {
                "confirmation": (messaging.confirmation_email, "confirmation"),
                "invoice": (messaging.invoice_email, "invoice"),
                "shipment": (messaging.shipment_email, "shipment"),
                "delivery": (messaging.delivery_email, "delivery"),
            }
            builder_func, email_type = builders.get(etype, (messaging.confirmation_email, "confirmation"))
            res = builder_func(order)
            subject, body_text, html_body = res[0], res[1], res[2] if len(res) > 2 else None

        print(f"🚀 Dispatching '{email_type}' email to: {recipient}...")
        print(f"Subject: {subject}")

        row = messaging.record_email(
            recipient=recipient,
            subject=subject,
            body_text=body_text,
            email_type=email_type,
            order_id=order.order_id,
            html_body=html_body,
        )

        print(f"✅ Result Status: {row.status}")
        if row.status == "sent":
            print(f"🎉 Email successfully delivered via Resend API to {recipient}!")
        elif row.status == "simulated":
            print(f"ℹ️ Email recorded as simulated (RESEND_API_KEY not found in .env).")
        else:
            print(f"❌ Email sending failed. Check terminal error logs above.")

if __name__ == "__main__":
    recip = sys.argv[1] if len(sys.argv) > 1 else "kamibaloch342@gmail.com"
    email_template = sys.argv[2] if len(sys.argv) > 2 else "confirmation"
    send_test(recip, email_template)
