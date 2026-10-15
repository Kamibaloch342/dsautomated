import os
import json
import csv

TUNNEL_BASE = "https://qzobz-182-182-185-233.run.pinggy-free.link"
SKU_ORIG = "G0424182"
SKU_HATHORO = "H0424182"
MPN = "1633"
BRAND = "Eagle Mfg"
HANDLE = "eagle-mfg-1633-1-drum-modular-low-profile-spill-containment-platform-h0424182"
TITLE = "Eagle Mfg 1633 1-Drum Modular Low-Profile Spill Containment Platform (15-Gallon Sump, 2,000 lb Capacity)"

# Exact Shopify Standard Product Taxonomy from official GitHub:
# gid://shopify/TaxonomyCategory/bi-18-3 : Business & Industrial > Material Handling > Pallets & Loading Platforms
SHOPIFY_CATEGORY = "Business & Industrial > Material Handling > Pallets & Loading Platforms"

# Pricing calculations
# Zoro base price: $140.67
# Zoro shipping rule: $5.99 for orders under $50, $0.00 for orders >= $50
zoro_base_price = 140.67
shipping_cost = 5.99 if zoro_base_price < 50.00 else 0.00
cost_price = round(zoro_base_price + shipping_cost, 2)

# 10% Margin: Selling Price = Cost / (1 - 0.10)
# 140.67 / 0.90 = $156.30 (Profit: $15.63 / $156.30 = exactly 10.0%)
selling_price = round(cost_price / 0.90, 2)
compare_at_price = round(selling_price * 1.15, 2)

# Stock set at 500 (Always 500 per user rule)
STOCK_QUANTITY = 500

# Build Ultra-Detailed, Super Good Looking HTML Description
desc_html = f'''<div class="hathoro-product-description" style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #1e293b;">
  <div style="background: linear-gradient(135deg, #f0fdf4 0%, #e0f2fe 100%); border-left: 5px solid #0284c7; padding: 20px 24px; border-radius: 8px; margin-bottom: 26px;">
    <h3 style="margin: 0 0 8px 0; color: #0369a1; font-size: 1.2rem; font-weight: 700; letter-spacing: -0.01em;">Executive Facility Safety Overview</h3>
    <p style="margin: 0; font-size: 0.98rem; color: #334155; line-height: 1.6;">
      The <strong>Eagle Mfg 1633</strong> is a commercial-grade, modular secondary spill containment platform engineered specifically for the safe storage and dispensing of one 55-gallon industrial drum. Molded from high-density polyethylene (HDPE), this ultra-low-profile containment deck protects warehouse floors, personnel, and surrounding facilities against catastrophic leaks, continuous drips, and corrosive chemical spills.
    </p>
  </div>

  <h3 style="color: #0f172a; font-size: 1.2rem; border-bottom: 2px solid #e2e8f0; padding-bottom: 10px; margin-top: 28px; font-weight: 700;">
    Key Engineering & Performance Highlights
  </h3>
  <ul style="list-style: none; padding-left: 0; margin: 18px 0;">
    <li style="margin-bottom: 14px; display: flex; align-items: flex-start;">
      <span style="display: inline-block; background-color: #dcfce7; color: #15803d; border-radius: 50%; width: 22px; height: 22px; text-align: center; line-height: 22px; font-size: 13px; font-weight: bold; margin-right: 12px; flex-shrink: 0;">✓</span>
      <span style="font-size: 0.96rem;"><strong>15-Gallon Secondary Sump Capacity:</strong> Captures accidental overflow and minor container breaches directly beneath stored drums, ensuring immediate containment before hazardous substances reach drainage systems.</span>
    </li>
    <li style="margin-bottom: 14px; display: flex; align-items: flex-start;">
      <span style="display: inline-block; background-color: #dcfce7; color: #15803d; border-radius: 50%; width: 22px; height: 22px; text-align: center; line-height: 22px; font-size: 13px; font-weight: bold; margin-right: 12px; flex-shrink: 0;">✓</span>
      <span style="font-size: 0.96rem;"><strong>Industrial 2,000 lb Load Rating:</strong> Engineered internal support ribs safely distribute dynamic weight loads, supporting fully filled 55-gallon steel, polyethylene, or composite drums without bowing.</span>
    </li>
    <li style="margin-bottom: 14px; display: flex; align-items: flex-start;">
      <span style="display: inline-block; background-color: #dcfce7; color: #15803d; border-radius: 50%; width: 22px; height: 22px; text-align: center; line-height: 22px; font-size: 13px; font-weight: bold; margin-right: 12px; flex-shrink: 0;">✓</span>
      <span style="font-size: 0.96rem;"><strong>Ergonomic 6.5" Low-Profile Clearance:</strong> Low deck height drastically minimizes physical strain during drum loading, forklift loading, manual positioning, and rotary pump dispensing.</span>
    </li>
    <li style="margin-bottom: 14px; display: flex; align-items: flex-start;">
      <span style="display: inline-block; background-color: #dcfce7; color: #15803d; border-radius: 50%; width: 22px; height: 22px; text-align: center; line-height: 22px; font-size: 13px; font-weight: bold; margin-right: 12px; flex-shrink: 0;">✓</span>
      <span style="font-size: 0.96rem;"><strong>100% Chemical-Grade Polyethylene (HDPE):</strong> Seamless, rotationally molded construction will never rust, corrode, or degrade when exposed to aggressive industrial acids, caustics, fuels, or synthetic solvents.</span>
    </li>
    <li style="margin-bottom: 14px; display: flex; align-items: flex-start;">
      <span style="display: inline-block; background-color: #dcfce7; color: #15803d; border-radius: 50%; width: 22px; height: 22px; text-align: center; line-height: 22px; font-size: 13px; font-weight: bold; margin-right: 12px; flex-shrink: 0;">✓</span>
      <span style="font-size: 0.96rem;"><strong>Modular Floor Expansion System:</strong> Includes patented heavy-duty U-channel connectors allowing multiple platforms to interlock effortlessly into a custom multi-drum containment zone.</span>
    </li>
    <li style="margin-bottom: 14px; display: flex; align-items: flex-start;">
      <span style="display: inline-block; background-color: #dcfce7; color: #15803d; border-radius: 50%; width: 22px; height: 22px; text-align: center; line-height: 22px; font-size: 13px; font-weight: bold; margin-right: 12px; flex-shrink: 0;">✓</span>
      <span style="font-size: 0.96rem;"><strong>Removable Anti-Skid Poly Grate:</strong> High-traction textured black grating prevents container slipping and lifts out in seconds for swift sump pump-out and visual inspection.</span>
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
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #334155;">Brand / Sourcing</td>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; color: #0f172a;">{BRAND}</td>
      </tr>
      <tr>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #334155;">Drum Compatibility</td>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; color: #0f172a;">For One (1) Standard 55-Gallon Drum (Steel or Poly)</td>
      </tr>
      <tr style="background-color: #f8fafc;">
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #334155;">Secondary Sump Capacity</td>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; color: #0f172a;">15 Gallons (56.8 Liters)</td>
      </tr>
      <tr>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #334155;">Uniform Load Capacity</td>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; color: #0f172a;">2,000 lbs (907.2 kg)</td>
      </tr>
      <tr style="background-color: #f8fafc;">
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #334155;">Overall Dimensions (L × W × H)</td>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; color: #0f172a;">26-1/4" L × 26-1/4" W × 6-1/2" H (66.7 × 66.7 × 16.5 cm)</td>
      </tr>
      <tr>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #334155;">Primary Construction Material</td>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; color: #0f172a;">High-Density Polyethylene (HDPE) with UV Stabilizers</td>
      </tr>
      <tr style="background-color: #f8fafc;">
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #334155;">Color Architecture</td>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; color: #0f172a;">Safety Yellow Basin / Black Removable Grate</td>
      </tr>
      <tr>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #334155;">Included Accessories</td>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; color: #0f172a;">Heavy-Duty U-Channel Interlocking Connection Strip</td>
      </tr>
      <tr style="background-color: #f8fafc;">
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #334155;">Compliance Standards</td>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; color: #0f172a;">EPA 40 CFR 264.175, SPCC, UFC 8.103, OSHA 1910.120</td>
      </tr>
      <tr>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #334155;">Ramp Compatibility</td>
        <td style="padding: 12px 16px; border: 1px solid #e2e8f0; color: #0f172a;">Fully compatible with Eagle Modular Ramps 1689 & 1689B</td>
      </tr>
    </tbody>
  </table>

  <h3 style="color: #0f172a; font-size: 1.2rem; border-bottom: 2px solid #e2e8f0; padding-bottom: 10px; margin-top: 32px; font-weight: 700;">
    Regulatory EPA & SPCC Compliance
  </h3>
  <p style="font-size: 0.95rem; color: #475569; line-height: 1.6;">
    Unchecked industrial leaks can trigger costly facility shutdowns, hazardous environmental cleanup costs, and significant regulatory fines. The Eagle Mfg 1633 satisfies strict <strong>EPA 40 CFR 264.175</strong> requirements for container storage and complies with <strong>Spill Prevention, Control, and Countermeasure (SPCC)</strong> protocols to safeguard your facility during municipal and federal safety audits.
  </p>

  <h3 style="color: #0f172a; font-size: 1.2rem; border-bottom: 2px solid #e2e8f0; padding-bottom: 10px; margin-top: 30px; font-weight: 700;">
    Ideal Facility & Industry Applications
  </h3>
  <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; margin: 18px 0;">
    <div style="background: #f8fafc; border: 1px solid #e2e8f0; padding: 14px 16px; border-radius: 8px;">
      <strong style="color: #0f172a; font-size: 0.95rem; display: block; margin-bottom: 4px;">🏭 Industrial Manufacturing</strong>
      <span style="color: #64748b; font-size: 0.88rem;">Safe containment for cutting fluids, machining oils, and hydraulic lubricants.</span>
    </div>
    <div style="background: #f8fafc; border: 1px solid #e2e8f0; padding: 14px 16px; border-radius: 8px;">
      <strong style="color: #0f172a; font-size: 0.95rem; display: block; margin-bottom: 4px;">🧪 Chemical Processing</strong>
      <span style="color: #64748b; font-size: 0.88rem;">Corrosion-proof secondary basin for acids, alkalines, and active reagents.</span>
    </div>
    <div style="background: #f8fafc; border: 1px solid #e2e8f0; padding: 14px 16px; border-radius: 8px;">
      <strong style="color: #0f172a; font-size: 0.95rem; display: block; margin-bottom: 4px;">🔧 Automotive & Fleet Garages</strong>
      <span style="color: #64748b; font-size: 0.88rem;">Stationary containment for motor oil, transmission fluids, and glycol coolants.</span>
    </div>
    <div style="background: #f8fafc; border: 1px solid #e2e8f0; padding: 14px 16px; border-radius: 8px;">
      <strong style="color: #0f172a; font-size: 0.95rem; display: block; margin-bottom: 4px;">📦 Warehousing & Logistics</strong>
      <span style="color: #64748b; font-size: 0.88rem;">Safe staging and regulatory transit point for liquid chemical distribution.</span>
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
# Row 1: Primary Product + Image 1
r1 = {h: '' for h in headers}
r1['Title'] = TITLE
r1['URL handle'] = HANDLE
r1['Description'] = desc_html
r1['Vendor'] = BRAND
# Official Shopify Standard Product Taxonomy entry
r1['Product category'] = SHOPIFY_CATEGORY
r1['Type'] = 'Spill Containment Platform'
r1['Tags'] = 'Eagle Mfg, Spill Containment, 1-Drum, 15 Gallon Sump, 2000 lb Load Capacity, EPA Compliant, SPCC, Polyethylene, Secondary Containment, Drum Platform, In Stock, Commercial Grade, Industrial Supplies'
r1['Published on online store'] = 'TRUE'
r1['Status'] = 'Active'
r1['SKU'] = SKU_HATHORO  # H0424182
r1['Barcode'] = MPN       # 1633
r1['Option1 name'] = 'Title'
r1['Option1 value'] = 'Default Title'
r1['Price'] = f"{selling_price:.2f}"
r1['Compare-at price'] = f"{compare_at_price:.2f}"
r1['Cost per item'] = f"{cost_price:.2f}"
r1['Charge tax'] = 'TRUE'
r1['Tax code'] = 'P0000000'
r1['Inventory tracker'] = 'shopify'
r1['Inventory quantity'] = str(STOCK_QUANTITY)  # ALWAYS 500
r1['Continue selling when out of stock'] = 'DENY'
r1['Weight value (grams)'] = '7257'
r1['Weight unit for display'] = 'lb'
r1['Requires shipping'] = 'TRUE'
r1['Fulfillment service'] = 'manual'
r1['Product image URL'] = f"{TUNNEL_BASE}/{SKU_ORIG}/images/1000x1000_retina/image_01_1000x1000.jpg"
r1['Image position'] = '1'
r1['Image alt text'] = f"{TITLE} - Primary Front Angle ({SKU_HATHORO})"
r1['Gift card'] = 'FALSE'
r1['SEO title'] = 'Eagle Mfg 1633 1-Drum Spill Platform | 15-Gal Sump, 2000 lb Load'
r1['SEO description'] = 'Heavy-duty Eagle Mfg 1633 modular 1-drum spill containment platform. Features 15-gallon sump, 2,000 lb load rating, chemical-grade HDPE. EPA & SPCC compliant.'
r1['Color (product.metafields.shopify.color-pattern)'] = 'Yellow / Black'
r1['Google Shopping / Google product category'] = 'Business & Industrial > Material Handling'
r1['Google Shopping / Gender'] = 'Unisex'
r1['Google Shopping / Age group'] = 'Adult'
r1['Google Shopping / Manufacturer part number (MPN)'] = MPN
r1['Google Shopping / Ad group name'] = 'Spill Containment Platforms'
r1['Google Shopping / Ads labels'] = 'Spill Containment, Drum Platform, Eagle Mfg, In Stock, Top Rated'
r1['Google Shopping / Condition'] = 'New'
r1['Google Shopping / Custom product'] = 'FALSE'
r1['Google Shopping / Custom label 0'] = 'Tier 1: $100-$200'
r1['Google Shopping / Custom label 1'] = 'In Stock'
r1['Google Shopping / Custom label 2'] = 'Free Freight'
r1['Google Shopping / Custom label 3'] = BRAND
r1['Google Shopping / Custom label 4'] = 'Secondary Containment'
rows.append(r1)

# Rows 2 to 6: Remaining images
img_labels = [
    'Perspective Angle View',
    'Top Sump & Grate Detail',
    'Corner Load Distribution View',
    'Modular Side Interlocking Profile',
    'Heavy-Duty Polyethylene Texture & Grating'
]

for idx in range(2, 7):
    r_img = {h: '' for h in headers}
    r_img['URL handle'] = HANDLE
    r_img['Product image URL'] = f"{TUNNEL_BASE}/{SKU_ORIG}/images/1000x1000_retina/image_{idx:02d}_1000x1000.jpg"
    r_img['Image position'] = str(idx)
    label = img_labels[idx - 2]
    r_img['Image alt text'] = f"{TITLE} - {label} ({SKU_HATHORO})"
    rows.append(r_img)

out_csv = "/Users/kamibaloch342/Downloads/Dropshipping/Development_Unit/Hathoro Website/Drums/shopify_listing_H0424182_perfect.csv"
with open(out_csv, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=headers)
    w.writeheader()
    w.writerows(rows)

print(f"✓ Generated {out_csv} with stock {STOCK_QUANTITY} and verified category: {SHOPIFY_CATEGORY}")
