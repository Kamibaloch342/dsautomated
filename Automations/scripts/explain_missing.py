"""
explain_missing.py — WHY each order in the daily_bulk COVERAGE list is still missing a field
(17 Oct 2026). Read-only: opens the DB with mode=ro and writes nothing.

The coverage report only counts holes; this splits every column's missing orders into the
reason closing cannot fill them, so what is left after a run is either "waiting on a step"
(the next run fills it) or "no source has it" (needs a look by hand).

Usage:
  ../.venv/bin/python scripts/explain_missing.py                 # both accounts
  ../.venv/bin/python scripts/explain_missing.py HOANK
  ../.venv/bin/python scripts/explain_missing.py HOANK --ids     # + up to 10 order ids per reason
"""
import os, sys, sqlite3
from collections import defaultdict

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _AUTO)
DB = os.path.join(_AUTO, "data", "automations_unified.db")

from utils.accounts_config import accounts
import closing as C

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
SHOW_IDS = "--ids" in sys.argv
ACCTS = ARGS or list(accounts())


def empty(v):
    return v is None or str(v).strip() in ("", "None")


def _has_table(c, t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def _speed_reason(start, end, start_name, end_name):
    if empty(start):
        return f"no {start_name}"
    if empty(end):
        return f"no {end_name}"
    a, b = C._parse_any(start), C._parse_any(end)
    if not a or not b:
        return f"unreadable time ({start!r} / {end!r})"
    secs = (b - a).total_seconds()
    if secs < 0:
        return f"{end_name} is BEFORE {start_name}"
    if secs > 90 * 86400:
        return "more than 90 days apart"
    return "computable — next reconcile fills it"


def main():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    c.row_factory = sqlite3.Row
    has_trk, has_zoro = _has_table(c, "archive_tracking"), _has_table(c, "archive_zoro")
    has_placer = _has_table(c, "order_placer_orders")
    cols = {r[1] for r in c.execute("PRAGMA table_info(closing_orders)")}
    for acct in ACCTS:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM closing_orders WHERE account=? AND order_status='Shipped'", (acct,))]
        reasons = defaultdict(lambda: defaultdict(list))      # column → reason → [ids]
        for o in rows:
            aid = o["amazon_order_id"]
            legs = C._extract_wb_ids(o.get("merchant_order_id")) or ([o["wb_order_id"]] if o.get("wb_order_id") else [])
            if not legs:
                continue        # WW / manual: not a Zoro order, daily_bulk no longer counts it
            ph = ",".join("?" * len(legs))
            pl = dict(c.execute("SELECT * FROM order_placer_orders WHERE amazon_order_id=?", (aid,)).fetchone() or {}) \
                if has_placer else {}

            if empty(o.get("wb_order_id")):
                reasons["wb_order_id"]["WB id only in merchant_order_id — next reconcile fills it"].append(aid)
            if empty(o.get("zoro_order_total")) or (C._safe_float(o.get("zoro_order_total")) or 0) == 0:
                z = has_zoro and c.execute(f"SELECT 1 FROM archive_zoro WHERE wb_order_id IN ({ph}) "
                                           f"AND COALESCE(order_total,0) != 0", legs).fetchone()
                why = ("Zoro receipt archived — next reconcile fills it" if z else
                       "placer has the cost — next reconcile fills it" if not empty(pl.get("zoro_order_total")) else
                       "no Zoro receipt e-mail in any inbox (live gap-fill searched)")
                reasons["zoro_order_total"][why].append(aid)
            if empty(o.get("tracking_ids")):
                reasons["tracking_ids"]["no Zoro shipping e-mail, no placer / tracker / Zoro parcel record"
                                        if not o.get("delivery_checked_at") else
                                        "Zoro's order lookup returned no parcel either"].append(aid)
            if empty(o.get("shipped_email_time")):
                t = has_trk and c.execute(f"SELECT 1 FROM archive_tracking WHERE wb_order_id IN ({ph})",
                                          legs).fetchone()
                reasons["shipped_email_time"]["shipping e-mail archived — next reconcile fills it" if t else
                                              "no Zoro 'has shipped' e-mail in any inbox"].append(aid)
            if empty(o.get("placement_speed")):
                reasons["placement_speed"][_speed_reason(o.get("sold_email_time"), o.get("zoro_email_ts"),
                                                         "Amazon sold e-mail", "Zoro received e-mail")].append(aid)
            if empty(o.get("zoro_ship_speed")):
                reasons["zoro_ship_speed"][_speed_reason(o.get("zoro_email_ts"), o.get("shipped_email_time"),
                                                         "Zoro received e-mail", "Zoro shipped e-mail")].append(aid)
            if empty(o.get("delivery_status")):
                if empty(o.get("delivery_checked_at")):
                    why = "never checked — delivery check (catch-up 300/run) will read it"
                elif str(o.get("delivery_note") or "").startswith("Zoro"):
                    note = str(o["delivery_note"])
                    why = ("Zoro could not find the order under its buying inbox(es)" if "not found" in note
                           else f"Zoro lookup skipped ({note.split(':', 1)[-1].strip()[:50]})")
                else:
                    why = "checked, Zoro reported no carrier status yet"
                reasons["delivery_status"][why].append(aid)
            if empty(o.get("deliver_by_date")):
                if not empty(pl.get("deliver_by_date")):
                    why = "placer has it — next reconcile fills it"
                elif "scraped_at" in cols and not empty(o.get("scraped_at")):
                    why = "Seller Central page read, showed no deliver-by (re-read after 30 days)"
                else:
                    why = "not read from Seller Central yet — SC scrape takes 200/run"
                reasons["deliver_by_date"][why].append(aid)
            if empty(o.get("profit_dollars")):
                reasons["profit_dollars"]["no earnings" if empty(o.get("earnings")) else
                                          "no Zoro cost" if not C._safe_float(o.get("zoro_order_total")) else
                                          "computable — next reconcile fills it"].append(aid)

        print(f"\n{'=' * 72}\n{acct}: {len(rows)} Shipped orders — why the gaps are still open\n{'=' * 72}")
        if not reasons:
            print("  nothing missing")
        for col, by in reasons.items():
            print(f"\n  {col}: {sum(len(v) for v in by.values())} missing")
            for why, ids in sorted(by.items(), key=lambda x: -len(x[1])):
                print(f"     {len(ids):6}  {why}")
                if SHOW_IDS:
                    print(f"             e.g. {', '.join(ids[:10])}")
    c.close()


if __name__ == "__main__":
    main()
