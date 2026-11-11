"""End-to-end and unit checks. Run with `python tests/test_pipeline.py` (or pytest)."""

import csv
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hathoro.data import fetch_rows, parse_listing  # noqa: E402
from hathoro.design import load_json  # noqa: E402
from hathoro.pricing import price_for, price_listing  # noqa: E402
from hathoro.shopify import listing_fields, make_handle  # noqa: E402
from hathoro.validate import Report, validate_csv  # noqa: E402

RULES = load_json(ROOT / "catalog_settings.json")["pricing"]
SETTINGS = load_json(ROOT / "catalog_settings.json")


def row(desc, **kw):
    return {"zoro_no": "T1", "short_description": desc, "sales_price": 1.0,
            "min_order_qty": 25, **kw}


def test_pricing_matches_plan_examples():
    a = price_listing(1.60, 25, RULES)
    assert (a.landed_cost, a.price) == (41.99, 46.99)
    b = price_listing(2.69, 25, RULES)
    assert (b.landed_cost, b.price, b.compare_at) == (60.53, 67.99, 84.99)
    for unit, qty in [(0.67, 25), (0.31, 10), (5.0, 10), (1.999, 15)]:
        p = price_listing(unit, qty, RULES)
        assert p.margin >= RULES["target_margin"] and f"{p.price:.2f}".endswith(".99")


def test_freight_surcharge_gets_no_discount():
    # G015226935: sales_price 48.37 = unit_price 25.37 + 23.00 freight surcharge, x5
    li = parse_listing(row("36 in x 24 in x 18 in Shipping Box, Kraft, Corrugate, Double Wall, "
                           "48 ECT", sales_price=48.37, unit_price=25.37, min_order_qty=5))
    p = price_for(li, RULES)
    assert (li.unit_cost, li.surcharge, p.base_cost, p.surcharge) == (25.37, 23.0, 126.85, 115.0)
    assert p.landed_cost == round(p.discounted_cost + 115.0, 2) and p.freight == 0
    assert p.margin >= RULES["target_margin"]


def test_free_shipping_is_judged_on_list_price():
    # $52.50 list is $47.25 after the partner discount: Zoro still ships it free
    p = price_listing(2.10, 25, RULES)
    assert (p.base_cost, p.discounted_cost, p.freight, p.price) == (52.5, 47.25, 0.0, 52.99)


def test_pk_rows_count_boxes_not_units():
    li = parse_listing(row("24 in x 18 in x 18 in Shipping Box, Kraft, Corrugate, Single Wall, "
                           "32 ECT, 10 PK", uom="PK", min_order_qty=1, sales_price=52.64))
    assert (li.order_qty, li.pack) == (1, 10)
    assert price_for(li, RULES).base_cost == 52.64


def test_multi_depth_uses_full_height_and_lists_cut_downs():
    li = parse_listing(row("26 in x 20 in x 6 in, 8 in, 10 in, 12 in Multi-Depth Box, Kraft, "
                           "Corrugate, Single Wall, 200#"))
    assert (li.L, li.W, li.H, li.score_depths, li.multi_depth) == (26, 20, 12, [10, 8, 6], True)
    assert li.fields()["box_type"] == "MULTI-DEPTH SHIPPING BOX"
    assert "scored at 10\", 8\", 6\"" in listing_fields(li)["style_items"]


def test_boxes_that_differ_get_different_handles():
    descs = ["24 in x 12 in x 12 in Shipping Box, Kraft, Corrugate, Single Wall, 32 ECT",
             "24 in x 12 in x 12 in Shipping Box, Kraft, Corrugate, Single Wall, 200#",
             "24 in x 12 in x 12 in Shipping Box, White, Corrugate, Single Wall, 32 ECT",
             "24 in x 12 in x 12 in Multi-Depth Box, Kraft, Corrugate, Single Wall, 32 ECT"]
    lis = [parse_listing(row(d)) for d in descs]
    handles = [make_handle(li, SETTINGS) for li in lis]
    assert len(set(handles)) == 4, handles
    white = listing_fields(lis[2])
    assert white["color_text"] == "White" and "White" in SETTINGS["shopify"]["title"].format(**white)


def test_burst_rated_box_is_not_called_ect():
    li = parse_listing(row("12 in x 9 in x 6 in Shipping Box, Kraft, Corrugate, Single Wall, 200#"))
    f = li.fields()
    assert (li.L, li.W, li.H, li.ect, li.burst, li.max_lbs) == (12, 9, 6, None, 200, 65)
    assert "ECT" not in f["rating"] + f["rating_claim"] + f["seal_sub"]


def test_ect_and_brand_name_parsing():
    li = parse_listing(row("Zoro Select Shipping Box, 22 in x 18 in x 8 in, Single Wall, 32 ECT"))
    assert (li.ect, li.max_lbs, li.fields()["rating"]) == (32, 65, "32 ECT")
    li = parse_listing(row("Zoro Select Shipping Box, 12\" L x 9\" W x 6\" H"))
    assert li.ect is None and not li.has_rating          # "Select" must not read as ECT


def test_dimension_formats():
    cases = {"Corrugated Box, 12-1/2 x 9-1/2 x 4 in": (12.5, 9.5, 4),
             "Box, Inside Length 24 in, Inside Width 6 in, Inside Height 6 in": (24, 6, 6),
             "Shipping Box, 9 in x 12 in x 3 in": (12, 9, 3)}
    for desc, dims in cases.items():
        li = parse_listing(row(desc))
        assert (li.L, li.W, li.H) == dims, desc


def test_db_query_filters_and_read_only_path_with_spaces():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "TSV Files" / "combined_reports.db"
        db.parent.mkdir()
        con = sqlite3.connect(db)
        cols = ("zoro_no manufacturer_part_number upc_code zoro_brand_name short_description "
                "long_description sales_price unit_price min_order_qty uom lead_time "
                "availabilityStatus ship_pack_length ship_pack_width ship_pack_height "
                "ship_pack_weight zoro_image_name").split()
        con.execute(f"CREATE TABLE zoro_data ({', '.join(cols)})")
        base = dict.fromkeys(cols, "")
        rows = [
            {"zoro_no": "OK1", "zoro_brand_name": "Zoro Select", "lead_time": "1 day",
             "short_description": "Shipping Box, 12 in x 9 in x 6 in", "availabilityStatus": "In Stock"},
            {"zoro_no": "LID", "zoro_brand_name": "Zoro Select", "lead_time": "1 day",
             "short_description": "Shipping Box Lid, 12 in x 9 in", "availabilityStatus": "In Stock"},
            {"zoro_no": "BRAND", "zoro_brand_name": "Uline", "lead_time": "1 day",
             "short_description": "Shipping Box, 12 in x 9 in x 6 in", "availabilityStatus": "In Stock"},
            {"zoro_no": "SLOW", "zoro_brand_name": "Zoro Select", "lead_time": "5 days",
             "short_description": "Shipping Box, 12 in x 9 in x 6 in", "availabilityStatus": "In Stock"},
        ]
        for r in rows:
            r = {**base, "sales_price": 1.0, "min_order_qty": 25, **r}
            con.execute(f"INSERT INTO zoro_data VALUES ({','.join('?' * len(cols))})",
                        [r[c] for c in cols])
        con.commit()
        con.close()
        found, origin_col = fetch_rows(db)
        assert [r["zoro_no"] for r in found] == ["OK1"] and origin_col is None


def test_export_tool_uses_the_pipeline_filters():
    import re as _re
    sys.path.insert(0, str(ROOT / "tools"))
    import export_zoro_rows
    from hathoro.data import QUERY
    norm = lambda s: _re.sub(r"\s+", " ", s).strip()  # noqa: E731
    assert norm(export_zoro_rows.WHERE) in norm(QUERY)


def run_pipeline(out_dir, *extra):
    cmd = [sys.executable, str(ROOT / "generate_shipping_boxes_catalog.py"),
           "--fixture", "test_data/G0507745.json", "--out-dir", str(out_dir), "--status", "draft",
           *extra]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)


def test_full_pipeline_g0507745_without_image_urls():
    with tempfile.TemporaryDirectory() as tmp:
        res = run_pipeline(tmp)
        assert res.returncode == 0, res.stdout + res.stderr
        out = Path(tmp)
        rows = list(csv.DictReader(open(out / "hathoro_zoro_select_shipping_boxes_master.csv")))
        assert len(rows) == 1
        r = rows[0]
        assert r["URL handle"] == "hathoro-12x9x6-single-wall-200-lb-test-shipping-boxes-25-pack"
        assert r["Collection"] == "Shipping Boxes"
        assert r["Product category"] == "Office Supplies > Shipping Supplies > Moving & Shipping Boxes"
        assert (r["Price"], r["Status"], r["Published on online store"]) == ("23.99", "draft", "true")
        assert len(list((out / "upload_to_shopify_files").glob("*.jpg"))) == 5
        assert "PASSED" in (out / "validation_report.txt").read_text()


def test_full_pipeline_with_image_urls():
    with tempfile.TemporaryDirectory() as tmp:
        res = run_pipeline(tmp, "--image-base-url", "https://cdn.example.com/files")
        assert res.returncode == 0, res.stdout + res.stderr
        rows = list(csv.DictReader(open(Path(tmp) / "hathoro_zoro_select_shipping_boxes_master.csv")))
        assert [r["Image position"] for r in rows] == ["1", "2", "3", "4", "5"]
        assert all(r["Product image URL"].startswith("https://cdn.example.com/files/") for r in rows)
        assert all(r["Title"] == "" for r in rows[1:])


def test_validator_catches_broken_csv():
    header = ["Title", "URL handle", "Price", "Compare-at price", "Status", "Charge tax",
              "Option1 name", "Option1 value", "SKU", "Product image URL", "Image position",
              "Collection", "Bogus column"]
    bad = [
        ["Box {pack} Pack", "Bad Handle!", "23.999", "20.00", "live", "TRUE", "", "", "SKU1",
         "http://x.test/a_thumb.jpg", "2", "Wrong", "x"],
        ["", "Bad Handle!", "", "", "", "", "", "", "", "https://x.test/b.jpg", "1", "", ""],
        ["Box", "second-box", "23.99", "20.00", "active", "true", "Pack Size", "25 Pack", "SKU2",
         "", "", "Shipping Boxes", ""],
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bad.csv"
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(header)
            w.writerows(bad)
        report = Report()
        validate_csv(path, report, SETTINGS)
        messages = " | ".join(m for _, _, m in report.errors)
    for expected in ("unknown columns", "URL handle", "Price", "Compare-at", "Status",
                     "Charge tax", "Option1", "placeholder", "https", "_thumb", "positions",
                     "Collection"):
        assert expected in messages, f"validator missed: {expected}\n{messages}"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
