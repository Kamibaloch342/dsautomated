import sqlite3, re
import pandas as pd

df = pd.read_excel('Amazon_DS_Hunting/3M.xlsx')
conn = sqlite3.connect('Automations/TSV Files/combined_reports.db')
cursor = conn.cursor()

def parse_pack(desc, uom):
    m = re.search(r'PK(\d+)', str(desc), re.IGNORECASE)
    if m: return int(m.group(1))
    m2 = re.search(r'(\d+)\s*/\s*PK', str(desc), re.IGNORECASE)
    if m2: return int(m2.group(1))
    m3 = re.search(r'(\d+)\s*per\s*box', str(desc), re.IGNORECASE)
    if m3: return int(m3.group(1))
    m4 = re.search(r'Box of (\d+)', str(desc), re.IGNORECASE)
    if m4: return int(m4.group(1))
    if str(uom).upper() == 'EA': return 1
    return 1

exact_matches = []
mismatches = []

for idx, row in df.iterrows():
    asin = str(row.get('ASIN', '')).strip()
    if not asin or pd.isna(row.get('ASIN')): continue
    
    upc_raw = str(row.get('Product Codes: UPC', '')).strip() if pd.notna(row.get('Product Codes: UPC')) else ''
    ean_raw = str(row.get('Product Codes: EAN', '')).strip() if pd.notna(row.get('Product Codes: EAN')) else ''
    mpn_raw = str(row.get('Product Codes: PartNumber', '')).strip() if pd.notna(row.get('Product Codes: PartNumber')) else ''
    num_items = row.get('Number of Items')
    amz_pack = int(num_items) if pd.notna(num_items) and str(num_items).replace('.', '').isdigit() else 1
    sales = row.get('Monthly Sales Trends: Bought in past month')
    bsr = row.get('Sales Rank: Current')
    
    upcs = [u.strip() for u in upc_raw.replace(';', ',').split(',') if u.strip()]
    eans = [e.strip() for e in ean_raw.replace(';', ',').split(',') if e.strip()]
    mpns = [m.strip() for m in mpn_raw.replace(';', ',').split(',') if m.strip()]
    
    found = None
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
        z_pack = parse_pack(found[3], found[6])
        is_pack_match = (amz_pack == z_pack)
        obj = {
            'asin': asin,
            'mpn': mpn_raw,
            'amz_pack': amz_pack,
            'zoro_no': found[0],
            'zoro_mpn': found[1],
            'zoro_price': float(found[4]) if found[4] is not None else 0.0,
            'z_pack': z_pack,
            'z_desc': found[3],
            'sales': sales,
            'bsr': bsr,
            'is_pack_match': is_pack_match
        }
        if is_pack_match:
            exact_matches.append(obj)
        else:
            mismatches.append(obj)

conn.close()
print(f'Total Matched: {len(exact_matches) + len(mismatches)}')
print(f'Exact Pack Matches: {len(exact_matches)}')
print(f'Pack Mismatches: {len(mismatches)}')
for m in exact_matches[:20]:
    print(f"EXACT: ASIN {m['asin']} (Pack: {m['amz_pack']}) | MPN {m['mpn']} -> Zoro {m['zoro_no']} (${m['zoro_price']}) | Sales: {m['sales']}/mo | BSR: {m['bsr']} | {m['z_desc'][:40]}")
