import os, sys, json
import pandas as pd

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)
from batch_scrape_offers import scrape_asins

# Load 3M matches
df = pd.read_excel(os.path.join(CURRENT_DIR, '3M.xlsx'))

# Load the exact matched ASINs from check_3m_packs
import check_3m_packs
matched = check_3m_packs.exact_matches + check_3m_packs.mismatches
# Sort by sales descending
matched.sort(key=lambda x: (-(x.get('sales') or 0), (x.get('bsr') or 999999)))

top_asins = [m['asin'] for m in matched[:40]]
print(f"[*] Top 40 3M ASINs to scrape (Sales from {matched[0]['sales']} to {matched[39]['sales']}/mo)...")
print(top_asins)

scrape_asins(top_asins)
