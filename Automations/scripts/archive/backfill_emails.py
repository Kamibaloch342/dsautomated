#!/usr/bin/env python3
"""
backfill_emails.py — High-performance background sync to backfill historical
emails from store/Zoro accounts into mkamrankhan342@gmail.com.

Features:
- Lifetime sync support with pagination
- SQLite tracking database (data/email_sync_state.db) to ensure 0 duplicates
- Safe error handling and rate-limiting
- Detailed console logging
"""
import os
import sys
import json
import base64
import time
import sqlite3
import argparse
from datetime import datetime

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _AUTO)

from utils import accounts_config
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TOKEN_DIR = os.path.join(_AUTO, "data", "All_Tokens")
STATE_DB = os.path.join(_AUTO, "data", "email_sync_state.db")
HUB_INBOX = "mkamrankhan342"

def init_db():
    conn = sqlite3.connect(STATE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS synced_messages (
            account TEXT NOT NULL,
            source_msg_id TEXT NOT NULL,
            message_header_id TEXT,
            subject TEXT,
            synced_at TEXT NOT NULL,
            PRIMARY KEY (account, source_msg_id)
        )
    """)
    conn.commit()
    return conn

def is_already_synced(conn, acct, msg_id):
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM synced_messages WHERE account=? AND source_msg_id=?", (acct, msg_id))
    return cur.fetchone() is not None

def mark_synced(conn, acct, msg_id, header_id, subject):
    conn.execute(
        "INSERT OR IGNORE INTO synced_messages VALUES (?, ?, ?, ?, ?)",
        (acct, msg_id, header_id, subject, datetime.now().isoformat())
    )
    conn.commit()

def get_service(acct, modify=False):
    token_name = f"{acct}_modify_token.json" if modify else f"{acct}_token.json"
    token_path = os.path.join(TOKEN_DIR, token_name)
    if not os.path.exists(token_path):
        token_path = os.path.join(TOKEN_DIR, f"{acct}_token.json")
    if not os.path.exists(token_path):
        return None
    try:
        creds = Credentials.from_authorized_user_file(token_path)
        return build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception as e:
        print(f"Error loading {acct}: {e}", flush=True)
        return None

def sync_account(acct, conn, query="", limit=None, dry_run=False):
    print(f"\n==================================================", flush=True)
    print(f"[*] Starting Lifetime Sync for [{acct}]", flush=True)
    print(f"==================================================", flush=True)

    src_svc = get_service(acct, modify=False)
    hub_svc = get_service(HUB_INBOX, modify=True)

    if not src_svc:
        print(f"[-] Cannot connect to {acct}. Skipping.", flush=True)
        return 0
    if not hub_svc:
        print(f"[-] Cannot connect to hub {HUB_INBOX}. Aborting.", flush=True)
        return 0

    page_token = None
    total_processed = 0
    total_synced = 0
    total_skipped = 0

    while True:
        try:
            resp = src_svc.users().messages().list(
                userId="me",
                q=query,
                pageToken=page_token,
                maxResults=500
            ).execute()
        except Exception as e:
            print(f"[-] Error fetching message list for {acct}: {e}", flush=True)
            break

        messages = resp.get("messages", [])
        if not messages:
            break

        for m in messages:
            msg_id = m["id"]
            total_processed += 1

            if is_already_synced(conn, acct, msg_id):
                total_skipped += 1
                continue

            try:
                # Fetch raw message
                raw_msg = src_svc.users().messages().get(userId="me", id=msg_id, format="raw").execute()
                raw_data = raw_msg.get("raw")
                if not raw_data:
                    mark_synced(conn, acct, msg_id, "", "(empty)")
                    continue

                # Fetch basic headers
                meta = src_svc.users().messages().get(
                    userId="me", id=msg_id, format="metadata",
                    metadataHeaders=["Subject", "From", "Date", "Message-ID"]
                ).execute()
                headers = {h["name"]: h["value"] for h in meta.get("payload", {}).get("headers", [])}
                subject = headers.get("Subject", "(no subject)")
                from_addr = headers.get("From", "(unknown)")
                date_str = headers.get("Date", "")
                header_id = headers.get("Message-ID", "")

                if dry_run:
                    print(f"  [DRY RUN] Would sync: '{subject[:45]}' from {from_addr[:30]} ({date_str})", flush=True)
                    mark_synced(conn, acct, msg_id, header_id, subject)
                    total_synced += 1
                else:
                    # Insert directly into hub
                    hub_svc.users().messages().insert(
                        userId="me",
                        body={"raw": raw_data, "labelIds": ["INBOX"]},
                        internalDateSource="dateHeader"
                    ).execute()
                    mark_synced(conn, acct, msg_id, header_id, subject)
                    total_synced += 1

                    if total_synced % 20 == 0:
                        print(f"  [+] Synced {total_synced} messages from {acct} (Latest: '{subject[:35]}' {date_str})", flush=True)
                    time.sleep(0.05)

            except Exception as e:
                print(f"  [!] Error syncing msg {msg_id}: {e}", flush=True)
                time.sleep(0.5)

            if limit and total_synced >= limit:
                print(f"[*] Reached limit of {limit} for {acct}.", flush=True)
                return total_synced

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    print(f"[✓] Completed {acct}: {total_synced} newly synced, {total_skipped} already synced ({total_processed} checked).", flush=True)
    return total_synced

def run_all(accounts, days=0, limit=None, dry_run=False):
    conn = init_db()
    after_query = ""
    if days > 0:
        # Optional date restriction
        after_date = (datetime.now() - timedelta(days=days)).strftime("%Y/%m/%d")
        after_query = f"after:{after_date}"

    grand_total = 0
    for acct in accounts:
        synced = sync_account(acct, conn, query=after_query, limit=limit, dry_run=dry_run)
        grand_total += synced

    print(f"\n==================================================", flush=True)
    print(f"[🎉] ALL ACCOUNTS SYNC COMPLETE! Total messages synced: {grand_total}", flush=True)
    print(f"==================================================", flush=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lifetime backfill of past emails into hub")
    parser.add_argument("--accounts", nargs="*", default=None, help="Accounts to sync (default: all configured except inhur)")
    parser.add_argument("--limit", type=int, default=None, help="Max per account")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    args = parser.parse_args()

    target_accounts = args.accounts
    if not target_accounts:
        # Sync the accounts that were previously disabled or need backfill
        target_accounts = ["wrenchlyllc", "abecomllc02", "toolzsolutionsllc220", "hifzasultan342"]

    run_all(target_accounts, limit=args.limit, dry_run=args.dry_run)
