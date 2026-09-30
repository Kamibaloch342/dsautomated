#!/usr/bin/env python3
"""
hunter.py — the ONE script that keeps finding profitable Zoro → Amazon products.

Run it once and leave it running:

    python3 Amazon_DS_Hunting/hunter.py

Every cycle it:
  0. Once a day re-downloads the Zoro TSV feed from S3 (TSV Files/auto_download.py) and rebuilds
     combined_reports.db (build_zoro_db.py), then rebuilds its own lookup index.
  1. Reloads the ASINs already listed on HOANK / Hathoro (All Listings reports) so those
     are never hunted again.
  2. Ingests any Keepa export you drop into  Amazon_DS_Hunting/keepa_inbox/  (xlsx / csv).
     This costs ZERO Keepa tokens — it is the main way to feed the hunter when the API is tight.
  3. Brand leg (Keepa API, token governed): ranks Zoro brands by in-stock ≥ $50 stock in niche
     MRO categories, asks Keepa Product Finder (fifteen brands per query) for the ASINs of that brand
     that actually sell 500+ a month, then pulls their product codes. Raw Zoro UPC mining only runs
     once that pre-qualified queue is empty, because a UPC lookup cannot be filtered by demand.
  4. Keeps only listings bought 500+ times a month (--min-sold), matches every candidate to Zoro
     by UPC/EAN/GTIN (then MPN), validates 1:1 pack size,
     drops anything Amazon Retail sells, and pre-screens profit on Keepa's price when known.
  5. Verifies survivors on the live Amazon offers page (ZIP 10001) with the shared AOD scraper,
     prices against the LOWEST new competitor, and again drops Amazon Retail / our own stores.
  6. Publishes winners (margin ≥ 1 % at the lowest competitor, no coupons, $5.99 shipping under $50)
     into the review portal (portal/portal_listings.db → Approve/Reject at http://127.0.0.1:5055),
     Hunter_Winners.xlsx, and a Telegram note.

State lives in Amazon_DS_Hunting/hunter.db so restarts never re-spend tokens or re-scrape.

Flags:
    --every 60        minutes between cycles (default hourly); a new inbox file or the portal's
                      "Run now" button starts a cycle immediately
    --once            run a single cycle and exit
    --status          print pipeline counts and exit
    --no-portal       don't auto-start the review portal
    --no-keepa-api    never spend Keepa tokens (inbox files + scraping only)
    --no-scrape       skip the live Amazon verification step (ingest/match only)
    --headed          show the Chrome window while scraping
    --min-margin 1.0  minimum net margin % at the lowest competitor price
    --zoro-min-price 50 / --zoro-max-price 400   reverse-mining price band
    --scrape-batch 20 ASINs verified per cycle
    --keepa-batch 50  UPCs sent to Keepa per API call (the hourly token refill affords ~52)
    --offer-ttl-days 4  how long a live offer snapshot stays valid
"""

from __future__ import annotations

import argparse
import glob
import gzip
import json
import os
import random
import re
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import pandas as pd
import warnings

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

# --------------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------------
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
AUTOMATIONS_DIR = os.path.join(PROJECT_ROOT, "Automations")
SCRIPTS_DIR = os.path.join(AUTOMATIONS_DIR, "scripts")
for p in (CURRENT_DIR, SCRIPTS_DIR, AUTOMATIONS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

ZORO_DB = os.path.join(AUTOMATIONS_DIR, "TSV Files", "combined_reports.db")
if not os.path.exists(ZORO_DB):
    ZORO_DB = os.path.join(CURRENT_DIR, "combined_reports.db")
HUNTER_DB = os.path.join(CURRENT_DIR, "hunter.db")
PORTAL_DIR = os.path.join(CURRENT_DIR, "portal")
PORTAL_DB = os.path.join(PORTAL_DIR, "portal_listings.db")
PORTAL_SERVER = os.path.join(PORTAL_DIR, "server.py")
PORTAL_PORT = 5055
INBOX_DIR = os.path.join(CURRENT_DIR, "keepa_inbox")
STORE_LISTINGS_DIR = os.path.join(CURRENT_DIR, "store_listings")
OFFER_CACHE_DIR = os.path.join(CURRENT_DIR, ".cache")
LOG_FILE = os.path.join(CURRENT_DIR, "hunter.log")
WINNERS_XLSX = os.path.join(CURRENT_DIR, "Hunter_Winners.xlsx")
KEEPA_KEY_FILE = os.path.join(CURRENT_DIR, ".keepa_key")
KEEPA_CACHE_DIR = os.path.join(CURRENT_DIR, ".keepa_cache")
RUN_NOW_FLAG = os.path.join(CURRENT_DIR, "RUN_NOW")
TSV_DIR = os.path.join(AUTOMATIONS_DIR, "TSV Files")
FEED_DOWNLOAD = os.path.join(TSV_DIR, "auto_download.py")
FEED_BUILD = os.path.join(TSV_DIR, "build_zoro_db.py")
FEED_LOG = os.path.join(CURRENT_DIR, "catalog_refresh.log")

for d in (INBOX_DIR, STORE_LISTINGS_DIR, OFFER_CACHE_DIR):
    os.makedirs(d, exist_ok=True)

# Where the stores' "All Listings Report" files are looked for (any of these, all merged).
STORE_REPORT_GLOBS = [
    os.path.join(AUTOMATIONS_DIR, "data", "reports", "HOANK", "*.txt"),
    os.path.join(AUTOMATIONS_DIR, "data", "reports", "Hathoro", "*.txt"),
    os.path.join(AUTOMATIONS_DIR, "data", "reports", "all_listings_report*.tsv"),
    os.path.join(AUTOMATIONS_DIR, "data", "reports", "temp_downloads", "All+Listings*.txt"),
    os.path.join(os.path.expanduser("~"), "Downloads", "All+Listings*Report*.txt"),
    os.path.join(STORE_LISTINGS_DIR, "*.txt"),
    os.path.join(STORE_LISTINGS_DIR, "*.tsv"),
    os.path.join(STORE_LISTINGS_DIR, "*.csv"),
]

OUR_STORE_WORDS = ("hoank", "hathoro")
AMAZON_SELLER_ID = "ATVPDKIKX0DER"
REFERRAL_FEE = 0.15
FREE_SHIP_THRESHOLD = 50.0
ZORO_SHIP_FEE = 5.99

# Amazon root categories a Zoro category's products can legitimately live in. Without this the brand
# filter pulls in a different company that happens to share the name: "Continental" returns bicycle
# tyres instead of industrial belts, "Parker" returns pens instead of Parker Hannifin fittings.
AMZ_INDUSTRIAL = [16310091, 228013]          # Industrial & Scientific · Tools & Home Improvement
AMZ_ROOTS: Dict[str, List[int]] = {
    "Tools & Machining": [228013, 16310091],
    "Safety": [16310091, 228013],
    "Plumbing": [228013, 16310091],
    "Electrical Supplies": [228013, 16310091, 172282],
    "Heating & Cooling": [228013, 16310091, 2619525011],
    "Power Transmission": [16310091, 228013],
    "Fasteners": [228013, 16310091],
    "Hardware & Building Materials": [228013, 16310091],
    "Material Handling": [16310091, 228013],
    "Pumps": [16310091, 228013],
    "Test Instruments & Gauges": [16310091, 228013],
    "Welding, Soldering & Brazing": [228013, 16310091],
    "Abrasives & Polishers": [16310091, 228013],
    "Adhesives & Tapes": [16310091, 228013],
    "Raw Materials": [16310091, 228013],
    "Lab Equipment & Supplies": [16310091],
    "Storage & Workspace": [16310091, 228013],
    "Lighting": [228013, 16310091, 172282],
    "Electronics": [172282, 16310091],
    "Vehicle Maintenance": [15684181, 16310091],
    "Grounds & Outdoor": [2972638011, 228013],
    "Janitorial & Cleaning Supplies": [16310091, 3760901],
    "Food Service & Restaurant Supplies": [16310091, 1055398],
    "Shipping, Packaging & Office Supplies": [1064954, 16310091],
    "Furniture, Linens & Decor": [1055398, 1064954],
}


def amz_roots(zoro_category: Optional[str]) -> List[int]:
    return AMZ_ROOTS.get(zoro_category or "", AMZ_INDUSTRIAL)


# Zoro level-1 categories: lower tier number = mined first (softer 3P competition).
CATEGORY_TIER = {
    "Plumbing": 1, "Electrical Supplies": 1, "Heating & Cooling": 1, "Lighting": 1,
    "Pumps": 1, "Power Transmission": 1, "Test Instruments & Gauges": 1,
    "Material Handling": 1, "Hardware & Building Materials": 1,
    "Welding, Soldering & Brazing": 1, "Motors": 1, "Hydraulics": 1, "Pneumatics": 1,
    "Tools & Machining": 2, "Fasteners": 2, "Vehicle Maintenance": 2, "Storage & Workspace": 2,
    "Grounds & Outdoor": 2, "Lab Equipment & Supplies": 2, "Food Service & Restaurant Supplies": 2,
    "Electronics": 2, "Fleet & Vehicle Maintenance": 2,
    "Safety": 3, "Janitorial & Cleaning Supplies": 3, "Adhesives & Tapes": 3,
    "Abrasives & Polishers": 3, "Raw Materials": 3, "Furniture, Linens & Decor": 3,
    "Shipping, Packaging & Office Supplies": 3,
}

# --------------------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------------------
def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 5_000_000:
            os.replace(LOG_FILE, LOG_FILE + ".1")
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------
def norm_code(code: Any) -> str:
    """UPC/EAN/GTIN → digits only, leading zeros dropped (so 0+UPC-A == EAN-13)."""
    s = re.sub(r"[^0-9]", "", str(code or ""))
    return s.lstrip("0")


def codes_in(text: Any) -> List[str]:
    """Every 8–14 digit code found in a cell (Keepa and Zoro both sometimes pack several)."""
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return []
    s = str(text)
    if s.endswith(".0"):
        s = s[:-2]
    out = []
    for c in re.findall(r"\d{8,14}", s):
        k = norm_code(c)
        if k and k not in out:
            out.append(k)
    return out


def norm_mpn(mpn: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(mpn or "").upper())


def norm_brand(b: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(b or "").upper())


def money(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        if pd.isna(val) or val <= 0:
            return None
        return float(val)
    s = str(val).replace("$", "").replace(",", "").strip()
    m = re.search(r"[0-9]+(?:\.[0-9]+)?", s)
    if not m:
        return None
    v = float(m.group(0))
    return v if v > 0 else None


def to_int(val: Any, default: int = 0) -> int:
    try:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return default
        return int(float(str(val).replace(",", "")))
    except (TypeError, ValueError):
        return default


_FRAC = re.compile(r"(\d+)\s+(\d+)\s*/\s*(\d+)")
_SIMPLE_FRAC = re.compile(r"(?<![\d/])(\d+)\s*/\s*(\d+)(?![\d/])")
_UNIT = r"(?:in(?:ch(?:es)?)?|\"|\'\'|”|′′|ft|feet|mm|cm|m\b|yd|yard)"
_NUM = r"\d+(?:\.\d+)?(?:\s+\d+\s*/\s*\d+)?(?:\s*/\s*\d+)?"
_DIM = re.compile(rf"({_NUM})\s*(?:{_UNIT})?\s*(?:[WLHDwlhd]\b\.?)?\s*[x×X]\s*({_NUM})\s*(?:{_UNIT})?\s*(?:[WLHDwlhd]\b\.?)?(?:\s*[x×X]\s*({_NUM})\s*(?:{_UNIT})?)?", re.I)
_BAD_DIM_UNITS = re.compile(r"\b(?:yd|yard|ft|feet|mm|cm|gauge|awg|ga)\b", re.I)
_STOP = {"the", "and", "for", "with", "from", "size", "color", "clear", "black", "white", "blue", "red",
         "pack", "each", "type", "duty", "heavy", "inch", "inches", "plus", "new", "style", "shape",
         "set", "kit", "pcs", "per", "usa", "pro", "max", "min", "ltd", "inc", "use", "duty", "grey",
         "gray", "green", "brown", "clr", "std", "reg", "lge", "sml"}


def _dim_num(t: str) -> Optional[float]:
    """'36' / '29 1/2' / '1/2' → float. Returns None for nonsense like the '1/0' in a wire gauge."""
    t = t.strip()
    m = _FRAC.fullmatch(t)
    if m:
        den = int(m.group(3))
        return round(int(m.group(1)) + int(m.group(2)) / den, 3) if den else None
    m = _SIMPLE_FRAC.fullmatch(t)
    if m:
        den = int(m.group(2))
        return round(int(m.group(1)) / den, 3) if den else None
    try:
        return round(float(t), 3)
    except ValueError:
        return None


def parse_dims(title: Any) -> Tuple[float, ...]:
    """Largest width x height (x depth) found in a title, sorted descending. () when none or ambiguous.
    Zoro writes '36 x 48 in' / '36\"x48\"'; Amazon writes '36 x 48 in' / \'36\'\' x 48\'\'\'."""
    s2 = str(title or "")
    best: Tuple[float, ...] = ()
    for m in _DIM.finditer(s2):
        span = s2[max(0, m.start() - 12):m.end() + 12]
        if _BAD_DIM_UNITS.search(span):
            continue
        vals = [_dim_num(g) for g in m.groups() if g]
        vals = [v for v in vals if v and 0.5 <= v <= 500]
        if len(vals) < 2:
            continue
        t = tuple(sorted(vals, reverse=True))
        if sum(t) > sum(best):
            best = t
    return best


def dims_key(title: Any) -> str:
    d = parse_dims(title)
    return "x".join(f"{v:g}" for v in d) if d else ""


def title_tokens(title: Any) -> Set[str]:
    """Significant words of a title, for checking two listings describe the same kind of product."""
    return {w for w in re.findall(r"[a-z]{3,}", str(title or "").lower()) if w not in _STOP}


def parse_pack(desc: Any) -> int:
    """Pack count from a Zoro or Amazon title ('PK12', '12/Pack', 'Pack of 6', 'Case of 4')."""
    s = str(desc or "")
    for pat in (r"\bPK\s*(\d+)\b", r"\b(\d+)\s*(?:/|-)?\s*PK\b", r"(\d+)\s*per\s*(?:box|case|carton|pack)",
                r"Pack of (\d+)", r"Case of (\d+)", r"Box of (\d+)", r"Set of (\d+)", r"\b(\d+)\s*[- ]?Pack\b", r"\b(\d+)\s*Count\b",
                r"\((\d+)\s*(?:Pack|Count|pcs|pieces|pair|pairs|ct)\)", r"\b(\d+)\s*(?:pcs|pieces|pairs?|ct)\b", r"\b(\d+)\s*rolls?\b"):
        m = re.search(pat, s, re.IGNORECASE)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 5000:
                return n
    return 1


def ship_fee(zoro_price: float) -> float:
    return 0.0 if zoro_price >= FREE_SHIP_THRESHOLD else ZORO_SHIP_FEE


ZORO_TAX_PCT = 0.0  # --zoro-tax-pct: sales tax Zoro charges you, as a percent of the item price (0 if tax-exempt)


def profit_at(zoro_price: float, sell_price: Optional[float], referral_pct: Optional[float] = None) -> Dict[str, Any]:
    """Strict standard P&L: no coupons, referral fee (Keepa's per-item % when known, else 15 %),
    Zoro shipping rule, optional Zoro sales tax. zoro_price already includes any freight surcharge."""
    pct = referral_pct if referral_pct and 0.02 <= referral_pct <= 0.5 else REFERRAL_FEE
    if not sell_price or sell_price <= 0:
        return {"sell": None, "ship": ship_fee(zoro_price), "fee": None, "cost": None, "profit": None, "margin": None, "pct": pct}
    ship = ship_fee(zoro_price)
    fee = round(sell_price * pct, 2)
    tax = round(zoro_price * ZORO_TAX_PCT / 100.0, 2)
    cost = round(zoro_price + ship + fee + tax, 2)
    profit = round(sell_price - cost, 2)
    margin = round(profit / sell_price * 100.0, 2)
    return {"sell": sell_price, "ship": ship, "fee": fee, "cost": cost, "profit": profit, "margin": margin, "pct": pct, "tax": tax}


def is_our_store(name: str) -> bool:
    n = (name or "").lower()
    return any(w in n for w in OUR_STORE_WORDS)


def is_amazon_retail(name: str) -> bool:
    n = (name or "").strip().lower()
    if not re.search(r"\bamazon(\.com)?\b", n):
        return False
    return not any(x in n for x in ("resale", "warehouse", "renewed", "outlet"))



def ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def http_json(url: str, timeout: int = 30) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, context=ssl_ctx(), timeout=timeout) as resp:
        raw = resp.read()
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
        return json.loads(raw.decode("utf-8"))


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------------------
# Hunter state DB
# --------------------------------------------------------------------------------------
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(HUNTER_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    conn = db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);

        CREATE TABLE IF NOT EXISTS candidates (
            asin TEXT PRIMARY KEY,
            source TEXT,
            brand TEXT, title TEXT, mpn TEXT, codes TEXT,
            monthly_sold INTEGER DEFAULT 0, sales_rank INTEGER DEFAULT 0,
            amz_items INTEGER DEFAULT 1,
            keepa_amazon_price REAL, keepa_new_price REAL, keepa_bb_seller TEXT,
            zoro_no TEXT, zoro_mpn TEXT, zoro_title TEXT, zoro_price REAL, zoro_pack INTEGER, zoro_surcharge REAL DEFAULT 0, referral_pct REAL,
            zoro_category TEXT, zoro_image TEXT, match_method TEXT, amz_size TEXT, sibling_asins TEXT, siblings_queued INTEGER DEFAULT 0,
            stage TEXT DEFAULT 'NEW',          -- NEW → READY → DONE
            verdict TEXT,                     -- WINNER / ALREADY_LISTED / AMAZON_ON_LISTING / NO_ZORO_MATCH / PACK_MISMATCH / UNPROFITABLE_KEEPA / UNPROFITABLE_LIVE / NO_OFFERS / OUT_OF_STOCK
            reason TEXT,
            keepa_margin REAL,
            live_price REAL, live_seller TEXT, live_profit REAL, live_margin REAL,
            competitors TEXT, offers_n INTEGER, scrape_tries INTEGER DEFAULT 0, pack_uncertain INTEGER DEFAULT 0,
            discovered_at TEXT, evaluated_at TEXT, verified_at TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_cand_stage ON candidates(stage);
        CREATE INDEX IF NOT EXISTS ix_cand_verdict ON candidates(verdict);

        CREATE TABLE IF NOT EXISTS zoro_queue (
            upc_key TEXT PRIMARY KEY,
            zoro_no TEXT, brand TEXT, price REAL, category TEXT, tier INTEGER,
            status TEXT DEFAULT 'PENDING',    -- PENDING / QUERIED / NO_AMAZON / ERROR
            queued_at TEXT, queried_at TEXT, asins TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_zq_status ON zoro_queue(status, tier);

        CREATE TABLE IF NOT EXISTS zoro_index (
            upc_key TEXT, mpn_key TEXT, brand_key TEXT,
            zoro_no TEXT, mpn TEXT, brand TEXT, title TEXT, price REAL, uom TEXT,
            status TEXT, category TEXT, image TEXT, is_ltl TEXT, surcharge REAL DEFAULT 0, moq INTEGER DEFAULT 1,
            dims_key TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_zi_upc ON zoro_index(upc_key);
        CREATE INDEX IF NOT EXISTS ix_zi_mpn ON zoro_index(mpn_key);

        CREATE TABLE IF NOT EXISTS ingested_files (
            path TEXT PRIMARY KEY, mtime REAL, rows INTEGER, added INTEGER, ingested_at TEXT
        );
        CREATE TABLE IF NOT EXISTS brand_queue (
            brand TEXT PRIMARY KEY, brand_key TEXT, zoro_items INTEGER, zoro_items_50 INTEGER, tier INTEGER,
            top_category TEXT,
            status TEXT DEFAULT 'PENDING',    -- PENDING / QUERIED / EMPTY / DONE (hunted before) / ERROR
            asins_found INTEGER DEFAULT 0, asins_fetched INTEGER DEFAULT 0, winners INTEGER DEFAULT 0,
            queued_at TEXT, queried_at TEXT, note TEXT
        );
        CREATE TABLE IF NOT EXISTS brand_asins (
            asin TEXT PRIMARY KEY, brand TEXT, rank INTEGER, tier INTEGER,
            status TEXT DEFAULT 'PENDING',    -- PENDING / FETCHED / MISSING
            found_at TEXT, fetched_at TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_ba_status ON brand_asins(status, tier, rank);
        CREATE TABLE IF NOT EXISTS brand_score (
            brand_key TEXT PRIMARY KEY, queried INTEGER DEFAULT 0, hits INTEGER DEFAULT 0, winners INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS keepa_calls (
            at TEXT, codes INTEGER, products INTEGER, tokens_consumed INTEGER, tokens_left INTEGER, note TEXT
        );
    """)
    have = {r[1] for r in conn.execute("PRAGMA table_info(candidates)")}
    for col, typ in (("zoro_surcharge", "REAL DEFAULT 0"), ("referral_pct", "REAL"),
                     ("amz_size", "TEXT"), ("sibling_asins", "TEXT"), ("siblings_queued", "INTEGER DEFAULT 0")):
        if col not in have:
            conn.execute(f"ALTER TABLE candidates ADD COLUMN {col} {typ}")
    have = {r[1] for r in conn.execute("PRAGMA table_info(zoro_index)")}
    if "dims_key" not in have:
        conn.execute("DROP TABLE zoro_index")
        conn.execute("""CREATE TABLE zoro_index (upc_key TEXT, mpn_key TEXT, brand_key TEXT, zoro_no TEXT, mpn TEXT, brand TEXT,
                        title TEXT, price REAL, uom TEXT, status TEXT, category TEXT, image TEXT, is_ltl TEXT,
                        surcharge REAL DEFAULT 0, moq INTEGER DEFAULT 1, dims_key TEXT)""")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_zi_upc ON zoro_index(upc_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_zi_mpn ON zoro_index(mpn_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_zi_dims ON zoro_index(brand_key, dims_key)")
        conn.execute("DELETE FROM kv WHERE k='zoro_index_sig'")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_zi_dims ON zoro_index(brand_key, dims_key)")
    conn.commit()
    conn.close()


def kv_get(conn: sqlite3.Connection, k: str, default: str = "") -> str:
    row = conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return row[0] if row else default


def kv_set(conn: sqlite3.Connection, k: str, v: str) -> None:
    conn.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


# --------------------------------------------------------------------------------------
# 1. Our stores' ASINs (already hunted → skip)
# --------------------------------------------------------------------------------------
_store_asins: Set[str] = set()
_store_sig = ""


def load_store_asins(force: bool = False) -> Set[str]:
    global _store_asins, _store_sig
    files = []
    for g in STORE_REPORT_GLOBS:
        files.extend(glob.glob(g))
    files = sorted(set(files))
    sig = "|".join(f"{f}:{int(os.path.getmtime(f))}" for f in files)
    if not force and sig == _store_sig and _store_asins:
        return _store_asins
    asins: Set[str] = set()
    for f in files:
        try:
            df = pd.read_csv(f, sep="\t" if not f.endswith(".csv") else ",", dtype=str,
                             on_bad_lines="skip", low_memory=False, encoding="utf-8", encoding_errors="ignore")
        except Exception as e:
            log(f"[stores] could not read {os.path.basename(f)}: {e}")
            continue
        df.columns = [str(c).strip().lstrip("﻿").lower() for c in df.columns]
        for col in ("asin1", "asin2", "asin3", "asin"):
            if col in df.columns:
                asins.update(a.strip().upper() for a in df[col].dropna().astype(str) if re.fullmatch(r"B0[0-9A-Z]{8}", a.strip().upper()))
        if "product-id" in df.columns:
            asins.update(a.strip().upper() for a in df["product-id"].dropna().astype(str) if re.fullmatch(r"B0[0-9A-Z]{8}", a.strip().upper()))
    _store_asins, _store_sig = asins, sig
    if not asins:
        log("[stores] WARNING: no All Listings report found — nothing will be skipped as already listed. "
            "Put the HOANK and Hathoro All Listings reports in Amazon_DS_Hunting/store_listings/ or Automations/data/reports/")
    else:
        log(f"[stores] {len(asins)} ASINs already listed on HOANK/Hathoro (from {len(files)} report files)")
    return asins


# --------------------------------------------------------------------------------------
# 2. Zoro lookup index (built once from combined_reports.db, rebuilt when it changes)
# --------------------------------------------------------------------------------------
def ensure_zoro_index(conn: sqlite3.Connection) -> None:
    if not os.path.exists(ZORO_DB):
        log(f"[zoro] catalog DB not found at {ZORO_DB}")
        return
    sig = f"{int(os.path.getmtime(ZORO_DB))}:{os.path.getsize(ZORO_DB)}"
    if kv_get(conn, "zoro_index_sig") == sig and conn.execute("SELECT 1 FROM zoro_index LIMIT 1").fetchone():
        return
    log("[zoro] building UPC/MPN lookup index from the 2M-row Zoro catalog (one-time, ~1–2 min)…")
    t0 = time.time()
    conn.execute("DELETE FROM zoro_index")
    conn.commit()
    src = sqlite3.connect(f"file:{ZORO_DB}?mode=ro", uri=True)
    cur = src.execute("""
        SELECT zoro_no, manufacturer_part_number, upc_code, zoro_brand_name, short_description,
               sales_price, uom, availabilityStatus, level_one_name, zoro_image_name, is_ltl,
               freight_surcharge, min_order_qty
        FROM zoro_data
        WHERE sales_price > 0 AND (upc_code IS NOT NULL OR manufacturer_part_number IS NOT NULL)
    """)
    batch, n = [], 0
    while True:
        rows = cur.fetchmany(50_000)
        if not rows:
            break
        for r in rows:
            z_no, mpn, upc, brand, title, price, uom, status, cat, img, ltl, surcharge, moq = r
            mk, bk = norm_mpn(mpn), norm_brand(brand)
            dk = dims_key(title)
            keys = codes_in(upc) or [""]
            for uk in keys:
                batch.append((uk, mk, bk, z_no, mpn or "", brand or "", title or "", float(price or 0),
                              uom or "EA", status or "", cat or "", img or "", str(ltl or ""),
                              float(surcharge or 0), max(1, to_int(moq, 1)), dk))
        conn.executemany("INSERT INTO zoro_index VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
        conn.commit()
        n += len(batch)
        batch = []
    src.close()
    kv_set(conn, "zoro_index_sig", sig)
    conn.commit()
    log(f"[zoro] index ready: {n:,} rows in {time.time() - t0:.0f}s")


def zoro_lookup(conn: sqlite3.Connection, codes: List[str], mpn: str, brand: str,
                title: str = "", size_attr: str = "") -> Tuple[Optional[sqlite3.Row], str]:
    """Best Zoro row for a set of product codes (preferred), then brand+MPN, then brand+exact size.

    The size route exists because Amazon splits one product family into a child listing per size while
    Zoro gives each size its own SKU with the size in the title ("Chair Mat,36 x 48 in"). Codes often
    only exist for some sizes. It requires a brand match AND identical dimensions AND overlapping
    product words, so a 36x48 chair mat can never match a 36x48 sign."""
    order = """ORDER BY CASE WHEN status='In Stock' THEN 0 WHEN status='Limited Stock' THEN 1
                              WHEN status='Drop Ship' THEN 2 WHEN status='' THEN 3 ELSE 4 END, price ASC"""
    codes = [c for c in codes if c]
    if codes:
        q = f"SELECT * FROM zoro_index WHERE upc_key IN ({','.join('?' * len(codes))}) AND is_ltl != 'True' AND moq <= 1 {order} LIMIT 1"
        row = conn.execute(q, codes).fetchone()
        if row:
            return row, "UPC"
    mk, bk = norm_mpn(mpn), norm_brand(brand)
    if mk and len(mk) >= 3:
        rows = conn.execute(f"SELECT * FROM zoro_index WHERE mpn_key = ? AND is_ltl != 'True' AND moq <= 1 {order} LIMIT 25", (mk,)).fetchall()
        for r in rows:
            zb = r["brand_key"]
            if bk and zb and (bk in zb or zb in bk or bk[:5] == zb[:5]):
                return r, "MPN+BRAND"

    dk = dims_key(title) or dims_key(size_attr)
    if dk and bk and len(bk) >= 3:
        rows = conn.execute(f"""SELECT * FROM zoro_index WHERE brand_key = ? AND dims_key = ?
                                AND is_ltl != 'True' AND moq <= 1 {order} LIMIT 25""", (bk, dk)).fetchall()
        amz_words = title_tokens(title)
        for r in rows:
            if len(amz_words & title_tokens(r["title"])) >= 2:
                return r, "BRAND+SIZE"
    return None, "NONE"


# --------------------------------------------------------------------------------------
# 3. Keepa export files dropped in keepa_inbox/  (0 tokens)
# --------------------------------------------------------------------------------------
def _find_col(cols: List[str], *needles: str, exclude: Tuple[str, ...] = ()) -> Optional[str]:
    low = {c: c.lower() for c in cols}
    for c, l in low.items():
        if all(n.lower() in l for n in needles) and not any(x.lower() in l for x in exclude):
            return c
    return None


def read_keepa_file(path: str) -> Tuple[pd.DataFrame, Dict[str, Optional[str]]]:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm", ".xls"):
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path, sep=None, engine="python", dtype=str, encoding="utf-8", encoding_errors="ignore")
    df.columns = [str(c).strip() for c in df.columns]
    cols = list(df.columns)
    asin_col = next((c for c in cols if c.strip().upper() == "ASIN"), None) or _find_col(cols, "asin", exclude=("parent",))
    m = {
        "asin": asin_col,
        "title": next((c for c in cols if c.strip().lower() == "title"), None) or _find_col(cols, "title", exclude=("parent", "store")),
        "brand": next((c for c in cols if c.strip().lower() == "brand"), None) or _find_col(cols, "brand", exclude=("store",)),
        "upc": _find_col(cols, "product codes", "upc") or _find_col(cols, "upc"),
        "ean": _find_col(cols, "product codes", "ean") or _find_col(cols, "ean"),
        "gtin": _find_col(cols, "product codes", "gtin"),
        "mpn": _find_col(cols, "product codes", "partnumber") or _find_col(cols, "part number") or _find_col(cols, "model"),
        "sold": _find_col(cols, "bought in past month") or _find_col(cols, "monthly sold") or _find_col(cols, "monthlysold"),
        "rank": _find_col(cols, "sales rank", "current"),
        "bb_seller": _find_col(cols, "buy box seller") or _find_col(cols, "buy box", "seller"),
        "items": _find_col(cols, "number of items") or _find_col(cols, "package", "quantity") or _find_col(cols, "item", "quantity"),
        "bb_price": _find_col(cols, "buy box", "current", exclude=("seller", "is ", "%", "used", "stock")),
        "new_price": next((c for c in cols if c.strip().lower() in ("new: current", "new current")), None) or _find_col(cols, "new, 3rd party fbm", "current") or _find_col(cols, "new", "current", exclude=("count", "offer", "used", "fba", "%")),
        "amz_price": _find_col(cols, "amazon", "current", exclude=("url", "rank", "%", "count")),
        "ref_pct": _find_col(cols, "referral fee"),
    }
    return df, m


def ingest_inbox(conn: sqlite3.Connection) -> int:
    files = [f for f in glob.glob(os.path.join(INBOX_DIR, "*")) if os.path.splitext(f)[1].lower() in (".xlsx", ".xlsm", ".xls", ".csv", ".tsv", ".txt")]
    added_total = 0
    for f in sorted(files):
        if os.path.basename(f).startswith("~$"):
            continue
        mtime = os.path.getmtime(f)
        prev = conn.execute("SELECT mtime FROM ingested_files WHERE path=?", (f,)).fetchone()
        if prev and abs(prev[0] - mtime) < 1:
            continue
        try:
            df, m = read_keepa_file(f)
        except Exception as e:
            log(f"[inbox] cannot read {os.path.basename(f)}: {e} — skipped until the file changes")
            conn.execute("INSERT OR REPLACE INTO ingested_files VALUES (?,?,?,?,?)", (f, mtime, -1, 0, now_iso())); conn.commit()
            continue
        if not m["asin"]:
            log(f"[inbox] {os.path.basename(f)}: no ASIN column, skipped until the file changes. Columns: {list(df.columns)[:12]}")
            conn.execute("INSERT OR REPLACE INTO ingested_files VALUES (?,?,?,?,?)", (f, mtime, -1, 0, now_iso())); conn.commit()
            continue
        log(f"[inbox] {os.path.basename(f)}: {len(df)} rows; columns → " +
            ", ".join(f"{k}={m[k]!r}" for k in ("upc", "mpn", "sold", "bb_seller", "new_price", "amz_price") if m[k]))
        added = 0
        src = f"keepa_file:{os.path.basename(f)}"
        for _, r in df.iterrows():
            asin = str(r.get(m["asin"], "")).strip().upper()
            if not re.fullmatch(r"B0[0-9A-Z]{8}", asin):
                continue
            codes: List[str] = []
            for key in ("upc", "ean", "gtin"):
                if m[key]:
                    for c in codes_in(r.get(m[key])):
                        if c not in codes:
                            codes.append(c)
            bb_seller = str(r.get(m["bb_seller"], "") or "") if m["bb_seller"] else ""
            if bb_seller.lower() in ("nan", "-", "none"):
                bb_seller = ""
            rec = {
                "asin": asin, "source": src,
                "brand": str(r.get(m["brand"], "") or "") if m["brand"] else "",
                "title": str(r.get(m["title"], "") or "") if m["title"] else "",
                "mpn": str(r.get(m["mpn"], "") or "") if m["mpn"] else "",
                "codes": ",".join(codes),
                "monthly_sold": to_int(r.get(m["sold"])) if m["sold"] else 0,
                "sales_rank": to_int(r.get(m["rank"])) if m["rank"] else 0,
                "amz_items": max(1, to_int(r.get(m["items"]), 1)) if m["items"] else 0,
                "keepa_amazon_price": money(r.get(m["amz_price"])) if m["amz_price"] else None,
                "keepa_new_price": (money(r.get(m["new_price"])) if m["new_price"] else None) or (money(r.get(m["bb_price"])) if m["bb_price"] else None),
                "keepa_bb_seller": bb_seller,
                "referral_pct": None,
            }
            if m["ref_pct"]:
                rp = money(r.get(m["ref_pct"]))
                if rp:
                    rec["referral_pct"] = rp / 100.0 if rp > 1 else rp
            for k in ("brand", "title", "mpn"):
                if rec[k].lower() == "nan":
                    rec[k] = ""
            added += upsert_candidate(conn, rec)
        conn.execute("INSERT OR REPLACE INTO ingested_files VALUES (?,?,?,?,?)", (f, mtime, len(df), added, now_iso()))
        conn.commit()
        added_total += added
        log(f"[inbox] {os.path.basename(f)}: {added} new candidates queued")
    return added_total


MIN_SOLD = 0  # set from --min-sold in main(); used when a later file upgrades a candidate's demand figure


def upsert_candidate(conn: sqlite3.Connection, rec: Dict[str, Any]) -> int:
    """Insert a new candidate; returns 1 if new. Existing rows keep their verdict, except that a
    higher monthly-sold figure (e.g. from a Keepa export) is recorded and, if it lifts the item over
    the demand floor, the item goes back through evaluation."""
    exists = conn.execute("SELECT monthly_sold, verdict, stage, title FROM candidates WHERE asin=?", (rec["asin"],)).fetchone()
    if exists:
        old_sold = exists[0] or 0
        new_sold = rec.get("monthly_sold") or 0
        if new_sold > old_sold:
            requeue = MIN_SOLD and old_sold < MIN_SOLD <= new_sold and exists[2] == "DONE" and \
                (exists[1] or "") in ("LOW_DEMAND", "WINNER", "UNPROFITABLE_LIVE", "UNPROFITABLE_KEEPA", "NO_OFFERS")
            if requeue:
                conn.execute("UPDATE candidates SET monthly_sold=?, stage='NEW', verdict=NULL, reason=NULL, scrape_tries=0 WHERE asin=?", (new_sold, rec["asin"]))
                log(f"[inbox] {rec['asin']}: demand updated {old_sold} → {new_sold}/month, re-queued")
            else:
                conn.execute("UPDATE candidates SET monthly_sold=? WHERE asin=?", (new_sold, rec["asin"]))
        if (rec.get("title") or "").strip() and not (exists[3] or "").strip():
            conn.execute("UPDATE candidates SET title=? WHERE asin=?", (rec["title"], rec["asin"]))
        return 0
    conn.execute("""
        INSERT INTO candidates (asin, source, brand, title, mpn, codes, monthly_sold, sales_rank, amz_items,
                                keepa_amazon_price, keepa_new_price, keepa_bb_seller, referral_pct,
                                amz_size, sibling_asins, stage, discovered_at)
        VALUES (:asin, :source, :brand, :title, :mpn, :codes, :monthly_sold, :sales_rank, :amz_items,
                :keepa_amazon_price, :keepa_new_price, :keepa_bb_seller, :referral_pct,
                :amz_size, :sibling_asins, 'NEW', :now)
    """, {**rec, "referral_pct": rec.get("referral_pct"), "amz_size": rec.get("amz_size", ""),
          "sibling_asins": rec.get("sibling_asins", ""), "now": now_iso()})
    return 1


# --------------------------------------------------------------------------------------
# 4. Reverse mining: Zoro catalog → Keepa API (token governed)
# --------------------------------------------------------------------------------------
def keepa_key() -> str:
    k = os.environ.get("KEEPA_API_KEY", "").strip()
    if k:
        return k
    try:
        with open(KEEPA_KEY_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def keepa_tokens(key: str) -> Dict[str, Any]:
    try:
        d = http_json(f"https://api.keepa.com/token?key={key}", timeout=10)
        return {"left": int(d.get("tokensLeft", 0)), "refill_sec": d.get("refillIn", 0) / 1000.0, "rate": d.get("refillRate", 1), "ok": True}
    except Exception as e:
        return {"left": 0, "refill_sec": 60, "rate": 1, "ok": False, "err": str(e)}


def seed_zoro_queue(conn: sqlite3.Connection, min_price: float, max_price: float, want: int = 3000) -> int:
    pending = conn.execute("SELECT COUNT(*) FROM zoro_queue WHERE status='PENDING'").fetchone()[0]
    if pending >= want:
        return 0
    if not os.path.exists(ZORO_DB):
        return 0
    src = sqlite3.connect(f"file:{ZORO_DB}?mode=ro", uri=True)
    cur = src.execute("""
        SELECT zoro_no, upc_code, zoro_brand_name, sales_price, level_one_name
        FROM zoro_data
        WHERE availabilityStatus = 'In Stock' AND sales_price BETWEEN ? AND ?
          AND upc_code IS NOT NULL AND LENGTH(upc_code) >= 10
          AND (is_ltl IS NULL OR is_ltl != 'True')
          AND (is_dropship IS NULL OR is_dropship != 'YES')
        ORDER BY RANDOM() LIMIT ?
    """, (min_price, max_price, want * 4))
    added = 0
    for z_no, upc, brand, price, cat in cur:
        tier = CATEGORY_TIER.get(cat or "", 2)
        for k in codes_in(upc):
            if len(k) < 8:
                continue
            r = conn.execute("INSERT OR IGNORE INTO zoro_queue (upc_key, zoro_no, brand, price, category, tier, queued_at) VALUES (?,?,?,?,?,?,?)",
                             (k, z_no, brand or "", float(price or 0), cat or "", tier, now_iso()))
            added += r.rowcount
        if added >= want:
            break
    src.close()
    conn.commit()
    if added:
        log(f"[mine] queued {added} Zoro UPCs (${min_price:.0f}–${max_price:.0f}, in stock, niche categories first)")
    return added


def candidate_from_keepa(p: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
    """Normalise one Keepa product object (with stats) into a candidate record."""
    asin = str(p.get("asin", "")).upper()
    if not re.fullmatch(r"B0[0-9A-Z]{8}", asin):
        return None
    codes: List[str] = []
    for c in (p.get("upcList") or []) + (p.get("eanList") or []):
        k = norm_code(c)
        if k and k not in codes:
            codes.append(k)
    stats = p.get("stats") or {}
    cur = stats.get("current") or []

    def cents(i: int) -> Optional[float]:
        try:
            v = cur[i]
            return round(v / 100.0, 2) if v and v > 0 else None
        except (IndexError, TypeError):
            return None

    amazon_price, new_price, fbm = cents(0), cents(1), cents(7)
    bb = None
    try:
        bbv = stats.get("buyBoxPrice")
        bb = round(bbv / 100.0, 2) if bbv and bbv > 0 else None
    except Exception:
        pass
    bb_seller = str(stats.get("buyBoxSellerId") or "")
    pack_qty = p.get("packageQuantity") or p.get("numberOfItems") or 0
    prices = [x for x in (new_price, fbm, bb) if x]
    siblings, own_size = [], ""
    for v in (p.get("variations") or []):
        v_asin = str(v.get("asin") or "").upper()
        attrs = " ".join(str(a.get("value") or "") for a in (v.get("attributes") or [])
                         if "size" in str(a.get("dimension") or "").lower())
        if v_asin == asin:
            own_size = attrs
        elif re.fullmatch(r"B0[0-9A-Z]{8}", v_asin):
            siblings.append(v_asin)
    return {
        "asin": asin, "source": source,
        "brand": p.get("brand") or "", "title": p.get("title") or "",
        "mpn": p.get("partNumber") or p.get("model") or "",
        "codes": ",".join(codes),
        "monthly_sold": to_int(p.get("monthlySold")),
        "sales_rank": to_int(cur[3]) if len(cur) > 3 and cur[3] and cur[3] > 0 else 0,
        "amz_items": max(1, to_int(pack_qty, 1)) if pack_qty else 0,
        "keepa_amazon_price": amazon_price,
        "keepa_new_price": min(prices) if prices else None,
        "keepa_bb_seller": "Amazon" if bb_seller == AMAZON_SELLER_ID else bb_seller,
        "amz_size": own_size, "sibling_asins": ",".join(siblings[:40]),
    }


def keepa_mine(conn: sqlite3.Connection, key: str, batch: int, reserve: int = 8) -> int:
    """One Keepa product lookup for the next batch of Zoro UPCs. Returns number of candidates added."""
    tok = keepa_tokens(key)
    if not tok["ok"]:
        log(f"[keepa] token check failed: {tok.get('err')}")
        return 0
    spare = tok["left"] - reserve
    if spare < 5:
        log(f"[keepa] {tok['left']} tokens left (reserve {reserve}) — waiting for refill, using inbox files + scraping only")
        return 0
    n = min(batch, spare)
    # Brands that have produced Amazon hits/winners before are mined first (within a category tier).
    rows = conn.execute("""
        SELECT q.* FROM zoro_queue q
        LEFT JOIN brand_score b ON b.brand_key = UPPER(REPLACE(REPLACE(q.brand,' ',''),'-',''))
        WHERE q.status='PENDING'
        ORDER BY q.tier ASC,
                 CASE WHEN b.queried IS NULL OR b.queried < 5 THEN 0.5
                      ELSE (b.hits + 3.0 * b.winners) * 1.0 / b.queried END DESC,
                 RANDOM()
        LIMIT ?""", (n,)).fetchall()
    if not rows:
        return 0
    code_map = {r["upc_key"]: r for r in rows}
    # Keepa wants the code as printed (UPC-A 12 digits / EAN 13). Send both spellings that matter.
    send = []
    for k in code_map:
        send.append(k.zfill(12) if len(k) <= 12 else k.zfill(13))
    url = (f"https://api.keepa.com/product?key={key}&domain=1&code={','.join(send)}&stats=30&history=0")
    try:
        data = http_json(url, timeout=40)
    except Exception as e:
        log(f"[keepa] request failed: {e}")
        conn.execute("INSERT INTO keepa_calls VALUES (?,?,?,?,?,?)", (now_iso(), len(send), 0, 0, tok["left"], f"error {e}"[:200]))
        conn.commit()
        if "429" in str(e):
            time.sleep(30)
        return 0
    products = data.get("products", []) or []
    consumed = data.get("tokensConsumed", 0)
    left = data.get("tokensLeft", tok["left"])
    conn.execute("INSERT INTO keepa_calls VALUES (?,?,?,?,?,?)", (now_iso(), len(send), len(products), consumed, left, ""))
    hit_keys: Dict[str, List[str]] = {}
    added = 0
    for p in products:
        rec = candidate_from_keepa(p, "keepa_api:zoro_reverse")
        if not rec:
            continue
        for k in rec["codes"].split(","):
            if k in code_map:
                hit_keys.setdefault(k, []).append(rec["asin"])
        added += upsert_candidate(conn, rec)
    for k, r in code_map.items():
        st = "QUERIED" if k in hit_keys else "NO_AMAZON"
        conn.execute("UPDATE zoro_queue SET status=?, queried_at=?, asins=? WHERE upc_key=?",
                     (st, now_iso(), ",".join(hit_keys.get(k, [])), k))
        bk = norm_brand(r["brand"])
        if bk:
            conn.execute("""INSERT INTO brand_score (brand_key, queried, hits) VALUES (?, 1, ?)
                            ON CONFLICT(brand_key) DO UPDATE SET queried = queried + 1, hits = hits + excluded.hits""",
                         (bk, 1 if st == "QUERIED" else 0))
    conn.commit()
    log(f"[keepa] {len(send)} UPCs → {len(products)} Amazon products, {added} new candidates | tokens used {consumed}, left {left}")
    return added


# --------------------------------------------------------------------------------------
# 4b. Brand leg: Zoro brands → Keepa Product Finder → product codes → Zoro match
# --------------------------------------------------------------------------------------
def hunted_before_brand_keys() -> Set[str]:
    """Brands already covered by an old *_DS_Hunting_Results.xlsx or a file in keepa_inbox/."""
    keys: Set[str] = set()
    for f in glob.glob(os.path.join(CURRENT_DIR, "*_DS_Hunting_Results.xlsx")):
        name = os.path.basename(f).split("_DS_Hunting")[0]
        if "multiple" in name.lower() or "hearthshire" in name.lower():
            continue
        keys.add(norm_brand(name))
    for f in glob.glob(os.path.join(INBOX_DIR, "*")):
        keys.add(norm_brand(os.path.splitext(os.path.basename(f))[0]))
    return {k for k in keys if k}


def seed_brand_queue(conn: sqlite3.Connection, min_price: float) -> int:
    """Rank Zoro brands by in-stock ≥ $min_price stock in niche categories; skip brands hunted before."""
    if not os.path.exists(ZORO_DB):
        return 0
    sig = kv_get(conn, "zoro_index_sig")
    if kv_get(conn, "brand_queue_sig") == sig and conn.execute("SELECT 1 FROM brand_queue LIMIT 1").fetchone():
        return 0
    src = sqlite3.connect(f"file:{ZORO_DB}?mode=ro", uri=True)
    rows = src.execute("""
        SELECT zoro_brand_name, level_one_name, COUNT(*) c, SUM(CASE WHEN sales_price >= ? THEN 1 ELSE 0 END) c50
        FROM zoro_data
        WHERE availabilityStatus = 'In Stock' AND zoro_brand_name IS NOT NULL AND LENGTH(zoro_brand_name) > 1
          AND (is_ltl IS NULL OR is_ltl != 'True')
        GROUP BY 1, 2
    """, (min_price,)).fetchall()
    src.close()
    src2 = sqlite3.connect(f"file:{HUNTER_DB}?mode=ro", uri=True)
    size_families: Dict[str, int] = {}
    try:
        for b, n in src2.execute("""SELECT brand_key, COUNT(DISTINCT dims_key) FROM zoro_index
                                    WHERE dims_key!='' AND brand_key!='' AND status='In Stock' AND price>=50 AND moq<=1
                                    GROUP BY brand_key"""):
            size_families[b] = n
    except sqlite3.Error:
        pass
    src2.close()
    agg: Dict[str, Dict[str, Any]] = {}
    for brand, cat, c, c50 in rows:
        a = agg.setdefault(brand, {"items": 0, "items50": 0, "cats": {}})
        a["items"] += c
        a["items50"] += c50 or 0
        a["cats"][cat or ""] = a["cats"].get(cat or "", 0) + c
    done = hunted_before_brand_keys()
    added = 0
    for brand, a in agg.items():
        if a["items50"] < 15:
            continue
        top_cat = max(a["cats"], key=a["cats"].get)
        tier = CATEGORY_TIER.get(top_cat, 2)
        bk = norm_brand(brand)
        # A brand selling the same product in many sizes maps onto many Amazon child listings at once.
        if size_families.get(bk, 0) >= 8:
            tier = max(1, tier - 1)
        status = "DONE" if bk in done else "PENDING"
        r = conn.execute("""INSERT INTO brand_queue (brand, brand_key, zoro_items, zoro_items_50, tier, top_category, status, queued_at, note)
                            VALUES (?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(brand) DO UPDATE SET zoro_items=excluded.zoro_items, zoro_items_50=excluded.zoro_items_50,
                                tier=excluded.tier, top_category=excluded.top_category""",
                         (brand, bk, a["items"], a["items50"], tier, top_cat, status, now_iso(), "hunted before" if status == "DONE" else ""))
        added += r.rowcount
    kv_set(conn, "brand_queue_sig", sig)
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM brand_queue WHERE status='PENDING'").fetchone()[0]
    log(f"[brands] brand queue ready: {n} Zoro brands pending (≥15 in-stock items ≥ ${min_price:.0f}), tier-1 categories first")
    return added


def import_cached_brand_lists(conn: sqlite3.Connection) -> None:
    """Old .keepa_cache/<brand>_100.json Product Finder results are free ASIN lists — queue them once."""
    if kv_get(conn, "cached_brand_lists_imported") == "1":
        return
    n = 0
    for f in glob.glob(os.path.join(KEEPA_CACHE_DIR, "*_100.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:
            continue
        brand = str(d.get("brand") or "").upper()
        asins = d.get("asins") or []
        if not brand or not asins:
            continue
        tier = 3 if norm_brand(brand) in {"3M", "RUSTOLEUM"} else 2
        for i, a in enumerate(asins):
            r = conn.execute("INSERT OR IGNORE INTO brand_asins (asin, brand, rank, tier, status, found_at) VALUES (?,?,?,?,'PENDING',?)",
                             (a, brand, i, tier, now_iso()))
            n += r.rowcount
        conn.execute("""INSERT INTO brand_queue (brand, brand_key, zoro_items, zoro_items_50, tier, top_category, status, asins_found, queued_at, queried_at, note)
                        VALUES (?,?,0,0,?,?,'QUERIED',?,?,?,'from .keepa_cache')
                        ON CONFLICT(brand) DO UPDATE SET status='QUERIED', asins_found=excluded.asins_found, queried_at=excluded.queried_at""",
                     (brand, norm_brand(brand), tier, "", len(asins), now_iso(), now_iso()))
    kv_set(conn, "cached_brand_lists_imported", "1")
    conn.commit()
    if n:
        log(f"[brands] imported {n} ASINs from earlier Keepa brand queries (free)")


def keepa_brand_query(conn: sqlite3.Connection, key: str, monthly_sold_gte: int, reserve: int,
                      tokens_left: int, batch_brands: int = 15) -> int:
    """One Keepa Product Finder query covering several pending Zoro brands at once.

    Product Finder accepts a list of brands and costs the same whether it carries one brand or twenty,
    so querying them in batches turns ~10 tokens per brand into ~10 tokens per fifteen brands."""
    if tokens_left - reserve < 12:
        return 0
    head = conn.execute("SELECT * FROM brand_queue WHERE status='PENDING' ORDER BY tier ASC, zoro_items_50 DESC LIMIT 1").fetchone()
    if not head:
        return 0
    # Batch brands that sell into the same Amazon categories, so one rootCategory filter fits them all.
    roots = amz_roots(head["top_category"])
    same = [c for c, r in AMZ_ROOTS.items() if r == roots] or [head["top_category"] or ""]
    bs = conn.execute(f"""SELECT * FROM brand_queue WHERE status='PENDING' AND COALESCE(top_category,'') IN ({','.join('?' * len(same))})
                          ORDER BY tier ASC, zoro_items_50 DESC LIMIT ?""", same + [batch_brands]).fetchall() or [head]
    b = bs[0]
    brand = b["brand"]
    # Keepa matches the brand string as Amazon spells it; try the full names, then bare first words.
    names = [x["brand"].lower().strip() for x in bs]
    firsts = []
    for x in bs:
        f = re.split(r"[\s/,-]+", x["brand"].strip())[0].lower()
        if f not in names and len(f) >= 4 and f not in ("the",):
            firsts.append(f)
    variants = [names, firsts] if firsts else [names]
    found: List[str] = []
    used_variant = ",".join(names)[:60]
    consumed_total = 0
    for v in variants:
        # availabilityAmazon -1 = Amazon itself has no offer on the listing. Filtering here is free;
        # finding out afterwards costs a token per ASIN, and measured 85% of them were Amazon's.
        selection = {"productType": [0], "brand": v, "monthlySold_gte": monthly_sold_gte,
                     "availabilityAmazon": [-1], "buyBoxIsAmazon": False, "rootCategory": roots,
                     "sort": [["monthlySold", "desc"]], "perPage": 300, "page": 0}
        url = f"https://api.keepa.com/query?key={key}&domain=1&selection={urllib.parse.quote(json.dumps(selection))}"
        try:
            data = http_json(url, timeout=40)
        except Exception as e:
            log(f"[brands] Product Finder failed for {brand!r}: {str(e)[:120]}")
            conn.execute("UPDATE brand_queue SET status='ERROR', note=?, queried_at=? WHERE brand=?", (str(e)[:200], now_iso(), brand))
            conn.execute("INSERT INTO keepa_calls VALUES (?,?,?,?,?,?)", (now_iso(), 0, 0, 0, tokens_left, f"brand query error {brand}"[:200]))
            conn.commit()
            return 0
        found = [a for a in (data.get("asinList") or []) if re.fullmatch(r"B0[0-9A-Z]{8}", str(a))]
        consumed_total += data.get("tokensConsumed", 0) or 0
        tokens_left = data.get("tokensLeft", tokens_left)
        conn.execute("INSERT INTO keepa_calls VALUES (?,?,?,?,?,?)",
                     (now_iso(), 0, len(found), data.get("tokensConsumed", 0), tokens_left, f"brand query {v!r} → {len(found)} ASINs"))
        used_variant = ",".join(v)[:60]
        if found:
            break
        if tokens_left - reserve < 12:
            break
    for i, a in enumerate(found):
        # tier 0-3 = came from a query that already filtered demand, Amazon and category.
        # Anything left at tier 9 predates those filters and is only a last resort.
        conn.execute("""INSERT INTO brand_asins (asin, brand, rank, tier, status, found_at) VALUES (?,?,?,?,'PENDING',?)
                        ON CONFLICT(asin) DO UPDATE SET tier=excluded.tier, rank=excluded.rank
                        WHERE brand_asins.status='PENDING'""",
                     (a, brand, i, b["tier"], now_iso()))
    note = f"keepa brands {used_variant!r}, monthlySold≥{monthly_sold_gte}"
    for x in bs:
        conn.execute("UPDATE brand_queue SET status=?, asins_found=?, queried_at=?, note=? WHERE brand=?",
                     ("QUERIED" if found else "EMPTY", len(found) if x["brand"] == brand else 0, now_iso(), note, x["brand"]))
    conn.commit()
    log(f"[brands] {len(bs)} {b['top_category'] or 'misc'} brands ({brand}, tier {b['tier']}, +{len(bs)-1} more) → "
        f"{len(found)} ASINs selling {monthly_sold_gte}+/mo, no Amazon offer, right category "
        f"| tokens used {consumed_total}, left {tokens_left}")
    return len(found)


def keepa_fetch_brand_asins(conn: sqlite3.Connection, key: str, max_asins: int, reserve: int, tokens_left: int) -> int:
    """Pull product codes/stats for queued brand ASINs (1 token each). Returns new candidates."""
    spare = tokens_left - reserve
    n = min(max_asins, spare)
    if n < 5:
        return 0
    # tier 9 holds ASINs queued before the demand/Amazon/category filters existed. Measured ~85% of
    # them are Amazon's, so a token spent there is a token not spent on a pre-qualified one.
    rows = conn.execute("""SELECT asin, brand FROM brand_asins WHERE status='PENDING' AND tier < 9
                           AND asin NOT IN (SELECT asin FROM candidates)
                           ORDER BY tier ASC, rank ASC LIMIT ?""", (n,)).fetchall()
    # ASINs we already know about need no tokens
    conn.execute("UPDATE brand_asins SET status='FETCHED', fetched_at=? WHERE status='PENDING' AND asin IN (SELECT asin FROM candidates)", (now_iso(),))
    if not rows:
        conn.commit()
        return 0
    added = 0
    for i in range(0, len(rows), 20):
        chunk = rows[i:i + 20]
        asins = [r["asin"] for r in chunk]
        url = f"https://api.keepa.com/product?key={key}&domain=1&asin={','.join(asins)}&stats=30&history=0"
        try:
            data = http_json(url, timeout=40)
        except Exception as e:
            log(f"[brands] product fetch failed: {str(e)[:120]}")
            conn.execute("INSERT INTO keepa_calls VALUES (?,?,?,?,?,?)", (now_iso(), len(asins), 0, 0, tokens_left, f"error {e}"[:200]))
            conn.commit()
            if "429" in str(e):
                time.sleep(30)
            break
        products = data.get("products") or []
        seen = set()
        for p in products:
            rec = candidate_from_keepa(p, f"keepa_api:brand:{chunk[0]['brand']}")
            if not rec:
                continue
            seen.add(rec["asin"])
            if not rec["brand"]:
                rec["brand"] = chunk[0]["brand"]
            added += upsert_candidate(conn, rec)
        for a in asins:
            conn.execute("UPDATE brand_asins SET status=?, fetched_at=? WHERE asin=?", ("FETCHED" if a in seen else "MISSING", now_iso(), a))
        conn.execute("UPDATE brand_queue SET asins_fetched = asins_fetched + ? WHERE brand=?", (len(asins), chunk[0]["brand"]))
        tokens_left = data.get("tokensLeft", tokens_left)
        conn.execute("INSERT INTO keepa_calls VALUES (?,?,?,?,?,?)",
                     (now_iso(), len(asins), len(products), data.get("tokensConsumed", 0), tokens_left, f"brand asins {chunk[0]['brand']}"))
        conn.commit()
        if tokens_left - reserve < 5:
            break
    log(f"[brands] fetched {len(rows)} brand ASINs → {added} new candidates | tokens left {tokens_left}")
    return added


def keepa_allocate(conn: sqlite3.Connection, key: str, args) -> Dict[str, int]:
    """Spend this cycle's tokens where they buy the most qualified candidates.

    Product Finder can filter on monthly sales before we pay for anything, so a brand query returns
    ASINs that already clear the demand floor. Looking up a raw Zoro UPC cannot filter, and the
    measured result was that nearly every item it found had no reported demand and was thrown away.
    So UPC mining now only runs once the pre-qualified brand backlog is empty."""
    out = {"brand_asins": 0, "brand_query": 0, "mined": 0}
    reserve = 8
    tok = keepa_tokens(key)
    if not tok["ok"]:
        log(f"[keepa] token check failed: {tok.get('err')}")
        return out
    left = tok["left"]
    log(f"[keepa] {left} tokens available (refill {tok['rate']}/min)")

    # Only freshly-filtered ASINs count as backlog. Tier 9 holds ASINs queued before the Amazon filter
    # existed; they are a last resort and must not block new, pre-qualified queries.
    # A filtered brand query costs ~11 tokens and returns only a handful of ASINs, because very few
    # niche industrial products clear 500 sales a month without Amazon on the listing. So keep querying
    # brands until either the budget or the brand queue runs out, fetching what each query turns up.
    def pending_qualified() -> int:
        return conn.execute("SELECT COUNT(*) FROM brand_asins WHERE status='PENDING' AND tier < 9").fetchone()[0]

    for _ in range(args.brand_queries_per_cycle):
        if left - reserve < 14 or pending_qualified() >= 120:
            break
        got = keepa_brand_query(conn, key, max(args.brand_min_sold, args.min_sold), reserve, left)
        left = keepa_tokens(key)["left"]
        out["brand_query"] += got
        if got == 0 and not conn.execute("SELECT 1 FROM brand_queue WHERE status='PENDING' LIMIT 1").fetchone():
            break

    out["brand_asins"] = keepa_fetch_brand_asins(conn, key, max_asins=max(args.keepa_batch * 2, 60),
                                                 reserve=reserve, tokens_left=left)
    left = keepa_tokens(key)["left"]

    if pending_qualified() == 0 and left - reserve >= 5 and not args.no_upc_mining:
        out["mined"] = keepa_mine(conn, key, min(args.keepa_batch, left - reserve), reserve)
    return out


# --------------------------------------------------------------------------------------
# 4c. Zoro feed refresh (auto_download.py from S3 → build_zoro_db.py), once per N hours
# --------------------------------------------------------------------------------------
def refresh_catalog(conn: sqlite3.Connection, hours: float) -> bool:
    """Re-download the Zoro TSV feed and rebuild combined_reports.db when it is older than `hours`."""
    if hours <= 0 or not (os.path.exists(FEED_DOWNLOAD) and os.path.exists(FEED_BUILD)):
        return False
    last = kv_get(conn, "catalog_refresh_at")
    if last:
        try:
            if datetime.now() - datetime.fromisoformat(last) < timedelta(hours=hours):
                return False
        except ValueError:
            pass
    # Someone else (Automations' refresh_zoro_and_listings.py) may already have pulled today's feed.
    meta = os.path.join(TSV_DIR, "download_metadata.json")
    if os.path.exists(meta) and time.time() - os.path.getmtime(meta) < hours * 3600 and os.path.exists(ZORO_DB) \
            and os.path.getmtime(ZORO_DB) >= os.path.getmtime(meta) - 60:
        kv_set(conn, "catalog_refresh_at", datetime.fromtimestamp(os.path.getmtime(meta)).isoformat(timespec="seconds")); conn.commit()
        log("[catalog] feed already refreshed recently by another process — skipping")
        return False
    lock = os.path.join(TSV_DIR, ".refresh.lock")
    if os.path.exists(lock) and time.time() - os.path.getmtime(lock) < 3 * 3600:
        log("[catalog] another refresh is in progress (lock present) — will try next cycle")
        return False
    try:
        with open(lock, "w") as fh:
            fh.write(f"hunter.py {os.getpid()} {now_iso()}")
    except OSError:
        pass
    try:
        return _refresh_catalog_locked(conn, hours)
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def _refresh_catalog_locked(conn: sqlite3.Connection, hours: float) -> bool:
    log(f"[catalog] refreshing Zoro feed from S3 (every {hours:g} h)…")
    kv_set(conn, "heartbeat", now_iso()); conn.commit()
    try:
        if os.path.exists(FEED_LOG) and os.path.getsize(FEED_LOG) > 2_000_000:
            os.replace(FEED_LOG, FEED_LOG + ".1")
    except OSError:
        pass
    t0 = time.time()
    with open(FEED_LOG, "a", encoding="utf-8") as fl:
        fl.write(f"\n===== {now_iso()} download =====\n")
        fl.flush()
        try:
            dl = subprocess.run([sys.executable, FEED_DOWNLOAD], cwd=TSV_DIR, stdout=fl, stderr=subprocess.STDOUT, timeout=45 * 60)
        except subprocess.TimeoutExpired:
            log("[catalog] download timed out after 45 min — will retry next cycle")
            kv_set(conn, "catalog_refresh_at", (datetime.now() - timedelta(hours=hours - 1)).isoformat(timespec="seconds")); conn.commit()
            return False
        if dl.returncode == 2:
            log("[catalog] Zoro is republishing the feed right now (bucket empty) — retrying next cycle")
            kv_set(conn, "catalog_refresh_at", (datetime.now() - timedelta(hours=hours - 1)).isoformat(timespec="seconds")); conn.commit()
            return False
        if dl.returncode != 0:
            log(f"[catalog] download failed (exit {dl.returncode}) — see catalog_refresh.log; retrying next cycle")
            kv_set(conn, "catalog_refresh_at", (datetime.now() - timedelta(hours=hours - 1)).isoformat(timespec="seconds")); conn.commit()
            return False
        fl.write(f"\n===== {now_iso()} build =====\n")
        fl.flush()
        try:
            bd = subprocess.run([sys.executable, FEED_BUILD], cwd=TSV_DIR, stdout=fl, stderr=subprocess.STDOUT, timeout=40 * 60)
        except subprocess.TimeoutExpired:
            log("[catalog] DB build timed out — the previous catalog stays in place; retrying next cycle")
            return False
    if bd.returncode != 0:
        log("[catalog] DB build failed — previous catalog kept (see catalog_refresh.log)")
        return False
    kv_set(conn, "catalog_refresh_at", now_iso()); conn.commit()
    log(f"[catalog] feed refreshed and catalog rebuilt in {time.time() - t0:.0f}s (index rebuilds automatically)")
    return True


def apply_min_sold(conn: sqlite3.Connection, min_sold: int) -> None:
    """When the demand floor changes, send un-verified candidates back through evaluation and
    drop pending portal rows that no longer qualify (portal DB is backed up first)."""
    if kv_get(conn, "min_sold_applied") == str(min_sold):
        return
    n = conn.execute("UPDATE candidates SET stage='NEW', verdict=NULL, reason=NULL WHERE stage='READY'").rowcount
    conn.commit()
    if n:
        log(f"[demand] re-evaluating {n} queued candidates under the {min_sold}+/month rule")
    if os.path.exists(PORTAL_DB):
        try:
            import shutil
            bak = PORTAL_DB + f".bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            shutil.copy2(PORTAL_DB, bak)
            pc = sqlite3.connect(PORTAL_DB, timeout=30)
            cur = pc.execute("DELETE FROM portal_listings WHERE status='PENDING' AND COALESCE(monthly_sales,0) < ?", (min_sold,))
            pc.commit(); pc.close()
            log(f"[demand] removed {cur.rowcount} pending portal listings under {min_sold}/month (backup: {os.path.basename(bak)})")
        except Exception as e:
            log(f"[demand] portal cleanup failed: {e}")
    kv_set(conn, "min_sold_applied", str(min_sold)); conn.commit()


# --------------------------------------------------------------------------------------
# 5. Evaluate NEW candidates (0 tokens): store check, Amazon check, Zoro match, pack, pre-screen
# --------------------------------------------------------------------------------------
def dims_clash(amz_title: str, amz_size: str, zoro_title: str) -> bool:
    """True when both sides state dimensions and they disagree — a wrong-size shipment waiting to happen."""
    a = parse_dims(amz_title) or parse_dims(amz_size)
    z = parse_dims(zoro_title)
    if not a or not z:
        return False
    if a == z:
        return False
    # 36x48 vs 36x48x0.2: a thickness on one side only is not a clash.
    return not (len(a) != len(z) and set(z[:2]) == set(a[:2]))


def queue_siblings(conn: sqlite3.Connection, asin: str, brand: str, siblings: str) -> int:
    """Amazon splits a product family into one listing per size; Zoro has a SKU for each size.
    Once one child matches Zoro, its siblings are worth a token each."""
    n = 0
    for sib in [x for x in (siblings or "").split(",") if x]:
        if conn.execute("SELECT 1 FROM candidates WHERE asin=?", (sib,)).fetchone():
            continue
        n += conn.execute("""INSERT OR IGNORE INTO brand_asins (asin, brand, rank, tier, status, found_at)
                             VALUES (?, ?, 0, 0, 'PENDING', ?)""", (sib, brand or "", now_iso())).rowcount
    if n:
        log(f"[variations] {asin}: queued {n} sibling size listing(s) from the same Amazon family")
    return n


def evaluate_new(conn: sqlite3.Connection, store_asins: Set[str], min_margin: float, min_sold: int = 0) -> Dict[str, int]:
    rows = conn.execute("SELECT * FROM candidates WHERE stage='NEW' ORDER BY monthly_sold DESC LIMIT 5000").fetchall()
    counts: Dict[str, int] = {}
    for r in rows:
        verdict, reason, upd = None, "", {}
        asin = r["asin"]
        if asin in store_asins:
            verdict, reason = "ALREADY_LISTED", "ASIN already on HOANK/Hathoro"
        elif (r["keepa_amazon_price"] or 0) > 0 or (r["keepa_bb_seller"] or "").strip().lower() == "amazon":
            verdict, reason = "AMAZON_ON_LISTING", "Keepa shows Amazon.com selling this"
        elif min_sold and (r["monthly_sold"] or 0) < min_sold:
            verdict, reason = "LOW_DEMAND", f"{r['monthly_sold'] or 0}/month (need {min_sold}+)"
        else:
            codes = [c for c in (r["codes"] or "").split(",") if c]
            # For reverse-mined items we already know the Zoro row via the queue, but re-resolve so pack/price are current.
            z, how = zoro_lookup(conn, codes, r["mpn"] or "", r["brand"] or "", r["title"] or "", r["amz_size"] or "")
            if z is None:
                verdict, reason = "NO_ZORO_MATCH", "no UPC/MPN match in Zoro catalog"
            else:
                zp = round(float(z["price"] or 0) + float(z["surcharge"] or 0), 2)
                z_pack = parse_pack(z["title"])
                title_pack = parse_pack(r["title"]) if (r["title"] or "").strip() else 0
                size_pack = parse_pack(r["amz_size"]) if (r["amz_size"] or "").strip() else 0
                keepa_items = r["amz_items"] or 0
                explicit_pack = title_pack if title_pack > 1 else size_pack
                a_items = explicit_pack if explicit_pack > 1 else (keepa_items if keepa_items > 0 else (1 if (title_pack or size_pack) else 0))
                pack_uncertain = 1 if (a_items <= 1 and z_pack > 1 and not (r["title"] or "").strip()) else 0
                upd = {"zoro_no": z["zoro_no"], "zoro_mpn": z["mpn"], "zoro_title": z["title"], "zoro_price": zp,
                       "zoro_pack": z_pack, "zoro_category": z["category"], "zoro_image": z["image"], "match_method": how,
                       "pack_uncertain": pack_uncertain, "zoro_surcharge": float(z["surcharge"] or 0)}
                if zp <= 0:
                    verdict, reason = "NO_ZORO_MATCH", "Zoro price missing"
                elif z["status"] in ("Out of Stock", "Backordered"):
                    verdict, reason = "OUT_OF_STOCK", f"Zoro status {z['status']}"
                elif a_items and z_pack != a_items and not pack_uncertain:
                    verdict, reason = "PACK_MISMATCH", f"Amazon {a_items} vs Zoro {z_pack}"
                elif dims_clash(r["title"] or "", r["amz_size"] or "", z["title"]):
                    verdict, reason = ("SIZE_MISMATCH",
                                       f"Amazon {parse_dims(r['title']) or parse_dims(r['amz_size'])} vs Zoro {parse_dims(z['title'])}")
                else:
                    kp = profit_at(zp, r["keepa_new_price"], r["referral_pct"])
                    upd["keepa_margin"] = kp["margin"]
                    if kp["margin"] is not None and kp["margin"] < min_margin - 3.0:
                        # Keepa price is stale-ish; only reject when clearly hopeless (3 pts under the bar).
                        verdict, reason = "UNPROFITABLE_KEEPA", f"{kp['margin']:.1f}% at Keepa price ${kp['sell']:.2f} (Zoro ${zp:.2f})"
        if verdict:
            sets = ", ".join(f"{k}=?" for k in upd)
            conn.execute(f"UPDATE candidates SET stage='DONE', verdict=?, reason=?, evaluated_at=?{(', ' + sets) if sets else ''} WHERE asin=?",
                         [verdict, reason, now_iso()] + list(upd.values()) + [asin])
            if r["verified_at"]:  # it was published once; it no longer qualifies
                drop_from_portal(asin, reason)
        else:
            sets = ", ".join(f"{k}=?" for k in upd)
            conn.execute(f"UPDATE candidates SET stage='READY', evaluated_at=?, {sets} WHERE asin=?",
                         [now_iso()] + list(upd.values()) + [asin])
            verdict = "READY"
            if not r["siblings_queued"] and r["sibling_asins"]:
                queue_siblings(conn, asin, r["brand"] or "", r["sibling_asins"])
                conn.execute("UPDATE candidates SET siblings_queued=1 WHERE asin=?", (asin,))
        counts[verdict] = counts.get(verdict, 0) + 1
    conn.commit()
    if counts:
        log("[eval] " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return counts


# --------------------------------------------------------------------------------------
# 6. Live Amazon verification (0 tokens) via the shared AOD scraper
# --------------------------------------------------------------------------------------
def offers_cache_path(asin: str) -> str:
    return os.path.join(OFFER_CACHE_DIR, f"{asin}.json")


def fresh_cached_offers(asin: str, ttl_days: float) -> Optional[List[Dict[str, Any]]]:
    p = offers_cache_path(asin)
    if not os.path.exists(p):
        return None
    if time.time() - os.path.getmtime(p) > ttl_days * 86400:
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) and data else None
    except Exception:
        return None


def analyze_offers(offers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Lowest landed NEW price on the listing, excluding our own stores. Flags Amazon Retail."""
    out = {"amazon_retail": False, "our_store": False, "lowest": None, "lowest_seller": "", "n": 0, "top": ""}
    live = []
    for o in offers:
        cond = str(o.get("condition", "New")).lower()
        if any(x in cond for x in ("used", "refurb", "collect", "renewed", "open box")):
            continue
        seller = str(o.get("seller_name", "")).strip()
        if is_our_store(seller):
            out["our_store"] = True
            continue
        if is_amazon_retail(seller):
            out["amazon_retail"] = True
        price = money(o.get("total_price")) or money(o.get("item_price"))
        if not price:
            continue
        ship_from = str(o.get("ships_from", "")).lower()
        live.append({"seller": seller, "price": price, "fba": "amazon" in ship_from})
    live.sort(key=lambda x: x["price"])
    out["n"] = len(live)
    if live:
        out["lowest"], out["lowest_seller"] = live[0]["price"], live[0]["seller"]
        out["top"] = " | ".join(f"{x['seller']} ${x['price']:.2f} {'FBA' if x['fba'] else 'MFN'}" for x in live[:4])
    return out


def live_title(html: str) -> str:
    m = re.search(r'id="productTitle"[^>]*>(.*?)</span>', html, re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""


def scrape_batch(conn: sqlite3.Connection, asins: List[str], headless: bool, titles: Dict[str, str]) -> Dict[str, List[Dict[str, Any]]]:
    from amazon_listing_offers import get_driver, set_amazon_zipcode, click_all_offers_button, parse_all_offers_from_page
    results: Dict[str, List[Dict[str, Any]]] = {}
    try:
        driver = get_driver(headless=headless)
    except Exception as e:
        log(f"[scrape] could not start Chrome: {e}")
        kv_set(conn, "scrape_backoff_until", (datetime.now() + timedelta(minutes=10)).isoformat())
        conn.commit()
        return results
    zip_set = False
    captchas = 0
    try:
        for i, asin in enumerate(asins, 1):
            url = f"https://www.amazon.com/dp/{asin}?th=1&psc=1"
            try:
                driver.get(url)
                time.sleep(2.5)
                if "validateCaptcha" in driver.page_source:
                    captchas += 1
                    log(f"[scrape] captcha on {asin} — pausing 20s")
                    time.sleep(20)
                    driver.get(url)
                    time.sleep(3)
                    if "validateCaptcha" in driver.page_source:
                        if captchas >= 2:
                            log("[scrape] repeated captchas — backing off 20 minutes")
                            kv_set(conn, "scrape_backoff_until", (datetime.now() + timedelta(minutes=20)).isoformat())
                            conn.commit()
                            break
                        continue
                if not zip_set:
                    set_amazon_zipcode(driver, "10001")
                    zip_set = True
                    time.sleep(1.5)
                html = driver.page_source
                t = live_title(html)
                if t:
                    titles[asin] = t
                click_all_offers_button(driver)
                time.sleep(3.0)
                offers = parse_all_offers_from_page(driver.page_source)
                results[asin] = offers
                with open(offers_cache_path(asin), "w", encoding="utf-8") as fh:
                    json.dump(offers, fh, indent=1, ensure_ascii=False)
                if t:
                    with open(offers_cache_path(asin) + ".title", "w", encoding="utf-8") as fh:
                        fh.write(t)
                log(f"[scrape] {i}/{len(asins)} {asin}: {len(offers)} offers")
                time.sleep(random.uniform(1.5, 3.5))
            except Exception as e:
                log(f"[scrape] {asin} failed: {str(e)[:120]}")
                results[asin] = []
    finally:
        try:
            driver.quit()
        except Exception:
            pass
    return results


def requeue_stale_pending(conn: sqlite3.Connection, days: float, limit: int = 15) -> int:
    """Winners still waiting in the portal get re-checked on Amazon every `days` so prices stay honest."""
    if days <= 0 or not os.path.exists(PORTAL_DB):
        return 0
    pc = sqlite3.connect(PORTAL_DB, timeout=30)
    pend = [r[0] for r in pc.execute("SELECT asin FROM portal_listings WHERE status='PENDING'")]
    pc.close()
    if not pend:
        return 0
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    rows = conn.execute(f"""SELECT asin FROM candidates WHERE stage='DONE' AND verdict='WINNER' AND verified_at < ?
                            AND asin IN ({','.join('?' * len(pend))}) ORDER BY verified_at ASC LIMIT ?""",
                        [cutoff] + pend + [limit]).fetchall()
    for r in rows:
        # Back to NEW, not READY: a full re-check re-reads Zoro stock, price, pack and size from the
        # freshly downloaded catalog before it re-checks Amazon. A winner whose Zoro item sold out or
        # went up in price must not sit in the portal looking profitable.
        conn.execute("UPDATE candidates SET stage='NEW', verdict=NULL, reason=NULL, scrape_tries=0 WHERE asin=?", (r["asin"],))
        for suffix in ("", ".title"):
            try:
                os.remove(offers_cache_path(r["asin"]) + suffix)
            except OSError:
                pass
    conn.commit()
    if rows:
        log(f"[recheck] {len(rows)} pending winners older than {days:g} days queued for a fresh Zoro + Amazon check")
    return len(rows)


def drop_from_portal(asin: str, why: str) -> None:
    """A previously published winner failed its re-check: remove it while it is still PENDING."""
    if not os.path.exists(PORTAL_DB):
        return
    pc = sqlite3.connect(PORTAL_DB, timeout=30)
    cur = pc.execute("DELETE FROM portal_listings WHERE asin=? AND status='PENDING'", (asin,))
    pc.commit(); pc.close()
    if cur.rowcount:
        log(f"[recheck] {asin} removed from the portal — {why}")


def verify_ready(conn: sqlite3.Connection, batch: int, ttl_days: float, min_margin: float, headless: bool, do_scrape: bool) -> List[sqlite3.Row]:
    """Verify READY candidates on live Amazon; returns the winners found this cycle."""
    backoff = kv_get(conn, "scrape_backoff_until")
    if backoff and datetime.fromisoformat(backoff) > datetime.now():
        log(f"[scrape] backing off until {backoff[11:19]}")
        return []
    rows = conn.execute("""
        SELECT * FROM candidates WHERE stage='READY' AND scrape_tries < 3
        ORDER BY (keepa_margin IS NULL), keepa_margin DESC, monthly_sold DESC LIMIT ?
    """, (batch,)).fetchall()
    if not rows:
        return []
    need = []
    offers_by: Dict[str, List[Dict[str, Any]]] = {}
    titles: Dict[str, str] = {}
    for r in rows:
        cached = fresh_cached_offers(r["asin"], ttl_days)
        if cached is not None:
            offers_by[r["asin"]] = cached
            try:
                with open(offers_cache_path(r["asin"]) + ".title", encoding="utf-8") as fh:
                    titles[r["asin"]] = fh.read().strip()
            except OSError:
                pass
        else:
            need.append(r["asin"])
    if need and do_scrape:
        log(f"[scrape] verifying {len(need)} ASINs live on Amazon (ZIP 10001), {len(offers_by)} from fresh cache")
        offers_by.update(scrape_batch(conn, need, headless, titles))
    elif need:
        log(f"[scrape] skipped (--no-scrape): {len(need)} ASINs waiting")
    winners = []
    for r in rows:
        asin = r["asin"]
        if asin not in offers_by:
            continue
        offers = offers_by[asin]
        a = analyze_offers(offers)
        zp = float(r["zoro_price"])
        verdict, reason = None, ""
        p = profit_at(zp, a["lowest"], r["referral_pct"])
        t = titles.get(asin, "")
        if t and not (r["title"] or "").strip():
            conn.execute("UPDATE candidates SET title=? WHERE asin=?", (t, asin))
        live_pack = parse_pack(t) if t else 0
        live_size_pack = parse_pack(r["amz_size"]) if (r["amz_size"] or "").strip() else 0
        eff_pack = live_pack if live_pack > 1 else live_size_pack
        if a["our_store"]:
            verdict, reason = "ALREADY_LISTED", "HOANK/Hathoro is already an offer on this listing"
        elif r["pack_uncertain"] and t and eff_pack != (r["zoro_pack"] or 1):
            verdict, reason = "PACK_MISMATCH", f"Amazon pack {eff_pack} vs Zoro {r['zoro_pack']} (live check)"
        elif not r["pack_uncertain"] and eff_pack > 1 and eff_pack != (r["zoro_pack"] or 1):
            verdict, reason = "PACK_MISMATCH", f"Amazon pack {eff_pack} vs Zoro {r['zoro_pack']} (live check)"
        elif a["amazon_retail"]:
            verdict, reason = "AMAZON_ON_LISTING", "Amazon.com has a New offer"
        elif not offers or a["n"] == 0:
            tries = (r["scrape_tries"] or 0) + 1
            if tries >= 3:
                verdict, reason = "NO_OFFERS", "no live New offers parsed after 3 tries"
            else:
                conn.execute("UPDATE candidates SET scrape_tries=? WHERE asin=?", (tries, asin))
                try:
                    os.remove(offers_cache_path(asin))
                except OSError:
                    pass
                continue
        elif p["margin"] is not None and p["margin"] >= min_margin:
            verdict, reason = "WINNER", f"+${p['profit']:.2f} ({p['margin']:.1f}%) at lowest competitor ${p['sell']:.2f}"
        else:
            verdict, reason = "UNPROFITABLE_LIVE", f"{p['margin']:.1f}% at lowest live ${p['sell']:.2f} (Zoro ${zp:.2f})"
        conn.execute("""
            UPDATE candidates SET stage='DONE', verdict=?, reason=?, live_price=?, live_seller=?, live_profit=?, live_margin=?,
                                  competitors=?, offers_n=?, verified_at=?, scrape_tries=scrape_tries+1
            WHERE asin=?
        """, (verdict, reason, a["lowest"], a["lowest_seller"], p["profit"], p["margin"], a["top"], a["n"], now_iso(), asin))
        if verdict == "WINNER":
            winners.append(conn.execute("SELECT * FROM candidates WHERE asin=?", (asin,)).fetchone())
            conn.execute("""INSERT INTO brand_score (brand_key, queried, hits, winners) VALUES (?, 0, 0, 1)
                            ON CONFLICT(brand_key) DO UPDATE SET winners = winners + 1""", (norm_brand(r["brand"]),))
            conn.execute("UPDATE brand_queue SET winners = winners + 1 WHERE brand_key=?", (norm_brand(r["brand"]),))
            log(f"[WINNER] {asin} | {r['brand']} | Zoro ${zp:.2f} → lowest ${p['sell']:.2f} | +${p['profit']:.2f} ({p['margin']:.1f}%) | {r['monthly_sold']}/mo")
        else:
            log(f"[verify] {asin}: {verdict} — {reason}")
            drop_from_portal(asin, reason)
    conn.commit()
    return winners


# --------------------------------------------------------------------------------------
# 7. Publish: portal DB, Excel, Telegram
# --------------------------------------------------------------------------------------
def ensure_portal_db() -> None:
    os.makedirs(PORTAL_DIR, exist_ok=True)
    conn = sqlite3.connect(PORTAL_DB, timeout=30)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portal_listings (
            asin TEXT PRIMARY KEY, brand TEXT, title TEXT, zoro_no TEXT, zoro_mpn TEXT, zoro_title TEXT,
            zoro_price REAL, zoro_shipping REAL, lowest_price REAL, lowest_seller TEXT, referral_fee REAL,
            total_cost REAL, net_profit REAL, net_margin REAL, monthly_sales INTEGER DEFAULT 50,
            pack_count INTEGER DEFAULT 1, status TEXT DEFAULT 'PENDING', source TEXT DEFAULT 'Zoro Reverse Mining',
            created_at TEXT, reviewed_at TEXT)
    """)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(portal_listings)")}
    for col, typ in (("competitors", "TEXT"), ("image_url", "TEXT"), ("category", "TEXT"),
                     ("verified_at", "TEXT"), ("match_method", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE portal_listings ADD COLUMN {col} {typ}")
    conn.commit()
    conn.close()


def publish_to_portal(winners: List[sqlite3.Row]) -> int:
    if not winners:
        return 0
    ensure_portal_db()
    conn = sqlite3.connect(PORTAL_DB, timeout=30)
    n = 0
    for w in winners:
        zp = float(w["zoro_price"])
        p = profit_at(zp, w["live_price"], w["referral_pct"])
        title = w["title"] or w["zoro_title"]
        row = (w["asin"], w["brand"], title, w["zoro_no"], w["zoro_mpn"], w["zoro_title"], zp, p["ship"],
               p["sell"], w["live_seller"], p["fee"], p["cost"], p["profit"], p["margin"],
               w["monthly_sold"] or 0, w["zoro_pack"] or 1, f"hunter · {w['source']}", now_iso(),
               w["competitors"], w["zoro_image"], w["zoro_category"], w["verified_at"], w["match_method"])
        existing = conn.execute("SELECT status FROM portal_listings WHERE asin=?", (w["asin"],)).fetchone()
        if existing and existing[0] != "PENDING":
            continue  # you already approved/rejected it — leave your decision alone
        conn.execute("""
            INSERT OR REPLACE INTO portal_listings
            (asin, brand, title, zoro_no, zoro_mpn, zoro_title, zoro_price, zoro_shipping, lowest_price, lowest_seller,
             referral_fee, total_cost, net_profit, net_margin, monthly_sales, pack_count, source, created_at,
             competitors, image_url, category, verified_at, match_method, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING')
        """, row)
        n += 1
    conn.commit()
    conn.close()
    return n


def export_excel(conn: sqlite3.Connection) -> None:
    try:
        win = pd.read_sql_query("""
            SELECT asin AS ASIN, brand AS Brand, COALESCE(NULLIF(title,''), zoro_title) AS Title, monthly_sold AS "Monthly Sold",
                   zoro_no AS "Zoro #", zoro_mpn AS "MPN", zoro_price AS "Zoro $", live_price AS "Lowest Competitor $",
                   live_seller AS "Lowest Seller", live_profit AS "Net Profit $", live_margin AS "Net Margin %",
                   zoro_pack AS "Pack", match_method AS "Matched by", zoro_category AS "Category", competitors AS "Top Offers",
                   'https://www.amazon.com/dp/' || asin AS "Amazon", 'https://www.zoro.com/search?q=' || zoro_no AS "Zoro",
                   source AS Source, verified_at AS "Verified"
            FROM candidates WHERE verdict='WINNER' AND monthly_sold >= ? ORDER BY live_margin DESC, monthly_sold DESC
        """, conn, params=(MIN_SOLD,))
        pipe = pd.read_sql_query("SELECT stage, COALESCE(verdict,'(pending)') AS verdict, COUNT(*) AS n FROM candidates GROUP BY 1,2 ORDER BY 1,3 DESC", conn)
        calls = pd.read_sql_query("SELECT * FROM keepa_calls ORDER BY at DESC LIMIT 200", conn)
        with pd.ExcelWriter(WINNERS_XLSX, engine="openpyxl") as xw:
            win.to_excel(xw, sheet_name="Winners", index=False)
            pipe.to_excel(xw, sheet_name="Pipeline", index=False)
            calls.to_excel(xw, sheet_name="Keepa calls", index=False)
            for ws in xw.book.worksheets:
                for col in ws.columns:
                    width = min(60, max(10, max(len(str(c.value)) if c.value is not None else 0 for c in col) + 2))
                    ws.column_dimensions[col[0].column_letter].width = width
                ws.freeze_panes = "A2"
    except Exception as e:
        log(f"[excel] export failed: {e}")


def telegram(text: str) -> None:
    try:
        from utils.telegram import send_telegram_message
        send_telegram_message(text, parse_mode="HTML", background=True)
    except Exception as e:
        log(f"[telegram] not sent: {str(e)[:100]}")


# --------------------------------------------------------------------------------------
# 8. Portal auto-start
# --------------------------------------------------------------------------------------
_portal_proc: Optional[subprocess.Popen] = None


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_portal_running() -> None:
    global _portal_proc
    if port_open(PORTAL_PORT):
        return
    if _portal_proc and _portal_proc.poll() is None:
        return
    if not os.path.exists(PORTAL_SERVER):
        return
    try:
        _portal_proc = subprocess.Popen([sys.executable, PORTAL_SERVER], cwd=PORTAL_DIR,
                                        stdout=open(os.path.join(PORTAL_DIR, "portal.log"), "a"), stderr=subprocess.STDOUT)
        log(f"[portal] started review portal → http://127.0.0.1:{PORTAL_PORT}")
    except Exception as e:
        log(f"[portal] could not start: {e}")


# --------------------------------------------------------------------------------------
# 9. Status
# --------------------------------------------------------------------------------------
PID_FILE = os.path.join(CURRENT_DIR, "hunter.pid")


def acquire_instance_lock() -> bool:
    """Only one hunter may run — a second copy would double-spend Keepa tokens and scrapes."""
    try:
        with open(PID_FILE, encoding="utf-8") as fh:
            old = int(fh.read().strip() or 0)
        if old and old != os.getpid():
            os.kill(old, 0)  # raises if that process is gone
            print(f"hunter.py is already running (pid {old}). Use the portal's Run now button, or stop it first.")
            return False
    except (OSError, ValueError, ProcessLookupError):
        pass
    except PermissionError:
        print(f"hunter.py is already running (pid file {PID_FILE}).")
        return False
    with open(PID_FILE, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    return True


def release_instance_lock() -> None:
    try:
        with open(PID_FILE, encoding="utf-8") as fh:
            if int(fh.read().strip() or 0) == os.getpid():
                os.remove(PID_FILE)
    except (OSError, ValueError):
        pass


def inbox_signature() -> str:
    return "|".join(f"{os.path.basename(f)}:{int(os.path.getmtime(f))}" for f in sorted(glob.glob(os.path.join(INBOX_DIR, "*"))))


def print_status(conn: sqlite3.Connection) -> None:
    print("\n=== HUNTER STATUS ===")
    for r in conn.execute("SELECT stage, COALESCE(verdict,'-') v, COUNT(*) n FROM candidates GROUP BY 1,2 ORDER BY 1,3 DESC"):
        print(f"  {r['stage']:<6} {r['v']:<20} {r['n']:>6}")
    q = conn.execute("SELECT status, COUNT(*) n FROM zoro_queue GROUP BY 1").fetchall()
    print("  zoro_queue: " + ", ".join(f"{r['status']}={r['n']}" for r in q))
    q = conn.execute("SELECT status, COUNT(*) n FROM brand_queue GROUP BY 1").fetchall()
    print("  brand_queue: " + ", ".join(f"{r['status']}={r['n']}" for r in q))
    q = conn.execute("SELECT status, COUNT(*) n FROM brand_asins GROUP BY 1").fetchall()
    print("  brand_asins: " + ", ".join(f"{r['status']}={r['n']}" for r in q))
    c = conn.execute("SELECT COUNT(*), COALESCE(SUM(tokens_consumed),0) FROM keepa_calls").fetchone()
    print(f"  keepa calls: {c[0]} (tokens spent {c[1]})")
    f = conn.execute("SELECT COUNT(*) FROM ingested_files").fetchone()[0]
    print(f"  inbox files ingested: {f}")
    if os.path.exists(PORTAL_DB):
        pc = sqlite3.connect(PORTAL_DB)
        for r in pc.execute("SELECT status, COUNT(*) FROM portal_listings GROUP BY 1"):
            print(f"  portal {r[0]}: {r[1]}")
        pc.close()
    print()


# --------------------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------------------
def run_cycle(args, key: str) -> Dict[str, Any]:
    conn = db()
    stats: Dict[str, Any] = {}
    try:
        ensure_zoro_index(conn)
        store_asins = load_store_asins()
        kv_set(conn, "heartbeat", now_iso()); kv_set(conn, "cycle_state", "running"); conn.commit()
        if not args.no_catalog_refresh and refresh_catalog(conn, args.catalog_refresh_hours):
            ensure_zoro_index(conn)
        apply_min_sold(conn, args.min_sold)
        requeue_stale_pending(conn, args.recheck_days)
        stats["inbox_added"] = ingest_inbox(conn)
        seed_brand_queue(conn, args.zoro_min_price)
        import_cached_brand_lists(conn)
        if not args.no_keepa_api and key:
            seed_zoro_queue(conn, args.zoro_min_price, args.zoro_max_price)
            alloc = keepa_allocate(conn, key, args)
            stats["mined_added"] = alloc["mined"] + alloc["brand_asins"]
            stats["brand_query"] = alloc["brand_query"]
        elif not key and not args.no_keepa_api:
            log("[keepa] no API key (.keepa_key / KEEPA_API_KEY) — inbox files + scraping only")
        winners: List[sqlite3.Row] = []
        scraped_cap = args.max_scrape_per_cycle if (args.max_scrape_per_cycle and args.max_scrape_per_cycle > 0) else float("inf")
        while True:
            stats["eval"] = evaluate_new(conn, store_asins, args.min_margin, args.min_sold)
            kv_set(conn, "heartbeat", now_iso()); conn.commit()
            batch = min(args.scrape_batch, int(scraped_cap)) if scraped_cap != float("inf") else args.scrape_batch
            if batch <= 0:
                break
            ready = conn.execute("SELECT COUNT(*) FROM candidates WHERE stage='READY' AND scrape_tries<3").fetchone()[0]
            if ready == 0:
                break
            w = verify_ready(conn, batch, args.offer_ttl_days, args.min_margin, not args.headed, not args.no_scrape)
            winners.extend(w)
            if scraped_cap != float("inf"):
                scraped_cap -= batch
            if args.no_scrape:
                break
            backoff = kv_get(conn, "scrape_backoff_until")
            if backoff and datetime.fromisoformat(backoff) > datetime.now():
                break
        stats["winners"] = len(winners)
        if winners:
            n = publish_to_portal(winners)
            log(f"[portal] {n} winners pushed for review → http://127.0.0.1:{PORTAL_PORT}")
            lines = [f"🎯 <b>Hunter: {len(winners)} new profitable listing(s)</b>"]
            for w in winners[:12]:
                lines.append(f"• <code>{w['asin']}</code> {(w['brand'] or '')[:18]} — Zoro ${w['zoro_price']:.2f} → ${w['live_price']:.2f} "
                             f"= <b>+${w['live_profit']:.2f} ({w['live_margin']:.1f}%)</b> {w['monthly_sold'] or 0}/mo")
            lines.append(f"Review: http://127.0.0.1:{PORTAL_PORT}")
            telegram("\n".join(lines))
        export_excel(conn)
        stats["ready_left"] = conn.execute("SELECT COUNT(*) FROM candidates WHERE stage='READY' AND scrape_tries<3").fetchone()[0]
        stats["new_left"] = conn.execute("SELECT COUNT(*) FROM candidates WHERE stage='NEW'").fetchone()[0]
        kv_set(conn, "last_cycle_at", now_iso())
        kv_set(conn, "last_cycle_summary", json.dumps({k: v for k, v in stats.items() if k != "eval"}))
        kv_set(conn, "cycle_state", "idle"); kv_set(conn, "heartbeat", now_iso())
        conn.commit()
    finally:
        conn.close()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="One script that keeps finding profitable Zoro→Amazon products.")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--no-portal", action="store_true")
    ap.add_argument("--no-keepa-api", action="store_true")
    ap.add_argument("--no-scrape", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--min-margin", type=float, default=1.0)
    ap.add_argument("--zoro-min-price", type=float, default=50.0)
    ap.add_argument("--zoro-max-price", type=float, default=400.0)
    ap.add_argument("--scrape-batch", type=int, default=20)
    # 50, not 20. Keepa refills 1 token/min = 60 an hour, the cycle runs hourly, and a batch
    # of 20 costs ~21 — so 39 tokens of refill were thrown away every cycle, ~940 a day
    # (measured 30 Sep 2026 over 24 cycles). A batch of 50 costs ~51 and still refills inside
    # the hour. keepa_mine already takes min(batch, tokens_left - reserve), so a token-poor
    # cycle simply does less instead of failing.
    ap.add_argument("--keepa-batch", type=int, default=50)
    ap.add_argument("--offer-ttl-days", type=float, default=4.0)
    ap.add_argument("--every", type=int, default=60, help="minutes between cycles (default hourly)")
    ap.add_argument("--max-scrape-per-cycle", type=int, default=500, help="max ASINs verified live per cycle (0 = unlimited)")
    ap.add_argument("--brand-min-sold", type=int, default=50, help="Keepa Product Finder: monthly sold ≥ this for brand queries (raised to --min-sold if higher)")
    ap.add_argument("--min-sold", type=int, default=500, help="only keep listings bought this many times per month or more (0 = no floor)")
    ap.add_argument("--catalog-refresh-hours", type=float, default=24.0, help="re-download the Zoro TSV feed and rebuild the catalog this often (0 = never)")
    ap.add_argument("--no-catalog-refresh", action="store_true")
    ap.add_argument("--brand-queries-per-cycle", type=int, default=4,
                    help="how many Keepa Product Finder brand batches to run per cycle (~11 tokens each)")
    ap.add_argument("--no-upc-mining", action="store_true",
                    help="never spend tokens looking up raw Zoro UPCs (they cannot be demand-filtered)")
    ap.add_argument("--recheck-days", type=float, default=3.0, help="re-verify pending portal winners on Amazon this often (0 = never)")
    ap.add_argument("--zoro-tax-pct", type=float, default=0.0, help="sales tax Zoro charges you, %% of item price (0 if tax-exempt)")
    args = ap.parse_args()

    init_db()
    ensure_portal_db()

    def _term(signum, frame):  # launchd / kill → clean stop (portal child included)
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)

    if args.status:
        conn = db()
        print_status(conn)
        conn.close()
        return

    key = keepa_key()
    global MIN_SOLD, ZORO_TAX_PCT
    MIN_SOLD = args.min_sold
    ZORO_TAX_PCT = args.zoro_tax_pct
    if not acquire_instance_lock():
        return
    log("=" * 78)
    log("HUNTER started — drop Keepa exports into keepa_inbox/, approve winners in the portal")
    log(f"  Zoro DB: {ZORO_DB}")
    log(f"  every {args.every} min | sold ≥ {args.min_sold}/month | min margin {args.min_margin}% | Zoro ${args.zoro_min_price:.0f}–${args.zoro_max_price:.0f} | "
        f"≤{args.max_scrape_per_cycle} live checks/cycle | Keepa API {'OFF' if args.no_keepa_api or not key else 'on'} | "
        f"catalog refresh {'off' if args.no_catalog_refresh or args.catalog_refresh_hours <= 0 else f'every {args.catalog_refresh_hours:g} h'}")
    if not args.no_portal:
        ensure_portal_running()

    cycle = 0
    while True:
        cycle += 1
        t0 = time.time()
        try:
            stats = run_cycle(args, key)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            import traceback
            log(f"[cycle {cycle}] error: {e}\n{traceback.format_exc()[-800:]}")
            stats = {"ready_left": 0, "new_left": 0}
        log(f"[cycle {cycle}] done in {time.time() - t0:.0f}s — winners {stats.get('winners', 0)}, "
            f"inbox +{stats.get('inbox_added', 0)}, mined +{stats.get('mined_added', 0)}, "
            f"ready to verify {stats.get('ready_left', 0)}, unevaluated {stats.get('new_left', 0)}")
        if args.once:
            break
        # Sleep until the next cycle, but wake early for a new inbox file or the portal's "Run now".
        next_run = datetime.now() + timedelta(minutes=args.every)
        try:
            with db() as c:
                kv_set(c, "next_run_at", next_run.isoformat(timespec="seconds")); kv_set(c, "heartbeat", now_iso()); c.commit()
        except Exception:
            pass
        inbox_sig = inbox_signature()
        log(f"[sleep] next cycle at {next_run.strftime('%H:%M')} (drop a Keepa file in keepa_inbox/ or press Run now in the portal to start earlier)")
        try:
            while datetime.now() < next_run:
                time.sleep(20)
                if not args.no_portal:
                    ensure_portal_running()
                try:
                    with db() as c:
                        kv_set(c, "heartbeat", now_iso()); c.commit()
                except Exception:
                    pass
                if os.path.exists(RUN_NOW_FLAG):
                    os.remove(RUN_NOW_FLAG)
                    log("[wake] Run now requested from the portal")
                    break
                if inbox_signature() != inbox_sig:
                    log("[wake] new file in keepa_inbox/")
                    break
        except KeyboardInterrupt:
            break
    if _portal_proc and _portal_proc.poll() is None:
        _portal_proc.terminate()
    release_instance_lock()
    log("HUNTER stopped")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        if _portal_proc and _portal_proc.poll() is None:
            _portal_proc.terminate()
        release_instance_lock()
        log("HUNTER stopped (Ctrl+C)")
