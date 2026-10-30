import sys, os, sqlite3, openpyxl, datetime
sys.path.insert(0, '/Users/kamranbaloch/Downloads/Dropshipping/Development_Unit/Automations')

from walmart_orders import _pick, _to_money, compute_earnings

DB_PATH = '/Users/kamranbaloch/Downloads/Dropshipping/Development_Unit/Automations/data/automations_unified.db'
# We will do both files so all data is in the DB
FILES = [
    '/Users/kamranbaloch/Downloads/Dropshipping/Development_Unit/Automations/PO_Data_100031269492026-07-16-19-36-06-604.xlsx',
    '/Users/kamranbaloch/Downloads/Dropshipping/Development_Unit/Automations/PO_Data_100031269492026-07-19-06-42-03-998.xlsx'
]

def backfill():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    updated = 0
    inserted = 0
    
    for EXCEL_PATH in FILES:
        if not os.path.exists(EXCEL_PATH): continue
        wb = openpyxl.load_workbook(EXCEL_PATH, data_only=True)
        ws = max(wb.worksheets, key=lambda s: (s.max_row or 0) * (s.max_column or 0))
        rows = [[('' if ch is None else ch) for ch in r] for r in ws.iter_rows(values_only=True)]
        wb.close()
        
        hidx = next((i for i, r in enumerate(rows) if sum(1 for ch in r if str(ch).strip()) >= 3), None)
        headers = [str(h).strip() for h in rows[hidx]]
        
        orders = []
        for r in rows[hidx+1:]:
            r = list(r) + [''] * (len(headers) - len(r))
            d = {headers[i]: r[i] for i in range(len(headers))}
            po = _pick(d, "Purchase order#", "PO#", "Purchase Order Number", "po", "ponumber")
            if not po: continue
            
            name = _pick(d, "Customer", "Customer Name", "Ship To Name", "name")
            addr1 = _pick(d, "Address Line 1", "Street", "Ship To Address 1", "address1")
            city = _pick(d, "City", "Ship To City")
            state = _pick(d, "State", "Ship To State")
            zipcode = _pick(d, "Zip", "Ship To Zip", "Postal Code")
            phone = _pick(d, "Phone", "Customer Phone Number")
            order_num = _pick(d, "Order#", "Order Number", "Customer Order#")
            order_date = _pick(d, "Order Date", "PO Date")
            ship_by = _pick(d, "Ship By", "Expected Ship Date")
            title = _pick(d, "Item Description", "Product Name", "Item Title")
            wm_sku = _pick(d, "SKU", "Item No")
            
            last_name = name.split()[-1] if name else ""
            addr_num = addr1.split()[0] if addr1 else ""
            
            qty_s = _pick(d, "Qty", "Quantity", "qty ordered", "order qty") or "1"
            try: wm_qty = max(1, int(float(qty_s)))
            except: wm_qty = 1
            
            item_price = _to_money(_pick(d, "Item price", "Item Cost", "Unit Price", "Price", "item_price"))
            if item_price is not None: item_price = round(item_price * wm_qty, 2)
            shipping_fee = _to_money(_pick(d, "Shipping fee", "Shipping Cost", "Shipping", "Ship Cost", "shipping_fee"))
            tax = _to_money(_pick(d, "Tax", "Sales Tax")) or 0.0
            order_total = (item_price or 0.0) + (shipping_fee or 0.0) + tax
            earnings = compute_earnings(item_price, shipping_fee)
            
            orders.append({
                "po": po, "name": name, "last_name": last_name, "addr_num": addr_num, 
                "addr1": addr1, "city": city, "state": state, "zip": zipcode, "phone": phone,
                "order_num": order_num, "order_date": order_date, "ship_by": ship_by, "title": title,
                "wm_sku": wm_sku, "earnings": earnings, "qty": wm_qty,
                "item_price": item_price, "shipping_fee": shipping_fee, "tax": tax, "order_total": order_total
            })
            
        print(f"Loaded {len(orders)} orders from {os.path.basename(EXCEL_PATH)}.")
        
        for o in orders:
            # Upsert into DB
            c.execute("SELECT wb_order_id, zoro_cost, profit FROM walmart_orders WHERE po_number=?", (o["po"],))
            db_row = c.fetchone()
            
            # Find in archive
            wb_id, zoro_cost, profit = None, None, None
            status = "pending"
            
            if db_row and db_row[0]:
                wb_id, zoro_cost, profit = db_row
                status = "placed"
            elif o["addr_num"]:
                c.execute("""
                    SELECT wb_order_id, order_total FROM archive_zoro 
                    WHERE ship_street LIKE ? AND ship_name LIKE ?
                    ORDER BY fetched_at DESC LIMIT 1
                """, (f"{o['addr_num']}%", f"%{o['last_name']}%"))
                zoro_row = c.fetchone()
                if zoro_row:
                    wb_id, zoro_cost = zoro_row
                    profit = round(o["earnings"] - zoro_cost, 2) if o["earnings"] else None
                    status = "placed"
            
            if not db_row:
                c.execute("""
                    INSERT INTO walmart_orders (
                        po_number, order_number, order_date, ship_by, title,
                        walmart_sku, wm_qty, item_price, shipping_fee, tax,
                        order_total, earnings, ship_name, ship_street, ship_city,
                        ship_state, ship_zip, ship_phone, wb_order_id, zoro_cost,
                        profit, status, source, created_at, placed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    o["po"], o["order_num"], str(o["order_date"]), str(o["ship_by"]), o["title"],
                    o["wm_sku"], o["qty"], o["item_price"], o["shipping_fee"], o["tax"],
                    o["order_total"], o["earnings"], o["name"], o["addr1"], o["city"],
                    o["state"], o["zip"], o["phone"], wb_id, zoro_cost, profit, status,
                    "imported", datetime.datetime.now().isoformat(), 
                    datetime.datetime.now().isoformat() if status == "placed" else None
                ))
                inserted += 1
            else:
                if wb_id and (db_row[0] != wb_id or db_row[1] != zoro_cost):
                    c.execute("""
                        UPDATE walmart_orders 
                        SET wb_order_id=?, zoro_cost=?, profit=?, status=?, error=NULL 
                        WHERE po_number=?
                    """, (wb_id, zoro_cost, profit, "placed", o["po"]))
                    updated += 1
                
    conn.commit()
    conn.close()
    print(f"\nInserted {inserted} and Updated {updated} orders successfully.")

if __name__ == '__main__':
    backfill()
