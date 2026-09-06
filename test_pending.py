import sys, os, json
sys.path.append('Automations')
import walmart_orders
rows = walmart_orders.load_queue('Automations/data/walmart_queue.json')
import_file = '/Users/kamranbaloch342/Desktop/SDPI/bisp_dflt2/SSPA/sspa_Kamran_Dashboard/Database/PO_Data_07-22-2026_11_42_27.xlsx'
rows, n_new = walmart_orders.run_import_phase(import_file, rows, False)
pending = [r for r in rows if r.get("status") == "pending" and not (r.get("wb") or "").strip() and r.get("_import_batch")]
print("Pending rows:")
for r in pending:
    print(r.get("id"), r.get("_import_batch"))
