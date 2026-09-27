#!/usr/bin/env python3
"""
ZUHRA Local Hunting & Approval Portal
Flask Backend + SQLite Database + Background Reverse Catalog Mining Engine
"""

from __future__ import annotations
import os
import sys
import json
import sqlite3
import threading
import time
import re
from datetime import datetime
import urllib.request
import ssl
import gzip
from typing import Dict, List, Any, Optional

from flask import Flask, request, jsonify, render_template, send_file, Response
import pandas as pd

# Paths
PORTAL_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(PORTAL_DIR))
DB_PATH = os.path.join(PORTAL_DIR, "portal_listings.db")
ZORO_DB_PATH = os.path.join(PROJECT_ROOT, "Automations", "TSV Files", "combined_reports.db")
KEEPA_KEY_PATH = os.path.join(PROJECT_ROOT, "Amazon_DS_Hunting", ".keepa_key")
HUNTER_DIR = os.path.join(PROJECT_ROOT, "Amazon_DS_Hunting")
HUNTER_DB = os.path.join(HUNTER_DIR, "hunter.db")
HUNTER_LOG = os.path.join(HUNTER_DIR, "hunter.log")
RUN_NOW_FLAG = os.path.join(HUNTER_DIR, "RUN_NOW")
INBOX_DIR = os.path.join(HUNTER_DIR, "keepa_inbox")

app = Flask(__name__, template_folder=os.path.join(PORTAL_DIR, "templates"))

# Global hunter state
hunter_state = {
    "is_running": False,
    "current_brand": "None",
    "scanned_count": 0,
    "matched_count": 0,
    "found_winners": 0,
    "status_message": "Ready to hunt",
    "log_history": []
}

our_stores = {"hoank", "hathoro", "hoank store"}

def get_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def load_keepa_key() -> str:
    k = os.environ.get("KEEPA_API_KEY", "").strip()
    if k: return k
    if os.path.exists(KEEPA_KEY_PATH):
        try:
            with open(KEEPA_KEY_PATH, encoding="utf-8") as f:
                return f.read().strip()
        except: pass
    return ""

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS portal_listings (
            asin TEXT PRIMARY KEY,
            brand TEXT,
            title TEXT,
            zoro_no TEXT,
            zoro_mpn TEXT,
            zoro_title TEXT,
            zoro_price REAL,
            zoro_shipping REAL,
            lowest_price REAL,
            lowest_seller TEXT,
            referral_fee REAL,
            total_cost REAL,
            net_profit REAL,
            net_margin REAL,
            monthly_sales INTEGER DEFAULT 50,
            pack_count INTEGER DEFAULT 1,
            status TEXT DEFAULT 'PENDING',
            source TEXT DEFAULT 'Zoro Reverse Mining',
            created_at TEXT,
            reviewed_at TEXT
        )
    """)
    conn.commit()
    have = {r[1] for r in cur.execute("PRAGMA table_info(portal_listings)")}
    for col, typ in (("competitors", "TEXT"), ("image_url", "TEXT"), ("category", "TEXT"),
                     ("verified_at", "TEXT"), ("match_method", "TEXT")):
        if col not in have:
            cur.execute(f"ALTER TABLE portal_listings ADD COLUMN {col} {typ}")
    conn.commit()

    # Pre-seed verified initial winners if table is empty
    cur.execute("SELECT COUNT(*) FROM portal_listings")
    if cur.fetchone()[0] == 0:
        initial_winners = [
            {
                "asin": "B011C34EVE", "brand": "DAP",
                "title": "DAP Kwik Seal Acrylic Latex Adhesive Caulk, 5.5 oz Tube, White",
                "zoro_no": "G0145363", "zoro_mpn": "18001",
                "zoro_title": "Acrylic Latex Adhesive Caulk, 5.5 oz, Tube, White, Kwik Seal",
                "zoro_price": 4.89, "zoro_shipping": 5.99, "lowest_price": 24.21,
                "lowest_seller": "Hearthshire", "referral_fee": 3.63, "total_cost": 14.51,
                "net_profit": 9.70, "net_margin": 40.07, "monthly_sales": 200, "pack_count": 1,
                "source": "Catalog Ingestion"
            },
            {
                "asin": "B00U1O5W12", "brand": "Nu-Calgon",
                "title": "Nu-Calgon 4287-34 Ice Machine Cleaner, Nickel Safe, 16 fl oz",
                "zoro_no": "G2902356", "zoro_mpn": "4287-34",
                "zoro_title": "Ice Machine Cleaner, Nickel Safe, 16 fl oz Bottle",
                "zoro_price": 13.99, "zoro_shipping": 5.99, "lowest_price": 37.09,
                "lowest_seller": "LP6572GH", "referral_fee": 5.56, "total_cost": 25.54,
                "net_profit": 11.55, "net_margin": 31.14, "monthly_sales": 200, "pack_count": 1,
                "source": "Catalog Ingestion"
            },
            {
                "asin": "B00004SBCS", "brand": "Channellock",
                "title": "Channellock 422 9.5-Inch V-Jaw Tongue & Groove Plier",
                "zoro_no": "G1067771", "zoro_mpn": "422",
                "zoro_title": "9 1/2 in V-Jaw Tongue and Groove Plier",
                "zoro_price": 4.04, "zoro_shipping": 5.99, "lowest_price": 14.77,
                "lowest_seller": "TheHardwareSource", "referral_fee": 2.22, "total_cost": 12.25,
                "net_profit": 2.52, "net_margin": 17.06, "monthly_sales": 500, "pack_count": 1,
                "source": "Catalog Ingestion"
            },
            {
                "asin": "B01AW326WE", "brand": "OdoBan",
                "title": "OdoBan Disinfectant & Odor Eliminator Concentrate, 5-Gallon Pail, Eucalyptus",
                "zoro_no": "G3401509", "zoro_mpn": "911062-5G",
                "zoro_title": "Disinfectant/Cleaner, 5 gal, Pail, Eucalyptus",
                "zoro_price": 72.99, "zoro_shipping": 0.00, "lowest_price": 99.98,
                "lowest_seller": "Outside 3P Merchant", "referral_fee": 15.00, "total_cost": 87.99,
                "net_profit": 11.99, "net_margin": 11.99, "monthly_sales": 50, "pack_count": 1,
                "source": "Catalog Ingestion"
            },
            {
                "asin": "B00F1I9ASO", "brand": "Dayton",
                "title": "Dayton 4 Bolt Flange Bearing, Ball, 1 1/4 in Bore",
                "zoro_no": "G3530182", "zoro_mpn": "4X731",
                "zoro_title": "4 Bolt Flange Bearing, Ball, 1 1/4in Bore, Cast Iron",
                "zoro_price": 72.39, "zoro_shipping": 0.00, "lowest_price": 87.22,
                "lowest_seller": "Sigweis (MFN)", "referral_fee": 13.08, "total_cost": 85.47,
                "net_profit": 1.75, "net_margin": 2.01, "monthly_sales": 80, "pack_count": 1,
                "source": "Dayton Reverse Mining"
            },
            {
                "asin": "B07GDLYJTL", "brand": "Dayton",
                "title": "Dayton Flange Bearing PPR0ZN001G Heavy Duty",
                "zoro_no": "G5510928", "zoro_mpn": "PPR0ZN001G",
                "zoro_title": "Flange Bearing Unit, PPR0ZN001G",
                "zoro_price": 32.89, "zoro_shipping": 5.99, "lowest_price": 43.35,
                "lowest_seller": "Megamart USA (MFN)", "referral_fee": 6.50, "total_cost": 45.38,
                "net_profit": 1.20, "net_margin": 2.77, "monthly_sales": 150, "pack_count": 1,
                "source": "Dayton Reverse Mining"
            }
        ]
        now_str = datetime.now().isoformat()
        for w in initial_winners:
            cur.execute("""
                INSERT OR IGNORE INTO portal_listings
                (asin, brand, title, zoro_no, zoro_mpn, zoro_title, zoro_price, zoro_shipping,
                 lowest_price, lowest_seller, referral_fee, total_cost, net_profit, net_margin,
                 monthly_sales, pack_count, status, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
            """, (
                w["asin"], w["brand"], w["title"], w["zoro_no"], w["zoro_mpn"], w["zoro_title"],
                w["zoro_price"], w["zoro_shipping"], w["lowest_price"], w["lowest_seller"],
                w["referral_fee"], w["total_cost"], w["net_profit"], w["net_margin"],
                w["monthly_sales"], w["pack_count"], w["source"], now_str
            ))
        conn.commit()

    conn.close()

init_db()

# -------------------------------------------------------------
# REST API ENDPOINTS
# -------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/listings", methods=["GET"])
def get_listings():
    status = request.args.get("status", "PENDING").upper()
    brand = request.args.get("brand", "").strip()
    min_margin = float(request.args.get("min_margin", 0.0))
    search = request.args.get("search", "").strip().lower()
    sort_by = request.args.get("sort_by", "net_margin")
    order = request.args.get("order", "desc").upper()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    query = "SELECT * FROM portal_listings WHERE net_margin >= ?"
    params = [min_margin]

    if status != "ALL":
        query += " AND status = ?"
        params.append(status)

    if brand:
        query += " AND UPPER(brand) = ?"
        params.append(brand.upper())

    if search:
        query += " AND (LOWER(asin) LIKE ? OR LOWER(title) LIKE ? OR LOWER(zoro_no) LIKE ?)"
        term = f"%{search}%"
        params.extend([term, term, term])

    allowed_sorts = {"net_margin", "net_profit", "lowest_price", "zoro_price", "monthly_sales", "created_at"}
    sort_col = sort_by if sort_by in allowed_sorts else "net_margin"
    sort_order = "DESC" if order == "DESC" else "ASC"

    query += f" ORDER BY {sort_col} {sort_order}"

    cur.execute(query, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    return jsonify({"listings": rows, "count": len(rows)})

@app.route("/api/stats", methods=["GET"])
def get_stats():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM portal_listings WHERE status = 'PENDING'")
    pending = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*), COALESCE(SUM(net_profit), 0) FROM portal_listings WHERE status = 'APPROVED'")
    appr_row = cur.fetchone()
    approved = appr_row[0]
    approved_profit = round(appr_row[1], 2)

    cur.execute("SELECT COUNT(*) FROM portal_listings WHERE status = 'REJECTED'")
    rejected = cur.fetchone()[0]

    cur.execute("SELECT DISTINCT brand FROM portal_listings WHERE brand IS NOT NULL AND brand != '' ORDER BY brand")
    brands = [r[0] for r in cur.fetchall()]

    conn.close()

    return jsonify({
        "pending": pending,
        "approved": approved,
        "approved_profit": approved_profit,
        "rejected": rejected,
        "brands": brands,
        "hunter": hunter_state
    })

@app.route("/api/action", methods=["POST"])
def perform_action():
    data = request.json or {}
    asin = data.get("asin")
    action = data.get("action", "").upper()

    if not asin or action not in ["APPROVE", "REJECT", "PENDING"]:
        return jsonify({"success": False, "error": "Invalid action or ASIN"}), 400

    new_status = "APPROVED" if action == "APPROVE" else ("REJECTED" if action == "REJECT" else "PENDING")
    now_str = datetime.now().isoformat()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE portal_listings SET status = ?, reviewed_at = ? WHERE asin = ?", (new_status, now_str, asin))
    conn.commit()
    conn.close()

    return jsonify({"success": True, "asin": asin, "new_status": new_status})

@app.route("/api/batch_action", methods=["POST"])
def batch_action():
    data = request.json or {}
    asins = data.get("asins", [])
    action = data.get("action", "").upper()

    if not asins or action not in ["APPROVE", "REJECT", "PENDING"]:
        return jsonify({"success": False, "error": "Invalid input"}), 400

    new_status = "APPROVED" if action == "APPROVE" else ("REJECTED" if action == "REJECT" else "PENDING")
    now_str = datetime.now().isoformat()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    placeholders = ",".join(["?"] * len(asins))
    cur.execute(f"UPDATE portal_listings SET status = ?, reviewed_at = ? WHERE asin IN ({placeholders})", [new_status, now_str] + asins)
    conn.commit()
    conn.close()

    return jsonify({"success": True, "count": len(asins), "new_status": new_status})

@app.route("/api/export_approved", methods=["GET"])
def export_approved():
    fmt = request.args.get("format", "csv").lower()
    status_filter = request.args.get("status", "APPROVED").upper()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    if status_filter == "ALL":
        cur.execute("""
            SELECT asin, brand, title, zoro_no, zoro_mpn, zoro_price, zoro_shipping,
                   lowest_price, lowest_seller, referral_fee, total_cost, net_profit,
                   net_margin, monthly_sales, status, created_at
            FROM portal_listings
            ORDER BY net_margin DESC
        """)
        cols = [
            "ASIN", "Brand", "Title", "Zoro Part #", "Zoro MPN", "Zoro Wholesale ($)",
            "Zoro Shipping ($)", "Selling Price ($)", "Lowest Competitor", "Amazon Fee 15% ($)",
            "Total Cost ($)", "Net Profit ($)", "Net Margin (%)", "Monthly Sales", "Status", "Discovered Date"
        ]
    else:
        cur.execute("""
            SELECT asin, brand, title, zoro_no, zoro_mpn, zoro_price, zoro_shipping,
                   lowest_price, lowest_seller, referral_fee, total_cost, net_profit,
                   net_margin, monthly_sales, created_at
            FROM portal_listings
            WHERE status = ?
            ORDER BY net_margin DESC
        """, (status_filter,))
        cols = [
            "ASIN", "Brand", "Title", "Zoro Part #", "Zoro MPN", "Zoro Wholesale ($)",
            "Zoro Shipping ($)", "Selling Price ($)", "Lowest Competitor", "Amazon Fee 15% ($)",
            "Total Cost ($)", "Net Profit ($)", "Net Margin (%)", "Monthly Sales", "Discovered Date"
        ]
        
    rows = cur.fetchall()
    conn.close()

    df = pd.DataFrame(rows, columns=cols)
    prefix = "Approved" if status_filter == "APPROVED" else f"Listings_{status_filter}"
    export_dir = os.path.join(PORTAL_DIR, "exports")
    os.makedirs(export_dir, exist_ok=True)
    # one file per status, overwritten on each export (no pile-up of timestamped copies)
    export_path = os.path.join(export_dir, f"{prefix}_Export.{ 'xlsx' if fmt == 'excel' else 'csv' }")

    if fmt == "excel":
        df.to_excel(export_path, index=False)
        return send_file(export_path, as_attachment=True, download_name=f"{prefix}_{datetime.now().strftime('%Y-%m-%d')}.xlsx")
    else:
        df.to_csv(export_path, index=False)
        return send_file(export_path, as_attachment=True, download_name=f"{prefix}_{datetime.now().strftime('%Y-%m-%d')}.csv")

# -------------------------------------------------------------
# BACKGROUND REVERSE CATALOG MINING ENGINE
# -------------------------------------------------------------
def log_hunter(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    hunter_state["status_message"] = msg
    hunter_state["log_history"].append(line)
    if len(hunter_state["log_history"]) > 60:
        hunter_state["log_history"].pop(0)
    print(line)

def check_and_wait_for_tokens(key: str):
    """Ensure Keepa token bucket is positive before dispatching batch requests."""
    try:
        url = f"https://api.keepa.com/token?key={key}"
        req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip", "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=get_ssl_context(), timeout=10) as r:
            raw = r.read()
            try:
                raw = gzip.decompress(raw)
            except:
                pass
            info = json.loads(raw.decode("utf-8"))
            tokens_left = info.get("tokensLeft", 0)
            refill_in = info.get("refillIn", 0)
            if tokens_left < 5:
                wait_sec = max(5, int(refill_in / 1000.0) + 2) if refill_in > 0 else 15
                log_hunter(f"Keepa token bucket low ({tokens_left}). Pacing engine: waiting {wait_sec}s for refill...")
                time.sleep(wait_sec)
    except Exception:
        pass

def run_reverse_hunt_thread(target_brand: Optional[str] = None):
    if hunter_state["is_running"]:
        return
    hunter_state["is_running"] = True
    hunter_state["scanned_count"] = 0
    hunter_state["matched_count"] = 0
    hunter_state["found_winners"] = 0

    try:
        log_hunter("Starting 24/7 Reverse Catalog Mining Engine...")
        key = load_keepa_key()
        if not key:
            log_hunter("Error: Keepa API key not found.")
            hunter_state["is_running"] = False
            return

        conn_z = sqlite3.connect(ZORO_DB_PATH)
        cur_z = conn_z.cursor()

        brands_to_scan = [target_brand] if target_brand else [
            "DAYTON", "WESTWARD", "CONDOR", "NU-CALGON", "RUBBERMAID",
            "SQUARE D", "WILLIAMS", "CHANNELLOCK", "SPEEDAIRE", "USA INDUSTRIALS"
        ]

        for b in brands_to_scan:
            hunter_state["current_brand"] = b
            log_hunter(f"Mining Zoro catalog for Brand: {b} (In Stock, $25-$300)...")

            cur_z.execute("""
                SELECT zoro_no, manufacturer_part_number, upc_code, short_description, sales_price, uom 
                FROM zoro_data 
                WHERE UPPER(zoro_brand_name) = ?
                  AND availabilityStatus = 'In Stock'
                  AND upc_code IS NOT NULL AND LENGTH(upc_code) >= 10
                  AND sales_price BETWEEN 25.0 AND 300.0
                LIMIT 60
            """, (b,))
            items = cur_z.fetchall()

            if not items:
                log_hunter(f"No in-stock UPC items found for {b}. Checking next brand.")
                continue

            log_hunter(f"Found {len(items)} in-stock Zoro products for {b}. Querying Keepa...")
            hunter_state["scanned_count"] += len(items)

            upc_map = {}
            for r in items:
                for u in re.findall(r"\d{10,14}", str(r[2])):
                    upc_map[u] = r

            upc_list = list(upc_map.keys())
            chunk_size = 20
            for i in range(0, len(upc_list), chunk_size):
                check_and_wait_for_tokens(key)

                chunk = upc_list[i:i+chunk_size]
                code_str = ",".join(chunk)
                url = f"https://api.keepa.com/product?key={key}&domain=1&code={code_str}&history=0"
                req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip", "User-Agent": "Mozilla/5.0"})

                try:
                    with urllib.request.urlopen(req, context=get_ssl_context(), timeout=20) as resp:
                        raw = gzip.decompress(resp.read())
                        data = json.loads(raw.decode("utf-8"))
                    products = data.get("products", [])
                    log_hunter(f"Keepa returned {len(products)} matching Amazon products (Tokens left: {data.get('tokensLeft', 0)})")
                except Exception as e:
                    if "429" in str(e):
                        log_hunter("Keepa rate-limited (429). Waiting 25s for bucket refill...")
                        time.sleep(25)
                    else:
                        log_hunter(f"Keepa request error: {e}")
                        time.sleep(5)
                    continue

                conn_p = sqlite3.connect(DB_PATH)
                cur_p = conn_p.cursor()

                for p in products:
                    asin = p.get("asin")
                    if not asin or not asin.startswith("B0"):
                        continue
                    hunter_state["matched_count"] += 1

                    title = str(p.get("title", ""))
                    amz_brand = str(p.get("brand", "")).upper()
                    stats = p.get("stats", {})
                    cur_new = stats.get("current", [None]*20)[1]
                    cur_bb = stats.get("buyBoxPrice")

                    # Check if Amazon Retail is on listing
                    is_amz_selling = False
                    bb_seller = str(stats.get("buyBoxSellerId", ""))
                    if "ATVPDKIKX0DER" in bb_seller: # Amazon.com seller ID
                        is_amz_selling = True

                    sp = round(cur_new / 100.0, 2) if cur_new and cur_new > 0 else (round(cur_bb / 100.0, 2) if cur_bb and cur_bb > 0 else None)
                    if not sp or sp <= 0 or is_amz_selling:
                        continue

                    # Match back to Zoro
                    z_match = None
                    for u in p.get("upcList", []):
                        if u in upc_map:
                            z_match = upc_map[u]
                            break

                    if not z_match:
                        continue

                    z_no, z_mpn, z_upc, z_title, z_price, z_uom = z_match
                    zp = float(z_price)
                    z_ship = 0.0 if zp >= 50.0 else 5.99

                    ref_fee = round(sp * 0.15, 2)
                    tot_cost = round(zp + z_ship + ref_fee, 2)
                    net_profit = round(sp - tot_cost, 2)
                    net_margin = round((net_profit / sp) * 100.0, 2)

                    if net_margin >= 1.0:
                        now_str = datetime.now().isoformat()
                        cur_p.execute("""
                            INSERT OR REPLACE INTO portal_listings
                            (asin, brand, title, zoro_no, zoro_mpn, zoro_title, zoro_price, zoro_shipping,
                             lowest_price, lowest_seller, referral_fee, total_cost, net_profit, net_margin,
                             monthly_sales, pack_count, status, source, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
                        """, (
                            asin, b, title, z_no, z_mpn or "", z_title,
                            zp, z_ship, sp, "3P Merchant (Keepa Live)", ref_fee, tot_cost,
                            net_profit, net_margin, p.get("monthlySold", 50) or 50, 1,
                            f"{b} Reverse Mining", now_str
                        ))
                        hunter_state["found_winners"] += 1
                        log_hunter(f"★ NEW PROFITABLE LISTING: {asin} | {b} | Zoro: ${zp:.2f} | Amazon: ${sp:.2f} | Profit: +${net_profit:.2f} ({net_margin:.1f}%)")

                conn_p.commit()
                conn_p.close()
                time.sleep(2.0)

        conn_z.close()
        log_hunter(f"Mining cycle completed! Found {hunter_state['found_winners']} new winning listings.")

    except Exception as e:
        log_hunter(f"Hunter encountered an error: {e}")
    finally:
        hunter_state["is_running"] = False

# -------------------------------------------------------------
# HUNTER (hunter.py) STATUS — read-only view of hunter.db + hunter.log
# -------------------------------------------------------------
def _hunter_conn():
    if not os.path.exists(HUNTER_DB):
        return None
    c = sqlite3.connect(f"file:{HUNTER_DB}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    return c

@app.route("/api/hunter", methods=["GET"])
def hunter_status():
    out = {"available": False}
    c = _hunter_conn()
    if c is None:
        return jsonify(out)
    try:
        kv = {r["k"]: r["v"] for r in c.execute("SELECT k, v FROM kv")}
        hb = kv.get("heartbeat")
        alive = False
        if hb:
            try:
                alive = (datetime.now() - datetime.fromisoformat(hb)).total_seconds() < 90
            except ValueError:
                pass
        pipeline = [dict(r) for r in c.execute("SELECT stage, COALESCE(verdict,'READY') AS verdict, COUNT(*) AS n FROM candidates GROUP BY 1,2 ORDER BY n DESC")]
        winners_today = c.execute("SELECT COUNT(*) FROM candidates WHERE verdict='WINNER' AND verified_at >= date('now','localtime')").fetchone()[0]
        ready = c.execute("SELECT COUNT(*) FROM candidates WHERE stage='READY' AND scrape_tries<3").fetchone()[0]
        total = c.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        winners = c.execute("SELECT COUNT(*) FROM candidates WHERE verdict='WINNER'").fetchone()[0]
        k24 = c.execute("SELECT COUNT(*), COALESCE(SUM(tokens_consumed),0), MAX(tokens_left) FROM keepa_calls WHERE at >= datetime('now','localtime','-1 day')").fetchone()
        last_call = c.execute("SELECT tokens_left, at FROM keepa_calls ORDER BY at DESC LIMIT 1").fetchone()
        brands_pending = [dict(r) for r in c.execute("SELECT brand, zoro_items, zoro_items_50, tier, top_category FROM brand_queue WHERE status='PENDING' ORDER BY tier ASC, zoro_items_50 DESC LIMIT 40")]
        brands_done = [dict(r) for r in c.execute("SELECT brand, status, asins_found, asins_fetched, winners, queried_at, note FROM brand_queue WHERE status IN ('QUERIED','EMPTY','ERROR') ORDER BY queried_at DESC LIMIT 25")]
        brand_asins_pending = c.execute("SELECT COUNT(*) FROM brand_asins WHERE status='PENDING'").fetchone()[0]
        zq = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM zoro_queue GROUP BY 1")}
        files = [dict(r) for r in c.execute("SELECT path, rows, added, ingested_at FROM ingested_files ORDER BY ingested_at DESC LIMIT 20")]
        for f in files:
            f["name"] = os.path.basename(f.pop("path"))
        recent = [dict(r) for r in c.execute("""SELECT asin, brand, verdict, reason, live_margin, live_profit, verified_at, evaluated_at
                                                 FROM candidates WHERE stage='DONE' ORDER BY COALESCE(verified_at, evaluated_at) DESC LIMIT 30""")]
        log_lines = []
        if os.path.exists(HUNTER_LOG):
            try:
                with open(HUNTER_LOG, "rb") as fh:
                    fh.seek(0, os.SEEK_END)
                    size = fh.tell()
                    fh.seek(max(0, size - 12000))
                    log_lines = fh.read().decode("utf-8", "ignore").splitlines()[-40:]
            except OSError:
                pass
        inbox = []
        if os.path.isdir(INBOX_DIR):
            for f in sorted(os.listdir(INBOX_DIR)):
                if not f.startswith(".") and not f.startswith("~$"):
                    inbox.append(f)
        out.update({
            "available": True, "alive": alive, "state": kv.get("cycle_state", "idle"),
            "heartbeat": hb, "last_cycle_at": kv.get("last_cycle_at"), "next_run_at": kv.get("next_run_at"),
            "last_cycle": json.loads(kv.get("last_cycle_summary") or "{}"),
            "pipeline": pipeline, "candidates_total": total, "winners_total": winners, "winners_today": winners_today,
            "ready_to_verify": ready,
            "keepa": {"calls_24h": k24[0], "tokens_24h": k24[1], "tokens_left": last_call[0] if last_call else None,
                      "last_call_at": last_call[1] if last_call else None},
            "brands_pending": brands_pending, "brands_done": brands_done, "brand_asins_pending": brand_asins_pending,
            "zoro_queue": zq, "files": files, "inbox": inbox, "inbox_dir": INBOX_DIR, "recent": recent, "log": log_lines,
            "run_now_requested": os.path.exists(RUN_NOW_FLAG),
            "catalog_refresh_at": kv.get("catalog_refresh_at"), "min_sold": kv.get("min_sold_applied"),
        })
    finally:
        c.close()
    return jsonify(out)

@app.route("/api/run_now", methods=["POST"])
def run_now():
    try:
        with open(RUN_NOW_FLAG, "w") as fh:
            fh.write(datetime.now().isoformat())
        return jsonify({"success": True})
    except OSError as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/trigger_hunt", methods=["POST"])
def trigger_hunt():
    if hunter_state["is_running"]:
        return jsonify({"success": False, "message": "Hunter is already running."}), 400

    brand = (request.json or {}).get("brand")
    t = threading.Thread(target=run_reverse_hunt_thread, args=(brand,), daemon=True)
    t.start()
    return jsonify({"success": True, "message": f"Hunting started for {brand or 'all priority industrial brands'}."})

if __name__ == "__main__":
    port = 5055
    print(f"\n=======================================================")
    print(f"🚀 ZUHRA Listing Approval & Hunting Portal")
    print(f"👉 Open in browser: http://127.0.0.1:{port}")
    print(f"=======================================================\n")
    app.run(host="127.0.0.1", port=port, debug=False)
