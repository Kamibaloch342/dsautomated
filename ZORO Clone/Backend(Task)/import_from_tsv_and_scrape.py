import sqlite3
import os
import sys
import json
import requests

# ── Add Backend(Task) to path for imports ──
sys.path.insert(0, os.path.dirname(__file__))

from app import app
from database import db
from models import Product


TSV_DB_PATH = "/Users/kamranbaloch342/Desktop/Dropshipping/Development_Unit/TSV Files/combined_reports.db"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
}

def get_tsv_data(zoro_code):
    """Fetch base product data from the SQLite TSV database."""
    conn = sqlite3.connect(TSV_DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    query = """
    SELECT zoro_no, short_description, zoro_brand_name, unit_price, 
           availabilityStatus, manufacturer_part_number, upc_code, 
           country_of_origin, zoro_image_name, long_description,
           lead_time, is_dropship, uom, freight_surcharge, 
           min_order_qty, is_ltl,
           ship_pack_height, ship_pack_length, ship_pack_width, ship_pack_weight,
           is_blind_ship
    FROM zoro_data 
    WHERE zoro_no = ?
    """
    cursor.execute(query, (zoro_code,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return dict(row)
    return None

import re
import codecs

def extract_specifications(zoro_code):
    """Scrape only the specifications from the Zoro product page."""
    url = f"https://www.zoro.com/i/{zoro_code}/"
    print(f"  Scraping specs from: {url}")
    
    response = requests.get(url, headers=HEADERS, timeout=15)
    response.encoding = 'utf-8' # Fix encoding issues with degree symbols
    if response.status_code != 200:
        print(f"  ❌ Failed to fetch page. Status: {response.status_code}")
        return {}
        
    html = response.text
    specifications = {}
    
    m = re.search(r'window\.PINIA_INITIAL_STATE\s*=\s*\"(.*?)\";', html, re.DOTALL)
    if m:
        raw_str = m.group(1)
        try:
            decoded_str = codecs.decode(raw_str, 'unicode_escape')
            data = json.loads(decoded_str)
            cache = data.get('productStore', {}).get('productDetailsCache', {})
            prod = cache.get(zoro_code, {})
            attrs = prod.get('attributes', [])
            specifications = {a['name']: a['value'] for a in attrs if 'name' in a and 'value' in a}
        except Exception as e:
            print(f"  [!] Failed to parse PINIA state: {e}")
            
    return specifications

def main():
    try:
        codes_file = os.path.join(os.path.dirname(__file__), 'zoro_codes.txt')
        if not os.path.exists(codes_file):
            print(f"❌ Could not find {codes_file}.")
            print("Please create this file and paste Zoro codes into it (one per line).")
            return
            
        with open(codes_file, 'r') as f:
            # Read lines, strip whitespace, skip empty lines
            target_codes = [line.strip() for line in f if line.strip()]
            
        if not target_codes:
            print(f"❌ {codes_file} is empty. Please add some Zoro codes.")
            return
            
        print(f"\n📦 Found {len(target_codes)} codes to process from zoro_codes.txt\n")

        with app.app_context():
            for i, code in enumerate(target_codes, 1):
                print(f"Processing {i}/{len(target_codes)}: {code}...")
                
                # 1. Get Base Data from TSV DB
                tsv_data = get_tsv_data(code)
                if not tsv_data:
                    print(f"  ❌ Product {code} not found in TSV database.")
                    continue
                    
                print(f"  ✅ Found in TSV: {tsv_data['short_description']}")
                
                # 2. Get Specifications via Scraper
                specs = extract_specifications(code)
                print(f"  ✅ Extracted {len(specs)} specifications")
            
                # 3. Save to PostgreSQL
                product = Product.query.filter_by(zoro_code=code).first()
                if not product:
                    product = Product(zoro_code=code)
                    db.session.add(product)
                    
                # Map TSV fields to PostgreSQL model
                product.title = tsv_data.get('short_description', '')
                product.brand = tsv_data.get('zoro_brand_name', '')
                
                # Handle possible string prices from TSV
                try:
                    product.unit_price = float(tsv_data.get('unit_price', 0))
                except (ValueError, TypeError):
                    product.unit_price = 0.0
                    
                product.availability = tsv_data.get('availabilityStatus', 'Unknown')
                product.mfr_number = tsv_data.get('manufacturer_part_number', '')
                product.upc = tsv_data.get('upc_code', '')
                product.country_of_origin = tsv_data.get('country_of_origin', '')
                product.image_url = tsv_data.get('zoro_image_name', '')
                product.product_url = f"https://www.zoro.com/i/{code}/"
                product.description = tsv_data.get('long_description', '')
                
                # New TSV fields
                product.lead_time = tsv_data.get('lead_time', '')
                product.is_dropship = str(tsv_data.get('is_dropship', '')).upper() == 'YES'
                product.uom = tsv_data.get('uom', '')
                
                try:
                    product.freight_surcharge = float(tsv_data.get('freight_surcharge', 0) or 0)
                except (ValueError, TypeError):
                    product.freight_surcharge = 0.0
                    
                try:
                    product.min_order_qty = int(tsv_data.get('min_order_qty', 1) or 1)
                except (ValueError, TypeError):
                    product.min_order_qty = 1
                    
                product.is_ltl = str(tsv_data.get('is_ltl', '')).upper() in ('YES', '1', 'TRUE')
                
                # Dimensions & Shipping
                try: product.ship_pack_height = float(tsv_data.get('ship_pack_height', 0) or 0)
                except (ValueError, TypeError): product.ship_pack_height = None
                try: product.ship_pack_length = float(tsv_data.get('ship_pack_length', 0) or 0)
                except (ValueError, TypeError): product.ship_pack_length = None
                try: product.ship_pack_width = float(tsv_data.get('ship_pack_width', 0) or 0)
                except (ValueError, TypeError): product.ship_pack_width = None
                try: product.ship_pack_weight = float(tsv_data.get('ship_pack_weight', 0) or 0)
                except (ValueError, TypeError): product.ship_pack_weight = None
                
                product.is_blind_ship = str(tsv_data.get('is_blind_ship', '')).upper() in ('YES', '1', 'TRUE')
                
                # The Scraped Specifications!
                product.specifications = specs
                
                db.session.add(product)
                db.session.commit()
                print(f"  💾 Saved {code} to PostgreSQL successfully!")
            
        print(f"\n🎉 All {len(target_codes)} products have been processed!")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
