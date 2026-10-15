import os

product_html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Hathoro Select 30-Gallon Closed-Head Heavy-Duty Steel Transport Drum - Black | Hathoro</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:ital,wght@0,400..700;1,400..700&display=swap" rel="stylesheet">
  <style>
    :root {
      --font-family: 'Instrument Sans', -apple-system, BlinkMacSystemFont, sans-serif;
      --color-primary: #0f172a;
      --color-primary-hover: #1e293b;
      --color-accent: #f74143;
      --color-accent-hover: #dc2626;
      --color-accent-light: #fef2f2;
      --color-success: #16a34a;
      --color-success-light: #f0fdf4;
      --color-warning: #f59e0b;
      --color-warning-light: #fef3c7;
      --color-bg: #ffffff;
      --color-surface: #f8fafc;
      --color-surface-hover: #f1f5f9;
      --color-border: #e2e8f0;
      --color-border-dark: #cbd5e1;
      --color-text: #0f172a;
      --color-text-secondary: #475569;
      --color-muted: #64748b;
      --radius-sm: 6px;
      --radius-md: 10px;
      --radius-lg: 16px;
      --shadow-sm: 0 1px 2px rgba(0,0,0,0.05);
      --shadow-md: 0 4px 12px rgba(0,0,0,0.08);
      --shadow-lg: 0 12px 24px -4px rgba(0,0,0,0.12);
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: var(--font-family);
      color: var(--color-text);
      background-color: #fafbfc;
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }

    a { color: inherit; text-decoration: none; }

    /* Top Announcement Bar */
    .announcement-bar {
      background: linear-gradient(90deg, #1e293b 0%, #0f172a 100%);
      color: #ffffff;
      padding: 10px 16px;
      font-size: 13.5px;
      font-weight: 600;
      display: flex;
      justify-content: center;
      align-items: center;
      gap: 12px;
      border-bottom: 1px solid rgba(255,255,255,0.1);
    }
    .announcement-bar .badge-free {
      background: var(--color-accent);
      color: #ffffff;
      font-size: 11px;
      font-weight: 800;
      padding: 2px 8px;
      border-radius: 4px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }

    /* Main Navigation Header */
    header {
      background: #ffffff;
      border-bottom: 1px solid var(--color-border);
      position: sticky;
      top: 0;
      z-index: 50;
      box-shadow: var(--shadow-sm);
    }
    .header-container {
      max-width: 1280px;
      margin: 0 auto;
      padding: 14px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 32px;
    }
    .brand-logo {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 26px;
      font-weight: 800;
      letter-spacing: -0.03em;
      color: var(--color-primary);
    }
    .brand-logo .dot {
      width: 9px;
      height: 9px;
      background: var(--color-accent);
      border-radius: 50%;
    }
    .search-bar {
      flex: 1;
      max-width: 540px;
      position: relative;
    }
    .search-bar input {
      width: 100%;
      padding: 10px 16px 10px 42px;
      font-family: inherit;
      font-size: 14px;
      border: 1px solid var(--color-border);
      border-radius: var(--radius-md);
      background: #f8fafc;
      outline: none;
      transition: all 0.2s;
    }
    .search-bar input:focus {
      background: #ffffff;
      border-color: var(--color-primary);
      box-shadow: 0 0 0 3px rgba(15, 23, 42, 0.08);
    }
    .search-bar svg {
      position: absolute;
      left: 14px;
      top: 50%;
      transform: translateY(-50%);
      width: 18px;
      height: 18px;
      color: var(--color-muted);
    }
    .header-right {
      display: flex;
      align-items: center;
      gap: 24px;
    }
    .header-link {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 14px;
      font-weight: 600;
      color: #334155;
      transition: color 0.15s;
    }
    .header-link:hover {
      color: var(--color-accent);
    }
    .cart-btn {
      display: flex;
      align-items: center;
      gap: 8px;
      background: #0f172a;
      color: #ffffff !important;
      padding: 8px 16px;
      border-radius: var(--radius-sm);
      font-size: 13.5px;
      font-weight: 700;
      transition: background 0.15s;
    }
    .cart-btn:hover {
      background: #1e293b;
    }
    .cart-pill {
      background: var(--color-accent);
      color: #ffffff;
      font-size: 11px;
      padding: 2px 7px;
      border-radius: 20px;
    }

    /* Sub-nav / Breadcrumb */
    .breadcrumb-bar {
      background: #ffffff;
      border-bottom: 1px solid var(--color-border);
      padding: 12px 24px;
    }
    .breadcrumb-container {
      max-width: 1280px;
      margin: 0 auto;
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
      color: var(--color-muted);
    }
    .breadcrumb-container a {
      color: var(--color-muted);
      transition: color 0.15s;
    }
    .breadcrumb-container a:hover {
      color: var(--color-primary);
    }
    .breadcrumb-container span.sep {
      color: #cbd5e1;
    }
    .breadcrumb-container span.current {
      color: var(--color-primary);
      font-weight: 600;
    }

    /* Main Product Layout */
    .product-main-wrapper {
      max-width: 1280px;
      margin: 32px auto 64px auto;
      padding: 0 24px;
    }

    .product-grid-layout {
      display: grid;
      grid-template-columns: 52% 48%;
      gap: 48px;
      background: #ffffff;
      border: 1px solid var(--color-border);
      border-radius: var(--radius-lg);
      padding: 40px;
      box-shadow: var(--shadow-sm);
    }

    /* Gallery (Left) */
    .gallery-column {
      display: flex;
      flex-direction: column;
      gap: 18px;
    }
    .main-stage {
      background: #ffffff;
      border: 1px solid var(--color-border);
      border-radius: var(--radius-md);
      position: relative;
      height: 540px;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 32px;
      overflow: hidden;
      cursor: crosshair;
    }
    .main-stage img {
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
      transition: transform 0.3s ease;
      transform-origin: center center;
    }
    .main-stage:hover img {
      transform: scale(1.18);
    }
    .stage-badges {
      position: absolute;
      top: 16px;
      left: 16px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      z-index: 10;
    }
    .badge-tag {
      font-size: 11.5px;
      font-weight: 800;
      letter-spacing: 0.04em;
      padding: 4px 10px;
      border-radius: 4px;
      text-transform: uppercase;
      width: fit-content;
      box-shadow: 0 2px 4px rgba(0,0,0,0.06);
    }
    .badge-select-brand {
      background: #0f172a;
      color: #ffffff;
    }
    .badge-un {
      background: #fef08a;
      color: #854d0e;
      border: 1px solid #fde047;
    }
    .badge-resolution {
      position: absolute;
      bottom: 16px;
      right: 16px;
      background: rgba(15, 23, 42, 0.7);
      backdrop-filter: blur(4px);
      color: #ffffff;
      font-size: 11px;
      font-weight: 700;
      padding: 3px 8px;
      border-radius: 4px;
    }

    /* Thumbnails Row */
    .thumbnails-rack {
      display: flex;
      gap: 14px;
    }
    .thumbnail-card {
      width: 100px;
      height: 100px;
      background: #ffffff;
      border: 2px solid var(--color-border);
      border-radius: var(--radius-sm);
      padding: 8px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.2s;
    }
    .thumbnail-card:hover {
      border-color: var(--color-border-dark);
      transform: translateY(-2px);
    }
    .thumbnail-card.active {
      border-color: var(--color-accent);
      box-shadow: 0 0 0 1px var(--color-accent);
    }
    .thumbnail-card img {
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
    }

    /* Right: Product Buying Actions */
    .info-column {
      display: flex;
      flex-direction: column;
    }
    .vendor-row {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 8px;
    }
    .vendor-title {
      font-size: 13px;
      font-weight: 800;
      color: var(--color-accent);
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .sku-badge {
      font-size: 12.5px;
      color: var(--color-muted);
      background: var(--color-surface);
      padding: 2px 8px;
      border-radius: 4px;
      border: 1px solid var(--color-border);
      font-weight: 600;
    }

    .product-heading {
      font-size: 28px;
      font-weight: 800;
      letter-spacing: -0.025em;
      line-height: 1.25;
      color: var(--color-primary);
      margin-bottom: 16px;
    }

    /* Rating & Reviews Bar */
    .reviews-summary {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 20px;
      font-size: 13.5px;
    }
    .stars {
      color: #f59e0b;
      font-size: 15px;
      letter-spacing: 1px;
    }
    .review-count {
      color: var(--color-muted);
      font-weight: 600;
    }

    /* Pricing Card */
    .price-container {
      background: var(--color-surface);
      border: 1px solid var(--color-border);
      border-radius: var(--radius-md);
      padding: 18px 22px;
      margin-bottom: 20px;
    }
    .price-row {
      display: flex;
      align-items: baseline;
      gap: 12px;
      margin-bottom: 8px;
    }
    .current-price {
      font-size: 34px;
      font-weight: 800;
      color: var(--color-primary);
      letter-spacing: -0.03em;
    }
    .compare-price {
      font-size: 19px;
      color: var(--color-muted);
      text-decoration: line-through;
      font-weight: 500;
    }
    .savings-pill {
      background: #fee2e2;
      color: #b91c1c;
      font-size: 12.5px;
      font-weight: 800;
      padding: 4px 10px;
      border-radius: 4px;
    }
    .tax-shipping-note {
      font-size: 12.5px;
      color: var(--color-muted);
    }

    /* Stock & Dispatch Indicator */
    .inventory-status-bar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 12px 16px;
      background: #f0fdf4;
      border: 1px solid #bbf7d0;
      border-radius: var(--radius-sm);
      margin-bottom: 24px;
    }
    .stock-left {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 14px;
      font-weight: 700;
      color: #15803d;
    }
    .pulse-dot {
      width: 10px;
      height: 10px;
      background: #22c55e;
      border-radius: 50%;
      box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.25);
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0% { box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.5); }
      70% { box-shadow: 0 0 0 8px rgba(34, 197, 94, 0); }
      100% { box-shadow: 0 0 0 0 rgba(34, 197, 94, 0); }
    }
    .dispatch-date {
      font-size: 12.5px;
      color: #166534;
      font-weight: 600;
    }

    /* Bulk Wholesale Pricing Tiers */
    .bulk-pricing-card {
      border: 1px solid var(--color-border);
      border-radius: var(--radius-sm);
      padding: 14px 18px;
      margin-bottom: 24px;
      background: #ffffff;
    }
    .bulk-pricing-header {
      font-size: 13px;
      font-weight: 700;
      color: var(--color-primary);
      margin-bottom: 10px;
      display: flex;
      justify-content: space-between;
    }
    .bulk-tiers-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
    }
    .bulk-tier {
      background: var(--color-surface);
      border: 1px solid var(--color-border);
      border-radius: var(--radius-sm);
      padding: 8px 10px;
      text-align: center;
    }
    .bulk-tier.active-tier {
      border-color: var(--color-primary);
      background: #ffffff;
      box-shadow: var(--shadow-sm);
    }
    .tier-qty {
      font-size: 12px;
      color: var(--color-muted);
      font-weight: 600;
    }
    .tier-price {
      font-size: 14.5px;
      font-weight: 800;
      color: var(--color-primary);
      margin-top: 2px;
    }

    /* Quantity and Action Buttons */
    .purchase-actions-box {
      margin-bottom: 28px;
    }
    .qty-row {
      display: flex;
      align-items: center;
      gap: 16px;
      margin-bottom: 14px;
    }
    .qty-label {
      font-size: 14px;
      font-weight: 700;
      color: var(--color-primary);
    }
    .stepper {
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--color-border-dark);
      border-radius: var(--radius-sm);
      overflow: hidden;
      background: #ffffff;
    }
    .stepper button {
      background: var(--color-surface);
      border: none;
      width: 44px;
      height: 44px;
      font-size: 18px;
      font-weight: 700;
      color: var(--color-primary);
      cursor: pointer;
      transition: background 0.15s;
    }
    .stepper button:hover {
      background: #e2e8f0;
    }
    .stepper input {
      width: 56px;
      height: 44px;
      border: none;
      text-align: center;
      font-family: inherit;
      font-size: 16px;
      font-weight: 700;
      color: var(--color-primary);
      outline: none;
    }
    .quick-qty-pills {
      display: flex;
      gap: 6px;
    }
    .quick-qty-pill {
      font-size: 12px;
      font-weight: 700;
      color: var(--color-text-secondary);
      background: var(--color-surface);
      border: 1px solid var(--color-border);
      padding: 6px 12px;
      border-radius: var(--radius-sm);
      cursor: pointer;
      transition: all 0.15s;
    }
    .quick-qty-pill:hover {
      border-color: var(--color-primary);
      color: var(--color-primary);
    }

    .btn-add-cart {
      width: 100%;
      padding: 16px;
      background: var(--color-primary);
      color: #ffffff;
      font-family: inherit;
      font-size: 16px;
      font-weight: 800;
      letter-spacing: 0.01em;
      border: none;
      border-radius: var(--radius-sm);
      cursor: pointer;
      transition: all 0.2s;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      box-shadow: 0 4px 12px rgba(15, 23, 42, 0.15);
      margin-bottom: 12px;
    }
    .btn-add-cart:hover {
      background: #1e293b;
      transform: translateY(-1px);
      box-shadow: 0 6px 16px rgba(15, 23, 42, 0.22);
    }
    .btn-shoppay {
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
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
    }
    .btn-shoppay:hover {
      background: #4c24e0;
    }

    /* B2B Trust Card */
    .b2b-trust-box {
      background: var(--color-surface);
      border: 1px solid var(--color-border);
      border-radius: var(--radius-md);
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .b2b-trust-item {
      display: flex;
      align-items: flex-start;
      gap: 14px;
    }
    .trust-svg-badge {
      width: 38px;
      height: 38px;
      background: #ffffff;
      border: 1px solid var(--color-border);
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 18px;
      flex-shrink: 0;
      color: var(--color-primary);
    }
    .trust-details h5 {
      font-size: 14px;
      font-weight: 700;
      color: var(--color-primary);
      margin-bottom: 2px;
    }
    .trust-details p {
      font-size: 12.5px;
      color: var(--color-muted);
      line-height: 1.4;
    }

    /* Tabbed Specifications & Content Section */
    .product-tabs-section {
      margin-top: 48px;
      background: #ffffff;
      border: 1px solid var(--color-border);
      border-radius: var(--radius-lg);
      overflow: hidden;
      box-shadow: var(--shadow-sm);
    }

    .tabs-header-bar {
      display: flex;
      background: var(--color-surface);
      border-bottom: 1px solid var(--color-border);
      padding: 0 24px;
      gap: 32px;
    }
    .tab-btn {
      padding: 18px 0;
      font-family: inherit;
      font-size: 15px;
      font-weight: 700;
      color: var(--color-text-secondary);
      background: none;
      border: none;
      border-bottom: 3px solid transparent;
      cursor: pointer;
      transition: all 0.2s;
    }
    .tab-btn:hover {
      color: var(--color-primary);
    }
    .tab-btn.active {
      color: var(--color-accent);
      border-bottom-color: var(--color-accent);
    }

    .tab-pane {
      display: none;
      padding: 40px;
    }
    .tab-pane.active {
      display: block;
    }

    /* Technical Specs Table Design */
    .specs-grid-container {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 32px;
    }
    .specs-table-block h3 {
      font-size: 17px;
      font-weight: 800;
      color: var(--color-primary);
      margin-bottom: 14px;
      padding-bottom: 8px;
      border-bottom: 2px solid var(--color-border);
      letter-spacing: -0.01em;
    }
    .table-clean {
      width: 100%;
      border-collapse: collapse;
      border: 1px solid var(--color-border);
      border-radius: var(--radius-sm);
      overflow: hidden;
    }
    .table-clean tr {
      border-bottom: 1px solid var(--color-border);
    }
    .table-clean tr:last-child {
      border-bottom: none;
    }
    .table-clean tr:nth-child(even) {
      background: #f8fafc;
    }
    .table-clean tr:hover {
      background: #f1f5f9;
    }
    .table-clean td {
      padding: 12px 16px;
      font-size: 14px;
    }
    .table-clean td.prop-name {
      width: 45%;
      font-weight: 700;
      color: #475569;
      border-right: 1px solid var(--color-border);
    }
    .table-clean td.prop-val {
      color: #0f172a;
      font-weight: 500;
    }

    /* Sticky Bottom Purchase Bar */
    .sticky-buy-bar {
      position: fixed;
      bottom: 0;
      left: 0;
      right: 0;
      background: #ffffff;
      border-top: 1px solid var(--color-border);
      padding: 12px 24px;
      box-shadow: 0 -4px 16px rgba(0,0,0,0.08);
      z-index: 100;
      display: flex;
      align-items: center;
      justify-content: space-between;
      transform: translateY(100%);
      transition: transform 0.3s ease;
    }
    .sticky-buy-bar.visible {
      transform: translateY(0);
    }
    .sticky-bar-content {
      max-width: 1280px;
      margin: 0 auto;
      width: 100%;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .sticky-left {
      display: flex;
      align-items: center;
      gap: 16px;
    }
    .sticky-thumb {
      width: 48px;
      height: 48px;
      border: 1px solid var(--color-border);
      border-radius: 4px;
      padding: 4px;
    }
    .sticky-thumb img {
      width: 100%;
      height: 100%;
      object-fit: contain;
    }
    .sticky-title {
      font-size: 14px;
      font-weight: 700;
      color: var(--color-primary);
      max-width: 500px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .sticky-sku {
      font-size: 12px;
      color: var(--color-muted);
    }
    .sticky-right {
      display: flex;
      align-items: center;
      gap: 16px;
    }
    .sticky-price {
      font-size: 20px;
      font-weight: 800;
      color: var(--color-primary);
    }

    /* Footer */
    footer {
      background: #0f172a;
      color: #ffffff;
      padding: 48px 24px 32px 24px;
      margin-top: 80px;
    }
    .footer-inner {
      max-width: 1280px;
      margin: 0 auto;
      text-align: center;
      font-size: 13.5px;
      color: #94a3b8;
    }

    /* Responsive */
    @media (max-width: 960px) {
      .product-grid-layout {
        grid-template-columns: 1fr;
        padding: 24px;
      }
      .specs-grid-container {
        grid-template-columns: 1fr;
      }
      .main-stage {
        height: 380px;
      }
      .sticky-title {
        max-width: 200px;
      }
    }
  </style>
</head>
<body>

  <!-- Top Announcement Bar -->
  <div class="announcement-bar">
    <span class="badge-free">B2B Commercial</span>
    <span>🚚 Free Freight on Orders Over $50 | 500 Units In Stock & Ready for Same-Day Dispatch</span>
  </div>

  <!-- Header -->
  <header>
    <div class="header-container">
      <a href="index.html" class="brand-logo">
        HATHORO<span class="dot"></span>
      </a>

      <div class="search-bar">
        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path></svg>
        <input type="text" placeholder="Search 108+ industrial drums, poly barrels, spill containment...">
      </div>

      <div class="header-right">
        <a href="index.html" class="header-link">Catalog Home</a>
        <a href="index.html#drums" class="header-link">All 108 Drums</a>
        <a href="tel:7276098404" class="header-link">
          <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z"></path></svg>
          (727) 609-8404
        </a>
        <a href="#cart" class="cart-btn" onclick="openCart()">
          <svg width="17" height="17" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z"></path></svg>
          Cart <span class="cart-pill" id="cartCount">0</span>
        </a>
      </div>
    </div>
  </header>

  <!-- Breadcrumbs -->
  <div class="breadcrumb-bar">
    <div class="breadcrumb-container">
      <a href="index.html">Home</a>
      <span class="sep">/</span>
      <a href="index.html#drums">Industrial Drums & Containment</a>
      <span class="sep">/</span>
      <span class="current">Hathoro Select 30-Gallon Closed-Head Heavy-Duty Steel Transport Drum</span>
    </div>
  </div>

  <!-- Product Stage Layout -->
  <div class="product-main-wrapper">
    <div class="product-grid-layout">
      
      <!-- Gallery Column -->
      <div class="gallery-column">
        <div class="main-stage" id="stageContainer">
          <div class="stage-badges">
            <span class="badge-tag badge-select-brand">Hathoro Select™</span>
            <span class="badge-tag badge-un">UN Certified 1A1/Y1.5/300</span>
          </div>
          <img id="mainDisplayImage" src="images/G1877111/image_01_1000x1000.jpg" alt="Hathoro Select 30-Gallon Closed-Head Steel Transport Drum">
          <span class="badge-resolution">1000 × 1000 Retina HD</span>
        </div>

        <div class="thumbnails-rack">
          <div class="thumbnail-card active" onclick="changeImage('images/G1877111/image_01_1000x1000.jpg', this)">
            <img src="images/G1877111/image_01_1000x1000.jpg" alt="Primary Angle">
          </div>
          <div class="thumbnail-card" onclick="changeImage('images/G1877111/image_02_1000x1000.jpg', this)">
            <img src="images/G1877111/image_02_1000x1000.jpg" alt="Top Bungs View">
          </div>
          <div class="thumbnail-card" onclick="changeImage('images/G1877111/image_03_1000x1000.jpg', this)">
            <img src="images/G1877111/image_03_1000x1000.jpg" alt="Full Profile View">
          </div>
        </div>
      </div>

      <!-- Information & Purchasing Column -->
      <div class="info-column">
        <div class="vendor-row">
          <span class="vendor-title">HATHORO SELECT™</span>
          <span class="sku-badge">SKU: H1877111</span>
        </div>

        <h1 class="product-heading">Hathoro Select 30-Gallon Closed-Head Heavy-Duty Steel Transport Drum - Black</h1>

        <div class="reviews-summary">
          <div class="stars">★★★★★</div>
          <span class="review-count">4.9 / 5.0 (28 Commercial Reviews)</span>
          <span style="color: #cbd5e1;">•</span>
          <span style="color: var(--color-success); font-weight: 700;">DOT 49 CFR Certified</span>
        </div>

        <!-- Pricing Block -->
        <div class="price-container">
          <div class="price-row">
            <span class="current-price" id="unitPriceDisplay">$151.99</span>
            <span class="compare-price">$174.79</span>
            <span class="savings-pill">Save $22.80 (15% Off)</span>
          </div>
          <div class="tax-shipping-note">Free standard freight shipping on this order. Tax calculated at checkout.</div>
        </div>

        <!-- Stock & Dispatch -->
        <div class="inventory-status-bar">
          <div class="stock-left">
            <span class="pulse-dot"></span>
            <span>IN STOCK (500 units available)</span>
          </div>
          <div class="dispatch-date">⚡ Order in 1h 42m for today's dispatch</div>
        </div>

        <!-- Bulk Wholesale Tiers -->
        <div class="bulk-pricing-card">
          <div class="bulk-pricing-header">
            <span>Tiered Facility Pricing</span>
            <span style="color: var(--color-accent); font-weight: 700;">Volume Savings</span>
          </div>
          <div class="bulk-tiers-grid">
            <div class="bulk-tier active-tier" id="tier1" onclick="setQty(1)">
              <div class="tier-qty">1 – 4 Units</div>
              <div class="tier-price">$151.99 / ea</div>
            </div>
            <div class="bulk-tier" id="tier2" onclick="setQty(5)">
              <div class="tier-qty">5 – 9 Units</div>
              <div class="tier-price">$144.39 / ea <span style="font-size:11px; color:#16a34a;">(-5%)</span></div>
            </div>
            <div class="bulk-tier" id="tier3" onclick="setQty(10)">
              <div class="tier-qty">10+ Units</div>
              <div class="tier-price">$136.79 / ea <span style="font-size:11px; color:#16a34a;">(-10%)</span></div>
            </div>
          </div>
        </div>

        <!-- Quantity & Add to Cart -->
        <div class="purchase-actions-box">
          <div class="qty-row">
            <span class="qty-label">Quantity:</span>
            <div class="stepper">
              <button type="button" onclick="adjustQty(-1)">−</button>
              <input type="text" id="orderQty" value="1" readonly>
              <button type="button" onclick="adjustQty(1)">+</button>
            </div>
            <div class="quick-qty-pills">
              <span class="quick-qty-pill" onclick="setQty(1)">1</span>
              <span class="quick-qty-pill" onclick="setQty(5)">5 (Tier 1)</span>
              <span class="quick-qty-pill" onclick="setQty(10)">10 (Tier 2)</span>
            </div>
          </div>

          <button class="btn-add-cart" onclick="addToCart()">
            <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z"></path></svg>
            <span id="btnCartText">Add to Cart — $151.99</span>
          </button>

          <button class="btn-shoppay" onclick="addToCart()">
            Buy with <span style="font-weight: 900; letter-spacing: -0.04em; font-size: 17px;">Shop Pay</span>
          </button>
        </div>

        <!-- B2B Trust Box -->
        <div class="b2b-trust-box">
          <div class="b2b-trust-item">
            <div class="trust-svg-badge">🚚</div>
            <div class="trust-details">
              <h5>Fast Freight Dispatch</h5>
              <p>Commercial orders placed by 3:00 PM CT ship same business day via FedEx Freight.</p>
            </div>
          </div>

          <div class="b2b-trust-item">
            <div class="trust-svg-badge">🛡️</div>
            <div class="trust-details">
              <h5>Hathoro Quality Guarantee</h5>
              <p>UN rated 1A1/Y1.5/300 pressure tested. Unlined with rust inhibitor for high reliability.</p>
            </div>
          </div>

          <div class="b2b-trust-item">
            <div class="trust-svg-badge">🏢</div>
            <div class="trust-details">
              <h5>B2B Purchase Orders & Net Terms</h5>
              <p>Corporate Net-30 accounts available for registered facilities and logistics depots.</p>
            </div>
          </div>
        </div>

      </div>

    </div>

    <!-- Tabbed Specifications & Content -->
    <div class="product-tabs-section">
      <div class="tabs-header-bar">
        <button class="tab-btn active" onclick="switchTab('specs', this)">Technical Specifications</button>
        <button class="tab-btn" onclick="switchTab('overview', this)">Product Overview</button>
        <button class="tab-btn" onclick="switchTab('shipping', this)">Freight & Delivery</button>
        <button class="tab-btn" onclick="switchTab('warranty', this)">Warranty & Compliance</button>
      </div>

      <!-- Tab 1: Specs -->
      <div class="tab-pane active" id="tab-specs">
        <div class="specs-grid-container">
          <div class="specs-table-block">
            <h3>Dimensions & Material Details</h3>
            <table class="table-clean">
              <tbody>
                <tr>
                  <td class="prop-name">Primary Drum Material</td>
                  <td class="prop-val">Carbon Steel</td>
                </tr>
                <tr>
                  <td class="prop-name">Drum Volume Capacity</td>
                  <td class="prop-val">30 gal (113.5 L)</td>
                </tr>
                <tr>
                  <td class="prop-name">Drum Head Type</td>
                  <td class="prop-val">Closed Head (Tight-Head)</td>
                </tr>
                <tr>
                  <td class="prop-name">Body Material Gauge</td>
                  <td class="prop-val">20 ga (0.9 mm)</td>
                </tr>
                <tr>
                  <td class="prop-name">Top & Bottom Material Gauge</td>
                  <td class="prop-val">20 ga (0.9 mm)</td>
                </tr>
                <tr>
                  <td class="prop-name">Overall Height</td>
                  <td class="prop-val">29 1/2 in</td>
                </tr>
                <tr>
                  <td class="prop-name">Top Outside Diameter</td>
                  <td class="prop-val">18 3/4 in</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="specs-table-block">
            <h3>UN Ratings & Closures</h3>
            <table class="table-clean">
              <tbody>
                <tr>
                  <td class="prop-name">UN Rating Liquids</td>
                  <td class="prop-val"><strong>1A1/Y1.5/300</strong></td>
                </tr>
                <tr>
                  <td class="prop-name">UN Rating Solids</td>
                  <td class="prop-val">Not Rated</td>
                </tr>
                <tr>
                  <td class="prop-name">Number of Bung Openings</td>
                  <td class="prop-val">2 (Includes 2" NPS & 3/4" Bungs)</td>
                </tr>
                <tr>
                  <td class="prop-name">Cover Color / Body Color</td>
                  <td class="prop-val">White Cover / Black Body</td>
                </tr>
                <tr>
                  <td class="prop-name">Interior Lining</td>
                  <td class="prop-val">Unlined with Rust Inhibitor</td>
                </tr>
                <tr>
                  <td class="prop-name">Empty Drum Weight</td>
                  <td class="prop-val">34.0 lbs (15.4 kg)</td>
                </tr>
                <tr>
                  <td class="prop-name">DOT Compliance</td>
                  <td class="prop-val">DOT 49 CFR 178.504 Compliant</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- Tab 2: Overview -->
      <div class="tab-pane" id="tab-overview">
        <h3 style="font-size: 18px; font-weight: 800; margin-bottom: 12px; color: var(--color-primary);">Engineered for Secure Chemical & Liquid Transport</h3>
        <p style="font-size: 15.5px; line-height: 1.7; color: #334155; margin-bottom: 24px;">
          The Hathoro Select™ 30-Gallon Closed-Head Heavy-Duty Steel Transport Drum is manufactured specifically for safe handling, transport, and containment of industrial solvents, lubricants, coolants, non-corrosive liquids, and regulated waste. Built with precision cold-rolled 20-gauge carbon steel on the body and heads, it meets stringent UN packaging group II and III standards.
        </p>
        <h4 style="font-size: 15px; font-weight: 700; margin-bottom: 12px;">Key Advantages:</h4>
        <ul style="padding-left: 20px; line-height: 1.8; color: #334155; font-size: 14.5px;">
          <li><strong>Dual Tri-Sure Bungs:</strong> Pre-fitted with a 2-inch national pipe straight (NPS) bung and a 3/4-inch vent bung for controlled pouring, pumping, and faucet mounting.</li>
          <li><strong>Rust Inhibitor Protection:</strong> Interior is treated with a specialized rust-preventive wash to prevent oxidation during storage prior to initial filling.</li>
          <li><strong>High-Durability Finish:</strong> Exterior baked enamel resists weather, UV exposure, and typical warehouse transit abrasion.</li>
          <li><strong>Pallet Optimized:</strong> Standard 18 3/4" diameter allows 4 drums to fit squarely on standard 48×48 industrial pallets.</li>
        </ul>
      </div>

      <!-- Tab 3: Shipping -->
      <div class="tab-pane" id="tab-shipping">
        <h3 style="font-size: 18px; font-weight: 800; margin-bottom: 12px; color: var(--color-primary);">Freight & Delivery Details</h3>
        <p style="font-size: 15px; line-height: 1.7; color: #334155; margin-bottom: 16px;">
          All drums ship directly from regional industrial fulfillment centers in protective heavy-gauge shrink wrap and banded pallets to prevent shipping dents.
        </p>
        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-top: 20px;">
          <div style="background: var(--color-surface); padding: 18px; border-radius: 8px; border: 1px solid var(--color-border);">
            <div style="font-size: 20px; margin-bottom: 6px;">⚡</div>
            <div style="font-weight: 700; margin-bottom: 4px;">Same-Day Cutoff</div>
            <div style="font-size: 13px; color: var(--color-muted);">Orders placed Monday–Friday before 3:00 PM CT ship the exact same day.</div>
          </div>
          <div style="background: var(--color-surface); padding: 18px; border-radius: 8px; border: 1px solid var(--color-border);">
            <div style="font-size: 20px; margin-bottom: 6px;">🚛</div>
            <div style="font-weight: 700; margin-bottom: 4px;">Liftgate Available</div>
            <div style="font-size: 13px; color: var(--color-muted);">Freight delivery options include commercial dock or residential liftgate service.</div>
          </div>
          <div style="background: var(--color-surface); padding: 18px; border-radius: 8px; border: 1px solid var(--color-border);">
            <div style="font-size: 20px; margin-bottom: 6px;">📦</div>
            <div style="font-weight: 700; margin-bottom: 4px;">Free Shipping</div>
            <div style="font-size: 13px; color: var(--color-muted);">This product qualifies for 100% free delivery anywhere in the contiguous USA.</div>
          </div>
        </div>
      </div>

      <!-- Tab 4: Warranty -->
      <div class="tab-pane" id="tab-warranty">
        <h3 style="font-size: 18px; font-weight: 800; margin-bottom: 12px; color: var(--color-primary);">Hathoro Commercial Guarantee</h3>
        <p style="font-size: 15px; line-height: 1.7; color: #334155; margin-bottom: 16px;">
          Every Hathoro Select™ product is guaranteed free of manufacturing defects, pressure tested at the factory, and warranted for 12 months under standard commercial handling. If your shipment arrives damaged or out of compliance, we will dispatch an immediate replacement or issue a 100% refund.
        </p>
      </div>

    </div>

  </div>

  <!-- Sticky Purchase Bar on Scroll -->
  <div class="sticky-buy-bar" id="stickyBar">
    <div class="sticky-bar-content">
      <div class="sticky-left">
        <div class="sticky-thumb">
          <img src="images/G1877111/image_01_1000x1000.jpg" alt="Thumbnail">
        </div>
        <div>
          <div class="sticky-title">Hathoro Select 30-Gallon Closed-Head Steel Transport Drum</div>
          <div class="sticky-sku">SKU: H1877111 | In Stock (500 units)</div>
        </div>
      </div>

      <div class="sticky-right">
        <div class="sticky-price" id="stickyPriceDisplay">$151.99</div>
        <button class="btn-add-cart" style="width: auto; padding: 10px 22px; margin: 0;" onclick="addToCart()">Add to Cart</button>
      </div>
    </div>
  </div>

  <!-- Footer -->
  <footer>
    <div class="footer-inner">
      <p style="margin-bottom: 8px;">&copy; 2026 Hathoro.com | Industrial Supplies, Spill Containment & Warehouse Equipment.</p>
      <p>Questions? Call Customer Support: (727) 609-8404 or email sales@hathoro.com</p>
    </div>
  </footer>

  <script>
    var currentBasePrice = 151.99;
    var currentQty = 1;
    var cartItems = 0;

    function changeImage(src, cardEl) {
      document.getElementById('mainDisplayImage').src = src;
      document.querySelectorAll('.thumbnail-card').forEach(function(c) {
        c.classList.remove('active');
      });
      cardEl.classList.add('active');
    }

    function adjustQty(delta) {
      var newQty = currentQty + delta;
      if (newQty >= 1 && newQty <= 500) {
        setQty(newQty);
      }
    }

    function setQty(qty) {
      currentQty = qty;
      document.getElementById('orderQty').value = currentQty;

      // Calculate Tier Discount
      var unitPrice = currentBasePrice;
      if (currentQty >= 10) {
        unitPrice = currentBasePrice * 0.90; // 10% off
        highlightTier('tier3');
      } else if (currentQty >= 5) {
        unitPrice = currentBasePrice * 0.95; // 5% off
        highlightTier('tier2');
      } else {
        highlightTier('tier1');
      }

      var total = (unitPrice * currentQty).toFixed(2);
      document.getElementById('unitPriceDisplay').innerText = '$' + unitPrice.toFixed(2);
      document.getElementById('btnCartText').innerText = 'Add ' + currentQty + ' to Cart — $' + total;
      document.getElementById('stickyPriceDisplay').innerText = '$' + unitPrice.toFixed(2);
    }

    function highlightTier(activeId) {
      ['tier1', 'tier2', 'tier3'].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) {
          if (id === activeId) {
            el.classList.add('active-tier');
          } else {
            el.classList.remove('active-tier');
          }
        }
      });
    }

    function switchTab(tabId, btn) {
      document.querySelectorAll('.tab-btn').forEach(function(b) {
        b.classList.remove('active');
      });
      document.querySelectorAll('.tab-pane').forEach(function(p) {
        p.classList.remove('active');
      });
      btn.classList.add('active');
      document.getElementById('tab-' + tabId).classList.add('active');
    }

    function addToCart() {
      cartItems += currentQty;
      document.getElementById('cartCount').innerText = cartItems;
      alert('✅ Successfully added ' + currentQty + ' unit(s) of Hathoro Select 30-Gallon Drum to your cart!');
    }

    function openCart() {
      alert('Shopping Cart: ' + cartItems + ' item(s). Ready for checkout!');
    }

    // Sticky buy bar observer on scroll
    window.addEventListener('scroll', function() {
      var bar = document.getElementById('stickyBar');
      if (window.scrollY > 450) {
        bar.classList.add('visible');
      } else {
        bar.classList.remove('visible');
      }
    });
  </script>

</body>
</html>
"""

with open('preview/product.html', 'w', encoding='utf-8') as f:
    f.write(product_html)

print("Updated preview/product.html with ultra-premium design successfully!")
