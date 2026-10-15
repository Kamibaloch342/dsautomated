import os
import re

# 1. Generate the ultra-minimalist, smoothly animated product.html
html_content = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Hathoro Select 30-Gallon Closed-Head Steel Transport Drum | Hathoro</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:ital,wght@0,400..700;1,400..700&display=swap" rel="stylesheet">
  <style>
    :root {
      --font-sans: 'Instrument Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      --ease-apple: cubic-bezier(0.16, 1, 0.3, 1);
      --ease-out: cubic-bezier(0.22, 1, 0.36, 1);
      
      --c-black: #09090b;
      --c-dark: #18181b;
      --c-muted: #71717a;
      --c-light-muted: #a1a1aa;
      --c-border: #f4f4f5;
      --c-border-strong: #e4e4e7;
      --c-surface: #fafafa;
      --c-white: #ffffff;
      
      --c-red: #f74143;
      --c-red-soft: #fff1f2;
      --c-green: #10b981;
      --c-green-soft: #ecfdf5;
      
      --radius-sm: 8px;
      --radius-md: 12px;
      --radius-lg: 20px;
      --radius-pill: 9999px;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: var(--font-sans);
      color: var(--c-black);
      background-color: #ffffff;
      -webkit-font-smoothing: antialiased;
      line-height: 1.5;
      overflow-x: hidden;
    }

    a { color: inherit; text-decoration: none; }

    /* Minimalist Top Utility Bar */
    .top-notice {
      background: #fafafa;
      border-bottom: 1px solid var(--c-border);
      color: var(--c-muted);
      font-size: 13px;
      font-weight: 500;
      padding: 9px 24px;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 16px;
      transition: all 0.3s var(--ease-apple);
    }
    .top-notice strong {
      color: var(--c-black);
    }
    .notice-dot {
      width: 6px;
      height: 6px;
      background: var(--c-green);
      border-radius: 50%;
      display: inline-block;
      box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.2);
    }

    /* Minimalist Header */
    header {
      background: rgba(255, 255, 255, 0.9);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border-bottom: 1px solid var(--c-border);
      position: sticky;
      top: 0;
      z-index: 90;
      transition: background 0.3s ease;
    }
    .header-inner {
      max-width: 1240px;
      margin: 0 auto;
      padding: 18px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 32px;
    }
    .brand {
      font-size: 22px;
      font-weight: 800;
      letter-spacing: -0.04em;
      color: var(--c-black);
      display: flex;
      align-items: center;
      gap: 4px;
    }
    .brand-dot {
      width: 7px;
      height: 7px;
      background: var(--c-red);
      border-radius: 50%;
    }
    
    .nav-links {
      display: flex;
      align-items: center;
      gap: 28px;
    }
    .nav-link {
      font-size: 14px;
      font-weight: 500;
      color: var(--c-muted);
      transition: color 0.2s var(--ease-apple);
      position: relative;
    }
    .nav-link:hover {
      color: var(--c-black);
    }
    .nav-link.active {
      color: var(--c-black);
      font-weight: 600;
    }
    
    .header-actions {
      display: flex;
      align-items: center;
      gap: 18px;
    }
    .cart-pill-btn {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      background: var(--c-black);
      color: #ffffff;
      padding: 8px 16px;
      border-radius: var(--radius-pill);
      font-size: 13.5px;
      font-weight: 600;
      transition: all 0.25s var(--ease-apple);
    }
    .cart-pill-btn:hover {
      transform: translateY(-1px);
      box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    }

    /* Minimal Breadcrumb */
    .breadcrumb-row {
      max-width: 1240px;
      margin: 0 auto;
      padding: 24px 24px 0 24px;
      font-size: 13px;
      color: var(--c-light-muted);
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .breadcrumb-row a {
      color: var(--c-muted);
      transition: color 0.15s;
    }
    .breadcrumb-row a:hover {
      color: var(--c-black);
    }
    .breadcrumb-row .sep {
      color: #e4e4e7;
    }

    /* Product Grid Layout */
    .product-wrap {
      max-width: 1240px;
      margin: 28px auto 80px auto;
      padding: 0 24px;
      display: grid;
      grid-template-columns: 54% 46%;
      gap: 64px;
      align-items: start;
    }

    /* Left: Minimal Gallery */
    .gallery-zone {
      position: sticky;
      top: 100px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }

    .main-canvas {
      background: #fafafa;
      border: 1px solid var(--c-border);
      border-radius: var(--radius-lg);
      height: 560px;
      position: relative;
      overflow: hidden;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 40px;
      transition: border-color 0.3s ease;
    }
    .main-canvas:hover {
      border-color: var(--c-border-strong);
    }

    .main-canvas img {
      max-width: 90%;
      max-height: 90%;
      object-fit: contain;
      mix-blend-mode: multiply;
      transition: transform 0.5s var(--ease-apple), opacity 0.25s ease;
      transform-origin: center center;
    }
    .main-canvas:hover img {
      transform: scale(1.12);
    }

    .canvas-tag {
      position: absolute;
      top: 18px;
      left: 18px;
      background: #ffffff;
      border: 1px solid var(--c-border-strong);
      color: var(--c-dark);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      padding: 5px 12px;
      border-radius: var(--radius-pill);
      box-shadow: 0 2px 6px rgba(0,0,0,0.03);
    }

    .thumbs-strip {
      display: flex;
      gap: 12px;
    }
    .thumb-slot {
      width: 88px;
      height: 88px;
      background: #fafafa;
      border: 1px solid var(--c-border);
      border-radius: var(--radius-md);
      padding: 10px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.25s var(--ease-apple);
    }
    .thumb-slot img {
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
      mix-blend-mode: multiply;
      transition: transform 0.25s var(--ease-apple);
    }
    .thumb-slot:hover {
      border-color: var(--c-black);
      transform: translateY(-2px);
    }
    .thumb-slot:hover img {
      transform: scale(1.06);
    }
    .thumb-slot.active {
      border-color: var(--c-black);
      background: #ffffff;
      box-shadow: 0 0 0 1px var(--c-black);
    }

    /* Right: Product Actions & Information */
    .info-zone {
      display: flex;
      flex-direction: column;
      animation: fadeIn 0.6s var(--ease-apple) both;
    }

    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(12px); }
      to { opacity: 1; transform: translateY(0); }
    }

    .vendor-badge-minimal {
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--c-muted);
      margin-bottom: 8px;
    }

    .product-title-minimal {
      font-size: 32px;
      font-weight: 700;
      letter-spacing: -0.03em;
      line-height: 1.2;
      color: var(--c-black);
      margin-bottom: 12px;
    }

    .sku-specs-subline {
      font-size: 13.5px;
      color: var(--c-muted);
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 24px;
    }
    .sku-specs-subline span.dot-sep {
      color: #d4d4d8;
    }

    /* Price Section */
    .price-strip {
      display: flex;
      align-items: baseline;
      gap: 14px;
      padding: 20px 0;
      border-top: 1px solid var(--c-border);
      border-bottom: 1px solid var(--c-border);
      margin-bottom: 24px;
    }
    .price-active {
      font-size: 36px;
      font-weight: 800;
      letter-spacing: -0.04em;
      color: var(--c-black);
    }
    .price-original {
      font-size: 18px;
      color: var(--c-light-muted);
      text-decoration: line-through;
      font-weight: 400;
    }
    .save-pill {
      background: var(--c-red-soft);
      color: var(--c-red);
      font-size: 12px;
      font-weight: 700;
      padding: 3px 10px;
      border-radius: var(--radius-pill);
      letter-spacing: 0.02em;
    }

    /* Stock Indicator */
    .stock-indicator {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 13.5px;
      font-weight: 600;
      color: var(--c-green);
      margin-bottom: 28px;
    }
    .live-dot {
      width: 8px;
      height: 8px;
      background: var(--c-green);
      border-radius: 50%;
      position: relative;
    }
    .live-dot::after {
      content: "";
      position: absolute;
      inset: -3px;
      border-radius: 50%;
      border: 1.5px solid var(--c-green);
      animation: pulseAnim 2s infinite var(--ease-apple);
    }
    @keyframes pulseAnim {
      0% { transform: scale(0.8); opacity: 1; }
      100% { transform: scale(2.2); opacity: 0; }
    }

    /* Minimalist Stepper & Action Controls */
    .action-panel {
      margin-bottom: 36px;
    }

    .stepper-row {
      display: flex;
      align-items: center;
      gap: 16px;
      margin-bottom: 16px;
    }
    .stepper-label {
      font-size: 13.5px;
      font-weight: 600;
      color: var(--c-muted);
    }

    .minimal-stepper {
      display: inline-flex;
      align-items: center;
      background: #fafafa;
      border: 1px solid var(--c-border);
      border-radius: var(--radius-pill);
      padding: 4px 6px;
      transition: border-color 0.2s;
    }
    .minimal-stepper:focus-within {
      border-color: var(--c-black);
    }
    .minimal-stepper button {
      background: none;
      border: none;
      width: 32px;
      height: 32px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 16px;
      color: var(--c-black);
      cursor: pointer;
      transition: background 0.15s var(--ease-apple);
    }
    .minimal-stepper button:hover {
      background: #f4f4f5;
    }
    .minimal-stepper input {
      width: 44px;
      border: none;
      background: none;
      text-align: center;
      font-family: inherit;
      font-size: 14.5px;
      font-weight: 700;
      color: var(--c-black);
      outline: none;
    }

    .preset-pill {
      font-size: 12.5px;
      font-weight: 600;
      color: var(--c-muted);
      background: #fafafa;
      border: 1px solid var(--c-border);
      padding: 6px 14px;
      border-radius: var(--radius-pill);
      cursor: pointer;
      transition: all 0.2s var(--ease-apple);
    }
    .preset-pill:hover, .preset-pill.active {
      color: var(--c-black);
      border-color: var(--c-black);
      background: #ffffff;
    }

    /* Buttons */
    .btn-submit-main {
      width: 100%;
      padding: 16px;
      background: var(--c-black);
      color: #ffffff;
      font-family: inherit;
      font-size: 15px;
      font-weight: 700;
      letter-spacing: -0.01em;
      border: none;
      border-radius: var(--radius-md);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      transition: all 0.25s var(--ease-apple);
      margin-bottom: 12px;
    }
    .btn-submit-main:hover {
      background: var(--c-dark);
      transform: translateY(-2px);
      box-shadow: 0 8px 24px rgba(0,0,0,0.12);
    }
    .btn-submit-main:active {
      transform: scale(0.985);
    }

    .btn-shoppay-minimal {
      width: 100%;
      padding: 15px;
      background: #5a31f4;
      color: #ffffff;
      font-family: inherit;
      font-size: 14.5px;
      font-weight: 600;
      border: none;
      border-radius: var(--radius-md);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      transition: all 0.25s var(--ease-apple);
    }
    .btn-shoppay-minimal:hover {
      background: #4f27e6;
      transform: translateY(-1px);
    }
    .btn-shoppay-minimal:active {
      transform: scale(0.985);
    }

    /* Minimalist Trust Grid */
    .trust-strip-minimal {
      display: grid;
      grid-template-columns: 1fr;
      gap: 16px;
      padding: 24px;
      background: #fafafa;
      border: 1px solid var(--c-border);
      border-radius: var(--radius-md);
    }
    .trust-cell {
      display: flex;
      align-items: flex-start;
      gap: 14px;
    }
    .trust-icon-box {
      width: 32px;
      height: 32px;
      display: flex;
      align-items: center;
      justify-content: center;
      background: #ffffff;
      border: 1px solid var(--c-border);
      border-radius: var(--radius-sm);
      font-size: 16px;
      flex-shrink: 0;
    }
    .trust-desc h5 {
      font-size: 13.5px;
      font-weight: 700;
      color: var(--c-black);
      margin-bottom: 2px;
    }
    .trust-desc p {
      font-size: 12.5px;
      color: var(--c-muted);
      line-height: 1.4;
    }

    /* Full Width Specs & Tab System */
    .specs-section {
      max-width: 1240px;
      margin: 40px auto 100px auto;
      padding: 0 24px;
    }

    .segmented-control {
      display: inline-flex;
      background: #f4f4f5;
      padding: 4px;
      border-radius: var(--radius-pill);
      gap: 4px;
      margin-bottom: 32px;
    }
    .segment-btn {
      background: none;
      border: none;
      font-family: inherit;
      font-size: 13.5px;
      font-weight: 600;
      color: var(--c-muted);
      padding: 8px 20px;
      border-radius: var(--radius-pill);
      cursor: pointer;
      transition: all 0.25s var(--ease-apple);
    }
    .segment-btn:hover {
      color: var(--c-black);
    }
    .segment-btn.active {
      background: #ffffff;
      color: var(--c-black);
      box-shadow: 0 2px 6px rgba(0,0,0,0.06);
    }

    .pane-content {
      display: none;
      animation: tabFadeIn 0.35s var(--ease-apple) both;
    }
    .pane-content.active {
      display: block;
    }
    @keyframes tabFadeIn {
      from { opacity: 0; transform: translateY(8px); }
      to { opacity: 1; transform: translateY(0); }
    }

    /* Minimal Spec Grid */
    .spec-table-clean {
      width: 100%;
      border-collapse: separate;
      border-spacing: 0;
      border: 1px solid var(--c-border);
      border-radius: var(--radius-md);
      overflow: hidden;
      background: #ffffff;
    }
    .spec-table-clean tr {
      border-bottom: 1px solid var(--c-border);
      transition: background 0.15s ease;
    }
    .spec-table-clean tr:last-child {
      border-bottom: none;
    }
    .spec-table-clean tr:hover {
      background: #fafafa;
    }
    .spec-table-clean td {
      padding: 14px 20px;
      font-size: 14px;
      border-bottom: 1px solid var(--c-border);
    }
    .spec-table-clean tr:last-child td {
      border-bottom: none;
    }
    .spec-table-clean td.prop {
      width: 38%;
      color: var(--c-muted);
      font-weight: 500;
      border-right: 1px solid var(--c-border);
    }
    .spec-table-clean td.val {
      color: var(--c-black);
      font-weight: 600;
    }

    /* Floating Glassmorphic Sticky Buy Pill (Apple/Linear Style) */
    .floating-buy-pill {
      position: fixed;
      bottom: 28px;
      left: 50%;
      transform: translateX(-50%) translateY(140%);
      background: rgba(255, 255, 255, 0.88);
      backdrop-filter: blur(24px);
      -webkit-backdrop-filter: blur(24px);
      border: 1px solid rgba(0, 0, 0, 0.08);
      border-radius: var(--radius-pill);
      padding: 8px 10px 8px 20px;
      box-shadow: 0 20px 40px -10px rgba(0,0,0,0.14), 0 0 0 1px rgba(0,0,0,0.04);
      z-index: 100;
      display: flex;
      align-items: center;
      gap: 20px;
      transition: transform 0.4s var(--ease-apple);
    }
    .floating-buy-pill.visible {
      transform: translateX(-50%) translateY(0);
    }
    .pill-left {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .pill-thumb {
      width: 36px;
      height: 36px;
      border-radius: 50%;
      background: #fafafa;
      border: 1px solid var(--c-border);
      padding: 3px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .pill-thumb img {
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
    }
    .pill-text {
      font-size: 13.5px;
      font-weight: 700;
      color: var(--c-black);
      white-space: nowrap;
    }
    .pill-price {
      font-size: 15px;
      font-weight: 800;
      color: var(--c-black);
    }
    .pill-btn-action {
      background: var(--c-black);
      color: #ffffff;
      border: none;
      border-radius: var(--radius-pill);
      padding: 10px 20px;
      font-family: inherit;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
      transition: transform 0.2s var(--ease-apple), background 0.2s ease;
    }
    .pill-btn-action:hover {
      background: var(--c-dark);
      transform: scale(1.03);
    }

    /* Minimalist Footer */
    footer {
      border-top: 1px solid var(--c-border);
      padding: 48px 24px;
      background: #ffffff;
      color: var(--c-muted);
      font-size: 13px;
      text-align: center;
    }
    .footer-content {
      max-width: 1240px;
      margin: 0 auto;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    @media (max-width: 900px) {
      .product-wrap {
        grid-template-columns: 1fr;
        gap: 36px;
      }
      .main-canvas {
        height: 400px;
      }
      .floating-buy-pill {
        width: calc(100% - 32px);
        justify-content: space-between;
      }
    }
  </style>
</head>
<body>

  <!-- Top Announcement -->
  <div class="top-notice">
    <span><span class="notice-dot"></span> <strong>500 Units in Stock</strong> &mdash; Free Shipping on Orders Over $50</span>
    <span style="color: #d4d4d8;">|</span>
    <span>Same-Day Freight Dispatch for Orders by 3:00 PM CT</span>
  </div>

  <!-- Header -->
  <header>
    <div class="header-inner">
      <a href="index.html" class="brand">
        HATHORO<span class="brand-dot"></span>
      </a>

      <div class="nav-links">
        <a href="index.html" class="nav-link">Catalog Home</a>
        <a href="index.html#drums" class="nav-link active">Industrial Drums & Containment</a>
        <a href="#specs" class="nav-link">Specifications</a>
        <a href="tel:7276098404" class="nav-link">(727) 609-8404</a>
      </div>

      <div class="header-actions">
        <a href="#cart" class="cart-pill-btn" onclick="triggerAddToCart()">
          <svg width="15" height="15" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z"></path></svg>
          Cart <span id="cartCountBadge">0</span>
        </a>
      </div>
    </div>
  </header>

  <!-- Breadcrumb -->
  <div class="breadcrumb-row">
    <a href="index.html">Home</a>
    <span class="sep">/</span>
    <a href="index.html#drums">Industrial Drums</a>
    <span class="sep">/</span>
    <span style="color: var(--c-black); font-weight: 500;">Hathoro Select 30-Gallon Steel Transport Drum</span>
  </div>

  <!-- Main Product View -->
  <main class="product-wrap">

    <!-- Gallery Column (Left) -->
    <div class="gallery-zone">
      <div class="main-canvas">
        <span class="canvas-tag">Hathoro Select™ &bull; UN 1A1/Y1.5/300</span>
        <img id="activeImage" src="images/G1877111/image_01_1000x1000.jpg" alt="Hathoro Select 30-Gallon Closed-Head Steel Transport Drum">
      </div>

      <div class="thumbs-strip">
        <div class="thumb-slot active" onclick="swapPhoto('images/G1877111/image_01_1000x1000.jpg', this)">
          <img src="images/G1877111/image_01_1000x1000.jpg" alt="Angle 1">
        </div>
        <div class="thumb-slot" onclick="swapPhoto('images/G1877111/image_02_1000x1000.jpg', this)">
          <img src="images/G1877111/image_02_1000x1000.jpg" alt="Angle 2">
        </div>
        <div class="thumb-slot" onclick="swapPhoto('images/G1877111/image_03_1000x1000.jpg', this)">
          <img src="images/G1877111/image_03_1000x1000.jpg" alt="Angle 3">
        </div>
      </div>
    </div>

    <!-- Buying Column (Right) -->
    <div class="info-zone">
      <div class="vendor-badge-minimal">HATHORO SELECT&trade; INDUSTRIAL</div>
      <h1 class="product-title-minimal">Hathoro Select 30-Gallon Closed-Head Heavy-Duty Steel Transport Drum</h1>

      <div class="sku-specs-subline">
        <span>SKU: <strong>H1877111</strong></span>
        <span class="dot-sep">&bull;</span>
        <span>20-Gauge Carbon Steel</span>
        <span class="dot-sep">&bull;</span>
        <span>DOT 49 CFR Certified</span>
      </div>

      <!-- Price -->
      <div class="price-strip">
        <span class="price-active" id="displayUnitPrice">$151.99</span>
        <span class="price-original">$174.79</span>
        <span class="save-pill">Save 15%</span>
      </div>

      <!-- Stock Indicator -->
      <div class="stock-indicator">
        <span class="live-dot"></span>
        <span>In Stock (500 Available) &mdash; Guaranteed Same-Day Dispatch</span>
      </div>

      <!-- Controls -->
      <div class="action-panel">
        <div class="stepper-row">
          <span class="stepper-label">Quantity:</span>
          <div class="minimal-stepper">
            <button type="button" onclick="stepQuantity(-1)">&minus;</button>
            <input type="text" id="qtyField" value="1" readonly>
            <button type="button" onclick="stepQuantity(1)">&plus;</button>
          </div>

          <div style="display: flex; gap: 6px; margin-left: 8px;">
            <span class="preset-pill active" id="pill-1" onclick="chooseQty(1)">1</span>
            <span class="preset-pill" id="pill-5" onclick="chooseQty(5)">5 (Tier 1)</span>
            <span class="preset-pill" id="pill-10" onclick="chooseQty(10)">10+ (Tier 2)</span>
          </div>
        </div>

        <button class="btn-submit-main" onclick="triggerAddToCart()">
          <span id="btnCartText">Add to Cart &mdash; $151.99</span>
        </button>

        <button class="btn-shoppay-minimal" onclick="triggerAddToCart()">
          Buy with <span style="font-weight: 800; letter-spacing: -0.04em;">Shop Pay</span>
        </button>
      </div>

      <!-- Minimal Trust Grid -->
      <div class="trust-strip-minimal">
        <div class="trust-cell">
          <div class="trust-icon-box">⚡</div>
          <div class="trust-desc">
            <h5>Fast Commercial Freight</h5>
            <p>Orders placed before 3:00 PM CT ship same business day via regional freight carrier.</p>
          </div>
        </div>
        <div class="trust-cell">
          <div class="trust-icon-box">🛡️</div>
          <div class="trust-desc">
            <h5>UN 1A1 Pressure Tested Guarantee</h5>
            <p>Certified for hazardous and standard liquids. Unlined interior with rust inhibitor.</p>
          </div>
        </div>
        <div class="trust-cell">
          <div class="trust-icon-box">🏢</div>
          <div class="trust-desc">
            <h5>B2B Wholesale Accounts</h5>
            <p>Net-30 commercial billing and pallet volume credits available for facilities.</p>
          </div>
        </div>
      </div>

    </div>

  </main>

  <!-- Full Width Specs Section with Animated Segmented Control -->
  <section class="specs-section" id="specs">
    <div class="segmented-control">
      <button class="segment-btn active" onclick="activateTab('specs', this)">Technical Specifications</button>
      <button class="segment-btn" onclick="activateTab('overview', this)">Product Overview</button>
      <button class="segment-btn" onclick="activateTab('shipping', this)">Freight & Handling</button>
      <button class="segment-btn" onclick="activateTab('compliance', this)">Compliance & Warranty</button>
    </div>

    <!-- Pane 1: Technical Specs -->
    <div class="pane-content active" id="pane-specs">
      <table class="spec-table-clean">
        <tbody>
          <tr>
            <td class="prop">Primary Drum Material</td>
            <td class="val">Heavy-Duty Carbon Steel</td>
          </tr>
          <tr>
            <td class="prop">Volume Capacity</td>
            <td class="val">30 Gallon (113.5 Liters)</td>
          </tr>
          <tr>
            <td class="prop">Drum Head Type</td>
            <td class="val">Closed-Head (Tight-Head) with Welded Seams</td>
          </tr>
          <tr>
            <td class="prop">Body Material Gauge</td>
            <td class="val">20 Gauge (0.9 mm cold-rolled steel)</td>
          </tr>
          <tr>
            <td class="prop">Top & Bottom Gauge</td>
            <td class="val">20 Gauge (0.9 mm)</td>
          </tr>
          <tr>
            <td class="prop">UN Rating Liquids</td>
            <td class="val">1A1/Y1.5/300 Certified</td>
          </tr>
          <tr>
            <td class="prop">Overall Height</td>
            <td class="val">29 1/2 in (749 mm)</td>
          </tr>
          <tr>
            <td class="prop">Top Outside Diameter</td>
            <td class="val">18 3/4 in (476 mm)</td>
          </tr>
          <tr>
            <td class="prop">Bung Openings</td>
            <td class="val">2 Precision Openings (2" NPS Bung & 3/4" Vent Bung)</td>
          </tr>
          <tr>
            <td class="prop">Cover & Body Color</td>
            <td class="val">White Cover / Baked Black Enamel Body</td>
          </tr>
          <tr>
            <td class="prop">Interior Lining</td>
            <td class="val">Unlined with Specialized Rust Inhibitor</td>
          </tr>
          <tr>
            <td class="prop">Tare Weight</td>
            <td class="val">34.0 lbs (15.4 kg)</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Pane 2: Overview -->
    <div class="pane-content" id="pane-overview">
      <div style="background: #ffffff; border: 1px solid var(--c-border); border-radius: var(--radius-md); padding: 32px; font-size: 15px; line-height: 1.7; color: #3f3f46;">
        <h4 style="font-size: 18px; font-weight: 700; color: var(--c-black); margin-bottom: 12px;">Engineered for High-Reliability Liquid Containment</h4>
        <p style="margin-bottom: 16px;">
          The Hathoro Select™ 30-Gallon Closed-Head Drum is precision-engineered from cold-rolled 20-gauge steel, built specifically to store, transport, and dispense industrial fluids, chemical solvents, non-corrosive liquids, and lubricants.
        </p>
        <p>
          Each unit is pressure tested at the factory to meet stringent UN 1A1 specifications. Dual Tri-Sure bung fittings ensure clean pumping, venting, and faucet attachments without risk of thread galling or vapor leakage.
        </p>
      </div>
    </div>

    <!-- Pane 3: Shipping -->
    <div class="pane-content" id="pane-shipping">
      <div style="background: #ffffff; border: 1px solid var(--c-border); border-radius: var(--radius-md); padding: 32px; font-size: 14.5px; line-height: 1.7; color: #3f3f46;">
        <h4 style="font-size: 18px; font-weight: 700; color: var(--c-black); margin-bottom: 12px;">Freight Dispatch & Logistics</h4>
        <p style="margin-bottom: 14px;">• <strong>Same-Day Dispatch:</strong> Orders confirmed by 3:00 PM Central ship today.</p>
        <p style="margin-bottom: 14px;">• <strong>Protective Packaging:</strong> Shipped banded to heavy pallets with multi-layer protective edge guards.</p>
        <p>• <strong>Free Freight:</strong> Fully covered free freight to any commercial facility or jobsite in the contiguous USA.</p>
      </div>
    </div>

    <!-- Pane 4: Compliance -->
    <div class="pane-content" id="pane-compliance">
      <div style="background: #ffffff; border: 1px solid var(--c-border); border-radius: var(--radius-md); padding: 32px; font-size: 14.5px; line-height: 1.7; color: #3f3f46;">
        <h4 style="font-size: 18px; font-weight: 700; color: var(--c-black); margin-bottom: 12px;">Regulatory Certifications</h4>
        <p style="margin-bottom: 14px;">• <strong>DOT 49 CFR:</strong> Fully certified under 49 CFR 178.504 for hazardous liquid packaging.</p>
        <p style="margin-bottom: 14px;">• <strong>UN Standard:</strong> 1A1/Y1.5/300 rated for Packing Groups II and III.</p>
        <p>• <strong>Hathoro Guarantee:</strong> 1-Year commercial defect warranty with immediate hassle-free replacements.</p>
      </div>
    </div>

  </section>

  <!-- Apple/Linear Floating Glassmorphic Sticky Pill -->
  <div class="floating-buy-pill" id="floatingPill">
    <div class="pill-left">
      <div class="pill-thumb">
        <img src="images/G1877111/image_01_1000x1000.jpg" alt="Pill Thumbnail">
      </div>
      <div>
        <div class="pill-text">Hathoro Select 30-Gallon Steel Drum</div>
        <div style="font-size: 11.5px; color: var(--c-muted);">In Stock (500 Units)</div>
      </div>
    </div>

    <div style="display: flex; align-items: center; gap: 14px;">
      <div class="pill-price" id="pillPriceText">$151.99</div>
      <button class="pill-btn-action" onclick="triggerAddToCart()">Add to Cart</button>
    </div>
  </div>

  <!-- Minimal Footer -->
  <footer>
    <div class="footer-content">
      <div>&copy; 2026 Hathoro.com &bull; Industrial Equipment & Facilities Supply.</div>
      <div>Questions? (727) 609-8404 &bull; sales@hathoro.com</div>
    </div>
  </footer>

  <script>
    var basePrice = 151.99;
    var currentQty = 1;
    var cartCount = 0;

    function swapPhoto(src, cardEl) {
      var img = document.getElementById('activeImage');
      img.style.opacity = '0';
      img.style.transform = 'scale(0.96)';
      
      setTimeout(function() {
        img.src = src;
        img.style.opacity = '1';
        img.style.transform = 'scale(1)';
      }, 150);

      document.querySelectorAll('.thumb-slot').forEach(function(s) {
        s.classList.remove('active');
      });
      cardEl.classList.add('active');
    }

    function stepQuantity(delta) {
      var next = currentQty + delta;
      if (next >= 1 && next <= 500) {
        chooseQty(next);
      }
    }

    function chooseQty(q) {
      currentQty = q;
      document.getElementById('qtyField').value = currentQty;

      var unitPrice = basePrice;
      if (currentQty >= 10) {
        unitPrice = basePrice * 0.90; // 10% volume discount
      } else if (currentQty >= 5) {
        unitPrice = basePrice * 0.95; // 5% volume discount
      }

      var total = (unitPrice * currentQty).toFixed(2);
      document.getElementById('displayUnitPrice').innerText = '$' + unitPrice.toFixed(2);
      document.getElementById('btnCartText').innerText = 'Add ' + currentQty + ' to Cart &mdash; $' + total;
      document.getElementById('pillPriceText').innerText = '$' + unitPrice.toFixed(2);

      // Update pills
      ['pill-1', 'pill-5', 'pill-10'].forEach(function(p) {
        document.getElementById(p).classList.remove('active');
      });
      if (currentQty === 1) document.getElementById('pill-1').classList.add('active');
      else if (currentQty === 5) document.getElementById('pill-5').classList.add('active');
      else if (currentQty >= 10) document.getElementById('pill-10').classList.add('active');
    }

    function activateTab(tabName, btn) {
      document.querySelectorAll('.segment-btn').forEach(function(b) {
        b.classList.remove('active');
      });
      document.querySelectorAll('.pane-content').forEach(function(p) {
        p.classList.remove('active');
      });
      btn.classList.add('active');
      document.getElementById('pane-' + tabName).classList.add('active');
    }

    function triggerAddToCart() {
      cartCount += currentQty;
      document.getElementById('cartCountBadge').innerText = cartCount;
      
      // Floating pill subtle bounce feedback
      var pill = document.getElementById('floatingPill');
      pill.style.transform = 'translateX(-50%) scale(1.05)';
      setTimeout(function() {
        pill.style.transform = 'translateX(-50%) scale(1)';
      }, 200);

      alert('Added ' + currentQty + ' unit(s) of Hathoro Select 30-Gallon Drum to cart!');
    }

    // Floating Glass Pill Scroll Observer
    window.addEventListener('scroll', function() {
      var pill = document.getElementById('floatingPill');
      if (window.scrollY > 480) {
        pill.classList.add('visible');
      } else {
        pill.classList.remove('visible');
      }
    });
  </script>

</body>
</html>
"""

with open('preview/product.html', 'w', encoding='utf-8') as f:
    f.write(html_content)

print("Saved ultra-minimalist product.html!")
