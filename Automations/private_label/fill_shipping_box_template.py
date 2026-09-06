#!/usr/bin/env python3
"""fill_shipping_box_template.py — fill Amazon's SHIPPING_BOX category template
(.xlsm downloaded from Seller Central) with HOANK listings from listings.csv.

New-format template: labels row 4, attribute names row 5, data from row 8.
Values must use the DISPLAY text from the template's dropdowns ("New",
"United States", "Not Applicable", "GTIN Exempt", ...).

    python3 fill_shipping_box_template.py --sku HOA-Kam-G1606934-10 --price 59.99
    python3 fill_shipping_box_template.py --all --out full.xlsm      # later, all 1,402
"""
import argparse, csv, os, re, sqlite3
import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
LISTINGS = os.path.join(HERE, "output", "listings.csv")
ZORO_DB = os.path.join(HERE, "..", "TSV Files", "combined_reports.db")
TEMPLATE = os.path.expanduser("~/Downloads/SHIPPING_BOX.xlsm")
ATTR_ROW, DATA_ROW = 5, 8


def zoro_dims(codes):
    c = sqlite3.connect(f"file:{ZORO_DB}?mode=ro", uri=True)
    ph = ",".join("?" * len(codes))
    out = {r[0]: r[1:] for r in c.execute(
        f"SELECT zoro_no, ship_pack_length, ship_pack_width, ship_pack_height, "
        f"ship_pack_weight FROM zoro_data WHERE zoro_no IN ({ph})", list(codes))}
    c.close()
    return out


def pack_multipliers(base):
    """Kamran's ladder: pack 10 → ×1..×5 (10..50); packs >10 → ×1..×4
    (15→60, 25→100). Base pack always included as ×1."""
    return [1, 2, 3, 4, 5] if base <= 10 else [1, 2, 3, 4]


def scale_listing(L, mult):
    """Derive the ×N variation row from the base listing: pack count, texts."""
    base = int(float(L["pack_qty"] or 1))
    total = base * mult
    C = dict(L)
    C["pack_qty"] = str(total)
    zc = L["zoro_code"]
    C["sku"] = f"HOA-Kam-{zc}-{total}"
    for f in ("title", "bullet1", "bullet2", "bullet3", "bullet4", "bullet5", "description"):
        t = C.get(f, "") or ""
        t = t.replace(f"{base} Pack", f"{total} Pack")
        t = t.replace(f"set of {base}", f"set of {total}")
        t = t.replace(f"{base} per pack", f"{total} per pack")
        C[f] = t
    C["_mult"] = mult
    return C


def build_row(L, dims, price, qty, ship_template="18.99"):
    """listings.csv row -> {attribute_name: value} using template display values."""
    size = L["size"].replace(" ", "")
    m = re.match(r"([\d.]+)x([\d.]+)x([\d.]+)", size)
    length, width, height = (m.groups() if m else ("", "", ""))
    pack = int(float(L["pack_qty"] or 1))
    pl, pw, ph_, pwt = dims.get(L["zoro_code"], ("", "", "", ""))
    _m = int(L.get("_mult", 1) or 1)
    if pwt:
        pwt = round(float(pwt) * _m, 2)
    mk = "[marketplace_id=ATVPDKIKX0DER]"
    lt = "[language_tag=en_US]"
    v = {
        "contribution_sku#1.value": L["sku"],
        "product_type#1.value": "SHIPPING_BOX",
        "::record_action": "Create or Replace (Full Update)",
        f"item_name{mk}{lt}#1.value": L["title"],
        f"brand{mk}{lt}#1.value": "HOANK",
        f"manufacturer{mk}{lt}#1.value": "HOANK",
        "amzn1.volt.ca.product_id_type": "GTIN Exempt",
        f"item_type_keyword{mk}#1.value": "shipping boxes",
        f"model_number{mk}#1.value": f"HO-K{L['zoro_code']}-{pack}",
        f"model_name{mk}{lt}#1.value": f'{length}x{width}x{height} {pack} Pack',
        f"is_fulfilled_from_origin_item{mk}#1.value": "Yes",
        f"street_date{mk}#1.value": "2026-08-01",
        f"merchant_release_date{mk}#1.value": "2026-08-01",
        "fulfillment_availability#1.fulfillment_channel_code": "Fulfillment by Merchant (Default)",
        f"list_price{mk}#1.value": price,
        f"product_description{mk}{lt}#1.value": L["description"],
        f"country_of_origin{mk}#1.value": "United States",
        f"supplier_declared_dg_hz_regulation{mk}#1.value": "Not Applicable",
        f"condition_type{mk}#1.value": "New",
        f"color{mk}{lt}#1.value": "Kraft Brown",
        f"size{mk}{lt}#1.value": f'{length}"L x {width}"W x {height}"H ({pack} Pack)',
        f"material{mk}{lt}#1.value": "Cardboard",
        f"number_of_items{mk}#1.value": pack,
        f"item_package_quantity{mk}#1.value": 1,
        f"unit_count{mk}#1.value": pack,
        f"unit_count{mk}#1.type{lt}.value": "Count",
        f"number_of_packs{mk}#1.value": pack,
        f"item_length_width_height{mk}#1.length.value": length,
        f"item_length_width_height{mk}#1.length.unit": "Inches",
        f"item_length_width_height{mk}#1.width.value": width,
        f"item_length_width_height{mk}#1.width.unit": "Inches",
        f"item_length_width_height{mk}#1.height.value": height,
        f"item_length_width_height{mk}#1.height.unit": "Inches",
        "fulfillment_availability#1.quantity": qty,
        "purchasable_offer[marketplace_id=ATVPDKIKX0DER][audience=ALL]#1.our_price#1.schedule#1.value_with_tax": price,
    }
    v[f"package_size_name{mk}{lt}#1.value"] = L.get("_setname") or f"{pack} Pack"
    v[f"merchant_shipping_group{mk}#1.value"] = ship_template
    if L.get("ect"):
        v[f"breaking_strength{mk}#1.value"] = L["ect"]
        v[f"breaking_strength{mk}#1.unit"] = "Pounds"
    if pwt:
        v[f"item_weight{mk}#1.value"] = pwt
        v[f"item_weight{mk}#1.unit"] = "Pounds"
        v[f"item_package_weight{mk}#1.value"] = pwt
        v[f"item_package_weight{mk}#1.unit"] = "Pounds"

    # ── images: served PUBLICLY by the dashboard at the permanent Funnel URL
    # (route /pl_images/, no login — Amazon downloads them during processing).
    # Slot 1 = this SKU's generated main; slot 2 = its dims image; slots 3+ =
    # whatever constants exist in private_label/constant_images (sorted).
    base = "https://kamrans-macbook-pro.tailbd8602.ts.net/pl_images"
    main_f = f"{L['sku']}_main_upload.png"
    if os.path.isfile(os.path.join(HERE, "output", "images", main_f)):
        v[f"main_product_image_locator{mk}#1.media_location"] = f"{base}/{main_f}"
    const_dir = os.path.join(HERE, "constant_images")
    others = []
    if os.path.isdir(const_dir):
        others = sorted(f for f in os.listdir(const_dir)
                        if f.lower().endswith((".jpg", ".jpeg", ".png")))
    for i, f in enumerate(others[:8], 1):
        v[f"other_product_image_locator_{i}{mk}#1.media_location"] = f"{base}/{f}"
    if pl:
        v[f"item_package_dimensions{mk}#1.length.value"] = pl
        v[f"item_package_dimensions{mk}#1.length.unit"] = "Inches"
        v[f"item_package_dimensions{mk}#1.width.value"] = pw
        v[f"item_package_dimensions{mk}#1.width.unit"] = "Inches"
        v[f"item_package_dimensions{mk}#1.height.value"] = ph_
        v[f"item_package_dimensions{mk}#1.height.unit"] = "Inches"
    for i in range(1, 6):
        b = L.get(f"bullet{i}", "")
        if b:
            v[f"bullet_point{mk}{lt}#{i}.value"] = b
    for i, kw in enumerate(["moving boxes", "packing boxes", "corrugated box",
                            "cardboard boxes for shipping", "mailing boxes"], 1):
        v[f"generic_keyword{mk}{lt}#{i}.value"] = kw
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sku")
    ap.add_argument("--skus", help="comma/space-separated list of SKUs")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--price", type=float, default=None,
                    help="explicit price; otherwise (cost+ship)/(1-referral-profit)")
    ap.add_argument("--qty", type=int, default=1000)
    ap.add_argument("--variations", action="store_true",
                    help="expand STANDALONE rows into parent + pack-ladder children")
    ap.add_argument("--upc", default=None,
                    help="12-digit UPC; without it the file uses GTIN Exempt")
    ap.add_argument("--ship", type=float, default=18.99,
                    help="outbound shipping cost per order (Kamran: $18.99)")
    ap.add_argument("--referral", type=float, default=0.15, help="Amazon referral fee share")
    ap.add_argument("--profit", type=float, default=0.15, help="target profit share of price")
    ap.add_argument("--ship-template", default="Main PL Tamplate 18.99",
                    help="Seller Central shipping template name")
    ap.add_argument("--out", default=os.path.expanduser("~/Downloads/SHIPPING_BOX_HOANK_filled.xlsm"))
    a = ap.parse_args()

    rows = list(csv.DictReader(open(LISTINGS)))
    if a.all:
        todo = rows
    elif a.skus:
        want = [x for x in a.skus.replace(",", " ").split() if x]
        by = {r["sku"]: r for r in rows}
        missing = [w for w in want if w not in by]
        if missing:
            raise SystemExit(f"SKUs not in listings.csv: {missing}")
        todo = [by[w] for w in want]
    else:
        sku = a.sku or "HOA-Kam-G1606934-10"
        todo = [r for r in rows if r["sku"] == sku]
        if not todo:
            raise SystemExit(f"SKU {sku} not found in listings.csv")

    # sheet-defined families: PARENT/CHILD rows map straight onto the template,
    # SET_NAME theme, child set name = the sheet's variation_name (uniquified —
    # Amazon rejects duplicate set names within one family)
    seen_names = {}
    for L in todo:
        rel = L.get("relationship", "")
        if rel == "PARENT":
            L["_parent"] = "1"
        elif rel == "CHILD":
            L["_child_of"] = L["parent_sku"]
            name = (L.get("variation_name") or "").strip() or f"{L['pack_qty']} Pack"
            key = (L["parent_sku"], name)
            n = seen_names.get(key, 0) + 1
            seen_names[key] = n
            L["_setname"] = name if n == 1 else f"{name} (Style {n})"

    if a.variations:
        # STANDALONE → parent + pack-ladder children (NUMBER_OF_ITEMS theme)
        expanded = []
        for L in todo:
            if L["relationship"] != "STANDALONE":
                expanded.append(L)          # existing families pass through untouched
                continue
            base = int(float(L["pack_qty"] or 1))
            parent = dict(L)
            parent["sku"] = f"HOA-Kam-{L['zoro_code']}-P"
            parent["_parent"] = "1"
            parent["title"] = (L["title"] or "").replace(f", {base} Pack", "")
            expanded.append(parent)
            for m in pack_multipliers(base):
                c = scale_listing(L, m)
                c["_child_of"] = parent["sku"]
                expanded.append(c)
        todo = expanded

    dims = zoro_dims({r["zoro_code"] for r in todo})
    print(f"filling {len(todo)} listing(s) into the template…")
    wb = openpyxl.load_workbook(TEMPLATE, keep_vba=True)
    ws = wb["Template"]
    attr_col = {}
    for ci, cell in enumerate(ws[ATTR_ROW], 1):
        if cell.value:
            attr_col[str(cell.value).strip()] = ci

    unknown = set()
    for ri, L in enumerate(todo):
        mult = int(L.get("_mult", 1) or 1)
        if a.price is not None:
            price = a.price
        else:
            # LIVE-CURVE pricing (matches the 18x18x4 family Kamran hand-built):
            # base pack = zoro pack price × 1.11, bigger packs earn per-count
            # discounts (margin comes from the $18.99 shipping template + zoro
            # coupons, not from item markup).
            LADDER_DISCOUNT = {1: 0.0, 2: 0.06, 3: 0.105, 4: 0.125, 5: 0.125}
            base = float(L["pack_cost"] or 0) * 1.11
            price = round(base * mult * (1.0 - LADDER_DISCOUNT.get(mult, 0.125)), 2)
        vals = build_row(L, dims, price, a.qty, a.ship_template)
        vmk = "[marketplace_id=ATVPDKIKX0DER]"
        vlt = "[language_tag=en_US]"
        if L.get("_parent"):
            vals[f"parentage_level{vmk}#1.value"] = "Parent"
            vals["variation_theme#1.name"] = "SET_NAME"
            # a parent is a non-buyable family head: strip offer/id/count fields
            # and the varying attribute (package_size_name) — EXPLICIT list, and
            # log anything that survives so this can never fail silently again
            drop_terms = ("purchasable_offer", "fulfillment_availability", "list_price",
                          "merchant_shipping_group", "product_id", "max_order_quantity",
                          "number_of_items", "unit_count", "number_of_packs",
                          "package_size_name", "item_package_quantity",
                          "model_number", "model_name", "item_weight",
                          "item_package_weight", "item_package_dimensions",
                          "breaking_strength")
            dropped = [k for k in list(vals) if any(t in k for t in drop_terms)]
            for k in dropped:
                del vals[k]
            leftover = [k for k in vals if any(t in k for t in drop_terms)]
            if leftover:
                print(f"  ⚠️ parent still carries: {leftover}")
        elif L.get("_child_of"):
            vals[f"parentage_level{vmk}#1.value"] = "Child"
            vals[f"child_parent_sku_relationship{vmk}#1.parent_sku"] = L["_child_of"]
            vals["variation_theme#1.name"] = "SET_NAME"
        if a.upc and mult == 1 and not L.get("_parent"):
            d = [int(x) for x in a.upc]
            assert len(d) == 12 and (10 - (sum(d[0:11:2])*3 + sum(d[1:11:2])) % 10) % 10 == d[11],                 f"UPC {a.upc} fails check-digit validation"
            vals["amzn1.volt.ca.product_id_type"] = "UPC"
            vals["amzn1.volt.ca.product_id_value"] = a.upc
        # write EVERY template column: our value or None. The template file may
        # carry residue (Kamran edits/saves it in Excel), and popped parent
        # fields must come out EMPTY, not inherit old row data.
        for attr, ci in attr_col.items():
            # NOTE: must assign via .value — cell(value=None) means "leave
            # unchanged" in openpyxl, which let template residue survive
            ws.cell(row=DATA_ROW + ri, column=ci).value = vals.get(attr)
        for attr in vals:
            if attr not in attr_col:
                unknown.add(attr)
        print(f"  row {DATA_ROW + ri}: {L['sku']}  price=${price}  qty={a.qty}")
    if unknown:
        print("⚠️ attributes not found in template (skipped):")
        for u in sorted(unknown):
            print("   ", u)
    wb.save(a.out)
    print(f"saved → {a.out}")


if __name__ == "__main__":
    main()
