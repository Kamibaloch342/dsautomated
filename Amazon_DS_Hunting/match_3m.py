import sqlite3, time
import pandas as pd

t0 = time.time()
df = pd.read_excel('Amazon_DS_Hunting/3M.xlsx')
conn = sqlite3.connect('Automations/TSV Files/combined_reports.db')
cursor = conn.cursor()

matches = []
for idx, row in df.iterrows():
    asin = str(row.get('ASIN', '')).strip()
    if not asin or pd.isna(row.get('ASIN')):
        continue
    
    upc_raw = str(row.get('Product Codes: UPC', '')).strip() if pd.notna(row.get('Product Codes: UPC')) else ''
    ean_raw = str(row.get('Product Codes: EAN', '')).strip() if pd.notna(row.get('Product Codes: EAN')) else ''
    mpn_raw = str(row.get('Product Codes: PartNumber', '')).strip() if pd.notna(row.get('Product Codes: PartNumber')) else ''
    num_items = row.get('Number of Items')
    sales = row.get('Monthly Sales Trends: Bought in past month')
    bsr = row.get('Sales Rank: Current')
    
    upcs = [u.strip() for u in upc_raw.replace(';', ',').split(',') if u.strip()]
    eans = [e.strip() for e in ean_raw.replace(';', ',').split(',') if e.strip()]
    mpns = [m.strip() for m in mpn_raw.replace(';', ',').split(',') if m.strip()]
    
    found = None
    # 1. Exact UPC matching
    for u in upcs + eans:
        u_clean = u.lstrip('0')
        variants = [u, u_clean, u.zfill(12), u.zfill(14)]
        placeholders = ','.join(['?']*len(variants))
        cursor.execute(f'''
            SELECT zoro_no, manufacturer_part_number, upc_code, short_description, sales_price, availabilityStatus, uom
            FROM zoro_data 
            WHERE upc_code IN ({placeholders}) AND (UPPER(zoro_brand_name) = '3M' OR UPPER(zoro_brand_name) LIKE '3M %')
            LIMIT 1
        ''', variants)
        res = cursor.fetchone()
        if res:
            found = res
            break
            
    # 2. MPN matching with brand 3M
    if not found:
        for m in mpns:
            m_variants = [m, m.replace('-', ''), m.replace(' ', ''), m.upper()]
            placeholders = ','.join(['?']*len(m_variants))
            cursor.execute(f'''
                SELECT zoro_no, manufacturer_part_number, upc_code, short_description, sales_price, availabilityStatus, uom
                FROM zoro_data 
                WHERE manufacturer_part_number IN ({placeholders}) AND (UPPER(zoro_brand_name) = '3M' OR UPPER(zoro_brand_name) LIKE '3M %')
                LIMIT 1
            ''', m_variants)
            res = cursor.fetchone()
            if res:
                found = res
                break

    if found:
        matches.append({
            'asin': asin,
            'amazon_mpn': mpn_raw,
            'amazon_upc': upc_raw,
            'amz_pack': num_items,
            'zoro_no': found[0],
            'zoro_mpn': found[1],
            'zoro_upc': found[2],
            'zoro_desc': found[3],
            'zoro_price': float(found[4]) if found[4] is not None else 0.0,
            'zoro_status': found[5],
            'zoro_uom': found[6],
            'sales': sales,
            'bsr': bsr
        })

conn.close()
print(f"Total matched in {time.time() - t0:.2f}s: {len(matches)} / {len(df)}")
for m in matches[:20]:
    print(f"MATCH: ASIN {m['asin']} (MPN {m['amazon_mpn']}) -> Zoro {m['zoro_no']} (MPN {m['zoro_mpn']}) | ${m['zoro_price']} | Sales: {m['sales']}/mo | BSR: {m['bsr']} | {m['zoro_desc'][:45]}")
