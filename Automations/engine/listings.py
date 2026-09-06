#!/usr/bin/env python3
"""
listings.py — Listing health engine (growth side).

Answers, per Amazon listing, across BOTH seller accounts:
  • has it sold recently?              → redundant / keep
  • is it live but out of stock at Zoro? → make inactive
  • is it dead but back in stock?        → make active
  • did it sell but no longer exist?     → relist

Three inputs, all files the operator refreshes by hand:
  1. All Listing Report (ALR)  — what is currently on the Amazon account
  2. Order Reports (3 years)   — what actually sold, per SKU, with dates
  3. Zoro catalog TSV database — supplier stock/price (optional; see below)

The Zoro catalog (TSV Files/combined_reports.db, table zoro_data) is OPTIONAL.
Everything sales-based still runs without it; only the stock-driven actions are
skipped, and that is stated explicitly rather than silently producing empty
lists — a missing feed must never read as "nothing is out of stock".

This module only ever COMPUTES and STORES. It never edits Amazon. Output is a
reviewed list the operator uploads themselves.

Usage:
    python engine/listings.py                 # analyse, write to DB, print summary
    python engine/listings.py --days 90       # different redundancy window
    python engine/listings.py --export out.xlsx
"""

from __future__ import annotations

import os
import re
import sys
import glob
import json
import sqlite3
import argparse
from datetime import datetime, timedelta, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_AUTOMATIONS_DIR = os.path.dirname(_SCRIPT_DIR)
if _AUTOMATIONS_DIR not in sys.path:
    sys.path.insert(0, _AUTOMATIONS_DIR)

import pandas as pd

# ── Where the operator's files live ────────────────────────────────────────
DROPSHIPPING_ROOT = os.path.dirname(os.path.dirname(_AUTOMATIONS_DIR))

# Keys are the STORE names the rest of the stack uses (closing_orders.account,
# the tracking profiles, the dashboard's account filter) — not the folder names.
# "1. AB ECOM" is the LLC that owns the Hathoro storefront; "2. Hypersale" owns
# HOANK. Naming these by folder made the dashboard's account filter silently
# match nothing, so store name it is.
ACCOUNTS = [
    {
        "key": "Hathoro",
        "name": "Hathoro",
        "folder": "1. AB ECOM",
        "alr": os.path.join(DROPSHIPPING_ROOT, "1. AB ECOM", "Discounted ALR.txt"),
        "reports": os.path.join(DROPSHIPPING_ROOT, "1. AB ECOM", "Order_Reports"),
    },
    {
        "key": "HOANK",
        "name": "HOANK",
        "folder": "2. Hypersale",
        "alr": os.path.join(DROPSHIPPING_ROOT, "2. Hypersale", "Hypersale ALR.txt"),
        "reports": os.path.join(DROPSHIPPING_ROOT, "2. Hypersale", "Order_Reports"),
    },
]
_KNOWN_ACCOUNTS = tuple(a["key"] for a in ACCOUNTS)

# Zoro catalog, rebuilt daily by TSV Files/quick_download.py. Searched in a few
# places because the project has moved between drives; first hit wins.
ZORO_DB_CANDIDATES = [
    os.path.join(_AUTOMATIONS_DIR, "TSV Files", "combined_reports.db"),
    os.path.join(DROPSHIPPING_ROOT, "TSV Files", "combined_reports.db"),
    os.path.join(_AUTOMATIONS_DIR, "..", "TSV Files", "combined_reports.db"),
    os.path.join(_AUTOMATIONS_DIR, "TSV Files", "combined_database.db"),
    os.path.join(DROPSHIPPING_ROOT, "TSV Files", "combined_database.db"),
]

UNIFIED_DB = os.path.join(_AUTOMATIONS_DIR, "data", "automations_unified.db")

# Defaults — overridable from the launcher via bot_config.json.
DEFAULT_REDUNDANT_DAYS = 60
# A listing younger than this is never called redundant. Kept equal to the
# sales window by default: judging a 40-day-old listing on "no sales in 60 days"
# would condemn it for 20 days it did not exist.
DEFAULT_GRACE_DAYS = 60
DEFAULT_STALE_HOURS = 36     # inputs older than this trigger the "refresh me" ping
DEFAULT_FEED_MAX_AGE_DAYS = 3   # Zoro feed older than this = no stock actions


# ══════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════

def load_settings():
    """Read listing settings from bot_config.json, falling back to defaults."""
    out = {
        "redundant_days": DEFAULT_REDUNDANT_DAYS,
        "grace_days": DEFAULT_GRACE_DAYS,
        "stale_hours": DEFAULT_STALE_HOURS,
        "feed_max_age_days": DEFAULT_FEED_MAX_AGE_DAYS,
    }
    try:
        with open(os.path.join(_AUTOMATIONS_DIR, "bot_config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        lc = cfg.get("listings") or {}
        for k in out:
            if lc.get(k) is not None:
                out[k] = int(lc[k])
    except (FileNotFoundError, ValueError, OSError, TypeError):
        pass
    return out


# ══════════════════════════════════════════════════════════════════════════
#  SKU → ZORO CODE
# ══════════════════════════════════════════════════════════════════════════

_ZORO_CODE_RE = re.compile(r'(G\d{7,}|ZOG\d{7,})', re.IGNORECASE)


def parse_sku(sku):
    """Pull (zoro_code, pack) out of a seller SKU.

    Seller SKUs embed the Zoro code and pack size, e.g. 'hath-G306004662-4'
    → ('G306004662', 4). The operator only maps SKUs in the sheet once an order
    arrives, so deriving it from the SKU string is the primary path, not a
    fallback. ZOG-prefixed codes are normalised to G.
    """
    if not isinstance(sku, str):
        return None, 1
    m = _ZORO_CODE_RE.search(sku)
    code = m.group(1).upper() if m else None
    if code and code.startswith("ZOG"):
        code = "G" + code[3:]
    packs = re.findall(r'-(\d+)', sku)
    pack = 1
    if packs:
        try:
            pack = int(packs[-1]) or 1
        except ValueError:
            pack = 1
    return code, pack


# ══════════════════════════════════════════════════════════════════════════
#  LOADERS
# ══════════════════════════════════════════════════════════════════════════

def load_alr(path):
    """Load an All Listing Report. Returns a normalised DataFrame."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"ALR not found: {path}")
    df = pd.read_csv(path, sep="\t", dtype=str, on_bad_lines="skip",
                     encoding="utf-8-sig", low_memory=False)
    df.columns = [c.strip().lstrip("﻿") for c in df.columns]
    keep = {
        "seller-sku": "sku", "asin1": "asin", "item-name": "title",
        "price": "price", "quantity": "qty", "status": "status",
        "open-date": "open_date", "fulfillment-channel": "fulfillment",
    }
    for src in keep:
        if src not in df.columns:
            df[src] = None
    out = df[list(keep)].rename(columns=keep)
    out["sku"] = out["sku"].astype(str).str.strip()
    out["asin"] = out["asin"].astype(str).str.strip().str.upper()
    out["price"] = pd.to_numeric(out["price"], errors="coerce")
    out["qty"] = pd.to_numeric(out["qty"], errors="coerce").fillna(0).astype(int)
    out["status"] = out["status"].astype(str).str.strip()
    # ALR open-date looks like "2024-11-10 12:18:54 PST". pandas can't infer a
    # format with the zone abbreviation on the end, so it falls back to
    # per-element dateutil parsing — slow on 6k rows and it spams a UserWarning
    # into the refresh log. Strip the abbreviation and parse the fixed part.
    # (Dropping the zone is harmless here: open_date is only used for a
    # listing-age check measured in days.)
    out["open_date"] = pd.to_datetime(
        out["open_date"].astype(str).str.replace(r"\s+[A-Z]{2,4}$", "", regex=True),
        format="%Y-%m-%d %H:%M:%S", errors="coerce", utc=True)
    codes = out["sku"].apply(parse_sku)
    out["zoro_code"] = [c for c, _ in codes]
    out["pack"] = [p for _, p in codes]
    return out[out["sku"].notna() & (out["sku"] != "")].reset_index(drop=True)


def load_order_reports(folder):
    """Load every Amazon order report in `folder` into one DataFrame."""
    files = sorted(glob.glob(os.path.join(folder, "*.txt")))
    if not files:
        return pd.DataFrame(columns=["sku", "asin", "purchase_date", "quantity"]), []
    frames = []
    for f in files:
        try:
            d = pd.read_csv(f, sep="\t", dtype=str, on_bad_lines="skip",
                            encoding="utf-8-sig", low_memory=False)
            frames.append(d)
        except Exception:
            continue
    if not frames:
        return pd.DataFrame(columns=["sku", "asin", "purchase_date", "quantity"]), files
    o = pd.concat(frames, ignore_index=True)
    o.columns = [c.strip().lstrip("﻿") for c in o.columns]
    for col in ("sku", "asin", "purchase-date", "quantity", "order-status"):
        if col not in o.columns:
            o[col] = None
    o = o.rename(columns={"purchase-date": "purchase_date", "order-status": "order_status"})
    o["purchase_date"] = pd.to_datetime(o["purchase_date"], errors="coerce", utc=True)
    o["quantity"] = pd.to_numeric(o["quantity"], errors="coerce").fillna(0)
    o["sku"] = o["sku"].astype(str).str.strip()
    o["asin"] = o["asin"].astype(str).str.strip().str.upper()
    # Cancelled orders are not demand.
    o = o[~o["order_status"].astype(str).str.strip().str.lower().eq("cancelled")]
    # De-duplicate order lines. Consecutive monthly reports overlap on the 1st
    # (a file for month N ends "…-01" and month N+1 begins "…-01"), so the same
    # order-item appears in two files and would be counted twice. One line per
    # (order-id, sku) is the natural key for an Amazon order-item.
    if "amazon-order-id" in o.columns:
        before = len(o)
        o = o.drop_duplicates(subset=["amazon-order-id", "sku"], keep="last")
        dropped = before - len(o)
        if dropped:
            print(f"     (de-duped {dropped:,} overlapping order lines)")
    return o, files


def find_zoro_db():
    for p in ZORO_DB_CANDIDATES:
        p = os.path.normpath(p)
        if os.path.exists(p):
            return p
    return None


def load_zoro_catalog(db_path=None):
    """Load the Zoro catalog. Returns (DataFrame|None, note)."""
    db_path = db_path or find_zoro_db()
    if not db_path:
        return None, ("Zoro catalog not found — looked in: "
                      + "; ".join(os.path.normpath(p) for p in ZORO_DB_CANDIDATES))
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as c:
            cols = {r[1].lower(): r[1] for r in c.execute("PRAGMA table_info(zoro_data)")}
            if not cols:
                return None, f"{db_path}: table zoro_data missing"
            want, sel = {}, []
            for logical, options in (
                ("zoro_code", ("zoro_no", "zorono", "sku")),
                ("availability", ("availabilitystatus", "availability_status", "availability")),
                ("price", ("sales_price", "salesprice", "price")),
                ("lead_time", ("lead_time", "leadtime")),
            ):
                for o in options:
                    if o in cols:
                        want[logical] = cols[o]
                        sel.append(f'"{cols[o]}" AS {logical}')
                        break
            if "zoro_code" not in want:
                return None, f"{db_path}: no zoro_no column in zoro_data"
            df = pd.read_sql_query(f"SELECT {', '.join(sel)} FROM zoro_data", c)
    except Exception as e:
        return None, f"{db_path}: {e}"
    df["zoro_code"] = df["zoro_code"].astype(str).str.strip().str.upper()
    if "price" in df.columns:
        df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.drop_duplicates(subset=["zoro_code"], keep="last")
    return df, f"loaded {len(df):,} Zoro catalog rows from {os.path.basename(db_path)}"


# ══════════════════════════════════════════════════════════════════════════
#  ANALYSIS
# ══════════════════════════════════════════════════════════════════════════

# Zoro's availabilityStatus is a fixed vocabulary (verified against the live
# feed 2026): "In Stock", "Limited Stock", "Drop Ship", "Out of Stock",
# "Backordered", and a large empty-string bucket. Classify by EXACT value, not
# substring — "Limited Stock" and "Drop Ship" are orderable but don't contain
# "in stock", and empty means UNKNOWN, never OOS. Treating unknown as OOS would
# wrongly deactivate ~600k catalog rows' worth of listings.
_ZORO_IN_STOCK = {"in stock", "limited stock", "drop ship", "dropship"}
_ZORO_OUT = {"out of stock", "backordered", "discontinued"}


def _stock_state(series):
    """Map availability strings to 'IN' / 'OUT' / 'UNKNOWN'."""
    s = series.astype(str).str.strip().str.lower()
    out = pd.Series("UNKNOWN", index=series.index)
    out[s.isin(_ZORO_IN_STOCK)] = "IN"
    out[s.isin(_ZORO_OUT)] = "OUT"
    return out


def _history_days(orders, anchor):
    """How many days of order history we actually hold (for honest wording)."""
    if not len(orders):
        return 0
    start = orders["purchase_date"].min()
    return 0 if pd.isna(start) else max(0, (anchor - start).days)


def zoro_feed_age_days():
    """Age in days of the newest Zoro TSV, or None if unknown.

    The catalog DB is rebuilt from the TSVs, so its own mtime proves nothing —
    a rebuild today from March's feed looks brand new. Only the download
    timestamps say how old the stock data really is.
    """
    seen = set()
    for cand in ZORO_DB_CANDIDATES:
        d = os.path.dirname(os.path.normpath(cand))
        if d in seen:
            continue
        seen.add(d)
        meta = os.path.join(d, "download_metadata.json")
        if not os.path.exists(meta):
            continue
        try:
            with open(meta, encoding="utf-8") as f:
                stamps = json.load(f)
            newest = max(datetime.fromisoformat(str(v)) for v in stamps.values())
            if newest.tzinfo is None:
                newest = newest.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - newest).days
        except Exception:
            continue
    return None


def analyse_account(acct, settings, zoro=None, now=None, stock_actionable=True):
    """Build the per-listing action table for one seller account.

    stock_actionable=False keeps the Zoro columns visible for reference but
    suppresses MAKE_INACTIVE / MAKE_ACTIVE — used when the supplier feed is too
    old to justify switching live listings on or off.
    """
    listings = load_alr(acct["alr"])
    orders, report_files = load_order_reports(acct["reports"])

    now = now or datetime.now(timezone.utc)
    days = settings["redundant_days"]
    grace = settings["grace_days"]

    # Sales windows. Anchor on the newest order actually present, not wall
    # clock: if the operator's reports are a week stale, measuring against
    # today would silently mark a week of real sales as "no sales".
    data_end = orders["purchase_date"].max() if len(orders) else None
    anchor = data_end if pd.notna(data_end) else now

    sales = {}
    for label, win in (("d30", 30), ("d60", 60), ("d90", 90), ("d365", 365)):
        cut = anchor - timedelta(days=win)
        w = orders[orders["purchase_date"] >= cut]
        sales[label] = w.groupby("sku")["quantity"].sum()
    ever = orders.groupby("sku")["quantity"].sum()
    last_sale = orders.groupby("sku")["purchase_date"].max()

    win_key = f"d{days}" if f"d{days}" in sales else None
    if win_key is None:                      # arbitrary window
        cut = anchor - timedelta(days=days)
        sales["custom"] = orders[orders["purchase_date"] >= cut].groupby("sku")["quantity"].sum()
        win_key = "custom"

    L = listings.copy()
    L["account"] = acct["key"]
    L["units_window"] = L["sku"].map(sales[win_key]).fillna(0).astype(int)
    L["units_30d"] = L["sku"].map(sales["d30"]).fillna(0).astype(int)
    L["units_365d"] = L["sku"].map(sales["d365"]).fillna(0).astype(int)
    L["units_ever"] = L["sku"].map(ever).fillna(0).astype(int)
    L["last_sale"] = L["sku"].map(last_sale)
    # Listing age is measured against TODAY (wall clock), not the order-data
    # end date: "has this listing been live for 60 days?" is a fact about the
    # calendar, independent of how current the order reports happen to be.
    L["age_days"] = (now - L["open_date"]).dt.days

    # Zoro stock join
    L["zoro_availability"] = None
    L["zoro_price"] = pd.NA
    have_zoro = zoro is not None and len(zoro)
    if have_zoro:
        z = zoro.set_index("zoro_code")
        if "availability" in zoro.columns:
            L["zoro_availability"] = L["zoro_code"].map(z["availability"])
        if "price" in zoro.columns:
            L["zoro_price"] = L["zoro_code"].map(z["price"])

    is_active = L["status"].str.lower().eq("active")
    # A row we matched in the catalog but whose availability is blank is NOT
    # evidence of OOS — only an explicit out-of-stock value is. This is the guard
    # that stops a mostly-empty availability column from deactivating everything.
    state = _stock_state(L["zoro_availability"])
    state[L["zoro_availability"].isna()] = "UNKNOWN"
    in_stock = state.eq("IN")
    oos = state.eq("OUT")

    # Too young to judge: no sales history yet is not evidence of no demand.
    too_young = L["age_days"].notna() & (L["age_days"] < grace)

    # No sales in the window splits two ways, and the difference matters more
    # than the window itself:
    #   • never sold at all           → dead weight, safe to delete
    #   • sold before but not lately  → PROVEN demand gone quiet (seasonal, a
    #                                   supplier gap, a buy-box loss). Deleting
    #                                   these throws away the ASIN's ranking and
    #                                   reviews, so they are flagged for review,
    #                                   never for deletion.
    # THE DELETE RULE: live for longer than the grace period AND zero units sold
    # in the window. Both buckets below are deletions — they are kept apart only
    # so the risk stays visible, because they are not equally safe:
    #   NEVER_SOLD — no sale in the whole history. Dead weight.
    #   DORMANT    — sold before, nothing lately. Deleting this destroys the
    #                ASIN's rank/review history, and some are past top sellers.
    quiet = (L["units_window"] == 0) & ~too_young
    never_sold = quiet & (L["units_ever"] == 0)
    dormant = quiet & (L["units_ever"] > 0)

    L["action"] = "KEEP"
    L.loc[never_sold, "action"] = "DELETE_NEVER_SOLD"
    L.loc[dormant, "action"] = "DELETE_DORMANT"
    # Stock actions outrank redundancy: a listing that is merely out of stock
    # should be paused, not deleted, or it can never come back.
    if have_zoro and stock_actionable:
        L.loc[is_active & oos, "action"] = "MAKE_INACTIVE"
        L.loc[~is_active & in_stock & ~quiet, "action"] = "MAKE_ACTIVE"
    L.loc[too_young, "action"] = "KEEP"

    L["reason"] = ""
    L.loc[L["action"] == "DELETE_NEVER_SOLD", "reason"] = (
        f"never sold in {int(_history_days(orders, anchor))}d of history")
    L.loc[L["action"] == "DELETE_DORMANT", "reason"] = (
        "listed >" + str(grace) + "d, no sales in " + str(days) + "d (sold "
        + L["units_ever"].astype(str) + " units historically)")
    L.loc[L["action"] == "MAKE_INACTIVE", "reason"] = (
        "active but OOS at Zoro: " + L["zoro_availability"].astype(str))
    L.loc[L["action"] == "MAKE_ACTIVE", "reason"] = (
        "inactive but back in stock at Zoro")
    L.loc[too_young, "reason"] = f"listed <{grace}d ago — too new to judge"

    # Sold but no longer listed → relist candidates
    listed_skus = set(L["sku"])
    cut = anchor - timedelta(days=365)
    recent = orders[orders["purchase_date"] >= cut]
    sold_gone = (recent[~recent["sku"].isin(listed_skus)]
                 .groupby(["sku", "asin"])["quantity"].sum()
                 .reset_index().rename(columns={"quantity": "units_365d"}))
    if len(sold_gone):
        sold_gone["account"] = acct["key"]
        sold_gone = sold_gone.sort_values("units_365d", ascending=False)

    meta = {
        "account": acct["key"],
        "account_name": acct["name"],
        "listings": len(L),
        "orders_rows": len(orders),
        "data_end": None if pd.isna(data_end) else data_end.isoformat(),
        "alr_mtime": datetime.fromtimestamp(
            os.path.getmtime(acct["alr"]), timezone.utc).isoformat(),
        "report_files": len(report_files),
        "reports_mtime": (datetime.fromtimestamp(
            max(os.path.getmtime(f) for f in report_files), timezone.utc).isoformat()
            if report_files else None),
        "zoro_joined": bool(have_zoro),
        "zoro_matched": int(L["zoro_availability"].notna().sum()) if have_zoro else 0,
    }
    return L, sold_gone if len(sold_gone) else pd.DataFrame(), meta


# ══════════════════════════════════════════════════════════════════════════
#  PERSISTENCE
# ══════════════════════════════════════════════════════════════════════════

DDL = """
CREATE TABLE IF NOT EXISTS listing_health (
    account         TEXT NOT NULL,
    sku             TEXT NOT NULL,
    asin            TEXT,
    title           TEXT,
    status          TEXT,
    price           REAL,
    qty             INTEGER,
    open_date       TEXT,
    age_days        INTEGER,
    zoro_code       TEXT,
    pack            INTEGER,
    units_window    INTEGER,
    units_30d       INTEGER,
    units_365d      INTEGER,
    units_ever      INTEGER,
    last_sale       TEXT,
    zoro_availability TEXT,
    zoro_price      REAL,
    action          TEXT,
    reason          TEXT,
    computed_at     TEXT,
    PRIMARY KEY (account, sku)
);
CREATE INDEX IF NOT EXISTS ix_listing_health_action ON listing_health(action);
CREATE INDEX IF NOT EXISTS ix_listing_health_asin   ON listing_health(asin);

CREATE TABLE IF NOT EXISTS listing_relist (
    account     TEXT NOT NULL,
    sku         TEXT NOT NULL,
    asin        TEXT,
    units_365d  INTEGER,
    computed_at TEXT,
    PRIMARY KEY (account, sku)
);

CREATE TABLE IF NOT EXISTS listing_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at      TEXT,
    settings    TEXT,
    meta        TEXT,
    zoro_note   TEXT
);
"""


def persist(per_account, relists, metas, settings, zoro_note):
    os.makedirs(os.path.dirname(UNIFIED_DB), exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(UNIFIED_DB, timeout=60) as c:
        c.execute("PRAGMA busy_timeout=60000")
        c.executescript(DDL)
        # Drop rows written under account keys we no longer use (the tables were
        # first populated with folder-derived keys like AB_ECOM/HYPERSALE), or
        # they would linger forever as duplicates of the same listings.
        ph = ",".join("?" * len(_KNOWN_ACCOUNTS))
        c.execute(f"DELETE FROM listing_health WHERE account NOT IN ({ph})", _KNOWN_ACCOUNTS)
        c.execute(f"DELETE FROM listing_relist WHERE account NOT IN ({ph})", _KNOWN_ACCOUNTS)
        cols = ["account", "sku", "asin", "title", "status", "price", "qty", "open_date",
                "age_days", "zoro_code", "pack", "units_window", "units_30d", "units_365d",
                "units_ever", "last_sale", "zoro_availability", "zoro_price",
                "action", "reason", "computed_at"]
        for L in per_account:
            d = L.copy()
            d["computed_at"] = now
            for t in ("open_date", "last_sale"):
                d[t] = d[t].astype(str).where(d[t].notna(), None)
            d = d.reindex(columns=cols)
            c.execute("DELETE FROM listing_health WHERE account = ?", (d["account"].iloc[0],))
            c.executemany(
                f"INSERT OR REPLACE INTO listing_health ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})",
                d.where(pd.notna(d), None).itertuples(index=False, name=None))
        c.execute("DELETE FROM listing_relist")
        for R in relists:
            if R is None or not len(R):
                continue
            R = R.copy()
            R["computed_at"] = now
            c.executemany(
                "INSERT OR REPLACE INTO listing_relist "
                "(account, sku, asin, units_365d, computed_at) VALUES (?,?,?,?,?)",
                R[["account", "sku", "asin", "units_365d", "computed_at"]]
                 .where(pd.notna(R), None).itertuples(index=False, name=None))
        c.execute("INSERT INTO listing_runs (run_at, settings, meta, zoro_note) VALUES (?,?,?,?)",
                  (now, json.dumps(settings), json.dumps(metas), zoro_note))


# ══════════════════════════════════════════════════════════════════════════
#  FRESHNESS
# ══════════════════════════════════════════════════════════════════════════

def freshness(metas, settings):
    """Which inputs are stale enough to ask the operator to refresh."""
    out = []
    limit = timedelta(hours=settings["stale_hours"])
    now = datetime.now(timezone.utc)
    for m in metas:
        for label, key in (("ALR", "alr_mtime"), ("Order reports", "reports_mtime")):
            ts = m.get(key)
            if not ts:
                out.append((m["account_name"], label, None, "missing"))
                continue
            age = now - datetime.fromisoformat(ts)
            if age > limit:
                out.append((m["account_name"], label, age, "stale"))
    return out


# ══════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════

def run(days=None, export=None, quiet=False):
    settings = load_settings()
    if days:
        settings["redundant_days"] = int(days)

    zoro, zoro_note = load_zoro_catalog()
    feed_age = zoro_feed_age_days()
    max_age = settings.get("feed_max_age_days", DEFAULT_FEED_MAX_AGE_DAYS)
    # Switching live listings on/off from a stale supplier feed is worse than
    # doing nothing: a product that sold out in March may be back today, and
    # vice-versa. Past the limit the stock columns still show (for reference)
    # but MAKE_INACTIVE / MAKE_ACTIVE are withheld.
    stock_actionable = bool(zoro is not None and len(zoro)) and (
        feed_age is None or feed_age <= max_age)
    if not quiet:
        print(f"📦 Zoro catalog: {zoro_note}")
        if feed_age is not None:
            flag = "" if feed_age <= max_age else f"  ⛔ older than {max_age}d limit"
            print(f"   feed age: {feed_age} day(s){flag}")

    per_account, relists, metas = [], [], []
    for acct in ACCOUNTS:
        if not os.path.exists(acct["alr"]):
            print(f"⚠️  {acct['name']}: ALR missing at {acct['alr']} — skipped")
            continue
        L, R, meta = analyse_account(acct, settings, zoro=zoro,
                                     stock_actionable=stock_actionable)
        per_account.append(L)
        relists.append(R)
        metas.append(meta)

    if not per_account:
        print("❌ No accounts could be analysed.")
        return None

    persist(per_account, relists, metas, settings, zoro_note)
    allL = pd.concat(per_account, ignore_index=True)

    if not quiet:
        print(f"\n{'='*66}")
        print(f"  LISTING HEALTH  —  redundant window: {settings['redundant_days']}d, "
              f"grace: {settings['grace_days']}d")
        print(f"{'='*66}")
        for m in metas:
            print(f"  {m['account_name']:<10} {m['listings']:>6,} listings  "
                  f"{m['orders_rows']:>7,} order rows  "
                  f"data → {(m['data_end'] or '?')[:10]}"
                  + (f"  zoro matched {m['zoro_matched']:,}" if m["zoro_joined"] else ""))
        print()
        for acct_key, grp in allL.groupby("account"):
            print(f"  {acct_key}:")
            for action, n in grp["action"].value_counts().items():
                print(f"     {action:<18} {n:>6,}")
        if zoro is None or not len(zoro):
            print("\n  ⚠️  No Zoro catalog — MAKE_INACTIVE / MAKE_ACTIVE were NOT evaluated.")
            print("      Restore TSV Files/combined_reports.db to enable stock actions.")
        elif not stock_actionable:
            print(f"\n  ⛔ Zoro feed is {feed_age} days old (limit {max_age}d) — "
                  f"MAKE_INACTIVE / MAKE_ACTIVE withheld.")
            print("      Stock columns are shown for reference only. Re-run "
                  "TSV Files/auto_download.py to refresh, then re-run this.")
        dormant_n = int((allL["action"] == "DELETE_DORMANT").sum())
        if dormant_n:
            print(f"\n  ⚠️  {dormant_n:,} of the deletions previously SOLD — deleting them "
                  f"discards each ASIN's rank and reviews. Listed as DELETE_DORMANT "
                  f"separately so you can eyeball the big sellers first.")
        stale = freshness(metas, settings)
        if stale:
            print("\n  🔔 Refresh needed:")
            for name, label, age, why in stale:
                hrs = f"{age.total_seconds()/3600:.0f}h old" if age else "missing"
                print(f"     • {name} — {label}: {hrs}")
        print(f"\n  💾 Written to {os.path.basename(UNIFIED_DB)} "
              f"(listing_health, listing_relist, listing_runs)")

    if export:
        with pd.ExcelWriter(export, engine="openpyxl") as w:
            allL.to_excel(w, sheet_name="All", index=False)
            for action in ("DELETE_NEVER_SOLD", "DELETE_DORMANT", "MAKE_INACTIVE", "MAKE_ACTIVE"):
                sub = allL[allL["action"] == action]
                if len(sub):
                    sub.to_excel(w, sheet_name=action[:31], index=False)
            rel = pd.concat([r for r in relists if r is not None and len(r)], ignore_index=True) \
                if any(r is not None and len(r) for r in relists) else pd.DataFrame()
            if len(rel):
                rel.to_excel(w, sheet_name="Relist", index=False)
        print(f"  📄 Exported {export}")

    return allL


def main():
    ap = argparse.ArgumentParser(description="Listing health analysis")
    ap.add_argument("--days", type=int, help="Redundancy window in days (default from config)")
    ap.add_argument("--export", help="Also write an .xlsx report")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    run(days=a.days, export=a.export, quiet=a.quiet)


if __name__ == "__main__":
    main()
