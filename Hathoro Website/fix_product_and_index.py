import os

# Define the shared decent typography, header, and footer HTML & CSS
shared_head = """
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --font-decent: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      --c-black: #0f172a;
      --c-dark: #1e293b;
      --c-muted: #64748b;
      --c-light-muted: #94a3b8;
      --c-border: #e2e8f0;
      --c-border-light: #f1f5f9;
      --c-surface: #f8fafc;
      --c-white: #ffffff;
      --c-accent: #f74143;
      --c-accent-hover: #dc2626;
      --c-green: #16a34a;
      --c-green-light: #f0fdf4;
      --radius-sm: 6px;
      --radius-md: 10px;
      --radius-lg: 14px;
      --shadow-subtle: 0 1px 3px rgba(0,0,0,0.06);
      --shadow-card: 0 4px 12px rgba(0,0,0,0.06);
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: var(--font-decent);
      color: var(--c-black);
      background-color: #ffffff;
      -webkit-font-smoothing: antialiased;
      line-height: 1.5;
    }

    a { color: inherit; text-decoration: none; }

    /* Top Announcement Bar */
    .announcement-bar {
      background-color: var(--c-accent);
      color: #ffffff;
      text-align: center;
      padding: 9px 16px;
      font-size: 13.5px;
      font-weight: 600;
      letter-spacing: 0.01em;
    }

    /* Main Solid Header */
    header {
      background: #ffffff;
      border-bottom: 1px solid var(--c-border);
      position: relative;
      z-index: 40;
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
      gap: 6px;
      font-size: 24px;
      font-weight: 800;
      letter-spacing: -0.03em;
      color: var(--c-black);
    }
    .brand-logo span.red-dot {
      display: inline-block;
      width: 9px;
      height: 9px;
      background: var(--c-accent);
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
      border: 1px solid var(--c-border);
      border-radius: var(--radius-sm);
      outline: none;
      background: #f8fafc;
      transition: all 0.2s ease;
    }
    .search-box input:focus {
      border-color: var(--c-black);
      background: #ffffff;
      box-shadow: 0 0 0 3px rgba(15, 23, 42, 0.06);
    }
    .search-box svg {
      position: absolute;
      left: 14px;
      top: 50%;
      transform: translateY(-50%);
      width: 16px;
      height: 16px;
      color: var(--c-muted);
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
      color: #334155;
      transition: color 0.15s;
    }
    .header-actions a:hover {
      color: var(--c-accent);
    }
    .cart-badge {
      background: var(--c-accent);
      color: white;
      font-size: 11px;
      font-weight: 700;
      padding: 2px 7px;
      border-radius: 20px;
    }

    /* Sub-nav Bar */
    .nav-bar {
      border-top: 1px solid var(--c-border-light);
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
      color: #475569;
      border-bottom: 2px solid transparent;
      transition: color 0.15s, border-color 0.15s;
    }
    .nav-container a:hover, .nav-container a.active {
      color: var(--c-accent);
      border-bottom-color: var(--c-accent);
    }

    /* Solid Full 4-Column Footer */
    footer {
      background: #0f172a;
      color: #ffffff;
      padding: 56px 24px 28px 24px;
      margin-top: 64px;
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
"""

shared_header_html = """
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
        <a href="#account">
          <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"></path></svg>
          B2B Account
        </a>
        <a href="#cart" onclick="alert('Cart is empty. Browse products to add items.')">
          <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z"></path></svg>
          Cart <span class="cart-badge" id="cartBadgeCount">0</span>
        </a>
      </div>
    </div>

    <!-- Navigation -->
    <div class="nav-bar">
      <div class="nav-container">
        <a href="index.html">All Categories</a>
        <a href="index.html#drums" class="active">Industrial Drums & Containment</a>
        <a href="index.html#drums">Hathoro Select™</a>
        <a href="index.html#drums">Cleaning & Degreasers</a>
        <a href="index.html#drums">Hardware & Tools</a>
        <a href="tel:7276098404">Facility Quotes: (727) 609-8404</a>
      </div>
    </div>
  </header>
"""

shared_footer_html = """
  <!-- Full 4-Column Footer -->
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
          <li><a href="index.html#drums">Industrial Drums & Barrels</a></li>
          <li><a href="index.html#drums">Hathoro Select Line</a></li>
          <li><a href="index.html#drums">Spill Containment</a></li>
          <li><a href="index.html#drums">Heavy-Duty Degreasers</a></li>
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
          <button style="background: var(--c-accent); color: white; border: none; border-radius: 4px; padding: 9px 16px; font-size: 13.5px; font-weight: 700; cursor: pointer;">Join</button>
        </div>
      </div>
    </div>

    <div class="footer-bottom">
      <div>&copy; 2026 Hathoro.com. All rights reserved.</div>
      <div>Powered by Hathoro Supply Division</div>
    </div>
  </footer>
"""

# 2. Build the perfect, stable, decent-font Product Page
product_page_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Hathoro Select 30-Gallon Closed-Head Steel Transport Drum | Hathoro</title>
  {shared_head}
  <style>
    /* Product Page Specific Clean Minimalist Styles */
    .breadcrumb-row {{
      max-width: 1240px;
      margin: 0 auto;
      padding: 20px 24px 0 24px;
      font-size: 13.5px;
      color: var(--c-muted);
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .breadcrumb-row a {{
      color: var(--c-muted);
      transition: color 0.15s;
    }}
    .breadcrumb-row a:hover {{
      color: var(--c-black);
    }}
    .breadcrumb-row .sep {{
      color: #cbd5e1;
    }}

    .product-grid-wrap {{
      max-width: 1240px;
      margin: 24px auto 64px auto;
      padding: 0 24px;
      display: grid;
      grid-template-columns: 52% 48%;
      gap: 56px;
      align-items: start;
    }}

    /* Gallery (Left) */
    .product-gallery {{
      display: flex;
      flex-direction: column;
      gap: 16px;
    }}
    .main-photo-stage {{
      background: #ffffff;
      border: 1px solid var(--c-border);
      border-radius: var(--radius-md);
      height: 520px;
      position: relative;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 32px;
      overflow: hidden;
      box-shadow: var(--shadow-subtle);
    }}
    .main-photo-stage img {{
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
      mix-blend-mode: multiply;
      transition: transform 0.3s ease, opacity 0.2s ease;
    }}
    .main-photo-stage:hover img {{
      transform: scale(1.08);
    }}
    .photo-badge {{
      position: absolute;
      top: 16px;
      left: 16px;
      background: #0f172a;
      color: #ffffff;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      padding: 4px 10px;
      border-radius: 4px;
    }}
    .un-badge {{
      position: absolute;
      top: 16px;
      right: 16px;
      background: #fef08a;
      color: #854d0e;
      border: 1px solid #fde047;
      font-size: 11px;
      font-weight: 700;
      padding: 4px 10px;
      border-radius: 4px;
    }}

    .thumbnail-row {{
      display: flex;
      gap: 12px;
    }}
    .thumb-btn {{
      width: 90px;
      height: 90px;
      background: #ffffff;
      border: 1px solid var(--c-border);
      border-radius: var(--radius-sm);
      padding: 8px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.2s ease;
    }}
    .thumb-btn img {{
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
      mix-blend-mode: multiply;
    }}
    .thumb-btn:hover {{
      border-color: var(--c-black);
      transform: translateY(-2px);
    }}
    .thumb-btn.active {{
      border-color: var(--c-black);
      box-shadow: 0 0 0 1px var(--c-black);
    }}

    /* Information & Buying (Right) */
    .product-info-panel {{
      display: flex;
      flex-direction: column;
    }}
    .vendor-tag {{
      font-size: 12.5px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--c-accent);
      margin-bottom: 8px;
    }}
    .product-title-text {{
      font-size: 28px;
      font-weight: 800;
      letter-spacing: -0.025em;
      line-height: 1.25;
      color: var(--c-black);
      margin-bottom: 12px;
    }}
    .product-sku-row {{
      font-size: 13.5px;
      color: var(--c-muted);
      margin-bottom: 20px;
    }}

    .price-strip {{
      display: flex;
      align-items: baseline;
      gap: 12px;
      padding: 16px 0;
      border-top: 1px solid var(--c-border);
      border-bottom: 1px solid var(--c-border);
      margin-bottom: 20px;
    }}
    .price-large {{
      font-size: 32px;
      font-weight: 800;
      letter-spacing: -0.03em;
      color: var(--c-black);
    }}
    .price-struck {{
      font-size: 17px;
      color: var(--c-light-muted);
      text-decoration: line-through;
    }}
    .discount-pill {{
      background: #fee2e2;
      color: #b91c1c;
      font-size: 12px;
      font-weight: 700;
      padding: 3px 8px;
      border-radius: 4px;
    }}

    /* Stock Indicator */
    .stock-indicator-row {{
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 14px;
      font-weight: 600;
      color: var(--c-green);
      margin-bottom: 24px;
    }}
    .green-dot-pulse {{
      width: 8px;
      height: 8px;
      background: var(--c-green);
      border-radius: 50%;
      box-shadow: 0 0 0 3px rgba(22, 163, 74, 0.2);
    }}

    /* Actions */
    .stepper-control {{
      display: flex;
      align-items: center;
      gap: 16px;
      margin-bottom: 16px;
    }}
    .stepper-label {{
      font-size: 14px;
      font-weight: 600;
      color: var(--c-black);
    }}
    .stepper-box {{
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--c-border);
      border-radius: var(--radius-sm);
      overflow: hidden;
      background: #ffffff;
    }}
    .stepper-box button {{
      background: var(--c-surface);
      border: none;
      width: 40px;
      height: 42px;
      font-size: 18px;
      font-weight: 600;
      cursor: pointer;
      color: var(--c-black);
      transition: background 0.15s;
    }}
    .stepper-box button:hover {{
      background: #e2e8f0;
    }}
    .stepper-box input {{
      width: 48px;
      height: 42px;
      border: none;
      text-align: center;
      font-family: inherit;
      font-size: 15px;
      font-weight: 700;
      color: var(--c-black);
      outline: none;
    }}

    .btn-add-main {{
      width: 100%;
      padding: 16px;
      background: var(--c-black);
      color: #ffffff;
      font-family: inherit;
      font-size: 15.5px;
      font-weight: 700;
      border: none;
      border-radius: var(--radius-sm);
      cursor: pointer;
      transition: all 0.2s ease;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      margin-bottom: 12px;
    }}
    .btn-add-main:hover {{
      background: var(--c-dark);
      transform: translateY(-1px);
      box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    }}
    .btn-add-main:active {{
      transform: scale(0.99);
    }}

    .btn-shoppay-main {{
      width: 100%;
      padding: 15px;
      background: #5a31f4;
      color: #ffffff;
      font-family: inherit;
      font-size: 15px;
      font-weight: 700;
      border: none;
      border-radius: var(--radius-sm);
      cursor: pointer;
      transition: background 0.15s;
      margin-bottom: 28px;
    }}
    .btn-shoppay-main:hover {{
      background: #4b23db;
    }}

    /* Trust Box */
    .b2b-trust-card {{
      background: var(--c-surface);
      border: 1px solid var(--c-border);
      border-radius: var(--radius-md);
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }}
    .trust-row-item {{
      display: flex;
      align-items: flex-start;
      gap: 12px;
    }}
    .trust-badge-icon {{
      font-size: 18px;
      width: 32px;
      height: 32px;
      background: #ffffff;
      border: 1px solid var(--c-border);
      border-radius: 6px;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }}
    .trust-content-text h5 {{
      font-size: 13.5px;
      font-weight: 700;
      color: var(--c-black);
      margin-bottom: 2px;
    }}
    .trust-content-text p {{
      font-size: 12.5px;
      color: var(--c-muted);
      line-height: 1.4;
    }}

    /* Technical Specifications Section */
    .specs-wrapper-full {{
      max-width: 1240px;
      margin: 48px auto 0 auto;
      padding: 0 24px;
    }}
    .specs-header-title {{
      font-size: 22px;
      font-weight: 800;
      letter-spacing: -0.02em;
      color: var(--c-black);
      margin-bottom: 20px;
      padding-bottom: 12px;
      border-bottom: 1px solid var(--c-border);
    }}
    .specs-table-decent {{
      width: 100%;
      border-collapse: separate;
      border-spacing: 0;
      border: 1px solid var(--c-border);
      border-radius: var(--radius-md);
      overflow: hidden;
      margin-bottom: 40px;
    }}
    .specs-table-decent tr {{
      border-bottom: 1px solid var(--c-border);
      transition: background 0.1s ease;
    }}
    .specs-table-decent tr:last-child {{
      border-bottom: none;
    }}
    .specs-table-decent tr:nth-child(even) {{
      background: var(--c-surface);
    }}
    .specs-table-decent tr:hover {{
      background: #f1f5f9;
    }}
    .specs-table-decent td {{
      padding: 13px 20px;
      font-size: 14px;
      border-bottom: 1px solid var(--c-border);
    }}
    .specs-table-decent tr:last-child td {{
      border-bottom: none;
    }}
    .specs-table-decent td.col-prop {{
      width: 40%;
      font-weight: 600;
      color: #475569;
      border-right: 1px solid var(--c-border);
    }}
    .specs-table-decent td.col-val {{
      color: var(--c-black);
      font-weight: 500;
    }}
  </style>
</head>
<body>

  {shared_header_html}

  <!-- Breadcrumbs -->
  <div class="breadcrumb-row">
    <a href="index.html">Home</a>
    <span class="sep">/</span>
    <a href="index.html#drums">Industrial Drums & Containment</a>
    <span class="sep">/</span>
    <span style="color: var(--c-black); font-weight: 600;">Hathoro Select 30-Gallon Closed-Head Steel Transport Drum</span>
  </div>

  <!-- Main Product Display -->
  <div class="product-grid-wrap">

    <!-- Gallery (Left) -->
    <div class="product-gallery">
      <div class="main-photo-stage">
        <span class="photo-badge">Hathoro Select™</span>
        <span class="un-badge">UN 1A1/Y1.5/300</span>
        <img id="primaryImg" src="images/G1877111/image_01_1000x1000.jpg" alt="Hathoro Select 30-Gallon Steel Transport Drum">
      </div>

      <div class="thumbnail-row">
        <div class="thumb-btn active" onclick="switchMainImage('images/G1877111/image_01_1000x1000.jpg', this)">
          <img src="images/G1877111/image_01_1000x1000.jpg" alt="Front View">
        </div>
        <div class="thumb-btn" onclick="switchMainImage('images/G1877111/image_02_1000x1000.jpg', this)">
          <img src="images/G1877111/image_02_1000x1000.jpg" alt="Top Bungs View">
        </div>
        <div class="thumb-btn" onclick="switchMainImage('images/G1877111/image_03_1000x1000.jpg', this)">
          <img src="images/G1877111/image_03_1000x1000.jpg" alt="Side View">
        </div>
      </div>
    </div>

    <!-- Buying Panel (Right) -->
    <div class="product-info-panel">
      <div class="vendor-tag">HATHORO SELECT™ INDUSTRIAL</div>
      <h1 class="product-title-text">Hathoro Select 30-Gallon Closed-Head Heavy-Duty Steel Transport Drum - Black</h1>
      <div class="product-sku-row">SKU: <strong>H1877111</strong> &bull; UN Liquids Rating: <strong>1A1/Y1.5/300</strong></div>

      <div class="price-strip">
        <span class="price-large" id="unitPriceDisplay">$151.99</span>
        <span class="price-struck">$174.79</span>
        <span class="discount-pill">Save 15% ($22.80)</span>
      </div>

      <div class="stock-indicator-row">
        <span class="green-dot-pulse"></span>
        <span>IN STOCK — 500 units available (Same-Day Dispatch for orders by 3:00 PM CT)</span>
      </div>

      <!-- Quantity & Buy Action -->
      <div class="stepper-control">
        <span class="stepper-label">Quantity:</span>
        <div class="stepper-box">
          <button type="button" onclick="modifyQty(-1)">&minus;</button>
          <input type="text" id="cartQtyInput" value="1" readonly>
          <button type="button" onclick="modifyQty(1)">&plus;</button>
        </div>
      </div>

      <button class="btn-add-main" onclick="addItemToCart()">
        <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z"></path></svg>
        <span id="addBtnText">Add to Cart &mdash; $151.99</span>
      </button>

      <button class="btn-shoppay-main" onclick="addItemToCart()">
        Buy with <span style="font-weight: 800; letter-spacing: -0.04em;">Shop Pay</span>
      </button>

      <!-- Trust Box -->
      <div class="b2b-trust-card">
        <div class="trust-row-item">
          <div class="trust-badge-icon">🚚</div>
          <div class="trust-content-text">
            <h5>Fast Commercial Freight Shipping</h5>
            <p>Orders placed by 3:00 PM CT ship same business day via regional freight carriers.</p>
          </div>
        </div>
        <div class="trust-row-item">
          <div class="trust-badge-icon">🛡️</div>
          <div class="trust-content-text">
            <h5>Hathoro Quality & Pressure Guarantee</h5>
            <p>UN-certified 1A1/Y1.5/300 pressure tested. Unlined with rust inhibitor for reliability.</p>
          </div>
        </div>
        <div class="trust-row-item">
          <div class="trust-badge-icon">🏢</div>
          <div class="trust-content-text">
            <h5>B2B Corporate Net-30 Accounts</h5>
            <p>Pallet volume pricing and purchase order billing available for verified facilities.</p>
          </div>
        </div>
      </div>

    </div>

  </div>

  <!-- Technical Specifications Table Section -->
  <div class="specs-wrapper-full">
    <h2 class="specs-header-title">Technical Specifications & Dimensions</h2>
    
    <table class="specs-table-decent">
      <tbody>
        <tr>
          <td class="col-prop">Primary Drum Material</td>
          <td class="col-val">Cold-Rolled Carbon Steel</td>
        </tr>
        <tr>
          <td class="col-prop">Drum Volume Capacity</td>
          <td class="col-val">30 Gallon (113.5 Liters)</td>
        </tr>
        <tr>
          <td class="col-prop">Drum Head Type</td>
          <td class="col-val">Closed-Head (Tight-Head) with Welded Seams</td>
        </tr>
        <tr>
          <td class="col-prop">Body Material Gauge</td>
          <td class="col-val">20 Gauge (0.9 mm)</td>
        </tr>
        <tr>
          <td class="col-prop">Top & Bottom Material Gauge</td>
          <td class="col-val">20 Gauge (0.9 mm)</td>
        </tr>
        <tr>
          <td class="col-prop">UN Rating Liquids</td>
          <td class="col-val"><strong>1A1/Y1.5/300</strong> (Packing Groups II & III)</td>
        </tr>
        <tr>
          <td class="col-prop">Overall Height</td>
          <td class="col-val">29 1/2 in (749 mm)</td>
        </tr>
        <tr>
          <td class="col-prop">Top Outside Diameter</td>
          <td class="col-val">18 3/4 in (476 mm)</td>
        </tr>
        <tr>
          <td class="col-prop">Bung Openings</td>
          <td class="col-val">2 Precision Openings (2" NPS Bung & 3/4" Vent Bung)</td>
        </tr>
        <tr>
          <td class="col-prop">Cover Color / Body Color</td>
          <td class="col-val">White Cover / Baked Black Enamel Body</td>
        </tr>
        <tr>
          <td class="col-prop">Interior Lining</td>
          <td class="col-val">Unlined with Specialized Rust Inhibitor</td>
        </tr>
        <tr>
          <td class="col-prop">Empty Drum Weight</td>
          <td class="col-val">34.0 lbs (15.4 kg)</td>
        </tr>
        <tr>
          <td class="col-prop">Regulatory Standards</td>
          <td class="col-val">DOT 49 CFR 178.504, OSHA, EPA Compliant</td>
        </tr>
      </tbody>
    </table>
  </div>

  {shared_footer_html}

  <script>
    var currentItemPrice = 151.99;
    var cartCountTotal = 0;

    function switchMainImage(src, btnEl) {{
      var img = document.getElementById('primaryImg');
      img.style.opacity = '0';
      setTimeout(function() {{
        img.src = src;
        img.style.opacity = '1';
      }}, 120);

      document.querySelectorAll('.thumb-btn').forEach(function(b) {{
        b.classList.remove('active');
      }});
      btnEl.classList.add('active');
    }}

    function modifyQty(change) {{
      var input = document.getElementById('cartQtyInput');
      var val = parseInt(input.value) + change;
      if (val >= 1 && val <= 500) {{
        input.value = val;
        var total = (currentItemPrice * val).toFixed(2);
        document.getElementById('addBtnText').innerText = 'Add ' + val + ' to Cart — $' + total;
      }}
    }}

    function addItemToCart() {{
      var qty = parseInt(document.getElementById('cartQtyInput').value);
      cartCountTotal += qty;
      document.getElementById('cartBadgeCount').innerText = cartCountTotal;
      alert('✅ Successfully added ' + qty + ' unit(s) of Hathoro Select 30-Gallon Drum to cart!');
    }}
  </script>

</body>
</html>
"""

with open('preview/product.html', 'w', encoding='utf-8') as f:
    f.write(product_page_content)

print("Product page successfully aligned with identical header/footer and decent Inter font!")
