import os
import json
import logging
import random
import re
import string
from datetime import datetime

from flask import Flask, jsonify, request, abort, g
from flask_cors import CORS
from dotenv import load_dotenv
from sqlalchemy import text

from database import db
from models import (
    Product, Category, ProductImage, Order, OrderItem,
    OrderEmail, Notification, Case, CaseMessage, User,
    ApiKey, Return, SiteSetting,
)
from auth import auth_bp, login_required
from admin import admin_bp
from messaging import (
    confirmation_email, batch_summary_email, invoice_email,
    return_status_email,
    record_email, push_notification,
)
from fulfillment import start_fulfillment_worker

load_dotenv()

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger(__name__)

app = Flask(__name__)

# ── CORS — restrict to known frontend origins ──
CORS(app, origins=[
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
])

app.config["SQLALCHEMY_DATABASE_URI"] = (
    f"postgresql://{os.getenv('DB_USER')}:"
    f"{os.getenv('DB_PASSWORD')}@"
    f"{os.getenv('DB_HOST')}:"
    f"{os.getenv('DB_PORT')}/"
    f"{os.getenv('DB_NAME')}"
)

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

# ── Register blueprints ──
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)

from api_v1 import api_v1  # noqa: E402  (needs app-level imports resolved)
app.register_blueprint(api_v1)


# ─────────────────────────────────────────────
#  Lightweight migrations
#  db.create_all() only creates missing TABLES; these statements add the
#  new COLUMNS to tables that already exist. All are idempotent.
# ─────────────────────────────────────────────

MIGRATIONS = [
    # Product variations
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS variant_group VARCHAR(100)",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS variant_label VARCHAR(100)",
    "CREATE INDEX IF NOT EXISTS idx_products_variant_group ON products (variant_group)",
    # Billing address
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS billing_same_as_shipping BOOLEAN DEFAULT TRUE",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS bill_first_name VARCHAR(100) DEFAULT ''",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS bill_last_name VARCHAR(100) DEFAULT ''",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS bill_phone VARCHAR(30) DEFAULT ''",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS bill_address1 VARCHAR(255) DEFAULT ''",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS bill_address2 VARCHAR(255) DEFAULT ''",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS bill_city VARCHAR(100) DEFAULT ''",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS bill_state VARCHAR(50) DEFAULT ''",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS bill_zip VARCHAR(20) DEFAULT ''",
    # Fulfillment
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS tracking_number VARCHAR(60) DEFAULT ''",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS carrier VARCHAR(40) DEFAULT ''",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS shipped_at TIMESTAMP",
    # Bulk ordering
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS source VARCHAR(20) DEFAULT 'web'",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS bulk_batch_id VARCHAR(40) DEFAULT ''",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS notification_email VARCHAR(255) DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS idx_orders_bulk_batch ON orders (bulk_batch_id)",
    # Full zoro.com extraction fields
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS package_qty INTEGER DEFAULT 1",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS original_price NUMERIC(10,2)",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS erp_id VARCHAR(30)",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS sales_status VARCHAR(10)",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS is_hazmat BOOLEAN DEFAULT FALSE",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS restricted_states JSONB DEFAULT '[]'::jsonb",
    # Admin
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE NOT NULL",
    # Net 30 credit accounts
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS company VARCHAR(150) DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS net30_status VARCHAR(20) DEFAULT 'none'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS net30_limit NUMERIC(10,2) DEFAULT 0",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS net30_applied_at TIMESTAMP",
    # Payment fields on orders
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_method VARCHAR(20) DEFAULT 'card_demo'",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS invoice_number VARCHAR(30) DEFAULT ''",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS net30_due_date TIMESTAMP",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS paid_at TIMESTAMP",
    # Quantity-break pricing
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS price_tiers JSONB DEFAULT '[]'::jsonb",
]


def run_migrations():
    for stmt in MIGRATIONS:
        db.session.execute(text(stmt))
    db.session.commit()


# ── Global error handlers ──

@app.errorhandler(404)
def not_found(_e):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def server_error(_e):
    log.exception("Internal server error")
    return jsonify({"error": "Internal server error"}), 500


# ─────────────────────────────────────────────
#  Health check
# ─────────────────────────────────────────────

@app.route("/")
def home():
    return {
        "message": "Zoro Backend Running Successfully 🚀",
        "endpoints": {
            "products":        "GET /api/products",
            "single_product":  "GET /api/products/<zoro_code>",
            "variations":      "GET /api/products/<zoro_code>/variations",
            "search":          "GET /api/products/search?q=...",
            "categories":      "GET /api/categories",
            "auth_register":   "POST /api/auth/register",
            "auth_login":      "POST /api/auth/login",
            "orders":          "POST /api/orders",
            "bulk_validate":   "POST /api/orders/bulk/validate",
            "bulk_orders":     "POST /api/orders/bulk",
            "batch":           "GET /api/orders/batch/<batch_id>",
            "order_emails":    "GET /api/orders/<order_id>/emails",
            "notifications":   "GET /api/notifications",
        }
    }


# ─────────────────────────────────────────────
#  GET /api/products — list all (paginated, sortable, filterable)
# ─────────────────────────────────────────────

@app.route("/api/products")
def get_products():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)

    # Clamp per_page to a reasonable range
    per_page = min(max(per_page, 1), 100)

    query = Product.query

    # ── Filtering ──
    category_id = request.args.get("category_id", type=int)
    if category_id:
        query = query.filter(Product.category_id == category_id)

    brand = request.args.get("brand", "").strip()
    if brand:
        query = query.filter(Product.brand.ilike(f"%{brand}%"))

    in_stock = request.args.get("in_stock", "").strip().lower()
    if in_stock == "true":
        query = query.filter(
            db.or_(
                Product.availability.ilike("%in stock%"),
                Product.availability.ilike("%stock%"),
            )
        )

    min_price = request.args.get("min_price", type=float)
    max_price = request.args.get("max_price", type=float)
    if min_price is not None:
        query = query.filter(Product.unit_price >= min_price)
    if max_price is not None:
        query = query.filter(Product.unit_price <= max_price)

    # ── Sorting ──
    sort = request.args.get("sort", "").strip()
    sort_map = {
        "price_asc":  Product.unit_price.asc(),
        "price_desc": Product.unit_price.desc(),
        "name_asc":   Product.title.asc(),
        "name_desc":  Product.title.desc(),
        "newest":     Product.product_id.desc(),
    }
    order_clause = sort_map.get(sort, Product.product_id.asc())
    query = query.order_by(order_clause)

    pagination = query.paginate(
        page=page, per_page=per_page, error_out=False
    )

    return jsonify({
        "products": [p.to_card_dict() for p in pagination.items],
        "total": pagination.total,
        "page": pagination.page,
        "pages": pagination.pages,
        "per_page": per_page,
        "has_next": pagination.has_next,
        "has_prev": pagination.has_prev,
    })


# ─────────────────────────────────────────────
#  GET /api/products/<zoro_code> — single product
# ─────────────────────────────────────────────

@app.route("/api/products/<zoro_code>")
def get_product(zoro_code):
    product = Product.query.filter_by(zoro_code=zoro_code).first()

    if product is None:
        return jsonify({"error": "Product not found"}), 404

    return jsonify(product.to_dict())


# ─────────────────────────────────────────────
#  GET /api/products/search?q=... — search (with sort/filter)
# ─────────────────────────────────────────────

@app.route("/api/products/search")
def search_products():
    q = request.args.get("q", "").strip()

    if not q:
        return jsonify({"error": "Missing search query ?q=..."}), 400

    # Case-insensitive search across title and brand
    like_pattern = f"%{q}%"
    query = Product.query.filter(
        db.or_(
            Product.title.ilike(like_pattern),
            Product.brand.ilike(like_pattern),
            Product.zoro_code.ilike(like_pattern),
            Product.mfr_number.ilike(like_pattern),
        )
    )

    # ── Filtering ──
    in_stock = request.args.get("in_stock", "").strip().lower()
    if in_stock == "true":
        query = query.filter(
            db.or_(
                Product.availability.ilike("%in stock%"),
                Product.availability.ilike("%stock%"),
            )
        )

    min_price = request.args.get("min_price", type=float)
    max_price = request.args.get("max_price", type=float)
    if min_price is not None:
        query = query.filter(Product.unit_price >= min_price)
    if max_price is not None:
        query = query.filter(Product.unit_price <= max_price)

    # ── Sorting ──
    sort = request.args.get("sort", "").strip()
    sort_map = {
        "price_asc":  Product.unit_price.asc(),
        "price_desc": Product.unit_price.desc(),
        "name_asc":   Product.title.asc(),
        "name_desc":  Product.title.desc(),
    }
    order_clause = sort_map.get(sort, Product.product_id.asc())
    query = query.order_by(order_clause)

    # ── Pagination ──
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    per_page = min(max(per_page, 1), 100)

    pagination = query.paginate(
        page=page, per_page=per_page, error_out=False
    )

    return jsonify({
        "query": q,
        "count": pagination.total,
        "products": [p.to_card_dict() for p in pagination.items],
        "page": pagination.page,
        "pages": pagination.pages,
        "per_page": per_page,
        "has_next": pagination.has_next,
        "has_prev": pagination.has_prev,
    })


# ─────────────────────────────────────────────
#  GET /api/categories — category tree
# ─────────────────────────────────────────────

@app.route("/api/categories")
def get_categories():
    categories = Category.query.order_by(Category.category_name).all()

    # Build tree: each category includes product_count
    cat_list = []
    for c in categories:
        cat_data = c.to_dict()
        cat_data["product_count"] = len(c.products)
        cat_list.append(cat_data)

    return jsonify({
        "categories": cat_list,
    })


# ─────────────────────────────────────────────
#  GET /api/products/<zoro_code>/related — same-category products
# ─────────────────────────────────────────────

@app.route("/api/products/<zoro_code>/related")
def get_related_products(zoro_code):
    product = Product.query.filter_by(zoro_code=zoro_code).first()

    if not product:
        return jsonify({"products": []})

    limit = request.args.get("limit", 8, type=int)

    if product.category_id:
        # Fetch products in the same category, excluding current
        related = (
            Product.query
            .filter(
                Product.category_id == product.category_id,
                Product.product_id != product.product_id,
            )
            .limit(limit)
            .all()
        )

        # If not enough, pad with same-brand products
        if len(related) < limit and product.brand:
            existing_ids = {p.product_id for p in related}
            existing_ids.add(product.product_id)
            brand_products = (
                Product.query
                .filter(
                    Product.brand == product.brand,
                    Product.product_id.notin_(existing_ids),
                )
                .limit(limit - len(related))
                .all()
            )
            related.extend(brand_products)
    else:
        # No category — fallback to same brand
        related = (
            Product.query
            .filter(
                Product.brand == product.brand,
                Product.product_id != product.product_id,
            )
            .limit(limit)
            .all()
        )

    return jsonify({
        "products": [p.to_card_dict() for p in related],
    })


# ─────────────────────────────────────────────
#  GET /api/brands — distinct brands
# ─────────────────────────────────────────────

@app.route("/api/brands")
def get_brands():
    rows = (
        db.session.query(Product.brand)
        .filter(Product.brand.isnot(None), Product.brand != "")
        .distinct()
        .order_by(Product.brand)
        .all()
    )
    return jsonify({"brands": [r[0] for r in rows]})


# ─────────────────────────────────────────────
#  Order helpers
# ─────────────────────────────────────────────

FREE_SHIPPING_THRESHOLD = 50.0
FLAT_SHIPPING = 7.99
TAX_RATE = 0.08

BILLING_REQUIRED = ["first_name", "last_name", "address1", "city", "state", "zip"]


def _order_totals(subtotal):
    shipping = 0.0 if subtotal >= FREE_SHIPPING_THRESHOLD else FLAT_SHIPPING
    tax = round(subtotal * TAX_RATE, 2)
    total = round(subtotal + shipping + tax, 2)
    return shipping, tax, total


def tier_price(product, qty):
    """Quantity-break unit price: the deepest tier whose min_qty is met."""
    price = float(product.unit_price or 0)
    for tier in sorted(product.price_tiers or [], key=lambda t: t.get("min_qty", 0)):
        try:
            if qty >= int(tier["min_qty"]):
                price = float(tier["price"])
        except (KeyError, TypeError, ValueError):
            continue
    return price


NET30_TERM_DAYS = 30


def net30_usage(user_id):
    """Open (unpaid, uncancelled) Net 30 balance for a user."""
    from sqlalchemy import func as sqlfunc
    return float(
        db.session.query(sqlfunc.coalesce(sqlfunc.sum(Order.total), 0))
        .filter(
            Order.user_id == user_id,
            Order.payment_method == "net30",
            Order.paid_at.is_(None),
            Order.status != "cancelled",
        )
        .scalar()
    )


def resolve_payment(data, user, amount):
    """Validate the requested payment method for `amount`.

    Returns (payment_method, error). Net 30 requires an approved account
    with enough available credit."""
    method = (data.get("payment_method") or "card_demo").strip()
    if method not in ("card_demo", "net30"):
        return None, "payment_method must be card_demo or net30"
    if method == "net30":
        if user is None:
            return None, "Sign in to use Net 30 terms"
        if (user.net30_status or "none") != "approved":
            return None, "Net 30 is not approved on this account — apply from My Account"
        available = float(user.net30_limit or 0) - net30_usage(user.user_id)
        if amount > available + 0.001:
            return None, (f"Net 30 credit exceeded — available "
                          f"${available:,.2f}, order total ${amount:,.2f}")
    return method, None


def apply_net30_terms(order):
    """Stamp invoice number + due date on a net30 order (after flush)."""
    from datetime import timedelta
    order.invoice_number = f"INV-{datetime.now().year}-{order.order_id:06d}"
    order.net30_due_date = datetime.now() + timedelta(days=NET30_TERM_DAYS)


def _current_user_id():
    """user_id from a Bearer token, or None for guests."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        from auth import _decode_token
        payload = _decode_token(auth_header.split(" ", 1)[1])
        if payload:
            return payload.get("user_id")
    return None


def _billing_fields(data, ship):
    """Resolve billing address: explicit fields, or a copy of shipping.

    `ship` is a dict with first_name/last_name/phone/address1/address2/
    city/state/zip keys. Returns (dict of bill_* kwargs, error or None).
    """
    same = bool(data.get("billing_same_as_shipping", True))
    if same:
        src = ship
    else:
        src = {k: (data.get(f"bill_{k}") or "").strip()
               for k in ["first_name", "last_name", "phone", "address1",
                         "address2", "city", "state", "zip"]}
        missing = [k for k in BILLING_REQUIRED if not src.get(k)]
        if missing:
            return None, f"Missing billing fields: {', '.join(missing)}"
    return {
        "billing_same_as_shipping": same,
        "bill_first_name": src.get("first_name", ""),
        "bill_last_name": src.get("last_name", ""),
        "bill_phone": src.get("phone", ""),
        "bill_address1": src.get("address1", ""),
        "bill_address2": src.get("address2", ""),
        "bill_city": src.get("city", ""),
        "bill_state": src.get("state", ""),
        "bill_zip": src.get("zip", ""),
    }, None


# ─────────────────────────────────────────────
#  POST /api/orders — create order (single checkout)
# ─────────────────────────────────────────────

@app.route("/api/orders", methods=["POST"])
def create_order():
    data = request.get_json(silent=True) or {}

    # Validate required shipping fields
    required = [
        "ship_first_name", "ship_last_name", "ship_email",
        "ship_address1", "ship_city", "ship_state", "ship_zip",
    ]
    missing = [f for f in required if not (data.get(f) or "").strip()]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    cart_items = data.get("items", [])
    if not cart_items:
        return jsonify({"error": "Cart is empty"}), 400

    # Billing address (defaults to a copy of shipping)
    ship_src = {
        "first_name": data["ship_first_name"],
        "last_name": data["ship_last_name"],
        "phone": data.get("ship_phone", ""),
        "address1": data["ship_address1"],
        "address2": data.get("ship_address2", ""),
        "city": data["ship_city"],
        "state": data["ship_state"],
        "zip": data["ship_zip"],
    }
    billing, bill_err = _billing_fields(data, ship_src)
    if bill_err:
        return jsonify({"error": bill_err}), 400

    # Build items — prices/titles come from the catalog when the SKU is
    # known so the client can't submit stale or tampered prices. Tier
    # (quantity-break) pricing is applied per line.
    subtotal = 0.0
    order_items = []
    for item in cart_items:
        code = (item.get("zoro_code") or "").strip()
        qty = max(int(item.get("quantity", 1)), 1)
        product = Product.query.filter_by(zoro_code=code).first() if code else None
        if product and product.unit_price is not None:
            price = tier_price(product, qty)
            title = product.title
        else:
            price = float(item.get("unit_price") or 0)
            title = item.get("title", "")
        subtotal += price * qty
        order_items.append(OrderItem(
            zoro_code=code,
            product_id=product.product_id if product else None,
            title=title,
            unit_price=price,
            quantity=qty,
        ))

    shipping, tax, total = _order_totals(subtotal)
    user_id = _current_user_id()
    user = User.query.get(user_id) if user_id else None

    payment_method, pay_err = resolve_payment(data, user, total)
    if pay_err:
        return jsonify({"error": pay_err}), 402

    order = Order(
        user_id=user_id,
        status="confirmed",
        source="web",
        payment_method=payment_method,
        ship_first_name=data["ship_first_name"],
        ship_last_name=data["ship_last_name"],
        ship_email=data["ship_email"],
        ship_phone=data.get("ship_phone", ""),
        ship_address1=data["ship_address1"],
        ship_address2=data.get("ship_address2", ""),
        ship_city=data["ship_city"],
        ship_state=data["ship_state"],
        ship_zip=data["ship_zip"],
        notification_email=data["ship_email"],
        subtotal=subtotal,
        shipping_cost=shipping,
        tax=tax,
        total=total,
        **billing,
    )
    order.items = order_items
    db.session.add(order)
    db.session.flush()
    if payment_method == "net30":
        apply_net30_terms(order)
    db.session.commit()

    # Confirmation email + bell notification
    subject, body = confirmation_email(order)
    record_email(order.notification_email, subject, body,
                 "confirmation", order_id=order.order_id)
    if payment_method == "net30":
        subject, body = invoice_email(order)
        record_email(order.notification_email, subject, body,
                     "invoice", order_id=order.order_id)
    push_notification(
        user_id,
        f"Order #{order.order_id} confirmed",
        f"Total ${total:,.2f} — we'll email you when it ships.",
        notif_type="order_placed",
        order_id=order.order_id,
    )

    log.info(f"Order #{order.order_id} created — total ${total}")
    return jsonify({"order": order.to_dict()}), 201


# ─────────────────────────────────────────────
#  GET /api/orders/<id> — single order
# ─────────────────────────────────────────────

@app.route("/api/orders/<int:order_id>")
def get_order(order_id):
    order = Order.query.get(order_id)
    if not order:
        return jsonify({"error": "Order not found"}), 404
    return jsonify({"order": order.to_dict()})


# ─────────────────────────────────────────────
#  GET /api/orders — user's orders (auth)
# ─────────────────────────────────────────────

@app.route("/api/my-orders")
@login_required
def get_my_orders():
    orders = (
        Order.query
        .filter_by(user_id=g.current_user.user_id)
        .order_by(Order.created_at.desc())
        .all()
    )
    return jsonify({"orders": [o.to_dict() for o in orders]})


# ─────────────────────────────────────────────
#  BULK ORDERING
#  Zoro.com doesn't allow bulk order placement — this does. Each row is
#  its own order (SKU + qty + ship-to address); the batch shares one
#  billing address and one notification email.
# ─────────────────────────────────────────────

MAX_BULK_ROWS = 100

ROW_REQUIRED = {
    "sku": "SKU",
    "first_name": "recipient first name",
    "last_name": "recipient last name",
    "address1": "address line 1",
    "city": "city",
    "state": "state",
    "zip": "ZIP code",
}


def _validate_bulk_row(row):
    """Returns (product, qty, error). Product lookup is case-insensitive."""
    sku = (row.get("sku") or "").strip()
    if not sku:
        return None, 0, "SKU is required"

    product = Product.query.filter(Product.zoro_code.ilike(sku)).first()
    if product is None:
        return None, 0, f"SKU '{sku}' not found in catalog"

    try:
        qty = int(row.get("quantity", 1))
    except (TypeError, ValueError):
        return product, 0, "Quantity must be a whole number"
    if qty < 1:
        return product, 0, "Quantity must be at least 1"

    min_qty = product.min_order_qty or 1
    if qty < min_qty:
        return product, qty, f"Minimum order quantity for {product.zoro_code} is {min_qty}"

    if product.unit_price is None:
        return product, qty, f"{product.zoro_code} has no price — cannot order online"

    return product, qty, None


def _validate_bulk_address(row):
    missing = [label for key, label in ROW_REQUIRED.items()
               if key != "sku" and not (row.get(key) or "").strip()]
    if missing:
        return f"Missing {', '.join(missing)}"
    return None


@app.route("/api/orders/bulk/validate", methods=["POST"])
def validate_bulk_orders():
    """Validate rows without placing anything. Returns catalog info per
    row so the grid can show live product titles, prices and stock."""
    data = request.get_json(silent=True) or {}
    rows = data.get("orders", [])
    if not isinstance(rows, list):
        return jsonify({"error": "orders must be a list"}), 400
    if len(rows) > MAX_BULK_ROWS:
        return jsonify({"error": f"Maximum {MAX_BULK_ROWS} rows per batch"}), 400

    results = []
    for i, row in enumerate(rows, start=1):
        product, qty, err = _validate_bulk_row(row)
        addr_err = _validate_bulk_address(row) if data.get("check_address") else None
        entry = {"row": i, "sku": (row.get("sku") or "").strip()}
        if product is not None:
            unit = float(product.unit_price) if product.unit_price is not None else None
            tiered = tier_price(product, qty) if (unit is not None and qty) else unit
            entry.update({
                "found": True,
                "zoro_code": product.zoro_code,
                "title": product.title,
                "brand": product.brand,
                "unit_price": tiered if tiered is not None else unit,
                "base_price": unit,
                "tier_applied": bool(tiered is not None and unit is not None and tiered < unit),
                "price_tiers": product.price_tiers or [],
                "image_url": product.image_url,
                "availability": product.availability,
                "min_order_qty": product.min_order_qty or 1,
                "uom": product.uom,
                "line_total": round(tiered * qty, 2) if (tiered is not None and qty) else None,
            })
        else:
            entry["found"] = False
        errors = [e for e in [err, addr_err] if e]
        entry["valid"] = not errors
        if errors:
            entry["error"] = "; ".join(errors)
        results.append(entry)

    return jsonify({
        "results": results,
        "valid_count": sum(1 for r in results if r["valid"]),
        "invalid_count": sum(1 for r in results if not r["valid"]),
    })


def place_bulk_batch(data, user_id, source="bulk"):
    """Core bulk placement shared by the web pad and /api/v1.

    Places one order per row (valid rows succeed even when others fail)
    and returns (response_dict, http_status)."""
    rows = data.get("orders", [])
    billing_in = data.get("billing") or {}
    notification_email = (data.get("notification_email") or "").strip()

    if not rows:
        return {"error": "No order rows provided"}, 400
    if len(rows) > MAX_BULK_ROWS:
        return {"error": f"Maximum {MAX_BULK_ROWS} rows per batch"}, 400
    if not notification_email or "@" not in notification_email:
        return {"error": "A valid notification email is required"}, 400

    missing_billing = [label for label in BILLING_REQUIRED
                       if not (billing_in.get(label) or "").strip()]
    if missing_billing:
        return {
            "error": f"Missing billing fields: {', '.join(missing_billing)}"
        }, 400

    user = User.query.get(user_id) if user_id else None

    # Pre-compute the batch merchandise total for the credit check
    batch_total_estimate = 0.0
    for row in rows:
        product, qty, err = _validate_bulk_row(row)
        if not err and product is not None:
            line_sub = tier_price(product, qty) * qty
            _, _, line_total = _order_totals(line_sub)
            batch_total_estimate += line_total
    payment_method, pay_err = resolve_payment(data, user, batch_total_estimate)
    if pay_err:
        return {"error": pay_err}, 402

    batch_id = "ZB-{}-{}".format(
        datetime.now().strftime("%Y%m%d"),
        "".join(random.choices(string.ascii_uppercase + string.digits, k=4)),
    )

    billing = {
        "billing_same_as_shipping": False,
        "bill_first_name": billing_in.get("first_name", "").strip(),
        "bill_last_name": billing_in.get("last_name", "").strip(),
        "bill_phone": billing_in.get("phone", "").strip(),
        "bill_address1": billing_in.get("address1", "").strip(),
        "bill_address2": billing_in.get("address2", "").strip(),
        "bill_city": billing_in.get("city", "").strip(),
        "bill_state": billing_in.get("state", "").strip(),
        "bill_zip": billing_in.get("zip", "").strip(),
    }

    results = []
    created_orders = []
    failed_rows = []

    for i, row in enumerate(rows, start=1):
        product, qty, err = _validate_bulk_row(row)
        addr_err = _validate_bulk_address(row)
        if err or addr_err:
            entry = {
                "row": i,
                "sku": (row.get("sku") or "").strip(),
                "status": "error",
                "error": "; ".join(e for e in [err, addr_err] if e),
            }
            results.append(entry)
            failed_rows.append(entry)
            continue

        unit_price = tier_price(product, qty)
        subtotal = round(unit_price * qty, 2)
        shipping, tax, total = _order_totals(subtotal)

        order = Order(
            user_id=user_id,
            status="confirmed",
            source=source,
            payment_method=payment_method,
            bulk_batch_id=batch_id,
            notification_email=notification_email,
            ship_first_name=row["first_name"].strip(),
            ship_last_name=row["last_name"].strip(),
            ship_email=(row.get("email") or notification_email).strip(),
            ship_phone=(row.get("phone") or "").strip(),
            ship_address1=row["address1"].strip(),
            ship_address2=(row.get("address2") or "").strip(),
            ship_city=row["city"].strip(),
            ship_state=row["state"].strip(),
            ship_zip=row["zip"].strip(),
            subtotal=subtotal,
            shipping_cost=shipping,
            tax=tax,
            total=total,
            **billing,
        )
        order.items = [OrderItem(
            zoro_code=product.zoro_code,
            product_id=product.product_id,
            title=product.title,
            unit_price=unit_price,
            quantity=qty,
        )]
        db.session.add(order)
        db.session.flush()
        if payment_method == "net30":
            apply_net30_terms(order)
        db.session.commit()
        created_orders.append(order)

        # Per-order confirmation email (part of that order's thread)
        subject, body = confirmation_email(order)
        record_email(notification_email, subject, body, "confirmation",
                     order_id=order.order_id, bulk_batch_id=batch_id)
        if payment_method == "net30":
            subject, body = invoice_email(order)
            record_email(notification_email, subject, body, "invoice",
                         order_id=order.order_id, bulk_batch_id=batch_id)

        results.append({
            "row": i,
            "sku": product.zoro_code,
            "status": "created",
            "order_id": order.order_id,
            "title": product.title,
            "quantity": qty,
            "unit_price": unit_price,
            "total": total,
            "ship_to": f"{order.ship_first_name} {order.ship_last_name}, "
                       f"{order.ship_city}, {order.ship_state} {order.ship_zip}",
        })

    # Batch-level summary email + one bell notification
    if created_orders:
        subject, body = batch_summary_email(
            batch_id, created_orders, failed_rows, notification_email
        )
        record_email(notification_email, subject, body, "batch_summary",
                     bulk_batch_id=batch_id)
        push_notification(
            user_id,
            f"Bulk batch {batch_id} placed — "
            f"{len(created_orders)} order{'s' if len(created_orders) != 1 else ''} created",
            f"Order IDs: {', '.join('#' + str(o.order_id) for o in created_orders)}"
            + (f" · {len(failed_rows)} row(s) failed" if failed_rows else ""),
            notif_type="batch_placed",
            bulk_batch_id=batch_id,
        )

    log.info("Bulk batch %s — %s created, %s failed (%s)",
             batch_id, len(created_orders), len(failed_rows), source)

    return {
        "batch_id": batch_id,
        "created_count": len(created_orders),
        "failed_count": len(failed_rows),
        "notification_email": notification_email,
        "payment_method": payment_method,
        "results": results,
        "batch_total": round(sum(float(o.total) for o in created_orders), 2),
    }, 201 if created_orders else 422


@app.route("/api/orders/bulk", methods=["POST"])
def create_bulk_orders():
    """Web bulk order pad endpoint — thin wrapper over place_bulk_batch."""
    data = request.get_json(silent=True) or {}
    body, status = place_bulk_batch(data, _current_user_id(), source="bulk")
    return jsonify(body), status


# ─────────────────────────────────────────────
#  GET /api/orders/batch/<batch_id> — all orders in a bulk batch
# ─────────────────────────────────────────────

@app.route("/api/orders/batch/<batch_id>")
def get_batch(batch_id):
    orders = (
        Order.query
        .filter_by(bulk_batch_id=batch_id)
        .order_by(Order.order_id.asc())
        .all()
    )
    if not orders:
        return jsonify({"error": "Batch not found"}), 404
    emails = (
        OrderEmail.query
        .filter_by(bulk_batch_id=batch_id, email_type="batch_summary")
        .all()
    )
    return jsonify({
        "batch_id": batch_id,
        "orders": [o.to_dict() for o in orders],
        "batch_emails": [e.to_dict() for e in emails],
    })


# ─────────────────────────────────────────────
#  GET /api/orders/<id>/emails — the order's email thread
# ─────────────────────────────────────────────

@app.route("/api/orders/<int:order_id>/emails")
def get_order_emails(order_id):
    emails = (
        OrderEmail.query
        .filter_by(order_id=order_id)
        .order_by(OrderEmail.created_at.asc())
        .all()
    )
    return jsonify({"emails": [e.to_dict() for e in emails]})


# ─────────────────────────────────────────────
#  Notifications (header bell)
# ─────────────────────────────────────────────

@app.route("/api/notifications")
@login_required
def get_notifications():
    notifications = (
        Notification.query
        .filter_by(user_id=g.current_user.user_id)
        .order_by(Notification.created_at.desc())
        .limit(30)
        .all()
    )
    unread = (
        Notification.query
        .filter_by(user_id=g.current_user.user_id, is_read=False)
        .count()
    )
    return jsonify({
        "notifications": [n.to_dict() for n in notifications],
        "unread_count": unread,
    })


@app.route("/api/notifications/mark-read", methods=["POST"])
@login_required
def mark_notifications_read():
    data = request.get_json(silent=True) or {}
    query = Notification.query.filter_by(user_id=g.current_user.user_id)
    if not data.get("all"):
        ids = data.get("ids", [])
        if not ids:
            return jsonify({"error": "Provide ids or all: true"}), 400
        query = query.filter(Notification.notification_id.in_(ids))
    updated = query.update({"is_read": True}, synchronize_session=False)
    db.session.commit()
    return jsonify({"updated": updated})


# ─────────────────────────────────────────────
#  NET 30 credit accounts
# ─────────────────────────────────────────────

@app.route("/api/net30/apply", methods=["POST"])
@login_required
def net30_apply():
    user = g.current_user
    if user.net30_status == "approved":
        return jsonify({"error": "Net 30 is already approved on this account"}), 400
    if user.net30_status == "pending":
        return jsonify({"error": "Your application is already under review"}), 400

    data = request.get_json(silent=True) or {}
    company = (data.get("company") or "").strip()
    if not company:
        return jsonify({"error": "Company name is required"}), 400

    user.company = company
    user.net30_status = "pending"
    user.net30_applied_at = datetime.now()
    db.session.commit()

    push_notification(
        user.user_id,
        "Net 30 application received",
        f"We're reviewing the application for {company} — you'll be notified here.",
        notif_type="net30",
    )
    log.info("Net 30 application: %s (%s)", user.email, company)
    return jsonify({"user": user.to_dict()})


@app.route("/api/net30/status")
@login_required
def net30_status():
    user = g.current_user
    used = net30_usage(user.user_id)
    invoices = (
        Order.query
        .filter(
            Order.user_id == user.user_id,
            Order.payment_method == "net30",
            Order.status != "cancelled",
        )
        .order_by(Order.created_at.desc())
        .all()
    )
    now = datetime.now()
    return jsonify({
        "status": user.net30_status or "none",
        "company": user.company,
        "limit": float(user.net30_limit or 0),
        "used": used,
        "available": max(float(user.net30_limit or 0) - used, 0),
        "invoices": [{
            "order_id": o.order_id,
            "invoice_number": o.invoice_number,
            "total": float(o.total),
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "due_date": o.net30_due_date.isoformat() if o.net30_due_date else None,
            "paid_at": o.paid_at.isoformat() if o.paid_at else None,
            "overdue": bool(o.paid_at is None and o.net30_due_date and o.net30_due_date < now),
        } for o in invoices],
    })


# ─────────────────────────────────────────────
#  RETURNS (RMA)
# ─────────────────────────────────────────────

RETURN_REASONS = ["damaged", "wrong_item", "defective", "no_longer_needed", "other"]
RETURNABLE_STATUSES = ["shipped", "delivered"]


@app.route("/api/returns", methods=["POST"])
def request_return():
    data = request.get_json(silent=True) or {}
    order_id = data.get("order_id")
    try:
        order_id = int(order_id)
    except (TypeError, ValueError):
        return jsonify({"error": "order_id is required"}), 400

    order = Order.query.get(order_id)
    if order is None:
        return jsonify({"error": f"Order #{order_id} not found"}), 404
    if order.status not in RETURNABLE_STATUSES:
        return jsonify({"error": f"Only shipped/delivered orders can be returned "
                                 f"(this one is {order.status})"}), 400

    user_id = _current_user_id()
    email = (data.get("email") or "").strip() or order.notification_email or order.ship_email
    # Guests must know the order's email; signed-in users must own the order
    if user_id is not None:
        if order.user_id is not None and order.user_id != user_id:
            return jsonify({"error": "This order belongs to a different account"}), 403
    elif email.lower() not in (
        (order.notification_email or "").lower(), (order.ship_email or "").lower()
    ):
        return jsonify({"error": "Email does not match this order"}), 403

    existing = Return.query.filter_by(order_id=order_id).filter(
        Return.status.in_(["requested", "approved", "received"])
    ).first()
    if existing:
        return jsonify({"error": f"A return ({existing.rma_number or 'pending'}) "
                                 f"is already open for this order"}), 409

    reason = data.get("reason")
    if reason not in RETURN_REASONS:
        reason = "other"

    ret = Return(
        order_id=order_id,
        user_id=user_id or order.user_id,
        email=email,
        reason=reason,
        details=(data.get("details") or "").strip(),
        status="requested",
    )
    db.session.add(ret)
    db.session.commit()

    subject, body = return_status_email(ret, order)
    record_email(email, subject, body, "return_update", order_id=order_id)
    push_notification(
        ret.user_id,
        f"Return requested for order #{order_id}",
        "We'll review it and email you an RMA number.",
        notif_type="return", order_id=order_id,
    )
    return jsonify({"return": ret.to_dict()}), 201


@app.route("/api/my-returns")
@login_required
def my_returns():
    returns = (
        Return.query
        .filter_by(user_id=g.current_user.user_id)
        .order_by(Return.updated_at.desc())
        .all()
    )
    return jsonify({"returns": [r.to_dict() for r in returns]})


# ─────────────────────────────────────────────
#  API KEYS (for the /api/v1 ordering API)
# ─────────────────────────────────────────────

@app.route("/api/keys", methods=["GET"])
@login_required
def list_api_keys():
    keys = (
        ApiKey.query
        .filter_by(user_id=g.current_user.user_id, is_active=True)
        .order_by(ApiKey.created_at.desc())
        .all()
    )
    return jsonify({"keys": [k.to_dict() for k in keys]})


@app.route("/api/keys", methods=["POST"])
@login_required
def create_api_key():
    import hashlib
    import secrets

    name = ((request.get_json(silent=True) or {}).get("name") or "").strip() or "default"
    active = ApiKey.query.filter_by(
        user_id=g.current_user.user_id, is_active=True
    ).count()
    if active >= 5:
        return jsonify({"error": "Maximum 5 active keys — revoke one first"}), 400

    raw = "zsk_" + secrets.token_hex(20)
    key = ApiKey(
        user_id=g.current_user.user_id,
        name=name,
        key_prefix=raw[:12],
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
    )
    db.session.add(key)
    db.session.commit()
    # The raw key is returned exactly once
    return jsonify({"key": key.to_dict(), "api_key": raw}), 201


@app.route("/api/keys/<int:key_id>", methods=["DELETE"])
@login_required
def revoke_api_key(key_id):
    key = ApiKey.query.filter_by(
        key_id=key_id, user_id=g.current_user.user_id
    ).first()
    if key is None:
        return jsonify({"error": "Key not found"}), 404
    key.is_active = False
    db.session.commit()
    return jsonify({"revoked": key_id})


# ─────────────────────────────────────────────
#  Site content (extracted from zoro page dumps)
# ─────────────────────────────────────────────

@app.route("/api/site/ribbon")
def site_ribbon():
    return jsonify({"messages": SiteSetting.get("messaging_ribbon", [])})


# ─────────────────────────────────────────────
#  CASES — customer support tickets
# ─────────────────────────────────────────────

CASE_CATEGORIES = ["order_issue", "return", "billing", "shipping",
                   "product_question", "other"]


@app.route("/api/cases", methods=["POST"])
def create_case():
    """Open a support case (contact form, order issue, return...).
    Works for guests; signed-in users get it linked to their account."""
    data = request.get_json(silent=True) or {}

    email = (data.get("email") or "").strip()
    subject = (data.get("subject") or "").strip()
    message = (data.get("message") or "").strip()
    if not email or "@" not in email:
        return jsonify({"error": "A valid email is required"}), 400
    if not subject:
        return jsonify({"error": "Subject is required"}), 400
    if not message:
        return jsonify({"error": "Message is required"}), 400

    category = data.get("category")
    if category not in CASE_CATEGORIES:
        category = "other"

    order_id = data.get("order_id")
    if order_id:
        try:
            order_id = int(order_id)
            if not Order.query.get(order_id):
                return jsonify({"error": f"Order #{order_id} not found"}), 404
        except (TypeError, ValueError):
            return jsonify({"error": "order_id must be a number"}), 400
    else:
        order_id = None

    user_id = _current_user_id()
    case = Case(
        user_id=user_id,
        order_id=order_id,
        email=email,
        name=(data.get("name") or "").strip(),
        subject=subject,
        category=category,
    )
    case.messages = [CaseMessage(sender="customer", body=message)]
    db.session.add(case)
    db.session.commit()

    push_notification(
        user_id,
        f"Case #{case.case_id} opened — {subject[:60]}",
        "Our support team will get back to you shortly.",
        notif_type="case",
        order_id=order_id,
    )
    log.info("Case #%s opened (%s) by %s", case.case_id, category, email)
    return jsonify({"case": case.to_dict(include_messages=True)}), 201


@app.route("/api/my-cases")
@login_required
def my_cases():
    cases = (
        Case.query
        .filter_by(user_id=g.current_user.user_id)
        .order_by(Case.updated_at.desc())
        .all()
    )
    return jsonify({"cases": [c.to_dict(include_messages=True) for c in cases]})


@app.route("/api/cases/<int:case_id>/reply", methods=["POST"])
@login_required
def customer_reply_case(case_id):
    case = Case.query.get(case_id)
    if not case or case.user_id != g.current_user.user_id:
        return jsonify({"error": "Case not found"}), 404

    body = ((request.get_json(silent=True) or {}).get("body") or "").strip()
    if not body:
        return jsonify({"error": "Reply body is required"}), 400

    db.session.add(CaseMessage(case_id=case.case_id, sender="customer", body=body))
    case.status = "open"
    db.session.commit()
    return jsonify({"case": case.to_dict(include_messages=True)})


# ─────────────────────────────────────────────
#  GET /api/products/<zoro_code>/variations — size/pack/length options
# ─────────────────────────────────────────────

@app.route("/api/products/<zoro_code>/variations")
def get_product_variations(zoro_code):
    product = Product.query.filter_by(zoro_code=zoro_code).first()
    if product is None:
        return jsonify({"error": "Product not found"}), 404

    if product.variant_group:
        variants = (
            Product.query
            .filter(Product.variant_group == product.variant_group)
            .all()
        )
    else:
        # Heuristic fallback: same brand + shared leading title words
        variants = []
        if product.brand and product.title:
            words = [w for w in re.split(r"[\s,]+", product.title) if w][:3]
            prefix = " ".join(words)
            if len(prefix) >= 8:
                variants = (
                    Product.query
                    .filter(
                        Product.brand == product.brand,
                        Product.title.ilike(f"{prefix}%"),
                    )
                    .limit(12)
                    .all()
                )

    if len(variants) <= 1:
        return jsonify({"current": zoro_code, "variations": []})

    variants.sort(key=lambda p: float(p.unit_price or 0))
    prices = [float(p.unit_price) for p in variants if p.unit_price is not None]
    return jsonify({
        "current": zoro_code,
        "lowest_price": min(prices) if prices else None,
        "highest_price": max(prices) if prices else None,
        "variations": [p.to_card_dict() for p in variants],
    })


# ─────────────────────────────────────────────
#  Legacy endpoint (kept for backward compatibility)
# ─────────────────────────────────────────────

@app.route("/products")
def get_products_legacy():
    """Legacy endpoint: reads from JSON files on disk."""
    products = []

    products_dir = os.path.join(
        os.path.dirname(__file__), "products"
    )

    if os.path.isdir(products_dir):
        for file in os.listdir(products_dir):
            if file.endswith(".json"):
                with open(
                    os.path.join(products_dir, file),
                    "r",
                    encoding="utf-8"
                ) as f:
                    products.append(json.load(f))

    return jsonify(products)


# ─────────────────────────────────────────────
#  Start
# ─────────────────────────────────────────────

def ensure_admin_user():
    """Dev convenience: guarantee one admin account exists so the panel
    is reachable out of the box. Change the password in production."""
    if User.query.filter_by(is_admin=True).first():
        return
    from werkzeug.security import generate_password_hash
    email = os.getenv("ADMIN_EMAIL", "admin@zorostore.dev")
    password = os.getenv("ADMIN_PASSWORD", "admin1234")
    user = User.query.filter_by(email=email).first()
    if user is None:
        user = User(
            email=email,
            password_hash=generate_password_hash(password),
            first_name="Store",
            last_name="Admin",
        )
        db.session.add(user)
    user.is_admin = True
    db.session.commit()
    log.warning("👑 Admin account ready: %s / %s — change this in production!",
                email, password)


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        run_migrations()
        ensure_admin_user()
        log.info("✅ Database tables created/verified (migrations applied).")

    is_dev = os.getenv("FLASK_ENV", "development") == "development"

    # Start the fulfillment worker once per process. Under the debug
    # reloader the module loads twice — only the reloader child
    # (WERKZEUG_RUN_MAIN=true) should run the worker.
    if not is_dev or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_fulfillment_worker(app)

    app.run(
        debug=is_dev,
        host="0.0.0.0",
        port=5001
    )