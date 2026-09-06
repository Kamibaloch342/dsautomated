#!/usr/bin/env python3
"""
migrate_unified_db.py — one-time DB migration for the 2026-06-22 bullet-proofing pass.

WHAT IT DOES (one backed-up, verified pass over closing_orders):
  1. BACK UP the whole DB via the sqlite backup API (WAL-safe) → data/backups/.
  2. INTEGRITY CHECK (quick_check) — abort if the DB is already corrupt.
  3. CLEAN closing_orders:
       • delete rows whose amazon_order_id is NOT a real Amazon id (TEST/garbage),
       • delete all order_label='Pending' rows  (the placement-sync pollution).
     This is the "VIEW model": placed-but-not-yet-closed orders are no longer stored
     physically — they are surfaced by the new v_orders_all VIEW and are fully
     reconstructable from order_placer_orders (which this script NEVER touches).
  4. REORDER closing_orders columns into a logical order. Done the SQLite-safe way:
     parse the LIVE `CREATE TABLE`, reorder the column-definition lines (preserving
     every type/constraint/default verbatim), rebuild the table, copy the rows,
     recreate all indexes + the v_closing view.
  5. CREATE the v_orders_all VIEW = closing_orders  ∪  placer-rows-not-closed.
  6. VERIFY row counts + integrity + print the new column order.

SAFETY
  • DRY-RUN by default: prints the plan (counts + proposed new column order) and exits
    WITHOUT changing anything. Pass --go to actually execute.
  • Reversible: a full backup is taken first, and order_placer_orders is untouched, so
    the deleted Pending rows can always be regenerated.
  • STOP the launcher (10-min auto-sync) and any running closing.py BEFORE --go — this
    rebuilds a table and must not race another writer. The order-placing script is fine
    to leave running (it writes to the `orders` table, not these).
  • --keep-pending : do the reorder + view but DON'T delete Pending rows.

RUN
  ../.venv/bin/python scripts/migrate_unified_db.py          # dry-run (safe, no writes)
  ../.venv/bin/python scripts/migrate_unified_db.py --go     # execute
"""
from __future__ import annotations

import os
import re
import sys
import time
import shutil
import sqlite3
from datetime import datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
_AUTOMATIONS = os.path.dirname(_DIR)
DB = os.path.join(_AUTOMATIONS, "data", "automations_unified.db")
BACKUP_DIR = os.path.join(_AUTOMATIONS, "data", "backups")

GO = "--go" in sys.argv
KEEP_PENDING = "--keep-pending" in sys.argv

AMAZON_ID_RE = re.compile(r"^\d{3}-\d{7}-\d{7}$")

# ── Target logical column order for closing_orders ──────────────────────────
# Any column that exists in the table but is NOT listed here is preserved and
# appended (in its original relative order) AFTER these — so nothing is ever lost.
TARGET_ORDER = [
    # identity
    "id", "amazon_order_id", "merchant_order_id", "account", "profile_name",
    "order_status", "order_label",
    # product
    "asin", "sku", "title", "quantity", "is_business", "item_price", "shipping_price",
    # amazon money in
    "earnings", "amazon_fees", "shipping_amt",
    # ship-to address
    "ship_name", "ship_street", "ship_city", "ship_state", "ship_postal_code",
    "ship_country", "ship_phone",
    # key dates
    "purchase_date", "ship_by_date", "deliver_by_date", "sold_email_time",
    # zoro identity / sourcing
    "wb_order_id", "zoro_account_name", "zoro_gmail_account", "zoro_po_number",
    "zoro_order_status", "zoro_sku", "zoro_qty", "zoro_item_desc", "zoro_buyer_zip",
    "zoro_payment_card", "zoro_payment_method",
    # zoro money out
    "zoro_subtotal", "zoro_shipping", "zoro_tax", "zoro_order_total",
    "coupon_used", "coupon_savings",
    # profit
    "profit_dollars", "profit_percent", "net_profit_after_refund",
    # timing / speed KPIs
    "zoro_order_date", "zoro_email_ts", "order_received_pkt", "zoro_placed_pkt",
    "zoro_shipped_pkt", "placement_speed", "placement_speed_hrs", "zoro_ship_speed",
    "zoro_ship_speed_hrs", "time_to_ship_hrs", "on_time",
    # tracking
    "tracking_ids", "carrier", "shipped_email_time",
    "tracking_id_uploaded", "tracking_upload_date", "tracking_carrier_uploaded",
    # delivery / FedEx
    "delivery_status", "delivery_date", "estimated_window", "last_scan_location",
    "last_scan_time", "signed_by", "left_at", "delivery_note", "delivery_from",
    "delivery_to",
    # returns / refund / Zoro claim
    "return_requested", "return_reason", "return_customer_comment",
    "refund_done", "refund_amount", "refund_date", "refund_reason",
    "zoro_case_number", "zoro_ra_number", "zoro_refund_amount", "zoro_refund_status",
    # customer contact / feedback
    "customer_messaged", "customer_message_text", "customer_inquiry",
    "customer_inquiry_count", "delivered_msg_sent", "review_request_sent",
    "review_requested", "feedback_rating", "feedback_text", "feedback_date",
    # CRM
    "comments", "case_status", "case_type", "case_resolution", "resolved_at",
    "reminder_at", "reminder_note", "flag_color", "manual_delivered",
    # enrichment provenance + audit (kept at the end on purpose)
    "enriched_from_archive", "enriched_from_live_gmail", "enriched_from_placer_db",
    "enriched_from_fedex", "enriched_from_scrape", "last_enrichment_run", "scraped_at",
    "source", "created_at", "updated_at",
]


def log(msg):
    print(msg, flush=True)


# ── CREATE TABLE parsing (reorder columns without losing any constraint) ─────
def strip_sql_comments(sql):
    """Remove -- line comments and /* */ block comments, but NOT inside string
    literals. The live closing_orders CREATE TABLE has inline `-- ...` comments;
    a naive comma split mistakes a comment for a column def and swallows the real
    column after it. Strip them first so every real column is parsed."""
    out, i, n = [], 0, len(sql)
    in_s = in_d = False
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if in_s:
            out.append(ch); in_s = ch != "'"; i += 1; continue
        if in_d:
            out.append(ch); in_d = ch != '"'; i += 1; continue
        if ch == "'":
            in_s = True; out.append(ch); i += 1; continue
        if ch == '"':
            in_d = True; out.append(ch); i += 1; continue
        if ch == "-" and nxt == "-":
            while i < n and sql[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i < n - 1 and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch); i += 1
    return "".join(out)


def _split_top_level(s):
    """Split on commas at parenthesis depth 0, ignoring commas inside string
    literals ('...' / "...") so a DEFAULT 'a,b' can't mis-split."""
    out, depth, cur = [], 0, []
    in_s = in_d = False
    for ch in s:
        if in_s:
            cur.append(ch); in_s = ch != "'"; continue
        if in_d:
            cur.append(ch); in_d = ch != '"'; continue
        if ch == "'":
            in_s = True; cur.append(ch); continue
        if ch == '"':
            in_d = True; cur.append(ch); continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def _col_name(defn):
    """First token of a column definition → bare column name (strip quotes/brackets)."""
    tok = defn.strip().split()[0]
    return tok.strip('"`[]')


_CONSTRAINT_STARTS = ("PRIMARY KEY", "UNIQUE", "CHECK", "FOREIGN KEY", "CONSTRAINT")


def parse_create_table(create_sql):
    """Return (col_defs:dict name->definition in original order, table_constraints:list)."""
    i = create_sql.find("(")
    j = create_sql.rfind(")")
    inner = create_sql[i + 1:j]
    parts = _split_top_level(inner)
    col_defs, constraints = {}, []
    for p in parts:
        if p.upper().lstrip().startswith(_CONSTRAINT_STARTS):
            constraints.append(p)
        else:
            col_defs[_col_name(p)] = p
    return col_defs, constraints


def build_reordered_create(col_defs, constraints):
    """New CREATE TABLE body in TARGET order; unknown cols appended in original order."""
    orig_order = list(col_defs.keys())
    ordered = [c for c in TARGET_ORDER if c in col_defs] + \
              [c for c in orig_order if c not in TARGET_ORDER]
    defs = [col_defs[c] for c in ordered] + list(constraints)
    body = ",\n  ".join(defs)
    return ordered, f"CREATE TABLE closing_orders_new (\n  {body}\n)"


def build_view_all(closing_cols, placer_cols):
    """v_orders_all = every closing_orders row ∪ placer rows not yet in closing_orders
    (presented as order_label='Pending'). Column arity matches closing_orders exactly."""
    sel_closing = ", ".join(f'"{c}"' for c in closing_cols)
    exprs = []
    for c in closing_cols:
        if c == "order_label":
            exprs.append("'Pending' AS order_label")
        elif c == "id":
            exprs.append("NULL AS id")                       # placer id ≠ closing id
        elif c == "merchant_order_id":
            exprs.append("p.wb_order_id AS merchant_order_id"
                         if "wb_order_id" in placer_cols else "NULL AS merchant_order_id")
        elif c in placer_cols:
            exprs.append(f"p.{c} AS {c}")
        else:
            exprs.append(f"NULL AS {c}")
    sel_placer = ", ".join(exprs)
    return (
        "CREATE VIEW v_orders_all AS\n"
        f"  SELECT {sel_closing} FROM closing_orders\n"
        "  UNION ALL\n"
        f"  SELECT {sel_placer} FROM order_placer_orders p\n"
        "  WHERE p.amazon_order_id NOT IN (SELECT amazon_order_id FROM closing_orders)"
    )


def main():
    if not os.path.exists(DB):
        log(f"❌ DB not found: {DB}")
        return 1

    mode = "EXECUTE (--go)" if GO else "DRY-RUN (no changes will be written)"
    log("=" * 70)
    log(f"  migrate_unified_db.py — {mode}")
    log(f"  DB: {DB}  ({os.path.getsize(DB) / 1e9:.2f} GB)")
    log("=" * 70)

    con = sqlite3.connect(DB, timeout=60)
    con.row_factory = sqlite3.Row
    con.isolation_level = None          # autocommit: our explicit BEGIN/COMMIT control txns
    con.execute("PRAGMA busy_timeout=60000")

    # ── Inspect current state ────────────────────────────────────────────────
    cur_cols = [r[1] for r in con.execute("PRAGMA table_info(closing_orders)")]
    placer_cols = {r[1] for r in con.execute("PRAGMA table_info(order_placer_orders)")}
    total = con.execute("SELECT COUNT(*) FROM closing_orders").fetchone()[0]
    pending = con.execute(
        "SELECT COUNT(*) FROM closing_orders WHERE order_label='Pending'").fetchone()[0]
    bad_ids = [r[0] for r in con.execute("SELECT amazon_order_id FROM closing_orders")
               if not AMAZON_ID_RE.match(str(r[0] or ""))]
    log(f"closing_orders: {total} rows | Pending: {pending} | "
        f"malformed amazon_order_id: {len(bad_ids)} | columns: {len(cur_cols)}")
    if bad_ids[:5]:
        log(f"  malformed ids (sample): {bad_ids[:5]}")

    create_sql = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='closing_orders'"
    ).fetchone()[0]
    create_sql = strip_sql_comments(create_sql)        # drop inline -- / /* */ comments
    col_defs, constraints = parse_create_table(create_sql)
    new_order, new_create = build_reordered_create(col_defs, constraints)

    # ── HARD SAFETY GUARD: never drop a column ───────────────────────────────
    # Compare the rebuilt column set against the authoritative PRAGMA list. If the
    # parser missed ANY real column, ABORT — a reorder must be loss-less.
    missing = [c for c in cur_cols if c not in new_order]
    extra = [c for c in new_order if c not in cur_cols]
    if missing or extra:
        log("")
        log("❌ SAFETY ABORT — column-set mismatch, refusing to touch the table:")
        if missing:
            log(f"   parser MISSED these real columns (would be DROPPED): {missing}")
        if extra:
            log(f"   parser INVENTED columns not in the table: {extra}")
        log("   No changes made. Fix the parser before re-running.")
        con.close()
        return 1

    idx_defs = [r[0] for r in con.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='closing_orders' "
        "AND sql IS NOT NULL")]
    vclosing = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name='v_closing'").fetchone()
    vclosing_sql = vclosing[0] if vclosing else None

    log("")
    log(f"Plan:")
    log(f"  • delete {len(bad_ids)} malformed-id row(s)")
    log(f"  • delete {0 if KEEP_PENDING else pending} Pending row(s)"
        + (" (SKIPPED via --keep-pending)" if KEEP_PENDING else ""))
    log(f"  • reorder {len(new_order)} columns "
        f"({len([c for c in new_order if c in TARGET_ORDER])} placed, "
        f"{len([c for c in new_order if c not in TARGET_ORDER])} appended-unknown)")
    log(f"  • recreate {len(idx_defs)} index(es) + v_closing"
        + ("" if vclosing_sql else " (no v_closing found)"))
    log(f"  • create v_orders_all VIEW")
    unknown = [c for c in new_order if c not in TARGET_ORDER]
    if unknown:
        log(f"  • columns kept but not in target list (appended at end): {unknown}")

    if not GO:
        log("")
        log("NEW COLUMN ORDER (proposed):")
        for n, c in enumerate(new_order):
            log(f"   {n:>3}  {c}")
        log("")
        log("DRY-RUN complete — nothing changed. Re-run with --go to execute.")
        con.close()
        return 0

    # ── EXECUTE ──────────────────────────────────────────────────────────────
    # 1) Backup (WAL-safe, via sqlite backup API)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"automations_unified.pre-migration-{stamp}.db")
    log(f"\n[1/6] Backing up → {backup_path} ...")
    t0 = time.time()
    bcon = sqlite3.connect(backup_path)
    with bcon:
        con.backup(bcon)
    bcon.close()
    if not os.path.exists(backup_path) or os.path.getsize(backup_path) < os.path.getsize(DB) * 0.5:
        log("❌ Backup looks too small — ABORTING before any change.")
        con.close()
        return 1
    log(f"      ✅ backup {os.path.getsize(backup_path)/1e9:.2f} GB in {time.time()-t0:.1f}s")

    # 2) Integrity check
    log("[2/6] Integrity quick_check ...")
    ic = con.execute("PRAGMA quick_check").fetchone()[0]
    if ic != "ok":
        log(f"❌ integrity check failed: {ic} — ABORTING (backup is safe).")
        con.close()
        return 1
    log("      ✅ ok")

    try:
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("BEGIN")

        # 3) Clean
        if bad_ids:
            con.executemany("DELETE FROM closing_orders WHERE amazon_order_id=?",
                            [(b,) for b in bad_ids])
            log(f"[3/6] deleted {len(bad_ids)} malformed-id row(s)")
        if not KEEP_PENDING:
            n = con.execute("DELETE FROM closing_orders WHERE order_label='Pending'").rowcount
            log(f"[3/6] deleted {n} Pending row(s)")
        else:
            log("[3/6] Pending rows kept (--keep-pending)")

        # 4) Reorder columns (rebuild)
        log("[4/6] rebuilding closing_orders in new column order ...")
        con.execute("DROP TABLE IF EXISTS closing_orders_new")
        con.execute(new_create)
        collist = ", ".join(f'"{c}"' for c in new_order)
        con.execute(f"INSERT INTO closing_orders_new ({collist}) "
                    f"SELECT {collist} FROM closing_orders")
        con.execute("DROP TABLE closing_orders")
        con.execute("ALTER TABLE closing_orders_new RENAME TO closing_orders")
        for s in idx_defs:
            con.execute(s)
        log(f"      ✅ table rebuilt + {len(idx_defs)} index(es) recreated")
        if vclosing_sql:
            con.execute("DROP VIEW IF EXISTS v_closing")
            con.execute(vclosing_sql)
            log("      ✅ v_closing recreated")

        # 5) v_orders_all
        con.execute("DROP VIEW IF EXISTS v_orders_all")
        con.execute(build_view_all(new_order, placer_cols))
        log("[5/6] ✅ v_orders_all view created")

        con.execute("COMMIT")
    except Exception as e:
        con.execute("ROLLBACK")
        log(f"❌ migration failed, rolled back: {e}")
        log(f"   Your data is unchanged. Backup at: {backup_path}")
        con.close()
        return 1

    # 6) Verify
    log("[6/6] verifying ...")
    new_total = con.execute("SELECT COUNT(*) FROM closing_orders").fetchone()[0]
    va = con.execute("SELECT COUNT(*) FROM v_orders_all").fetchone()[0]
    vp = con.execute("SELECT COUNT(*) FROM v_orders_all WHERE order_label='Pending'").fetchone()[0]
    final_cols = [r[1] for r in con.execute("PRAGMA table_info(closing_orders)")]
    ic2 = con.execute("PRAGMA quick_check").fetchone()[0]
    log(f"      closing_orders now {new_total} rows, {len(final_cols)} columns")
    log(f"      v_orders_all {va} rows ({vp} Pending from placer)")
    log(f"      integrity: {ic2}")
    con.close()

    log("\n" + "=" * 70)
    log("✅ MIGRATION COMPLETE")
    log(f"   backup: {backup_path}")
    log("   NEXT: point the dashboard at v_orders_all (so Pending orders still show),")
    log("         then re-run scripts/sync_order_placer_logs.py --full.")
    log("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
