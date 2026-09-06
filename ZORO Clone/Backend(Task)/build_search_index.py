"""Build catalog_index.db — the derived search/browse index over the catalog.

Prioritizes catalog_enrichment.db (the enriched Zoro Select catalog, ~110k rows)
with rich 3-5 level category trees, exact specifications, and multi-media.
Falls back to combined_reports.db if enrichment is not present.

Rebuild any time with:  python build_search_index.py
"""

import os
import re
import sys
import json
import time
import sqlite3
import collections

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENRICH_DB = os.path.join(BASE_DIR, "catalog_enrichment.db")
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

BATCH = 15000

OUT_OF_STOCK = {"out of stock", "discontinued", "backordered"}

_slug_re = re.compile(r"[^a-z0-9]+")


def slugify(name):
    return _slug_re.sub("-", (name or "").lower()).strip("-")


def lead_days(lead_time):
    m = re.search(r"(\d+)", str(lead_time or ""))
    return int(m.group(1)) if m else 1


def to_int(v, default=1):
    try:
        return max(int(float(v)), 1)
    except (TypeError, ValueError):
        return default


def extract_first_image(media_raw, fallback_img=""):
    if not media_raw:
        return fallback_img or ""
    try:
        media = json.loads(media_raw) if isinstance(media_raw, str) else media_raw
        for m in media:
            if isinstance(m, dict):
                if not m.get("is_pdf") and m.get("url"):
                    return m["url"]
                if not m.get("is_pdf") and m.get("name"):
                    return f"https://www.zoro.com/static/cms/product/full/{m['name']}"
            elif isinstance(m, str) and not m.lower().endswith(".pdf"):
                return f"https://www.zoro.com/static/cms/product/full/{m}"
    except Exception:
        pass
    return fallback_img or ""


def build():
    t0 = time.time()
    use_enriched = False

    if os.path.exists(ENRICH_DB):
        try:
            econ = sqlite3.connect(f"file:{ENRICH_DB}?mode=ro", uri=True)
            cnt = econ.execute("SELECT COUNT(*) FROM enriched_products WHERE status = 'OK'").fetchone()[0]
            econ.close()
            if cnt > 0:
                use_enriched = True
                print(f"📦 Using catalog_enrichment.db ({cnt:,} enriched products)")
        except Exception:
            pass

    if not use_enriched and not os.path.exists(SOURCE_DB):
        sys.exit(f"Source catalog not found: {SOURCE_DB}")

    tmp = INDEX_DB + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)

    dst = sqlite3.connect(tmp)
    dst.executescript("""
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        PRAGMA cache_size=-200000;

        CREATE TABLE cards (
            zoro_no     TEXT PRIMARY KEY,
            title       TEXT,
            brand       TEXT,
            price       REAL,
            uom         TEXT,
            image       TEXT,
            l1          TEXT,
            l2          TEXT,
            l3          TEXT,
            avail       TEXT,
            in_stock    INTEGER,
            min_qty     INTEGER,
            lead_days   INTEGER,
            lead_time   TEXT,
            mpn         TEXT,
            upc         TEXT,
            created     TEXT,
            is_dropship INTEGER,
            rank        INTEGER
        );
        CREATE VIRTUAL TABLE fts USING fts5(
            zoro_no UNINDEXED, title, brand, mpn, upc, cats,
            prefix='2 3 4', tokenize='unicode61 remove_diacritics 2'
        );
        CREATE TABLE cat_stats (
            l1 TEXT, l2 TEXT, l3 TEXT,
            slug TEXT, l2_slug TEXT, l3_slug TEXT,
            count INTEGER, image TEXT
        );
        CREATE TABLE brand_stats (brand TEXT, slug TEXT, count INTEGER, image TEXT);
        CREATE TABLE lexicon (name TEXT PRIMARY KEY, cnt INTEGER);
        CREATE TABLE featured (zoro_no TEXT, kind TEXT, pos INTEGER);
        CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
    """)

    n = 0
    card_rows, fts_rows = [], []
    ins_cards = ("INSERT OR REPLACE INTO cards VALUES "
                 "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
    ins_fts = "INSERT INTO fts (zoro_no,title,brand,mpn,upc,cats) VALUES (?,?,?,?,?,?)"
    lex = collections.Counter()

    if use_enriched:
        src = sqlite3.connect(f"file:{ENRICH_DB}?mode=ro", uri=True)
        cur = src.execute("""
            SELECT zoro_no, title, brand, price, price_unit, media,
                   l1, l2, l3, lead_time_days, min_qty, mfr_no, upc,
                   scraped_at, is_dropship, specifications
            FROM enriched_products
            WHERE status = 'OK'
        """)

        while True:
            rows = cur.fetchmany(BATCH)
            if not rows:
                break
            for (zno, title, brand, price, uom, media_raw, l1, l2, l3,
                 lead_days_val, minq, mpn, upc, created, dropship, specs_raw) in rows:
                if not zno:
                    continue
                brand_clean = (brand or "").strip()
                if not brand_clean or brand_clean.lower() in ("zoro select", "zoro", "zoro_select"):
                    brand_clean = "EKUIPT Select"

                title_clean = (title or "").strip()
                if "Zoro Select" in title_clean:
                    title_clean = title_clean.replace("Zoro Select", "EKUIPT Select")
                elif "zoro select" in title_clean:
                    title_clean = title_clean.replace("zoro select", "EKUIPT Select")

                img = extract_first_image(media_raw)
                if not img or img.strip() == "":
                    continue
                in_stock = 1
                avail = "In Stock"
                ldays = to_int(lead_days_val, default=1)
                ltime = f"Ships in {ldays} day{'s' if ldays != 1 else ''}"
                rank = (2 if img else 0) + in_stock

                card_rows.append((
                    zno, title_clean, brand_clean, price, uom or "EA",
                    img, l1 or "", l2 or "", l3 or "", avail, in_stock,
                    to_int(minq), ldays, ltime, mpn or "",
                    upc or "", created or "", 1 if dropship else 0,
                    rank,
                ))
                fts_rows.append((
                    zno, title_clean, brand_clean, mpn or "",
                    upc or "", f"{l1 or ''} {l2 or ''} {l3 or ''}"
                ))

                if specs_raw:
                    try:
                        sdict = json.loads(specs_raw)
                        for sk in sdict.keys():
                            lex[sk] += 1
                    except Exception:
                        pass

            dst.executemany(ins_cards, card_rows)
            dst.executemany(ins_fts, fts_rows)
            n += len(card_rows)
            card_rows, fts_rows = [], []
            if n % 25000 == 0:
                print(f"  {n:,} products indexed ({time.time()-t0:.1f}s)", flush=True)

        src.close()
    else:
        src = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)
        cur = src.execute("""
            SELECT zoro_no, short_description, zoro_brand_name, sales_price, uom,
                   zoro_image_name, level_one_name, level_two_name,
                   availabilityStatus, min_order_qty, lead_time,
                   manufacturer_part_number, upc_code, created_dt, is_dropship,
                   long_description
            FROM zoro_data
        """)

        while True:
            rows = cur.fetchmany(BATCH)
            if not rows:
                break
            for (zno, title, brand, price, uom, img, l1, l2, avail, minq,
                 lead, mpn, upc, created, dropship, ldesc) in rows:
                if not zno or not img or img.strip() == "":
                    continue
                avail = (avail or "").strip()
                in_stock = 0 if avail.lower() in OUT_OF_STOCK else 1
                rank = (2 if img else 0) + in_stock
                card_rows.append((
                    zno, title or "", brand or "", price, uom or "EA",
                    img or "", l1 or "", l2 or "", "", avail, in_stock,
                    to_int(minq), lead_days(lead), lead or "", mpn or "",
                    upc or "", created or "", 1 if dropship == "YES" else 0,
                    rank,
                ))
                fts_rows.append((zno, title or "", brand or "", mpn or "",
                                 upc or "", f"{l1 or ''} {l2 or ''}"))
            dst.executemany(ins_cards, card_rows)
            dst.executemany(ins_fts, fts_rows)
            n += len(rows)
            card_rows, fts_rows = [], []
        src.close()

    print(f"Loaded {n:,} products, building indexes…", flush=True)
    dst.executescript("""
        CREATE INDEX idx_cards_rank       ON cards (rank, price);
        CREATE INDEX idx_cards_l1l2_rank  ON cards (l1, l2, rank);
        CREATE INDEX idx_cards_l1l2l3     ON cards (l1, l2, l3, rank);
        CREATE INDEX idx_cards_l1_rank    ON cards (l1, rank);
        CREATE INDEX idx_cards_brand      ON cards (brand);
        CREATE INDEX idx_cards_price      ON cards (price);
        CREATE INDEX idx_cards_l1_price   ON cards (l1, price);
        CREATE INDEX idx_cards_l1l2_price ON cards (l1, l2, price);
        CREATE INDEX idx_cards_l1l2_brand ON cards (l1, l2, brand);
        CREATE INDEX idx_cards_title      ON cards (title);
    """)
    dst.execute("ANALYZE")

    # ── category + brand stats ──
    # L1 stats
    dst.execute("""
        INSERT INTO cat_stats (l1, l2, l3, slug, l2_slug, l3_slug, count, image)
        SELECT l1, NULL, NULL, '', '', '', COUNT(*),
               MAX(CASE WHEN image != '' AND in_stock = 1 THEN image ELSE '' END)
        FROM cards WHERE l1 != '' GROUP BY l1
    """)
    # L2 stats
    dst.execute("""
        INSERT INTO cat_stats (l1, l2, l3, slug, l2_slug, l3_slug, count, image)
        SELECT l1, l2, NULL, '', '', '', COUNT(*),
               MAX(CASE WHEN image != '' AND in_stock = 1 THEN image ELSE '' END)
        FROM cards WHERE l1 != '' AND l2 != '' GROUP BY l1, l2
    """)
    # L3 stats
    dst.execute("""
        INSERT INTO cat_stats (l1, l2, l3, slug, l2_slug, l3_slug, count, image)
        SELECT l1, l2, l3, '', '', '', COUNT(*),
               MAX(CASE WHEN image != '' AND in_stock = 1 THEN image ELSE '' END)
        FROM cards WHERE l1 != '' AND l2 != '' AND l3 != '' GROUP BY l1, l2, l3
    """)

    for (rid, l1, l2, l3) in dst.execute("SELECT rowid, l1, l2, l3 FROM cat_stats").fetchall():
        dst.execute(
            "UPDATE cat_stats SET slug=?, l2_slug=?, l3_slug=? WHERE rowid=?",
            (slugify(l1), slugify(l2) if l2 else "", slugify(l3) if l3 else "", rid),
        )

    dst.execute("""
        INSERT INTO brand_stats (brand, slug, count, image)
        SELECT brand, '', COUNT(*),
               MAX(CASE WHEN image != '' AND in_stock = 1 THEN image ELSE '' END)
        FROM cards WHERE brand != '' GROUP BY brand
    """)
    for (rid, b) in dst.execute("SELECT rowid, brand FROM brand_stats").fetchall():
        dst.execute("UPDATE brand_stats SET slug=? WHERE rowid=?", (slugify(b), rid))

    # ── lexicon ──
    dst.executemany("INSERT OR IGNORE INTO lexicon VALUES (?,?)",
                    [(k, v) for k, v in lex.items() if v >= 10])

    # ── featured picks for the homepage ──
    top_l1 = [r[0] for r in dst.execute(
        "SELECT l1 FROM cat_stats WHERE l2 IS NULL ORDER BY count DESC LIMIT 10"
    )]
    pos = 0
    for l1 in top_l1:
        for (zno,) in dst.execute(
            """SELECT zoro_no FROM cards
               WHERE l1=? AND image != '' AND in_stock=1
                 AND price BETWEEN 10 AND 500 AND title != ''
               ORDER BY random() LIMIT 6""", (l1,)):
            dst.execute("INSERT INTO featured VALUES (?,?,?)", (zno, "top", pos))
            pos += 1

    pos = 0
    for (zno,) in dst.execute(
        """SELECT zoro_no FROM cards
           WHERE image != '' AND in_stock=1 AND price > 5 AND title != ''
           ORDER BY created DESC LIMIT 40"""):
        dst.execute("INSERT INTO featured VALUES (?,?,?)", (zno, "new", pos))
        pos += 1

    dst.executemany("INSERT INTO meta VALUES (?,?)", [
        ("built_at", time.strftime("%Y-%m-%d %H:%M:%S")),
        ("source_db", ENRICH_DB if use_enriched else SOURCE_DB),
        ("product_count", str(n)),
        ("lexicon_size", str(len(lex))),
        ("is_enriched", "1" if use_enriched else "0"),
    ])
    dst.commit()
    dst.execute("PRAGMA optimize")
    dst.close()

    if os.path.exists(INDEX_DB):
        os.replace(INDEX_DB, INDEX_DB + ".old")
    os.replace(tmp, INDEX_DB)
    if os.path.exists(INDEX_DB + ".old"):
        os.remove(INDEX_DB + ".old")

    size_mb = os.path.getsize(INDEX_DB) / 1e6
    print(f"🎉 Done — {n:,} products indexed, {size_mb:.1f} MB in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    build()
