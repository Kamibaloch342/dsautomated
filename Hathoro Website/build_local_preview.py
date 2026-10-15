import os
import json

homepage_html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Hathoro | Industrial Supplies & Heavy-Duty Equipment</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:ital,wght@0,400..700;1,400..700&display=swap" rel="stylesheet">
  <style>
    :root {
      --font-family: 'Instrument Sans', -apple-system, BlinkMacSystemFont, sans-serif;
      --color-primary: #1c2228;
      --color-accent: #f74143;
      --color-bg: #ffffff;
      --color-bg-alt: #f8fafc;
      --color-border: #e2e8f0;
      --color-text: #0f172a;
      --color-muted: #64748b;
      --radius-sm: 6px;
      --radius-md: 8px;
      --radius-lg: 12px;
      --shadow-sm: 0 1px 3px rgba(0,0,0,0.06);
      --shadow-md: 0 4px 6px -1px rgba(0,0,0,0.08);
      --shadow-lg: 0 10px 15px -3px rgba(0,0,0,0.1);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--font-family);
      color: var(--color-text);
      background-color: var(--color-bg);
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }
    a { color: inherit; text-decoration: none; }
    
    /* Announcement Bar */
    .announcement-bar {
      background-color: var(--color-accent);
      color: #ffffff;
      text-align: center;
      padding: 9px 16px;
      font-size: 13.5px;
      font-weight: 600;
      letter-spacing: 0.01em;
    }
    
    /* Header */
    header {
      border-bottom: 1px solid var(--color-border);
      background: #ffffff;
      position: sticky;
      top: 0;
      z-index: 50;
    }
    .header-container {
      max-width: 1240px;
      margin: 0 auto;
      padding: 16px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 32px;
    }
    .brand-logo {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 24px;
      font-weight: 800;
      letter-spacing: -0.03em;
      color: var(--color-primary);
    }
    .brand-logo span.red-dot {
      display: inline-block;
      width: 10px;
      height: 10px;
      background: var(--color-accent);
      border-radius: 50%;
    }
    .search-box {
      flex: 1;
      max-width: 520px;
      position: relative;
    }
    .search-box input {
      width: 100%;
      padding: 10px 16px 10px 40px;
      font-family: inherit;
      font-size: 14px;
      border: 1px solid var(--color-border);
      border-radius: var(--radius-sm);
      outline: none;
      background: #f8fafc;
      transition: border-color 0.2s;
    }
    .search-box input:focus {
      border-color: var(--color-primary);
      background: #ffffff;
    }
    .search-box svg {
      position: absolute;
      left: 14px;
      top: 50%;
      transform: translateY(-50%);
      width: 16px;
      height: 16px;
      color: var(--color-muted);
    }
    .header-actions {
      display: flex;
      align-items: center;
      gap: 20px;
    }
    .header-actions a {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 14px;
      font-weight: 600;
      color: var(--color-primary);
    }
    .cart-badge {
      background: var(--color-accent);
      color: white;
      font-size: 11px;
      font-weight: 700;
      padding: 2px 7px;
      border-radius: 20px;
    }
    
    /* Navigation Bar */
    .nav-bar {
      border-top: 1px solid #f1f5f9;
      background: #ffffff;
    }
    .nav-container {
      max-width: 1240px;
      margin: 0 auto;
      padding: 0 24px;
      display: flex;
      gap: 28px;
    }
    .nav-container a {
      display: inline-block;
      padding: 12px 0;
      font-size: 14px;
      font-weight: 600;
      color: #334155;
      border-bottom: 2px solid transparent;
      transition: color 0.15s, border-color 0.15s;
    }
    .nav-container a:hover, .nav-container a.active {
      color: var(--color-accent);
      border-bottom-color: var(--color-accent);
    }
    
    /* Hero Banner */
    .hero-banner {
      background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
      color: #ffffff;
      padding: 64px 24px;
      text-align: center;
    }
    .hero-content {
      max-width: 860px;
      margin: 0 auto;
    }
    .hero-badge {
      display: inline-block;
      background: rgba(247, 65, 67, 0.2);
      color: #fca5a5;
      border: 1px solid rgba(247, 65, 67, 0.4);
      padding: 5px 14px;
      border-radius: 30px;
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      margin-bottom: 16px;
    }
    .hero-content h1 {
      font-size: 44px;
      font-weight: 800;
      letter-spacing: -0.025em;
      line-height: 1.15;
      margin-bottom: 18px;
    }
    .hero-content p {
      font-size: 18px;
      color: #cbd5e1;
      margin-bottom: 28px;
      line-height: 1.6;
    }
    .hero-buttons {
      display: flex;
      justify-content: center;
      gap: 16px;
    }
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 12px 26px;
      font-size: 15px;
      font-weight: 700;
      border-radius: var(--radius-sm);
      cursor: pointer;
      transition: all 0.15s;
    }
    .btn-primary {
      background: var(--color-accent);
      color: #ffffff;
    }
    .btn-primary:hover {
      background: #e02f31;
      transform: translateY(-1px);
    }
    .btn-secondary {
      background: rgba(255,255,255,0.12);
      color: #ffffff;
      border: 1px solid rgba(255,255,255,0.25);
    }
    .btn-secondary:hover {
      background: rgba(255,255,255,0.2);
    }

    /* Trust Props Bar */
    .trust-bar {
      background: #f8fafc;
      border-bottom: 1px solid var(--color-border);
      padding: 24px;
    }
    .trust-container {
      max-width: 1240px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 24px;
    }
    .trust-item {
      display: flex;
      align-items: center;
      gap: 14px;
    }
    .trust-icon {
      width: 44px;
      height: 44px;
      background: #ffffff;
      border: 1px solid var(--color-border);
      border-radius: 10px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 20px;
      flex-shrink: 0;
    }
    .trust-text h4 {
      font-size: 14.5px;
      font-weight: 700;
      color: var(--color-primary);
    }
    .trust-text p {
      font-size: 12.5px;
      color: var(--color-muted);
    }

    /* Section Structure */
    .section-wrapper {
      max-width: 1240px;
      margin: 0 auto;
      padding: 48px 24px;
    }
    .section-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      margin-bottom: 28px;
    }
    .section-title {
      font-size: 26px;
      font-weight: 800;
      letter-spacing: -0.02em;
      color: var(--color-primary);
    }
    .section-subtitle {
      font-size: 14px;
      color: var(--color-muted);
      margin-top: 4px;
    }
    .view-all-link {
      font-size: 14px;
      font-weight: 700;
      color: var(--color-accent);
      display: flex;
      align-items: center;
      gap: 4px;
    }

    /* Category Grid */
    .category-grid {
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 16px;
      margin-bottom: 32px;
    }
    .category-card {
      background: #ffffff;
      border: 1px solid var(--color-border);
      border-radius: var(--radius-md);
      padding: 20px 16px;
      text-align: center;
      transition: all 0.2s;
      cursor: pointer;
    }
    .category-card:hover {
      border-color: var(--color-primary);
      transform: translateY(-2px);
      box-shadow: var(--shadow-md);
    }
    .category-card .icon {
      font-size: 32px;
      margin-bottom: 10px;
    }
    .category-card h3 {
      font-size: 14px;
      font-weight: 700;
      color: var(--color-primary);
    }

    /* Products Grid */
    .product-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 20px;
    }
    .product-card {
      background: #ffffff;
      border: 1px solid var(--color-border);
      border-radius: var(--radius-md);
      overflow: hidden;
      display: flex;
      flex-direction: column;
      transition: all 0.2s;
    }
    .product-card:hover {
      border-color: #cbd5e1;
      box-shadow: var(--shadow-md);
      transform: translateY(-2px);
    }
    .product-thumb {
      background: #f8fafc;
      padding: 20px;
      position: relative;
      height: 250px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .product-thumb img {
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
      mix-blend-mode: multiply;
      transition: transform 0.3s;
    }
    .product-card:hover .product-thumb img {
      transform: scale(1.04);
    }
    .badge-select {
      position: absolute;
      top: 12px;
      left: 12px;
      background: #0f172a;
      color: #ffffff;
      font-size: 11px;
      font-weight: 700;
      padding: 3px 8px;
      border-radius: 4px;
      letter-spacing: 0.02em;
    }
    .badge-discount {
      position: absolute;
      top: 12px;
      right: 12px;
      background: #fee2e2;
      color: #b91c1c;
      font-size: 11px;
      font-weight: 700;
      padding: 3px 8px;
      border-radius: 4px;
    }
    .product-info {
      padding: 18px;
      display: flex;
      flex-direction: column;
      flex: 1;
    }
    .product-vendor {
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--color-muted);
      margin-bottom: 4px;
    }
    .product-title {
      font-size: 14.5px;
      font-weight: 700;
      color: var(--color-primary);
      line-height: 1.35;
      margin-bottom: 12px;
      flex: 1;
    }
    .product-pricing {
      display: flex;
      align-items: baseline;
      gap: 8px;
      margin-bottom: 10px;
    }
    .price-current {
      font-size: 19px;
      font-weight: 800;
      color: var(--color-primary);
    }
    .price-compare {
      font-size: 14px;
      color: var(--color-muted);
      text-decoration: line-through;
    }
    .stock-badge {
      font-size: 12px;
      font-weight: 600;
      color: #15803d;
      display: flex;
      align-items: center;
      gap: 5px;
      margin-bottom: 14px;
    }
    .stock-badge::before {
      content: "";
      width: 7px;
      height: 7px;
      background: #22c55e;
      border-radius: 50%;
    }
    .btn-view {
      display: block;
      width: 100%;
      text-align: center;
      padding: 9px;
      background: #ffffff;
      border: 1px solid var(--color-border);
      border-radius: var(--radius-sm);
      font-size: 13.5px;
      font-weight: 700;
      color: var(--color-primary);
      transition: all 0.15s;
    }
    .product-card:hover .btn-view {
      background: var(--color-primary);
      color: #ffffff;
      border-color: var(--color-primary);
    }

    /* Footer */
    footer {
      background: #0f172a;
      color: #ffffff;
      padding: 48px 24px 24px 24px;
      border-top: 1px solid #1e293b;
    }
    .footer-container {
      max-width: 1240px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: 2fr 1fr 1fr 1.5fr;
      gap: 40px;
      margin-bottom: 40px;
    }
    .footer-col h4 {
      font-size: 15px;
      font-weight: 700;
      margin-bottom: 16px;
      color: #f8fafc;
    }
    .footer-col p {
      font-size: 13.5px;
      color: #94a3b8;
      line-height: 1.6;
      margin-bottom: 12px;
    }
    .footer-col ul {
      list-style: none;
    }
    .footer-col ul li {
      margin-bottom: 8px;
    }
    .footer-col ul li a {
      font-size: 13.5px;
      color: #94a3b8;
      transition: color 0.15s;
    }
    .footer-col ul li a:hover {
      color: #ffffff;
    }
    .footer-bottom {
      max-width: 1240px;
      margin: 0 auto;
      border-top: 1px solid #1e293b;
      padding-top: 24px;
      display: flex;
      justify-content: space-between;
      font-size: 13px;
      color: #64748b;
    }
  </style>
</head>
<body>

  <!-- Top Announcement Bar -->
  <div class="announcement-bar">
    🚚 You now qualify for FREE shipping on orders over $50 | 500+ Units Ready to Ship Same Day
  </div>

  <!-- Main Header -->
  <header>
    <div class="header-container">
      <a href="index.html" class="brand-logo">
        HATHORO<span class="red-dot"></span>
      </a>

      <div class="search-box">
        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path></svg>
        <input type="text" placeholder="Search 108+ industrial drums, poly barrels, degreasers, or SKU...">
      </div>

      <div class="header-actions">
        <a href="#quote">
          <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"></path></svg>
          B2B Account
        </a>
        <a href="product.html">
          <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z"></path></svg>
          Cart <span class="cart-badge">0</span>
        </a>
      </div>
    </div>

    <!-- Navigation -->
    <div class="nav-bar">
      <div class="nav-container">
        <a href="index.html" class="active">All Categories</a>
        <a href="#drums">Industrial Drums & Containment</a>
        <a href="#select">Hathoro Select™</a>
        <a href="#cleaning">Cleaning & Degreasers</a>
        <a href="#hardware">Hardware & Tools</a>
        <a href="#bulk">Bulk & Facility Pricing</a>
      </div>
    </div>
  </header>

  <!-- Hero Banner -->
  <section class="hero-banner">
    <div class="hero-content">
      <div class="hero-badge">Industrial Supply & Equipment</div>
      <h1>Heavy-Duty Drums & Containment Built for American Industry.</h1>
      <p>Direct B2B supply of steel transport drums, UN-rated salvage barrels, spill containment, and industrial degreasers. 500 units in stock with guaranteed same-day dispatch.</p>
      <div class="hero-buttons">
        <a href="#drums" class="btn btn-primary">Shop Industrial Drums</a>
        <a href="product.html" class="btn btn-secondary">View Featured 30-Gal Drum</a>
      </div>
    </div>
  </section>

  <!-- Trust Bar -->
  <div class="trust-bar">
    <div class="trust-container">
      <div class="trust-item">
        <div class="trust-icon">⚡</div>
        <div class="trust-text">
          <h4>Same-Day Dispatch</h4>
          <p>Orders before 3PM CT ship today</p>
        </div>
      </div>
      <div class="trust-item">
        <div class="trust-icon">🛡️</div>
        <div class="trust-text">
          <h4>Hathoro Guarantee</h4>
          <p>UN-certified & tested quality</p>
        </div>
      </div>
      <div class="trust-item">
        <div class="trust-icon">🏷️</div>
        <div class="trust-text">
          <h4>B2B Bulk Pricing</h4>
          <p>Commercial tier discounts on volume</p>
        </div>
      </div>
      <div class="trust-item">
        <div class="trust-icon">📞</div>
        <div class="trust-text">
          <h4>Direct Support</h4>
          <p>Dedicated team: (727) 609-8404</p>
        </div>
      </div>
    </div>
  </div>

  <!-- Shop by Categories -->
  <div class="section-wrapper">
    <div class="section-header">
      <div>
        <h2 class="section-title">Shop by Category</h2>
        <p class="section-subtitle">Engineered for facility managers, logistics depots, and manufacturing plants.</p>
      </div>
    </div>

    <div class="category-grid">
      <div class="category-card">
        <div class="icon">🛢️</div>
        <h3>Industrial Drums & Barrels</h3>
      </div>
      <div class="category-card">
        <div class="icon">🛡️</div>
        <h3>Spill Containment & Salvage</h3>
      </div>
      <div class="category-card">
        <div class="icon">🧴</div>
        <h3>Industrial Cleaners & Degreasers</h3>
      </div>
      <div class="category-card">
        <div class="icon">📦</div>
        <h3>Packaging & Warehouse</h3>
      </div>
      <div class="category-card">
        <div class="icon">🔧</div>
        <h3>Hardware & Facility Maintenance</h3>
      </div>
    </div>
  </div>

  <!-- Featured Collection: The 108 Drums & Equipment Catalog -->
  <div class="section-wrapper" id="drums" style="padding-top: 0;">
    <div class="section-header">
      <div>
        <h2 class="section-title">Featured Industrial Drums & Containment</h2>
        <p class="section-subtitle">Brand new 108-product industrial catalog. All units pre-stocked at 500 units.</p>
      </div>
      <a href="product.html" class="view-all-link">View All 108 Items →</a>
    </div>

    <div class="product-grid">
      <!-- Product 1: Hathoro Select 30-Gal Steel Drum -->
      <div class="product-card">
        <div class="product-thumb">
          <span class="badge-select">Hathoro Select</span>
          <span class="badge-discount">-15%</span>
          <img src="images/G1877111/image_01_1000x1000.jpg" alt="Hathoro Select 30-Gallon Closed-Head Heavy-Duty Steel Transport Drum">
        </div>
        <div class="product-info">
          <div class="product-vendor">HATHORO SELECT</div>
          <a href="product.html" class="product-title">Hathoro Select 30-Gallon Closed-Head Heavy-Duty Steel Transport Drum - Black</a>
          <div class="product-pricing">
            <span class="price-current">$151.99</span>
            <span class="price-compare">$174.79</span>
          </div>
          <div class="stock-badge">In Stock (500 units)</div>
          <a href="product.html" class="btn-view">View Details & Specs</a>
        </div>
      </div>

      <!-- Product 2: Hathoro Select 16-Gal Steel Drum -->
      <div class="product-card">
        <div class="product-thumb">
          <span class="badge-select">Hathoro Select</span>
          <span class="badge-discount">-15%</span>
          <img src="images/G5815031/image_01_1000x1000.jpg" alt="Hathoro Select 16-Gallon Open-Head Steel Transport Drum">
        </div>
        <div class="product-info">
          <div class="product-vendor">HATHORO SELECT</div>
          <a href="product.html" class="product-title">Hathoro Select 16-Gallon Open-Head Steel Transport Drum - Black</a>
          <div class="product-pricing">
            <span class="price-current">$141.99</span>
            <span class="price-compare">$163.29</span>
          </div>
          <div class="stock-badge">In Stock (500 units)</div>
          <a href="product.html" class="btn-view">View Details & Specs</a>
        </div>
      </div>

      <!-- Product 3: Hathoro Select 10-Gal Steel Drum -->
      <div class="product-card">
        <div class="product-thumb">
          <span class="badge-select">Hathoro Select</span>
          <span class="badge-discount">-15%</span>
          <img src="images/G1180891/image_01_1000x1000.jpg" alt="Hathoro Select 10-Gallon Open-Head Steel Transport Drum">
        </div>
        <div class="product-info">
          <div class="product-vendor">HATHORO SELECT</div>
          <a href="product.html" class="product-title">Hathoro Select 10-Gallon Open-Head Steel Transport Drum - Black</a>
          <div class="product-pricing">
            <span class="price-current">$129.99</span>
            <span class="price-compare">$149.49</span>
          </div>
          <div class="stock-badge">In Stock (500 units)</div>
          <a href="product.html" class="btn-view">View Details & Specs</a>
        </div>
      </div>

      <!-- Product 4: Hathoro Select 55-Gal Poly Drum -->
      <div class="product-card">
        <div class="product-thumb">
          <span class="badge-select">Hathoro Select</span>
          <span class="badge-discount">-15%</span>
          <img src="images/G6723787/image_01_1000x1000.jpg" alt="Hathoro Select 55-Gallon Polyethylene Open-Head Transport Drum">
        </div>
        <div class="product-info">
          <div class="product-vendor">HATHORO SELECT</div>
          <a href="product.html" class="product-title">Hathoro Select 55-Gallon Polyethylene Open-Head Transport Drum - Blue</a>
          <div class="product-pricing">
            <span class="price-current">$145.99</span>
            <span class="price-compare">$167.89</span>
          </div>
          <div class="stock-badge">In Stock (500 units)</div>
          <a href="product.html" class="btn-view">View Details & Specs</a>
        </div>
      </div>
    </div>
  </div>

  <!-- Footer -->
  <footer>
    <div class="footer-container">
      <div class="footer-col">
        <div class="brand-logo" style="color: #ffffff; margin-bottom: 14px;">
          HATHORO<span class="red-dot"></span>
        </div>
        <p>Premium industrial supplies, hazardous material handling, and warehouse containment equipment. Built for American manufacturing facilities and commercial operations.</p>
        <p><strong>Customer Service:</strong> (727) 609-8404<br>Email: sales@hathoro.com</p>
      </div>

      <div class="footer-col">
        <h4>Products</h4>
        <ul>
          <li><a href="#drums">Industrial Drums & Barrels</a></li>
          <li><a href="#select">Hathoro Select Line</a></li>
          <li><a href="#containment">Spill Containment</a></li>
          <li><a href="#cleaners">Heavy-Duty Degreasers</a></li>
        </ul>
      </div>

      <div class="footer-col">
        <h4>Customer Care</h4>
        <ul>
          <li><a href="#shipping">Shipping & Freight Policy</a></li>
          <li><a href="#returns">Returns & Guarantee</a></li>
          <li><a href="#terms">Terms & B2B Purchase Orders</a></li>
          <li><a href="#contact">Contact Support</a></li>
        </ul>
      </div>

      <div class="footer-col">
        <h4>B2B Direct Accounts</h4>
        <p>Subscribe for wholesale tier discounts, freight credits, and tax-exempt purchasing.</p>
        <div style="display: flex; gap: 8px;">
          <input type="email" placeholder="facility@company.com" style="padding: 9px 12px; font-family: inherit; font-size: 13.5px; border: 1px solid #334155; border-radius: 4px; background: #1e293b; color: white; width: 100%;">
          <button class="btn btn-primary" style="padding: 9px 16px; font-size: 13.5px;">Join</button>
        </div>
      </div>
    </div>

    <div class="footer-bottom">
      <div>&copy; 2026 Hathoro.com. All rights reserved.</div>
      <div>Designed with Shopify Trade Theme & Instrument Sans</div>
    </div>
  </footer>

</body>
</html>
"""

product_html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Hathoro Select 30-Gallon Closed-Head Heavy-Duty Steel Transport Drum | Hathoro</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:ital,wght@0,400..700;1,400..700&display=swap" rel="stylesheet">
  <style>
    :root {
      --font-family: 'Instrument Sans', -apple-system, BlinkMacSystemFont, sans-serif;
      --color-primary: #1c2228;
      --color-accent: #f74143;
      --color-border: #e2e8f0;
      --color-text: #0f172a;
      --color-muted: #64748b;
      --radius-sm: 6px;
      --radius-md: 8px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--font-family);
      color: var(--color-text);
      background-color: #ffffff;
      line-height: 1.5;
    }
    a { color: inherit; text-decoration: none; }

    /* Announcement Bar & Header */
    .announcement-bar {
      background-color: var(--color-accent);
      color: #ffffff;
      text-align: center;
      padding: 9px 16px;
      font-size: 13.5px;
      font-weight: 600;
    }
    header {
      border-bottom: 1px solid var(--color-border);
      background: #ffffff;
      position: sticky;
      top: 0;
      z-index: 50;
    }
    .header-container {
      max-width: 1240px;
      margin: 0 auto;
      padding: 16px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .brand-logo {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 24px;
      font-weight: 800;
      color: var(--color-primary);
    }
    .brand-logo span.red-dot {
      width: 10px; height: 10px; background: var(--color-accent); border-radius: 50%; display: inline-block;
    }
    .header-nav a {
      font-size: 14px;
      font-weight: 600;
      color: #334155;
      margin-left: 24px;
      transition: color 0.15s;
    }
    .header-nav a:hover { color: var(--color-accent); }

    /* Breadcrumbs */
    .breadcrumbs {
      max-width: 1240px;
      margin: 0 auto;
      padding: 18px 24px 0 24px;
      font-size: 13.5px;
      color: var(--color-muted);
    }
    .breadcrumbs a { color: var(--color-muted); }
    .breadcrumbs a:hover { color: var(--color-primary); }
    .breadcrumbs span { margin: 0 8px; }

    /* Product Layout */
    .product-page-container {
      max-width: 1240px;
      margin: 0 auto;
      padding: 24px 24px 64px 24px;
      display: grid;
      grid-template-columns: 55% 45%;
      gap: 48px;
    }

    /* Left: Media Gallery */
    .media-gallery {
      position: sticky;
      top: 90px;
      height: fit-content;
    }
    .main-image-container {
      background: #f8fafc;
      border: 1px solid var(--color-border);
      border-radius: var(--radius-md);
      padding: 32px;
      text-align: center;
      height: 520px;
      display: flex;
      align-items: center;
      justify-content: center;
      margin-bottom: 16px;
    }
    .main-image-container img {
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
      mix-blend-mode: multiply;
      transition: transform 0.2s;
    }
    .thumbnails-row {
      display: flex;
      gap: 12px;
    }
    .thumb-item {
      width: 90px;
      height: 90px;
      border: 2px solid var(--color-border);
      border-radius: var(--radius-sm);
      padding: 8px;
      cursor: pointer;
      background: #f8fafc;
      transition: all 0.15s;
    }
    .thumb-item.active, .thumb-item:hover {
      border-color: var(--color-primary);
    }
    .thumb-item img {
      width: 100%;
      height: 100%;
      object-fit: contain;
      mix-blend-mode: multiply;
    }

    /* Right: Product Buy Info */
    .product-info-wrapper {
      display: flex;
      flex-direction: column;
    }
    .vendor-badge {
      display: inline-block;
      font-size: 13px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: #0f172a;
      background: #f1f5f9;
      padding: 4px 10px;
      border-radius: 4px;
      width: fit-content;
      margin-bottom: 12px;
    }
    .product-title {
      font-size: 28px;
      font-weight: 800;
      color: var(--color-primary);
      line-height: 1.25;
      letter-spacing: -0.02em;
      margin-bottom: 12px;
    }
    .sku-line {
      font-size: 13.5px;
      color: var(--color-muted);
      margin-bottom: 18px;
    }
    .price-box {
      display: flex;
      align-items: baseline;
      gap: 12px;
      padding: 16px 0;
      border-top: 1px solid var(--color-border);
      border-bottom: 1px solid var(--color-border);
      margin-bottom: 18px;
    }
    .price-main {
      font-size: 32px;
      font-weight: 800;
      color: var(--color-primary);
    }
    .price-compare {
      font-size: 18px;
      color: var(--color-muted);
      text-decoration: line-through;
    }
    .badge-savings {
      background: #fee2e2;
      color: #b91c1c;
      font-size: 13px;
      font-weight: 700;
      padding: 4px 10px;
      border-radius: 4px;
    }
    .inventory-status {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 14.5px;
      font-weight: 700;
      color: #15803d;
      margin-bottom: 24px;
    }
    .inventory-status .dot {
      width: 9px;
      height: 9px;
      background: #22c55e;
      border-radius: 50%;
      box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.2);
    }

    /* Quantity & Buy Buttons */
    .quantity-row {
      display: flex;
      align-items: center;
      gap: 16px;
      margin-bottom: 16px;
    }
    .qty-label {
      font-size: 14px;
      font-weight: 700;
      color: var(--color-primary);
    }
    .qty-picker {
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--color-border);
      border-radius: var(--radius-sm);
      overflow: hidden;
    }
    .qty-picker button {
      background: #f8fafc;
      border: none;
      width: 40px;
      height: 42px;
      font-size: 18px;
      cursor: pointer;
      color: var(--color-primary);
    }
    .qty-picker input {
      width: 50px;
      height: 42px;
      border: none;
      text-align: center;
      font-family: inherit;
      font-size: 15px;
      font-weight: 700;
      outline: none;
    }
    .btn-buy-solid {
      width: 100%;
      padding: 15px;
      background: #0f172a;
      color: #ffffff;
      font-family: inherit;
      font-size: 16px;
      font-weight: 700;
      border: none;
      border-radius: var(--radius-sm);
      cursor: pointer;
      margin-bottom: 10px;
      transition: background 0.15s, transform 0.15s;
    }
    .btn-buy-solid:hover {
      background: #1e293b;
      transform: translateY(-1px);
    }
    .btn-shop-pay {
      width: 100%;
      padding: 15px;
      background: #5a31f4;
      color: #ffffff;
      font-family: inherit;
      font-size: 16px;
      font-weight: 700;
      border: none;
      border-radius: var(--radius-sm);
      cursor: pointer;
      margin-bottom: 24px;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
    }
    .btn-shop-pay:hover {
      background: #4922db;
    }

    /* B2B Trust Card */
    .b2b-trust-card {
      background: #f8fafc;
      border: 1px solid var(--color-border);
      border-radius: var(--radius-md);
      padding: 20px;
      margin-bottom: 32px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .b2b-trust-row {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .b2b-trust-icon {
      font-size: 20px;
      flex-shrink: 0;
    }
    .b2b-trust-info h5 {
      font-size: 14px;
      font-weight: 700;
      color: var(--color-primary);
    }
    .b2b-trust-info p {
      font-size: 12.5px;
      color: var(--color-muted);
    }

    /* Product Description & Technical Table */
    .product-details-section {
      grid-column: 1 / -1;
      border-top: 1px solid var(--color-border);
      padding-top: 40px;
      margin-top: 20px;
    }
    .details-title {
      font-size: 22px;
      font-weight: 800;
      margin-bottom: 18px;
      color: var(--color-primary);
      letter-spacing: -0.01em;
    }
    .overview-text {
      font-size: 15.5px;
      line-height: 1.7;
      color: #334155;
      margin-bottom: 32px;
      max-width: 980px;
    }
    .highlights-list {
      list-style: none;
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 12px;
      margin-bottom: 36px;
    }
    .highlights-list li {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 14.5px;
      color: #1e293b;
      background: #f8fafc;
      padding: 12px 16px;
      border: 1px solid var(--color-border);
      border-radius: var(--radius-sm);
    }
    .highlights-list li strong {
      color: #0f172a;
    }

    /* Technical Specifications Table */
    .specs-table {
      width: 100%;
      border-collapse: collapse;
      border: 1px solid var(--color-border);
      border-radius: var(--radius-md);
      overflow: hidden;
      margin-bottom: 48px;
    }
    .specs-table tr {
      border-bottom: 1px solid var(--color-border);
    }
    .specs-table tr:last-child {
      border-bottom: none;
    }
    .specs-table tr:nth-child(even) {
      background-color: #f8fafc;
    }
    .specs-table td {
      padding: 12px 18px;
      font-size: 14px;
      line-height: 1.5;
    }
    .specs-table td.spec-name {
      width: 40%;
      font-weight: 700;
      color: #475569;
      border-right: 1px solid var(--color-border);
    }
    .specs-table td.spec-val {
      color: #0f172a;
      font-weight: 500;
    }

    /* Footer */
    footer {
      background: #0f172a;
      color: #ffffff;
      padding: 40px 24px;
      text-align: center;
      font-size: 13.5px;
      color: #94a3b8;
    }
  </style>
</head>
<body>

  <!-- Announcement Bar -->
  <div class="announcement-bar">
    🚚 You now qualify for FREE shipping on orders over $50 | 500+ Units Ready to Ship Same Day
  </div>

  <!-- Header -->
  <header>
    <div class="header-container">
      <a href="index.html" class="brand-logo">
        HATHORO<span class="red-dot"></span>
      </a>

      <div class="header-nav">
        <a href="index.html">← Back to Homepage</a>
        <a href="index.html#drums">All Industrial Drums</a>
        <a href="#specs">Technical Specs</a>
        <a href="#support">Facility Inquiries</a>
      </div>
    </div>
  </header>

  <!-- Breadcrumbs -->
  <div class="breadcrumbs">
    <a href="index.html">Home</a> <span>/</span>
    <a href="index.html#drums">Industrial Drums & Containment</a> <span>/</span>
    Hathoro Select 30-Gallon Closed-Head Steel Transport Drum
  </div>

  <!-- Main Product Page -->
  <div class="product-page-container">
    
    <!-- Left: Image Gallery -->
    <div class="media-gallery">
      <div class="main-image-container">
        <img id="mainImage" src="images/G1877111/image_01_1000x1000.jpg" alt="Hathoro Select 30-Gallon Steel Transport Drum">
      </div>
      <div class="thumbnails-row">
        <div class="thumb-item active" onclick="switchImage('images/G1877111/image_01_1000x1000.jpg', this)">
          <img src="images/G1877111/image_01_1000x1000.jpg" alt="View 1">
        </div>
        <div class="thumb-item" onclick="switchImage('images/G1877111/image_02_1000x1000.jpg', this)">
          <img src="images/G1877111/image_02_1000x1000.jpg" alt="View 2">
        </div>
        <div class="thumb-item" onclick="switchImage('images/G1877111/image_03_1000x1000.jpg', this)">
          <img src="images/G1877111/image_03_1000x1000.jpg" alt="View 3">
        </div>
      </div>
    </div>

    <!-- Right: Buying Info -->
    <div class="product-info-wrapper">
      <span class="vendor-badge">HATHORO SELECT™</span>
      <h1 class="product-title">Hathoro Select 30-Gallon Closed-Head Heavy-Duty Steel Transport Drum - Black</h1>
      <div class="sku-line">SKU: <strong>H1877111</strong> | UN Rated: <strong>1A1/Y1.5/300</strong></div>

      <div class="price-box">
        <span class="price-main">$151.99</span>
        <span class="price-compare">$174.79</span>
        <span class="badge-savings">SAVE $22.80 (15%)</span>
      </div>

      <div class="inventory-status">
        <span class="dot"></span>
        IN STOCK — 500 units available (Ships Today)
      </div>

      <!-- Quantity Selector & Action Buttons -->
      <div class="quantity-row">
        <span class="qty-label">Quantity:</span>
        <div class="qty-picker">
          <button type="button" onclick="changeQty(-1)">−</button>
          <input type="text" id="qtyInput" value="1" readonly>
          <button type="button" onclick="changeQty(1)">+</button>
        </div>
      </div>

      <button class="btn-buy-solid" onclick="alert('Item added to cart! Proceed to checkout.')">Add to Cart</button>
      <button class="btn-shop-pay" onclick="alert('Launching fast Shop Pay checkout...')">Buy with Shop Pay</button>

      <!-- B2B Trust Box -->
      <div class="b2b-trust-card">
        <div class="b2b-trust-row">
          <div class="b2b-trust-icon">🚚</div>
          <div class="b2b-trust-info">
            <h5>Fast Freight & Parcel Shipping</h5>
            <p>Orders placed by 3:00 PM CT ship same business day.</p>
          </div>
        </div>
        <div class="b2b-trust-row">
          <div class="b2b-trust-icon">🛡️</div>
          <div class="b2b-trust-info">
            <h5>Hathoro Quality Guarantee</h5>
            <p>Factory pressure tested and UN certified for commercial compliance.</p>
          </div>
        </div>
        <div class="b2b-trust-row">
          <div class="b2b-trust-icon">🏷️</div>
          <div class="b2b-trust-info">
            <h5>B2B Volume Pricing Available</h5>
            <p>Direct pallet discounts and tax-exempt purchasing for facilities.</p>
          </div>
        </div>
      </div>

    </div>

    <!-- Full-Width Specifications Section -->
    <div class="product-details-section" id="specs">
      <h2 class="details-title">Product Overview & Features</h2>
      <p class="overview-text">
        The Hathoro Select 30-Gallon Closed-Head Steel Transport Drum is engineered for secure storage, transport, and containment of industrial liquids and hazardous materials. Constructed with heavy-duty 20-gauge carbon steel body and top, it features an unlined interior with a protective rust inhibitor and a durable black baked enamel exterior finish. Equipped with two precision bungs (2" and 3/4") for secure filling and dispensing.
      </p>

      <h3 class="details-title" style="font-size: 18px; margin-bottom: 14px;">Key Highlights</h3>
      <ul class="highlights-list">
        <li><strong>UN Liquids Rating:</strong> 1A1/Y1.5/300 certified</li>
        <li><strong>Drum Volume Capacity:</strong> 30 Gallon</li>
        <li><strong>Construction:</strong> 20 Gauge Cold-Rolled Carbon Steel</li>
        <li><strong>Head Type:</strong> Closed-Head (Tight-Head) with 2 Bungs</li>
        <li><strong>Interior Finish:</strong> Unlined with Rust Inhibitor</li>
        <li><strong>Compliance:</strong> DOT 49 CFR, OSHA, and EPA certified</li>
      </ul>

      <h2 class="details-title">Complete Technical Specifications</h2>
      <table class="specs-table">
        <tbody>
          <tr>
            <td class="spec-name">Primary Drum Material</td>
            <td class="spec-val">Carbon Steel</td>
          </tr>
          <tr>
            <td class="spec-name">Drum Volume Capacity</td>
            <td class="spec-val">30 gal</td>
          </tr>
          <tr>
            <td class="spec-name">Drum Head Type</td>
            <td class="spec-val">Closed Head</td>
          </tr>
          <tr>
            <td class="spec-name">Body Material Gauge</td>
            <td class="spec-val">20 ga (0.9 mm)</td>
          </tr>
          <tr>
            <td class="spec-name">Top & Bottom Gauge</td>
            <td class="spec-val">20 ga (0.9 mm)</td>
          </tr>
          <tr>
            <td class="spec-name">UN Rating Liquids</td>
            <td class="spec-val">1A1/Y1.5/300</td>
          </tr>
          <tr>
            <td class="spec-name">UN Rating Solids</td>
            <td class="spec-val">Not Rated</td>
          </tr>
          <tr>
            <td class="spec-name">Overall Height</td>
            <td class="spec-val">29 1/2 in</td>
          </tr>
          <tr>
            <td class="spec-name">Top Outside Diameter</td>
            <td class="spec-val">18 3/4 in</td>
          </tr>
          <tr>
            <td class="spec-name">Number of Bung Holes</td>
            <td class="spec-val">2 (Includes 2" and 3/4" Tri-Sure Bungs)</td>
          </tr>
          <tr>
            <td class="spec-name">Cover & Body Color</td>
            <td class="spec-val">White Cover / Black Body</td>
          </tr>
          <tr>
            <td class="spec-name">Interior Lining</td>
            <td class="spec-val">Unlined with Rust Inhibitor</td>
          </tr>
          <tr>
            <td class="spec-name">Drum Weight</td>
            <td class="spec-val">34.0 lbs</td>
          </tr>
        </tbody>
      </table>
    </div>

  </div>

  <!-- Footer -->
  <footer>
    <div style="max-width: 1240px; margin: 0 auto;">
      &copy; 2026 Hathoro.com | All specifications verified for industrial compliance. Call (727) 609-8404 for freight questions.
    </div>
  </footer>

  <script>
    function switchImage(src, el) {
      document.getElementById('mainImage').src = src;
      document.querySelectorAll('.thumb-item').forEach(function(item) {
        item.classList.remove('active');
      });
      el.classList.add('active');
    }
    function changeQty(delta) {
      var input = document.getElementById('qtyInput');
      var val = parseInt(input.value) + delta;
      if (val >= 1 && val <= 500) {
        input.value = val;
      }
    }
  </script>

</body>
</html>
"""

with open('preview/index.html', 'w', encoding='utf-8') as f:
    f.write(homepage_html)

with open('preview/product.html', 'w', encoding='utf-8') as f:
    f.write(product_html)

print("Created preview/index.html and preview/product.html successfully!")
