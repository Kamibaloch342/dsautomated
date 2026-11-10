import json, sqlite3, os, openpyxl
from datetime import datetime

QUEUE_PATH = '/Users/kamranbaloch/Downloads/Dropshipping/Development_Unit/Automations/data/walmart_queue.json'
DB_PATH = '/Users/kamranbaloch/Downloads/Dropshipping/Development_Unit/Automations/data/automations_unified.db'

def populate():
    # Load all rows from DB ordered by created_at DESC (last 100)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT * FROM walmart_orders 
        ORDER BY created_at DESC LIMIT 100
    """)
    rows = c.fetchall()
    
    queue = []
    for r in rows:
        po = r['po_number']
        if not po: continue
        
        q = {
            "id": f"PO{po}",
            "sku": r['zoro_code'] or "",
            "walmart_sku": r['walmart_sku'] or "",
            "wm_qty": r['wm_qty'] or 1,
            "address_raw": f"{r['ship_name']}\n{r['ship_street']}\n{r['ship_city']}, {r['ship_state']} {r['ship_zip']}",
            "walmart_po": po,
            "order_number": r['order_number'] or "",
            "order_date": r['order_date'] or "",
            "ship_by": r['ship_by'] or "",
            "title": r['title'] or "",
            "item_price": r['item_price'],
            "shipping_fee": r['shipping_fee'],
            "tax": r['tax'],
            "order_total": r['order_total'],
            "earnings": r['earnings'],
            "source": "db_sync",
            "status": r['status'] or "pending",
            "wb": r['wb_order_id'] or "",
            "error": r['error'] or "",
            "zoro_qty": r['zoro_qty'],
            "created_at": r['created_at'],
            "parsed": {
                "name": r['ship_name'] or "",
                "street": r['ship_street'] or "",
                "unit": "",
                "city": r['ship_city'] or "",
                "state": r['ship_state'] or "",
                "zip": r['ship_zip'] or "",
                "phone": r['ship_phone'] or ""
            },
            "placed_at": r['placed_at'] or "",
            "zoro_cost": r['zoro_cost'],
            "profit": r['profit']
        }
        queue.append(q)
        
    conn.close()
    
    # Save to queue
    with open(QUEUE_PATH, 'w') as f:
        json.dump(queue, f, indent=2)
    print(f"Populated GUI queue with {len(queue)} recent orders from DB!")

if __name__ == '__main__':
    populate()
