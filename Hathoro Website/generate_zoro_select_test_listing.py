import os
import csv

TUNNEL_BASE = "https://subsidiary-obituaries-upload-departmental.trycloudflare.com"
SKU_ORIG = "G6723787"
SKU_HATHORO = "H6723787"
MPN = "1656MB"
BRAND = "Hathoro Commercial"
HANDLE = "55-gallon-polyethylene-open-head-transport-drum-lever-lock-ring-blue-h6723787"
TITLE = "55-Gallon Polyethylene Open-Head Transport Drum with Lever Lock Ring - Blue"

# Official Shopify Standard Product Taxonomy
SHOPIFY_CATEGORY = "Business & Industrial > Industrial Storage"

# Pricing calculations with Zoro Select 10% Discount applied
zoro_base_price = 145.99
zoro_select_discount = 0.10
# Purchase cost from Zoro with 10% discount:
discounted_cost = round(zoro_base_price * (1.0 - zoro_select_discount), 2)  # $131.39
# Free shipping over $50:
shipping_cost = 5.99 if discounted_cost < 50.00 else 0.00
cost_price = round(discounted_cost + shipping_cost, 2)  # $131.39

# 10% Margin on store: Selling Price = Cost / (1 - 0.10)
# 131.39 / 0.90 = $145.99 (Matches Zoro retail price, locks in 10% net profit!)
selling_price = round(cost_price / 0.90, 2)
compare_at_price = round(selling_price * 1.15, 2)

# Stock locked at 500
STOCK_QUANTITY = 500

desc_html = f'''<div class="hathoro-product-description" style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #1e293b;">
  <div style="background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%); border-left: 5px solid #2563eb; padding: 20px 24px; border-radius: 8px; margin-bottom: 26px;">
    <h3 style="margin: 0 0 8px 0; color: #1e40af; font-size: 1.2rem; font-weight: 700; letter-spacing: -0.01em;">Industrial Storage & Transport Overview</h3>
    <p style="margin: 0; font-size: 0.98rem; color: #334155; line-height: 1.6;">
      The <strong>Hathoro 55-Gallon Open-Head Polyethylene Transport Drum</strong> is engineered for heavy-duty commercial storage, chemical handling, and regulatory material transit. Blow-molded from virgin high-density polyethylene (HDPE), this versatile container drum provides superior impact durability, chemical resistance, and corrosion-proof security for solids, liquids, and regulated waste materials.
    </p>
  </div>

  <h3 style="color: #0f172a; font-size: 1.2rem; border-bottom: 2px solid #e2e8f0; padding-bottom: 10px; margin-top: 28px; font-weight: 700;">
    Key Engineering & Performance Highlights
  </h3>
  <ul style="list-style: none; padding-left: 0; margin: 18px 0;">
    <li style="margin-bottom: 14px; display: flex; align-items: flex-start;">
      <span style="display: inline-block; background-color: #dbeafe; color: #1d4ed8; border-radius: 50%; width: 22px; height: 22px; text-align: center; line-height: 22px; font-size: 13px; font-weight: bold; margin-right: 12px; flex-shrink: 0;">✓</span>
      <span style="font-size: 0.96rem;"><strong>Heavy-Duty 55-Gallon Capacity:</strong> Sized for standard commercial bulk storage with a robust 552 lb (250 kg) load rating.</span>
    </li>
    <li style="margin-bottom: 14px; display: flex; align-items: flex-start;">
      <span style="display: inline-block; background-color: #dbeafe; color: #1d4ed8; border-radius: 50%; width: 22px; height: 22px; text-align: center; line-height: 22px; font-size: 13px; font-weight: bold; margin-right: 12px; flex-shrink: 0;">✓</span>
      <span style="font-size: 0.96rem;"><strong>Lever-Lock Steel Ring Closure:</strong> Heavy-gauge galvanized metal lever lock ring ensures airtight, tamper-evident compression sealing of the lid.</span>
    </li>
    <li style="margin-bottom: 14px; display: flex; align-items: flex-start;">
      <span style="display: inline-block; background-color: #dbeafe; color: #1d4ed8; border-radius: 50%; width: 22px; height: 22px; text-align: center; line-height: 22px; font-size: 13px; font-weight: bold; margin-right: 12px; flex-shrink: 0;">✓</span>
      <span style="font-size: 0.96rem;"><strong>100% Chemical-Grade HDPE Body:</strong> Seamless blow-molded construction resists harsh acids, caustics, lubricants, and weather extremes without rusting or denting.</span>
    </li>
    <li style="margin-bottom: 14px; display: flex; align-items: flex-start;">
      <span style="display: inline-block; background-color: #dbeafe; color: #1d4ed8; border-radius: 50%; width: 22px; height: 22px; text-align: center; line-height: 22px; font-size: 13px; font-weight: bold; margin-right: 12px; flex-shrink: 0;">✓</span>
      <span style="font-size: 0.96rem;"><strong>UN & DOT 49 CFR Certified:</strong> Certified UN Ratings (1H2/X200/S & 1H2/Y250/S) for hazardous and non-hazardous materials compliance.</span>
    </li>
    <li style="margin-bottom: 14px; display: flex; align-items: flex-start;">
      <span style="display: inline-block; background-color: #dbeafe; color: #1d4ed8; border-radius: 50%; width: 22px; height: 22px; text-align: center; line-height: 22px; font-size: 13px; font-weight: bold; margin-right: 12px; flex-shrink: 0;">✓</span>
      <span style="font-size: 0.96rem;"><strong>Wide Open-Head Access:</strong> Removable white lid allows unobstructed dumping, rotary pumping, and pressure washing.</span>
    </li>
  </ul>

  <h3 style="color: #0f172a; font-size: 1.2rem; border-bottom: 2px solid #e2e8f0; padding-bottom: 10px; margin-top: 32px; font-weight: 700;">
    Comprehensive Technical Specifications
  </h3>
  <table style="width: 100%; border-collapse: collapse; margin: 18px 0; font-size: 0.94rem; box-shadow: 0 1px 3px rgba(0,0,0,0.05); border-radius: 6px; overflow: hidden;">
    <tbody>
      <tr style="background-color: #f8fafc;">
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; font-weight: 600; width: 38%; color: #334155;">Catalog SKU</td>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; color: #0f172a; font-weight: 700;">{SKU_HATHORO}</td>
      </tr>
      <tr>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #334155;">Manufacturer Part # (MPN)</td>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; color: #0f172a;">{MPN}</td>
      </tr>
      <tr style="background-color: #f8fafc;">
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #334155;">Capacity</td>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; color: #0f172a;">55 Gallons (208 Liters)</td>
      </tr>
      <tr>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #334155;">Drum Head Type</td>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; color: #0f172a;">Open Head with Metal Lever Lock Ring</td>
      </tr>
      <tr style="background-color: #f8fafc;">
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #334155;">Primary Construction Material</td>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; color: #0f172a;">Blow-Molded High-Density Polyethylene (HDPE)</td>
      </tr>
      <tr>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #334155;">Uniform Load Capacity</td>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; color: #0f172a;">552 lbs (250 kg)</td>
      </tr>
      <tr style="background-color: #f8fafc;">
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #334155;">Dimensions (Dia. × Height)</td>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; color: #0f172a;">22-1/2" Top Outside Dia. × 36-3/8" Overall Height</td>
      </tr>
      <tr>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #334155;">Empty Weight</td>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; color: #0f172a;">23.5 lbs (10.7 kg)</td>
      </tr>
      <tr style="background-color: #f8fafc;">
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #334155;">Color Scheme</td>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; color: #0f172a;">Blue Body / White Removable Lid</td>
      </tr>
      <tr>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #334155;">UN Rating</td>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; color: #0f172a;">1H2/X200/S, 1H2/Y250/S (Solids & Regulated Goods)</td>
      </tr>
      <tr style="background-color: #f8fafc;">
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #334155;">Compliance Standards</td>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; color: #0f172a;">DOT 49 CFR 173.12, FDA Compliant Material</td>
      </tr>
    </tbody>
  </table>

  <h3 style="color: #0f172a; font-size: 1.2rem; border-bottom: 2px solid #e2e8f0; padding-bottom: 10px; margin-top: 30px; font-weight: 700;">
    Ideal Facility & Industry Applications
  </h3>
  <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; margin: 18px 0;">
    <div style="background: #f8fafc; border: 1px solid #e2e8f0; padding: 14px 16px; border-radius: 8px;">
      <strong style="color: #0f172a; font-size: 0.95rem; display: block; margin-bottom: 4px;">🏭 Industrial Chemical Storage</strong>
      <span style="color: #64748b; font-size: 0.88rem;">Safe containment for cutting fluids, degreasers, cooling agents, and chemical compounds.</span>
    </div>
    <div style="background: #f8fafc; border: 1px solid #e2e8f0; padding: 14px 16px; border-radius: 8px;">
      <strong style="color: #0f172a; font-size: 0.95rem; display: block; margin-bottom: 4px;">🧪 Hazardous Waste Cleanup</strong>
      <span style="color: #64748b; font-size: 0.88rem;">DOT-compliant disposal staging for contaminated rags, absorbents, and bio-waste materials.</span>
    </div>
    <div style="background: #f8fafc; border: 1px solid #e2e8f0; padding: 14px 16px; border-radius: 8px;">
      <strong style="color: #0f172a; font-size: 0.95rem; display: block; margin-bottom: 4px;">🔧 Automotive & Shop Garages</strong>
      <span style="color: #64748b; font-size: 0.88rem;">Bulk transport and containment for washdown liquids, spent fluids, and shop recyclables.</span>
    </div>
    <div style="background: #f8fafc; border: 1px solid #e2e8f0; padding: 14px 16px; border-radius: 8px;">
      <strong style="color: #0f172a; font-size: 0.95rem; display: block; margin-bottom: 4px;">📦 Freight & Material Transit</strong>
      <span style="color: #64748b; font-size: 0.88rem;">Heavy-gauge lever ring locks contents securely for domestic and international freight shipment.</span>
    </div>
  </div>
</div>'''

headers = [
    'Title', 'URL handle', 'Description', 'Vendor', 'Product category', 'Type', 'Tags',
    'Published on online store', 'Status', 'SKU', 'Barcode', 'Option1 name', 'Option1 value',
    'Option1 Linked To', 'Option2 name', 'Option2 value', 'Option2 Linked To', 'Option3 name',
    'Option3 value', 'Option3 Linked To', 'Price', 'Compare-at price', 'Cost per item',
    'Charge tax', 'Tax code', 'Unit price total measure', 'Unit price total measure unit',
    'Unit price base measure', 'Unit price base measure unit', 'Inventory tracker',
    'Inventory quantity', 'Continue selling when out of stock', 'Weight value (grams)',
    'Weight unit for display', 'Requires shipping', 'Fulfillment service', 'Product image URL',
    'Image position', 'Image alt text', 'Variant image URL', 'Gift card', 'SEO title',
    'SEO description', 'Color (product.metafields.shopify.color-pattern)',
    'Google Shopping / Google product category', 'Google Shopping / Gender',
    'Google Shopping / Age group', 'Google Shopping / Manufacturer part number (MPN)',
    'Google Shopping / Ad group name', 'Google Shopping / Ads labels',
    'Google Shopping / Condition', 'Google Shopping / Custom product',
    'Google Shopping / Custom label 0', 'Google Shopping / Custom label 1',
    'Google Shopping / Custom label 2', 'Google Shopping / Custom label 3',
    'Google Shopping / Custom label 4'
]

rows = []
# Row 1: Main Product + Image 1
r1 = {h: '' for h in headers}
r1['Title'] = TITLE
r1['URL handle'] = HANDLE
r1['Description'] = desc_html
r1['Vendor'] = BRAND
r1['Product category'] = SHOPIFY_CATEGORY
r1['Type'] = 'Transport Drum'
r1['Tags'] = '55 Gallon Drum, Transport Drum, Open Head Drum, Polyethylene, Lever Lock Ring, Chemical Resistant, DOT Compliant, UN Rated, In Stock, Commercial Grade, Industrial Storage'
r1['Published on online store'] = 'TRUE'
r1['Status'] = 'Active'
r1['SKU'] = SKU_HATHORO
r1['Barcode'] = MPN
r1['Option1 name'] = 'Title'
r1['Option1 value'] = 'Default Title'
r1['Price'] = f"{selling_price:.2f}"
r1['Compare-at price'] = f"{compare_at_price:.2f}"
r1['Cost per item'] = f"{cost_price:.2f}"
r1['Charge tax'] = 'TRUE'
r1['Tax code'] = 'P0000000'
r1['Inventory tracker'] = 'shopify'
r1['Inventory quantity'] = str(STOCK_QUANTITY)
r1['Continue selling when out of stock'] = 'DENY'
r1['Weight value (grams)'] = '10659'  # 23.5 lbs
r1['Weight unit for display'] = 'lb'
r1['Requires shipping'] = 'TRUE'
r1['Fulfillment service'] = 'manual'
r1['Product image URL'] = f"{TUNNEL_BASE}/{SKU_ORIG}/images/1000x1000_retina/image_01_1000x1000.jpg"
r1['Image position'] = '1'
r1['Image alt text'] = f"{TITLE} - Front Angle ({SKU_HATHORO})"
r1['Gift card'] = 'FALSE'
r1['SEO title'] = '55-Gallon Polyethylene Open-Head Drum | Lever Lock Ring - Blue'
r1['SEO description'] = 'Commercial-grade 55-gallon open-head polyethylene transport drum with metal lever lock ring. Chemical-resistant HDPE, UN certified for hazardous materials & bulk transit.'
r1['Color (product.metafields.shopify.color-pattern)'] = 'Blue'
r1['Google Shopping / Google product category'] = 'Business & Industrial > Industrial Storage > Drums & Barrels'
r1['Google Shopping / Gender'] = 'Unisex'
r1['Google Shopping / Age group'] = 'Adult'
r1['Google Shopping / Manufacturer part number (MPN)'] = MPN
r1['Google Shopping / Ad group name'] = 'Transport Drums'
r1['Google Shopping / Ads labels'] = 'Transport Drum, 55 Gal Drum, Poly Drum, In Stock, Commercial Grade'
r1['Google Shopping / Condition'] = 'New'
r1['Google Shopping / Custom product'] = 'FALSE'
r1['Google Shopping / Custom label 0'] = 'Tier 1: $100-$200'
r1['Google Shopping / Custom label 1'] = 'In Stock'
r1['Google Shopping / Custom label 2'] = 'Free Freight'
r1['Google Shopping / Custom label 3'] = BRAND
r1['Google Shopping / Custom label 4'] = 'Industrial Packaging'
rows.append(r1)

# This product only has 1 genuine image on Zoro (Z1p1qulcpEx_.JPG)
# We do not add duplicate or low-res thumbnail rows.

out_csv = "/Users/kamibaloch342/Downloads/Dropshipping/Development_Unit/Hathoro Website/Drums/shopify_test_H6723787_zoro_select.csv"
with open(out_csv, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=headers)
    w.writeheader()
    w.writerows(rows)

print(f"Successfully generated {out_csv}")
