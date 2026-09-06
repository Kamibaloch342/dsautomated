"""Store brand identity — the one place the backend knows the store's name.

Rename the store by editing this file (and frontend/src/lib/brand.js).
"""

STORE_NAME = "EKUIPT"
STORE_TAGLINE = "Everything for business, industry & trade"
CODE_LABEL = "EQ Code"
PRIVATE_LABEL = "EKUIPT Select"
SUPPORT_EMAIL = "support@ekuipt.com"
ADMIN_DEFAULT_EMAIL = "admin@ekuipt.com"
PRIMARY_COLOR = "#19454B"
ACCENT_COLOR = "#964C14"


def format_eq_code(code):
    if not code:
        return ""
    s = str(code).strip()
    if s.startswith("G") or s.startswith("g"):
        return "E" + s[1:]
    if s.startswith("E") or s.startswith("e"):
        return "E" + s[1:]
    if s.isdigit():
        return "E" + s
    return ("E" + s) if not s.startswith("E") else s.upper()


def resolve_db_code(code):
    """Maps E..., G..., or pure digits back to G... for SQLite database lookups."""
    if not code:
        return ""
    s = str(code).strip()
    if s.startswith("E") or s.startswith("e"):
        return "G" + s[1:]
    if s.startswith("G") or s.startswith("g"):
        return "G" + s[1:]
    if s.isdigit():
        return "G" + s
    return s


def format_brand(brand):
    if not brand:
        return ""
    b = str(brand).strip()
    if b.lower() in ("zoro select", "zoro", "zoro_select", "zoroselect"):
        return PRIVATE_LABEL
    return b

