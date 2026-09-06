import sys, os
sys.path.append('Automations')
import walmart_orders
rows = walmart_orders.load_queue('Automations/data/walmart_queue.json')
import_file = '/Users/kamranbaloch342/Desktop/SDPI/bisp_dflt2/SSPA/sspa_Kamran_Dashboard/Database/PO_Data_07-22-2026_11_42_27.xlsx'
merged, n = walmart_orders.run_import_phase(import_file, rows, False)
for r in merged:
    if r.get('_import_batch'):
        print(f"Imported row: {r.get('id')}")
