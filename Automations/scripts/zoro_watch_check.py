#!/usr/bin/env python3
"""
zoro_watch_check.py — hourly Zoro restock / handling-time watcher.

Checks every Zoro code in the zoro_watch table using the dedicated UA stealth scraper
(TSV Files/live_enrich.py, port 9225 — the user-agent Zoro whitelisted, so no captcha)
and raises a dashboard Insights alert when a watched item:
  • comes BACK IN STOCK (was OOS/limited/backordered → In Stock), or
  • its handling time drops BELOW 5 days.

Add codes from the dashboard (Insights → Restock Watch box). Run hourly via the launcher,
or manually:  ../.venv/bin/python scripts/zoro_watch_check.py
"""
import os, sys, re, sqlite3
from datetime import datetime

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_AUTO)
sys.path.insert(0, _AUTO)
sys.path.insert(0, os.path.join(_ROOT, "TSV Files"))   # live_enrich.py lives here
DB = os.path.join(_AUTO, "data", "automations_unified.db")


def ensure_table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS zoro_watch(
        zoro_code TEXT PRIMARY KEY, label TEXT,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_availability TEXT, last_lead_days INTEGER, last_lead_text TEXT,
        last_checked TIMESTAMP, alert TEXT, alert_at TIMESTAMP)""")


def lead_days(txt):
    """'Ships in 3 days' / '3-5 business days' → the largest integer (conservative)."""
    if not txt:
        return None
    nums = [int(x) for x in re.findall(r"\d+", str(txt))]
    return max(nums) if nums else None


def main():
    con = sqlite3.connect(DB, timeout=60)
    con.isolation_level = None
    con.execute("PRAGMA busy_timeout=60000")
    con.row_factory = sqlite3.Row
    ensure_table(con)
    rows = [dict(r) for r in con.execute("SELECT * FROM zoro_watch")]
    if not rows:
        print("zoro_watch: nothing to check (add codes in the dashboard).")
        return

    import live_enrich as LE
    driver, proc = LE.launch_chrome()
    try:
        for row in rows:
            code = row["zoro_code"]
            try:
                data = LE.extract_product_data(driver, code)
            except Exception as e:
                print(f"  {code}: check error — {e}")
                continue
            avail = data.get("availability")
            lt = data.get("lead_time")
            ld = lead_days(lt)
            prev_avail, prev_ld = row.get("last_availability"), row.get("last_lead_days")

            alerts = []
            # Only alert on a TRANSITION (skip the first baseline check where prev is None).
            if prev_avail and prev_avail != "In Stock" and avail == "In Stock":
                alerts.append(f"BACK IN STOCK (was {prev_avail})")
            if ld is not None and ld < 5 and (prev_ld is None or prev_ld >= 5) and prev_avail is not None:
                alerts.append(f"Handling time now {ld} days ({lt})")

            alert = " · ".join(alerts) if alerts else row.get("alert")     # keep prior unacked alert
            alert_at = datetime.now().isoformat() if alerts else row.get("alert_at")
            con.execute(
                "UPDATE zoro_watch SET last_availability=?, last_lead_days=?, last_lead_text=?, "
                "last_checked=?, alert=?, alert_at=? WHERE zoro_code=?",
                (avail, ld, lt, datetime.now().isoformat(), alert, alert_at, code))
            mark = ("  🔔 " + " · ".join(alerts)) if alerts else ""
            print(f"  {code}: {avail or '?'} | {lt or '?'}{mark}")
    finally:
        try:
            LE.close_chrome(driver, proc)
        except Exception:
            pass
    print("zoro_watch: done")


if __name__ == "__main__":
    main()
