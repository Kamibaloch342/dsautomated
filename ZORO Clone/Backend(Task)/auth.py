"""
Auth Blueprint — JWT-based authentication
──────────────────────────────────────────
Endpoints:
  POST /api/auth/register
  POST /api/auth/login
  GET  /api/auth/me          (protected)
  PUT  /api/auth/me          (protected — update profile)
"""

import os
import datetime
import functools

import jwt
from flask import Blueprint, request, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash
from database import db
from models import User

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
JWT_EXPIRY_HOURS = 72


# ── Helpers ─────────────────────────────────────

def _create_token(user_id):
    """Create a signed JWT for the given user."""
    payload = {
        "user_id": user_id,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=JWT_EXPIRY_HOURS),
        "iat": datetime.datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _decode_token(token):
    """Decode and verify a JWT. Returns payload dict or None."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def login_required(fn):
    """Decorator that enforces JWT auth on a route."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Authorization header"}), 401

        token = auth_header.split(" ", 1)[1]
        payload = _decode_token(token)
        if payload is None:
            return jsonify({"error": "Invalid or expired token"}), 401

        user = User.query.get(payload["user_id"])
        if user is None:
            return jsonify({"error": "User not found"}), 401

        g.current_user = user
        return fn(*args, **kwargs)

    return wrapper


# ── Routes ──────────────────────────────────────

@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}

    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()

    # Validation
    errors = []
    if not email or "@" not in email:
        errors.append("Valid email is required.")
    if len(password) < 6:
        errors.append("Password must be at least 6 characters.")
    if not first_name:
        errors.append("First name is required.")
    if not last_name:
        errors.append("Last name is required.")

    if errors:
        return jsonify({"error": errors}), 400

    # Duplicate check
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "An account with this email already exists."}), 409

    user = User(
        email=email,
        password_hash=generate_password_hash(password),
        first_name=first_name,
        last_name=last_name,
        phone=data.get("phone", ""),
        address_line1=data.get("address_line1", ""),
        address_line2=data.get("address_line2", ""),
        city=data.get("city", ""),
        state=data.get("state", ""),
        zip_code=data.get("zip_code", ""),
    )
    db.session.add(user)
    db.session.commit()

    token = _create_token(user.user_id)
    return jsonify({"token": token, "user": user.to_dict()}), 201


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}

    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400

    user = User.query.filter_by(email=email).first()
    if user is None or not check_password_hash(user.password_hash, password):
        return jsonify({"error": "Invalid email or password."}), 401

    token = _create_token(user.user_id)
    return jsonify({"token": token, "user": user.to_dict()})


@auth_bp.route("/me", methods=["GET"])
@login_required
def get_me():
    return jsonify({"user": g.current_user.to_dict()})


@auth_bp.route("/me", methods=["PUT"])
@login_required
def update_me():
    data = request.get_json(silent=True) or {}
    user = g.current_user

    for field in ("first_name", "last_name", "phone",
                  "address_line1", "address_line2", "city", "state", "zip_code"):
        if field in data:
            setattr(user, field, data[field])

    db.session.commit()
    return jsonify({"user": user.to_dict()})
