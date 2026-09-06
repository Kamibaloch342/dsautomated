"""
sheet_sync.py — Background sync of local order_log.txt to Google Sheets Order_Log tab.

This module is designed to be non-blocking. Call sync_order_log_to_sheet()
in a background thread so order placement is never interrupted.
"""

import os
import re
import threading
from datetime import datetime

# ── paths ────────────────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_AUTOMATIONS_DIR = os.path.dirname(_SCRIPT_DIR)
_LOG_FILE = os.path.join(_AUTOMATIONS_DIR, "data", "rajar_logs", "order_log.txt")

# ── Google Sheets config (imported lazily to avoid circular deps) ───
_GOOGLE_SHEET_ID = None
_ORDER_LOG_TAB = None
_SA_FILE = None


def _resolve_config():
    """Lazy-load config constants from utils.config."""
    global _GOOGLE_SHEET_ID, _ORDER_LOG_TAB, _SA_FILE
    if _GOOGLE_SHEET_ID is None:
        from utils import config
        _GOOGLE_SHEET_ID = config.GOOGLE_SHEET_ID
        _ORDER_LOG_TAB = config.GOOGLE_SHEET_ORDER_LOG_TAB
        _SA_FILE = config.GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE


# ── Sheet header ─────────────────────────────────────────────────────
# 40 columns — grouped logically: IDs → Product → Financials → Ops → Address
_HEADER = [
    # Order IDs & Timing
    "Amazon Order Date", "Order Time", "Amazon Order ID", "Zoro WB Order ID",
    "Bot Profile", "Purchase Date", "Ship By", "Deliver By",
    # Product Info
    "Amazon ASIN", "Product Title", "Amazon Original SKU", "Amazon Qty",
    "Zoro SKU", "Zoro Multiple", "Zoro Multiplier", "Zoro Qty",
    # Amazon Financials
    "Amazon Earnings", "Amazon Shipping", "Amazon Fees",
    # Zoro Financials
    "Zoro Subtotal", "Zoro Discount", "Zoro Shipping", "Zoro Tax",
    "Zoro Total Cost",
    # Profitability
    "Net Profit", "Final Margin %", "Est Margin %",
    # Operations
    "Coupon Used", "Zoro Order Status", "Payment Method",
    "Tracking IDs", "Shipped Email Time", "Checkout Time (s)",
    # Address
    "Ship To Name", "Ship To Street", "Ship To City", "Ship To State",
    "Ship To ZIP", "Ship To Phone", "Bill To Name",
]

# (log_key, dtype) in the exact column order above
_COL_DEFS = [
    ("date", "str"), ("time", "str"), ("amazon_id", "str"), ("wb_id", "str"),
    ("profile", "str"), ("purchasedate", "str"), ("shipby", "str"), ("deliverby", "str"),
    ("asin", "str"), ("title", "str"), ("originalsku", "str"), ("aqty", "int"),
    ("sku", "str"), ("zmultiple", "int"), ("zmultiplier", "float"), ("zqty", "int"),
    ("earnings", "currency"), ("shippingamt", "currency"), ("amazonfees", "currency"),
    ("subtotal", "currency"), ("discount", "currency"), ("shipping", "currency"),
    ("tax", "currency"), ("cost", "currency"),
    ("profit", "currency"), ("finalmargin", "pct"), ("estmargin", "pct"),
    ("coupon", "str"), ("status", "str"), ("payment", "str"),
    ("trackingids", "str"), ("shippedemailtime", "str"), ("time_sec", "time"),
    ("shipname", "str"), ("shipstreet", "str"), ("shipcity", "str"),
    ("shipstate", "str"), ("shipzip", "str"), ("shipphone", "str"), ("billname", "str"),
]


def _clean_value(val, dtype):
    """Strip $ / % / N/A and return a native Python type for the Sheets API.

    With valueInputOption="RAW" the API stores ints/floats as actual numbers
    (not text), so SUM and other formulas work.
    """
    if val is None:
        return ""
    s = str(val).strip()
    if s in ("N/A", "NA", "None", ""):
        return ""

    if dtype == "str":
        return s

    if dtype == "int":
        try:
            return int(float(s))
        except ValueError:
            return s

    if dtype == "float":
        try:
            return float(s)
        except ValueError:
            return s

    if dtype == "currency":
        s = s.replace("$", "").replace(",", "")
        try:
            return float(s)
        except ValueError:
            return s

    if dtype == "pct":
        s = s.replace("%", "").replace(",", "")
        try:
            return float(s) / 100.0
        except ValueError:
            return s

    if dtype == "time":
        s = s.replace("s", "").replace(",", "")
        try:
            return float(s)
        except ValueError:
            return s

    return s


def _entry_to_row(entry):
    """Convert a parsed entry dict to a sheet row (mix of strings and numbers)."""
    return [_clean_value(entry.get(key, ""), dtype) for key, dtype in _COL_DEFS]


def _parse_log_entries(content):
    """Parse order_log.txt into a list of dicts (same logic as GUI log tab)."""
    entries = []
    one_liner_pattern = (
        r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \| WB:.+?"
        r"(?=\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \| WB:|\Z)"
    )
    for raw in re.findall(one_liner_pattern, content, re.DOTALL):
        raw = raw.strip()
        m = re.match(r"^\[([^\]]+)\]\s+\|\s+(.+)", raw)
        if not m:
            continue
        dt_str, rest = m.groups()
        parts = rest.split(" | ")
        entry = {}
        dt_parts = dt_str.strip().split()
        entry["date"] = dt_parts[0] if dt_parts else ""
        entry["time"] = dt_parts[1] if len(dt_parts) > 1 else ""
        for part in parts:
            part = part.strip()
            if ":" in part:
                key, val = part.split(":", 1)
                key = key.strip().lower()
                if key == "time":
                    key = "time_sec"
                entry[key] = val.strip()
        key_map = {"wb": "wb_id", "amazon": "amazon_id", "margin": "finalmargin"}
        entries.append({key_map.get(k, k): v for k, v in entry.items()})
    return entries


def _build_sheet_service():
    """Build a Google Sheets API service with write access."""
    from google.oauth2 import service_account as _sa
    from googleapiclient.discovery import build as _build

    creds = _sa.Credentials.from_service_account_file(
        _SA_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return _build("sheets", "v4", credentials=creds, cache_discovery=False)


def _get_existing_keys(svc):
    """Read the sheet and return a set of WB Order IDs already present."""
    try:
        result = svc.spreadsheets().values().get(
            spreadsheetId=_GOOGLE_SHEET_ID,
            range=f"'{_ORDER_LOG_TAB}'!D:D",
            valueRenderOption="UNFORMATTED_VALUE",
        ).execute()
        rows = result.get("values", [])
        existing = set()
        for row in rows[1:]:  # skip header
            if row:
                existing.add(str(row[0]).strip())
        return existing
    except Exception as e:
        # Tab may not exist yet or be empty
        print(f"[SheetSync] Could not read existing keys: {e}")
        return set()


def _ensure_header(svc):
    """Write header row if the sheet appears empty."""
    try:
        result = svc.spreadsheets().values().get(
            spreadsheetId=_GOOGLE_SHEET_ID,
            range=f"'{_ORDER_LOG_TAB}'!A1:A1",
            valueRenderOption="UNFORMATTED_VALUE",
        ).execute()
        values = result.get("values", [])
        if not values or not values[0] or values[0][0] != _HEADER[0]:
            svc.spreadsheets().values().update(
                spreadsheetId=_GOOGLE_SHEET_ID,
                range=f"'{_ORDER_LOG_TAB}'!A1",
                valueInputOption="RAW",
                body={"values": [_HEADER]},
            ).execute()
            print(f"[SheetSync] Wrote header row to '{_ORDER_LOG_TAB}'")
    except Exception as e:
        print(f"[SheetSync] Could not ensure header: {e}")


def sync_order_log_to_sheet():
    """Sync local order_log.txt to the Google Sheets Order_Log tab.

    This is safe to call from a background thread — it re-reads the file
    each time and appends new rows OR updates existing rows when data
    changes (e.g., tracking IDs added later).
    """
    _resolve_config()

    if not os.path.exists(_LOG_FILE):
        print("[SheetSync] No local log file to sync.")
        return

    try:
        with open(_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        print(f"[SheetSync] Failed to read log file: {e}")
        return

    entries = _parse_log_entries(content)
    if not entries:
        print("[SheetSync] No entries found in log file.")
        return

    # Push only the trim window. trim_sheet_to_days(5) keeps the sheet to the
    # last 5 days; uploading the WHOLE log made every sync re-append the tens
    # of thousands of rows trim had just removed (the append/trim war that
    # ballooned the sheet toward the 10M-cell cap on 2026-08-14). Same cutoff
    # formula as trim so the two can never fight.
    from datetime import timedelta
    _cutoff = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    entries = [e for e in entries if str(e.get("date", "")).strip() >= _cutoff]
    if not entries:
        return

    try:
        svc = _build_sheet_service()
    except Exception as e:
        print(f"[SheetSync] Failed to build sheet service: {e}")
        return

    _ensure_header(svc)

    # Read existing rows to build wb_id → row_index map
    try:
        result = svc.spreadsheets().values().get(
            spreadsheetId=_GOOGLE_SHEET_ID,
            range=f"'{_ORDER_LOG_TAB}'!A1:AN",
            valueRenderOption="UNFORMATTED_VALUE",
        ).execute()
        sheet_rows = result.get("values", [])
    except Exception as e:
        print(f"[SheetSync] Could not read sheet: {e}")
        return

    # Build map: wb_id (column D = index 3) → sheet row index (1-based)
    wb_id_to_row_idx = {}
    for idx, row in enumerate(sheet_rows[1:], start=2):  # skip header, 1-based
        if len(row) > 3 and row[3]:
            wb_id_to_row_idx[str(row[3]).strip()] = idx

    new_rows = []
    updates = []
    for e in entries:
        wb_id = e.get("wb_id", "")
        if not wb_id:
            continue
        row_data = _entry_to_row(e)
        if wb_id not in wb_id_to_row_idx:
            new_rows.append(row_data)
        else:
            # Compare with existing row — update if changed
            sheet_row_idx = wb_id_to_row_idx[wb_id]
            existing_row = sheet_rows[sheet_row_idx - 1] if sheet_row_idx <= len(sheet_rows) else []
            # Pad existing row to match length
            while len(existing_row) < len(row_data):
                existing_row.append("")
            if list(existing_row) != list(row_data):
                updates.append((sheet_row_idx, row_data))

    # Flood brake: a healthy sync appends a handful of rows. Thousands means
    # the dedup/window logic is broken — refuse rather than balloon the sheet.
    if len(new_rows) > 2000:
        print(f"[SheetSync] 🚨 FLOOD BRAKE: refusing to append {len(new_rows)} rows "
              f"in one sync — investigate sheet_sync window/dedup.")
        new_rows = []

    # Append new rows
    if new_rows:
        try:
            svc.spreadsheets().values().append(
                spreadsheetId=_GOOGLE_SHEET_ID,
                range=f"'{_ORDER_LOG_TAB}'!A1",
                valueInputOption="RAW",
                insertDataOption="OVERWRITE",
                body={"values": new_rows},
            ).execute()
            print(f"[SheetSync] Appended {len(new_rows)} row(s) to '{_ORDER_LOG_TAB}'")
        except Exception as e:
            print(f"[SheetSync] Failed to append rows: {e}")

    # Batch-update changed rows (single API call avoids 60 writes/min quota)
    if updates:
        batch_data = []
        for row_idx, row_data in updates:
            batch_data.append({
                "range": f"'{_ORDER_LOG_TAB}'!A{row_idx}",
                "values": [row_data],
            })
        try:
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=_GOOGLE_SHEET_ID,
                body={
                    "valueInputOption": "RAW",
                    "data": batch_data,
                },
            ).execute()
            wb_ids = [row_data[3] for _, row_data in updates]
            if len(wb_ids) <= 5:
                ids_str = ", ".join(wb_ids)
            else:
                ids_str = ", ".join(wb_ids[:5]) + f" ...and {len(wb_ids) - 5} more"
            print(f"[SheetSync] Batch-updated {len(updates)} row(s): {ids_str}")
        except Exception as e:
            print(f"[SheetSync] Failed to batch-update rows: {e}")


def sync_order_log_async():
    """Fire-and-forget background sync (does not block caller)."""
    t = threading.Thread(target=sync_order_log_to_sheet, daemon=True)
    t.start()


# ══════════════════════════════════════════════════════════════════════════════
#  TWO-WAY SYNC — the sheet is the transport between machines:
#    upload   : local order_log.txt → sheet   (already above; also fired per-placement)
#    download : sheet rows placed on ANOTHER machine → local order_log.txt
#               (they then flow into order_placer_orders via sync_order_placer_logs,
#               show on the dashboard, AND get a CONFIRMED placer_claims row so the
#               duplicate lock knows about them — no cross-machine re-buy)
#    trim     : sheet keeps only the last N days (default 5) so it never grows huge
# ══════════════════════════════════════════════════════════════════════════════

# Reverse map: entry key → the exact field label used in order_log.txt lines,
# in the placer's canonical field order (so reconstructed lines parse identically).
_LINE_ORDER = [
    ("wb_id", "WB"), ("profile", "Profile"), ("amazon_id", "Amazon"), ("asin", "ASIN"),
    ("title", "Title"), ("aqty", "AQty"), ("sku", "SKU"), ("zmultiple", "ZMultiple"),
    ("zmultiplier", "ZMultiplier"), ("zqty", "ZQty"), ("earnings", "Earnings"),
    ("cost", "Cost"), ("profit", "Profit"), ("finalmargin", "FinalMargin"),
    ("estmargin", "EstMargin"), ("coupon", "Coupon"), ("status", "Status"),
    ("subtotal", "Subtotal"), ("discount", "Discount"), ("shipping", "Shipping"),
    ("tax", "Tax"), ("payment", "Payment"), ("shipname", "ShipName"),
    ("shipstreet", "ShipStreet"), ("shipcity", "ShipCity"), ("shipstate", "ShipState"),
    ("shipzip", "ShipZIP"), ("shipphone", "ShipPhone"), ("billname", "BillName"),
    ("time_sec", "Time"), ("originalsku", "OriginalSKU"), ("shippingamt", "ShippingAmt"),
    ("amazonfees", "AmazonFees"), ("purchasedate", "PurchaseDate"), ("shipby", "ShipBy"),
    ("deliverby", "DeliverBy"), ("trackingids", "TrackingIDs"),
    ("shippedemailtime", "ShippedEmailTime"),
]
_DTYPE = dict(_COL_DEFS)


def _row_to_entry(row):
    """Sheet row (list) → entry dict keyed like the log parser's output."""
    row = list(row) + [""] * (len(_COL_DEFS) - len(row))
    return {key: row[i] for i, (key, _d) in enumerate(_COL_DEFS)}


def _fmt_field(key, val):
    """Format a sheet value back into the log line's text form ($x.xx / x.x% / x.xs)."""
    if val is None or val == "":
        return ""
    d = _DTYPE.get(key, "str")
    try:
        if d == "currency":
            return f"${float(val):.2f}"
        if d == "pct":
            return f"{float(val) * 100:.1f}%"
        if d == "time":
            return f"{float(val):.1f}s"
        if d == "int":
            return str(int(float(val)))
        if d == "float":
            return str(float(val))
    except (ValueError, TypeError):
        pass
    # str fields: the log format is ' | '-delimited, so a literal ' | ' inside a value
    # (some product titles have it) would corrupt the reconstructed line — soften it.
    return str(val).replace(" | ", " - ").replace("\n", " ").strip()


def _entry_to_line(e):
    """Rebuild the canonical order_log.txt one-liner from a sheet entry."""
    ts = f"{str(e.get('date', '')).strip()} {str(e.get('time', '')).strip()}".strip()
    parts = [f"[{ts}]"]
    for key, label in _LINE_ORDER:
        parts.append(f"{label}:{_fmt_field(key, e.get(key, ''))}")
    return " | ".join(parts)


def pull_sheet_to_local():
    """Append placements found in the sheet but NOT in the local log (placed on another
    machine) to local order_log.txt, and register them with the duplicate lock. Returns
    the number of foreign placements pulled."""
    _resolve_config()
    try:
        svc = _build_sheet_service()
        result = svc.spreadsheets().values().get(
            spreadsheetId=_GOOGLE_SHEET_ID,
            range=f"'{_ORDER_LOG_TAB}'!A1:AN",
            valueRenderOption="UNFORMATTED_VALUE",
        ).execute()
        sheet_rows = result.get("values", [])[1:]  # skip header
    except Exception as e:
        print(f"[SheetSync] pull: could not read sheet: {e}")
        return 0

    content = ""
    if os.path.exists(_LOG_FILE):
        with open(_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    local_wbs = set(re.findall(r"WB:(\S+)", content))

    pulled = []
    for row in sheet_rows:
        e = _row_to_entry(row)
        wb = str(e.get("wb_id", "")).strip()
        amazon = str(e.get("amazon_id", "")).strip()
        if not wb or not amazon or wb in local_wbs:
            continue
        pulled.append((_entry_to_line(e), amazon, wb))
        local_wbs.add(wb)

    if not pulled:
        return 0
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        for line, _a, _w in pulled:
            f.write(line + "\n")
    # Register with the duplicate lock so THIS machine can never re-buy an order the
    # other machine already placed.
    try:
        from utils import placement_lock as plock
        for _l, amazon, wb in pulled:
            plock.confirm(amazon, wb, note="placed on another machine (synced via sheet)")
    except Exception as e:
        print(f"[SheetSync] pull: placement_lock registration failed: {e}")
    print(f"[SheetSync] pulled {len(pulled)} placement(s) from the sheet into the local log")
    return len(pulled)


def trim_sheet_to_days(days=5):
    """Keep only the last `days` of rows in the sheet (col A = YYYY-MM-DD order date),
    so it stays small. The full history always lives in each machine's order_log.txt.
    Returns the number of rows removed."""
    from datetime import timedelta
    _resolve_config()
    try:
        svc = _build_sheet_service()
        result = svc.spreadsheets().values().get(
            spreadsheetId=_GOOGLE_SHEET_ID,
            range=f"'{_ORDER_LOG_TAB}'!A1:AN",
            valueRenderOption="UNFORMATTED_VALUE",
        ).execute()
        rows = result.get("values", [])
        if len(rows) < 2:
            return 0
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        keep = [r for r in rows[1:] if r and str(r[0]).strip() >= cutoff]
        removed = len(rows) - 1 - len(keep)
        if removed <= 0:
            return 0
        svc.spreadsheets().values().clear(
            spreadsheetId=_GOOGLE_SHEET_ID, range=f"'{_ORDER_LOG_TAB}'!A:AN").execute()
        svc.spreadsheets().values().update(
            spreadsheetId=_GOOGLE_SHEET_ID,
            range=f"'{_ORDER_LOG_TAB}'!A1",
            valueInputOption="RAW",
            body={"values": [_HEADER] + keep},
        ).execute()
        print(f"[SheetSync] trimmed {removed} row(s) older than {days} days "
              f"({len(keep)} kept)")
        return removed
    except Exception as e:
        print(f"[SheetSync] trim failed: {e}")
        return 0


def sync_bidirectional(days=5):
    """upload → download → trim. Each step isolated; safe to run every few minutes."""
    try:
        sync_order_log_to_sheet()
    except Exception as e:
        print(f"[SheetSync] upload failed: {e}")
    pulled = 0
    try:
        pulled = pull_sheet_to_local()
    except Exception as e:
        print(f"[SheetSync] pull failed: {e}")
    try:
        trim_sheet_to_days(days)
    except Exception as e:
        print(f"[SheetSync] trim failed: {e}")
    return pulled


def setup_order_log_formatting():
    """One-time setup: clear sheet, rewrite header, rebuild from log,
    apply number formats + conditional formatting.

    Run this manually whenever you want to refresh the Order_Log tab layout
    or re-apply formatting rules.
    """
    _resolve_config()
    try:
        svc = _build_sheet_service()
    except Exception as e:
        print(f"[SheetSync] Failed to build sheet service: {e}")
        return

    sheet_id = _get_sheet_id(svc, _ORDER_LOG_TAB)

    # ── 1. Clear all data rows (keep header) ──
    try:
        svc.spreadsheets().values().clear(
            spreadsheetId=_GOOGLE_SHEET_ID,
            range=f"'{_ORDER_LOG_TAB}'!A2:AN10000",
        ).execute()
        print(f"[SheetSync] Cleared data rows in '{_ORDER_LOG_TAB}'")
    except Exception as e:
        print(f"[SheetSync] Could not clear rows: {e}")

    # ── 2. Rewrite header row ──
    try:
        svc.spreadsheets().values().update(
            spreadsheetId=_GOOGLE_SHEET_ID,
            range=f"'{_ORDER_LOG_TAB}'!A1",
            valueInputOption="RAW",
            body={"values": [_HEADER]},
        ).execute()
        print(f"[SheetSync] Header rewritten in '{_ORDER_LOG_TAB}'")
    except Exception as e:
        print(f"[SheetSync] Failed to rewrite header: {e}")
        return

    # ── 3. Re-populate from local log ──
    sync_order_log_to_sheet()

    # ── 4. Apply number formats ──
    # Column indices (0-based) from _COL_DEFS:
    #   Qty cols      : 11 (L), 13 (N), 15 (P)        → NUMBER
    #   Currency cols : 16-24 (Q-X)                    → CURRENCY
    #   Margin cols   : 25-26 (Z-AA)                   → PERCENT  0.0%
    #   Time col      : 32 (AG)                        → NUMBER 0.0"s"
    fmt_requests = []

    def _fmt_range(start_col, end_col, type_, pattern=None):
        nf = {"type": type_}
        if pattern:
            nf["pattern"] = pattern
        fmt_requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": start_col,
                    "endColumnIndex": end_col,
                },
                "cell": {"userEnteredFormat": {"numberFormat": nf}},
                "fields": "userEnteredFormat.numberFormat",
            }
        })

    _fmt_range(11, 12, "NUMBER")            # Amazon Qty
    _fmt_range(13, 14, "NUMBER")            # Zoro Multiple
    _fmt_range(15, 16, "NUMBER")            # Zoro Qty
    _fmt_range(16, 25, "CURRENCY")          # All $ columns (Q through X)
    _fmt_range(25, 27, "PERCENT", "0.0%")   # Margins
    _fmt_range(32, 33, "NUMBER", '0.0"s"')  # Checkout Time

    try:
        svc.spreadsheets().batchUpdate(
            spreadsheetId=_GOOGLE_SHEET_ID,
            body={"requests": fmt_requests},
        ).execute()
        print("[SheetSync] Number formats applied:")
        print("   • Currency columns → $")
        print("   • Margin columns   → %")
        print("   • Time column      → s")
    except Exception as e:
        print(f"[SheetSync] Failed to apply number formats: {e}")

    # ── 5. Apply conditional formatting ──
    requests = []

    # Delete all existing conditional-formatting rules first
    sheet_meta = svc.spreadsheets().get(spreadsheetId=_GOOGLE_SHEET_ID).execute()
    for sheet in sheet_meta.get("sheets", []):
        if sheet["properties"]["sheetId"] == sheet_id:
            existing_rules = sheet.get("conditionalFormats", [])
            for _ in existing_rules:
                requests.append({"deleteConditionalFormatRule": {
                    "sheetId": sheet_id, "index": 0}})
            break

    # ── Margin rules: Final Margin % (Z = index 25) ──
    requests.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": 25,
                    "endColumnIndex": 26,
                }],
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": '=AND(Z2<>"",Z2<0.05)'}],
                    },
                    "format": {
                        "backgroundColor": {"red": 0.92, "green": 0.28, "blue": 0.28},
                        "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                    },
                },
            },
            "index": 0,
        }
    })
    requests.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": 25,
                    "endColumnIndex": 26,
                }],
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": '=AND(Z2<>"",Z2>=0.05,Z2<0.10)'}],
                    },
                    "format": {
                        "backgroundColor": {"red": 1.0, "green": 0.85, "blue": 0.0},
                        "textFormat": {"foregroundColor": {"red": 0, "green": 0, "blue": 0}},
                    },
                },
            },
            "index": 1,
        }
    })
    requests.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": 25,
                    "endColumnIndex": 26,
                }],
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": '=AND(Z2<>"",Z2>=0.10)'}],
                    },
                    "format": {
                        "backgroundColor": {"red": 0.20, "green": 0.80, "blue": 0.20},
                        "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                    },
                },
            },
            "index": 2,
        }
    })

    # ── Margin rules: Est Margin % (AA = index 26) ──
    requests.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": 26,
                    "endColumnIndex": 27,
                }],
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": '=AND(AA2<>"",AA2<0.05)'}],
                    },
                    "format": {
                        "backgroundColor": {"red": 0.92, "green": 0.28, "blue": 0.28},
                        "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                    },
                },
            },
            "index": 3,
        }
    })
    requests.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": 26,
                    "endColumnIndex": 27,
                }],
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": '=AND(AA2<>"",AA2>=0.05,AA2<0.10)'}],
                    },
                    "format": {
                        "backgroundColor": {"red": 1.0, "green": 0.85, "blue": 0.0},
                        "textFormat": {"foregroundColor": {"red": 0, "green": 0, "blue": 0}},
                    },
                },
            },
            "index": 4,
        }
    })
    requests.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": 26,
                    "endColumnIndex": 27,
                }],
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": '=AND(AA2<>"",AA2>=0.10)'}],
                    },
                    "format": {
                        "backgroundColor": {"red": 0.20, "green": 0.80, "blue": 0.20},
                        "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                    },
                },
            },
            "index": 5,
        }
    })

    # Rule 7: Title contains "HOANK" AND Zoro Shipping = $0 → highlight Shipping cell
    # Title = J = index 9, Zoro Shipping = V = index 21
    requests.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": 21,
                    "endColumnIndex": 22,
                }],
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": '=AND(REGEXMATCH($J2,"HOANK"),$V2=0)'}],
                    },
                    "format": {
                        "backgroundColor": {"red": 1.0, "green": 0.85, "blue": 0.0},
                        "textFormat": {"foregroundColor": {"red": 0, "green": 0, "blue": 0}},
                    },
                },
            },
            "index": 6,
        }
    })

    # Rule 8: Zoro Tax > 0 → red highlight
    # Zoro Tax = W = index 22
    requests.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": 22,
                    "endColumnIndex": 23,
                }],
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": '=AND(W2<>"",W2>0)'}],
                    },
                    "format": {
                        "backgroundColor": {"red": 0.92, "green": 0.28, "blue": 0.28},
                        "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                    },
                },
            },
            "index": 7,
        }
    })

    try:
        svc.spreadsheets().batchUpdate(
            spreadsheetId=_GOOGLE_SHEET_ID,
            body={"requests": requests},
        ).execute()
        print("[SheetSync] Conditional formatting applied:")
        print("   • Margin < 5%  → red")
        print("   • Margin 5-10% → yellow")
        print("   • Margin ≥ 10% → green")
        print("   • Title contains 'HOANK' + Zoro Shipping = $0 → yellow highlight")
        print("   • Zoro Tax > 0 → red highlight")
    except Exception as e:
        print(f"[SheetSync] Failed to apply formatting: {e}")


def _get_sheet_id(svc, tab_name):
    """Resolve a tab name to its numeric sheetId."""
    sheet_metadata = svc.spreadsheets().get(spreadsheetId=_GOOGLE_SHEET_ID).execute()
    for sheet in sheet_metadata.get("sheets", []):
        if sheet["properties"]["title"] == tab_name:
            return sheet["properties"]["sheetId"]
    raise ValueError(f"Tab '{tab_name}' not found in spreadsheet")
