import re

for filename in ['preview/index.html', 'preview/product.html']:
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()

    # Replace all references to 108 with general / 500+ catalog wording
    content = content.replace('Search 108+ industrial steel drums, poly barrels, spill pallets, degreasers...',
                              'Search industrial supplies, equipment, tools, or SKU...')
    content = content.replace('Search 108+ industrial drums, poly barrels, degreasers, or SKU...',
                              'Search industrial supplies, equipment, tools, or SKU...')
    content = content.replace('Search 108+ industrial drums, poly barrels, spill containment...',
                              'Search industrial supplies, equipment, tools, or SKU...')
    content = content.replace('Shop 108 Industrial Drums', 'Shop Industrial Equipment')
    content = content.replace('All 108 Drums', 'All Industrial Equipment')
    content = content.replace('All 108 Products', 'All Products')
    content = content.replace('108-product', '500+ product')
    content = content.replace('108-item', 'commercial')
    content = content.replace('108 Products', 'All Equipment')
    content = content.replace('108 drums', 'equipment')
    content = content.replace('108 Drums', 'Equipment & Storage')
    content = content.replace('108 items', 'supplies')
    content = content.replace('108 Items', 'All Products')
    content = content.replace('108 Catalog', 'Catalog')

    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)

print("Scrubbed all '108' references from preview pages successfully!")
