# Hathoro.com — Zoro Select Shipping Boxes Catalog Automation
## Comprehensive Implementation Plan & Engineering Specification

**Document Version:** 1.0  
**Target Platform:** [hathoro.com](https://hathoro.com) (Shopify)  
**Catalog Scope:** Zoro Select Unbranded Corrugated Shipping Boxes (1-Day Dispatch)  
**Database Source:** `Automations/TSV Files/combined_reports.db`  
**Total Target SKUs Identified:** **1,303 Qualifying Products**

---

### Table of Contents
1. [Executive Summary & Strategic Objective](#1-executive-summary--strategic-objective)
2. [Complete Local Paths & Directory Structure](#2-complete-local-paths--directory-structure)
3. [Database Extraction & Filtering Rules](#3-database-extraction--filtering-rules)
4. [Pricing, Freight & Profit Margin Architecture](#4-pricing-freight--profit-margin-architecture)
5. [Automated 5-Image Production Pipeline](#5-automated-5-image-production-pipeline)
6. [Shopify Flat File & Automated Collection Linkage](#6-shopify-flat-file--automated-collection-linkage)
7. [End-to-End Execution Roadmap & Phases](#7-end-to-end-execution-roadmap--phases)
8. [Risk Mitigation & Operational Guardrails](#8-risk-mitigation--operational-guardrails)

---

### 1. Executive Summary & Strategic Objective

The objective of this initiative is to automate the extraction, pricing, branding, image generation, and catalog upload of all **Zoro Select Shipping Boxes** from the Zoro catalog feed to **hathoro.com**. 

By leveraging Hathoro's special **10% partner discount agreement** on Zoro Select products, applying Zoro's **$50 free shipping threshold** ($5.99 shipping if under $50), and pricing each product at a strict **10% net profit margin**, Hathoro can offer competitive industrial packaging supplies while guaranteeing profitable dropshipping fulfillment with **1-day dispatch speed**.

Every listing will feature:
* **Private-Label Hathoro Branding** ("Hathoro Industrial Packaging")
* **A standardized 5-image gallery** including dynamic dimension diagrams, corrugated strength certifications, pack quantity badges, and Made in USA guarantees.
* **Automated Shopify Collection integration** matching the store's `"Moving & Shipping Boxes"` category rule so products appear immediately on the storefront.

---

### 2. Complete Local Paths & Directory Structure

All scripts, databases, source assets, and output directories reside on the local system at the following exact paths:

| Component | Exact Local Path | Purpose |
|---|---|---|
| **Root Workspace** | `/Users/kamibaloch342/Downloads/Dropshipping/Development_Unit` | Base repository directory |
| **Zoro Catalog Database** | `/Users/kamibaloch342/Downloads/Dropshipping/Development_Unit/Automations/TSV Files/combined_reports.db` | SQLite master feed (Table: `zoro_data`, 2,042,363 rows) |
| **Production Script (To Build)** | `/Users/kamibaloch342/Downloads/Dropshipping/Development_Unit/Hathoro Website/generate_shipping_boxes_catalog.py` | Master Python script for extract, pricing, image generation & CSV |
| **Reference Catalog Script** | `/Users/kamibaloch342/Downloads/Dropshipping/Development_Unit/Hathoro Website/generate_master_108_shopify_catalog.py` | Proven template used for Hathoro 108 Drum catalog |
| **Base Asset: Made in USA** | `/Users/kamibaloch342/.gemini/antigravity/brain/089b5d35-3020-4a09-b972-2ac057946c15/.user_uploaded/media_1793125069179_194f7540.png` | Source template for Image 5 |
| **Base Asset: Reusable Pack** | `/Users/kamibaloch342/.gemini/antigravity/brain/089b5d35-3020-4a09-b972-2ac057946c15/.user_uploaded/media_1793125082759_0d32b24e.jpg` | Source template for Image 4 |
| **Base Asset: 32 ECT Wall** | `/Users/kamibaloch342/.gemini/antigravity/brain/089b5d35-3020-4a09-b972-2ac057946c15/.user_uploaded/media_1793125133263_8ce303e5.png` | Source template for Image 3 |
| **Base Asset: Dimensions** | `/Users/kamibaloch342/.gemini/antigravity/brain/089b5d35-3020-4a09-b972-2ac057946c15/.user_uploaded/media_1793125204677_3223395f.png` | Source template for Image 2 |
| **Shopify Collection Evidence** | `/Users/kamibaloch342/.gemini/antigravity/brain/089b5d35-3020-4a09-b972-2ac057946c15/.user_uploaded/media_1793124907432_c3e50c6c.png` | Screenshot of Shopify Automated Collection rule |
| **Test Output Folder** | `/Users/kamibaloch342/Downloads/Dropshipping/Development_Unit/test_listing_images_G4784577/` | Single-item verified output folder (G4784577) |
| **Production Images Folder** | `/Users/kamibaloch342/Downloads/Dropshipping/Development_Unit/Hathoro Website/shipping_boxes_images/` | Destination folder for all generated JPG image packs |
| **Master Shopify Flat File** | `/Users/kamibaloch342/Downloads/Dropshipping/Development_Unit/Hathoro Website/hathoro_zoro_select_shipping_boxes_master.csv` | Ready-to-import bulk Shopify CSV flat file |

---

### 3. Database Extraction & Filtering Rules

The Zoro master database `combined_reports.db` contains over 2 million products across all categories. To isolate strictly **Zoro Select Shipping Boxes** eligible for dropshipping, the script executes the following SQL query:

```sql
SELECT 
    zoro_no,
    manufacturer_part_number,
    upc_code,
    zoro_brand_name,
    short_description,
    long_description,
    sales_price,
    unit_price,
    min_order_qty,
    uom,
    lead_time,
    availabilityStatus,
    ship_pack_length,
    ship_pack_width,
    ship_pack_height,
    ship_pack_weight,
    zoro_image_name
FROM zoro_data 
WHERE 
    -- 1. Brand Rule: Strictly Zoro Select (Null, empty, or explicit 'Zoro Select')
    (zoro_brand_name IS NULL OR zoro_brand_name = '' OR zoro_brand_name = 'Zoro Select')
    
    -- 2. Category Rule: Shipping Boxes, Corrugated Boxes, Multi-Depth Boxes
    AND (
        short_description LIKE '%Shipping Box%' 
        OR short_description LIKE '%Corrugated Box%' 
        OR short_description LIKE '%Multi-Depth Box%'
    )
    
    -- 3. Exclusion Filter: Remove accessories, pads, tapes, rolls, lids
    AND (
        short_description NOT LIKE '%Lid%' 
        AND short_description NOT LIKE '%Pad%' 
        AND short_description NOT LIKE '%Roll%' 
        AND short_description NOT LIKE '%Tape%'
    )
    
    -- 4. Fulfillment Speed: Must dispatch in 1 day
    AND lead_time LIKE '%1 day%'
    
    -- 5. Inventory Availability: Must be currently In Stock
    AND (availabilityStatus = 'In Stock' OR availabilityStatus IS NULL)
    
    -- 6. Integrity check: Valid price and MOQ
    AND sales_price > 0 
    AND min_order_qty > 0;
```

#### Query Yield
* **1,303 Qualifying Bundles** verified in `combined_reports.db`.
* Pack counts range from 10-packs to 25-packs, covering cube boxes, flat boxes, long boxes, and heavy-duty double-wall containers.

---

### 4. Pricing, Freight & Profit Margin Architecture

Every price on Hathoro is calculated through a transparent 4-stage formula:

```
[Supplier Base Pack Cost] 
          │
          ▼  -10% (Hathoro Agreement Discount)
[Discounted Zoro Cost] 
          │
          ▼  + $5.99 if < $50.00 (Otherwise Free $0.00)
[Total Landed Supplier Cost] 
          │
          ▼  / 0.90 (10% Target Net Profit Margin)
[Hathoro Storefront Selling Price]
```

#### Detailed Mathematical Breakdown

1. **Base Supplier Pack Cost ($C_{base}$):**
   $$\text{Base Pack Cost} = \text{sales\_price} \times \text{min\_order\_qty}$$
   *(Note: In the Zoro feed, `sales_price` is the per-box price, and `min_order_qty` is the required bundle pack size).*

2. **Hathoro Zoro Select 10% Partner Discount ($C_{disc}$):**
   $$C_{disc} = \text{Base Pack Cost} \times (1 - 0.10) = \text{Base Pack Cost} \times 0.90$$

3. **Zoro $50 Shipping Rule ($F_{freight}$):**
   $$F_{freight} = \begin{cases} \$5.99 & \text{if } C_{disc} < \$50.00 \\ \$0.00 & \text{if } C_{disc} \ge \$50.00 \end{cases}$$
   $$\text{Total Landed Cost } (C_{landed}) = C_{disc} + F_{freight}$$

4. **Hathoro 10% Net Profit Margin Selling Price ($P_{sell}$):**
   To achieve an exact 10% net profit margin on the sale:
   $$\text{Margin} = \frac{P_{sell} - C_{landed}}{P_{sell}} = 0.10 \implies P_{sell} = \frac{C_{landed}}{1 - 0.10} = \frac{C_{landed}}{0.90}$$
   *(The selling price is rounded to `.99` or `.95` for standard e-commerce retail display).*

5. **MSRP / Compare-At Price ($P_{compare}$):**
   $$P_{compare} = \text{round}\left(\frac{P_{sell}}{0.80}, 2\right)$$
   *(Shows customers a 20% savings strike-through on Hathoro storefront).*

---

#### Worked Pricing Examples

| Metric | Scenario A: Under $50 (User Example) | Scenario B: Over $50 (Free Shipping) |
|---|---|---|
| **Zoro SKU** | Example Case | `G800906080` (22"x18"x8", 25-pk) |
| **Box Unit Price** | \$1.60 / box | \$2.69 / box |
| **Pack Size (MOQ)** | 25 Boxes | 25 Boxes |
| **Base Supplier Cost** | **\$40.00** | **\$67.25** |
| **10% Zoro Select Discount** | - \$4.00 | - \$6.73 |
| **Discounted Zoro Cost** | **\$36.00** | **\$60.53** |
| **Zoro Shipping Fee** | **+ \$5.99** *(Cost < $50)* | **\$0.00** *(Cost ≥ $50 Free)* |
| **Total Landed Cost** | **\$41.99** | **\$60.53** |
| **10% Net Profit Markup** | $\div 0.90$ | $\div 0.90$ |
| **Hathoro Selling Price** | **\$46.66** *(or \$46.99)* | **\$67.26** *(or \$67.99)* |
| **Net Profit Dollar Return** | **\$4.67 per order** | **\$6.73 per order** |
| **Net Profit Margin %** | **10.01%** | **10.00%** |
| **Compare-At (MSRP)** | \$58.33 | \$84.08 |

---

### 5. Automated 5-Image Production Pipeline

Each of the 1,303 products will receive an automated, professionally designed 5-image graphic suite (1024x1024 resolution, 300 DPI, clean JPEG format).

```
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│     HATHORO     │ │   DIMENSIONS    │ │   CORRUGATED    │ │   PACK BUNDLE   │ │   MADE IN USA   │
│                 │ │  22" x 18" x 8" │ │  32 ECT STRENGTH│ │     25 PACK     │ │   GUARANTEED    │
│    [Product]    │ │    [Diagram]    │ │   [Flute Wall]  │ │   [Flat-Pack]   │ │   [USA Badge]   │
│                 │ │                 │ │                 │ │                 │ │                 │
│  Image 1: Main  │ │Image 2: Dynamic │ │ Image 3: Flute  │ │ Image 4: Bundle │ │ Image 5: Origin │
└─────────────────┘ └─────────────────┘ └─────────────────┘ └─────────────────┘ └─────────────────┘
```

#### Specifications for Each Image

1. **Image 1 — Main Product Hero Shot (`1_Hathoro_[Size]_Main.jpg`)**
   * **Source:** Download original high-res photo from Zoro CDN (`zoro_image_name`) using browser `User-Agent` headers to prevent Akamai 403 blocks.
   * **Canvas:** 1024x1024 px pure white background (`#FFFFFF`). Product centered with 60px padding.
   * **Branding:** Bold navy blue Hathoro wordmark in top-right corner:
     * Header: **HATHORO** (`#0B2240`, 32pt bold)
     * Subheader: `INDUSTRIAL PACKAGING` (`#64748B`, 12pt)

2. **Image 2 — Dynamic Dimensions Infographic (`2_Hathoro_[Size]_Dimensions.jpg`)**
   * **Base Template:** `media_1793125204677_3223395f.png`
   * **Dynamic Logic:** Regex parses Length ($L$), Width ($W$), and Height ($H$) from `short_description` (e.g. `22 in x 18 in x 8 in` $\rightarrow$ `L=22`, `W=18`, `H=8`).
   * **Dynamic Overlays:**
     * Header Banner: `DIMENSIONS: 22" x 18" x 8"` (Dark Navy Box with white bold text).
     * Dimension Callouts: Dynamic text rendered along the perspective arrows:
       * Length: `22" (Length)`
       * Width: `18" (Width)`
       * Height: `8" (Height)`
     * Construction Callout: `Single Wall Corrugated (32 ECT)` or `Double Wall Heavy Duty (48 ECT)`.

3. **Image 3 — Corrugated Strength & Material Certification (`3_Hathoro_Corrugated_32ECT.jpg`)**
   * **Base Template:** `media_1793125133263_8ce303e5.png`
   * **Visual Assets:** Exploded view of outer liner, fluted corrugated medium, and inner liner.
   * **Specifications Highlighted:**
     * 32 ECT (Edge Crush Test) / 200 lb Bursting Test certified.
     * Maximum gross weight capacity: 65 lbs.
     * High puncture and compression resistance for stacking safety.

4. **Image 4 — Dynamic Pack Bundle Badge (`4_Hathoro_Reusable_[MOQ]Pack.jpg`)**
   * **Base Template:** `media_1793125082759_0d32b24e.jpg`
   * **Dynamic Logic:** Reads `min_order_qty` from database row.
   * **Dynamic Overlay:**
     * Prominent Badge: `[MOQ] PACK BUNDLE` (e.g., `25 PACK`, `15 PACK`, `10 PACK`).
     * Value Badges: "100% Recyclable", "Reusable Kraft Material", "Shipped Flat to Save Storage Space".

5. **Image 5 — Made in USA & Carrier Compliance (`5_Hathoro_Made_In_USA.jpg`)**
   * **Base Template:** `media_1793125069179_194f7540.png`
   * **Visual Highlights:** Official American Flag crest, "Manufactured in the USA".
   * **Carrier Compliance:** Certified for USPS, FedEx, UPS, and DHL domestic and international shipping. Meets ASTM D5118 shipping standards.

---

### 6. Shopify Flat File & Automated Collection Linkage

#### How Automated Collection Assignment Works

In Shopify, manual product-by-product collection tagging is slow and prone to errors. Hathoro's Shopify admin already contains an automated collection named **"Shipping Boxes"** with the following built-in rule:

$$\mathbf{Condition:} \quad \text{Category is equal to } \textbf{"Moving \& Shipping Boxes"}$$

To make every uploaded box automatically appear in the **Shipping Boxes** collection without any manual clicks, the bulk flat file maps the exact Shopify Standard Taxonomy:

* **`Product category` column:** `Office Supplies > Shipping Supplies > Moving & Shipping Boxes`
* **`Type` column:** `Shipping Boxes`
* **`Tags` column:** `Shipping Boxes, Corrugated Boxes, Hathoro Packaging, [MOQ] Pack, [L]x[W]x[H], Zoro Select`

As soon as Shopify finishes processing the CSV import, its collection engine matches the category rule and automatically displays all products in the **Shipping Boxes** collection on Hathoro.com.

#### Shopify CSV 57-Column Mapping Structure

| CSV Column | Value Logic | Sample Product (G800906080) |
|---|---|---|
| `Handle` | `hathoro-[L]x[W]x[H]-shipping-boxes-[MOQ]-pack` | `hathoro-22x18x8-shipping-boxes-25-pack` |
| `Title` | `Hathoro [L]" x [W]" x [H]" Corrugated Shipping Boxes ([MOQ] Pack) - 32 ECT Single Wall` | `Hathoro 22" x 18" x 8" Corrugated Shipping Boxes (25 Pack) - 32 ECT Single Wall` |
| `Body (HTML)` | Clean responsive HTML with dimension specs, packaging tables, and shipping guarantees | `<h3>Heavy-Duty Corrugated Shipping Boxes...</h3>` |
| `Vendor` | Fixed text | `Hathoro` |
| `Product category` | **Exact collection trigger** | `Office Supplies > Shipping Supplies > Moving & Shipping Boxes` |
| `Type` | Storefront type | `Shipping Boxes` |
| `Tags` | Searchable comma-separated tags | `Shipping Boxes, 22x18x8, 25 Pack, 32 ECT, Hathoro` |
| `Published` | Publication flag | `TRUE` |
| `Option1 Name` | Variant dimension | `Title` |
| `Option1 Value` | Variant value | `25 Pack` |
| `Variant SKU` | Formatted inventory SKU | `HAT-BOX-G800906080-22X18X8` |
| `Variant Grams` | Metric weight from `ship_pack_weight` | `11340` (calculated from lbs $\times$ 453.592) |
| `Variant Inventory Tracker` | Shopify stock management | `shopify` |
| `Variant Inventory Qty` | Initial stock buffer | `50` |
| `Variant Inventory Policy`| Stockout behavior | `deny` |
| `Variant Fulfillment Service`| Service type | `manual` |
| `Variant Price` | **10% Net Profit Margin Price** | `67.99` |
| `Variant Compare At Price`| 20% MSRP Anchor | `84.99` |
| `Variant Requires Shipping`| Physical shipment flag | `TRUE` |
| `Variant Taxable` | Sales tax flag | `TRUE` |
| `Image Position` | 1 to 5 (one row per image) | `1`, `2`, `3`, `4`, `5` |
| `Image Src` | Hosted Image URL / Local Path | `https://cdn.hathoro.com/boxes/...` |
| `Status` | Active storefront listing | `active` |

---

### 7. End-to-End Execution Roadmap & Phases

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ PHASE 1: Data Ingestion & Sanitization                                         │
│ • Query combined_reports.db for 1,303 Zoro Select SKUs                         │
│ • Parse L x W x H dimensions, wall types, and MOQ pack quantities             │
│ • Run 10% partner discount, $50 freight threshold, and 10% margin pricing    │
└──────────────────────────────────────┬────────────────────────────────────────┘
                                       │
                                       ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ PHASE 2: Image Production & Optimization                                       │
│ • Batch-download Zoro product photos (Akamai-safe User-Agent)                 │
│ • Render Image 1: Hathoro wordmark overlay on white canvas                    │
│ • Render Image 2: Dynamic dimension arrows + header text                      │
│ • Render Image 3: Corrugated 32 ECT strength certification                    │
│ • Render Image 4: Dynamic [MOQ]-pack bundle badge                             │
│ • Render Image 5: Made in USA & carrier guarantee                             │
│ • Output to Hathoro Website/shipping_boxes_images/                            │
└──────────────────────────────────────┬────────────────────────────────────────┘
                                       │
                                       ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ PHASE 3: Master Shopify CSV Compilation                                       │
│ • Generate 57-column standard Shopify import file                             │
│ • Structure HTML product descriptions (spec tables, bullet points)            │
│ • Embed category taxonomy for instant collection linkage                      │
│ • Output to Hathoro Website/hathoro_zoro_select_shipping_boxes_master.csv      │
└──────────────────────────────────────┬────────────────────────────────────────┘
                                       │
                                       ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ PHASE 4: Quality Audit & Shopify Storefront Deployment                         │
│ • Verify pricing math (zero negative or zero-margin SKUs)                     │
│ • Verify lead time = 1 day across all SKUs                                    │
│ • Import CSV into Shopify Admin (Products > Import)                           │
│ • Confirm auto-population in hathoro.com/collections/shipping-boxes           │
└───────────────────────────────────────────────────────────────────────────────┘
```

---

### 8. Risk Mitigation & Operational Guardrails

1. **Akamai 403 Image Blocking Prevention:**
   * Direct script downloads of Zoro images often trigger HTTP 403 Forbidden.
   * **Mitigation:** The downloader uses rotating desktop browser headers (`User-Agent: Mozilla/5.0...`), `Referer: https://www.zoro.com/`, and automatic retry backoff.

2. **Lead Time Guarantee:**
   * Only SKUs with `lead_time LIKE '%1 day%'` and `availabilityStatus = 'In Stock'` are accepted. Any SKU that shifts to dropship supplier backorder is automatically skipped.

3. **Strict Price Integrity:**
   * The script asserts that every final selling price satisfies:
     $$\text{Selling Price} - \text{Total Landed Cost} \ge 0.10 \times \text{Selling Price}$$
   * If any anomalous zero price or missing MOQ is detected, the SKU is logged to an exceptions file and excluded from the catalog.

4. **Zero Impact on Production Order Placers:**
   * The database read runs in read-only URI mode (`file:...combined_reports.db?mode=ro`), ensuring zero locks or contention with live placers or hunter routines.

---

### Summary Checklist Before Execution

- [x] **Catalog Scope:** Zoro Select only (`zoro_brand_name IS NULL` or unbranded).
- [x] **Fulfillment Speed:** Ships in 1 day (`lead_time LIKE '%1 day%'`).
- [x] **Supplier Discount:** 10% off base cost for Zoro Select items.
- [x] **Freight Rule:** +$5.99 shipping if discounted cost < $50.00; $0.00 if $\ge \$50.00$.
- [x] **Profit Margin:** Strict 10% net profit margin on storefront price.
- [x] **5 Branded Images:** Hathoro Logo, Dimensions, 32 ECT Corrugated, Pack Badge, Made in USA.
- [x] **Collection Linkage:** `Office Supplies > Shipping Supplies > Moving & Shipping Boxes` triggers Shopify's automated collection rule.
- [x] **Local Paths:** All Mac workspace paths identified and verified.
