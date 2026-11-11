"""Zoro feed extraction and parsing: dimensions, wall type, ECT, pack size, strength specs."""

import re
import sqlite3
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from urllib.parse import quote

QUERY = """
SELECT
    zoro_no, manufacturer_part_number, upc_code, zoro_brand_name,
    short_description, long_description, sales_price, unit_price, min_order_qty, uom,
    lead_time, availabilityStatus, ship_pack_length, ship_pack_width, ship_pack_height,
    ship_pack_weight, zoro_image_name{extra}
FROM zoro_data
WHERE
    (zoro_brand_name IS NULL OR zoro_brand_name = '' OR zoro_brand_name = 'Zoro Select')
    AND (short_description LIKE '%Shipping Box%'
         OR short_description LIKE '%Corrugated Box%'
         OR short_description LIKE '%Multi-Depth Box%')
    AND short_description NOT LIKE '%Lid%'
    AND short_description NOT LIKE '%Pad%'
    AND short_description NOT LIKE '%Roll%'
    AND short_description NOT LIKE '%Tape%'
    AND lead_time LIKE '%1 day%'
    AND (availabilityStatus = 'In Stock' OR availabilityStatus IS NULL)
    AND sales_price > 0
    AND min_order_qty > 0
"""

# Box Maker's Certificate limits: ECT -> (burst test lb, max gross weight lb, max L+W+H in)
STRENGTH = {
    "Single Wall": {23: (125, 20, 40), 26: (150, 35, 50), 29: (175, 50, 60), 32: (200, 65, 75),
                    40: (250, 80, 80), 44: (275, 95, 105), 55: (350, 120, 105)},
    "Double Wall": {42: (200, 80, 85), 48: (275, 100, 110), 51: (350, 120, 110),
                    61: (400, 140, 110), 71: (500, 160, 110)},
}

NUM = r"(\d+(?:\.\d+)?(?:[ -]+\d+/\d+)?|\d+/\d+)"
UNIT = r"\s*(?:in\.?|inches|inch|\"|”|'')?\s*(?:[LWHD]\b\.?)?\s*"
DIMS_RE = re.compile(NUM + UNIT + r"[x×]\s*" + NUM + UNIT + r"[x×]\s*" + NUM, re.I)
LABELLED_RE = {
    "L": re.compile(r"(?:inside\s+)?length[^0-9]{0,12}" + NUM, re.I),
    "W": re.compile(r"(?:inside\s+)?width[^0-9]{0,12}" + NUM, re.I),
    "H": re.compile(r"(?:inside\s+)?(?:height|depth)[^0-9]{0,12}" + NUM, re.I),
}
ECT_RE = [re.compile(r"\b(\d{2})\s*(?:lb\.?\s*)?ECT\b", re.I),
          re.compile(r"\b(?:ECT|edge crush(?: test)?)\b[^0-9]{0,24}(\d{2})\b", re.I)]
BURST_RE = [re.compile(r"\b(\d{3})\s*(?:#|lbs?\.?\s*(?:test|burst|mullen))", re.I),
            re.compile(r"\bburst(?:ing)?(?:\s+strength|\s+test)?[^0-9]{0,15}(\d{3})\b", re.I)]
PACK_RE = re.compile(r"(\d+)\s*(?:/\s*)?(?:pk|pack|bundle)\b", re.I)


def to_float(text):
    text = text.strip()
    m = re.match(r"^(\d+)[ -]+(\d+)/(\d+)$", text)
    if m:
        return int(m[1]) + int(m[2]) / int(m[3])
    if "/" in text:
        n, d = text.split("/")
        return int(n) / int(d)
    return float(text)


def fmt_dim(v):
    """12 -> '12', 12.5 -> '12-1/2', 8.75 -> '8-3/4', 10.3 -> '10.3'."""
    v = float(v)
    whole = int(v)
    frac = Fraction(v - whole).limit_denominator(16)
    if frac == 0:
        return str(whole)
    if abs(float(frac) - (v - whole)) < 1e-6:
        return f"{whole}-{frac.numerator}/{frac.denominator}" if whole else f"{frac}"
    return f"{v:g}"


def parse_dims(*texts):
    for text in texts:
        if not text:
            continue
        m = DIMS_RE.search(text)
        if m:
            return tuple(to_float(g) for g in m.groups())
    for text in texts:
        if not text:
            continue
        found = {k: rx.search(text) for k, rx in LABELLED_RE.items()}
        if all(found.values()):
            return tuple(to_float(found[k][1]) for k in "LWH")
    return None


def parse_wall(text):
    t = (text or "").lower()
    if "triple wall" in t or "triple-wall" in t:
        return "Triple Wall"
    if "double wall" in t or "double-wall" in t:
        return "Double Wall"
    if "single wall" in t or "single-wall" in t:
        return "Single Wall"
    return None


def parse_ect(text):
    for rx in ECT_RE:
        m = rx.search(text or "")
        if m:
            return int(m[1])
    return None


def parse_burst(text):
    for rx in BURST_RE:
        m = rx.search(text or "")
        if m:
            return int(m[1])
    return None


def box_type(L, W, H, wall, multi_depth=False):
    if multi_depth:
        kind = "MULTI-DEPTH SHIPPING BOX"
    elif max(L, W, H) / min(L, W, H) <= 1.15:
        kind = "CUBE SHIPPING BOX"
    elif H <= 0.35 * min(L, W):
        kind = "FLAT SHIPPING BOX"
    elif L >= 2.5 * max(W, H):
        kind = "LONG SHIPPING BOX"
    elif H >= 1.5 * max(L, W):
        kind = "TALL SHIPPING BOX"
    else:
        kind = "SHIPPING BOX"
    if wall == "Double Wall":
        kind = "HEAVY-DUTY " + kind.replace(" SHIPPING", "")
    return kind


@dataclass
class Listing:
    zoro_no: str
    short_description: str
    sales_price: float
    min_order_qty: int
    long_description: str = ""
    manufacturer_part_number: str = ""
    upc_code: str = ""
    unit_price: float = None
    uom: str = ""
    lead_time: str = ""
    availabilityStatus: str = ""
    ship_pack_length: float = None
    ship_pack_width: float = None
    ship_pack_height: float = None
    ship_pack_weight: float = None
    zoro_image_name: str = ""
    country_of_origin: str = None
    image_path: str = None              # optional local supplier photo (tests / manual)
    # parsed
    L: float = None
    W: float = None
    H: float = None
    wall: str = None
    ect: int = None
    burst: int = None
    max_lbs: int = None
    size_limit: int = None
    color: str = "Kraft"
    multi_depth: bool = False
    score_depths: list = field(default_factory=list)   # lower heights a multi-depth box cuts to
    boxes_per_unit: int = 1                             # "10 PK" rows: one Zoro unit = 10 boxes
    problems: list = field(default_factory=list)

    @property
    def order_qty(self):
        """Zoro units bought per Hathoro order (the minimum order quantity)."""
        return int(self.min_order_qty)

    @property
    def pack(self):
        """Boxes the customer gets."""
        return self.order_qty * self.boxes_per_unit

    @property
    def unit_cost(self):
        """Zoro's item price without its freight surcharge (sales_price = unit_price + surcharge)."""
        up = float(self.unit_price or 0)
        return up if 0 < up <= self.sales_price else self.sales_price

    @property
    def surcharge(self):
        """Per-unit freight surcharge: no partner discount applies to it."""
        return round(self.sales_price - self.unit_cost, 2)

    @property
    def made_in_usa(self):
        c = (self.country_of_origin or "").strip().lower()
        return c in ("usa", "us", "united states", "united states of america", "u.s.a.", "u.s.")

    @property
    def has_rating(self):
        return bool((self.ect or self.burst) and self.max_lbs)

    def rating_fields(self):
        """Strength wording. ECT and burst (Mullen) are different tests: a box is only ever
        described with the test it was actually rated by."""
        if self.ect:
            return {"rating": f"{self.ect} ECT", "rating_title": f"{self.ect} ECT",
                    "rating_claim": f"{self.ect} ECT Edge Crush Tested",
                    "rating_label": "Edge Crush Test Rated", "rating_file": f"{self.ect}ECT",
                    "seal_top": "EDGE CRUSH TEST", "seal_main": str(self.ect),
                    "seal_sub": "ECT RATED"}
        if self.burst:
            return {"rating": f"{self.burst}# Test", "rating_title": f"{self.burst} LB TEST",
                    "rating_claim": f"{self.burst} lb Burst Strength Tested",
                    "rating_label": "Mullen Burst Tested", "rating_file": f"{self.burst}lb",
                    "seal_top": "BURST STRENGTH TEST", "seal_main": str(self.burst),
                    "seal_sub": "LB BURST TEST"}
        return dict.fromkeys(("rating", "rating_title", "rating_claim", "rating_label",
                              "rating_file", "seal_top", "seal_main", "seal_sub"), "")

    def fields(self):
        """Placeholder values for content.json / catalog_settings.json templates."""
        r = self.rating_fields()
        ect_text = r["rating"]
        spec = " · ".join(x for x in (f"{self.wall} Corrugated" if self.wall else "Corrugated",
                                      ect_text) if x)
        return {**r,
            "L": fmt_dim(self.L), "W": fmt_dim(self.W), "H": fmt_dim(self.H),
            "LxWxH": f"{self.L:g}x{self.W:g}x{self.H:g}",       # file/handle safe: 12.5x9.5x4
            "pack": self.pack,
            "wall": self.wall or "Corrugated",
            "WALL": (self.wall or "Corrugated").upper(),
            "wall_slug": (self.wall or "Corrugated").replace(" ", ""),
            "ect": self.ect or "",
            "ect_text": ect_text,
            "burst": self.burst or "",
            "max_lbs": self.max_lbs or "",
            "size_limit": self.size_limit or "",
            "box_type": box_type(self.L, self.W, self.H, self.wall, self.multi_depth),
            "spec_line": spec,
            "sku": self.zoro_no,
            "color": self.color,
            "style_file": "_MultiDepth" if self.multi_depth else "",
        }

    def to_dict(self):
        return asdict(self)


def parse_listing(row):
    """Build a Listing from a DB row (dict) and fill in parsed specs + problems."""
    known = Listing.__dataclass_fields__
    li = Listing(**{k: v for k, v in row.items() if k in known})
    li.min_order_qty = int(li.min_order_qty or 0)
    li.sales_price = float(li.sales_price or 0)
    text = f"{li.short_description or ''} | {li.long_description or ''}"

    dims = parse_dims(li.short_description, li.long_description)
    if dims:
        L, W, H = dims
        if W > L:                       # convention: length >= width
            L, W = W, L
        li.L, li.W, li.H = L, W, H
    else:
        li.problems.append("dimensions not found in description")

    short = li.short_description or ""
    li.color = "White" if re.search(r"\bwhite\b", short, re.I) else "Kraft"
    li.multi_depth = bool(re.search(r"multi-?\s?depth", short, re.I))
    if li.multi_depth and dims:
        # "26 in x 20 in x 6 in, 8 in, 10 in, 12 in Multi-Depth Box": a 12 in tall box
        # scored to cut down to 10, 8 and 6 in
        head = re.split(r"multi-?\s?depth", short, flags=re.I)[0]
        m = DIMS_RE.search(head)
        extra = [to_float(n) for n in re.findall(r",\s*" + NUM + r"\s*(?:in\b|\")",
                                                 head[m.end():])] if m else []
        if extra:
            depths = {li.H, *extra}
            li.H = max(depths)
            li.score_depths = sorted((d for d in depths if d != li.H), reverse=True)

    if (li.uom or "").upper() == "PK":
        m = PACK_RE.search(short)
        if m:
            li.boxes_per_unit = int(m[1])
        else:
            li.problems.append("missing pack quantity: sold per PK, box count not in description")

    li.wall = parse_wall(text)
    li.ect = parse_ect(text)
    li.burst = parse_burst(text)
    if li.wall is None:
        li.wall = next((w for w, tbl in STRENGTH.items() if li.ect in tbl), None)
    if li.wall is None:
        li.wall = "Single Wall"
        li.problems.append("wall type not found: assumed Single Wall")
    table = STRENGTH.get(li.wall, {})
    # box maker's limits come from whichever test the box was rated by (ECT or burst)
    if li.ect in table:
        _, li.max_lbs, li.size_limit = table[li.ect]
    elif li.burst:
        limits = next(((m, s) for b, m, s in table.values() if b == li.burst), None)
        if limits:
            li.max_lbs, li.size_limit = limits
    if not li.has_rating:
        li.problems.append("ECT / burst rating not found: strength image uses generic text")

    if li.min_order_qty <= 0:
        li.problems.append("missing pack quantity")
    if li.sales_price <= 0:
        li.problems.append("missing price")
    return li


def fetch_rows(db_path, sku=None, limit=None):
    """Read qualifying rows from the Zoro SQLite feed in read-only mode."""
    uri = f"file:{quote(str(db_path))}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    cols = [r[1] for r in con.execute("PRAGMA table_info(zoro_data)")]
    origin = next((c for c in cols if "country" in c.lower() or "origin" in c.lower()), None)
    extra = f", {origin} AS country_of_origin" if origin else ""
    sql = QUERY.format(extra=extra)
    params = []
    if sku:
        sql += " AND zoro_no = ?"
        params.append(sku)
    sql += " ORDER BY zoro_no"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = [dict(r) for r in con.execute(sql, params)]
    con.close()
    return rows, origin
