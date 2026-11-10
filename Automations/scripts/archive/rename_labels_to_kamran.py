#!/usr/bin/env python3
"""
rename_labels_to_kamran.py — Rename all 'ZUHRA' labels to 'Kamran' across all inboxes
and update the local tracking database.
"""
import os
import sys
import sqlite3
import time

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _AUTO)

from scripts.gmail_organizer import _service
from utils import accounts_config

OLD_PREFIX = "ZUHRA"
NEW_PREFIX = "Kamran"
STATE_DB = os.path.join(_AUTO, "data", "gmail_organizer.db")

def rename_account_labels(acct):
    svc = _service(acct, modify=True)
    if not svc:
        print(f"[{acct}] No modify service available. Skipping.")
        return 0

    try:
        labels = svc.users().labels().list(userId="me").execute().get("labels", [])
    except Exception as e:
        print(f"[{acct}] Error fetching labels: {e}")
        return 0

    target_labels = [l for l in labels if l["name"] == OLD_PREFIX or l["name"].startswith(OLD_PREFIX + "/")]
    if not target_labels:
        print(f"[{acct}] No '{OLD_PREFIX}' labels found.")
        return 0

    # Sort shortest name first so parents are renamed before children
    target_labels.sort(key=lambda l: len(l["name"].split("/")))

    print(f"[{acct}] Renaming {len(target_labels)} labels from '{OLD_PREFIX}' to '{NEW_PREFIX}'...")
    count = 0
    for l in target_labels:
        old_name = l["name"]
        if old_name == OLD_PREFIX:
            new_name = NEW_PREFIX
        else:
            new_name = NEW_PREFIX + old_name[len(OLD_PREFIX):]

        try:
            svc.users().labels().patch(userId="me", id=l["id"], body={"name": new_name}).execute()
            print(f"  ✓ {old_name} -> {new_name}")
            count += 1
            time.sleep(0.05)
        except Exception as e:
            print(f"  ✗ Failed to rename {old_name}: {e}")

    return count

def update_db():
    if not os.path.exists(STATE_DB):
        return
    print(f"\nUpdating database {STATE_DB}...")
    conn = sqlite3.connect(STATE_DB)
    cur = conn.cursor()
    cur.execute(f"UPDATE organized SET label = replace(label, '{OLD_PREFIX}/', '{NEW_PREFIX}/') WHERE label LIKE '{OLD_PREFIX}/%'")
    c1 = cur.rowcount
    cur.execute(f"UPDATE organized SET label = replace(label, '{OLD_PREFIX}', '{NEW_PREFIX}') WHERE label = '{OLD_PREFIX}'")
    c2 = cur.rowcount
    conn.commit()
    print(f"Updated {c1 + c2} rows in organized table.")

def main():
    inboxes = sorted(accounts_config.gmail_tokens())
    total_renamed = 0
    for acct in inboxes:
        total_renamed += rename_account_labels(acct)

    update_db()
    print(f"\n[🎉] Complete! Renamed {total_renamed} labels across inboxes to '{NEW_PREFIX}'.")

if __name__ == "__main__":
    main()
