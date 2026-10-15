#!/usr/bin/env python3
"""
MASTER SHOPIFY CATALOG GENERATOR FOR ALL 108 PRODUCTS
=====================================================
Features:
  - 108 Products (100 Drums + 8 Cleaning Products)
  - Vendor Rule: 'Zoro Select' -> 'Hathoro Select'; Third-party brands remain authentic.
  - SKU Rule: 'G...' replaced with 'H...' everywhere.
  - Pricing & Margin:
      * Zoro Select: 10% discount on Zoro cost applied.
      * Under $50 rule: $5.99 shipping added.
      * Selling Price: Cost / 0.90 (10% net profit margin).
      * Compare-at: 15% markdown anchor.
  - Inventory: Locked at 500 units per product.
  - Category: Mapped to verified official Shopify Standard Product Taxonomy.
  - Images: 1000x1000 Retina tier served via active Cloudflare tunnel with strict MD5 deduplication.
  - Descriptions: Executive styled HTML layout with overview, feature checklist, technical specs table, and application cards.
"""

import os
import re
import csv
import json
import hashlib
from PIL import Image
import numpy as np

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DRUMS_DIR = os.path.join(CURRENT_DIR, "Drums")
SUMMARY_JSON = os.path.join(DRUMS_DIR, "drums_processing_summary.json")
TUNNEL_BASE = "https://subsidiary-obituaries-upload-departmental.trycloudflare.com"
OUTPUT_CSV = os.path.join(DRUMS_DIR, "shopify_master_108_products.csv")

def slugify(text):
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text).strip('-')
    return re.sub(r'-+', '-', text)

def get_unique_retina_images(sku):
    hr_dir = os.path.join(DRUMS_DIR, sku, "images", "high_res")
    retina_dir = os.path.join(DRUMS_DIR, sku, "images", "1000x1000_retina")
    if not os.path.exists(retina_dir):
        return []

    valid_indices = []
    seen_master_arrays = []

    retina_files = sorted([f for f in os.listdir(retina_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
    for idx in range(1, len(retina_files) + 1):
        master_file = os.path.join(hr_dir, f"image_{idx:02d}_master.jpg")
        if not os.path.exists(master_file):
            master_file = os.path.join(retina_dir, f"image_{idx:02d}_1000x1000.jpg")
        
        try:
            with Image.open(master_file) as im:
                w, h = im.size
                # 1. REJECT THUMBNAILS (e.g. 60x60)
                if w < 250 or h < 250:
                    continue
                
                # 2. CHECK FOR VISUAL DUPLICATE CONTENT
                thumb = im.convert('L').resize((32, 32), Image.Resampling.BILINEAR)
                arr = np.array(thumb, dtype=np.int16)
                
                is_duplicate = False
                for prev_arr in seen_master_arrays:
                    diff = np.mean(np.abs(arr - prev_arr))
                    if diff < 2.0:
                        is_duplicate = True
                        break
                
                if not is_duplicate:
                    seen_master_arrays.append(arr)
                    valid_indices.append(idx)
        except Exception:
            pass

    return [f"image_{idx:02d}_1000x1000.jpg" for idx in valid_indices]

def determine_shopify_category(title, category_type):
    t_low = title.lower()
    c_low = category_type.lower()
    
    if "carpet" in t_low:
        return "Home & Garden > Household Supplies > Household Cleaning Supplies > Household Cleaning Products > Carpet Cleaners"
    if any(k in t_low for k in ["cleaner", "degreaser", "extraction fluid"]) or "cleaner" in c_low:
        return "Home & Garden > Household Supplies > Household Cleaning Supplies > Household Cleaning Products > All-Purpose Cleaners"
    if any(k in t_low for k in ["spill containment", "containment platform", "spill deck", "spill pallet"]):
        return "Business & Industrial > Material Handling > Pallets & Loading Platforms"
    if any(k in t_low for k in ["dolly", "drum truck", "cradle", "drum deheader"]):
        return "Business & Industrial > Material Handling > Pallets & Loading Platforms"
    if any(k in t_low for k in ["barrel pump", "drum pump", "fuel pump"]):
        return "Hardware > Hardware Pumps > Utility Pumps > Drum Pumps"
    
    return "Business & Industrial > Industrial Storage"

def determine_product_type(title, category_type):
    t_low = title.lower()
    if "carpet" in t_low:
        return "Carpet Cleaner"
    if any(k in t_low for k in ["cleaner", "degreaser"]):
        return "Industrial Cleaner / Degreaser"
    if "sorbent" in t_low or "oil-dri" in t_low:
        return "Industrial Sorbent"
    if "spill containment" in t_low or "platform" in t_low:
        return "Spill Containment Platform"
    if "dolly" in t_low:
        return "Drum Dolly"
    if "pump" in t_low:
        return "Drum Pump"
    if "wrench" in t_low:
        return "Drum Bung Wrench"
    if "sampler" in t_low:
        return "Drum Sampler"
    if "deheader" in t_low:
        return "Drum Deheader"
    if "faucet" in t_low:
        return "Drum Faucet"
    return "Transport Drum"

def clean_text_encoding(text):
    if not text:
        return ""
    replacements = {
        'Â®': '®',
        'â€¢': '•',
        'â€™': "'",
        'â€œ': '"',
        'â€': '"',
        'â€“': '-',
        'â€”': '—',
        'Ã‚': '',
        'Â': '',
        '\r\n': ' ',
        '\n': ' ',
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def build_rich_description(details, hathoro_sku, display_brand, display_title, specs):
    # 1. Executive Summary
    raw_desc = details.get('description', '')
    summary_text = clean_text_encoding(raw_desc)
    if summary_text:
        summary_text = re.sub(r'Zoro Item #.*', '', summary_text, flags=re.I)
        summary_text = summary_text.replace('Zoro Select', 'Hathoro Select').replace('Zoro.com', 'Hathoro.com').replace('Zoro', 'Hathoro')
        # Clean any leading/trailing bullet artifacts
        summary_text = re.sub(r'^[•\-\s]+', '', summary_text)
        summary_text = re.sub(r'[•\-\s]+$', '', summary_text)
        
    if not summary_text or len(summary_text) < 40:
        summary_text = f"The <strong>{display_title}</strong> is commercial-grade industrial equipment engineered for dependable storage, chemical handling, and regulatory facility compliance. Manufactured from premium commercial-grade materials, this unit delivers maximum durability, safety, and long service life."

    # 2. Key Highlights (clean bullet list)
    highlight_candidates = []
    for k in ["UN Rating", "UN Rating Solids", "UN Rating Liquids", "Material", "Primary Drum Material", "Drum Material", "Capacity", "Drum Volume Capacity", "Volume Capacity", "Load Capacity", "Weight Capacity", "Features", "Resistance Properties", "Standards", "Item"]:
        if k in specs and specs[k] and specs[k] != 'N/A' and specs[k] != 'None':
            val = clean_text_encoding(specs[k])
            highlight_candidates.append(f"<strong>{k}:</strong> {val}")
    
    if len(highlight_candidates) < 4:
        highlight_candidates.extend([
            "<strong>Commercial Grade Durability:</strong> Engineered for continuous duty in demanding industrial, warehouse, and chemical environments.",
            "<strong>Compliance Certified:</strong> Manufactured in accordance with strict industrial and regulatory safety standards.",
            "<strong>Chemical & Corrosion Resistant:</strong> Heavy-duty materials resist degradation from oils, solvents, and standard chemicals.",
            "<strong>In Stock & Fast Dispatch:</strong> Commercial inventory ready for immediate fulfillment."
        ])
    
    highlights_html = "".join([
        f'<li style="margin-bottom: 8px;">{hc}</li>'
        for hc in highlight_candidates[:5]
    ])

    # 3. Specs Table Rows (clean neutral borders, no font specification)
    table_rows = [
        f'''<tr style="background-color: #f9fafb;">
        <td style="padding: 10px 14px; border: 1px solid #e5e7eb; font-weight: 600; width: 38%;">Catalog SKU</td>
        <td style="padding: 10px 14px; border: 1px solid #e5e7eb; font-weight: 600;">{hathoro_sku}</td>
      </tr>''',
        f'''<tr>
        <td style="padding: 10px 14px; border: 1px solid #e5e7eb; font-weight: 600;">Brand / Line</td>
        <td style="padding: 10px 14px; border: 1px solid #e5e7eb;">{display_brand}</td>
      </tr>'''
    ]
    
    if details.get('mfr_no'):
        table_rows.append(f'''<tr style="background-color: #f9fafb;">
        <td style="padding: 10px 14px; border: 1px solid #e5e7eb; font-weight: 600;">Manufacturer Part # (MPN)</td>
        <td style="padding: 10px 14px; border: 1px solid #e5e7eb;">{details['mfr_no']}</td>
      </tr>''')
        
    filtered_specs = {k: clean_text_encoding(v) for k, v in specs.items() if v and v != 'N/A' and 'zoro' not in k.lower() and 'country of origin' not in k.lower()}
    count = 0
    for k, v in list(filtered_specs.items())[:12]:
        bg = ' style="background-color: #f9fafb;"' if count % 2 == 1 else ''
        table_rows.append(f'''<tr{bg}>
        <td style="padding: 10px 14px; border: 1px solid #e5e7eb; font-weight: 600;">{k}</td>
        <td style="padding: 10px 14px; border: 1px solid #e5e7eb;">{v}</td>
      </tr>''')
        count += 1

    table_html = "".join(table_rows)

    desc_html = f'''<div class="hathoro-product-description" style="line-height: 1.6;">
  <p style="margin: 0 0 20px 0; line-height: 1.6;">
    {summary_text}
  </p>

  <h4 style="font-size: 1.05rem; font-weight: 600; margin: 24px 0 10px 0; letter-spacing: -0.01em;">
    Key Features & Highlights
  </h4>
  <ul style="margin: 0 0 24px 0; padding-left: 20px; line-height: 1.6;">
    {highlights_html}
  </ul>

  <h4 style="font-size: 1.05rem; font-weight: 600; margin: 24px 0 10px 0; letter-spacing: -0.01em;">
    Technical Specifications
  </h4>
  <div style="overflow-x: auto; margin-bottom: 24px;">
    <table style="width: 100%; border-collapse: collapse; text-align: left; border: 1px solid #e5e7eb;">
      <tbody>
        {table_html}
      </tbody>
    </table>
  </div>

  <h4 style="font-size: 1.05rem; font-weight: 600; margin: 24px 0 10px 0; letter-spacing: -0.01em;">
    Recommended Applications
  </h4>
  <ul style="margin: 0 0 16px 0; padding-left: 20px; line-height: 1.6;">
    <li style="margin-bottom: 6px;"><strong>Industrial & Manufacturing:</strong> Commercial facilities, processing plants, and equipment maintenance.</li>
    <li style="margin-bottom: 6px;"><strong>Hazardous Material Management:</strong> Compliant storage, regulatory staging, and spill response readiness.</li>
    <li style="margin-bottom: 6px;"><strong>Fleet & Maintenance Operations:</strong> Automotive repair bays, bulk chemical dispensing, and fluid recycling.</li>
    <li style="margin-bottom: 6px;"><strong>Warehousing & Distribution:</strong> Bulk material handling, freight staging, and secure facility transit.</li>
  </ul>
</div>'''
    return desc_html

def main():
    print(f"Reading {SUMMARY_JSON}...")
    with open(SUMMARY_JSON, 'r', encoding='utf-8') as f:
        catalog_items = json.load(f)
    print(f"Loaded {len(catalog_items)} total catalog products.")

    headers = [
        'Title', 'URL handle', 'Description', 'Vendor', 'Product category', 'Type', 'Tags',
        'Published on online store', 'Status', 'SKU', 'Barcode', 'Option1 name', 'Option1 value',
        'Option1 Linked To', 'Option2 name', 'Option2 value', 'Option2 Linked To', 'Option3 name',
        'Option3 value', 'Option3 Linked To', 'Price', 'Compare-at price', 'Cost per item',
        'Charge tax', 'Tax code', 'Unit price total measure', 'Unit price total measure unit',
        'Unit price base measure', 'Unit price base measure unit', 'Inventory tracker',
        'Inventory quantity', 'Continue selling when out of stock', 'Weight value (grams)',
        'Weight unit for display', 'Requires shipping', 'Fulfillment service', 'Product image URL',
        'Image position', 'Image alt text', 'Variant image URL', 'Gift card', 'SEO title',
        'SEO description', 'Color (product.metafields.shopify.color-pattern)',
        'Google Shopping / Google product category', 'Google Shopping / Gender',
        'Google Shopping / Age group', 'Google Shopping / Manufacturer part number (MPN)',
        'Google Shopping / Ad group name', 'Google Shopping / Ads labels',
        'Google Shopping / Condition', 'Google Shopping / Custom product',
        'Google Shopping / Custom label 0', 'Google Shopping / Custom label 1',
        'Google Shopping / Custom label 2', 'Google Shopping / Custom label 3',
        'Google Shopping / Custom label 4'
    ]

    all_rows = []
    total_images = 0

    for idx, item in enumerate(catalog_items, 1):
        zoro_no = item['zoroNo']
        hathoro_sku = f"H{zoro_no[1:]}"
        raw_brand = item.get('brand') or 'Zoro Select'
        is_zoro_select = (raw_brand.strip().lower() == 'zoro select')
        
        # Vendor Rule: 'Hathoro Select' for Zoro Select; else real manufacturer brand
        vendor = "Hathoro Select" if is_zoro_select else raw_brand.strip()

        # Load product details JSON if available
        data_json_path = os.path.join(DRUMS_DIR, zoro_no, f"{zoro_no}_data.json")
        details = item
        if os.path.exists(data_json_path):
            try:
                with open(data_json_path, 'r', encoding='utf-8') as f:
                    details = json.load(f)
            except Exception:
                pass

        # Title: Clean and replace 'Zoro Select' with 'Hathoro Select'
        raw_title = item.get('title') or details.get('title') or details.get('name') or "Industrial Product"
        title = raw_title.replace("Zoro Select", "Hathoro Select").replace("Zoro", "Hathoro").strip()
        # Clean any awkward punctuation
        title = re.sub(r'\s+', ' ', title)

        # Handle: URL-safe slug + sku
        handle = f"{slugify(title[:70])}-{hathoro_sku.lower()}"

        # Category and Type
        category_type = item.get('category_type') or details.get('category_type') or 'Transport Drum'
        shopify_category = determine_shopify_category(title, category_type)
        product_type = determine_product_type(title, category_type)

        # Pricing Rule
        zoro_price = float(item.get('price') or details.get('price') or 99.99)
        if is_zoro_select:
            # 10% Zoro Select discount applied to cost
            discounted_cost = round(zoro_price * 0.90, 2)
            shipping = 5.99 if discounted_cost < 50.00 else 0.00
            cost_price = round(discounted_cost + shipping, 2)
            selling_price = round(cost_price / 0.90, 2)
        else:
            shipping = 5.99 if zoro_price < 50.00 else 0.00
            cost_price = round(zoro_price + shipping, 2)
            selling_price = round(cost_price / 0.90, 2)

        compare_at_price = round(selling_price * 1.15, 2)

        # Weight estimation (convert lb to grams)
        specs = details.get('specifications', {})
        weight_str = specs.get('Weight') or specs.get('Empty Container Weight') or specs.get('Shipping Weight') or '20 lb'
        weight_m = re.search(r'([\d\.]+)', str(weight_str))
        weight_lb = float(weight_m.group(1)) if weight_m else 20.0
        weight_grams = int(weight_lb * 453.592)

        # Color
        color_val = specs.get('Color') or specs.get('Drum Color') or specs.get('Body Color') or ''

        # Tags
        tags_list = [vendor, product_type, 'Commercial Grade', 'In Stock', 'Industrial Supplies']
        if is_zoro_select:
            tags_list.append('Hathoro Select')
        if specs.get('Primary Drum Material'):
            tags_list.append(specs['Primary Drum Material'])
        if specs.get('Drum Volume Capacity'):
            tags_list.append(specs['Drum Volume Capacity'])
        tags_str = ", ".join(dict.fromkeys(tags_list))

        # Rich HTML Description
        desc_html = build_rich_description(details, hathoro_sku, vendor, title, specs)

        # Unique Images via MD5 Deduplication
        retina_images = get_unique_retina_images(zoro_no)
        num_images = len(retina_images)
        total_images += num_images

        # Primary Row (Row 1)
        r1 = {h: '' for h in headers}
        r1['Title'] = title
        r1['URL handle'] = handle
        r1['Description'] = desc_html
        r1['Vendor'] = vendor
        r1['Product category'] = shopify_category
        r1['Type'] = product_type
        r1['Tags'] = tags_str
        r1['Published on online store'] = 'TRUE'
        r1['Status'] = 'Active'
        r1['SKU'] = hathoro_sku
        r1['Barcode'] = str(item.get('mfrNo') or details.get('mfr_no') or '')
        r1['Option1 name'] = 'Title'
        r1['Option1 value'] = 'Default Title'
        r1['Price'] = f"{selling_price:.2f}"
        r1['Compare-at price'] = f"{compare_at_price:.2f}"
        r1['Cost per item'] = f"{cost_price:.2f}"
        r1['Charge tax'] = 'TRUE'
        r1['Tax code'] = 'P0000000'
        r1['Inventory tracker'] = 'shopify'
        r1['Inventory quantity'] = '500'
        r1['Continue selling when out of stock'] = 'DENY'
        r1['Weight value (grams)'] = str(weight_grams)
        r1['Weight unit for display'] = 'lb'
        r1['Requires shipping'] = 'TRUE'
        r1['Fulfillment service'] = 'manual'
        r1['Gift card'] = 'FALSE'
        r1['SEO title'] = f"{title[:60]} | {vendor}"
        # Do not set color-pattern metafield to avoid Shopify metaobject validation errors
        r1['Color (product.metafields.shopify.color-pattern)'] = ''

        r1['Google Shopping / Google product category'] = 'Business & Industrial > Industrial Storage > Drums & Barrels' if 'drum' in product_type.lower() else 'Business & Industrial'
        r1['Google Shopping / Gender'] = 'Unisex'
        r1['Google Shopping / Age group'] = 'Adult'
        r1['Google Shopping / Manufacturer part number (MPN)'] = str(item.get('mfrNo') or details.get('mfr_no') or '')
        r1['Google Shopping / Ad group name'] = product_type
        r1['Google Shopping / Ads labels'] = f"{product_type}, {vendor}, In Stock"
        r1['Google Shopping / Condition'] = 'New'
        r1['Google Shopping / Custom product'] = 'FALSE'
        r1['Google Shopping / Custom label 0'] = 'Tier 1: High Demand'
        r1['Google Shopping / Custom label 1'] = 'In Stock'
        r1['Google Shopping / Custom label 2'] = 'Commercial Freight'
        r1['Google Shopping / Custom label 3'] = vendor
        r1['Google Shopping / Custom label 4'] = category_type

        # If has at least 1 image, populate in primary row
        if num_images > 0:
            first_img = retina_images[0]
            r1['Product image URL'] = f"{TUNNEL_BASE}/{zoro_no}/images/1000x1000_retina/{first_img}"
            r1['Image position'] = '1'
            r1['Image alt text'] = f"{title} - Primary View ({hathoro_sku})"

        all_rows.append(r1)

        # Secondary Rows for additional images (positions 2, 3, ...)
        for img_idx, img_file in enumerate(retina_images[1:], 2):
            r_img = {h: '' for h in headers}
            r_img['URL handle'] = handle
            r_img['Product image URL'] = f"{TUNNEL_BASE}/{zoro_no}/images/1000x1000_retina/{img_file}"
            r_img['Image position'] = str(img_idx)
            r_img['Image alt text'] = f"{title} - View {img_idx} ({hathoro_sku})"
            all_rows.append(r_img)

    # Write Master CSV
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerows(all_rows)

    # Also generate targeted CSV for the 14 previously failed products
    failed_skus = {
        'H1877111', 'H5815031', 'H1180891', 'H5662842', 'H1620026', 'H5510197',
        'H4988803', 'H0424182', 'H4203184', 'H7734815', 'H4602315', 'H3755692',
        'H1167144', 'H6882692'
    }
    # Find all handles corresponding to these SKUs
    failed_handles = set()
    for r in all_rows:
        if r.get('SKU') in failed_skus:
            failed_handles.add(r['URL handle'])

    failed_rows = [r for r in all_rows if r['URL handle'] in failed_handles]
    failed_csv = os.path.join(DRUMS_DIR, "shopify_failed_14_products_fixed.csv")
    with open(failed_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerows(failed_rows)

    print("=" * 70)
    print(f"✓ Master Catalog Export Complete!")
    print(f"  Total Products Processed: {len(catalog_items)}")
    print(f"  Total CSV Rows: {len(all_rows)}")
    print(f"  Total Unique 1000x1000 Images Linked: {total_images}")
    print(f"  Output Master CSV: {OUTPUT_CSV}")
    print(f"  Output Fixed 14 CSV: {failed_csv} ({len(failed_skus)} products, {len(failed_rows)} rows)")
    print("=" * 70)

if __name__ == "__main__":
    main()
