# =======================================================================================
#    --- 📦 DEDICATED AMAZON BULK LISTER (Hardcoded Qty: 100) 📦 ---
# =======================================================================================

import pandas as pd
import time
import os
import sys
import subprocess
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.action_chains import ActionChains

# =======================================================================================
#                     --- 🛠️ CONFIGURATION 🛠️ ---
# =======================================================================================

# Path to your Excel file containing columns: 'ASIN', 'SKU', 'Selling_Price'
EXCEL_FILE_PATH = "/Users/kamranbaloch/Downloads/Kamran Macos/Dropshipping/Development_Unit/Automatic_Listings.xlsx"

# Browser Settings
DEBUGGING_PORT = 9223
FIXED_QUANTITY = 100  # <-- Requested hardcoded quantity

# Profile Paths (Mac/Windows auto-detect)
user_profile_dir = os.path.expanduser("~")
if sys.platform == "darwin":
    PROFILE_PATH = os.path.join(user_profile_dir, "Library", "Application Support", "Google", "Chrome", "hathoro_orders")
    CHROME_EXECUTABLE_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
else:
    PROFILE_PATH = os.path.join(user_profile_dir, "AppData", "Local", "Google", "Chrome", "User Data", "hoank_orders")
    CHROME_EXECUTABLE_PATH = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"

# =======================================================================================
#               --- 🧩 HELPER FUNCTIONS 🧩 ---
# =======================================================================================

def setup_driver():
    """Connects to the existing Chrome session."""
    print("🚀 Connecting to Chrome...")
    command = (f'"{CHROME_EXECUTABLE_PATH}" --remote-debugging-port={DEBUGGING_PORT} --user-data-dir="{PROFILE_PATH}"')
    try:
        chrome_process = subprocess.Popen(command, shell=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
        time.sleep(4)
    except Exception as e:
        print(f"❌ Error launching Chrome process: {e}")
        return None, None

    try:
        chrome_options = ChromeOptions()
        chrome_options.add_experimental_option("debuggerAddress", f"127.0.0.1:{DEBUGGING_PORT}")
        driver = webdriver.Chrome(options=chrome_options)
        print("✅ Successfully connected to Chrome.")
        return driver, chrome_process
    except WebDriverException:
        print(f"❌ Could not connect. Close other Chrome instances using port {DEBUGGING_PORT}.")
        return None, None

# --- Shadow DOM Interaction Helpers ---
def _type_in_shadow_dom(driver, wait, host_selector, inner_selector, text):
    try:
        host_element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, host_selector)))
        driver.execute_script(f'arguments[0].shadowRoot.querySelector("{inner_selector}").focus()', host_element)
        time.sleep(0.2)
        ActionChains(driver).send_keys(str(text)).perform()
        return True
    except: return False

def _set_value_in_shadow_dom(driver, wait, host_selector, inner_selector, value):
    try:
        host_element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, host_selector)))
        driver.execute_script(f"arguments[0].shadowRoot.querySelector('{inner_selector}').value = `{value}`;", host_element)
        return True
    except: return False

# =======================================================================================
#               --- 📝 LISTING FUNCTION 📝 ---
# =======================================================================================

def list_product(driver, wait, asin, sku, price):
    print(f"  -> Starting listing for ASIN: {asin} | SKU: {sku} | Price: {price}")
    driver.get(f"https://sellercentral.amazon.com/abis/listing/syh/offer?asin={asin}")
    
    try:
        # 1. Wait for SKU input to ensure page loaded
        sku_host_selector = 'kat-input[name="contribution_sku-0-value"]'
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, sku_host_selector)))
        except TimeoutException:
            print("     ⚠️ Page load timeout. Requires Approval or invalid ASIN.")
            return False, "Approval Needed / Page Fail"

        # 2. Enter SKU
        if not _type_in_shadow_dom(driver, wait, sku_host_selector, 'input', sku):
            return False, "SKU Field Error"
        
        # 3. Select Merchant Fulfilled (MFN)
        try:
            mfn_radio = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'kat-radiobutton[value="DEFAULT"]')))
            driver.execute_script("arguments[0].click();", mfn_radio) # JavaScript click is more reliable here
        except:
            print("     ⚠️ Could not select MFN. Might be FBA only.")
        
        time.sleep(1)

        # 4. Enter Price
        price_host_selector = 'kat-input[name*="our_price"]'
        if not _type_in_shadow_dom(driver, wait, price_host_selector, 'input', price):
            return False, "Price Field Error"

        # 5. Enter Quantity (FIXED at 100)
        quantity_host_selector = 'kat-input[id*="quantity"]'
        try:
            # Click to activate logic
            wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, quantity_host_selector))).click()
            time.sleep(0.5)
            _set_value_in_shadow_dom(driver, wait, quantity_host_selector, 'input', str(FIXED_QUANTITY))
        except:
            return False, "Quantity Field Error"

        # 6. Select Condition (New)
        condition_dropdown_host = 'kat-dropdown[name="condition_type-0-value"]'
        try:
            condition_ele = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, condition_dropdown_host)))
            condition_ele.click()
            driver.execute_script('arguments[0].value = "new_new"; arguments[0].dispatchEvent(new Event("change", { bubbles: true }));', condition_ele)
            ActionChains(driver).send_keys(Keys.ENTER).perform()
        except:
            return False, "Condition Field Error"

        # --- 🛑 10 SECOND WAIT FOR VALIDATION ---
        print("     - Waiting 10 seconds for validation...")
        time.sleep(10)
        # -----------------------------------------

        # 7. Submit
        print("     - Submitting...")
        submit_host = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'kat-button[data-testid="submit-button"]')))
        driver.execute_script("arguments[0].shadowRoot.querySelector('button').click();", submit_host)
        
        # --- 🛑 10 SECOND WAIT AFTER SUBMIT ---
        print("     - Waiting 10 seconds for processing...")
        time.sleep(10)
        # --------------------------------------
        
        print(f"  ✅ Listed successfully.")
        return True, "Listed"

    except Exception as e:
        err_msg = str(e).splitlines()[0][:50]
        print(f"  ❌ Failed: {err_msg}")
        return False, f"Error: {err_msg}"

# =======================================================================================
#               --- ▶️ MAIN EXECUTION ---
# =======================================================================================

if __name__ == "__main__":
    print(f"Starting Bulk Lister. Reading from: {os.path.basename(EXCEL_FILE_PATH)}")
    
    driver, chrome_process = setup_driver()
    if not driver: exit()
    wait = WebDriverWait(driver, 20) # Shorter wait time for bulk listing

    try:
        # Check Login
        driver.get("https://sellercentral.amazon.com/home")
        if "ap/signin" in driver.current_url:
            input("⚠️ Please log in to Amazon, then press Enter...")
        
        # Load Excel
        df = pd.read_excel(EXCEL_FILE_PATH)
        
        # Validate Columns
        req_cols = ['ASIN', 'SKU', 'Selling_Price']
        if not all(col in df.columns for col in req_cols):
            print(f"❌ Error: Excel is missing one of these columns: {req_cols}")
            exit()

        # Add Status column if missing
        if 'Status' not in df.columns: df['Status'] = ''

        print(f"Loaded {len(df)} rows.")

        # Loop
        for index, row in df.iterrows():
            # Skip if already done
            if pd.notna(row.get('Status')) and row.get('Status') != '':
                continue

            asin = str(row['ASIN']).strip()
            sku = str(row['SKU']).strip()
            price = row['Selling_Price']

            # Basic Validation
            if not asin or not sku or pd.isna(price):
                df.loc[index, 'Status'] = 'Missing Data'
                continue

            # Run Listing Function
            success, status_msg = list_product(driver, wait, asin, sku, price)
            
            # Update Excel
            df.loc[index, 'Status'] = status_msg
            df.to_excel(EXCEL_FILE_PATH, index=False)
            
            # Small pause to look natural
            time.sleep(2)

    except Exception as e:
        print(f"❌ Script crash: {e}")
    finally:
        print("Done.")
        if driver: driver.quit()
        if chrome_process: chrome_process.terminate()