#!/usr/bin/env python3
import urllib.request
import ssl
import json
import re
import codecs
import time
import os

ctx = ssl._create_unverified_context()
headers = {
    'User-Agent': 'Mozilla/5.0 (RajarEnterpriseLLC/2026) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.7727.138 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

def fetch_url(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
                return resp.read().decode('utf-8', errors='ignore')
        except Exception as e:
            if attempt == retries - 1:
                print(f"Failed to fetch {url}: {e}")
                return None
            time.sleep(1.5)

def get_reviews_batch(skus):
    if not skus:
        return {}
    sku_str = ','.join(skus)
    url = f'https://readservices-b2c.powerreviews.com/m/297763/l/en_US/product/{sku_str}/snippet?apikey=90d71773-2b19-45c6-a4ec-053f308ea2cd'
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                res = {}
                for r in data.get('results', []):
                    p_id = r.get('page_id')
                    rollup = r.get('rollup', {})
                    rc = rollup.get('rating_count', 0) or rollup.get('review_count', 0)
                    if rc >= 1:
                        res[p_id] = {
                            'rating': rollup.get('average_rating', 0),
                            'reviews': rc
                        }
                return res
        except Exception as e:
            time.sleep(1)
    return {}

BAD_KEYWORDS = [
    'printer', 'laser', 'toner', 'brake', 'cable', 'winch', 'fan', 'motor',
    'tool balancer', 'caulk', 'drain', 'switch', 'drill', 'bit barrel',
    'cleaner', 'degreaser', 'coolant', 'oil eater', 'grease', 'detergent',
    'lubricant', 'antifreeze', 'solvent', 'wash', 'fluid', 'sanitizer',
    'primer', 'paint', 'adhesive', 'cement', 'tape', 'hose', 'wire',
    'shredder', 'mower', 'saw', 'blade', 'sensor', 'gauge', 'relay',
    'socket', 'ratchet', 'wrench set', 'pliers', 'screwdriver', 'knife'
]

def is_valid_drum_product(title):
    t = title.lower()
    for b in BAD_KEYWORDS:
        if b in t:
            return False
    # Must explicitly mention drum or barrel
    return ('drum' in t or 'barrel' in t)

def is_container_drum(title):
    t = title.lower()
    container_indicators = [
        'open head', 'closed head', 'tight head', 'transport drum',
        'salvage drum', 'overpack drum', 'reconditioned', 'steel drum',
        'poly drum', 'fiber drum', 'gal drum', 'gallon drum', 'gal. drum',
        'gallon barrel', 'storage drum', 'wide neck drum', 'rain barrel'
    ]
    return any(ind in t for ind in container_indicators)

def run():
    seen_skus = set()
    all_candidates = []

    queries = [
        'Drums',
        'Transport+Drums',
        '55+gallon+drum',
        '55+gal+drum',
        '30+gallon+drum',
        'steel+drum',
        'poly+drum',
        'salvage+drum',
        'overpack+drum',
        'reconditioned+drum',
        'drum+barrel',
        '55+gal+barrel',
        'drum+dolly+55',
        'drum+spill+pallet',
        'drum+cradle+55',
        'drum+lifter+55'
    ]

    for q in queries:
        print(f"Scanning query: {q}...")
        for page in range(1, 12):
            url = f"https://www.zoro.com/search?q={q}&page={page}"
            html = fetch_url(url)
            if not html:
                break
            m = re.search(r'window\.INITIAL_STATE\s*=\s*\"(.*?)\";\s*\n', html)
            if not m:
                break
            try:
                data = json.loads(codecs.decode(m.group(1), 'unicode_escape'))
            except Exception:
                break
            results = data.get('search', {}).get('response', {}).get('detailedResults', [])
            if not results:
                break

            new_on_page = 0
            for it in results:
                p = it.get('product', {})
                zno = p.get('zoroNo')
                title = p.get('title') or ''
                price = p.get('price')
                if not zno or zno in seen_skus:
                    continue
                seen_skus.add(zno)

                if price is not None and 65 <= float(price) <= 185 and is_valid_drum_product(title):
                    all_candidates.append({
                        'zoroNo': zno,
                        'title': title,
                        'price': float(price),
                        'brand': p.get('brand') or 'Zoro Select',
                        'mfrNo': p.get('mfrNo') or '',
                        'is_container': is_container_drum(title),
                        'source': 'live_search'
                    })
                    new_on_page += 1

            if new_on_page == 0 and page > 2:
                break

    # Also check local DB
    db_path = '/Users/kamibaloch342/Downloads/Dropshipping/Development_Unit/Automations/TSV Files/combined_reports.db'
    if os.path.exists(db_path):
        import sqlite3
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        query = """
            SELECT zoro_no, manufacturer_part_number, zoro_brand_name, short_description, sales_price
            FROM zoro_data
            WHERE (short_description LIKE '%drum%' OR long_description LIKE '%drum%' OR short_description LIKE '%barrel%')
              AND sales_price BETWEEN 65 AND 185
        """
        for row in cur.execute(query).fetchall():
            zno = row[0]
            title = row[3] or ''
            price = row[4]
            if zno and zno not in seen_skus and price and is_valid_drum_product(title):
                seen_skus.add(zno)
                all_candidates.append({
                    'zoroNo': zno,
                    'title': title,
                    'price': float(price),
                    'brand': row[2] or 'Zoro Select',
                    'mfrNo': row[1] or '',
                    'is_container': is_container_drum(title),
                    'source': 'db'
                })
        conn.close()

    print(f"Total valid drum candidates: {len(all_candidates)}")

    # Check reviews in batches
    skus = [c['zoroNo'] for c in all_candidates]
    print(f"Checking reviews for {len(skus)} SKUs in batches of 50...")
    reviews_map = {}
    for i in range(0, len(skus), 50):
        chunk = skus[i:i+50]
        revs = get_reviews_batch(chunk)
        reviews_map.update(revs)
        time.sleep(0.05)

    reviewed_candidates = []
    for c in all_candidates:
        zno = c['zoroNo']
        if zno in reviews_map:
            c['rating'] = reviews_map[zno]['rating']
            c['reviews'] = reviews_map[zno]['reviews']
            reviewed_candidates.append(c)

    print(f"Total candidates with at least 1 verified review: {len(reviewed_candidates)}")

    # Sort prioritizing container drums first, then review count
    reviewed_candidates.sort(key=lambda x: (not x['is_container'], -x['reviews'], -x['rating']))

    top_100 = reviewed_candidates[:100]
    container_count = sum(1 for x in top_100 if x['is_container'])
    print(f"\nFinal Selected 100 Drum Listings:")
    print(f"  Container Drums (55 gal, 30 gal, salvage, reconditioned, etc.): {container_count}")
    print(f"  55-Gal Drum Equipment (dollies, spill pallets, lifters, pumps): {len(top_100) - container_count}")

    output_path = '/Users/kamibaloch342/Downloads/Dropshipping/Development_Unit/Hathoro Website/selected_100_drums.json'
    with open(output_path, 'w') as f:
        json.dump(top_100, f, indent=2)
    print(f"Saved exactly {len(top_100)} listings to {output_path}")

if __name__ == '__main__':
    run()
