import os
import json
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTOMATIONS_DIR = os.path.dirname(SCRIPT_DIR)
TRACKER_FILE = os.path.join(AUTOMATIONS_DIR, "data", "dataset", "price_tracker.json")
LOG_FILE = os.path.join(AUTOMATIONS_DIR, "data", "dataset", "price_log.txt")

def _load_tracker():
    if not os.path.exists(TRACKER_FILE):
        return {}
    try:
        with open(TRACKER_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def _save_tracker(data):
    with open(TRACKER_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def _append_log(message):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {message}\n"
    with open(LOG_FILE, 'a') as f:
        f.write(line)
    return line

def log_price_or_status(sku, current_price, status="IN_STOCK"):
    """
    Check if the price or status for a SKU has changed since last time.
    If it has, write to log file and update tracker.
    `current_price` should be a float.
    `status` should be e.g. "IN_STOCK", "OOS", "API_ERROR", etc.
    """
    tracker = _load_tracker()
    
    # Initialize if new
    if sku not in tracker:
        tracker[sku] = {
            "price": current_price,
            "status": status,
            "first_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        _save_tracker(tracker)
        # We don't necessarily want to log every new item, just changes.
        # _append_log(f"NEW: {sku} added at ${current_price:.2f} ({status})")
        return None
        
    old_data = tracker[sku]
    old_price = old_data.get("price", 0.0)
    old_status = old_data.get("status", "UNKNOWN")
    
    changed = False
    log_msgs = []
    
    if current_price != old_price and status == "IN_STOCK":
        # Only log price change if it's currently in stock
        log_msgs.append(f"PRICE CHANGE: {sku} from ${old_price:.2f} to ${current_price:.2f}")
        tracker[sku]["price"] = current_price
        changed = True
        
    if status != old_status:
        if status == "OOS":
            log_msgs.append(f"STATUS CHANGE: {sku} went OUT OF STOCK")
        elif status == "IN_STOCK":
            log_msgs.append(f"STATUS CHANGE: {sku} is back IN STOCK (Price: ${current_price:.2f})")
        else:
            log_msgs.append(f"STATUS CHANGE: {sku} -> {status}")
        tracker[sku]["status"] = status
        changed = True
        
    if changed:
        tracker[sku]["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save_tracker(tracker)
        full_msg = " | ".join(log_msgs)
        _append_log(full_msg)
        return full_msg
        
    return None
