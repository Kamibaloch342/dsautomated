import sys
import time
import argparse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

JS_AUTOMATION = """
let mode = arguments[0];

function findDeepElements(selector) {
    let results = [];
    function walk(root) {
        let els = root.querySelectorAll('*');
        for (let el of els) {
            if (el.matches && el.matches(selector)) {
                results.push(el);
            }
            if (el.shadowRoot) walk(el.shadowRoot);
        }
    }
    walk(document);
    return results;
}

if (mode === 'open_menu') {
    let dds = findDeepElements('kat-dropdown-button');
    if (dds.length > 0) {
        let trigger = dds[0].shadowRoot ? dds[0].shadowRoot.querySelector('button') : dds[0];
        (trigger || dds[0]).click();
        return 'Opened 3-dots menu';
    }
    return 'Menu trigger not found';
} 
else if (mode === 'click_delete') {
    let buttons = findDeepElements('button');
    for (let btn of buttons) {
        if (btn.innerText && btn.innerText.includes('Delete listing')) {
            btn.click();
            return 'Clicked Delete listing option';
        }
    }
    return 'Delete option not found';
}
else if (mode === 'click_confirm') {
    // Look for the kat-button with variant="primary"
    let allKatButtons = findDeepElements('kat-button');
    for (let btn of allKatButtons) {
        if (btn.getAttribute('variant') === 'primary' && btn.innerText && btn.innerText.includes('Delete listing')) {
            // Click the web component host directly
            btn.click();
            
            // Also try clicking its internal button just in case
            if (btn.shadowRoot) {
                let inner = btn.shadowRoot.querySelector('button');
                if (inner) inner.click();
            }
            return 'Clicked primary kat-button!';
        }
    }
    
    // Fallback: any button containing 'Delete listing' that is NOT the dropdown menu item
    let allBtns = findDeepElements('kat-button, button');
    for (let btn of allBtns) {
        if (btn.innerText && btn.innerText.includes('Delete listing') && !btn.closest('kat-dropdown-button, kat-dropdown-item, .options, [role="menu"]')) {
            btn.click();
            if (btn.shadowRoot) {
                let inner = btn.shadowRoot.querySelector('button');
                if (inner) inner.click();
            }
            return 'Clicked confirm modal (fallback)';
        }
    }
    
    return 'Confirm button not found in modal';
}
return 'Invalid mode';
"""

def delete_listing(asin, port):
    print(f"Starting automation for ASIN: {asin} on port {port}")
    
    options = Options()
    options.debugger_address = f"127.0.0.1:{port}"
    
    try:
        driver = webdriver.Chrome(options=options)
    except Exception as e:
        print(f"Error connecting to browser on port {port}.")
        return

    url = f"https://sellercentral.amazon.com/myinventory/inventory?searchTerm={asin}"
    print(f"Navigating to {url}")
    driver.get(url)
    
    print("Waiting 5 seconds for page to load...")
    time.sleep(5) 
    
    print("1. Opening the 3-dots action menu...")
    res = driver.execute_script(JS_AUTOMATION, 'open_menu')
    print(f"Result: {res}")
    if "not found" in res:
        print("Could not find the action menu. Aborting.")
        return
        
    time.sleep(2)
    
    print("2. Clicking 'Delete listing'...")
    res = driver.execute_script(JS_AUTOMATION, 'click_delete')
    print(f"Result: {res}")
    if "not found" in res:
        print("Could not find Delete option. Aborting.")
        return
        
    print("Waiting 3 seconds for confirmation modal to appear...")
    time.sleep(3)
    
    print("3. Confirming deletion in modal...")
    res = driver.execute_script(JS_AUTOMATION, 'click_confirm')
    print(f"Result: {res}")
    
    if "not found" not in res:
        print(f"✅ Successfully clicked the final delete confirmation for ASIN: {asin}")
        print("Note: Amazon may take up to 15 minutes to fully remove it from the page.")
    else:
        print("Failed to click confirm button.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Amazon ASIN Listing Deleter")
    parser.add_argument("asin", help="The ASIN to delete")
    parser.add_argument("--port", default=9333, type=int, help="Chrome debugging port")
    args = parser.parse_args()
    
    delete_listing(args.asin, args.port)
