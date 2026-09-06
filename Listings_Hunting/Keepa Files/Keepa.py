import pandas as pd

import sqlite3

from pathlib import Path

from openpyxl.styles import Font, Border, Side, Alignment

import numpy as np

import time



# --- 1. CONFIGURATION ---

tsv_folder_path = Path("/Users/kamranbaloch/Downloads/Kamran Macos/Dropshipping/TSV Files")

db_path = tsv_folder_path / "zoro_combined.db"



# Input Files

keepa_excel_path = Path("Development_Unit/Listings_Hunting/Keepa Files/Keepa Listings.xlsx")

# The script uses the current date in the filename for the listings report.

# Using a fixed name as per your previous request.

all_listings_report_path = Path(f"/Users/kamranbaloch/Downloads/Kamran Macos/Dropshipping/Development_Unit/Listings_Hunting/All+Listings+Report+09-02-2025.txt")





# Output File

output_excel_path = Path("Development_Unit/Listings_Hunting/PROFITABILITY_REPORT_V7.xlsx")



# --- 2. FORMATTING FUNCTION ---

def format_excel_output(writer, df, sheet_name):

    df.to_excel(writer, sheet_name=sheet_name, index=False)

    # This function remains the same...

    workbook = writer.book

    worksheet = writer.sheets[sheet_name]

    header_font = Font(name='Calibri', size=11, bold=True)

    body_font = Font(name='Calibri', size=10)

    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))

    worksheet.sheet_view.showGridLines = False

    for cell in worksheet[1]:

        cell.font = header_font

        cell.border = thin_border

        cell.alignment = Alignment(horizontal='center', vertical='center')

    for row in worksheet.iter_rows(min_row=2, max_row=worksheet.max_row, max_col=worksheet.max_column):

        for cell in row:

            cell.font = body_font

            cell.border = thin_border

    for column_cells in worksheet.columns:

        max_length = 0

        column = column_cells[0].column_letter

        for cell in column_cells:

            try:

                if len(str(cell.value)) > max_length:

                    max_length = len(str(cell.value))

            except: pass

        adjusted_width = (max_length + 2)

        worksheet.column_dimensions[column].width = adjusted_width



# --- 3. MAIN SCRIPT LOGIC ---

def main():

    if not db_path.exists():

        print(f"Database not found. Creating it now...")

        # ... database creation logic remains the same ...

    else:

        print(f"Database already exists. Skipping creation.")



    print("\nLoading data into memory...")

    try:

        keepa_df = pd.read_excel(keepa_excel_path)

        

        if not all_listings_report_path.exists():

            print(f"FATAL ERROR: Your listings report was not found at the specified path:")

            print(f"{all_listings_report_path}")

            return

            

        listings_df = pd.read_csv(all_listings_report_path, sep='\t', on_bad_lines='warn', low_memory=False)

        existing_asins = set(listings_df['asin1'].dropna().astype(str))

        print(f"Loaded {len(existing_asins)} existing ASINs from your listings report.")

        

        with sqlite3.connect(db_path) as conn:

            zoro_df = pd.read_sql_query("SELECT * FROM zoro_products", conn)

        zoro_df['upc_code'] = zoro_df['upc_code'].astype(str).str.strip()

        zoro_df['manufacturer_part_number'] = zoro_df['manufacturer_part_number'].astype(str).str.strip()

        print(f"Loaded {len(keepa_df)} listings from Keepa and {len(zoro_df)} products from Zoro.")



    except Exception as e:

        print(f"An error occurred while loading data: {e}")

        return



    # --- Step 4: Process and Match Listings with DETAILED LOGGING ---

    results = []

    skipped_count = 0

    processed_count = 0

    total_to_process = len(keepa_df)

    

    print("\n--- Starting Detailed Listing-by-Listing Analysis ---\n")

    

    for _, keepa_row in keepa_df.iterrows():

        processed_count += 1

        current_asin = keepa_row.get('ASIN')

        

        print(f"[{processed_count}/{total_to_process}] Processing ASIN: {current_asin}...")



        if current_asin in existing_asins:

            print(f"  [⏭️] SKIPPED: This ASIN is already in your listings report.")

            skipped_count += 1

            continue



        upc = str(keepa_row.get('Product Codes: UPC', '')).strip()

        ean = str(keepa_row.get('Product Codes: EAN', '')).strip()

        gtin = str(keepa_row.get('Product Codes: GTIN', '')).strip()

        keepa_mfr = str(keepa_row.get('Product Codes: PartNumber', '')).strip()

        

        potential_matches = pd.DataFrame()

        initial_match_method = None

        

        if upc or ean or gtin:

            potential_matches = zoro_df[(zoro_df['upc_code'] == upc) | (zoro_df['upc_code'] == ean) | (zoro_df['upc_code'] == gtin)]

            if not potential_matches.empty: initial_match_method = 'CODE'



        if potential_matches.empty and keepa_mfr:

            potential_matches = zoro_df[zoro_df['manufacturer_part_number'] == keepa_mfr]

            if not potential_matches.empty: initial_match_method = 'MFR'



        if potential_matches.empty:

            print(f"  [❌] NO MATCH: No corresponding product found in Zoro database.")

            continue

        

        print(f"  [✔️] MATCH FOUND: Found {len(potential_matches)} potential match(es) via {initial_match_method} method.")



        found_profitable_match = False

        for _, zoro_row in potential_matches.iterrows():

            zoro_no = zoro_row.get('zoro_no')

            if not ('1 day' in str(zoro_row.get('lead_time', '')) and zoro_row.get('availabilityStatus') == 'In Stock' and zoro_row.get('min_order_qty') == 1):

                print(f"    - Discarding Zoro No {zoro_no}: Does not meet stock/lead time criteria.")

                continue

            

            # Profitability calculation...

            match_type = "MFR Match Only" if initial_match_method == 'MFR' else ("Perfect Match" if keepa_mfr == zoro_row['manufacturer_part_number'] else "UPC Match Only")

            amazon_price = keepa_row.get('New: 90 days avg.', 0)

            zoro_price = zoro_row.get('sales_price', 0)

            if not isinstance(amazon_price, (int, float)) or amazon_price <= 0 or not isinstance(zoro_price, (int, float)) or zoro_price <= 0:

                print(f"    - Discarding Zoro No {zoro_no}: Invalid price data.")

                continue

            

            ref_fee_percent = keepa_row.get('Referral Fee %', 15.0)

            if pd.isna(ref_fee_percent) or ref_fee_percent == 0: ref_fee_percent = 15.0

            if 0 < ref_fee_percent < 1: ref_fee_percent *= 100

            

            ref_fee_decimal = ref_fee_percent / 100.0

            zoro_shipping = 5.0 if zoro_price < 50 else 0.0

            profit = amazon_price - (zoro_price + zoro_shipping + (amazon_price * ref_fee_decimal))

            profit_margin = (profit / amazon_price) * 100 if amazon_price > 0 else 0

            

            print(f"    - Analyzing Zoro No {zoro_no}: Profit margin is {profit_margin:.2f}%.")



            if profit_margin < 10.0:

                print(f"      [⚠️] LOW PROFIT: Adding to report with suggested price.")

                denominator = 1 - ref_fee_decimal - 0.10

                suggested_price = (zoro_price + zoro_shipping) / denominator if denominator > 0 else 0

            else:

                print(f"      [✅] PROFITABLE: Adding to report.")

                suggested_price = amazon_price



            zoro_brand = zoro_row.get('zoro_brand_name', '')

            if not zoro_brand or pd.isna(zoro_brand): zoro_brand = "Zoro Select"

            

            results.append({

                'Match Type': match_type, 'ASIN': current_asin, 'Zoro No': zoro_row.get('zoro_no'),

                # ... all other columns remain the same ...

                 'Keepa MFR': keepa_mfr, 'Zoro MFR': zoro_row.get('manufacturer_part_number'),

                'Keepa Brand': keepa_row.get('Brand'), 'Zoro Brand': zoro_brand,

                'Keepa UPC': upc, 'Review Rating': keepa_row.get('Reviews: Rating'),

                'Review Count': keepa_row.get('Reviews: Rating Count'),

                'Bought Last Month': keepa_row.get('Bought in past month'),

                'Buybox Seller': keepa_row.get('Buy Box Seller'),

                'Amazon 90d Avg Price': amazon_price, 'Zoro Price': zoro_price,

                'Referral Fee %': ref_fee_percent, 'Zoro Shipping': zoro_shipping,

                'Profit': profit, 'Profit Margin %': profit_margin,

                'Suggested Price': suggested_price

            })

            found_profitable_match = True



        if not found_profitable_match:

             print(f"  [ℹ️] INFO: Match(es) found, but none passed the filters.")





    # --- Step 5: Create and Save Report ---

    print("\n--- Analysis Complete ---\n")

    if skipped_count > 0:

        print(f"Total Skipped (already listed): {skipped_count}")



    if not results:

        print("No new matching profitable products were found.")

        return

        

    print(f"Found {len(results)} potential new products. Generating Excel report...")

    output_df = pd.DataFrame(results)

    

    numeric_cols = ['Zoro Price', 'Amazon 90d Avg Price', 'Zoro Shipping', 'Profit', 'Profit Margin %', 'Suggested Price', 'Referral Fee %']

    for col in numeric_cols: output_df[col] = pd.to_numeric(output_df[col], errors='coerce').round(2)

    output_df.sort_values(by=['Match Type', 'Profit Margin %'], ascending=[True, False], inplace=True)

    

    try:

        with pd.ExcelWriter(output_excel_path, engine='openpyxl') as writer:

            format_excel_output(writer, output_df, 'Profitability Report')

        print(f"\n✅ Success! Report saved to: {output_excel_path.resolve()}")

    except Exception as e:

        print(f"\nError saving Excel file: {e}")



if __name__ == "__main__":

    main()

