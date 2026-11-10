import json, sqlite3, os

QUEUE_PATH = '/Users/kamranbaloch/Downloads/Dropshipping/Development_Unit/Automations/data/walmart_queue.json'
DB_PATH = '/Users/kamranbaloch/Downloads/Dropshipping/Development_Unit/Automations/data/automations_unified.db'

def sync():
    if not os.path.exists(QUEUE_PATH):
        print("Queue not found")
        return
        
    with open(QUEUE_PATH, 'r') as f:
        queue = json.load(f)
        
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    updated = 0
    for q in queue:
        po = q.get('walmart_po')
        if not po: continue
        
        c.execute("SELECT wb_order_id, zoro_cost, profit, status FROM walmart_orders WHERE po_number=?", (po,))
        row = c.fetchone()
        
        if row and row[0]:
            wb, cost, profit, status = row
            if q.get('wb') != wb or q.get('zoro_cost') != cost:
                q['wb'] = wb
                q['zoro_cost'] = cost
                q['profit'] = profit
                q['status'] = status
                updated += 1
                
    conn.close()
    
    if updated > 0:
        with open(QUEUE_PATH, 'w') as f:
            json.dump(queue, f, indent=2)
        print(f"Synced {updated} orders from DB to GUI queue!")
    else:
        print("Everything is up to date!")

if __name__ == '__main__':
    sync()
