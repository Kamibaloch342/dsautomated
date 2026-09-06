"""
Programmatic Ordering API — /api/v1
───────────────────────────────────
Key-authenticated REST API so external scripts and automations can place
and track bulk orders without the web UI.

Auth:  X-API-Key: zsk_...   (create keys under My Account → API)

Endpoints:
  GET  /api/v1/ping                     sanity check + identity
  GET  /api/v1/products?q=&page=        catalog search
  GET  /api/v1/products/<sku>           full product record
  POST /api/v1/orders/bulk              place a batch (same payload as the
                                        web Bulk Order Pad)
  GET  /api/v1/orders/<id>              one of your orders (+ emails)
  GET  /api/v1/batches/<batch_id>       all orders in one of your batches
"""

import hashlib
from datetime import datetime
from functools import wraps

from flask import Blueprint, request, jsonify, g
from sqlalchemy import or_

from database import db
from models import ApiKey, User, Product, Order, OrderEmail

api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")


def key_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        raw = request.headers.get("X-API-Key", "").strip()
        if not raw:
            return jsonify({"error": "Missing X-API-Key header"}), 401
        key = ApiKey.query.filter_by(
            key_hash=hashlib.sha256(raw.encode()).hexdigest(),
            is_active=True,
        ).first()
        if key is None:
            return jsonify({"error": "Invalid or revoked API key"}), 401
        key.last_used_at = datetime.now()
        db.session.commit()
        g.api_user = User.query.get(key.user_id)
        g.api_key = key
        return fn(*args, **kwargs)
    return wrapper


@api_v1.route("/ping")
@key_required
def ping():
    return jsonify({
        "ok": True,
        "user": g.api_user.email,
        "key": g.api_key.key_prefix + "…",
        "server_time": datetime.now().isoformat(),
    })


@api_v1.route("/products")
@key_required
def v1_products():
    query = Product.query
    q = request.args.get("q", "").strip()
    if q:
        query = query.filter(or_(
            Product.title.ilike(f"%{q}%"),
            Product.brand.ilike(f"%{q}%"),
            Product.zoro_code.ilike(f"%{q}%"),
            Product.mfr_number.ilike(f"%{q}%"),
        ))
    page = request.args.get("page", 1, type=int)
    per_page = min(max(request.args.get("per_page", 50, type=int), 1), 100)
    pagination = query.order_by(Product.product_id).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return jsonify({
        "products": [p.to_card_dict() for p in pagination.items],
        "total": pagination.total,
        "page": pagination.page,
        "pages": pagination.pages,
    })


@api_v1.route("/products/<sku>")
@key_required
def v1_product(sku):
    product = Product.query.filter(Product.zoro_code.ilike(sku)).first()
    if product is None:
        return jsonify({"error": f"SKU '{sku}' not found"}), 404
    return jsonify(product.to_dict())


@api_v1.route("/orders/bulk", methods=["POST"])
@key_required
def v1_bulk():
    from app import place_bulk_batch
    data = request.get_json(silent=True) or {}
    # Default the notification email to the key owner's address
    data.setdefault("notification_email", g.api_user.email)
    body, status = place_bulk_batch(data, g.api_user.user_id, source="api")
    return jsonify(body), status


@api_v1.route("/orders/<int:order_id>")
@key_required
def v1_order(order_id):
    order = Order.query.get(order_id)
    if order is None or order.user_id != g.api_user.user_id:
        return jsonify({"error": "Order not found"}), 404
    emails = (
        OrderEmail.query.filter_by(order_id=order_id)
        .order_by(OrderEmail.created_at.asc()).all()
    )
    return jsonify({
        "order": order.to_dict(),
        "emails": [{
            "email_type": e.email_type,
            "subject": e.subject,
            "status": e.status,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        } for e in emails],
    })


@api_v1.route("/batches/<batch_id>")
@key_required
def v1_batch(batch_id):
    orders = (
        Order.query
        .filter_by(bulk_batch_id=batch_id, user_id=g.api_user.user_id)
        .order_by(Order.order_id.asc())
        .all()
    )
    if not orders:
        return jsonify({"error": "Batch not found"}), 404
    return jsonify({
        "batch_id": batch_id,
        "orders": [o.to_dict() for o in orders],
    })
