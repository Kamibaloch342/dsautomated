import logging
from datetime import datetime
from flask import Blueprint, jsonify, request, g
from database import db
from models import ProjectList, ProjectListItem, Order, OrderItem, User
from auth import login_required
from brand import resolve_db_code, format_eq_code, format_brand, PRIVATE_LABEL
import catalog

log = logging.getLogger(__name__)

lists_bp = Blueprint("lists_bp", __name__)


@lists_bp.route("/api/lists", methods=["GET"])
@login_required
def get_user_lists():
    """Get all saved project lists for the authenticated user."""
    user = g.current_user
    lists = ProjectList.query.filter_by(user_id=user.user_id).order_by(ProjectList.updated_at.desc()).all()
    return jsonify({
        "lists": [l.to_dict(include_items=False) for l in lists]
    })


@lists_bp.route("/api/lists", methods=["POST"])
@login_required
def create_list():
    """Create a new project list."""
    user = g.current_user
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "List name is required"}), 400

    new_list = ProjectList(
        user_id=user.user_id,
        name=name,
        description=(data.get("description") or "").strip(),
        job_site=(data.get("job_site") or "").strip(),
    )
    db.session.add(new_list)
    db.session.commit()

    # If initial items were provided
    items_data = data.get("items") or []
    for it in items_data:
        zcode = resolve_db_code(it.get("zoro_code") or it.get("product_id"))
        if not zcode:
            continue
        qty = max(int(it.get("quantity") or 1), 1)
        # Fetch product metadata from catalog if missing
        prod = catalog.get_product_detail(zcode)
        if prod:
            item = ProjectListItem(
                list_id=new_list.list_id,
                zoro_code=zcode,
                title=prod.get("title") or it.get("title") or "Industrial Item",
                brand=prod.get("brand") or PRIVATE_LABEL,
                unit_price=prod.get("unit_price") or it.get("unit_price") or 0.0,
                image_url=prod.get("image_url") or it.get("image_url") or "",
                uom=prod.get("uom") or it.get("uom") or "EA",
                quantity=qty,
                notes=(it.get("notes") or "").strip(),
            )
            db.session.add(item)
    if items_data:
        db.session.commit()

    return jsonify({
        "message": "Project list created",
        "list": new_list.to_dict(include_items=True)
    }), 201


@lists_bp.route("/api/lists/<int:list_id>", methods=["GET"])
@login_required
def get_list_detail(list_id):
    """Get single list with full line items and real-time live prices."""
    user = g.current_user
    plist = ProjectList.query.filter_by(list_id=list_id, user_id=user.user_id).first()
    if not plist:
        return jsonify({"error": "Project list not found"}), 404

    # Optionally refresh live prices from catalog
    for item in plist.items:
        try:
            prod = catalog.get_product_detail(item.zoro_code)
            if prod and prod.get("unit_price"):
                item.unit_price = prod.get("unit_price")
                if prod.get("image_url") and not item.image_url:
                    item.image_url = prod.get("image_url")
        except Exception:
            pass

    return jsonify({
        "list": plist.to_dict(include_items=True)
    })


@lists_bp.route("/api/lists/<int:list_id>", methods=["PUT"])
@login_required
def update_list(list_id):
    """Update list name, description, or job site."""
    user = g.current_user
    plist = ProjectList.query.filter_by(list_id=list_id, user_id=user.user_id).first()
    if not plist:
        return jsonify({"error": "Project list not found"}), 404

    data = request.get_json() or {}
    if "name" in data and data["name"].strip():
        plist.name = data["name"].strip()
    if "description" in data:
        plist.description = data["description"].strip()
    if "job_site" in data:
        plist.job_site = data["job_site"].strip()

    plist.updated_at = datetime.now()
    db.session.commit()

    return jsonify({
        "message": "List updated",
        "list": plist.to_dict(include_items=True)
    })


@lists_bp.route("/api/lists/<int:list_id>", methods=["DELETE"])
@login_required
def delete_list(list_id):
    """Delete a saved project list."""
    user = g.current_user
    plist = ProjectList.query.filter_by(list_id=list_id, user_id=user.user_id).first()
    if not plist:
        return jsonify({"error": "Project list not found"}), 404

    db.session.delete(plist)
    db.session.commit()
    return jsonify({"message": "List deleted successfully"})


@lists_bp.route("/api/lists/<int:list_id>/items", methods=["POST"])
@login_required
def add_item_to_list(list_id):
    """Add a product to an existing project list."""
    user = g.current_user
    plist = ProjectList.query.filter_by(list_id=list_id, user_id=user.user_id).first()
    if not plist:
        return jsonify({"error": "Project list not found"}), 404

    data = request.get_json() or {}
    zcode_raw = data.get("zoro_code") or data.get("product_id") or data.get("eq_code")
    zcode = resolve_db_code(zcode_raw)
    if not zcode:
        return jsonify({"error": "Product code is required"}), 400

    quantity = max(int(data.get("quantity") or 1), 1)

    # Check if item is already in list -> increment qty
    existing = ProjectListItem.query.filter_by(list_id=list_id, zoro_code=zcode).first()
    if existing:
        existing.quantity += quantity
        if data.get("notes"):
            existing.notes = data["notes"].strip()
        plist.updated_at = datetime.now()
        db.session.commit()
        return jsonify({
            "message": f"Updated quantity to {existing.quantity}",
            "item": existing.to_dict(),
            "list": plist.to_dict(include_items=False)
        })

    # Fetch live product details
    prod = catalog.get_product_detail(zcode)
    title = (prod.get("title") if prod else None) or data.get("title") or f"Item {format_eq_code(zcode)}"
    brand = (prod.get("brand") if prod else None) or data.get("brand") or PRIVATE_LABEL
    unit_price = (prod.get("unit_price") if prod else None) or data.get("unit_price") or 0.0
    image_url = (prod.get("image_url") if prod else None) or data.get("image_url") or ""
    uom = (prod.get("uom") if prod else None) or data.get("uom") or "EA"

    item = ProjectListItem(
        list_id=plist.list_id,
        zoro_code=zcode,
        title=title,
        brand=brand,
        unit_price=unit_price,
        image_url=image_url,
        uom=uom,
        quantity=quantity,
        notes=(data.get("notes") or "").strip(),
    )
    db.session.add(item)
    plist.updated_at = datetime.now()
    db.session.commit()

    return jsonify({
        "message": "Item added to list",
        "item": item.to_dict(),
        "list": plist.to_dict(include_items=False)
    }), 201


@lists_bp.route("/api/lists/<int:list_id>/items/<int:item_id>", methods=["PUT"])
@login_required
def update_list_item(list_id, item_id):
    """Update item quantity or notes."""
    user = g.current_user
    plist = ProjectList.query.filter_by(list_id=list_id, user_id=user.user_id).first()
    if not plist:
        return jsonify({"error": "Project list not found"}), 404

    item = ProjectListItem.query.filter_by(item_id=item_id, list_id=list_id).first()
    if not item:
        return jsonify({"error": "Item not found in list"}), 404

    data = request.get_json() or {}
    if "quantity" in data:
        q = int(data["quantity"])
        if q <= 0:
            db.session.delete(item)
            db.session.commit()
            return jsonify({"message": "Item removed from list"})
        item.quantity = q
    if "notes" in data:
        item.notes = data["notes"].strip()

    plist.updated_at = datetime.now()
    db.session.commit()

    return jsonify({
        "message": "Item updated",
        "item": item.to_dict(),
        "list": plist.to_dict(include_items=False)
    })


@lists_bp.route("/api/lists/<int:list_id>/items/<int:item_id>", methods=["DELETE"])
@login_required
def remove_list_item(list_id, item_id):
    """Delete an item from a list."""
    user = g.current_user
    plist = ProjectList.query.filter_by(list_id=list_id, user_id=user.user_id).first()
    if not plist:
        return jsonify({"error": "Project list not found"}), 404

    item = ProjectListItem.query.filter_by(item_id=item_id, list_id=list_id).first()
    if not item:
        return jsonify({"error": "Item not found in list"}), 404

    db.session.delete(item)
    plist.updated_at = datetime.now()
    db.session.commit()

    return jsonify({
        "message": "Item removed",
        "list": plist.to_dict(include_items=False)
    })


@lists_bp.route("/api/lists/from-order/<int:order_id>", methods=["POST"])
@login_required
def create_list_from_order(order_id):
    """1-Click convert a past order into a saved project reorder list."""
    user = g.current_user
    order = Order.query.filter_by(order_id=order_id, user_id=user.user_id).first()
    if not order:
        return jsonify({"error": "Order not found"}), 404

    data = request.get_json() or {}
    name = (data.get("name") or "").strip() or f"Reorder: {order.order_number}"

    new_list = ProjectList(
        user_id=user.user_id,
        name=name,
        description=f"Created from Order #{order.order_number}",
        job_site=order.ship_company or "",
    )
    db.session.add(new_list)
    db.session.commit()

    for o_item in order.items:
        zcode = resolve_db_code(o_item.zoro_code)
        item = ProjectListItem(
            list_id=new_list.list_id,
            zoro_code=zcode,
            title=o_item.title or f"Item {format_eq_code(zcode)}",
            brand=o_item.brand or PRIVATE_LABEL,
            unit_price=o_item.unit_price,
            image_url=o_item.image_url or "",
            uom=o_item.uom or "EA",
            quantity=o_item.quantity or 1,
            notes=f"Prev price: ${float(o_item.unit_price or 0):.2f}",
        )
        db.session.add(item)
    db.session.commit()

    return jsonify({
        "message": "Reorder project list created from order",
        "list": new_list.to_dict(include_items=True)
    }), 201


@lists_bp.route("/api/lists/from-cart", methods=["POST"])
@login_required
def create_list_from_cart():
    """1-Click save active cart items as a named project list."""
    user = g.current_user
    data = request.get_json() or {}
    name = (data.get("name") or "").strip() or f"Saved Cart ({datetime.now().strftime('%b %d, %Y')})"
    items_data = data.get("items") or []
    if not items_data:
        return jsonify({"error": "Cart is empty"}), 400

    new_list = ProjectList(
        user_id=user.user_id,
        name=name,
        description=(data.get("description") or "").strip() or "Saved from shopping cart",
        job_site=(data.get("job_site") or "").strip(),
    )
    db.session.add(new_list)
    db.session.commit()

    for c_item in items_data:
        zcode = resolve_db_code(c_item.get("zoro_code") or c_item.get("product_id"))
        if not zcode:
            continue
        item = ProjectListItem(
            list_id=new_list.list_id,
            zoro_code=zcode,
            title=c_item.get("title") or f"Item {format_eq_code(zcode)}",
            brand=c_item.get("brand") or PRIVATE_LABEL,
            unit_price=c_item.get("unit_price") or 0.0,
            image_url=c_item.get("image_url") or "",
            uom=c_item.get("uom") or "EA",
            quantity=max(int(c_item.get("quantity") or 1), 1),
        )
        db.session.add(item)
    db.session.commit()

    return jsonify({
        "message": "Cart saved as project list",
        "list": new_list.to_dict(include_items=True)
    }), 201
