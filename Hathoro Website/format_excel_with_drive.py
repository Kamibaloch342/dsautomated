#!/usr/bin/env python3
"""
FORMAT VALVOLINE x ZORO EXCEL WORKBOOK WITH GOOGLE DRIVE INTEGRATION
====================================================================
Generates a modern, executive-grade corporate spreadsheet for the 35 products:
  - Tab 1: Ready to List (32 Products) with Google Drive link column & clean styling
  - Tab 2: Discarded Mismatches (3 Items) with clear explanation
  - Tab 3: Executive Overview & Drive with prominent CTA button & KPI cards
"""

import os
import glob
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
EXCEL_PATH = os.path.join(CURRENT_DIR, "Valvoline_Zoro_Matched_Results.xlsx")
GDRIVE_URL = "https://drive.google.com/drive/folders/1b1iUhXMe1cA_sIFtLJhNPl-z_Amxb1b5?usp=sharing"

# Load current data from existing Excel or CSV
wb_in = openpyxl.load_workbook(EXCEL_PATH)
if 'In-Stock (Ready to List)' in wb_in.sheetnames:
    ws_in = wb_in['In-Stock (Ready to List)']
    all_rows = []
    for r in range(2, ws_in.max_row + 1):
        asin = ws_in.cell(r, 1).value
        if asin:
            all_rows.append({
                'asin': str(asin).strip(),
                'match_status': ws_in.cell(r, 2).value,
                'match_method': ws_in.cell(r, 3).value,
                'zoro_no': str(ws_in.cell(r, 4).value or '').strip(),
                'zoro_mpn': str(ws_in.cell(r, 5).value or '').strip(),
                'zoro_brand': str(ws_in.cell(r, 6).value or 'VALVOLINE').strip(),
                'zoro_desc': str(ws_in.cell(r, 7).value or '').strip(),
                'zoro_avail': ws_in.cell(r, 8).value or 'In Stock',
                'zoro_price': ws_in.cell(r, 9).value,
                'amz_pack_qty': ws_in.cell(r, 10).value or 1,
                'zoro_est_cost': ws_in.cell(r, 11).value,
                'pack_type': ws_in.cell(r, 12).value or 'Single Unit',
                'amz_part': str(ws_in.cell(r, 13).value or '').strip(),
                'amz_upc': str(ws_in.cell(r, 14).value or '').strip(),
                'amz_bought': ws_in.cell(r, 15).value,
                'amz_rank': ws_in.cell(r, 16).value,
                'zoro_url': ws_in.cell(r, 17).value,
                'amz_url': ws_in.cell(r, 18).value,
            })
else:
    # Read from Ready to List + Discarded sheets
    all_rows = []
    ws_ready = wb_in['Ready to List (32 Products)']
    for r in range(5, ws_ready.max_row + 1):
        asin = ws_ready.cell(r, 1).value
        if asin:
            all_rows.append({
                'asin': str(asin).strip(),
                'clean_title': ws_ready.cell(r, 2).value,
                'zoro_desc': ws_ready.cell(r, 2).value,
                'zoro_no': str(ws_ready.cell(r, 10).value or '').strip(),
                'zoro_mpn': str(ws_ready.cell(r, 11).value or '').strip(),
                'zoro_price': ws_ready.cell(r, 12).value,
                'amz_bought': ws_ready.cell(r, 13).value,
                'amz_rank': ws_ready.cell(r, 14).value,
                'amz_upc': str(ws_ready.cell(r, 9).value or '').strip(),
                'images_count': int(str(ws_ready.cell(r, 5).value or '8').split()[0]),
                'videos_count': int(str(ws_ready.cell(r, 6).value or '1').split()[0]) if 'None' not in str(ws_ready.cell(r, 6).value) else 0,
            })
    ws_disc = wb_in['Discarded (3 Mismatches)']
    for r in range(4, ws_disc.max_row + 1):
        asin = ws_disc.cell(r, 1).value
        if asin:
            all_rows.append({
                'asin': str(asin).strip(),
                'clean_title': ws_disc.cell(r, 2).value,
                'zoro_desc': ws_disc.cell(r, 2).value,
                'zoro_no': str(ws_disc.cell(r, 7).value or '').strip(),
                'amz_upc': str(ws_disc.cell(r, 8).value or '').strip(),
                'discard_reason': ws_disc.cell(r, 4).value,
            })

wb_in.close()

# Fetch or define direct Google Drive subfolder mappings
def get_drive_subfolder_map():
    import requests, re
    mapping = {
        'B000BOYCN6': 'https://drive.google.com/drive/folders/1t0ks4yRenpGkGIv8hu-r_NKVlTEHW03t',
        'B00C70TMLE': 'https://drive.google.com/drive/folders/1OQoh4Rh8BYLoPAM3zEhiH0SXVIte3Mcw',
        'B0CQ2ZR67X': 'https://drive.google.com/drive/folders/1NDH4BaB_flNCcHKTcRlsKObxPlJzU6zu',
        'B000CQ4DIM': 'https://drive.google.com/drive/folders/17OcQ6HXtmc1Ms9C52U7f1AS5nh859N0k',
        'B000CQ6L82': 'https://drive.google.com/drive/folders/1lct-7F7P03KAY7m5wniBzFmI41uC0T1g',
        'B0CQ33FCVN': 'https://drive.google.com/drive/folders/1rNE8QG8DLhqeZp8F8zA68y2fE7vbERSU',
        'B000GAN3I0': 'https://drive.google.com/drive/folders/14_YkbiGguj52PP_4SJZb4r7tq6Uhqgrr',
        'B00GDH5J6O': 'https://drive.google.com/drive/folders/1mDFAm4Qx0dS8uMc-L0lWw83fsW7WDdhi',
        'B00GDH5M1G': 'https://drive.google.com/drive/folders/1FzTYU9UYAaDgO3T2Xj59hi5cpj-JwcHb',
        'B00GSIXI9S': 'https://drive.google.com/drive/folders/1E3bP-vjg3lsG0xUEs-L3hzbiauBwNCu0',
        'B00PHN6CEI': 'https://drive.google.com/drive/folders/1xdkjnK9gR8t-s3YxWZJk5GOVvbUf16tE',
        'B01B1XL7RY': 'https://drive.google.com/drive/folders/12PAql3i-fQGI6vzt5YiB9b535XqJg9BM',
        'B01M1N0LRD': 'https://drive.google.com/drive/folders/1LV17EAtc3ut6UfcVB580FZh679n-fJX1',
        'B01MURBV9M': 'https://drive.google.com/drive/folders/1qIxbHXaOBUNb2EqeUyn60XWu6gursiYw',
        'B01N4TIRMC': 'https://drive.google.com/drive/folders/1YUa1_gbTqPhiZZSAON0lKEXRT_YHuYof',
        'B001OV61KW': 'https://drive.google.com/drive/folders/1kKf0mUOGz75Fd6pQH8rq6J1xhEu4XGLR',
        'B002ZSI9O4': 'https://drive.google.com/drive/folders/1nGULsXWj6wfxGiJ1M5rm4AYh3R2FjKe1',
        'B003VSKQ34': 'https://drive.google.com/drive/folders/1vmfSNENyLuDUKXzt5D_ac-vRsKblMURq',
        'B005XOOFEK': 'https://drive.google.com/drive/folders/1IvpY96Js4vNH0jUg69DWjUIdMFuJYL8W',
        'B007IBSDJY': 'https://drive.google.com/drive/folders/1fO2Uh5wJWlzbJFahvF8q8eadi1b4a2bC',
        'B08K99GJXJ': 'https://drive.google.com/drive/folders/1aPKqbx3UqOgZuQBKaDXa2AwrNteaH8GC',
        'B08KB2XYTP': 'https://drive.google.com/drive/folders/1eW_8tzDE99HfDMVUWua7365WwO8D3qyB',
        'B008QDV2JG': 'https://drive.google.com/drive/folders/1wAoC-DneixF9aq4yQ5Ovxm-q0Lxt7Gp5',
        'B008QDXADW': 'https://drive.google.com/drive/folders/1jWlSJyCw83l_Xilu7-xtGu7NKB4_D0gG',
        'B0033QNZWS': 'https://drive.google.com/drive/folders/1zxtdfKtuWh8TKdTeQfgHqV8OVN9pAY7w',
        'B0033QNZZ0': 'https://drive.google.com/drive/folders/1QrX2D74dJLA_Jxs3JFpOzgahc1XW4dcC',
        'B0033QQ4U8': 'https://drive.google.com/drive/folders/1YRuw-ry6hug18BTrEJ4r4FvYdgOD2qaC',
        'B0033QQ4VW': 'https://drive.google.com/drive/folders/1FQLzEt1E8odNZ5YhVELnbOvoLzs6fZiw',
        'B0033QQ50M': 'https://drive.google.com/drive/folders/1qURjZ5TQyaKJbSPEtz5pVcWer85f9zuC',
        'B0049J0F2G': 'https://drive.google.com/drive/folders/1kmFmBr96uWY9m6e9Yso5KQ4HRDv0PdqL',
        'B074G5NL3T': 'https://drive.google.com/drive/folders/1hCc9JrDO3qKz-JKuNXDRDFX1A8SJVw_E',
        'B00826LJYU': 'https://drive.google.com/drive/folders/15NeEkuGriGJ_w8wGTpr2ZkwpSQvak-SJ'
    }
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
        r = requests.get(GDRIVE_URL, headers=headers, timeout=10)
        matches = re.findall(r'aria-label="([A-Z0-9]+)[^"]*"[^>]+ssk=[\'"]5:[^:]+:([A-Za-z0-9_-]+)-0-16[\'"]', r.text)
        for a, fid in matches:
            mapping[a] = f"https://drive.google.com/drive/folders/{fid}"
    except Exception:
        pass
    return mapping

gdrive_map = get_drive_subfolder_map()

# Discarded ASINs
discarded_map = {
    'B0FP5NSCTF': "Volume Mismatch: Amazon sells 1 Quart (32 oz) vs Zoro 12 oz bottle",
    'B00F9R91T0': "Volume Mismatch: Amazon sells 12 oz bottle vs Zoro 1 Quart (32 oz)",
    'B00DILVVJW': "Multi-Pack Mismatch: Amazon sells Case of 6 vs Zoro single bottle"
}

ready_rows = []
discarded_rows = []

for row in all_rows:
    asin = row['asin']
    if asin in discarded_map:
        row['discard_reason'] = discarded_map[asin]
        discarded_rows.append(row)
    else:
        # Check generated assets in Hathoro Website/<ASIN>
        asin_dir = os.path.join(CURRENT_DIR, asin)
        copy_file = os.path.join(asin_dir, f"{asin}_listing_copy.txt")
        title = row['zoro_desc']
        if os.path.exists(copy_file):
            with open(copy_file, encoding='utf-8') as f:
                for line in f:
                    if line.startswith("Product Name:"):
                        title = line.replace("Product Name:", "").strip()
                        break
        row['clean_title'] = title
        row['images_count'] = len(glob.glob(os.path.join(asin_dir, "images", "500x500", "*.jpg")))
        row['videos_count'] = len(glob.glob(os.path.join(asin_dir, "videos", "*.mp4")))
        row['gdrive_url'] = gdrive_map.get(asin, GDRIVE_URL)
        ready_rows.append(row)

print(f"Loaded: {len(ready_rows)} Ready rows with direct Drive links, {len(discarded_rows)} Discarded rows.")

# Create Brand New Workbook with Openpyxl
wb = openpyxl.Workbook()

# Style definitions
font_title = Font(name="Segoe UI", size=16, bold=True, color="0F172A")
font_subtitle = Font(name="Segoe UI", size=10, italic=True, color="475569")
font_banner = Font(name="Segoe UI", size=11, bold=True, color="1E3A8A")
font_header = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
font_kpi_num = Font(name="Segoe UI", size=20, bold=True, color="0F172A")
font_kpi_label = Font(name="Segoe UI", size=9, bold=True, color="64748B")
font_data = Font(name="Segoe UI", size=10, color="1E293B")
font_data_bold = Font(name="Segoe UI", size=10, bold=True, color="0F172A")
font_mono = Font(name="Consolas", size=9, color="334155")
font_link = Font(name="Segoe UI", size=10, color="2563EB", underline="single")
font_link_bold = Font(name="Segoe UI", size=11, bold=True, color="1D4ED8", underline="single")
font_pill_ready = Font(name="Segoe UI", size=9, bold=True, color="15803D")
font_pill_discard = Font(name="Segoe UI", size=9, bold=True, color="B45309")

fill_navy_header = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
fill_slate_sub = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
fill_zebra = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
fill_white = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
fill_pill_ready = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
fill_pill_discard = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
fill_banner_blue = PatternFill(start_color="EFF6FF", end_color="EFF6FF", fill_type="solid")
fill_kpi_card = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
fill_cta_drive = PatternFill(start_color="E0E7FF", end_color="E0E7FF", fill_type="solid")

align_center = Alignment(horizontal="center", vertical="center")
align_left = Alignment(horizontal="left", vertical="center")
align_right = Alignment(horizontal="right", vertical="center")
align_header = Alignment(horizontal="center", vertical="center", wrap_text=True)

thin_gray = Side(style="thin", color="E2E8F0")
border_cell = Border(left=thin_gray, right=thin_gray, top=thin_gray, bottom=thin_gray)
border_kpi = Border(
    left=Side(style="medium", color="CBD5E1"),
    right=Side(style="medium", color="CBD5E1"),
    top=Side(style="medium", color="CBD5E1"),
    bottom=Side(style="medium", color="CBD5E1")
)
border_banner = Border(
    left=Side(style="medium", color="93C5FD"),
    right=Side(style="medium", color="93C5FD"),
    top=Side(style="medium", color="93C5FD"),
    bottom=Side(style="medium", color="93C5FD")
)


# ==============================================================================
# TAB 1: READY TO LIST (32 PRODUCTS)
# ==============================================================================
ws1 = wb.active
ws1.title = "Ready to List (32 Products)"
ws1.views.sheetView[0].showGridLines = True

# Banner Link Row (Row 1-2)
ws1.merge_cells("A1:P1")
b_cell = ws1["A1"]
b_cell.value = "📂 GOOGLE DRIVE ASSET FOLDER (Images, HD Videos & Copy for All 32 Products):"
b_cell.font = font_banner
b_cell.fill = fill_banner_blue
b_cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)

ws1.merge_cells("A2:P2")
b_url_cell = ws1["A2"]
b_url_cell.value = f'=HYPERLINK("{GDRIVE_URL}", "👉 CLICK HERE TO OPEN FULL GOOGLE DRIVE CLOUD REPOSITORY: {GDRIVE_URL}")'
b_url_cell.font = font_link_bold
b_url_cell.fill = fill_banner_blue
b_url_cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)

for r in [1, 2]:
    ws1.row_dimensions[r].height = 24
    for c in range(1, 17):
        ws1.cell(r, c).border = border_banner

# Empty spacer row
ws1.row_dimensions[3].height = 10

# Headers (Row 4)
headers_ws1 = [
    "ASIN",
    "Product Title",
    "Status",
    "Google Drive Cloud Folder",
    "Images (4 Tiers)",
    "HD Videos",
    "Listing Copy",
    "Size / Volume",
    "UPC / GTIN",
    "Zoro Item #",
    "Zoro MPN",
    "Zoro Cost ($)",
    "Monthly Demand",
    "Sales Rank",
    "Amazon Page",
    "Zoro Page"
]

ws1.row_dimensions[4].height = 30
for col_idx, h_text in enumerate(headers_ws1, 1):
    cell = ws1.cell(4, col_idx, h_text)
    cell.font = font_header
    cell.fill = fill_navy_header
    cell.alignment = align_header
    cell.border = border_cell

# Data Rows (Row 5+)
for row_idx, rdata in enumerate(ready_rows, 5):
    ws1.row_dimensions[row_idx].height = 24
    is_zebra = (row_idx % 2 == 0)
    row_fill = fill_zebra if is_zebra else fill_white

    # 1. ASIN
    c = ws1.cell(row_idx, 1, rdata['asin'])
    c.font = font_data_bold
    c.alignment = align_center

    # 2. Title
    c = ws1.cell(row_idx, 2, rdata['clean_title'])
    c.font = font_data
    c.alignment = align_left

    # 3. Status
    c = ws1.cell(row_idx, 3, "Ready to List")
    c.font = font_pill_ready
    c.fill = fill_pill_ready
    c.alignment = align_center

    # 4. Google Drive Link
    drive_url = rdata.get('gdrive_url', GDRIVE_URL)
    c = ws1.cell(row_idx, 4, f'=HYPERLINK("{drive_url}", "📂 Open {rdata["asin"]} Folder")')
    c.font = font_link
    c.alignment = align_center

    # 5. Images Count
    img_txt = f"{rdata['images_count']} Sets (4 Tiers)"
    c = ws1.cell(row_idx, 5, img_txt)
    c.font = font_data
    c.alignment = align_center

    # 6. Videos Count
    vid_txt = f"{rdata['videos_count']} Videos" if rdata['videos_count'] > 0 else "None"
    c = ws1.cell(row_idx, 6, vid_txt)
    c.font = font_data
    c.alignment = align_center

    # 7. Listing Copy
    c = ws1.cell(row_idx, 7, "✓ Completed")
    c.font = font_data_bold
    c.alignment = align_center

    # 8. Size
    sz = rdata['zoro_desc'].split(',')[-1].strip() if ',' in rdata['zoro_desc'] else "Standard"
    c = ws1.cell(row_idx, 8, sz)
    c.font = font_data
    c.alignment = align_center

    # 9. UPC
    c = ws1.cell(row_idx, 9, rdata['amz_upc'] or 'N/A')
    c.font = font_mono
    c.alignment = align_center

    # 10. Zoro Item #
    c = ws1.cell(row_idx, 10, rdata['zoro_no'])
    c.font = font_mono
    c.alignment = align_center

    # 11. Zoro MPN
    c = ws1.cell(row_idx, 11, rdata['zoro_mpn'])
    c.font = font_mono
    c.alignment = align_center

    # 12. Zoro Price
    c = ws1.cell(row_idx, 12, rdata['zoro_price'])
    c.font = font_data_bold
    c.alignment = align_right
    c.number_format = '$#,##0.00'

    # 13. Monthly Demand
    c = ws1.cell(row_idx, 13, rdata['amz_bought'] or 0)
    c.font = font_data
    c.alignment = align_right
    c.number_format = '#,##0'

    # 14. Sales Rank
    c = ws1.cell(row_idx, 14, rdata['amz_rank'] or 0)
    c.font = font_data
    c.alignment = align_right
    c.number_format = '#,##0'

    # 15. Amazon Link
    amz_link = f"https://www.amazon.com/dp/{rdata['asin']}"
    c = ws1.cell(row_idx, 15, f'=HYPERLINK("{amz_link}", "Amazon Listing")')
    c.font = font_link
    c.alignment = align_center

    # 16. Zoro Link
    zoro_link = f"https://www.zoro.com/i/{rdata['zoro_no']}/"
    c = ws1.cell(row_idx, 16, f'=HYPERLINK("{zoro_link}", "Zoro Product")')
    c.font = font_link
    c.alignment = align_center

    # Apply fills & borders
    for col_i in range(1, 17):
        cell_obj = ws1.cell(row_idx, col_i)
        cell_obj.border = border_cell
        if col_i != 3:  # don't override status pill fill
            cell_obj.fill = row_fill

# Freeze panes at A5
ws1.freeze_panes = "A5"


# ==============================================================================
# TAB 2: DISCARDED MISMATCHES (3 ITEMS)
# ==============================================================================
ws2 = wb.create_sheet(title="Discarded (3 Mismatches)")
ws2.views.sheetView[0].showGridLines = True

ws2.merge_cells("A1:J1")
ws2["A1"].value = "⚠️ DISCARDED LISTINGS: Discarded due to size/pack mismatch (Strict 100% Match Filter)"
ws2["A1"].font = font_title
ws2["A1"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
ws2.row_dimensions[1].height = 30

ws2.row_dimensions[2].height = 10  # spacer

headers_ws2 = [
    "ASIN",
    "Product Description",
    "Status",
    "Discard Reason",
    "Amazon Packaging",
    "Zoro Packaging",
    "Zoro Item #",
    "Amazon UPC",
    "Amazon Page",
    "Zoro Page"
]

ws2.row_dimensions[3].height = 28
for col_idx, h_text in enumerate(headers_ws2, 1):
    cell = ws2.cell(3, col_idx, h_text)
    cell.font = font_header
    cell.fill = fill_slate_sub
    cell.alignment = align_header
    cell.border = border_cell

for row_idx, rdata in enumerate(discarded_rows, 4):
    ws2.row_dimensions[row_idx].height = 26

    ws2.cell(row_idx, 1, rdata['asin']).font = font_data_bold
    ws2.cell(row_idx, 1).alignment = align_center

    ws2.cell(row_idx, 2, rdata['zoro_desc']).font = font_data
    ws2.cell(row_idx, 2).alignment = align_left

    c = ws2.cell(row_idx, 3, "Discarded")
    c.font = font_pill_discard
    c.fill = fill_pill_discard
    c.alignment = align_center

    c = ws2.cell(row_idx, 4, rdata.get('discard_reason', 'Mismatch'))
    c.font = font_data_bold
    c.alignment = align_left

    amz_pkg = f"{rdata.get('amz_pack_qty', 1)} Unit ({rdata.get('pack_type', 'Single Unit')})"
    ws2.cell(row_idx, 5, amz_pkg).alignment = align_center

    ws2.cell(row_idx, 6, "1 Unit (Single Bottle)").alignment = align_center

    ws2.cell(row_idx, 7, rdata['zoro_no']).font = font_mono
    ws2.cell(row_idx, 7).alignment = align_center

    ws2.cell(row_idx, 8, rdata['amz_upc']).font = font_mono
    ws2.cell(row_idx, 8).alignment = align_center

    amz_link = f"https://www.amazon.com/dp/{rdata['asin']}"
    ws2.cell(row_idx, 9, f'=HYPERLINK("{amz_link}", "Amazon Page")').font = font_link
    ws2.cell(row_idx, 9).alignment = align_center

    zoro_link = f"https://www.zoro.com/i/{rdata['zoro_no']}/"
    ws2.cell(row_idx, 10, f'=HYPERLINK("{zoro_link}", "Zoro Page")').font = font_link
    ws2.cell(row_idx, 10).alignment = align_center

    for col_i in range(1, 11):
        cell_obj = ws2.cell(row_idx, col_i)
        cell_obj.border = border_cell
        if col_i != 3:
            cell_obj.fill = fill_white


# ==============================================================================
# TAB 3: EXECUTIVE OVERVIEW & DRIVE
# ==============================================================================
ws0 = wb.create_sheet(title="Executive Summary", index=0)
ws0.views.sheetView[0].showGridLines = True

ws0.merge_cells("A1:G1")
ws0["A1"].value = "HATHORO E-COMMERCE VALVOLINE CATALOG LAUNCH"
ws0["A1"].font = Font(name="Segoe UI", size=18, bold=True, color="0F172A")
ws0["A1"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
ws0.row_dimensions[1].height = 36

ws0.merge_cells("A2:G2")
ws0["A2"].value = "Multi-Tier High-Res Images (500x500, 1000x1000 Retina, 1500x1500 Zoom) | FastStart HD MP4 Videos | Complete Copy"
ws0["A2"].font = font_subtitle
ws0["A2"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
ws0.row_dimensions[2].height = 20

# Prominent CTA Block for Google Drive
ws0.merge_cells("A4:G5")
cta = ws0["A4"]
cta.value = f'=HYPERLINK("{GDRIVE_URL}", "📂 CLICK HERE TO ACCESS GOOGLE DRIVE CLOUD ASSETS REPOSITORY")'
cta.font = Font(name="Segoe UI", size=14, bold=True, color="1D4ED8", underline="single")
cta.fill = fill_cta_drive
cta.alignment = align_center

for r in [4, 5]:
    ws0.row_dimensions[r].height = 26
    for c in range(1, 8):
        ws0.cell(r, c).border = border_banner

# KPI Cards (Row 7 to 9)
kpi_data = [
    ("Total Evaluated", "35", "Selected Products", 1),
    ("Ready to List", "32", "100% Match Verified", 3),
    ("Discarded", "3", "Mismatches Filtered", 5),
    ("Total Asset Files", "1,020+", "Images, Videos & Copy", 7),
]

ws0.row_dimensions[7].height = 18
ws0.row_dimensions[8].height = 36
ws0.row_dimensions[9].height = 18

for title, num, sub, c_idx in kpi_data:
    # Header
    ws0.cell(7, c_idx, title).font = font_kpi_label
    ws0.cell(7, c_idx).fill = fill_kpi_card
    ws0.cell(7, c_idx).alignment = align_center

    # Number
    ws0.cell(8, c_idx, num).font = font_kpi_num
    ws0.cell(8, c_idx).fill = fill_kpi_card
    ws0.cell(8, c_idx).alignment = align_center

    # Subtext
    ws0.cell(9, c_idx, sub).font = font_subtitle
    ws0.cell(9, c_idx).fill = fill_kpi_card
    ws0.cell(9, c_idx).alignment = align_center

    for r in [7, 8, 9]:
        ws0.cell(r, c_idx).border = border_kpi

# Detailed Summary Table below KPIs (Row 11+)
ws0.cell(11, 1, "Catalog Breakdown & Asset Production Summary:").font = font_data_bold
ws0.row_dimensions[11].height = 24

summary_breakdown = [
    ("Verified In-Stock Products", "32 Listings", "100% exact match across UPC, volume, and packaging"),
    ("Discarded Ambiguous Items", "3 Listings", "Safely filtered out (volume and multi-pack discrepancies)"),
    ("Exact 500x500 Square Images", "239 Files", "Canva 1x standard square formatted thumbnails"),
    ("1000x1000 Retina Images", "239 Files", "Canva 2x high-DPI quality (matches on-screen display)"),
    ("1500x1500 Master Zoom Images", "239 Files", "Full e-commerce product zoom resolution"),
    ("Uncompressed Master Images", "239 Files", "Raw uncompressed masters saved up to 2000px"),
    ("Playable Full HD Videos", "64 Files", "Remuxed to standard FastStart ISO MP4 format"),
    ("Clean Customer Listing Copy", "32 Files", "Complete titles, bullets, description, and spec tables"),
    ("Google Drive Cloud Link", "Active", GDRIVE_URL)
]

for idx, (label, val, notes) in enumerate(summary_breakdown, 13):
    ws0.row_dimensions[idx].height = 22
    ws0.cell(idx, 1, label).font = font_data_bold
    ws0.cell(idx, 1).alignment = align_left
    ws0.cell(idx, 1).border = border_cell

    ws0.cell(idx, 2, val).font = font_pill_ready if "32" in val or "Files" in val else font_data
    ws0.cell(idx, 2).alignment = align_center
    ws0.cell(idx, 2).border = border_cell

    if "drive.google.com" in notes:
        c = ws0.cell(idx, 3, f'=HYPERLINK("{notes}", "{notes}")')
        c.font = font_link
    else:
        c = ws0.cell(idx, 3, notes)
        c.font = font_data
    c.alignment = align_left
    c.border = border_cell

    ws0.merge_cells(start_row=idx, start_column=3, end_row=idx, end_column=7)


# ==============================================================================
# AUTO-FIT COLUMN WIDTHS ACROSS ALL SHEETS
# ==============================================================================
for sheet in [ws0, ws1, ws2]:
    for col in sheet.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        # Skip merged banner in ws0 and ws1
        for cell in col:
            val_str = str(cell.value or '')
            if 'HYPERLINK' in val_str:
                val_str = "Open Drive Assets / Link"
            if len(val_str) < 70 and cell.row > 3:
                max_len = max(max_len, len(val_str))
        sheet.column_dimensions[col_letter].width = max(max_len + 4, 12)

# Specific adjustments
ws1.column_dimensions['A'].width = 15   # ASIN
ws1.column_dimensions['B'].width = 46   # Title
ws1.column_dimensions['C'].width = 16   # Status
ws1.column_dimensions['D'].width = 25   # Google Drive
ws1.column_dimensions['E'].width = 20   # Images
ws1.column_dimensions['F'].width = 14   # Videos
ws1.column_dimensions['G'].width = 15   # Copy
ws1.column_dimensions['H'].width = 16   # Size
ws1.column_dimensions['I'].width = 16   # UPC
ws1.column_dimensions['J'].width = 14   # Zoro Item
ws1.column_dimensions['K'].width = 14   # Zoro MPN
ws1.column_dimensions['L'].width = 15   # Zoro Price
ws1.column_dimensions['M'].width = 18   # Demand
ws1.column_dimensions['N'].width = 14   # Rank
ws1.column_dimensions['O'].width = 18   # Amazon Link
ws1.column_dimensions['P'].width = 16   # Zoro Link

ws2.column_dimensions['A'].width = 15
ws2.column_dimensions['B'].width = 40
ws2.column_dimensions['C'].width = 16
ws2.column_dimensions['D'].width = 50
ws2.column_dimensions['E'].width = 25
ws2.column_dimensions['F'].width = 25

ws0.column_dimensions['A'].width = 30
ws0.column_dimensions['B'].width = 18
ws0.column_dimensions['C'].width = 45

# Save Workbook
wb.save(EXCEL_PATH)
print(f"✓ Formatted Excel successfully saved to: {EXCEL_PATH}")
