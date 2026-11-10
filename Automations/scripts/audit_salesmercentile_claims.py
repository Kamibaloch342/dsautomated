#!/usr/bin/env python3
import json
import re
import sqlite3
from collections import defaultdict, Counter
from datetime import datetime

SCRATCH_DIR = "/Users/kamibaloch342/.gemini/antigravity/brain/7176937a-223c-4b69-9125-54858c463ac0/scratch"
UNIFIED_DB = "file:Automations/data/automations_unified.db?mode=ro"
ARCHIVE_DB = "file:Automations/data/gmail_archive.db?mode=ro&immutable=1"

STALLED_CUTOFF_DATE = "2026-11-03"

BILL_ZIPS = {"32304", "99338", "33702"}

def norm_str(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def norm_zip(z):
    m = re.search(r"(\d{5})", str(z or ""))
    return m.group(1) if m else ""

def is_terminal_exception_or_damaged(text):
    t = (text or "").lower()
    keywords = [
        "damage",
        "returning to sender",
        "return to sender",
        "returning to shipper",
        "returned to shipper",
        "returned to sender",
        "undeliverable",
        "refused",
        "unable to deliver",
        "lost",
        "missing",
        "destroyed",
        "disposed",
        "improperly packaged",
    ]
    return any(k in t for k in keywords)

def run():
    with open(f"{SCRATCH_DIR}/salesmercentile_cash_refunds.json") as f:
        raw_crs = json.load(f)

    print(f"Loaded {len(raw_crs)} raw cash refunds.")

    # Parse Cash Refunds
    parsed_crs = []
    for cr in raw_crs:
        raw = cr.get("raw", "")
        m_cr = re.search(r"(?:Cash Refund|Refund #)\s*#?\s*(\d+)", raw) or re.search(r"#(\d+)", cr.get("subject", ""))
        cr_no = m_cr.group(1) if m_cr else cr.get("cash_refund_no", "")
        
        m_dt = re.search(r"(?:Date|Cash Refund Date)\s*#?\s*(\d{1,2}/\d{1,2}/\d{4})", raw)
        dt_str = m_dt.group(1) if m_dt else cr.get("date", "")
        
        m_amt = re.search(r"Total\s*\$?([\d,]+\.\d{2})", raw)
        amount = float(m_amt.group(1).replace(",", "")) if m_amt else cr.get("amount", 0.0)
        
        m_so = re.search(r"\b(SO\d{7,10})\b", raw)
        so = m_so.group(1) if m_so else cr.get("so", "")

        # Zip extraction
        zips = re.findall(r"\b([A-Z]{2})\s*(\d{5})(?:\d{4})?\b", raw)
        valid_zips = [z[1] for z in zips if z[1] not in BILL_ZIPS]
        zip5 = valid_zips[0] if valid_zips else ""

        # Name extraction
        name = ""
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        for i, ln in enumerate(lines):
            if "Bill To Ship To" in ln and i + 1 < len(lines):
                name_line = lines[i+1]
                for p in ["Mark Booth ", "INUHR ECOM ", "INUHR LLC ", "Sales Mercentile "]:
                    if name_line.startswith(p):
                        name = name_line[len(p):].strip()
                        break
                else:
                    name = name_line
                break
            elif "Ship To" in ln and not name:
                cleaned = ln.replace("Ship To", "").replace("Bill To", "").strip()
                if cleaned and not any(p in cleaned for p in ["Mark Booth", "INUHR", "United States", "Cloverland"]):
                    name = cleaned
                    break

        dt_iso = ""
        if dt_str:
            try:
                dt_iso = datetime.strptime(dt_str, "%m/%d/%Y").strftime("%Y-%m-%d")
            except Exception:
                dt_iso = dt_str

        parsed_crs.append({
            "acct": cr.get("acct"),
            "cash_refund_no": cr_no,
            "so": so,
            "date": dt_str,
            "date_iso": dt_iso,
            "amount": amount,
            "ship_name": name,
            "ship_name_norm": norm_str(name),
            "zip5": zip5,
            "raw": raw
        })

    # Index Cash Refunds
    cr_by_zip_name = defaultdict(list)
    cr_by_zip = defaultdict(list)
    cr_by_name = defaultdict(list)
    for cr in parsed_crs:
        if cr["zip5"] and cr["ship_name_norm"]:
            cr_by_zip_name[(cr["zip5"], cr["ship_name_norm"])].append(cr)
        if cr["zip5"]:
            cr_by_zip[cr["zip5"]].append(cr)
        if cr["ship_name_norm"] and len(cr["ship_name_norm"]) > 4:
            cr_by_name[cr["ship_name_norm"]].append(cr)

    uconn = sqlite3.connect(UNIFIED_DB, uri=True)
    uconn.row_factory = sqlite3.Row
    aconn = sqlite3.connect(ARCHIVE_DB, uri=True)
    aconn.row_factory = sqlite3.Row

    # zoro_package_tracking lookup
    pkg_by_wb = defaultdict(list)
    for r in uconn.execute("SELECT * FROM zoro_package_tracking"):
        pkg_by_wb[r["wb_order_id"]].append(dict(r))

    # archive_tracking lookup
    trk_by_wb = defaultdict(lambda: {"tids": [], "row": None})
    for r in uconn.execute("SELECT * FROM archive_tracking WHERE email_timestamp >= '2026-09-15'"):
        wb = r["wb_order_id"]
        if not wb: continue
        entry = trk_by_wb[wb]
        if entry["row"] is None:
            entry["row"] = dict(r)
        for t in (r["tracking_ids"] or "").replace(";", ",").split(","):
            t = t.strip()
            if t and t not in entry["tids"]:
                entry["tids"].append(t)

    # Let's run full audit on both salesmercentile1 and salesmercentile
    results = {}
    for target in ["salesmercentile1", "salesmercentile"]:
        closing_rows = uconn.execute(f"""
            SELECT * FROM closing_orders
            WHERE zoro_email_ts >= '2026-09-20'
              AND (zoro_gmail_account = '{target}' OR zoro_account_name = '{target}')
              AND COALESCE(wb_order_id, '') LIKE 'WB%'
            ORDER BY zoro_email_ts DESC
        """).fetchall()

        archive_rows = uconn.execute(f"""
            SELECT * FROM archive_zoro
            WHERE email_timestamp >= '2026-09-20'
              AND (gmail_account = '{target}' OR zoro_account_name = '{target}')
              AND wb_order_id LIKE 'WB%'
            ORDER BY email_timestamp DESC
        """).fetchall()

        all_wbs = {}
        for r in closing_rows:
            all_wbs[r["wb_order_id"]] = dict(r)
        for r in archive_rows:
            d = dict(r)
            wb = d["wb_order_id"]
            if wb not in all_wbs:
                all_wbs[wb] = {
                    "wb_order_id": wb,
                    "amazon_order_id": "",
                    "account": "HOANK" if target == "salesmercentile1" else "Hathoro",
                    "profile_name": "HS_COUPON" if target == "salesmercentile1" else "DM_COUPON",
                    "sku": d.get("sku"),
                    "title": d.get("zoro_item_desc"),
                    "quantity": d.get("qty"),
                    "ship_name": d.get("ship_name"),
                    "ship_street": d.get("ship_street"),
                    "ship_city": d.get("ship_city"),
                    "ship_state": d.get("ship_state"),
                    "ship_postal_code": d.get("ship_zip"),
                    "zoro_order_total": d.get("order_total"),
                    "zoro_subtotal": d.get("subtotal"),
                    "zoro_tax": d.get("tax"),
                    "zoro_shipping": d.get("shipping_cost"),
                    "zoro_sku": d.get("sku"),
                    "zoro_qty": d.get("qty"),
                    "tracking_ids": "",
                    "zoro_email_ts": d.get("email_timestamp"),
                    "delivery_status": None,
                    "delivery_date": None,
                    "delivery_note": None,
                    "last_scan_location": None,
                    "last_scan_time": None,
                    "zoro_gmail_account": target,
                    "zoro_account_name": target,
                }

        classified_orders = []
        for wb, c in all_wbs.items():
            tk_info = trk_by_wb.get(wb, {"tids": [], "row": None})
            pkgs = list(pkg_by_wb.get(wb, []))
            pkg_map = {str(p.get("tracking_number") or "").strip(): p for p in pkgs if p.get("tracking_number")}

            zoro_tids = list(tk_info["tids"])
            for t in (c.get("tracking_ids") or "").replace(";", ",").split(","):
                t = t.strip()
                if not t or t in zoro_tids: continue
                p = pkg_map.get(t)
                if p:
                    pst = p.get("status") or ""
                    pdt = (p.get("delivered_at") or p.get("last_event_at") or p.get("shipped_at") or "")[:10]
                    if pst in ("Untracked", "Unknown"): continue
                    if pdt and pdt < "2026-09-01": continue
                    zoro_tids.append(t)
                elif not tk_info["tids"]:
                    zoro_tids.append(t)

            for p in pkgs:
                tn = str(p.get("tracking_number") or "").strip()
                pst = p.get("status") or ""
                pdt = (p.get("delivered_at") or p.get("last_event_at") or p.get("shipped_at") or "")[:10]
                if tn and tn not in zoro_tids and pst not in ("Untracked", "Unknown") and (not pdt or pdt >= "2026-09-01"):
                    zoro_tids.append(tn)

            ds = c.get("delivery_status") or ""
            dnote = c.get("delivery_note") or ""
            last_loc = c.get("last_scan_location") or ""
            last_time = c.get("last_scan_time") or ""

            delivered_tids = []
            undelivered_tids = []
            undelivered_details = []
            undelivered_scan_dates = []
            undelivered_statuses = []

            for tid in zoro_tids:
                p = pkg_map.get(tid)
                if p:
                    pst = p.get("status") or "Unknown"
                    ple = p.get("last_event") or ""
                    plex = p.get("last_exception") or ""
                    ploc = p.get("last_location") or ""
                    pdt = (p.get("delivered_at") or p.get("last_event_at") or p.get("shipped_at") or "")[:10]
                    detail_str = f"{tid}: {pst}"
                    if plex and plex != ple: detail_str += f" ({ple} | {plex})"
                    elif ple: detail_str += f" ({ple})"
                    if ploc: detail_str += f" [{ploc}]"
                    if pdt: detail_str += f" on {pdt}"
                    if pst in ("Delivered", "AllDelivered"):
                        delivered_tids.append(tid)
                    else:
                        undelivered_tids.append(tid)
                        undelivered_details.append(detail_str)
                        undelivered_statuses.append(pst)
                        if pdt: undelivered_scan_dates.append(pdt)
                else:
                    if ds in ("Delivered", "AllDelivered"):
                        delivered_tids.append(tid)
                    else:
                        undelivered_tids.append(tid)
                        st_label = ds or "Unconfirmed"
                        undelivered_statuses.append(st_label)
                        note_part = f" ({dnote})" if dnote else ""
                        loc_part = f" [{last_loc}]" if last_loc else ""
                        dt_part = f" on {last_time[:10]}" if last_time else ""
                        undelivered_details.append(f"{tid}: {st_label}{note_part}{loc_part}{dt_part}")
                        if last_time: undelivered_scan_dates.append(last_time[:10])

            if zoro_tids and delivered_tids and not undelivered_tids:
                ds = "AllDelivered" if len(zoro_tids) > 1 else "Delivered"

            # Check Cash Refunds
            ship_name = c.get("ship_name") or ""
            sname_norm = norm_str(ship_name)
            ship_zip = (c.get("ship_postal_code") or c.get("ship_zip") or "")[:5]
            zoro_order_dt = (c.get("zoro_email_ts") or "")[:10]
            latest_undeliv_scan = max(undelivered_scan_dates) if undelivered_scan_dates else (last_time[:10] if last_time else "")

            matched_crs = []
            if ship_zip and sname_norm and (ship_zip, sname_norm) in cr_by_zip_name:
                matched_crs.extend(cr_by_zip_name[(ship_zip, sname_norm)])
            elif sname_norm and sname_norm in cr_by_name:
                matched_crs.extend(cr_by_name[sname_norm])

            # Filter crs with refund date >= order date
            valid_crs = [cr for cr in matched_crs if not (cr["date_iso"] and zoro_order_dt and cr["date_iso"] < zoro_order_dt)]

            db_ref_amt = float(c.get("zoro_refund_amount") or 0.0)
            db_ref_status = (c.get("zoro_refund_status") or "").strip()
            
            is_already_refunded = False
            ref_note = ""
            ref_amt = 0.0
            if valid_crs:
                is_already_refunded = True
                ref_amt = sum(x["amount"] for x in valid_crs)
                cr_nos = ", ".join(dict.fromkeys(x["cash_refund_no"] for x in valid_crs if x["cash_refund_no"]))
                ref_dates = ", ".join(dict.fromkeys(x["date"] for x in valid_crs if x["date"]))
                ref_note = f"Cash Refund #{cr_nos} on {ref_dates}: ${ref_amt:.2f}"
            elif db_ref_amt > 0 or db_ref_status.lower() in ("refunded", "completed", "done", "issued", "credited"):
                is_already_refunded = True
                ref_amt = db_ref_amt
                ref_note = f"DB Refund ({db_ref_status}: ${db_ref_amt:.2f})"

            notes_all = (dnote + " " + " ".join(undelivered_details)).lower()
            has_damage = "damage" in notes_all or "improperly packaged" in notes_all
            has_return = any(k in notes_all for k in ["returning to sender", "return to sender", "returning to shipper", "returned to shipper", "returned to sender"])
            has_exception = any(k in notes_all for k in ["undeliverable", "refused", "unable to deliver", "delivery exception", "incorrect address", "barcode unreadable", "customer not available", "closed"])

            category = "Other"
            is_act = False

            if is_already_refunded:
                category = "Already Refunded by Zoro"
            elif not zoro_tids and not ds:
                category = "Freight / Untracked / Cancelled"
            elif ds in ("Delivered", "AllDelivered") and not undelivered_tids:
                category = "Delivered (Complete)"
            elif has_damage:
                category = "1A. Damaged in transit"
                is_act = True
            elif has_return:
                category = "1B. Returned / returning to sender"
                is_act = True
            elif has_exception and ((latest_undeliv_scan and latest_undeliv_scan <= STALLED_CUTOFF_DATE) or (not latest_undeliv_scan and zoro_order_dt <= STALLED_CUTOFF_DATE) or is_terminal_exception_or_damaged(notes_all)):
                category = "1C. Carrier exception (unable to deliver)"
                is_act = True
            elif delivered_tids and undelivered_tids:
                category = "2. Partial delivery (box missing or damaged)"
                is_act = True
            elif all(s in ("LabelCreated",) for s in undelivered_statuses) and ((latest_undeliv_scan and latest_undeliv_scan <= STALLED_CUTOFF_DATE) or (not latest_undeliv_scan and zoro_order_dt <= STALLED_CUTOFF_DATE)):
                category = "3. Ghost label (never picked up by carrier)"
                is_act = True
            elif undelivered_tids and ((latest_undeliv_scan and latest_undeliv_scan <= STALLED_CUTOFF_DATE) or (not latest_undeliv_scan and zoro_order_dt <= STALLED_CUTOFF_DATE)):
                category = "4. Lost or stalled in transit"
                is_act = True
            else:
                category = "In Transit (Active Window)"

            tot = float(c.get("zoro_order_total") or 0.0)
            n_all = len(zoro_tids) or 1
            n_claim = len(undelivered_tids) if is_act else 0
            est = tot if n_claim >= n_all else round(tot * n_claim / n_all, 2)

            classified_orders.append({
                "wb_order_id": wb,
                "amazon_order_id": c.get("amazon_order_id") or "",
                "account": c.get("account") or "",
                "profile_name": c.get("profile_name") or "",
                "true_acct": target,
                "zoro_email": f"{target}@gmail.com",
                "cust_no": "CUST25061417" if target == "salesmercentile1" else "CUST25017919",
                "zoro_order_date": zoro_order_dt,
                "deliver_by_date": c.get("deliver_by_date") or "",
                "zoro_order_total": round(tot, 2),
                "est_claim_value": round(est, 2),
                "zoro_subtotal": round(float(c.get("zoro_subtotal") or 0.0), 2),
                "zoro_tax": round(float(c.get("zoro_tax") or 0.0), 2),
                "zoro_shipping": round(float(c.get("zoro_shipping") or 0.0), 2),
                "category": category,
                "is_actionable": is_act,
                "all_tids": zoro_tids,
                "delivered_tids": delivered_tids,
                "undelivered_tids": undelivered_tids,
                "undelivered_details": undelivered_details,
                "latest_undeliv_scan": latest_undeliv_scan,
                "carrier": c.get("carrier") or "FedEx",
                "delivery_status": ds,
                "delivery_note": dnote,
                "ship_name": ship_name,
                "ship_street": c.get("ship_street") or "",
                "ship_city": c.get("ship_city") or "",
                "ship_state": c.get("ship_state") or "",
                "ship_zip": ship_zip,
                "ship_phone": c.get("ship_phone") or "",
                "zoro_sku": c.get("zoro_sku") or c.get("sku") or "",
                "zoro_qty": c.get("zoro_qty") or c.get("quantity") or 1,
                "item_desc": c.get("zoro_item_desc") or c.get("title") or "",
                "zoro_po_number": c.get("zoro_po_number") or "",
                "refund_source_note": ref_note,
                "refund_amt": ref_amt,
            })

        results[target] = classified_orders

    # Print summary
    print("\n================ FINAL AUDIT RESULTS ================")
    for target, orders in results.items():
        print(f"\n--- {target} ({len(orders)} orders) ---")
        cats = Counter([o["category"] for o in orders])
        for cat, cnt in sorted(cats.items()):
            tot_v = sum(o["zoro_order_total"] for o in orders if o["category"] == cat)
            clm_v = sum(o["est_claim_value"] for o in orders if o["category"] == cat)
            print(f"  {cat:45s}: {cnt:4d} orders | Total: ${tot_v:9.2f} | Est Claim: ${clm_v:9.2f}")
        act = [o for o in orders if o["is_actionable"]]
        print(f"  --> TOTAL ACTIONABLE UNREFUNDED: {len(act)} orders | Claim: ${sum(o['est_claim_value'] for o in act):,.2f}")

    # Save to json
    with open(f"{SCRATCH_DIR}/salesmercentile_compiled_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved results to {SCRATCH_DIR}/salesmercentile_compiled_results.json")

if __name__ == "__main__":
    run()
