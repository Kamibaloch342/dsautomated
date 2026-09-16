#!/usr/bin/env python3
"""
download_all_listings_report.py — Automates downloading the "All Listings Report" from Amazon Seller Central.

Workflow:
1. Reuses an authenticated Chrome debugging session (default: 9222, or 9221/9223/9224).
2. Opens a new tab to: https://sellercentral.amazon.com/listing/reports/ref=xx_invreport_dnav_xx
3. Selects "All Listings Report" (value 47700) from the kat-dropdown.
4. Clicks "Request Report" (kat-button[data-testid="request-report-testid"]).
5. Polls the submission status table until the status is "Done" (kat-badge[label="Done"]).
6. Clicks "Download" and saves the TSV to Automations/data/reports/all_listings_report.tsv.
7. Closes the temporary tab cleanly.
"""

import os
import sys
import time
import glob
import shutil
import argparse
import functools
from typing import Optional

# Ensure unbuffered stdout
print = functools.partial(print, flush=True)

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.options import Options

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_REPORT_DIR = os.path.join(BASE_DIR, "data", "reports")
DEFAULT_OUTPUT_TSV = os.path.join(DEFAULT_REPORT_DIR, "all_listings_report.tsv")
REPORTS_URL = "https://sellercentral.amazon.com/listing/reports/ref=xx_invreport_dnav_xx"


def find_active_sc_port(preferred_ports=(9221, 9222, 9223, 9224)) -> Optional[int]:
    """Find a Chrome port that is open and has an active Seller Central session."""
    import socket
    import urllib.request
    import json

    for port in preferred_ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        is_open = s.connect_ex(('127.0.0.1', port)) == 0
        s.close()
        if not is_open:
            continue
        try:
            req = urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1.0)
            tabs = json.loads(req.read().decode("utf-8"))
            for t in tabs:
                url = t.get("url", "")
                if "sellercentral.amazon.com" in url or "amazon.com" in url:
                    return port
        except Exception:
            pass

    # Fallback to first open port
    for port in preferred_ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        is_open = s.connect_ex(('127.0.0.1', port)) == 0
        s.close()
        if is_open:
            return port
    return None


def download_all_listings_report(
    port: Optional[int] = None,
    output_tsv: str = DEFAULT_OUTPUT_TSV,
    timeout_seconds: int = 180,
) -> Optional[str]:
    """Download the All Listings Report from Amazon Seller Central."""
    if port is None:
        port = find_active_sc_port()
        if not port:
            print("❌ [Reports] No active Chrome session found on ports 9221-9224.")
            return None

    print(f"[*] [Reports] Connecting to Chrome on port {port}...")
    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")

    try:
        driver = webdriver.Chrome(options=opts)
    except Exception as e:
        print(f"❌ [Reports] Failed to attach to Chrome on port {port}: {e}")
        return None

    os.makedirs(DEFAULT_REPORT_DIR, exist_ok=True)
    temp_download_dir = os.path.join(DEFAULT_REPORT_DIR, "temp_downloads")
    os.makedirs(temp_download_dir, exist_ok=True)

    # Clear any old txt/tsv in temp_download_dir
    for old_f in glob.glob(os.path.join(temp_download_dir, "*")):
        try:
            os.remove(old_f)
        except OSError:
            pass

    # Configure Chrome CDP download directory
    try:
        driver.execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": temp_download_dir}
        )
    except Exception as e:
        print(f"  [Reports] ⚠️ CDP setDownloadBehavior warning: {e}")

    # Open a new tab for reports
    orig_handles = list(driver.window_handles)
    driver.execute_script("window.open('about:blank', '_blank');")
    time.sleep(0.5)
    new_handles = [h for h in driver.window_handles if h not in orig_handles]
    reports_tab = new_handles[0] if new_handles else driver.window_handles[-1]
    driver.switch_to.window(reports_tab)

    try:
        print(f"[*] [Reports] Navigating to {REPORTS_URL}...")
        driver.get(REPORTS_URL)
        time.sleep(4)

        if "signin" in driver.current_url or "ap/signin" in driver.current_url:
            print("❌ [Reports] Session expired — Seller Central sign-in required.")
            return None

        # 1. Select "All Listings Report" (value: 47700) from kat-dropdown
        print("[*] [Reports] Selecting 'All Listings Report' from dropdown...")
        select_js = """
        var dd = document.querySelector('kat-dropdown');
        if (!dd) return {ok: false, reason: 'kat-dropdown not found'};
        
        // Find option 47700 in shadowRoot or options
        var opt = null;
        if (dd.shadowRoot) {
            opt = Array.from(dd.shadowRoot.querySelectorAll('kat-option')).find(
                o => o.getAttribute('value') === '47700' || (o.innerText || '').includes('All Listings Report')
            );
        }
        if (opt) {
            opt.click();
        }
        dd.value = '47700';
        dd.dispatchEvent(new CustomEvent('change', {detail: {value: '47700'}, bubbles: true, composed: true}));
        return {ok: true};
        """
        sel_res = driver.execute_script(select_js)
        if not sel_res.get("ok"):
            print(f"❌ [Reports] Could not select report type: {sel_res.get('reason')}")
            return None

        time.sleep(2.5)

        # 2. Click "Request Report" button
        print("[*] [Reports] Clicking 'Request Report' button...")
        click_req_js = """
        var btn = document.querySelector('kat-button[data-testid=\"request-report-testid\"]') ||
                  document.querySelector('.request-report-button kat-button');
        if (!btn) {
            var allBtns = Array.from(document.querySelectorAll('kat-button, button'));
            btn = allBtns.find(b => (b.innerText || b.getAttribute('label') || '').includes('Request Report'));
        }
        if (!btn) return {ok: false, reason: 'Request Report button not found'};
        if (btn.shadowRoot && btn.shadowRoot.querySelector('button')) {
            btn.shadowRoot.querySelector('button').click();
        } else {
            btn.click();
        }
        return {ok: true};
        """
        req_res = driver.execute_script(click_req_js)
        if req_res.get("ok"):
            print("  ✅ [Reports] 'Request Report' submitted successfully.")
        else:
            print(f"  ⚠️ [Reports] Note on request button: {req_res.get('reason')} (may already have pending report)")

        # 3. Poll for "Done" status on the newest All Listings Report row
        print(f"[*] [Reports] Polling for report completion (timeout: {timeout_seconds}s)...")
        start_time = time.time()
        ready_row_index = None

        while time.time() - start_time < timeout_seconds:
            poll_js = """
            var trs = Array.from(document.querySelectorAll('tbody tr'));
            for (var i = 0; i < trs.length; i++) {
                var text = trs[i].innerText || '';
                if (text.includes('All Listings Report')) {
                    var isDone = !!trs[i].querySelector('kat-badge[label=\"Done\"]') ||
                                 text.includes('Done') ||
                                 !!trs[i].querySelector('kat-button.actions-button');
                    var isProgress = text.includes('In progress') || !!trs[i].querySelector('kat-badge[label*=\"progress\" i]');
                    return {found: true, index: i, done: isDone, inProgress: isProgress, rowText: text.slice(0, 100)};
                }
            }
            return {found: false};
            """
            status_info = driver.execute_script(poll_js)
            if status_info.get("found"):
                if status_info.get("done") and not status_info.get("inProgress"):
                    ready_row_index = status_info.get("index", 0)
                    print(f"  ✅ [Reports] Report is READY (Row {ready_row_index + 1})!")
                    break
                else:
                    print(f"  ⏳ [Reports] Status: In Progress... ({int(time.time() - start_time)}s elapsed)")
            else:
                print(f"  ⏳ [Reports] Waiting for report row to appear... ({int(time.time() - start_time)}s)")

            time.sleep(6)
            # Refresh status table or page if needed
            try:
                driver.execute_script("""
                var refreshBtn = document.querySelector('kat-button[label*=\"Refresh\" i]');
                if (refreshBtn) refreshBtn.click();
                """)
            except Exception:
                pass

        if ready_row_index is None:
            print("❌ [Reports] Timed out waiting for All Listings Report to finish.")
            return None

        # 4. Click Download on the ready row
        print(f"[*] [Reports] Clicking Download on row {ready_row_index + 1}...")
        download_clicked = False
        try:
            # Re-ensure CDP download path
            driver.execute_cdp_cmd(
                "Page.setDownloadBehavior",
                {"behavior": "allow", "downloadPath": temp_download_dir}
            )

            row_elem = driver.find_elements(By.CSS_SELECTOR, "tbody tr")[ready_row_index]
            dl_btn = row_elem.find_element(By.CSS_SELECTOR, "kat-button.actions-button")
            ActionChains(driver).move_to_element(dl_btn).click().perform()
            download_clicked = True
            print("  ✅ [Reports] Download clicked via ActionChains.")
        except Exception as ce:
            print(f"  ⚠️ ActionChains click failed: {ce}, attempting JS click...")
            js_click = f"""
            var tr = document.querySelectorAll('tbody tr')[{ready_row_index}];
            var btn = tr.querySelector('kat-button.actions-button');
            if (btn && btn.shadowRoot && btn.shadowRoot.querySelector('button')) {{
                btn.shadowRoot.querySelector('button').click();
                return true;
            }}
            if (btn) {{ btn.click(); return true; }}
            return false;
            """
            download_clicked = driver.execute_script(js_click)

        if not download_clicked:
            print("❌ [Reports] Could not trigger download button.")
            return None

        # 5. Wait for downloaded file to appear
        print("[*] [Reports] Waiting for file download to complete...")
        dl_start = time.time()
        downloaded_file = None
        user_dl = os.path.expanduser("~/Downloads")

        while time.time() - dl_start < 45:
            files = glob.glob(os.path.join(temp_download_dir, "*")) + glob.glob(os.path.join(user_dl, "All*Listings*Report*"))
            valid_files = [
                f for f in files
                if not f.endswith(".crdownload") and not f.endswith(".tmp") and os.path.getmtime(f) >= (dl_start - 15)
            ]
            if valid_files:
                valid_files.sort(key=os.path.getmtime, reverse=True)
                downloaded_file = valid_files[0]
                if os.path.getsize(downloaded_file) > 1000:
                    print(f"  ✅ [Reports] Detected report: {downloaded_file}")
                    break
            time.sleep(1)

        if not downloaded_file or not os.path.exists(downloaded_file):
            print("❌ [Reports] Downloaded file not detected on disk.")
            return None

        # 6. Copy/rename to destination TSV
        os.makedirs(os.path.dirname(output_tsv), exist_ok=True)
        shutil.copy2(downloaded_file, output_tsv)
        file_size = os.path.getsize(output_tsv)
        print(f"🎉 [Reports] All Listings Report saved successfully!")
        print(f"   • Path: {output_tsv}")
        print(f"   • Size: {file_size:,} bytes")
        return output_tsv

    finally:
        # Close the reports tab cleanly
        try:
            driver.close()
            # Switch back to remaining window
            if driver.window_handles:
                driver.switch_to.window(driver.window_handles[0])
        except Exception:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download Amazon All Listings Report via Seller Central")
    parser.add_argument("--port", type=int, default=None, help="Chrome debugging port (default: auto-detect)")
    parser.add_argument("--output", type=str, default=DEFAULT_OUTPUT_TSV, help="Output TSV path")
    parser.add_argument("--timeout", type=int, default=180, help="Polling timeout in seconds")
    args = parser.parse_args()

    res = download_all_listings_report(port=args.port, output_tsv=args.output, timeout_seconds=args.timeout)
    if res:
        print(f"SUCCESS: {res}")
        sys.exit(0)
    else:
        print("FAILED to download report")
        sys.exit(1)
