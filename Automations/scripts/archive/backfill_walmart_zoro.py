import sys, os, re, sqlite3, openpyxl, time
sys.path.insert(0, '/Users/kamranbaloch342/Desktop/Dropshipping/Development_Unit/Automations')

from platforms.gmail import authenticate_gmail_account
from engine.live_gmail_per_order import _extract_zoro_from_body, _get_message_body, _list_messages
from walmart_orders import _pick, _to_money, compute_earnings

DB_PATH = '/Users/kamranbaloch342/Desktop/Dropshipping/Development_Unit/Automations/data/automations_unified.db'
EXCEL_PATH = '/Users/kamranbaloch342/Desktop/Dropshipping/Development_Unit/Automations/PO_Data_100031269492026-07-16-19-36-06-604.xlsx'
ACCOUNTS = ["asad_amazon_us", "abecomllc26"]

def extract_wb(body, subj):
    m = re.search(r'\b(WB\d+)\b', subj, re.I)
    if m: return m.group(1).upper()
    m = re.search(r'\b(WB\d+)\b', body, re.I)
    if m: return m.group(1).upper()
    return None

def backfill():
    # 1. Parse Excel
    wb = openpyxl.load_workbook(EXCEL_PATH, data_only=True)
    ws = max(wb.worksheets, key=lambda s: (s.max_row or 0) * (s.max_column or 0))
    rows = [[('' if c is None else c) for c in r] for r in ws.iter_rows(values_only=True)]
    wb.close()
    
    hidx = next((i for i, r in enumerate(rows) if sum(1 for c in r if str(c).strip()) >= 3), None)
    headers = [str(h).strip() for h in rows[hidx]]
    
    orders = []
    for r in rows[hidx+1:]:
        r = list(r) + [''] * (len(headers) - len(r))
        d = {headers[i]: r[i] for i in range(len(headers))}
        po = _pick(d, "Purchase order#", "PO#", "Purchase Order Number", "po", "ponumber")
        if not po: continue
        
        name = _pick(d, "Customer", "Customer Name", "Ship To Name", "name")
        addr1 = _pick(d, "Address Line 1", "Street", "Ship To Address 1", "address1")
        
        last_name = name.split()[-1] if name else ""
        addr_num = addr1.split()[0] if addr1 else ""
        
        qty_s = _pick(d, "Qty", "Quantity", "qty ordered", "order qty") or "1"
        try: wm_qty = max(1, int(float(qty_s)))
        except: wm_qty = 1
        
        item_price = _to_money(_pick(d, "Item price", "Item Cost", "Unit Price", "Price", "item_price"))
        if item_price is not None: item_price = round(item_price * wm_qty, 2)
        shipping_fee = _to_money(_pick(d, "Shipping fee", "Shipping Cost", "Shipping", "Ship Cost", "shipping_fee"))
        earnings = compute_earnings(item_price, shipping_fee)
        
        orders.append({
            "po": po, "name": name, "last_name": last_name, "addr_num": addr_num, 
            "addr1": addr1, "earnings": earnings, "qty": wm_qty
        })
        
    print(f"Loaded {len(orders)} orders from Excel.")
    
    # 2. Init Gmail
    services = {}
    for acc in ACCOUNTS:
        try:
            services[acc] = authenticate_gmail_account(acc)
            print(f"Authenticated {acc}")
        except Exception as e:
            print(f"Failed to auth {acc}: {e}")
    
    # 3. Process
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    updated = 0
    
    for o in orders:
        if not o["last_name"]: continue
        
        # Check DB first
        c.execute("SELECT wb_order_id, zoro_cost, profit FROM walmart_orders WHERE po_number=?", (o["po"],))
        db_row = c.fetchone()
        if db_row and db_row[0] and db_row[1]:
            print(f"PO {o['po']}: Already backfilled -> {db_row[0]}")
            continue
            
        found = False
        for acc in ACCOUNTS:
            if acc not in services: continue
            
            # The search query uses street number OR last name just to be safe, sometimes last name differs on Zoro
            query = f'"{o["addr_num"]}" ("Has Been Received" OR "Receipt")'
            msgs = list(_list_messages(services[acc], query, max_results=3))
            
            if msgs:
                try:
                    meta = services[acc].users().messages().get(userId="me", id=msgs[0]["id"], format="metadata", metadataHeaders=["Subject"]).execute()
                    subj = ""
                    for h in meta.get("payload", {}).get("headers", []):
                        if h["name"] == "Subject": subj = h["value"]
                        
                    body = _get_message_body(services[acc], msgs[0]["id"])
                    
                    wb_id = extract_wb(body, subj)
                    zoro_data = _extract_zoro_from_body(body) or {}
                    zoro_cost = zoro_data.get("zoro_order_total")
                    
                    if wb_id and zoro_cost:
                        profit = round(o["earnings"] - zoro_cost, 2) if o["earnings"] else None
                        
                        print(f"PO {o['po']}: Found {wb_id} | Cost: ${zoro_cost} | Profit: ${profit} (via {acc})")
                        c.execute("""
                            UPDATE walmart_orders 
                            SET wb_order_id=?, zoro_cost=?, profit=?, status=?, error=NULL 
                            WHERE po_number=?
                        """, (wb_id, zoro_cost, profit, "placed", o["po"]))
                        found = True
                        updated += 1
                        conn.commit()
                        break
                except Exception as e:
                    print(f"Error extracting PO {o['po']}: {e}")
                    
        if not found:
            print(f"PO {o['po']}: Could not find WB in Gmail for {o['name']} ({o['addr1']})")
            
    conn.close()
    print(f"\nBackfilled {updated} orders successfully.")

if __name__ == '__main__':
    backfill()
