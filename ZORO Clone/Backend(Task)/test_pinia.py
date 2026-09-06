import requests
import re
import codecs
import json

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
}
zoro_code = "G7804513"
url = f"https://www.zoro.com/i/{zoro_code}/"
print(f"Fetching {url}")
response = requests.get(url, headers=HEADERS, timeout=15)
response.encoding = 'utf-8'

html = response.text
m = re.search(r'window\.PINIA_INITIAL_STATE\s*=\s*\"(.*?)\";', html, re.DOTALL)
if m:
    raw_str = m.group(1)
    decoded_str = codecs.decode(raw_str, 'unicode_escape')
    data = json.loads(decoded_str)
    
    # Save the full dump to explore it
    with open('zoro_dump.json', 'w') as f:
        json.dump(data, f, indent=2)
    
    print("Dumped to zoro_dump.json")
    
    # Try to find category data in product details
    prod = data.get('productStore', {}).get('productDetailsCache', {}).get(zoro_code, {})
    categories = prod.get('categories', [])
    breadcrumbs = prod.get('breadcrumbs', [])
    print("Categories:", categories)
    print("Breadcrumbs:", breadcrumbs)
else:
    print("Could not find PINIA state.")
