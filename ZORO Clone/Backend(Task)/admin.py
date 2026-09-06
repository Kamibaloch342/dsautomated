"""
Admin API — control panel backend
─────────────────────────────────
Everything the store owner needs: KPIs, order management (status
transitions with real emails/notifications), support cases, product
edits, customers and the outbound email log.

All endpoints require a Bearer token of a user with is_admin=TRUE.
"""

import random
from datetime import datetime, timedelta
from functools import wraps

from flask import Blueprint, request, jsonify, g
from sqlalchemy import func, or_

from database import db
from models import (
    Product, Order, OrderEmail, Notification, User, Case, CaseMessage,
    Return, OrderItem,
)
from auth import login_required
from messaging import (
    record_email, push_notification,
    processing_email, shipment_email, confirmation_email,
    net30_decision_email, return_status_email, invoice_email,
)
from fulfillment import CARRIERS, _gen_tracking

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        if not getattr(g.current_user, "is_admin", False):
            return jsonify({"error": "Admin access required"}), 403
        return fn(*args, **kwargs)
    return wrapper


def _paginate(query, default_per_page=25):
    page = request.args.get("page", 1, type=int)
    per_page = min(max(request.args.get("per_page", default_per_page, type=int), 1), 100)
    return query.paginate(page=page, per_page=per_page, error_out=False)


# ─────────────────────────────────────────────
#  Dashboard stats
# ─────────────────────────────────────────────

@admin_bp.route("/stats")
@admin_required
def stats():
    status_rows = (
        db.session.query(Order.status, func.count(), func.coalesce(func.sum(Order.total), 0))
        .group_by(Order.status).all()
    )
    by_status = {s: {"count": c, "revenue": float(r)} for s, c, r in status_rows}

    today_count, today_revenue = (
        db.session.query(func.count(), func.coalesce(func.sum(Order.total), 0))
        .filter(Order.created_at >= func.current_date())
        .first()
    )

    total_orders = sum(v["count"] for v in by_status.values())
    total_revenue = sum(v["revenue"] for v in by_status.values())

    batches = (
        db.session.query(func.count(func.distinct(Order.bulk_batch_id)))
        .filter(Order.bulk_batch_id != "").scalar()
    )

    recent_orders = (
        Order.query.order_by(Order.created_at.desc()).limit(8).all()
    )
    recent_cases = (
        Case.query.order_by(Case.updated_at.desc()).limit(5).all()
    )

    return jsonify({
        "total_orders": total_orders,
        "total_revenue": round(total_revenue, 2),
        "today_orders": today_count,
        "today_revenue": float(today_revenue),
        "by_status": by_status,
        "bulk_batches": batches,
        "products": Product.query.count(),
        "customers": User.query.count(),
        "open_cases": Case.query.filter(Case.status.in_(["open", "pending"])).count(),
        "open_returns": Return.query.filter(
            Return.status.in_(["requested", "approved", "received"])
        ).count(),
        "pending_net30": User.query.filter_by(net30_status="pending").count(),
        "unpaid_invoices": Order.query.filter(
            Order.payment_method == "net30",
            Order.paid_at.is_(None),
            Order.status != "cancelled",
        ).count(),
        "emails_sent": OrderEmail.query.count(),
        "recent_orders": [o.to_dict() for o in recent_orders],
        "recent_cases": [c.to_dict() for c in recent_cases],
    })


# ─────────────────────────────────────────────
#  Analytics
# ─────────────────────────────────────────────

@admin_bp.route("/analytics")
@admin_required
def analytics():
    days = min(max(request.args.get("days", 30, type=int), 7), 120)
    since = datetime.now() - timedelta(days=days)

    daily_rows = (
        db.session.query(
            func.date(Order.created_at),
            func.count(),
            func.coalesce(func.sum(Order.total), 0),
        )
        .filter(Order.created_at >= since, Order.status != "cancelled")
        .group_by(func.date(Order.created_at))
        .order_by(func.date(Order.created_at))
        .all()
    )
    revenue_by_day = [
        {"date": str(d), "orders": c, "revenue": float(r)}
        for d, c, r in daily_rows
    ]

    top_products = (
        db.session.query(
            OrderItem.zoro_code,
            func.min(OrderItem.title),
            func.sum(OrderItem.quantity),
            func.sum(OrderItem.unit_price * OrderItem.quantity),
        )
        .join(Order, Order.order_id == OrderItem.order_id)
        .filter(Order.status != "cancelled")
        .group_by(OrderItem.zoro_code)
        .order_by(func.sum(OrderItem.unit_price * OrderItem.quantity).desc())
        .limit(10)
        .all()
    )

    source_rows = (
        db.session.query(Order.source, func.count(), func.coalesce(func.sum(Order.total), 0))
        .filter(Order.status != "cancelled")
        .group_by(Order.source).all()
    )
    payment_rows = (
        db.session.query(Order.payment_method, func.count(), func.coalesce(func.sum(Order.total), 0))
        .filter(Order.status != "cancelled")
        .group_by(Order.payment_method).all()
    )

    total_orders, total_revenue = (
        db.session.query(func.count(), func.coalesce(func.sum(Order.total), 0))
        .filter(Order.status != "cancelled")
        .first()
    )

    return jsonify({
        "days": days,
        "revenue_by_day": revenue_by_day,
        "top_products": [
            {"sku": s, "title": t, "qty": int(q), "revenue": float(r)}
            for s, t, q, r in top_products
        ],
        "source_split": [
            {"source": s or "web", "orders": c, "revenue": float(r)}
            for s, c, r in source_rows
        ],
        "payment_split": [
            {"method": m or "card_demo", "orders": c, "revenue": float(r)}
            for m, c, r in payment_rows
        ],
        "aov": round(float(total_revenue) / total_orders, 2) if total_orders else 0,
        "total_orders": total_orders,
        "total_revenue": float(total_revenue),
    })


# ─────────────────────────────────────────────
#  Orders
# ─────────────────────────────────────────────

@admin_bp.route("/orders")
@admin_required
def list_orders():
    query = Order.query

    status = request.args.get("status", "").strip()
    if status:
        query = query.filter(Order.status == status)

    source = request.args.get("source", "").strip()
    if source:
        query = query.filter(Order.source == source)

    q = request.args.get("q", "").strip()
    if q:
        clauses = [
            Order.ship_email.ilike(f"%{q}%"),
            Order.notification_email.ilike(f"%{q}%"),
            Order.ship_first_name.ilike(f"%{q}%"),
            Order.ship_last_name.ilike(f"%{q}%"),
            Order.bulk_batch_id.ilike(f"%{q}%"),
            Order.tracking_number.ilike(f"%{q}%"),
        ]
        if q.lstrip("#").isdigit():
            clauses.append(Order.order_id == int(q.lstrip("#")))
        query = query.filter(or_(*clauses))

    pagination = _paginate(query.order_by(Order.created_at.desc()))
    return jsonify({
        "orders": [o.to_dict() for o in pagination.items],
        "total": pagination.total,
        "page": pagination.page,
        "pages": pagination.pages,
    })


@admin_bp.route("/orders/<int:order_id>")
@admin_required
def get_order_admin(order_id):
    order = Order.query.get(order_id)
    if not order:
        return jsonify({"error": "Order not found"}), 404
    emails = (
        OrderEmail.query.filter_by(order_id=order_id)
        .order_by(OrderEmail.created_at.asc()).all()
    )
    cases = Case.query.filter_by(order_id=order_id).all()
    return jsonify({
        "order": order.to_dict(),
        "emails": [e.to_dict() for e in emails],
        "cases": [c.to_dict() for c in cases],
    })


VALID_TRANSITIONS = ["confirmed", "processing", "shipped", "delivered", "cancelled"]


@admin_bp.route("/orders/<int:order_id>/status", methods=["POST"])
@admin_required
def set_order_status(order_id):
    order = Order.query.get(order_id)
    if not order:
        return jsonify({"error": "Order not found"}), 404

    data = request.get_json(silent=True) or {}
    new_status = (data.get("status") or "").strip()
    if new_status not in VALID_TRANSITIONS:
        return jsonify({"error": f"status must be one of {VALID_TRANSITIONS}"}), 400

    order.status = new_status
    recipient = order.notification_email or order.ship_email

    if new_status == "processing":
        db.session.commit()
        subject, body = processing_email(order)
        record_email(recipient, subject, body, "processing",
                     order_id=order.order_id, bulk_batch_id=order.bulk_batch_id or "")
        push_notification(order.user_id, f"Order #{order.order_id} is being processed",
                          "Updated by our support team.",
                          notif_type="order_processing", order_id=order.order_id)

    elif new_status == "shipped":
        order.carrier = (data.get("carrier") or "").strip() or random.choice(CARRIERS)
        order.tracking_number = (data.get("tracking_number") or "").strip() \
            or _gen_tracking(order.carrier)
        order.shipped_at = func.now()
        db.session.commit()
        subject, body = shipment_email(order)
        record_email(recipient, subject, body, "shipment",
                     order_id=order.order_id, bulk_batch_id=order.bulk_batch_id or "")
        push_notification(order.user_id, f"Order #{order.order_id} has shipped",
                          f"{order.carrier} tracking {order.tracking_number}",
                          notif_type="order_shipped", order_id=order.order_id)

    elif new_status == "cancelled":
        db.session.commit()
        subject = f"ZoroStore order #{order.order_id} has been cancelled"
        body = (
            f"Hi {order.ship_first_name},\n\nOrder #{order.order_id} has been "
            f"cancelled. If you didn't request this or have questions, just "
            f"reply to this email or open a support case.\n\n— ZoroStore"
        )
        record_email(recipient, subject, body, "cancellation",
                     order_id=order.order_id, bulk_batch_id=order.bulk_batch_id or "")
        push_notification(order.user_id, f"Order #{order.order_id} was cancelled",
                          "Contact support if this wasn't expected.",
                          notif_type="order", order_id=order.order_id)
    else:
        db.session.commit()
        push_notification(order.user_id, f"Order #{order.order_id} is {new_status}",
                          "", notif_type="order", order_id=order.order_id)

    return jsonify({"order": order.to_dict()})


@admin_bp.route("/orders/<int:order_id>/resend", methods=["POST"])
@admin_required
def resend_order_email(order_id):
    order = Order.query.get(order_id)
    if not order:
        return jsonify({"error": "Order not found"}), 404
    email_type = (request.get_json(silent=True) or {}).get("email_type", "confirmation")

    builders = {
        "confirmation": confirmation_email,
        "processing": processing_email,
        "shipment": shipment_email,
    }
    builder = builders.get(email_type)
    if builder is None:
        return jsonify({"error": f"email_type must be one of {list(builders)}"}), 400
    if email_type == "shipment" and not order.tracking_number:
        return jsonify({"error": "Order has no tracking number yet"}), 400

    subject, body = builder(order)
    row = record_email(order.notification_email or order.ship_email,
                       subject, body, email_type,
                       order_id=order.order_id,
                       bulk_batch_id=order.bulk_batch_id or "")
    return jsonify({"email": row.to_dict()})


# ─────────────────────────────────────────────
#  Returns (RMA)
# ─────────────────────────────────────────────

@admin_bp.route("/orders/<int:order_id>/mark-paid", methods=["POST"])
@admin_required
def mark_invoice_paid(order_id):
    order = Order.query.get(order_id)
    if not order:
        return jsonify({"error": "Order not found"}), 404
    if order.payment_method != "net30":
        return jsonify({"error": "Not a Net 30 order"}), 400
    order.paid_at = datetime.now()
    db.session.commit()
    push_notification(order.user_id,
                      f"Invoice {order.invoice_number} marked paid",
                      f"Thanks — ${float(order.total):,.2f} received.",
                      notif_type="net30", order_id=order.order_id)
    return jsonify({"order": order.to_dict()})


@admin_bp.route("/returns")
@admin_required
def list_returns():
    query = Return.query
    status = request.args.get("status", "").strip()
    if status:
        query = query.filter(Return.status == status)
    q = request.args.get("q", "").strip()
    if q:
        clauses = [Return.email.ilike(f"%{q}%"), Return.rma_number.ilike(f"%{q}%")]
        if q.lstrip("#").isdigit():
            num = int(q.lstrip("#"))
            clauses += [Return.order_id == num, Return.return_id == num]
        query = query.filter(or_(*clauses))
    pagination = _paginate(query.order_by(Return.updated_at.desc()))

    items = []
    for r in pagination.items:
        d = r.to_dict()
        order = Order.query.get(r.order_id)
        if order:
            d["order_total"] = float(order.total)
            d["order_status"] = order.status
            d["items_summary"] = ", ".join(
                f"{i.title[:40]} ×{i.quantity}" for i in order.items
            )
        items.append(d)
    return jsonify({
        "returns": items,
        "total": pagination.total,
        "page": pagination.page,
        "pages": pagination.pages,
        "open_count": Return.query.filter(
            Return.status.in_(["requested", "approved", "received"])
        ).count(),
    })


RETURN_ACTIONS = {
    "approve": "approved",
    "deny": "denied",
    "received": "received",
    "refund": "refunded",
}


@admin_bp.route("/returns/<int:return_id>/action", methods=["POST"])
@admin_required
def return_action(return_id):
    ret = Return.query.get(return_id)
    if not ret:
        return jsonify({"error": "Return not found"}), 404
    order = Order.query.get(ret.order_id)
    if not order:
        return jsonify({"error": "Linked order missing"}), 404

    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action not in RETURN_ACTIONS:
        return jsonify({"error": f"action must be one of {list(RETURN_ACTIONS)}"}), 400

    ret.status = RETURN_ACTIONS[action]
    if data.get("note"):
        ret.admin_note = data["note"].strip()

    if action == "approve" and not ret.rma_number:
        ret.rma_number = "RMA-" + "".join(random.choices("0123456789", k=6))
    if action in ("approve", "refund"):
        try:
            ret.refund_amount = float(data.get("refund_amount") or order.total)
        except (TypeError, ValueError):
            ret.refund_amount = order.total
    db.session.commit()

    subject, body = return_status_email(ret, order)
    record_email(ret.email, subject, body, "return_update", order_id=order.order_id)
    push_notification(
        ret.user_id,
        f"Return {ret.rma_number or ''} {ret.status}".replace("  ", " "),
        f"Order #{order.order_id}" +
        (f" — refund ${float(ret.refund_amount):,.2f}" if ret.status == "refunded" and ret.refund_amount else ""),
        notif_type="return", order_id=order.order_id,
    )
    return jsonify({"return": ret.to_dict()})


# ─────────────────────────────────────────────
#  Net 30 approvals
# ─────────────────────────────────────────────

@admin_bp.route("/customers/<int:user_id>/net30", methods=["POST"])
@admin_required
def net30_decide(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "Customer not found"}), 404

    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action not in ("approve", "reject", "revoke"):
        return jsonify({"error": "action must be approve, reject or revoke"}), 400

    if action == "approve":
        try:
            limit = float(data.get("limit") or 1000)
        except (TypeError, ValueError):
            limit = 1000
        user.net30_status = "approved"
        user.net30_limit = limit
        subject, body = net30_decision_email(user, True, limit)
        record_email(user.email, subject, body, "net30")
        push_notification(user.user_id, "Net 30 approved",
                          f"Credit limit ${limit:,.2f} — select Net 30 at checkout.",
                          notif_type="net30")
    elif action == "reject":
        user.net30_status = "rejected"
        subject, body = net30_decision_email(user, False)
        record_email(user.email, subject, body, "net30")
        push_notification(user.user_id, "Net 30 application declined", "",
                          notif_type="net30")
    else:  # revoke
        user.net30_status = "none"
        user.net30_limit = 0

    db.session.commit()
    return jsonify({"customer": user.to_dict()})


# ─────────────────────────────────────────────
#  Cases
# ─────────────────────────────────────────────

@admin_bp.route("/cases")
@admin_required
def list_cases():
    query = Case.query

    status = request.args.get("status", "").strip()
    if status:
        query = query.filter(Case.status == status)

    q = request.args.get("q", "").strip()
    if q:
        clauses = [
            Case.email.ilike(f"%{q}%"),
            Case.subject.ilike(f"%{q}%"),
            Case.name.ilike(f"%{q}%"),
        ]
        if q.lstrip("#").isdigit():
            num = int(q.lstrip("#"))
            clauses += [Case.case_id == num, Case.order_id == num]
        query = query.filter(or_(*clauses))

    pagination = _paginate(query.order_by(Case.updated_at.desc()))
    return jsonify({
        "cases": [c.to_dict() for c in pagination.items],
        "total": pagination.total,
        "page": pagination.page,
        "pages": pagination.pages,
        "open_count": Case.query.filter(Case.status.in_(["open", "pending"])).count(),
    })


@admin_bp.route("/cases/<int:case_id>")
@admin_required
def get_case_admin(case_id):
    case = Case.query.get(case_id)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    order = Order.query.get(case.order_id) if case.order_id else None
    return jsonify({
        "case": case.to_dict(include_messages=True),
        "order": order.to_dict() if order else None,
    })


@admin_bp.route("/cases/<int:case_id>/reply", methods=["POST"])
@admin_required
def reply_case(case_id):
    case = Case.query.get(case_id)
    if not case:
        return jsonify({"error": "Case not found"}), 404

    data = request.get_json(silent=True) or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"error": "Reply body is required"}), 400

    message = CaseMessage(case_id=case.case_id, sender="admin", body=body)
    db.session.add(message)
    if data.get("status") in ["open", "pending", "resolved", "closed"]:
        case.status = data["status"]
    elif case.status == "open":
        case.status = "pending"
    db.session.commit()

    # Email the customer + bell notification
    subject = f"ZoroStore support — case #{case.case_id}: {case.subject}"
    email_body = (
        f"Hi {case.name or 'there'},\n\n{body}\n\n"
        f"— ZoroStore Support (case #{case.case_id}"
        f"{', order #' + str(case.order_id) if case.order_id else ''})"
    )
    record_email(case.email, subject, email_body, "case_reply",
                 order_id=case.order_id)
    push_notification(case.user_id,
                      f"Support replied to case #{case.case_id}",
                      body[:140], notif_type="case", order_id=case.order_id)

    return jsonify({"case": case.to_dict(include_messages=True)})


@admin_bp.route("/cases/<int:case_id>", methods=["PUT"])
@admin_required
def update_case(case_id):
    case = Case.query.get(case_id)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    data = request.get_json(silent=True) or {}
    if data.get("status") in ["open", "pending", "resolved", "closed"]:
        case.status = data["status"]
    if data.get("priority") in ["normal", "high"]:
        case.priority = data["priority"]
    db.session.commit()
    return jsonify({"case": case.to_dict(include_messages=True)})


# ─────────────────────────────────────────────
#  Products
# ─────────────────────────────────────────────

PRODUCT_EDITABLE = [
    "unit_price", "original_price", "availability", "min_order_qty",
    "package_qty", "lead_time", "uom", "is_dropship", "is_ltl",
    "is_hazmat", "freight_surcharge", "brand", "title",
]


@admin_bp.route("/products")
@admin_required
def list_products_admin():
    query = Product.query
    q = request.args.get("q", "").strip()
    if q:
        query = query.filter(or_(
            Product.title.ilike(f"%{q}%"),
            Product.brand.ilike(f"%{q}%"),
            Product.zoro_code.ilike(f"%{q}%"),
            Product.mfr_number.ilike(f"%{q}%"),
        ))
    pagination = _paginate(query.order_by(Product.product_id.asc()))
    items = []
    for p in pagination.items:
        d = p.to_card_dict()
        d["spec_count"] = len(p.specifications or {})
        d["package_qty"] = p.package_qty or 1
        d["is_dropship"] = p.is_dropship
        d["freight_surcharge"] = float(p.freight_surcharge or 0)
        items.append(d)
    return jsonify({
        "products": items,
        "total": pagination.total,
        "page": pagination.page,
        "pages": pagination.pages,
    })


@admin_bp.route("/products/<int:product_id>", methods=["PUT"])
@admin_required
def update_product(product_id):
    product = Product.query.get(product_id)
    if not product:
        return jsonify({"error": "Product not found"}), 404
    data = request.get_json(silent=True) or {}
    for field in PRODUCT_EDITABLE:
        if field in data:
            setattr(product, field, data[field])
    if isinstance(data.get("specifications"), dict):
        product.specifications = data["specifications"]
    db.session.commit()
    return jsonify({"product": product.to_dict()})


# ─────────────────────────────────────────────
#  Customers
# ─────────────────────────────────────────────

@admin_bp.route("/customers")
@admin_required
def list_customers():
    query = User.query
    q = request.args.get("q", "").strip()
    if q:
        query = query.filter(or_(
            User.email.ilike(f"%{q}%"),
            User.first_name.ilike(f"%{q}%"),
            User.last_name.ilike(f"%{q}%"),
        ))
    pagination = _paginate(query.order_by(User.created_at.desc()))
    customers = []
    for u in pagination.items:
        row = u.to_dict()
        row["created_at"] = u.created_at.isoformat() if u.created_at else None
        row["order_count"] = Order.query.filter_by(user_id=u.user_id).count()
        row["total_spent"] = float(
            db.session.query(func.coalesce(func.sum(Order.total), 0))
            .filter(Order.user_id == u.user_id).scalar()
        )
        customers.append(row)
    return jsonify({
        "customers": customers,
        "total": pagination.total,
        "page": pagination.page,
        "pages": pagination.pages,
    })


# ─────────────────────────────────────────────
#  Email log
# ─────────────────────────────────────────────

@admin_bp.route("/emails")
@admin_required
def list_emails():
    query = OrderEmail.query
    email_type = request.args.get("type", "").strip()
    if email_type:
        query = query.filter(OrderEmail.email_type == email_type)
    q = request.args.get("q", "").strip()
    if q:
        query = query.filter(or_(
            OrderEmail.recipient.ilike(f"%{q}%"),
            OrderEmail.subject.ilike(f"%{q}%"),
        ))
    pagination = _paginate(query.order_by(OrderEmail.created_at.desc()))
    return jsonify({
        "emails": [e.to_dict() for e in pagination.items],
        "total": pagination.total,
        "page": pagination.page,
        "pages": pagination.pages,
    })
