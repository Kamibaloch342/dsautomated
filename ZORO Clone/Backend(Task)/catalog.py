"""Live catalog served directly from catalog_enrichment.db / combined_reports.db (read-only).

Handles:
  * ENRICH  — catalog_enrichment.db (the enriched Zoro Select catalog, ~110k products)
              with full 3-5 level category trees, exact specifications, and media.
  * SOURCE  — combined_reports.db, the master 2M catalog fallback.
  * INDEX   — catalog_index.db, derived by build_search_index.py:
              FTS5 search, card mirror with display-rank indexes,
              category/brand stats, spec-attribute lexicon.
"""

import os
import re
import json
import hashlib
import logging
import sqlite3
import subprocess
import sys
import threading

from flask import Blueprint, jsonify, request, send_file, abort

import specs as specs_mod
from brand import format_eq_code, format_brand, resolve_db_code

log = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENRICH_DB = os.path.join(BASE_DIR, "catalog_enrichment.db")
# Priority order for source catalog:
# 1. CATALOG_DB env variable
# 2. Local catalog_enrichment.db in Backend(Task)
# 3. Relative TSV Files/combined_reports.db fallback if present
def _resolve_source_db():
    if os.environ.get("CATALOG_DB") and os.path.exists(os.environ.get("CATALOG_DB")):
        return os.environ.get("CATALOG_DB")
    if os.path.exists(ENRICH_DB):
        return ENRICH_DB
    relative_combined = os.path.abspath(os.path.join(BASE_DIR, "..", "..", "Automations", "TSV Files", "combined_reports.db"))
    if os.path.exists(relative_combined):
        return relative_combined
    return ENRICH_DB

SOURCE_DB = _resolve_source_db()
INDEX_DB = os.path.join(BASE_DIR, "catalog_index.db")
STORE_DB = os.path.join(BASE_DIR, "zoro_clone.db")
IMG_CACHE = os.path.join(BASE_DIR, "img_cache")

COUNT_CAP = 100000          # stop counting result sets beyond this
FACET_SAMPLE = 40000        # facet counts computed over at most this many rows

catalog_bp = Blueprint("catalog", __name__)

_local = threading.local()
_lexicon_loaded = False
_build_proc = None


# ─────────────────────────────────────────────
#  Connections
# ─────────────────────────────────────────────

def _enrich():
    con = getattr(_local, "enrich", None)
    if con is None:
        if os.path.exists(ENRICH_DB):
            con = sqlite3.connect(f"file:{ENRICH_DB}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            _local.enrich = con
    return con


def _source():
    con = getattr(_local, "source", None)
    if con is None:
        if os.path.exists(SOURCE_DB):
            con = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            _local.source = con
    return con


def _index():
    con = getattr(_local, "index", None)
    if con is None:
        con = sqlite3.connect(f"file:{INDEX_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        _local.index = con
    return con


def _store():
    con = getattr(_local, "store", None)
    if con is None:
        con = sqlite3.connect(f"file:{STORE_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        _local.store = con
    return con


def ready():
    if not os.path.exists(INDEX_DB):
        return False
    try:
        _index().execute("SELECT v FROM meta WHERE k='product_count'").fetchone()
        return True
    except sqlite3.Error:
        return False


def ensure_index(auto_build=True):
    """Kick off an index build in a subprocess when the sidecar is missing."""
    global _build_proc
    if ready() or not auto_build:
        return
    if _build_proc is None or _build_proc.poll() is not None:
        log.warning("catalog_index.db missing — starting background build")
        _build_proc = subprocess.Popen(
            [sys.executable, os.path.join(BASE_DIR, "build_search_index.py")],
            cwd=BASE_DIR,
        )


def _ensure_lexicon():
    global _lexicon_loaded
    if _lexicon_loaded or not ready():
        return
    names = [r["name"] for r in _index().execute("SELECT name FROM lexicon")]
    specs_mod.load_lexicon(names)
    _lexicon_loaded = True
    log.info("spec lexicon loaded: %s attribute names", len(names))


def _building_response():
    return jsonify({
        "building": True,
        "error": "Catalog index is building — try again in a couple of minutes.",
    }), 503


# ─────────────────────────────────────────────
#  Overrides (admin edits, stored in zoro_clone.db)
# ─────────────────────────────────────────────

OVERRIDE_FIELDS = ("unit_price", "availability", "min_order_qty", "lead_time")


def get_overrides(zoro_nos):
    if not zoro_nos:
        return {}
    try:
        marks = ",".join("?" * len(zoro_nos))
        rows = _store().execute(
            f"SELECT * FROM product_overrides WHERE zoro_code IN ({marks})",
            list(zoro_nos),
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {r["zoro_code"]: dict(r) for r in rows}


def _apply_override(item, ov):
    if not ov:
        return item
    if ov.get("unit_price") is not None:
        base = item.get("unit_price")
        item["unit_price"] = float(ov["unit_price"])
        if base and float(ov["unit_price"]) < float(base):
            item["original_price"] = float(base)
    if ov.get("availability"):
        item["availability"] = ov["availability"]
        item["in_stock"] = ov["availability"].lower() not in (
            "out of stock", "discontinued", "backordered")
    if ov.get("min_order_qty") is not None:
        item["min_order_qty"] = int(ov["min_order_qty"])
    if ov.get("lead_time"):
        item["lead_time"] = ov["lead_time"]
    item["overridden"] = True
    return item


# ─────────────────────────────────────────────
#  Serializers
# ─────────────────────────────────────────────

def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def _availability(avail):
    return avail if avail else "In Stock"


def card_dict(r):
    """Card fields from an index `cards` row (sqlite3.Row)."""
    zno = r["zoro_no"]
    return {
        "product_id": zno,
        "zoro_code": zno,
        "eq_code": format_eq_code(zno),
        "title": r["title"],
        "brand": format_brand(r["brand"] or "Zoro Select"),
        "unit_price": r["price"],
        "availability": _availability(r["avail"]),
        "in_stock": bool(r["in_stock"]),
        "image_url": r["image"] or None,
        "mfr_number": r["mpn"] or None,
        "lead_time": r["lead_time"] or "Ships in 1 day",
        "lead_days": r["lead_days"] or 1,
        "uom": r["uom"] or "EA",
        "min_order_qty": r["min_qty"] or 1,
        "is_dropship": bool(r["is_dropship"]),
        "category_name": (r["l3"] if "l3" in r.keys() and r["l3"] else None) or r["l2"] or r["l1"] or None,
        "l1": r["l1"] or None,
        "l2": r["l2"] or None,
        "l3": (r["l3"] if "l3" in r.keys() and r["l3"] else None) or None,
        "l1_slug": slugify(r["l1"]),
        "l2_slug": slugify(r["l2"]),
        "l3_slug": slugify(r["l3"]) if "l3" in r.keys() and r["l3"] else None,
        "price_tiers": [],
        "variant_group": None,
        "variant_label": None,
    }


def cards_with_overrides(rows):
    items = [card_dict(r) for r in rows]
    ovs = get_overrides([i["zoro_code"] for i in items])
    for i in items:
        _apply_override(i, ovs.get(i["zoro_code"]))
    return items


def _parse_states(raw):
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    return [s.strip() for s in str(raw).split(",") if s.strip()]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def detail_dict(row):
    """Full product detail from enriched_products or fallback zoro_data."""
    zno = row["zoro_no"]

    # 1. Enriched record handling
    if "specifications" in row.keys() and "categories" in row.keys():
        specs = {}
        if row.get("specifications"):
            try:
                specs = json.loads(row["specifications"])
            except Exception:
                pass

        cat_list = []
        if row.get("categories"):
            try:
                cat_list = json.loads(row["categories"])
            except Exception:
                pass

        category_path = []
        for idx, c in enumerate(cat_list, 1):
            category_path.append({
                "category_name": c.get("name", ""),
                "slug": c.get("slug") or slugify(c.get("name")),
                "id": c.get("id"),
                "level": idx,
            })

        media = []
        if row.get("media"):
            try:
                media = json.loads(row["media"])
            except Exception:
                pass

        images = []
        documents = []
        for m in media:
            if isinstance(m, dict):
                url = m.get("url") or (f"https://www.zoro.com/static/cms/product/full/{m.get('name')}" if m.get("name") else "")
                if m.get("is_pdf") or (m.get("name") and m.get("name").lower().endswith(".pdf")):
                    documents.append({"name": m.get("name"), "url": url})
                elif url:
                    images.append({"image_url": url})
            elif isinstance(m, str):
                if m.lower().endswith(".pdf"):
                    documents.append({"name": m, "url": f"https://www.zoro.com/static/cms/product/full/{m}"})
                else:
                    images.append({"image_url": f"https://www.zoro.com/static/cms/product/full/{m}"})

        primary_img = images[0]["image_url"] if images else None

        variant_group = row.get("variant_group")
        variant_options = []
        if row.get("variant_attributes"):
            try:
                variant_options = json.loads(row["variant_attributes"])
            except Exception:
                pass

        variant_members = []
        if row.get("variant_members"):
            try:
                variant_members = json.loads(row["variant_members"])
            except Exception:
                pass

        l1, l2, l3 = row.get("l1") or "", row.get("l2") or "", row.get("l3") or ""
        lead_days_val = row.get("lead_time_days") or 1
        lead_time_str = f"Ships in {lead_days_val} day{'s' if lead_days_val != 1 else ''}"

        return {
            "product_id": zno,
            "zoro_code": zno,
            "eq_code": format_eq_code(zno),
            "title": row.get("title") or "",
            "brand": format_brand((row.get("brand") or "").strip() or "Zoro Select"),
            "unit_price": _num(row.get("price")),
            "original_price": _num(row.get("original_price")),
            "availability": "In Stock",
            "in_stock": True,
            "category_name": l3 or l2 or l1 or None,
            "category_path": category_path,
            "l1": l1 or None,
            "l2": l2 or None,
            "l3": l3 or None,
            "l1_slug": slugify(l1),
            "l2_slug": slugify(l2),
            "l3_slug": slugify(l3),
            "mfr_number": row.get("mfr_no") or None,
            "upc": row.get("upc") or None,
            "country_of_origin": specs.get("Country of Origin (subject to change)") or specs.get("Country of Origin"),
            "image_url": primary_img,
            "images": images,
            "documents": documents,
            "product_url": f"https://www.zoro.com/i/{zno}/",
            "description": row.get("description") or f"<p>{specs.get('Item', '')}</p>",
            "product_details": [],
            "specifications": specs,
            "lead_time": lead_time_str,
            "is_dropship": bool(row.get("is_dropship")),
            "uom": row.get("price_unit") or "EA",
            "freight_surcharge": 0.0,
            "min_order_qty": row.get("min_qty") or 1,
            "package_qty": row.get("package_qty") or 1,
            "is_ltl": bool(row.get("is_ltl")),
            "is_blind_ship": True,
            "is_hazmat": bool(row.get("is_hazmat")),
            "restricted_states": _parse_states(row.get("restricted_states")),
            "prop65": None,
            "variant_group": variant_group,
            "variant_options": variant_options,
            "variant_members": variant_members,
            "created_at": row.get("scraped_at"),
        }

    # 2. Fallback for raw zoro_data rows
    _ensure_lexicon()
    extracted = specs_mod.extract(row)
    img = row.get("zoro_image_name") or None
    l1, l2 = row.get("level_one_name") or "", row.get("level_two_name") or ""
    path = []
    if l1:
        path.append({"category_name": l1, "slug": slugify(l1), "level": 1})
    if l2:
        path.append({"category_name": l2, "slug": slugify(l2), "level": 2})
    avail = _availability((row.get("availabilityStatus") or "").strip())
    prop65 = None
    if row.get("cali_prop65_message"):
        prop65 = {
            "message": row["cali_prop65_message"],
            "cancer": row.get("cali_prop65_cancercausechem"),
            "reproductive": row.get("cali_prop65_reproharmcausechem"),
        }
    return {
        "product_id": zno,
        "zoro_code": zno,
        "title": row.get("short_description") or "",
        "brand": format_brand(row.get("zoro_brand_name") or "Zoro Select"),
        "unit_price": _num(row.get("sales_price")),
        "original_price": None,
        "availability": avail,
        "in_stock": avail.lower() not in ("out of stock", "discontinued", "backordered"),
        "category_name": l2 or l1 or None,
        "category_path": path,
        "l1": l1 or None,
        "l2": l2 or None,
        "l3": None,
        "l1_slug": slugify(l1),
        "l2_slug": slugify(l2),
        "l3_slug": None,
        "mfr_number": row.get("manufacturer_part_number") or None,
        "upc": row.get("upc_code") or None,
        "country_of_origin": row.get("country_of_origin") or None,
        "image_url": img,
        "images": [{"image_url": img}] if img else [],
        "documents": [],
        "product_url": f"https://www.zoro.com/i/{zno}/",
        "description": extracted["details_html"],
        "product_details": extracted["bullets"],
        "specifications": extracted["specifications"],
        "lead_time": row.get("lead_time") or "Ships in 1 day",
        "is_dropship": row.get("is_dropship") == "YES",
        "uom": row.get("uom") or "EA",
        "freight_surcharge": _num(row.get("freight_surcharge")) or 0.0,
        "min_order_qty": 1,
        "package_qty": 1,
        "is_ltl": row.get("is_ltl") == "True",
        "is_blind_ship": row.get("is_blind_ship") == "True",
        "is_hazmat": False,
        "restricted_states": _parse_states(row.get("restricted_states")),
        "prop65": prop65,
        "variant_group": None,
        "variant_options": [],
        "variant_members": [],
        "created_at": row.get("created_dt"),
    }


# ─────────────────────────────────────────────
#  Lookups used by orders / bulk / admin
# ─────────────────────────────────────────────

def get_source_row(zoro_no):
    if not zoro_no:
        return None
    db_no = resolve_db_code(zoro_no)
    econ = _enrich()
    if econ:
        r = econ.execute(
            "SELECT * FROM enriched_products WHERE zoro_no = ? AND status = 'OK'",
            (db_no.upper(),),
        ).fetchone()
        if r is None and db_no != zoro_no:
            r = econ.execute(
                "SELECT * FROM enriched_products WHERE zoro_no = ? AND status = 'OK'",
                (zoro_no.upper(),),
            ).fetchone()
        if r:
            return dict(r)

    # 2. Check source DB fallback
    scon = _source()
    if scon:
        r = scon.execute(
            "SELECT * FROM zoro_data WHERE zoro_no = ?", (db_no.upper(),)
        ).fetchone()
        if r is None and db_no != zoro_no:
            r = scon.execute(
                "SELECT * FROM zoro_data WHERE zoro_no = ?", (zoro_no.upper(),)
            ).fetchone()
        if r:
            return dict(r)

    return None


def lookup(sku):
    """Order-time product lookup: card-level dict + overrides, or None."""
    if not sku:
        return None
    db_sku = resolve_db_code(sku)
    con = _index() if ready() else None
    if con:
        row = con.execute("SELECT * FROM cards WHERE zoro_no = ?", (db_sku.upper(),)).fetchone()
        if row is None and db_sku != sku:
            row = con.execute("SELECT * FROM cards WHERE zoro_no = ?", (sku.upper(),)).fetchone()
        if row is None:
            row = con.execute("SELECT * FROM cards WHERE zoro_no = ? COLLATE NOCASE LIMIT 1", (db_sku,)).fetchone()
        if row is None:
            row = con.execute("SELECT * FROM cards WHERE mpn = ? COLLATE NOCASE LIMIT 1", (sku,)).fetchone()
        if row:
            item = card_dict(row)
            ovs = get_overrides([item["zoro_code"]])
            return _apply_override(item, ovs.get(item["zoro_code"]))

    src = get_source_row(sku)
    if not src:
        return None
    item = detail_dict(src)
    ovs = get_overrides([item["zoro_code"]])
    return _apply_override(item, ovs.get(item["zoro_code"]))


def product_count():
    if not ready():
        return 0
    r = _index().execute("SELECT v FROM meta WHERE k='product_count'").fetchone()
    return int(r["v"]) if r else 0


# ─────────────────────────────────────────────
#  Search / browse engine
# ─────────────────────────────────────────────

def _fts_query(q):
    tokens = re.findall(r"[A-Za-z0-9']+", q)[:8]
    extra_tokens = []
    for t in tokens:
        if (t.startswith("E") or t.startswith("e")) and len(t) > 2 and t[1:].isdigit():
            extra_tokens.append(f"G{t[1:]}")
        elif (t.startswith("G") or t.startswith("g")) and len(t) > 2 and t[1:].isdigit():
            extra_tokens.append(f"E{t[1:]}")
        elif t.isdigit() and len(t) >= 4:
            extra_tokens.append(f"G{t}")
            extra_tokens.append(f"E{t}")
    all_tokens = tokens + extra_tokens
    return " ".join(f'"{t}"*' for t in all_tokens if t)


SORTS = {
    "price_asc":  "c.price ASC",
    "price_desc": "c.price DESC",
    "name_asc":   "c.title ASC",
    "name_desc":  "c.title DESC",
    "newest":     "c.created DESC",
}


def _resolve_cat(slug, level):
    """slug → exact category name via cat_stats."""
    if not slug:
        return None
    col = "slug" if level == 1 else ("l2_slug" if level == 2 else "l3_slug")
    src = "l1" if level == 1 else ("l2" if level == 2 else "l3")
    r = _index().execute(
        f"SELECT {src} AS name FROM cat_stats WHERE {col} = ? LIMIT 1", (slug,)
    ).fetchone()
    return r["name"] if r else None


def search(params):
    """Unified search/browse. Returns (payload dict, status)."""
    q = (params.get("q") or "").strip()
    l1_slug = (params.get("l1") or "").strip()
    l2_slug = (params.get("l2") or "").strip()
    l3_slug = (params.get("l3") or "").strip()
    raw_brands = [b for b in params.getlist("brand") if b.strip()]
    brands = []
    for b in raw_brands:
        bs = b.strip()
        if bs.lower() in ("ekuipt select", "zoro select", "ekuipt-select", "zoro-select", "ekuipt"):
            brands.extend(["EKUIPT Select", "Zoro Select"])
        else:
            brands.append(bs)
    brands = list(set(brands))
    in_stock = (params.get("in_stock") or "").lower() == "true"
    min_price = params.get("min_price", type=float)
    max_price = params.get("max_price", type=float)
    sort = (params.get("sort") or "").strip()
    page = max(params.get("page", 1, type=int) or 1, 1)
    per_page = min(max(params.get("per_page", 24, type=int) or 24, 1), 96)

    l1 = _resolve_cat(l1_slug, 1) or (l1_slug or None)
    l2 = _resolve_cat(l2_slug, 2) or (l2_slug or None)
    l3 = _resolve_cat(l3_slug, 3) or (l3_slug or None)

    where, args = [], []
    # Filter out products without images across all catalog listings
    where.append("c.image IS NOT NULL AND c.image != ''")

    if l1:
        where.append("c.l1 = ?"); args.append(l1)
    if l2:
        where.append("c.l2 = ?"); args.append(l2)
    if l3:
        where.append("c.l3 = ?"); args.append(l3)
    if brands:
        where.append("c.brand IN (%s)" % ",".join("?" * len(brands)))
        args.extend(brands)
    if in_stock:
        where.append("c.in_stock = 1")
    if min_price is not None:
        where.append("c.price >= ?"); args.append(min_price)
    if max_price is not None:
        where.append("c.price <= ?"); args.append(max_price)

    if q:
        match = _fts_query(q)
        base = ("FROM fts f JOIN cards c ON c.zoro_no = f.zoro_no "
                "WHERE fts MATCH ?")
        base_args = [match]
        if where:
            base += " AND " + " AND ".join(where)
            base_args += args
        order = SORTS.get(sort, "f.rank, c.rank DESC")
    else:
        base = "FROM cards c"
        if where:
            base += " WHERE " + " AND ".join(where)
        base_args = list(args)
        order = SORTS.get(sort, "c.rank DESC")
        if sort in ("price_asc", "price_desc"):
            order += ", c.rank DESC"

    con = _index()
    offset = (page - 1) * per_page
    rows = con.execute(
        f"SELECT c.* {base} ORDER BY {order} LIMIT ? OFFSET ?",
        base_args + [per_page, offset],
    ).fetchall()

    exact = None
    if q and page == 1 and re.fullmatch(r"[A-Za-z0-9._/-]{3,}", q):
        db_q = resolve_db_code(q)
        er = con.execute("SELECT * FROM cards WHERE zoro_no = ? AND image IS NOT NULL AND image != ''", (db_q.upper(),)).fetchone()
        if er is None and db_q != q:
            er = con.execute("SELECT * FROM cards WHERE zoro_no = ? AND image IS NOT NULL AND image != ''", (q.upper(),)).fetchone()
        if er is None and q.isdigit():
            er = con.execute("SELECT * FROM cards WHERE zoro_no = ? AND image IS NOT NULL AND image != ''", (f"G{q}",)).fetchone()
        if er is None:
            er = con.execute("SELECT * FROM cards WHERE mpn = ? COLLATE NOCASE AND image IS NOT NULL AND image != '' LIMIT 1", (q,)).fetchone()
        if er is not None:
            exact = er
            rows = [er] + [r for r in rows if r["zoro_no"] != er["zoro_no"]]

    if not q and not where:
        total, capped = product_count(), False
    else:
        total = con.execute(
            f"SELECT COUNT(*) AS n FROM (SELECT 1 {base} LIMIT {COUNT_CAP + 1})",
            base_args,
        ).fetchone()["n"]
        capped = total > COUNT_CAP
        if capped:
            total = COUNT_CAP

    if exact is not None and total == 0:
        total = 1
    pages = max((total + per_page - 1) // per_page, 1)
    items = cards_with_overrides(rows)

    payload = {
        "products": items,
        "total": total,
        "total_capped": capped,
        "count": total,
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "has_next": page < pages,
        "has_prev": page > 1,
        "query": q or None,
        "category": {
            "l1": l1, "l2": l2, "l3": l3,
            "l1_slug": slugify(l1), "l2_slug": slugify(l2), "l3_slug": slugify(l3),
        } if l1 else None,
        "facets": _facets(base, base_args, l1, l2, l3, q, brands),
    }
    return payload


def _facets(base, base_args, l1, l2, l3, q, selected_brands):
    con = _index()
    facets = {"brands": [], "categories": [], "price": None, "sampled": False}

    if not q and not selected_brands:
        if not l1:
            rows = con.execute(
                "SELECT l1 AS name, slug, count FROM cat_stats "
                "WHERE l2 IS NULL AND l3 IS NULL ORDER BY count DESC"
            ).fetchall()
            facets["categories"] = [
                {"name": r["name"], "slug": r["slug"], "count": r["count"], "level": 1}
                for r in rows
            ]
        elif l1 and not l2:
            rows = con.execute(
                "SELECT l2 AS name, l2_slug AS slug, count FROM cat_stats "
                "WHERE l1 = ? AND l2 IS NOT NULL AND l3 IS NULL ORDER BY count DESC",
                (l1,),
            ).fetchall()
            facets["categories"] = [
                {"name": r["name"], "slug": r["slug"], "count": r["count"], "level": 2}
                for r in rows
            ]
        else:
            rows = con.execute(
                "SELECT l3 AS name, l3_slug AS slug, count FROM cat_stats "
                "WHERE l1 = ? AND l2 = ? AND l3 IS NOT NULL ORDER BY count DESC",
                (l1, l2),
            ).fetchall()
            facets["categories"] = [
                {"name": r["name"], "slug": r["slug"], "count": r["count"], "level": 3}
                for r in rows
            ]
    else:
        rows = con.execute(
            f"""SELECT name, MAX(lvl) AS level, COUNT(*) AS n FROM (
                  SELECT CASE
                           WHEN c.l3 != '' THEN c.l3
                           WHEN c.l2 != '' THEN c.l2
                           ELSE c.l1
                         END AS name,
                         CASE
                           WHEN c.l3 != '' THEN 3
                           WHEN c.l2 != '' THEN 2
                           ELSE 1
                         END AS lvl
                  {base} LIMIT {FACET_SAMPLE}
                ) WHERE name != '' GROUP BY name ORDER BY n DESC LIMIT 15""",
            base_args,
        ).fetchall()
        facets["categories"] = [
            {"name": r["name"], "slug": slugify(r["name"]), "count": r["n"], "level": r["level"]}
            for r in rows
        ]
        facets["sampled"] = True

    brand_rows = con.execute(
        f"""SELECT brand, COUNT(*) AS n FROM
              (SELECT c.brand {base} LIMIT {FACET_SAMPLE})
            WHERE brand != '' GROUP BY brand ORDER BY n DESC LIMIT 30""",
        base_args,
    ).fetchall()
    facets["brands"] = [{"name": r["brand"], "count": r["n"]} for r in brand_rows]

    pr = con.execute(
        f"""SELECT MIN(price) AS lo, MAX(price) AS hi FROM
              (SELECT c.price {base} LIMIT {FACET_SAMPLE})""",
        base_args,
    ).fetchone()
    if pr and pr["lo"] is not None:
        facets["price"] = {"min": pr["lo"], "max": pr["hi"]}
    return facets


# ─────────────────────────────────────────────
#  Variations & related
# ─────────────────────────────────────────────

def variations(zoro_no):
    # 1. If enriched, check exact variant_members
    src = get_source_row(zoro_no)
    if src and src.get("variant_group") and src.get("variant_members"):
        try:
            members = json.loads(src["variant_members"]) if isinstance(src["variant_members"], str) else src["variant_members"]
            options = json.loads(src["variant_attributes"]) if isinstance(src.get("variant_attributes"), str) else src.get("variant_attributes", [])
            if members:
                me = {
                    "zoro_code": src["zoro_no"],
                    "title": src.get("title", ""),
                    "unit_price": _num(src.get("price")),
                    "variant_label": "This item",
                    "attributes": json.loads(src["specifications"]) if isinstance(src.get("specifications"), str) else src.get("specifications", {}),
                }
                all_prices = [m.get("price") for m in members if m.get("price") is not None] + ([me["unit_price"]] if me["unit_price"] is not None else [])
                return {
                    "current": zoro_no,
                    "variant_group": src.get("variant_group"),
                    "variant_options": options,
                    "lowest_price": min(all_prices) if all_prices else None,
                    "highest_price": max(all_prices) if all_prices else None,
                    "variations": [me] + [
                        {
                            "zoro_code": m.get("zoro_no"),
                            "title": m.get("title"),
                            "unit_price": m.get("price"),
                            "mfr_number": m.get("mfr_no"),
                            "attributes": m.get("attributes", {}),
                            "variant_label": ", ".join(str(v) for v in m.get("attributes", {}).values()),
                        }
                        for m in members if m.get("zoro_no") != zoro_no
                    ],
                }
        except Exception as e:
            log.warning("variations extraction error: %s", e)

    # 2. Fallback fuzzy variant search
    con = _index()
    cur = con.execute("SELECT * FROM cards WHERE zoro_no = ?", (zoro_no.upper(),)).fetchone()
    if cur is None:
        return {"current": zoro_no, "variations": []}

    return {"current": zoro_no, "variations": []}


def related(zoro_no, limit=8):
    con = _index()
    cur = con.execute("SELECT * FROM cards WHERE zoro_no = ?", (zoro_no.upper(),)).fetchone()
    if cur is None:
        return []
    rows = con.execute(
        """SELECT * FROM cards
           WHERE l1 = ? AND l2 = ? AND zoro_no != ?
             AND image != '' AND in_stock = 1
           ORDER BY (brand = ?) DESC, ABS(COALESCE(price,0) - ?) ASC
           LIMIT ?""",
        (cur["l1"], cur["l2"], cur["zoro_no"], cur["brand"],
         cur["price"] or 0, limit),
    ).fetchall()
    return cards_with_overrides(rows)


# ─────────────────────────────────────────────
#  Routes
# ─────────────────────────────────────────────

@catalog_bp.route("/api/catalog/status")
def catalog_status():
    ok = ready()
    info = {"ready": ok}
    if ok:
        meta = {r["k"]: r["v"] for r in _index().execute("SELECT * FROM meta")}
        info.update({
            "products": int(meta.get("product_count", 0)),
            "built_at": meta.get("built_at"),
            "source_db": meta.get("source_db"),
            "is_enriched": meta.get("is_enriched") == "1",
        })
    else:
        ensure_index()
        info["building"] = True
    return jsonify(info)


@catalog_bp.route("/api/products")
def list_products():
    if not ready():
        ensure_index()
        return _building_response()
    return jsonify(search(request.args))


@catalog_bp.route("/api/products/search")
def search_products():
    if not (request.args.get("q") or "").strip():
        return jsonify({"error": "Missing search query ?q=..."}), 400
    if not ready():
        ensure_index()
        return _building_response()
    return jsonify(search(request.args))


@catalog_bp.route("/api/products/<zoro_code>")
def product_detail(zoro_code):
    row = get_source_row(zoro_code)
    if row is None:
        return jsonify({"error": "Product not found"}), 404
    item = detail_dict(row)
    ovs = get_overrides([item["zoro_code"]])
    _apply_override(item, ovs.get(item["zoro_code"]))
    return jsonify(item)


@catalog_bp.route("/api/products/<zoro_code>/related")
def product_related(zoro_code):
    if not ready():
        return jsonify({"products": []})
    limit = min(request.args.get("limit", 8, type=int), 24)
    return jsonify({"products": related(zoro_code, limit)})


@catalog_bp.route("/api/products/<zoro_code>/variations")
def product_variations(zoro_code):
    if not ready():
        return jsonify({"current": zoro_code, "variations": []})
    return jsonify(variations(zoro_code))


@catalog_bp.route("/api/categories")
def categories():
    if not ready():
        ensure_index()
        return _building_response()
    con = _index()

    # L1 departments
    l1_rows = con.execute(
        "SELECT l1, slug, count, image FROM cat_stats WHERE l2 IS NULL AND l3 IS NULL "
        "ORDER BY count DESC"
    ).fetchall()

    # L2 subcategories
    l2_rows = con.execute(
        "SELECT l1, l2, l2_slug, count, image FROM cat_stats "
        "WHERE l2 IS NOT NULL AND l3 IS NULL ORDER BY count DESC"
    ).fetchall()

    # L3 leaf categories
    l3_rows = con.execute(
        "SELECT l1, l2, l3, l3_slug, count FROM cat_stats "
        "WHERE l3 IS NOT NULL ORDER BY count DESC"
    ).fetchall()

    l3_map = {}
    for r in l3_rows:
        key = (r["l1"], r["l2"])
        l3_map.setdefault(key, []).append({
            "name": r["l3"],
            "slug": r["l3_slug"],
            "count": r["count"],
        })

    l2_map = {}
    for r in l2_rows:
        children_l3 = l3_map.get((r["l1"], r["l2"]), [])
        l2_map.setdefault(r["l1"], []).append({
            "name": r["l2"],
            "slug": r["l2_slug"],
            "count": r["count"],
            "image": r["image"] or None,
            "children": children_l3,
        })

    return jsonify({"categories": [{
        "name": r["l1"],
        "slug": r["slug"],
        "count": r["count"],
        "image": r["image"] or None,
        "children": l2_map.get(r["l1"], []),
    } for r in l1_rows]})


@catalog_bp.route("/api/brands")
def brands():
    if not ready():
        ensure_index()
        return _building_response()
    limit = min(request.args.get("limit", 40, type=int), 500)
    q = (request.args.get("q") or "").strip()
    sql = "SELECT brand, slug, count, image FROM brand_stats"
    args = []
    if q:
        sql += " WHERE brand LIKE '%' || ? || '%'"
        args.append(q)
    sql += " ORDER BY count DESC LIMIT ?"
    args.append(limit)
    rows = _index().execute(sql, args).fetchall()
    return jsonify({"brands": [{
        "name": format_brand(r["brand"]), "slug": slugify(format_brand(r["brand"])), "count": r["count"],
        "image": r["image"] or None} for r in rows]})


@catalog_bp.route("/api/suggest")
def suggest():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2 or not ready():
        return jsonify({"products": [], "categories": [], "brands": []})
    con = _index()
    match = _fts_query(q)
    prows = con.execute(
        """SELECT c.* FROM fts f JOIN cards c ON c.zoro_no = f.zoro_no
           WHERE fts MATCH ? ORDER BY f.rank, c.rank DESC LIMIT 6""",
        (match,),
    ).fetchall()
    like = f"%{q}%"
    crows = con.execute(
        """SELECT l1, l2, l3, slug, l2_slug, l3_slug, count FROM cat_stats
           WHERE (l3 IS NOT NULL AND l3 LIKE ?) OR (l2 IS NOT NULL AND l2 LIKE ?) OR (l2 IS NULL AND l1 LIKE ?)
           ORDER BY count DESC LIMIT 4""", (like, like, like),
    ).fetchall()
    brows = con.execute(
        "SELECT brand, slug, count FROM brand_stats WHERE brand LIKE ? "
        "ORDER BY count DESC LIMIT 3", (like,),
    ).fetchall()
    return jsonify({
        "products": [{
            "zoro_code": r["zoro_no"], "title": r["title"], "brand": format_brand(r["brand"]),
            "unit_price": r["price"], "image_url": r["image"] or None,
            "uom": r["uom"],
        } for r in prows],
        "categories": [{
            "name": r["l3"] or r["l2"] or r["l1"],
            "parent": r["l2"] or r["l1"] if (r["l3"] or r["l2"]) else None,
            "l1_slug": r["slug"],
            "l2_slug": r["l2_slug"] or None,
            "l3_slug": r["l3_slug"] or None,
            "count": r["count"],
        } for r in crows],
        "brands": [{"name": format_brand(r["brand"]), "slug": slugify(format_brand(r["brand"])), "count": r["count"]}
                   for r in brows],
    })


@catalog_bp.route("/api/home")
def home_payload():
    if not ready():
        ensure_index()
        return _building_response()
    con = _index()

    def picks(kind, limit):
        rows = con.execute(
            """SELECT c.* FROM featured f JOIN cards c ON c.zoro_no = f.zoro_no
               WHERE f.kind = ? ORDER BY f.pos LIMIT ?""", (kind, limit),
        ).fetchall()
        return cards_with_overrides(rows)

    cats = con.execute(
        "SELECT l1, slug, count, image FROM cat_stats WHERE l2 IS NULL AND l3 IS NULL "
        "ORDER BY count DESC"
    ).fetchall()
    brand_rows = con.execute(
        "SELECT brand, slug, count, image FROM brand_stats "
        "ORDER BY count DESC LIMIT 12"
    ).fetchall()
    return jsonify({
        "product_count": product_count(),
        "categories": [{"name": r["l1"], "slug": r["slug"], "count": r["count"],
                        "image": r["image"] or None} for r in cats],
        "top_products": picks("top", 24),
        "new_products": picks("new", 12),
        "brands": [{"name": format_brand(r["brand"]), "slug": slugify(format_brand(r["brand"])), "count": r["count"],
                    "image": r["image"] or None} for r in brand_rows],
    })


# ─────────────────────────────────────────────
#  Image proxy (fallback when the browser can't hotlink the Zoro CDN)
# ─────────────────────────────────────────────

_ALLOWED_IMG_HOSTS = ("https://www.zoro.com/static/", "https://zoro.com/static/")
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36")


@catalog_bp.route("/api/img")
def image_proxy():
    url = request.args.get("u", "")
    if not url.startswith(_ALLOWED_IMG_HOSTS):
        abort(400)
    os.makedirs(IMG_CACHE, exist_ok=True)
    key = hashlib.sha1(url.encode()).hexdigest()
    ext = os.path.splitext(url)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        ext = ".jpg"
    path = os.path.join(IMG_CACHE, key + ext)
    if not os.path.exists(path):
        try:
            import requests as rq
            resp = rq.get(url, headers={"User-Agent": _UA}, timeout=15)
            resp.raise_for_status()
            with open(path, "wb") as f:
                f.write(resp.content)
        except Exception:
            log.warning("image proxy failed: %s", url)
            abort(404)
    mimetype = {
        ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
    }.get(ext, "image/jpeg")
    resp = send_file(path, mimetype=mimetype, conditional=True)
    resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp
