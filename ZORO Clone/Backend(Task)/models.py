from database import db
from sqlalchemy.dialects.postgresql import JSONB


class User(db.Model):
    __tablename__ = 'users'

    user_id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.Text, nullable=False)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    phone = db.Column(db.String(30), default="")
    company = db.Column(db.String(150), default="")

    # Net 30 credit account
    net30_status = db.Column(db.String(20), default="none")  # none|pending|approved|rejected
    net30_limit = db.Column(db.Numeric(10, 2), default=0)
    net30_applied_at = db.Column(db.DateTime, nullable=True)
    address_line1 = db.Column(db.String(255), default="")
    address_line2 = db.Column(db.String(255), default="")
    city = db.Column(db.String(100), default="")
    state = db.Column(db.String(50), default="")
    zip_code = db.Column(db.String(20), default="")
    created_at = db.Column(
        db.DateTime, server_default=db.func.now(), nullable=False
    )

    # Relationships
    orders = db.relationship('Order', backref='user', lazy=True)
    cart_items = db.relationship('CartItem', backref='user', lazy=True)

    def to_dict(self):
        return {
            "user_id": self.user_id,
            "email": self.email,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "is_admin": self.is_admin,
            "phone": self.phone,
            "company": self.company,
            "net30_status": self.net30_status or "none",
            "net30_limit": float(self.net30_limit or 0),
            "address_line1": self.address_line1,
            "address_line2": self.address_line2,
            "city": self.city,
            "state": self.state,
            "zip_code": self.zip_code,
        }


class Category(db.Model):
    __tablename__ = 'categories'

    category_id = db.Column(db.Integer, primary_key=True)
    category_name = db.Column(db.String(100), nullable=False)
    parent_category_id = db.Column(
        db.Integer,
        db.ForeignKey('categories.category_id'),
        nullable=True
    )

    # Relationships
    products = db.relationship('Product', backref='category', lazy=True)
    parent = db.relationship(
        'Category', remote_side=[category_id], backref='subcategories'
    )

    def get_path(self):
        """Walk up the tree to build the full category path."""
        path = []
        current = self
        while current:
            path.insert(0, {
                "category_id": current.category_id,
                "category_name": current.category_name,
            })
            current = current.parent
        return path

    def to_dict(self):
        return {
            "category_id": self.category_id,
            "category_name": self.category_name,
            "parent_category_id": self.parent_category_id,
        }


class Product(db.Model):
    __tablename__ = 'products'

    product_id = db.Column(db.Integer, primary_key=True)
    zoro_code = db.Column(db.String(50), unique=True, nullable=False)
    title = db.Column(db.Text, nullable=False)
    brand = db.Column(db.String(150))
    unit_price = db.Column(db.Numeric(10, 2))
    availability = db.Column(db.String(100))
    category_id = db.Column(
        db.Integer,
        db.ForeignKey('categories.category_id'),
        nullable=True
    )

    mfr_number = db.Column(db.String(100))
    upc = db.Column(db.String(100))
    country_of_origin = db.Column(db.String(100))
    image_url = db.Column(db.Text)
    product_url = db.Column(db.Text)
    description = db.Column(db.Text)

    # ── TSV specific columns ──
    lead_time = db.Column(db.String(100))
    is_dropship = db.Column(db.Boolean, default=False)
    uom = db.Column(db.String(20))
    freight_surcharge = db.Column(db.Numeric(10, 2), default=0.0)
    min_order_qty = db.Column(db.Integer, default=1)
    is_ltl = db.Column(db.Boolean, default=False)
    
    # ── Dimensions and Shipping (from TSV) ──
    ship_pack_height = db.Column(db.Numeric(10, 2))
    ship_pack_length = db.Column(db.Numeric(10, 2))
    ship_pack_width = db.Column(db.Numeric(10, 2))
    ship_pack_weight = db.Column(db.Numeric(10, 2))
    is_blind_ship = db.Column(db.Boolean, default=False)

    # ── JSONB specifications column ──
    # Stores product specs as a JSON object, e.g.:
    #   {"Voltage": "120V", "Wattage": "15W", "Lumens": "1600"}
    # Queryable, indexable, and much faster than a separate attributes table.
    specifications = db.Column(JSONB, default=dict)

    # ── Variations ──
    # Products sharing the same variant_group are size/pack/length options
    # of one another; variant_label is the option shown to the buyer.
    variant_group = db.Column(db.String(100), index=True)
    variant_label = db.Column(db.String(100))

    # ── Full zoro.com extraction fields ──
    package_qty = db.Column(db.Integer, default=1)
    original_price = db.Column(db.Numeric(10, 2), nullable=True)  # pre-sale price
    erp_id = db.Column(db.String(30))
    sales_status = db.Column(db.String(10))
    is_hazmat = db.Column(db.Boolean, default=False)
    restricted_states = db.Column(JSONB, default=list)

    # ── Quantity-break pricing ──
    # [{"min_qty": 10, "price": 2.99}, ...] — ascending min_qty; the
    # base unit_price applies below the first tier.
    price_tiers = db.Column(JSONB, default=list)

    # Relationships
    images = db.relationship('ProductImage', backref='product', lazy=True)
    attributes = db.relationship(
        'ProductAttribute', backref='product', lazy=True
    )

    def to_dict(self, include_images=True):
        """Serialize product to a dict for API responses."""
        data = {
            "product_id": self.product_id,
            "zoro_code": self.zoro_code,
            "title": self.title,
            "brand": self.brand,
            "unit_price": float(self.unit_price) if self.unit_price else None,
            "availability": self.availability,
            "category_id": self.category_id,
            "category_name": (
                self.category.category_name if self.category else None
            ),
            "category_path": (
                self.category.get_path() if self.category else []
            ),
            "mfr_number": self.mfr_number,
            "upc": self.upc,
            "country_of_origin": self.country_of_origin,
            "image_url": self.image_url,
            "product_url": self.product_url,
            "description": self.description,
            "lead_time": self.lead_time,
            "is_dropship": self.is_dropship,
            "uom": self.uom,
            "freight_surcharge": float(self.freight_surcharge) if self.freight_surcharge else 0.0,
            "min_order_qty": self.min_order_qty,
            "is_ltl": self.is_ltl,
            "ship_pack_height": float(self.ship_pack_height) if self.ship_pack_height else None,
            "ship_pack_length": float(self.ship_pack_length) if self.ship_pack_length else None,
            "ship_pack_width": float(self.ship_pack_width) if self.ship_pack_width else None,
            "ship_pack_weight": float(self.ship_pack_weight) if self.ship_pack_weight else None,
            "is_blind_ship": self.is_blind_ship,
            "specifications": self.specifications or {},
            "variant_group": self.variant_group,
            "variant_label": self.variant_label,
            "package_qty": self.package_qty or 1,
            "original_price": float(self.original_price) if self.original_price else None,
            "erp_id": self.erp_id,
            "sales_status": self.sales_status,
            "is_hazmat": self.is_hazmat,
            "restricted_states": self.restricted_states or [],
            "price_tiers": self.price_tiers or [],
        }

        if include_images:
            data["images"] = [img.to_dict() for img in self.images]

        return data

    def to_card_dict(self):
        """Lightweight dict for product listing cards."""
        return {
            "product_id": self.product_id,
            "zoro_code": self.zoro_code,
            "title": self.title,
            "brand": self.brand,
            "unit_price": float(self.unit_price) if self.unit_price else None,
            "availability": self.availability,
            "image_url": self.image_url,
            "mfr_number": self.mfr_number,
            "lead_time": self.lead_time,
            "uom": self.uom,
            "min_order_qty": self.min_order_qty,
            "price_tiers": self.price_tiers or [],
            "variant_group": self.variant_group,
            "variant_label": self.variant_label,
            "category_name": (
                self.category.category_name if self.category else None
            ),
        }


class ProductAttribute(db.Model):
    """Legacy per-row attribute storage. Kept for compatibility."""
    __tablename__ = 'product_attributes'

    attribute_id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer,
        db.ForeignKey('products.product_id'),
        nullable=False
    )
    attribute_name = db.Column(db.String(255), nullable=False)
    attribute_value = db.Column(db.Text)

    def to_dict(self):
        return {
            "attribute_name": self.attribute_name,
            "attribute_value": self.attribute_value,
        }


class ProductImage(db.Model):
    __tablename__ = 'product_images'

    image_id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer,
        db.ForeignKey('products.product_id'),
        nullable=False
    )
    image_url = db.Column(db.Text)

    def to_dict(self):
        return {
            "image_id": self.image_id,
            "image_url": self.image_url,
        }


class CartItem(db.Model):
    __tablename__ = 'cart_items'

    cart_item_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('users.user_id'), nullable=False
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey('products.product_id'), nullable=False
    )
    quantity = db.Column(db.Integer, default=1, nullable=False)
    created_at = db.Column(
        db.DateTime, server_default=db.func.now(), nullable=False
    )

    product = db.relationship('Product', lazy=True)

    def to_dict(self):
        return {
            "cart_item_id": self.cart_item_id,
            "product": self.product.to_card_dict() if self.product else None,
            "quantity": self.quantity,
        }


class Order(db.Model):
    __tablename__ = 'orders'

    order_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('users.user_id'), nullable=True
    )
    status = db.Column(db.String(50), default="pending", nullable=False)

    # Shipping address
    ship_first_name = db.Column(db.String(100), nullable=False)
    ship_last_name = db.Column(db.String(100), nullable=False)
    ship_email = db.Column(db.String(255), nullable=False)
    ship_phone = db.Column(db.String(30), default="")
    ship_address1 = db.Column(db.String(255), nullable=False)
    ship_address2 = db.Column(db.String(255), default="")
    ship_city = db.Column(db.String(100), nullable=False)
    ship_state = db.Column(db.String(50), nullable=False)
    ship_zip = db.Column(db.String(20), nullable=False)

    # Billing address (mirrors shipping when billing_same_as_shipping)
    billing_same_as_shipping = db.Column(db.Boolean, default=True)
    bill_first_name = db.Column(db.String(100), default="")
    bill_last_name = db.Column(db.String(100), default="")
    bill_phone = db.Column(db.String(30), default="")
    bill_address1 = db.Column(db.String(255), default="")
    bill_address2 = db.Column(db.String(255), default="")
    bill_city = db.Column(db.String(100), default="")
    bill_state = db.Column(db.String(50), default="")
    bill_zip = db.Column(db.String(20), default="")

    # Fulfillment
    tracking_number = db.Column(db.String(60), default="")
    carrier = db.Column(db.String(40), default="")
    shipped_at = db.Column(db.DateTime, nullable=True)

    # Bulk ordering — orders placed together share a bulk_batch_id
    source = db.Column(db.String(20), default="web")  # web | bulk
    bulk_batch_id = db.Column(db.String(40), default="", index=True)
    # All confirmation / shipment emails for this order go here
    notification_email = db.Column(db.String(255), default="")

    # Payment — demo card or Net 30 invoice
    payment_method = db.Column(db.String(20), default="card_demo")  # card_demo | net30
    invoice_number = db.Column(db.String(30), default="")
    net30_due_date = db.Column(db.DateTime, nullable=True)
    paid_at = db.Column(db.DateTime, nullable=True)

    subtotal = db.Column(db.Numeric(10, 2), nullable=False)
    shipping_cost = db.Column(db.Numeric(10, 2), default=0.0)
    tax = db.Column(db.Numeric(10, 2), default=0.0)
    total = db.Column(db.Numeric(10, 2), nullable=False)

    created_at = db.Column(
        db.DateTime, server_default=db.func.now(), nullable=False
    )

    # Relationships
    items = db.relationship('OrderItem', backref='order', lazy=True)
    emails = db.relationship('OrderEmail', backref='order', lazy=True)

    def to_dict(self):
        return {
            "order_id": self.order_id,
            "status": self.status,
            "ship_first_name": self.ship_first_name,
            "ship_last_name": self.ship_last_name,
            "ship_email": self.ship_email,
            "ship_phone": self.ship_phone,
            "ship_address1": self.ship_address1,
            "ship_address2": self.ship_address2,
            "ship_city": self.ship_city,
            "ship_state": self.ship_state,
            "ship_zip": self.ship_zip,
            "billing_same_as_shipping": self.billing_same_as_shipping,
            "bill_first_name": self.bill_first_name,
            "bill_last_name": self.bill_last_name,
            "bill_phone": self.bill_phone,
            "bill_address1": self.bill_address1,
            "bill_address2": self.bill_address2,
            "bill_city": self.bill_city,
            "bill_state": self.bill_state,
            "bill_zip": self.bill_zip,
            "tracking_number": self.tracking_number,
            "carrier": self.carrier,
            "shipped_at": self.shipped_at.isoformat() if self.shipped_at else None,
            "source": self.source,
            "bulk_batch_id": self.bulk_batch_id,
            "notification_email": self.notification_email,
            "payment_method": self.payment_method or "card_demo",
            "invoice_number": self.invoice_number or "",
            "net30_due_date": self.net30_due_date.isoformat() if self.net30_due_date else None,
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
            "subtotal": float(self.subtotal),
            "shipping_cost": float(self.shipping_cost),
            "tax": float(self.tax),
            "total": float(self.total),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "items": [item.to_dict() for item in self.items],
        }


class OrderItem(db.Model):
    __tablename__ = 'order_items'

    order_item_id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(
        db.Integer, db.ForeignKey('orders.order_id'), nullable=False
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey('products.product_id'), nullable=True
    )
    zoro_code = db.Column(db.String(50), nullable=False)
    title = db.Column(db.Text, nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)

    def to_dict(self):
        return {
            "order_item_id": self.order_item_id,
            "zoro_code": self.zoro_code,
            "title": self.title,
            "unit_price": float(self.unit_price),
            "quantity": self.quantity,
        }


class OrderEmail(db.Model):
    """Every email the store sends for an order — the order's email thread.

    When SMTP is configured the mail is really sent; otherwise it is
    recorded with status 'simulated' so the thread is still visible in
    the UI and the flow can be verified end-to-end.
    """
    __tablename__ = 'order_emails'

    email_id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(
        db.Integer, db.ForeignKey('orders.order_id'), nullable=True
    )
    bulk_batch_id = db.Column(db.String(40), default="", index=True)
    # confirmation | processing | shipment | batch_summary
    email_type = db.Column(db.String(30), nullable=False)
    recipient = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.Text, nullable=False)
    body_text = db.Column(db.Text, default="")
    status = db.Column(db.String(20), default="simulated")  # sent | simulated | failed
    created_at = db.Column(
        db.DateTime, server_default=db.func.now(), nullable=False
    )

    def to_dict(self):
        return {
            "email_id": self.email_id,
            "order_id": self.order_id,
            "bulk_batch_id": self.bulk_batch_id,
            "email_type": self.email_type,
            "recipient": self.recipient,
            "subject": self.subject,
            "body_text": self.body_text,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ApiKey(db.Model):
    """API key for the programmatic ordering API (/api/v1).
    Only a SHA-256 hash is stored; the full key is shown once."""
    __tablename__ = 'api_keys'

    key_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('users.user_id'), nullable=False, index=True
    )
    name = db.Column(db.String(100), default="")
    key_prefix = db.Column(db.String(16), nullable=False)   # shown in lists
    key_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(
        db.DateTime, server_default=db.func.now(), nullable=False
    )
    last_used_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            "key_id": self.key_id,
            "name": self.name,
            "key_prefix": self.key_prefix,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
        }


class Return(db.Model):
    """RMA / return request for an order."""
    __tablename__ = 'returns'

    return_id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, nullable=False, index=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('users.user_id'), nullable=True, index=True
    )
    email = db.Column(db.String(255), nullable=False)
    rma_number = db.Column(db.String(20), default="", index=True)
    # damaged | wrong_item | defective | no_longer_needed | other
    reason = db.Column(db.String(40), default="other")
    details = db.Column(db.Text, default="")
    # requested | approved | denied | received | refunded
    status = db.Column(db.String(20), default="requested", nullable=False, index=True)
    refund_amount = db.Column(db.Numeric(10, 2), nullable=True)
    admin_note = db.Column(db.Text, default="")
    created_at = db.Column(
        db.DateTime, server_default=db.func.now(), nullable=False
    )
    updated_at = db.Column(
        db.DateTime, server_default=db.func.now(),
        onupdate=db.func.now(), nullable=False
    )

    def to_dict(self):
        return {
            "return_id": self.return_id,
            "order_id": self.order_id,
            "user_id": self.user_id,
            "email": self.email,
            "rma_number": self.rma_number,
            "reason": self.reason,
            "details": self.details,
            "status": self.status,
            "refund_amount": float(self.refund_amount) if self.refund_amount is not None else None,
            "admin_note": self.admin_note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SiteSetting(db.Model):
    """Key/value store for extracted site content (promo ribbon, etc.)."""
    __tablename__ = 'site_settings'

    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(JSONB, default=dict)

    @staticmethod
    def get(key, default=None):
        row = SiteSetting.query.get(key)
        return row.value if row else default

    @staticmethod
    def set(key, value):
        row = SiteSetting.query.get(key)
        if row is None:
            row = SiteSetting(key=key, value=value)
            db.session.add(row)
        else:
            row.value = value
        db.session.commit()


class Case(db.Model):
    """Customer-service case (support ticket) — order issues, returns,
    billing questions, general contact-form submissions."""
    __tablename__ = 'cases'

    case_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('users.user_id'), nullable=True, index=True
    )
    order_id = db.Column(db.Integer, nullable=True, index=True)
    email = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(150), default="")
    subject = db.Column(db.String(255), nullable=False)
    # order_issue | return | billing | shipping | product_question | other
    category = db.Column(db.String(40), default="other")
    # open | pending | resolved | closed
    status = db.Column(db.String(20), default="open", nullable=False, index=True)
    priority = db.Column(db.String(10), default="normal")  # normal | high
    created_at = db.Column(
        db.DateTime, server_default=db.func.now(), nullable=False
    )
    updated_at = db.Column(
        db.DateTime, server_default=db.func.now(),
        onupdate=db.func.now(), nullable=False
    )

    messages = db.relationship(
        'CaseMessage', backref='case', lazy=True,
        order_by='CaseMessage.created_at'
    )

    def to_dict(self, include_messages=False):
        data = {
            "case_id": self.case_id,
            "user_id": self.user_id,
            "order_id": self.order_id,
            "email": self.email,
            "name": self.name,
            "subject": self.subject,
            "category": self.category,
            "status": self.status,
            "priority": self.priority,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "message_count": len(self.messages),
        }
        if include_messages:
            data["messages"] = [m.to_dict() for m in self.messages]
        return data


class CaseMessage(db.Model):
    __tablename__ = 'case_messages'

    case_message_id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(
        db.Integer, db.ForeignKey('cases.case_id'), nullable=False, index=True
    )
    sender = db.Column(db.String(20), nullable=False)  # customer | admin
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime, server_default=db.func.now(), nullable=False
    )

    def to_dict(self):
        return {
            "case_message_id": self.case_message_id,
            "case_id": self.case_id,
            "sender": self.sender,
            "body": self.body,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Notification(db.Model):
    """In-app notification (header bell) for order / batch events."""
    __tablename__ = 'notifications'

    notification_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('users.user_id'), nullable=True, index=True
    )
    order_id = db.Column(db.Integer, nullable=True)
    bulk_batch_id = db.Column(db.String(40), default="")
    # order_placed | order_processing | order_shipped | batch_placed
    notif_type = db.Column(db.String(30), default="order")
    title = db.Column(db.String(255), nullable=False)
    body = db.Column(db.Text, default="")
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(
        db.DateTime, server_default=db.func.now(), nullable=False
    )

    def to_dict(self):
        return {
            "notification_id": self.notification_id,
            "order_id": self.order_id,
            "bulk_batch_id": self.bulk_batch_id,
            "notif_type": self.notif_type,
            "title": self.title,
            "body": self.body,
            "is_read": self.is_read,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }