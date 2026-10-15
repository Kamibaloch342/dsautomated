import re

with open('generate_super_beautiful_theme.py', 'r') as f:
    theme_code = f.read()

# Extract shared_css, header_html, footer_html
import generate_super_beautiful_theme as g

homepage = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Hathoro | Industrial Supplies & Heavy-Duty Equipment</title>
  {g.shared_css}
  <style>
    /* Homepage Specific Super Beautiful Styles */
    .hero-luxe-section {{
      background: linear-gradient(135deg, #09090b 0%, #18181b 45%, #0f172a 100%);
      color: #ffffff;
      padding: 80px 24px;
      text-align: center;
      position: relative;
      overflow: hidden;
    }}
    .hero-luxe-section::before {{
      content: "";
      position: absolute;
      top: -50%;
      left: 50%;
      transform: translateX(-50%);
      width: 800px;
      height: 400px;
      background: radial-gradient(ellipse, rgba(247, 65, 67, 0.15) 0%, rgba(0,0,0,0) 70%);
      pointer-events: none;
    }}
    .hero-inner-shell {{
      max-width: 860px;
      margin: 0 auto;
      position: relative;
      z-index: 10;
    }}
    .hero-tag-pill {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(255, 255, 255, 0.1);
      backdrop-filter: blur(8px);
      border: 1px solid rgba(255, 255, 255, 0.15);
      color: #fca5a5;
      font-size: 12.5px;
      font-weight: 700;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      padding: 6px 16px;
      border-radius: var(--radius-pill);
      margin-bottom: 20px;
    }}
    .hero-title-main {{
      font-size: 48px;
      font-weight: 800;
      letter-spacing: -0.035em;
      line-height: 1.15;
      margin-bottom: 20px;
      color: #ffffff;
    }}
    .hero-subhead {{
      font-size: 18px;
      color: #cbd5e1;
      line-height: 1.6;
      margin-bottom: 32px;
      font-weight: 400;
    }}
    .hero-cta-rack {{
      display: flex;
      justify-content: center;
      gap: 16px;
    }}
    .btn-luxe-primary {{
      background: var(--c-accent);
      color: #ffffff;
      font-family: var(--font-display);
      font-size: 15px;
      font-weight: 700;
      padding: 14px 30px;
      border-radius: var(--radius-pill);
      transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
      box-shadow: 0 4px 16px rgba(247, 65, 67, 0.35);
    }}
    .btn-luxe-primary:hover {{
      background: var(--c-accent-hover);
      transform: translateY(-2px);
      box-shadow: 0 8px 24px rgba(247, 65, 67, 0.45);
    }}
    .btn-luxe-secondary {{
      background: rgba(255, 255, 255, 0.08);
      color: #ffffff;
      border: 1px solid rgba(255, 255, 255, 0.2);
      font-family: var(--font-display);
      font-size: 15px;
      font-weight: 600;
      padding: 14px 28px;
      border-radius: var(--radius-pill);
      transition: all 0.2s ease;
    }}
    .btn-luxe-secondary:hover {{
      background: rgba(255, 255, 255, 0.16);
    }}

    /* Trust Props Banner */
    .trust-strip-card {{
      background: #ffffff;
      border-bottom: 1px solid var(--c-border);
      padding: 24px;
    }}
    .trust-grid-inner {{
      max-width: 1280px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 28px;
    }}
    .trust-block {{
      display: flex;
      align-items: center;
      gap: 14px;
    }}
    .trust-icon-gem {{
      width: 44px;
      height: 44px;
      background: #f8fafc;
      border: 1px solid var(--c-border);
      border-radius: 10px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 20px;
      flex-shrink: 0;
    }}
    .trust-caption h4 {{
      font-size: 14px;
      font-weight: 700;
      color: var(--c-black);
    }}
    .trust-caption p {{
      font-size: 12.5px;
      color: var(--c-muted);
    }}

    /* Sections */
    .section-shell {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 56px 24px;
    }}
    .section-head-bar {{
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      margin-bottom: 32px;
    }}
    .head-title {{
      font-size: 28px;
      font-weight: 800;
      letter-spacing: -0.03em;
      color: var(--c-black);
    }}
    .head-subtitle {{
      font-size: 14px;
      color: var(--c-muted);
      margin-top: 4px;
    }}

    /* Categories Grid */
    .cats-grid {{
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 16px;
      margin-bottom: 24px;
    }}
    .cat-item-card {{
      background: #ffffff;
      border: 1px solid var(--c-border);
      border-radius: var(--radius-lg);
      padding: 24px 18px;
      text-align: center;
      transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
      cursor: pointer;
    }}
    .cat-item-card:hover {{
      border-color: var(--c-black);
      transform: translateY(-3px);
      box-shadow: var(--shadow-md);
    }}
    .cat-icon-lg {{
      font-size: 36px;
      margin-bottom: 12px;
    }}
    .cat-name {{
      font-size: 14.5px;
      font-weight: 700;
      color: var(--c-black);
    }}

    /* Product Cards */
    .products-mosaic {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 24px;
    }}
    .card-luxe {{
      background: #ffffff;
      border: 1px solid var(--c-border);
      border-radius: var(--radius-lg);
      overflow: hidden;
      display: flex;
      flex-direction: column;
      transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
      box-shadow: var(--shadow-sm);
    }}
    .card-luxe:hover {{
      border-color: #cbd5e1;
      transform: translateY(-4px);
      box-shadow: var(--shadow-lift);
    }}
    .card-thumb-wrap {{
      background: radial-gradient(ellipse at center, #ffffff 40%, #f4f6f8 100%);
      padding: 24px;
      position: relative;
      height: 260px;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .card-thumb-wrap img {{
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
      mix-blend-mode: multiply;
      transition: transform 0.4s cubic-bezier(0.16, 1, 0.3, 1);
    }}
    .card-luxe:hover .card-thumb-wrap img {{
      transform: scale(1.06);
    }}
    .badge-select-sm {{
      position: absolute;
      top: 14px;
      left: 14px;
      background: #09090b;
      color: #ffffff;
      font-size: 11px;
      font-weight: 800;
      padding: 3px 9px;
      border-radius: 4px;
      letter-spacing: 0.04em;
    }}
    .badge-discount-sm {{
      position: absolute;
      top: 14px;
      right: 14px;
      background: #fee2e2;
      color: #b91c1c;
      font-size: 11px;
      font-weight: 700;
      padding: 3px 8px;
      border-radius: 4px;
    }}
    .card-body {{
      padding: 20px;
      display: flex;
      flex-direction: column;
      flex: 1;
    }}
    .card-vendor {{
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--c-accent);
      margin-bottom: 4px;
    }}
    .card-title {{
      font-size: 15px;
      font-weight: 700;
      color: var(--c-black);
      line-height: 1.35;
      margin-bottom: 12px;
      flex: 1;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}
    .card-price-row {{
      display: flex;
      align-items: baseline;
      gap: 8px;
      margin-bottom: 10px;
    }}
    .card-price-cur {{
      font-size: 20px;
      font-weight: 800;
      letter-spacing: -0.03em;
      color: var(--c-black);
    }}
    .card-price-was {{
      font-size: 14px;
      color: var(--c-light-muted);
      text-decoration: line-through;
    }}
    .card-stock {{
      font-size: 12px;
      font-weight: 600;
      color: var(--c-green);
      display: flex;
      align-items: center;
      gap: 6px;
      margin-bottom: 16px;
    }}
    .card-stock::before {{
      content: "";
      width: 7px;
      height: 7px;
      background: var(--c-green);
      border-radius: 50%;
    }}
    .btn-card-action {{
      display: block;
      width: 100%;
      text-align: center;
      padding: 10px;
      background: #f8fafc;
      border: 1px solid var(--c-border);
      border-radius: var(--radius-sm);
      font-size: 13.5px;
      font-weight: 700;
      color: var(--c-black);
      transition: all 0.2s ease;
    }}
    .card-luxe:hover .btn-card-action {{
      background: var(--c-black);
      color: #ffffff;
      border-color: var(--c-black);
    }}
  </style>
</head>
<body>

  {g.header_html}

  <!-- Hero Section -->
  <section class="hero-luxe-section">
    <div class="hero-inner-shell">
      <div class="hero-tag-pill">Hathoro Industrial Direct</div>
      <h1 class="hero-title-main">Heavy-Duty Drums & Containment Built for American Industry.</h1>
      <p class="hero-subhead">Direct supply of certified steel drums, UN-rated salvage barrels, spill containment, and heavy-duty degreasers. 500 units in stock with same-day freight dispatch.</p>
      <div class="hero-cta-rack">
        <a href="#drums" class="btn-luxe-primary">Shop 108 Industrial Drums</a>
        <a href="product.html" class="btn-luxe-secondary">View Featured 30-Gal Drum</a>
      </div>
    </div>
  </section>

  <!-- Trust Strip -->
  <div class="trust-strip-card">
    <div class="trust-grid-inner">
      <div class="trust-block">
        <div class="trust-icon-gem">⚡</div>
        <div class="trust-caption">
          <h4>Same-Day Dispatch</h4>
          <p>Orders by 3:00 PM CT ship today</p>
        </div>
      </div>
      <div class="trust-block">
        <div class="trust-icon-gem">🛡️</div>
        <div class="trust-caption">
          <h4>UN 1A1 Pressure Tested</h4>
          <p>Certified hazardous & standard liquid containment</p>
        </div>
      </div>
      <div class="trust-block">
        <div class="trust-icon-gem">🏷️</div>
        <div class="trust-caption">
          <h4>B2B Bulk Volume</h4>
          <p>Direct pallet discounts on commercial orders</p>
        </div>
      </div>
      <div class="trust-block">
        <div class="trust-icon-gem">📞</div>
        <div class="trust-caption">
          <h4>Dedicated Support</h4>
          <p>Direct facility hotline: (727) 609-8404</p>
        </div>
      </div>
    </div>
  </div>

  <!-- Categories -->
  <div class="section-shell">
    <div class="section-head-bar">
      <div>
        <h2 class="head-title">Shop by Industrial Category</h2>
        <p class="head-subtitle">Engineered for facility managers, logistics depots, and manufacturing plants.</p>
      </div>
    </div>

    <div class="cats-grid">
      <div class="cat-item-card">
        <div class="cat-icon-lg">🛢️</div>
        <div class="cat-name">Industrial Drums & Barrels</div>
      </div>
      <div class="cat-item-card">
        <div class="cat-icon-lg">🛡️</div>
        <div class="cat-name">Spill Containment Pallets</div>
      </div>
      <div class="cat-item-card">
        <div class="cat-icon-lg">🧴</div>
        <div class="cat-name">Cleaners & Degreasers</div>
      </div>
      <div class="cat-item-card">
        <div class="cat-icon-lg">📦</div>
        <div class="cat-name">Packaging & Shipping</div>
      </div>
      <div class="cat-item-card">
        <div class="cat-icon-lg">🔧</div>
        <div class="cat-name">Hardware & Facility</div>
      </div>
    </div>
  </div>

  <!-- Featured 108 Drums Grid -->
  <div class="section-shell" id="drums" style="padding-top: 0;">
    <div class="section-head-bar">
      <div>
        <h2 class="head-title">Featured Industrial Drums & Equipment</h2>
        <p class="head-subtitle">High-margin 108-item industrial catalog. Pre-stocked at 500 units per item.</p>
      </div>
      <a href="product.html" style="font-size: 14px; font-weight: 700; color: var(--c-accent);">View All 108 Products &rarr;</a>
    </div>

    <div class="products-mosaic">
      <!-- Item 1 -->
      <div class="card-luxe">
        <div class="card-thumb-wrap">
          <span class="badge-select-sm">Hathoro Select™</span>
          <span class="badge-discount-sm">-15%</span>
          <img src="images/G1877111/image_01_1000x1000.jpg" alt="Hathoro Select 30-Gallon Steel Transport Drum">
        </div>
        <div class="card-body">
          <div class="card-vendor">HATHORO SELECT™</div>
          <a href="product.html" class="card-title">Hathoro Select 30-Gallon Closed-Head Heavy-Duty Steel Transport Drum - Black</a>
          <div class="card-price-row">
            <span class="card-price-cur">$151.99</span>
            <span class="card-price-was">$174.79</span>
          </div>
          <div class="card-stock">In Stock (500 units)</div>
          <a href="product.html" class="btn-card-action">View Details & Specs</a>
        </div>
      </div>

      <!-- Item 2 -->
      <div class="card-luxe">
        <div class="card-thumb-wrap">
          <span class="badge-select-sm">Hathoro Select™</span>
          <span class="badge-discount-sm">-15%</span>
          <img src="images/G5815031/image_01_1000x1000.jpg" alt="Hathoro Select 16-Gallon Open-Head Steel Drum">
        </div>
        <div class="card-body">
          <div class="card-vendor">HATHORO SELECT™</div>
          <a href="product.html" class="card-title">Hathoro Select 16-Gallon Open-Head Steel Transport Drum - Black</a>
          <div class="card-price-row">
            <span class="card-price-cur">$141.99</span>
            <span class="card-price-was">$163.29</span>
          </div>
          <div class="card-stock">In Stock (500 units)</div>
          <a href="product.html" class="btn-card-action">View Details & Specs</a>
        </div>
      </div>

      <!-- Item 3 -->
      <div class="card-luxe">
        <div class="card-thumb-wrap">
          <span class="badge-select-sm">Hathoro Select™</span>
          <span class="badge-discount-sm">-15%</span>
          <img src="images/G1180891/image_01_1000x1000.jpg" alt="Hathoro Select 10-Gallon Open-Head Steel Drum">
        </div>
        <div class="card-body">
          <div class="card-vendor">HATHORO SELECT™</div>
          <a href="product.html" class="card-title">Hathoro Select 10-Gallon Open-Head Steel Transport Drum - Black</a>
          <div class="card-price-row">
            <span class="card-price-cur">$129.99</span>
            <span class="card-price-was">$149.49</span>
          </div>
          <div class="card-stock">In Stock (500 units)</div>
          <a href="product.html" class="btn-card-action">View Details & Specs</a>
        </div>
      </div>

      <!-- Item 4 -->
      <div class="card-luxe">
        <div class="card-thumb-wrap">
          <span class="badge-select-sm">Hathoro Select™</span>
          <span class="badge-discount-sm">-15%</span>
          <img src="images/G6723787/image_01_1000x1000.jpg" alt="Hathoro Select 55-Gallon Polyethylene Drum">
        </div>
        <div class="card-body">
          <div class="card-vendor">HATHORO SELECT™</div>
          <a href="product.html" class="card-title">Hathoro Select 55-Gallon Polyethylene Open-Head Transport Drum - Blue</a>
          <div class="card-price-row">
            <span class="card-price-cur">$145.99</span>
            <span class="card-price-was">$167.89</span>
          </div>
          <div class="card-stock">In Stock (500 units)</div>
          <a href="product.html" class="btn-card-action">View Details & Specs</a>
        </div>
      </div>
    </div>
  </div>

  {g.footer_html}

</body>
</html>
"""

with open('preview/index.html', 'w', encoding='utf-8') as f:
    f.write(homepage)

print("Super beautiful index.html created successfully!")
