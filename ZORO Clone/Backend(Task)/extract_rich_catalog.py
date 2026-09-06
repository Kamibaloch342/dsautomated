"""
High-Speed Asynchronous Enrichment Extractor for Zoro Catalog
─────────────────────────────────────────────────────────────
Extracts full Vuex/Pinia state from zoro.com:
  • Complete 3-5 level Category hierarchy (names, slugs, IDs)
  • Exact structured specifications dictionary
  • Multi-image gallery URLs (all media angles & embedded spec PDFs)
  • Variant families, option attributes, and sibling SKUs
  • Logistics/Compliance (isHazmat, isLTL, restrictedStates, leadTime, minRetailQty, packageQty)
  • SEO tags and slugs

Results are stored in Backend(Task)/catalog_enrichment.db with resume checkpointing.
"""

import os
import sys
import json
import re
import codecs
import time
import ssl
import sqlite3
import signal
import argparse
import asyncio
import aiohttp
import certifi

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_DB = os.environ.get(
    "CATALOG_DB",
    os.path.abspath(os.path.join(BASE_DIR, "..", "..", "Automations", "TSV Files", "combined_reports.db")),
)
ENRICH_DB = os.path.join(BASE_DIR, "catalog_enrichment.db")

USER_AGENT = (
    "Mozilla/5.0 (RajarEnterpriseLLC/2026) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.7727.138 Safari/537.36"
)

PINIA_RE = re.compile(r'window\.PINIA_INITIAL_STATE\s*=\s*\"(.*?)\";', re.DOTALL)


def init_db(db_path=ENRICH_DB):
    con = sqlite3.connect(db_path)
    con.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;

        CREATE TABLE IF NOT EXISTS enriched_products (
            zoro_no             TEXT PRIMARY KEY,
            title               TEXT,
            brand               TEXT,
            mfr_no              TEXT,
            upc                 TEXT,
            slug                TEXT,
            price               REAL,
            original_price      REAL,
            price_unit          TEXT,
            min_qty             INTEGER,
            package_qty         INTEGER,
            lead_time_days      INTEGER,
            is_dropship         INTEGER,
            is_hazmat           INTEGER,
            is_ltl              INTEGER,
            is_grainger         INTEGER,
            sales_status        TEXT,
            erp_id              TEXT,
            description         TEXT,
            restricted_states   TEXT,
            categories          TEXT,
            category_path       TEXT,
            l1                  TEXT,
            l2                  TEXT,
            l3                  TEXT,
            l4                  TEXT,
            l5                  TEXT,
            specifications      TEXT,
            media               TEXT,
            variant_group       TEXT,
            variant_attributes  TEXT,
            variant_members     TEXT,
            scraped_at          TEXT,
            status              TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_enriched_cats ON enriched_products(l1, l2, l3);
        CREATE INDEX IF NOT EXISTS idx_enriched_vg ON enriched_products(variant_group);
        CREATE INDEX IF NOT EXISTS idx_enriched_status ON enriched_products(status);
        CREATE INDEX IF NOT EXISTS idx_enriched_brand ON enriched_products(brand);
    """)
    con.commit()
    con.close()


def get_target_skus(limit=None, skip_existing=True, brand=None, top_brands=0, fast_ship_only=False, shortlist_zoro_select=False):
    if not os.path.exists(SOURCE_DB):
        raise FileNotFoundError(f"Source catalog not found at {SOURCE_DB}")

    src = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)
    cur = src.cursor()

    existing = set()
    if skip_existing and os.path.exists(ENRICH_DB):
        econ = sqlite3.connect(ENRICH_DB)
        rows = econ.execute("SELECT zoro_no FROM enriched_products WHERE status = 'OK'").fetchall()
        existing = {r[0] for r in rows}
        econ.close()

    where = ["zoro_no != ''"]
    args = []

    if top_brands and top_brands > 0:
        if fast_ship_only:
            order_col = """SUM(CASE WHEN (lead_time LIKE '%1 day%' OR lead_time = '1') 
                                AND (availabilityStatus IN ('In Stock', 'Limited Stock') OR (LOWER(availabilityStatus) NOT IN ('out of stock', 'discontinued', 'backordered') AND availabilityStatus IS NOT NULL AND availabilityStatus != ''))
                                AND (is_blind_ship = 'True' OR is_blind_ship = 'YES') 
                          THEN 1 ELSE 0 END)"""
        else:
            order_col = "COUNT(*)"

        top_rows = cur.execute(f"""
            SELECT zoro_brand_name 
            FROM zoro_data 
            WHERE zoro_brand_name IS NOT NULL AND zoro_brand_name != '' AND zoro_brand_name != 'Zoro Select'
            GROUP BY zoro_brand_name 
            ORDER BY {order_col} DESC 
            LIMIT ?
        """, (top_brands,)).fetchall()

        top_brand_names = [r[0] for r in top_rows]
        print(f"📋 Top {top_brands} Brands: {', '.join(top_brand_names[:10])} (+{max(0, len(top_brand_names)-10)} more)")

        marks = ",".join("?" * len(top_brand_names))
        where.append(f"zoro_brand_name IN ({marks})")
        args.extend(top_brand_names)
    elif brand:
        if brand.upper() == "3M":
            where.append("(zoro_brand_name = '3M' OR zoro_brand_name LIKE '3M %')")
        elif brand.lower() in ("zoro select", "zoro_select"):
            where.append("(zoro_brand_name = 'Zoro Select' OR zoro_brand_name IS NULL OR zoro_brand_name = '')")
        else:
            where.append("zoro_brand_name = ?")
            args.append(brand)

    if fast_ship_only or shortlist_zoro_select:
        where.append("(lead_time LIKE '%1 day%' OR lead_time = '1')")
        where.append("(is_blind_ship = 'True' OR is_blind_ship = 'YES')")
        where.append("(is_dropship = 'False' OR is_dropship = 'NO' OR is_dropship = '0' OR is_dropship IS NULL)")
        where.append("(availabilityStatus IN ('In Stock', 'Limited Stock') OR (LOWER(availabilityStatus) NOT IN ('out of stock', 'discontinued', 'backordered') AND availabilityStatus IS NOT NULL AND availabilityStatus != ''))")
        where.append("(zoro_image_name IS NOT NULL AND zoro_image_name != '')")

    query = f"SELECT zoro_no FROM zoro_data WHERE {' AND '.join(where)} ORDER BY rowid ASC"

    cur.execute(query, args)
    all_skus = []
    while True:
        chunk = cur.fetchmany(50000)
        if not chunk:
            break
        for (zno,) in chunk:
            if zno not in existing:
                all_skus.append(zno)
                if limit and len(all_skus) >= limit:
                    break
        if limit and len(all_skus) >= limit:
            break

    src.close()
    return all_skus


def parse_pinia_data(html, zoro_no):
    m = PINIA_RE.search(html)
    if not m:
        return None

    try:
        raw_str = m.group(1)
        decoded = codecs.decode(raw_str, "unicode_escape")
        data = json.loads(decoded)
    except Exception:
        return None

    cache = data.get("productStore", {}).get("productDetailsCache", {})
    prod = cache.get(zoro_no) or (list(cache.values())[0] if cache else None)
    if not prod:
        return None

    # Brand: if brand is empty or missing, default to Zoro Select
    brand = (prod.get("brand") or "").strip()
    if not brand:
        brand = "Zoro Select"

    # Specifications
    attrs = prod.get("attributes") or []
    specs = {}
    for a in attrs:
        name = (a.get("name") or "").strip()
        val = a.get("value")
        if name and val not in (None, ""):
            specs[name] = str(val).strip()

    # Category hierarchy
    cat_paths = prod.get("primaryCategoryPaths") or []
    categories = []
    cat_names = []
    for c in cat_paths:
        if isinstance(c, dict):
            c_id = c.get("1", "")
            c_name = c.get("2", "")
            c_slug = c.get("3", "")
            if c_name:
                categories.append({"id": c_id, "name": c_name, "slug": c_slug})
                cat_names.append(c_name)
        elif isinstance(c, str) and c.strip():
            categories.append({"id": "", "name": c.strip(), "slug": ""})
            cat_names.append(c.strip())

    category_path = " > ".join(cat_names)
    l1 = cat_names[0] if len(cat_names) > 0 else None
    l2 = cat_names[1] if len(cat_names) > 1 else None
    l3 = cat_names[2] if len(cat_names) > 2 else None
    l4 = cat_names[3] if len(cat_names) > 3 else None
    l5 = cat_names[4] if len(cat_names) > 4 else None

    # Media / Gallery / Documents
    media_list = []
    for m_item in prod.get("media") or []:
        if isinstance(m_item, dict) and m_item.get("name"):
            name = m_item.get("name")
            m_type = m_item.get("type", "image/jpeg")
            url = f"https://www.zoro.com/static/cms/product/full/{name}"
            media_list.append({
                "name": name,
                "type": m_type,
                "url": url,
                "is_pdf": name.lower().endswith(".pdf"),
            })

    # Variants
    variants_obj = prod.get("variants") or {}
    variant_group = variants_obj.get("label")
    variant_attributes = variants_obj.get("attributes") or []
    variant_members = []
    for member in variants_obj.get("products") or []:
        m_zno = member.get("zoroNo")
        if m_zno:
            m_attrs = {a.get("name"): a.get("value") for a in member.get("attributes") or [] if a.get("name")}
            variant_members.append({
                "zoro_no": m_zno,
                "title": member.get("title"),
                "mfr_no": member.get("mfrNo"),
                "price": member.get("price"),
                "price_unit": member.get("priceUnit"),
                "attributes": m_attrs,
            })

    return {
        "zoro_no": prod.get("zoroNo") or zoro_no,
        "title": prod.get("title") or "",
        "brand": brand,
        "mfr_no": prod.get("mfrNo") or "",
        "upc": prod.get("upc") or "",
        "slug": prod.get("slug") or "",
        "price": prod.get("price"),
        "original_price": prod.get("originalPrice"),
        "price_unit": prod.get("priceUnit") or "EA",
        "min_qty": prod.get("minRetailQty") or 1,
        "package_qty": prod.get("packageQty") or 1,
        "lead_time_days": prod.get("leadTime"),
        "is_dropship": 1 if prod.get("isDropShipDirect") else 0,
        "is_hazmat": 1 if prod.get("isHazmat") else 0,
        "is_ltl": 1 if prod.get("isLTL") else 0,
        "is_grainger": 1 if prod.get("isGraingerSKU") else 0,
        "sales_status": prod.get("salesStatus") or "",
        "erp_id": str(prod.get("erpId") or "") or None,
        "description": prod.get("description") or "",
        "restricted_states": json.dumps(prod.get("restrictedStates") or []),
        "categories": json.dumps(categories),
        "category_path": category_path,
        "l1": l1,
        "l2": l2,
        "l3": l3,
        "l4": l4,
        "l5": l5,
        "specifications": json.dumps(specs),
        "media": json.dumps(media_list),
        "variant_group": variant_group,
        "variant_attributes": json.dumps(variant_attributes),
        "variant_members": json.dumps(variant_members),
        "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "OK",
    }


async def fetch_worker(sku_queue, result_queue, session, ssl_ctx):
    while True:
        sku = await sku_queue.get()
        if sku is None:
            sku_queue.task_done()
            break

        url = f"https://www.zoro.com/i/{sku}/"
        record = None
        status = "ERROR"

        try:
            async with session.get(url, ssl=ssl_ctx, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    parsed = parse_pinia_data(html, sku)
                    if parsed:
                        record = parsed
                    else:
                        status = "PARSE_FAIL"
                elif resp.status == 404:
                    status = "NOT_FOUND"
                else:
                    status = f"HTTP_{resp.status}"
        except asyncio.TimeoutError:
            status = "TIMEOUT"
        except Exception as e:
            status = f"ERR: {str(e)[:30]}"

        if not record:
            record = {
                "zoro_no": sku,
                "title": None,
                "brand": "Zoro Select",
                "mfr_no": None,
                "upc": None,
                "slug": None,
                "price": None,
                "original_price": None,
                "price_unit": None,
                "min_qty": None,
                "package_qty": None,
                "lead_time_days": None,
                "is_dropship": 0,
                "is_hazmat": 0,
                "is_ltl": 0,
                "is_grainger": 0,
                "sales_status": None,
                "erp_id": None,
                "description": None,
                "restricted_states": "[]",
                "categories": "[]",
                "category_path": None,
                "l1": None,
                "l2": None,
                "l3": None,
                "l4": None,
                "l5": None,
                "specifications": "{}",
                "media": "[]",
                "variant_group": None,
                "variant_attributes": "[]",
                "variant_members": "[]",
                "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "status": status,
            }

        await result_queue.put(record)
        sku_queue.task_done()


async def db_writer(result_queue, total_targets):
    con = sqlite3.connect(ENRICH_DB)
    ins_sql = """
        INSERT OR REPLACE INTO enriched_products VALUES (
            :zoro_no, :title, :brand, :mfr_no, :upc, :slug,
            :price, :original_price, :price_unit, :min_qty, :package_qty,
            :lead_time_days, :is_dropship, :is_hazmat, :is_ltl, :is_grainger,
            :sales_status, :erp_id, :description, :restricted_states,
            :categories, :category_path, :l1, :l2, :l3, :l4, :l5,
            :specifications, :media, :variant_group, :variant_attributes,
            :variant_members, :scraped_at, :status
        )
    """

    batch = []
    saved = 0
    ok_count = 0
    t0 = time.time()

    while True:
        record = await result_queue.get()
        if record is None:
            if batch:
                con.executemany(ins_sql, batch)
                con.commit()
            result_queue.task_done()
            break

        batch.append(record)
        if record["status"] == "OK":
            ok_count += 1
        saved += 1

        if len(batch) >= 50:
            con.executemany(ins_sql, batch)
            con.commit()
            batch = []

        if saved % 50 == 0 or saved == total_targets:
            elapsed = time.time() - t0
            rate = saved / elapsed if elapsed > 0 else 0
            pct = (saved / total_targets) * 100 if total_targets else 100
            print(
                f"[{saved:>6,}/{total_targets:,}] ({pct:>5.1f}%) "
                f"| OK: {ok_count:,} "
                f"| Speed: {rate:>5.1f} items/s "
                f"| Elapsed: {elapsed:>4.0f}s",
                flush=True,
            )

        result_queue.task_done()

    con.close()
    return saved, ok_count


async def run_enrichment(limit=None, concurrency=25, skip_existing=True, brand=None, top_brands=0, fast_ship_only=False, shortlist=False):
    init_db()

    if top_brands > 0:
        brand_label = f"Top {top_brands} Brands"
    elif brand:
        brand_label = f"Brand: {brand}"
    else:
        brand_label = "Zoro Select Shortlist" if shortlist else "All Brands"

    mode_label = f"{brand_label} | Fast-Ship Only: {fast_ship_only or shortlist}"

    print("=" * 70)
    print("🚀 Zoro Catalog Rich Enrichment Pipeline")
    print(f"• User-Agent: {USER_AGENT[:50]}...")
    print(f"• Mode: {mode_label}")
    print(f"• Target Limit: {limit if limit else 'ALL MATCHING'}")
    print(f"• Concurrency: {concurrency} async workers")
    print(f"• Target Database: {ENRICH_DB}")
    print("=" * 70)

    skus = get_target_skus(
        limit=limit,
        skip_existing=skip_existing,
        brand=brand,
        top_brands=top_brands,
        fast_ship_only=fast_ship_only,
        shortlist_zoro_select=shortlist,
    )
    total = len(skus)
    print(f"📦 Found {total:,} products queued for extraction.\n")

    if total == 0:
        print("✅ No products to process (all targets are already enriched!).")
        return

    sku_queue = asyncio.Queue()
    result_queue = asyncio.Queue()

    for s in skus:
        await sku_queue.put(s)

    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    conn = aiohttp.TCPConnector(limit=concurrency, limit_per_host=concurrency, ssl=ssl_ctx)
    headers = {"User-Agent": USER_AGENT}

    async with aiohttp.ClientSession(headers=headers, connector=conn) as session:
        writer_task = asyncio.create_task(db_writer(result_queue, total))

        workers = [
            asyncio.create_task(fetch_worker(sku_queue, result_queue, session, ssl_ctx))
            for _ in range(concurrency)
        ]

        await sku_queue.join()

        for _ in range(concurrency):
            await sku_queue.put(None)
        await asyncio.gather(*workers)

        await result_queue.put(None)
        saved, ok_count = await writer_task

    print("\n" + "=" * 70)
    print("🎉 Enrichment Batch Complete!")
    print(f"• Total Processed: {saved:,}")
    print(f"• Successfully Enriched (OK): {ok_count:,} ({(ok_count/saved)*100:.1f}%)" if saved else "• OK: 0")
    print(f"• Stored in: {ENRICH_DB}")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Zoro Rich Catalog Extractor")
    parser.add_argument("--top-brands", type=int, default=0, help="Extract top N brands by volume (e.g. 20)")
    parser.add_argument("--brand", type=str, default=None, help="Brand name to extract (e.g. '3M', 'Zoro Select')")
    parser.add_argument("--limit", type=int, default=0, help="Number of products to extract (0 for all matching)")
    parser.add_argument("--concurrency", type=int, default=25, help="Number of concurrent async workers (default: 25)")
    parser.add_argument("--force", action="store_true", help="Re-extract products already marked as OK")
    parser.add_argument("--fast-ship", action="store_true", help="Only extract products that ship in 1 day, in stock, blindship")
    args = parser.parse_args()

    limit = None if args.limit <= 0 else args.limit

    asyncio.run(run_enrichment(
        limit=limit,
        concurrency=args.concurrency,
        skip_existing=not args.force,
        brand=args.brand,
        top_brands=args.top_brands,
        fast_ship_only=args.fast_ship,
        shortlist=False if (args.brand or args.top_brands) else True,
    ))


if __name__ == "__main__":
    main()
