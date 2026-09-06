#!/usr/bin/env python3
"""
extract_product_media.py

Automated extractor for Amazon & Zoro listings:
- Scrapes Amazon product page via Selenium with NYC zip code 10001.
- Scrapes Zoro product page via HTTP request using custom Rajar user agent.
- Extracts all Amazon high-res images and videos and downloads them locally.
- Creates a dedicated folder per listing containing:
    1. {ASIN}_details.txt (Comprehensive text profile of Amazon + Zoro data)
    2. {ASIN}_data.json (Structured raw JSON)
    3. images/ (All high-resolution gallery images)
    4. videos/ (All product videos if present)
"""

import os
import sys
import re
import json
import time
import ssl
import codecs
import urllib.request
import sqlite3
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Directories
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.abspath(os.path.join(CURRENT_DIR, "..", "Automations", "TSV Files", "combined_reports.db"))

ZORO_USER_AGENT = (
    "Mozilla/5.0 (RajarEnterpriseLLC/2026) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.7727.138 Safari/537.36"
)

AMAZON_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)


def get_zoro_sku_from_db(asin, db_path=DB_PATH):
    """Finds the matched Zoro SKU for an ASIN from the matched results or combined_reports.db."""
    csv_path = os.path.join(CURRENT_DIR, "Valvoline_Zoro_Matched_Results.csv")
    if os.path.exists(csv_path):
        import pandas as pd
        df = pd.read_csv(csv_path)
        match = df[df['ASIN'] == asin]
        if not match.empty:
            z_no = match.iloc[0].get('Zoro Item #')
            if z_no and str(z_no).strip():
                return str(z_no).strip()

    # Fallback to DB
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        # Default lookup for Valvoline
        cur.execute("SELECT zoro_no FROM zoro_data WHERE zoro_brand_name='VALVOLINE' LIMIT 1;")
        res = cur.fetchone()
        conn.close()
        if res:
            return res[0]
    return None


def fetch_zoro_details(zoro_no):
    """Fetches and parses live Zoro product page using custom profile/user-agent."""
    print(f"\n[Zoro] Fetching data for Zoro Item #{zoro_no}...")
    url = f"https://www.zoro.com/i/{zoro_no}/"
    headers = {
        'User-Agent': ZORO_USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, headers=headers)
    zoro_data = {
        'zoro_no': zoro_no,
        'url': url,
        'title': '',
        'brand': '',
        'mpn': '',
        'upc': '',
        'price': None,
        'availability': '',
        'uom': 'Each',
        'short_desc': '',
        'long_desc': '',
        'specifications': {},
        'images': [],
        'category_path': '',
        'lead_time': '',
        'shipping_restrictions': []
    }

    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
            m = re.search(r'window\.PINIA_INITIAL_STATE\s*=\s*\"(.*?)\";', html, re.DOTALL)
            if m:
                raw_str = m.group(1)
                decoded = codecs.decode(raw_str, "unicode_escape")
                data = json.loads(decoded)
                cache = data.get("productStore", {}).get("productDetailsCache", {})
                prod = cache.get(zoro_no) or (list(cache.values())[0] if cache else None)

                if prod:
                    zoro_data['title'] = prod.get('name') or prod.get('title') or ''
                    zoro_data['brand'] = (prod.get('brand') or '').strip()
                    zoro_data['mpn'] = prod.get('mfrPartNo') or ''
                    zoro_data['upc'] = prod.get('upc') or ''
                    zoro_data['price'] = prod.get('price')
                    zoro_data['availability'] = prod.get('availabilityStatus') or ''
                    zoro_data['uom'] = prod.get('unitOfMeasure') or 'Each'
                    zoro_data['short_desc'] = prod.get('shortDescription') or ''
                    zoro_data['long_desc'] = prod.get('longDescription') or ''
                    zoro_data['lead_time'] = prod.get('leadTime') or ''
                    zoro_data['shipping_restrictions'] = prod.get('restrictedStates') or []

                    # Specifications / Attributes
                    specs = {}
                    for a in prod.get('attributes') or []:
                        name = (a.get('name') or '').strip()
                        val = a.get('value')
                        if name and val not in (None, ""):
                            specs[name] = str(val).strip()
                    zoro_data['specifications'] = specs

                    # Category hierarchy
                    cat_names = []
                    for c in prod.get('primaryCategoryPaths') or []:
                        if isinstance(c, dict) and c.get('2'):
                            cat_names.append(c.get('2'))
                    zoro_data['category_path'] = ' > '.join(cat_names)

                    # Media / Images
                    for m_item in prod.get('media') or []:
                        name = m_item.get('name')
                        if name and not name.lower().endswith('.pdf'):
                            img_url = f"https://www.zoro.com/static/cms/product/full/{name}"
                            if img_url not in zoro_data['images']:
                                zoro_data['images'].append(img_url)

                    print(f"[Zoro] Success: '{zoro_data['title']}' | Specs: {len(specs)} | Images: {len(zoro_data['images'])}")
    except Exception as e:
        print(f"[Zoro] Live fetch error: {e}. Falling back to combined_reports.db...")
        # Fallback to database
        if os.path.exists(DB_PATH):
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("""
                SELECT manufacturer_part_number, upc_code, zoro_brand_name,
                       short_description, long_description, sales_price,
                       availabilityStatus, uom
                FROM zoro_data WHERE zoro_no = ?;
            """, (zoro_no,))
            r = cur.fetchone()
            conn.close()
            if r:
                zoro_data['mpn'] = r[0] or ''
                zoro_data['upc'] = r[1] or ''
                zoro_data['brand'] = r[2] or ''
                zoro_data['title'] = r[3] or ''
                zoro_data['short_desc'] = r[3] or ''
                zoro_data['long_desc'] = r[4] or ''
                zoro_data['price'] = r[5]
                zoro_data['availability'] = r[6] or ''
                zoro_data['uom'] = r[7] or 'Each'

    return zoro_data


def set_amazon_zip_code(driver, zip_code="10001"):
    """Sets Amazon delivery postal code to 10001 via modal."""
    print(f"[Amazon] Setting delivery location to ZIP: {zip_code}...")
    try:
        nav_slot = WebDriverWait(driver, 6).until(
            EC.element_to_be_clickable((By.ID, 'nav-global-location-slot'))
        )
        nav_slot.click()
        time.sleep(2)

        zip_input = WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located((By.ID, 'GLUXZipUpdateInput'))
        )
        zip_input.clear()
        zip_input.send_keys(zip_code)

        apply_btn = driver.find_element(By.CSS_SELECTOR, '#GLUXZipUpdate input, [aria-labelledby="GLUXZipUpdate-announce"]')
        apply_btn.click()
        time.sleep(2)

        # Confirm / Done button
        try:
            done_btn = driver.find_element(By.CSS_SELECTOR, 'button[name="glowDoneButton"], #GLUXConfirmClose, input[name="glowDoneButton"]')
            done_btn.click()
        except Exception:
            pass

        time.sleep(2)
        driver.refresh()
        time.sleep(3)

        # Verify location
        loc = driver.find_element(By.ID, 'glow-ingress-line2').text.strip()
        print(f"[Amazon] Delivery location confirmed: {loc}")
    except Exception as e:
        print(f"[Amazon] Zip code update notice: {e} (continuing with page scraping)")


def fetch_amazon_details(driver, asin):
    """Scrapes Amazon product page details, bullet points, specs, images, and videos."""
    print(f"\n[Amazon] Scraping product page: https://www.amazon.com/dp/{asin}...")
    driver.get(f"https://www.amazon.com/dp/{asin}")
    time.sleep(3)

    set_amazon_zip_code(driver, "10001")

    data = {
        'asin': asin,
        'url': f"https://www.amazon.com/dp/{asin}",
        'title': '',
        'brand': '',
        'price': '',
        'rating': '',
        'review_count': '',
        'availability': '',
        'buy_box_seller': '',
        'bullets': [],
        'description': '',
        'aplus_text': '',
        'specifications': {},
        'images': [],
        'videos': []
    }

    # Title
    try:
        data['title'] = driver.find_element(By.ID, 'productTitle').text.strip()
    except Exception:
        pass

    # Brand
    try:
        data['brand'] = driver.find_element(By.ID, 'bylineInfo').text.replace('Brand:', '').replace('Visit the', '').replace('Store', '').strip()
    except Exception:
        pass

    # Price
    try:
        p_elem = driver.find_elements(By.CSS_SELECTOR, '.apexPriceToPay .a-offscreen, .a-price .a-offscreen, #priceblock_ourprice, #priceblock_dealprice')
        for pe in p_elem:
            ptxt = pe.get_attribute('innerText').strip()
            if ptxt and '$' in ptxt:
                data['price'] = ptxt
                break
    except Exception:
        pass

    # Rating & Reviews
    try:
        data['rating'] = driver.find_element(By.CSS_SELECTOR, '#acrPopover span.a-icon-alt, span[data-hook="rating-out-of-text"]').text.strip()
    except Exception:
        pass

    try:
        data['review_count'] = driver.find_element(By.ID, 'acrCustomerReviewText').text.strip()
    except Exception:
        pass

    # Availability
    try:
        data['availability'] = driver.find_element(By.ID, 'availability').text.strip()
    except Exception:
        pass

    # Buy Box Seller
    try:
        seller = driver.find_element(By.ID, 'sellerProfileTriggerId').text.strip()
        data['buy_box_seller'] = seller
    except Exception:
        try:
            mbs = driver.find_element(By.CSS_SELECTOR, '#merchant-info, div[tabular-attribute-name="Sold by"]').text.strip()
            data['buy_box_seller'] = mbs
        except Exception:
            pass

    # Bullets
    try:
        bullets = driver.find_elements(By.CSS_SELECTOR, '#feature-bullets ul li span.a-list-item')
        for b in bullets:
            btxt = b.text.strip()
            if btxt and not btxt.lower().startswith('make sure this fits'):
                data['bullets'].append(btxt)
    except Exception:
        pass

    # Product Description
    try:
        desc = driver.find_element(By.ID, 'productDescription').text.strip()
        data['description'] = desc
    except Exception:
        pass

    # A+ Content
    try:
        aplus_elems = driver.find_elements(By.CSS_SELECTOR, '#aplus p, #aplus h3, #aplus h4, #aplus .apm-hovermodule-slideshow')
        aplus_texts = [e.text.strip() for e in aplus_elems if e.text.strip()]
        data['aplus_text'] = '\n'.join(aplus_texts)
    except Exception:
        pass

    # Technical Specifications
    specs = {}
    try:
        spec_rows = driver.find_elements(By.CSS_SELECTOR, '#productDetails_techSpec_section_1 tr, #prodDetails table tr')
        for r in spec_rows:
            try:
                th = r.find_element(By.TAG_NAME, 'th').text.strip()
                td = r.find_element(By.TAG_NAME, 'td').text.strip()
                if th and td:
                    specs[th] = td
            except Exception:
                pass
    except Exception:
        pass

    if not specs:
        try:
            detail_bullets = driver.find_elements(By.CSS_SELECTOR, '#detailBullets_feature_div ul li')
            for db in detail_bullets:
                txt = db.text.strip()
                if ':' in txt:
                    parts = txt.split(':', 1)
                    k = parts[0].replace('\n', '').replace('‎', '').strip()
                    v = parts[1].replace('\n', '').replace('‎', '').strip()
                    if k and v:
                        specs[k] = v
        except Exception:
            pass

    data['specifications'] = specs

    # --- EXTRACT HIGH-RES IMAGES ---
    img_urls = []
    # 1. Extract from JavaScript colorImages initial data
    try:
        js_get_images = """
            var urls = [];
            var scripts = document.querySelectorAll('script');
            for (var i = 0; i < scripts.length; i++) {
                var t = scripts[i].innerText;
                if (t.indexOf('colorImages') !== -1 && t.indexOf('initial') !== -1) {
                    var idx = t.indexOf("'initial': A.$.parseJSON('");
                    if (idx === -1) idx = t.indexOf('"initial": A.$.parseJSON("');
                    if (idx !== -1) {
                        var start = t.indexOf("'", idx + 20) + 1;
                        var end = t.indexOf("')", start);
                        try {
                            var raw = t.substring(start, end);
                            var items = JSON.parse(raw);
                            items.forEach(function(item) {
                                var u = item.hiRes || item.large;
                                if (u && urls.indexOf(u) === -1 && u.indexOf('play-icon') === -1) {
                                    urls.push(u);
                                }
                            });
                        } catch(e) {}
                    }
                }
            }
            // Also check landingImage data-old-hires
            var mainEl = document.getElementById('landingImage');
            if (mainEl) {
                var oldHiRes = mainEl.getAttribute('data-old-hires');
                if (oldHiRes && urls.indexOf(oldHiRes) === -1) {
                    urls.unshift(oldHiRes);
                }
            }
            return urls;
        """
        js_imgs = driver.execute_script(js_get_images)
        if js_imgs:
            for u in js_imgs:
                if 'play-icon' not in u and u not in img_urls:
                    img_urls.append(u)
    except Exception as e:
        print(f"[Amazon] JS image extraction notice: {e}")

    # 2. Regex search in page source for hiRes images
    if not img_urls:
        try:
            html = driver.page_source
            hires_matches = re.findall(r'\"hiRes\"\s*:\s*\"([^\"]+)\"', html)
            for u in hires_matches:
                if 'play-icon' not in u and u not in img_urls:
                    img_urls.append(u)
        except Exception:
            pass

    # 3. Fallback: altImages thumbnails
    if not img_urls:
        try:
            thumb_imgs = driver.find_elements(By.CSS_SELECTOR, '#altImages ul li img')
            for img in thumb_imgs:
                src = img.get_attribute('src') or ''
                if 'media-amazon.com' in src and 'play-icon' not in src:
                    full_res = re.sub(r'\._[A-Z0-9_,]+_\.', '.', src)
                    if full_res not in img_urls:
                        img_urls.append(full_res)
        except Exception:
            pass

    data['images'] = img_urls
    print(f"[Amazon] Extracted {len(img_urls)} true high-resolution images.")

    # --- EXTRACT VIDEOS ---
    video_urls = []
    # Trigger video load if thumbnail exists
    try:
        v_thumbs = driver.find_elements(By.CSS_SELECTOR, 'li.video, .video-thumbnail, [aria-label*="video" i]')
        for vt in v_thumbs:
            try:
                vt.click()
                time.sleep(1)
            except Exception:
                pass
        time.sleep(2)
    except Exception:
        pass

    # Search page source for direct mp4 / m3u8 URLs
    try:
        html_content = driver.page_source
        raw_matches = re.findall(r'https://[^"\'\s\\]+?\.(?:mp4|m3u8)', html_content)
        for u in raw_matches:
            clean_u = u.replace(r'\u0026', '&').replace('\\', '')
            if 'media-amazon.com' in clean_u and clean_u not in video_urls:
                video_urls.append(clean_u)
    except Exception as e:
        print(f"[Amazon] Video regex notice: {e}")

    data['videos'] = video_urls
    print(f"[Amazon] Extracted {len(video_urls)} product video links.")

    return data


def download_media_file(url, target_path):
    """Downloads a media file (image/video) with progress reporting."""
    try:
        headers = {'User-Agent': AMAZON_USER_AGENT}
        resp = requests.get(url, headers=headers, stream=True, timeout=25)
        if resp.status_code == 200:
            with open(target_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
            file_size_kb = os.path.getsize(target_path) / 1024.0
            print(f"  ✓ Saved: {os.path.basename(target_path)} ({file_size_kb:.1f} KB)")
            return True
        else:
            print(f"  ✗ Failed to download {url} (HTTP {resp.status_code})")
    except Exception as e:
        print(f"  ✗ Error downloading {url}: {e}")
    return False


def build_details_text(asin, amz, zoro):
    """Creates a comprehensive, beautifully structured text document containing all extracted data."""
    lines = []
    lines.append("=" * 85)
    lines.append(f"HATHORO PRODUCT PROFILE & CATALOG EXTRACTION: ASIN {asin}")
    lines.append("=" * 85)
    lines.append(f"Extraction Date: {time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"Amazon URL:      {amz.get('url')}")
    lines.append(f"Zoro URL:        {zoro.get('url')}")
    lines.append(f"Delivery Zip:    10001 (New York, NY)")
    lines.append("=" * 85)
    lines.append("")

    # --- 1. CORE PRODUCT IDENTIFIERS ---
    lines.append("1. CORE PRODUCT IDENTIFIERS & CROSS-REFERENCE")
    lines.append("-" * 55)
    lines.append(f"ASIN:                {asin}")
    lines.append(f"Amazon Title:        {amz.get('title')}")
    lines.append(f"Zoro Title:          {zoro.get('title')}")
    lines.append(f"Brand:               {amz.get('brand') or zoro.get('brand')}")
    lines.append(f"Manufacturer Part #: {zoro.get('mpn') or amz.get('specifications', {}).get('Item model number', '')}")
    lines.append(f"Zoro Item #:         {zoro.get('zoro_no')}")
    lines.append(f"UPC:                 {zoro.get('upc')}")
    lines.append(f"Category Path:       {zoro.get('category_path')}")
    lines.append("")

    # --- 2. PRICING & INVENTORY ---
    lines.append("2. PRICING & INVENTORY AVAILABILITY")
    lines.append("-" * 55)
    lines.append(f"Amazon Buy Box Price:     {amz.get('price') or 'See page'}")
    lines.append(f"Amazon Buy Box Seller:    {amz.get('buy_box_seller')}")
    lines.append(f"Amazon Stock Status:      {amz.get('availability')}")
    lines.append(f"Amazon Customer Rating:   {amz.get('rating')} ({amz.get('review_count')})")
    lines.append(f"Zoro Supplier Price:      ${zoro.get('price'):.2f}" if zoro.get('price') is not None else "Zoro Supplier Price:      N/A")
    lines.append(f"Zoro Stock Availability:  {zoro.get('availability')}")
    lines.append(f"Zoro Unit of Measure:     {zoro.get('uom')}")
    lines.append(f"Zoro Lead Time:           {zoro.get('lead_time') or 'Standard Ground'}")
    if zoro.get('shipping_restrictions'):
        lines.append(f"Restricted States:        {', '.join(zoro.get('shipping_restrictions'))}")
    lines.append("")

    # --- 3. AMAZON KEY BULLET POINTS ---
    lines.append("3. AMAZON KEY FEATURE BULLETS")
    lines.append("-" * 55)
    if amz.get('bullets'):
        for idx, bullet in enumerate(amz['bullets'], 1):
            lines.append(f"[{idx}] {bullet}")
    else:
        lines.append("No bullet points extracted.")
    lines.append("")

    # --- 4. ZORO TECHNICAL SPECIFICATIONS ---
    lines.append("4. ZORO STRUCTURED SPECIFICATIONS")
    lines.append("-" * 55)
    if zoro.get('specifications'):
        for k, v in sorted(zoro['specifications'].items()):
            lines.append(f"• {k:<32}: {v}")
    else:
        lines.append("No structured specifications extracted from Zoro.")
    lines.append("")

    # --- 5. AMAZON TECHNICAL DETAILS ---
    lines.append("5. AMAZON TECHNICAL SPECIFICATIONS")
    lines.append("-" * 55)
    if amz.get('specifications'):
        for k, v in sorted(amz['specifications'].items()):
            lines.append(f"• {k:<32}: {v}")
    else:
        lines.append("No technical specifications extracted from Amazon.")
    lines.append("")

    # --- 6. PRODUCT DESCRIPTIONS ---
    lines.append("6. DETAILED DESCRIPTIONS & COPY")
    lines.append("-" * 55)
    lines.append("--- [Zoro Short Description] ---")
    lines.append(zoro.get('short_desc') or "N/A")
    lines.append("")
    lines.append("--- [Zoro Long Description] ---")
    lines.append(zoro.get('long_desc') or "N/A")
    lines.append("")
    lines.append("--- [Amazon Description] ---")
    lines.append(amz.get('description') or "N/A")
    lines.append("")
    if amz.get('aplus_text'):
        lines.append("--- [Amazon A+ Enhanced Brand Content] ---")
        lines.append(amz.get('aplus_text'))
        lines.append("")

    # --- 7. EXTRACTED MEDIA ASSETS ---
    lines.append("7. EXTRACTED MEDIA ASSETS")
    lines.append("-" * 55)
    lines.append(f"Total Amazon High-Res Images Extracted: {len(amz.get('images', []))}")
    for idx, u in enumerate(amz.get('images', []), 1):
        lines.append(f"  Image {idx:02d}: {u}")
    lines.append("")
    lines.append(f"Total Amazon Videos Extracted: {len(amz.get('videos', []))}")
    for idx, u in enumerate(amz.get('videos', []), 1):
        lines.append(f"  Video {idx:02d}: {u}")
    lines.append("")
    lines.append(f"Total Zoro Images Extracted: {len(zoro.get('images', []))}")
    for idx, u in enumerate(zoro.get('images', []), 1):
        lines.append(f"  Zoro Image {idx:02d}: {u}")
    lines.append("")
    lines.append("=" * 85)
    lines.append("END OF DATA EXTRACT")
    lines.append("=" * 85)

    return '\n'.join(lines)


def process_listing(asin="B008QDV2JG", zoro_no=None):
    """Orchestrates complete extraction and asset download for an ASIN."""
    print("=" * 70)
    print(f"STARTING EXTRACTION PIPELINE FOR: {asin}")
    print("=" * 70)

    # 1. Resolve Zoro SKU
    if not zoro_no:
        zoro_no = get_zoro_sku_from_db(asin)
    if not zoro_no:
        print(f"Warning: No Zoro SKU found for ASIN {asin}. Proceeding with Amazon-only.")

    # 2. Setup Target Folder
    product_dir = os.path.join(CURRENT_DIR, asin)
    images_dir = os.path.join(product_dir, "images")
    videos_dir = os.path.join(product_dir, "videos")
    os.makedirs(images_dir, exist_ok=True)
    os.makedirs(videos_dir, exist_ok=True)
    print(f"Output directory created: {product_dir}")

    # 3. Fetch Zoro Details
    zoro_data = {}
    if zoro_no:
        zoro_data = fetch_zoro_details(zoro_no)

    # 4. Launch Headless Chrome for Amazon
    print("\n[Selenium] Launching Chrome driver...")
    chrome_opts = Options()
    chrome_opts.add_argument('--headless=new')
    chrome_opts.add_argument('--no-sandbox')
    chrome_opts.add_argument('--disable-dev-shm-usage')
    chrome_opts.add_argument('--window-size=1920,1080')
    chrome_opts.add_argument(f'user-agent={AMAZON_USER_AGENT}')

    driver = webdriver.Chrome(options=chrome_opts)
    try:
        amz_data = fetch_amazon_details(driver, asin)
    finally:
        driver.quit()
        print("[Selenium] Chrome driver closed.")

    # 5. Download and Format Images (Canva-Grade 500x500, 1000x1000 Retina, and 1500px Master)
    high_res_dir = os.path.join(images_dir, "high_res")
    px500_dir = os.path.join(images_dir, "500x500")
    retina_dir = os.path.join(images_dir, "1000x1000_retina")
    master_sq_dir = os.path.join(images_dir, "1500x1500_square")
    os.makedirs(high_res_dir, exist_ok=True)
    os.makedirs(px500_dir, exist_ok=True)
    os.makedirs(retina_dir, exist_ok=True)
    os.makedirs(master_sq_dir, exist_ok=True)

    def canva_fit(im, target_size=500, padding_pct=0.02):
        import numpy as np
        from PIL import ImageFilter
        arr = np.array(im.convert('RGB'))
        mask = (arr[:, :, 0] < 250) | (arr[:, :, 1] < 250) | (arr[:, :, 2] < 250)
        rows = np.where(mask.any(axis=1))[0]
        cols = np.where(mask.any(axis=0))[0]
        if len(rows) > 0 and len(cols) > 0:
            r_min = max(0, rows[0] - 8)
            r_max = min(im.height, rows[-1] + 8)
            c_min = max(0, cols[0] - 8)
            c_max = min(im.width, cols[-1] + 8)
            cropped = im.crop((c_min, r_min, c_max, r_max))
        else:
            cropped = im

        w, h = cropped.size
        max_dim = int(target_size * (1 - padding_pct * 2))
        scale = min(max_dim / w, max_dim / h)
        new_w = int(w * scale)
        new_h = int(h * scale)
        resized = cropped.resize((new_w, new_h), Image.Resampling.LANCZOS)
        if scale < 1.0:
            resized = resized.filter(ImageFilter.UnsharpMask(radius=1.0, percent=60, threshold=1))
        canvas = Image.new('RGB', (target_size, target_size), (255, 255, 255))
        offset_x = (target_size - new_w) // 2
        offset_y = (target_size - new_h) // 2
        canvas.paste(resized, (offset_x, offset_y))
        return canvas

    print(f"\n[Download] Processing {len(amz_data['images'])} Amazon gallery images with Canva-grade pipeline...")
    headers = {'User-Agent': AMAZON_USER_AGENT}
    for idx, img_url in enumerate(amz_data['images'], 1):
        # Try raw base URL first (up to 2000px without crop), then fallback to _AC_SL1500_
        raw_url = re.sub(r'\._[A-Z0-9_,]+_\.', '.', img_url)
        master_url = re.sub(r'\._[A-Z0-9_,]+_\.', '._AC_SL1500_.', img_url)
        try:
            resp = requests.get(raw_url, headers=headers, timeout=20)
            if resp.status_code != 200:
                resp = requests.get(master_url, headers=headers, timeout=20)
            if resp.status_code == 200:
                from PIL import Image
                from io import BytesIO
                im = Image.open(BytesIO(resp.content))
                if im.mode != 'RGB':
                    im = im.convert('RGB')

                # 1. High-Res master
                im.save(os.path.join(high_res_dir, f"image_{idx:02d}_master.jpg"), 'JPEG', quality=98)

                # 2. 500x500 (Canva 1x)
                sq_500 = canva_fit(im, 500)
                sq_500.save(os.path.join(px500_dir, f"image_{idx:02d}_500x500.jpg"), 'JPEG', quality=98, subsampling=0)
                sq_500.save(os.path.join(images_dir, f"image_{idx:02d}.jpg"), 'JPEG', quality=98, subsampling=0)

                # 3. 1000x1000 (Canva 2x Retina)
                sq_1000 = canva_fit(im, 1000)
                sq_1000.save(os.path.join(retina_dir, f"image_{idx:02d}_1000x1000.jpg"), 'JPEG', quality=98, subsampling=0)

                # 4. 1500x1500 (Master Zoom Square)
                sq_1500 = canva_fit(im, 1500)
                sq_1500.save(os.path.join(master_sq_dir, f"image_{idx:02d}_1500x1500.jpg"), 'JPEG', quality=98, subsampling=0)

                print(f"  ✓ Image {idx:02d}: Master {im.size} -> 500x500, 1000x1000 Retina, & 1500x1500 saved")
        except Exception as e:
            print(f"  ✗ Failed processing image {idx:02d}: {e}")

    # Zoro Image
    if zoro_data.get('images'):
        for idx, img_url in enumerate(zoro_data['images'], 1):
            try:
                resp = requests.get(img_url, headers={'User-Agent': ZORO_USER_AGENT}, timeout=20)
                if resp.status_code == 200:
                    from PIL import Image
                    from io import BytesIO
                    im = Image.open(BytesIO(resp.content))
                    if im.mode != 'RGB':
                        im = im.convert('RGB')
                    im.save(os.path.join(high_res_dir, f"zoro_image_{idx:02d}_master.jpg"), 'JPEG', quality=98)
                    sq_500 = canva_fit(im, 500)
                    sq_500.save(os.path.join(px500_dir, f"zoro_image_{idx:02d}_500x500.jpg"), 'JPEG', quality=98, subsampling=0)
                    sq_500.save(os.path.join(images_dir, f"zoro_image_{idx:02d}.jpg"), 'JPEG', quality=98, subsampling=0)
                    sq_1000 = canva_fit(im, 1000)
                    sq_1000.save(os.path.join(retina_dir, f"zoro_image_{idx:02d}_1000x1000.jpg"), 'JPEG', quality=98, subsampling=0)
                    sq_1500 = canva_fit(im, 1500)
                    sq_1500.save(os.path.join(master_sq_dir, f"zoro_image_{idx:02d}_1500x1500.jpg"), 'JPEG', quality=98, subsampling=0)
                    print(f"  ✓ Zoro Image {idx:02d}: Master {im.size} -> 500x500, 1000x1000, & 1500x1500 saved")
            except Exception as e:
                print(f"  ✗ Zoro image download notice: {e}")

    # 6. Download and Remux Amazon Videos into 100% Playable MP4
    import subprocess
    import imageio_ffmpeg
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    ctx = ssl._create_unverified_context()

    valid_vid_urls = [
        u for u in amz_data.get('videos', [])
        if u.startswith('https://m.media-amazon.com/images/S/vse-vms-transcoding-artifact-us-east-1-prod/') and u.endswith('.m3u8')
    ]
    guid_map = {}
    for u in valid_vid_urls:
        parts = u.split('/')
        if len(parts) >= 7:
            guid = parts[6]
            guid_map.setdefault(guid, []).append(u)

    print(f"\n[Videos] Processing {len(guid_map)} unique Amazon videos into standard MP4...")
    for idx, (guid, u_list) in enumerate(guid_map.items(), 1):
        base_url = f"https://m.media-amazon.com/images/S/vse-vms-transcoding-artifact-us-east-1-prod/{guid}/"
        playlist_url = u_list[0]
        req = urllib.request.Request(playlist_url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=6, context=ctx) as r:
                lines = r.read().decode().splitlines()

            ts_lines = [l.strip() for l in lines if l.strip().endswith('.ts')]
            if not ts_lines:
                sub_playlists = [l.strip() for l in lines if l.strip().endswith('.m3u8')]
                if sub_playlists:
                    sub_name = sub_playlists[0]
                    for sp in sub_playlists:
                        if '1080' in sp:
                            sub_name = sp
                            break
                        elif '720' in sp:
                            sub_name = sp
                    sub_url = base_url + sub_name
                    req2 = urllib.request.Request(sub_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req2, timeout=6, context=ctx) as r2:
                        sub_lines = r2.read().decode().splitlines()
                    ts_lines = [l.strip() for l in sub_lines if l.strip().endswith('.ts')]

            if ts_lines:
                temp_ts = os.path.join(videos_dir, f"temp_{idx:02d}.ts")
                final_mp4 = os.path.join(videos_dir, f"product_video_{idx:02d}.mp4")

                with open(temp_ts, 'wb') as out_f:
                    for ts in ts_lines:
                        ts_url = base_url + ts
                        req_ts = urllib.request.Request(ts_url, headers={'User-Agent': 'Mozilla/5.0'})
                        with urllib.request.urlopen(req_ts, timeout=10, context=ctx) as r_ts:
                            out_f.write(r_ts.read())

                cmd = [
                    ffmpeg_exe, '-y',
                    '-i', temp_ts,
                    '-c', 'copy',
                    '-movflags', '+faststart',
                    final_mp4
                ]
                subprocess.run(cmd, capture_output=True, text=True)
                if os.path.exists(temp_ts):
                    os.remove(temp_ts)

                if os.path.exists(final_mp4):
                    size_mb = os.path.getsize(final_mp4) / (1024.0 * 1024.0)
                    print(f"  ✓ Video {idx:02d}: Saved standard MP4 ({size_mb:.1f} MB) -> {os.path.basename(final_mp4)}")
        except Exception as e:
            print(f"  ✗ Error processing video {guid}: {e}")

    # 7. Write Structured JSON Data
    json_path = os.path.join(product_dir, f"{asin}_data.json")
    combined_json = {
        'asin': asin,
        'zoro_no': zoro_no,
        'extracted_at': time.strftime("%Y-%m-%d %H:%M:%S UTC"),
        'amazon': amz_data,
        'zoro': zoro_data
    }
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(combined_json, f, indent=2, ensure_ascii=False)
    print(f"\n[Output] Structured data saved to: {json_path}")

    # 8. Write Comprehensive Text Details File
    details_txt = build_details_text(asin, amz_data, zoro_data)
    txt_path = os.path.join(product_dir, f"{asin}_details.txt")
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write(details_txt)
    print(f"[Output] Formatted listing text saved to: {txt_path}")

    print("\n" + "=" * 70)
    print(f"EXTRACTION COMPLETED FOR {asin}!")
    print(f"Folder: {product_dir}")
    print("=" * 70)


if __name__ == "__main__":
    target_asin = sys.argv[1] if len(sys.argv) > 1 else "B008QDV2JG"
    target_zoro = sys.argv[2] if len(sys.argv) > 2 else "G1769677"
    process_listing(target_asin, target_zoro)
