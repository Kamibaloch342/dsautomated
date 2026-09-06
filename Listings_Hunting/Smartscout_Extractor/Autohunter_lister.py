# =======================================================================================
#    --- 🚀 ZORO HUNTER & INTELLIGENT AMAZON LISTER (v4.4 - FINAL) 🚀 ---
# =======================================================================================
# VERSION 4.4 CHANGES:
# 1. [NEW LOGIC] Added a price reality check: Skips items if the Zoro price is
#    more than 40% higher than the Amazon offer price.
# 2. [LOGIC CHANGE] SKU, Zoro Code, and Selling Price are now saved even if the
#    listing fails (status "Approval Needed").
# 3. [TEXT CHANGE] Status "Not Found on Zoro" is now "Couldn't found on Zoro".
# 4. [TEXT CHANGE] Status "Listing Failed" is now "Approval Needed".
# 5. [NEW COLUMN] Added 'Profit Margin$' column for successfully listed items.
# 6. [NEW COLUMN] Added 'Price Increased to make 8% Margin' (yes/no) column.
# 7. [NEW FEATURE] Integrated an "Out of Stock" (OOS) check on Zoro.
# 8. [NEW FEATURE] Added a check against an Amazon "All Listings Report".
# =======================================================================================

# --- Core Libraries ---
import pandas as pd
import time
import re
import json
import os
import sys
import subprocess
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.action_chains import ActionChains

# =======================================================================================
#                     --- 🛠️ MASTER CONFIGURATION 🛠️ ---
# =======================================================================================

# --- File & Path Configuration ---
EXCEL_FILE_PATH = "/Users/kamranbaloch/Downloads/Kamran Macos/Dropshipping/Development_Unit/Listings_Hunting/Smartscout_Extractor/SEKEGE.xlsx"
ALL_LISTINGS_REPORT_PATH = "/Users/kamranbaloch/Downloads/all_listings_report.txt" 

# --- Browser & Profile Paths ---
DEBUGGING_PORT = 9223
user_profile_dir = os.path.expanduser("~")
if sys.platform == "darwin":
    PROFILE_PATH = os.path.join(user_profile_dir, "Library", "Application Support", "Google", "Chrome", "hathoro_orders")
    CHROME_EXECUTABLE_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
else: # Assuming Windows
    PROFILE_PATH = os.path.join(user_profile_dir, "AppData", "Local", "Google", "Chrome", "User Data", "hoank_orders")
    CHROME_EXECUTABLE_PATH = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"

# --- Listing & Pricing Configuration ---
SKU_PREFIX = "Kam"
DEFAULT_AMAZON_QUANTITY = 33
AMAZON_REFERRAL_FEE_PERCENT = 0.15
PROFIT_MARGIN_TARGET = 0.08
PROFIT_MARGIN_MINIMUM = 0.06
ZORO_SELECT_PROFIT_MARGIN_ACCEPTABLE = -0.08

# --- Zoro Configuration ---
ZORO_SHIPPING_COST = 5.00
ZORO_FREE_SHIPPING_MINIMUM = 50.00
ZORO_BASE_SEARCH_URL = "https://www.zoro.com/search?q="

# --- Selenium Selectors ---
ZORO_PRODUCT_JSON_SELECTOR = (By.CSS_SELECTOR, 'script[data-za="product-microdata"]')
ZORO_OOS_SELECTOR = (By.XPATH, "//*[contains(text(), 'Notify me when this product is back in stock')]")

# =======================================================================================
#               --- 🧩 HELPER FUNCTIONS (Replicated from Stand-Alone Lister) 🧩 ---
# =======================================================================================

def setup_driver():
    """Initializes WebDriver by attaching to a pre-started Chrome instance."""
    print("🚀 Initializing WebDriver with Stealth Method...")
    command = (f'"{CHROME_EXECUTABLE_PATH}" --remote-debugging-port={DEBUGGING_PORT} --user-data-dir="{PROFILE_PATH}"')
    try:
        chrome_process = subprocess.Popen(command, shell=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
        print(f"  -> Chrome process started with PID: {chrome_process.pid}")
        time.sleep(5)
    except Exception as e:
        print(f"❌ Could not launch Chrome: {e}")
        return None, None
    try:
        chrome_options = ChromeOptions()
        chrome_options.add_experimental_option("debuggerAddress", f"127.0.0.1:{DEBUGGING_PORT}")
        driver = webdriver.Chrome(options=chrome_options)
        print("✅ Selenium connected to existing Chrome session successfully.")
        return driver, chrome_process
    except WebDriverException as e:
        print(f"❌ Error connecting Selenium to Chrome. Is another script using port {DEBUGGING_PORT}? Error: {e}")
        if 'chrome_process' in locals() and chrome_process.poll() is None:
            chrome_process.terminate()
        return None, None

def get_zoro_product_details(driver, wait, upc):
    """Searches Zoro.com and extracts product data, now with an OOS check."""
    print(f"  -> Searching Zoro for UPC: {upc}...")
    driver.get(f"{ZORO_BASE_SEARCH_URL}{upc}")
    time.sleep(5)

    try:
        WebDriverWait(driver, 7).until(EC.visibility_of_element_located(ZORO_OOS_SELECTOR))
        print("  -> Result: Product is Out of Stock (OOS).")
        return {'status': 'OOS'} 
    except TimeoutException:
        pass 
    
    try:
        script_element = wait.until(EC.presence_of_element_located(ZORO_PRODUCT_JSON_SELECTOR))
        json_text = script_element.get_attribute('innerHTML')
        data = json.loads(json_text)
        
        price = float(data.get('offers', {}).get('price', 0.0))
        zoro_code = data.get('sku', 'NOT_FOUND').strip()
        brand = data.get('brand', {}).get('name', 'UNKNOWN').strip()
        
        if price > 0 and zoro_code != 'NOT_FOUND':
            print(f"  -> Result: Found! Price=${price:.2f}, Brand='{brand}', Zoro Code='{zoro_code}'")
            return {'price': price, 'zoro_code': zoro_code, 'brand': brand, 'status': 'FOUND'}
        else:
            print("  -> Result: Product page found, but data was invalid (Price=0 or Code=NotFound).")
            return None
    except TimeoutException:
        print("  -> Result: Not a valid product page (Timed out looking for data).")
        return None
    except Exception as e:
        print(f"  -> FAILED: An unexpected error occurred while parsing product details: {e}")
        return None

def list_product_on_amazon(driver, wait, asin, sku, selling_price):
    """
    Lists product on Amazon, exactly replicating the trusted stand-alone script's logic.
    """
    print(f"  -> Starting Amazon listing for ASIN: {asin}")
    driver.get(f"https://sellercentral.amazon.com/abis/listing/syh/offer?asin={asin}")
    
    try:
        print("     - Waiting for listing form to load...")
        sku_host_selector = 'kat-input[name="contribution_sku-0-value"]'
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, sku_host_selector)))
        print("     - Listing form loaded successfully.")
        time.sleep(2)

        if not _type_in_shadow_dom(driver, wait, sku_host_selector, 'input', sku):
            raise Exception("SKU field not found.")
        time.sleep(1)

        print("     - Selecting 'I'll ship this item myself' (Merchant Fulfilled)...")
        mfn_radio_element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'kat-radiobutton[value="DEFAULT"]')))
        ActionChains(driver).move_to_element(mfn_radio_element).click().perform()
        print("     - Click sent. Waiting for page to react...")
        time.sleep(5)

        price_host_selector = 'kat-input[name*="our_price"]'
        if not _type_price_in_shadow_dom(driver, wait, price_host_selector, 'input', selling_price):
            raise Exception("Price field not found.")
        time.sleep(1)
        
        print("     - Validating Price by clicking Quantity field...")
        quantity_host_selector = 'kat-input[id*="quantity"]'
        quantity_host_element = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, quantity_host_selector)))
        quantity_host_element.click()
        time.sleep(2)

        if not _set_value_in_shadow_dom(driver, wait, quantity_host_selector, 'input', str(DEFAULT_AMAZON_QUANTITY)):
             raise Exception("Quantity field not found.")
        time.sleep(1)

        print("     - Validating Quantity by clicking Condition field...")
        condition_dropdown_host = 'kat-dropdown[name="condition_type-0-value"]'
        condition_host_element = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, condition_dropdown_host)))
        condition_host_element.click()
        time.sleep(2)

        print("     - Setting item condition to 'New'...")
        driver.execute_script('arguments[0].value = "new_new"; arguments[0].dispatchEvent(new Event("change", { bubbles: true }));', condition_host_element)
        time.sleep(1)
        ActionChains(driver).send_keys(Keys.ENTER).perform()
        print("     - Waiting for page to react...")
        time.sleep(5)

        print("     - Clicking 'Submit'...")
        submit_button_host = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'kat-button[data-testid="submit-button"]')))
        driver.execute_script("arguments[0].shadowRoot.querySelector('button').click();", submit_button_host)
        print("     - Waiting for submission to complete...")
        time.sleep(15)

        print(f"  ✅ SUCCESS: Listing submitted for SKU: {sku}")
        return True

    except Exception as e:
        print(f"  ❌ FAILED: An error occurred during the listing process. Error: {str(e).splitlines()[0]}")
        return False

# --- Shadow DOM Helpers (Replicated) ---
def _type_in_shadow_dom(driver, wait, host_selector, inner_selector, text):
    host_element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, host_selector)))
    driver.execute_script(f'arguments[0].shadowRoot.querySelector("{inner_selector}").focus()', host_element)
    time.sleep(0.5)
    ActionChains(driver).send_keys(text).perform()
    return True

def _type_price_in_shadow_dom(driver, wait, host_selector, inner_selector, price):
    formatted_price = f"{float(price):.2f}"
    host_element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, host_selector)))
    driver.execute_script(f'arguments[0].shadowRoot.querySelector("{inner_selector}").focus()', host_element)
    time.sleep(0.5)
    ActionChains(driver).send_keys(formatted_price).perform()
    return True
    
def _set_value_in_shadow_dom(driver, wait, host_selector, inner_selector, value):
    host_element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, host_selector)))
    script = f"arguments[0].shadowRoot.querySelector('{inner_selector}').value = `{value}`;"
    driver.execute_script(script, host_element)
    return True

# =======================================================================================
#                           --- ▶️ MAIN EXECUTION SCRIPT ---
# =======================================================================================
if __name__ == "__main__":
    print("="*60 + "\n🚀 ZORO HUNTER & INTELLIGENT AMAZON LISTER (v4.4) 🚀\n" + "="*60)
    
    driver, chrome_process = setup_driver()
    if not driver:
        print("❌ FATAL ERROR: Could not start WebDriver. Exiting.")
        exit()
    wait = WebDriverWait(driver, 45)

    try:
        driver.get("https://sellercentral.amazon.com/home")
        if "ap/signin" in driver.current_url:
            input("[ACTION] Please log in to Amazon Seller Central, then press Enter here...")
        print("[SUCCESS] Logged into Amazon.")

        print("\n[INFO] Reading data from Excel file...")
        df = pd.read_excel(EXCEL_FILE_PATH)
        
        required_cols = ['ASIN', 'UPC', 'Offer Price']
        if not all(col in df.columns for col in required_cols):
            raise ValueError(f"Excel file must contain columns: {', '.join(required_cols)}")
        
        if 'Status' not in df.columns: df['Status'] = ''
        if 'Profit Margin$' not in df.columns: df['Profit Margin$'] = ''
        if 'Price Increased to make 8% Margin' not in df.columns: df['Price Increased to make 8% Margin'] = ''

        df['UPC'] = df['UPC'].apply(lambda x: str(int(x)) if pd.notna(x) else '')
        print(f"[SUCCESS] Loaded {len(df)} items to process.")

        existing_asins = set()
        if ALL_LISTINGS_REPORT_PATH and os.path.exists(ALL_LISTINGS_REPORT_PATH):
            try:
                print(f"[INFO] Reading your Amazon Listings Report from: {ALL_LISTINGS_REPORT_PATH}")
                listings_df = pd.read_csv(ALL_LISTINGS_REPORT_PATH, sep='\t', on_bad_lines='warn', low_memory=False)
                if 'asin1' in listings_df.columns:
                    existing_asins = set(listings_df['asin1'].dropna().astype(str))
                    print(f"[SUCCESS] Loaded {len(existing_asins)} existing ASINs from your report.")
                else:
                    print("[WARNING] 'asin1' column not found in the report. Cannot check for existing listings.")
            except Exception as e:
                print(f"[WARNING] Could not read the listings report. Skipping this check. Error: {e}")
        else:
            print("[INFO] No 'All Listings Report' provided. The script will not check for existing listings.")
        
        print("\n" + "="*60 + "\n🏁 STARTING TO PROCESS PRODUCTS 🏁\n" + "="*60)
        for index, row in df.iterrows():
            if pd.notna(row.get('Status')) and row.get('Status') != '':
                print(f"\n▶️ SKIPPING Item {index + 1}/{len(df)} | ASIN: {row['ASIN']} (Already processed with status: '{row['Status']}')")
                continue

            asin = str(row.get("ASIN", "")).strip()
            upc = str(row.get("UPC", "")).strip()
            offer_price = pd.to_numeric(row.get("Offer Price"), errors='coerce')

            if not all([asin, upc, pd.notna(offer_price)]):
                df.loc[index, 'Status'] = 'Missing ASIN, UPC, or Offer Price'
                continue
            
            print(f"\n▶️ Processing Item {index + 1}/{len(df)} | ASIN: {asin}")

            if asin in existing_asins:
                print(f"  -> ⏭️  SKIPPED: ASIN {asin} is already in your listings report.")
                df.loc[index, 'Status'] = 'Already Listed'
                df.to_excel(EXCEL_FILE_PATH, index=False)
                continue

            zoro_details = get_zoro_product_details(driver, wait, upc)
            
            if zoro_details and zoro_details.get('status') == 'OOS':
                df.loc[index, 'Status'] = 'Out of Stock on Zoro'
            elif zoro_details and zoro_details.get('status') == 'FOUND':
                # <-- MODIFICATION: Add the 40% price difference check here.
                zoro_price = zoro_details['price']
                if zoro_price > (offer_price * 1.40):
                    print(f"  -> ❌ SKIP: Zoro price ${zoro_price:.2f} is >40% higher than Amazon offer price ${offer_price:.2f}.")
                    df.loc[index, 'Status'] = 'Price Mismatch >40%'
                    df.to_excel(EXCEL_FILE_PATH, index=False)
                    time.sleep(3)
                    continue
                
                shipping = ZORO_SHIPPING_COST if zoro_price < ZORO_FREE_SHIPPING_MINIMUM else 0
                zoro_cost = zoro_price + shipping
                brand = zoro_details['brand']
                
                acceptable_margin = ZORO_SELECT_PROFIT_MARGIN_ACCEPTABLE if "Zoro Select" in brand else PROFIT_MARGIN_MINIMUM
                referral_fee = offer_price * AMAZON_REFERRAL_FEE_PERCENT
                profit = offer_price - (zoro_cost + referral_fee)
                margin_at_offer_price = profit / offer_price if offer_price > 0 else 0

                list_item = False
                price_was_increased = False
                
                if margin_at_offer_price >= acceptable_margin:
                    if margin_at_offer_price > PROFIT_MARGIN_TARGET:
                        final_selling_price = offer_price - 0.02
                        list_item = True
                        print(f"  -> ✅ UNDERCUT: Margin is {margin_at_offer_price:.2%}. Listing at ${final_selling_price:.2f} (was ${offer_price:.2f}).")
                    else:
                        final_selling_price = offer_price
                        list_item = True
                        print(f"  -> ✅ MATCH: Margin is {margin_at_offer_price:.2%}. Listing at ${final_selling_price:.2f}.")
                elif "Zoro Select" not in brand:
                    final_selling_price = zoro_cost / (1 - AMAZON_REFERRAL_FEE_PERCENT - PROFIT_MARGIN_TARGET)
                    list_item = True
                    price_was_increased = True
                    print(f"  -> 📈 REPRICE: Margin too low. Listing at ${final_selling_price:.2f} to reach {PROFIT_MARGIN_TARGET:.0%} (was ${offer_price:.2f}).")
                else:
                    print(f"  -> ❌ SKIP: Zoro Select margin {margin_at_offer_price:.2%} is below the {acceptable_margin:.2%} threshold.")
                    df.loc[index, 'Status'] = 'Not Profitable'

                if list_item:
                    current_date_str = datetime.now().strftime("%d%b%y").lower()
                    zoro_code = zoro_details['zoro_code']
                    sku = f"{SKU_PREFIX}-{current_date_str}-{zoro_code}"

                    is_listed = list_product_on_amazon(driver, wait, asin, sku, final_selling_price)
                    
                    df.loc[index, 'Status'] = 'Listed' if is_listed else 'Approval Needed'

                    df.loc[index, 'SKU'] = sku
                    df.loc[index, 'Selling_Price'] = f"{final_selling_price:.2f}"
                    df.loc[index, 'Zoro_Code'] = zoro_code
                    
                    if is_listed:
                        final_profit = final_selling_price - (zoro_cost + (final_selling_price * AMAZON_REFERRAL_FEE_PERCENT))
                        df.loc[index, 'Profit Margin$'] = f"{final_profit:.2f}"
                        df.loc[index, 'Price Increased to make 8% Margin'] = 'yes' if price_was_increased else 'no'
            else:
                df.loc[index, 'Status'] = "Couldn't found on Zoro"
                
            df.to_excel(EXCEL_FILE_PATH, index=False)
            time.sleep(3)
            
    except Exception as e:
        print(f"\n❌ A critical error occurred in the main process: {e}")
    finally:
        print("\n" + "="*60 + "\n🎉 All items have been processed! 🎉\n" + "="*60)
        if driver:
            driver.quit()
        if 'chrome_process' in locals() and chrome_process.poll() is None:
            chrome_process.terminate()
            print("[INFO] Chrome process terminated.")