#!/usr/bin/env python3
"""
gmail_organizer.py — label and archive the store inboxes (31 Oct 2026).

Every mail that matches a rule in config/gmail_rules.json gets one ZUHRA/… label (first matching
rule wins). Rules marked archive=true also take the mail out of the inbox once it is
archive_after_days old: Sold / Zoro receipts / shipped / delivered / returns / promotions.
Mail a person has to act on (buyer messages, cancel requests, account health, Zoro support,
cash refunds, money) is labelled and stays in the inbox.

Safe for the live stack: every Gmail reader (placers' duplicate check, tracker, archive fetch,
messaging) searches all mail by sender / subject, never by inbox, unread or label, so an archived
mail is still found. Nothing is ever deleted, trashed or marked read. A mail the organizer has
archived once is recorded in data/gmail_organizer.db and never archived again, so a mail moved
back to the inbox stays there.

Needs a second token per inbox with the gmail.modify scope —
data/All_Tokens/<inbox>_modify_token.json. The read-only <inbox>_token.json the stack uses is
never touched. An inbox without a modify token is skipped (with --dry-run it is previewed through
its read-only token).

Usage:
  ~/.zuhra/venv/bin/python scripts/gmail_organizer.py --authorize qurtdis   # once per inbox, opens a browser
  ~/.zuhra/venv/bin/python scripts/gmail_organizer.py --dry-run             # what it would do, changes nothing
  ~/.zuhra/venv/bin/python scripts/gmail_organizer.py --dry-run --account qurtdis --days 30
  ~/.zuhra/venv/bin/python scripts/gmail_organizer.py --apply --days 365    # one-off backfill
  ~/.zuhra/venv/bin/python scripts/gmail_organizer.py --apply               # last 3 days (hourly)
Hub inbox (hub_inbox in the rules, mkamrankhan342): every store Gmail forwards into it, so there
the labels go one folder deeper — ZUHRA/<source Gmail>/Amazon/Sold - Ship now — picked with
Gmail's deliveredto: search; the hub's own mail sits under ZUHRA/mkamrankhan342/.

Runs hourly from scripts/auto_sync_10min.py (--apply, last 3 days; a few minutes, the hub takes
the longest); off with bot_config.json "gmail_organizer": {"enabled": false}. Back-fill old mail
once by hand with --days 365 (or 0 = all mail); mail already labelled is skipped.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _AUTO)

from utils import accounts_config  # noqa: E402

TOKEN_DIR = os.path.join(_AUTO, "data", "All_Tokens")
CLIENT_FILE = os.path.join(TOKEN_DIR, "AB ECOM Credentials.json")
RULES_FILE = os.path.join(_AUTO, "config", "gmail_rules.json")
STATE_DB = os.path.join(_AUTO, "data", "gmail_organizer.db")
MODIFY = ["https://www.googleapis.com/auth/gmail.modify"]
READONLY = ["https://www.googleapis.com/auth/gmail.readonly"]


def log(msg):
    print(f"[gmail-org] {msg}", flush=True)


def all_inboxes():
    seen = []
    for name in (accounts_config.gmail_accounts() + accounts_config.zoro_accounts()
                 + accounts_config.gmail_tokens()):
        if name not in seen:
            seen.append(name)
    return seen


def modify_token_path(acct):
    return os.path.join(TOKEN_DIR, f"{acct}_modify_token.json")


def _write_token(path, creds):
    # write a temp file and swap it in, so a crash never leaves a half-written token
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(creds.to_json())
    os.replace(tmp, path)


def _service(acct, modify):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    path = modify_token_path(acct) if modify else os.path.join(TOKEN_DIR, f"{acct}_token.json")
    if not os.path.exists(path):
        return None
    creds = Credentials.from_authorized_user_file(path, MODIFY if modify else READONLY)
    if not creds.valid:
        if not (creds.expired and creds.refresh_token):
            return None
        creds.refresh(Request())
        if modify:  # the read-only tokens belong to the stack: refreshed in memory only
            _write_token(path, creds)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def authorize(acct):
    """Sign this inbox in once with gmail.modify. The Google account chosen in the browser must
    be the same mailbox as the existing read-only token, or nothing is saved."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    ro = _service(acct, modify=False)
    expected = ro.users().getProfile(userId="me").execute()["emailAddress"] if ro else \
        accounts_config.gmail_addresses().get(acct)
    log(f"{acct}: sign in as {expected or '(unknown address)'} in the browser window and allow "
        f"'Read, compose and send … / manage labels' (gmail.modify)")
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_FILE, MODIFY)
    kwargs = {"port": 0, "prompt": "consent", "access_type": "offline"}
    if expected:
        kwargs["login_hint"] = expected
    creds = flow.run_local_server(**kwargs)

    from googleapiclient.discovery import build
    got = build("gmail", "v1", credentials=creds, cache_discovery=False) \
        .users().getProfile(userId="me").execute()["emailAddress"]
    if expected and got.lower() != expected.lower():
        log(f"{acct}: ✗ signed in as {got}, expected {expected} — token NOT saved")
        return False
    _write_token(modify_token_path(acct), creds)
    log(f"{acct}: ✓ modify token saved for {got}")
    return True


def load_rules():
    with open(RULES_FILE) as f:
        cfg = json.load(f)
    cfg["label_prefix"] = cfg.get("label_prefix", "ZUHRA").strip("/")
    cfg["archive_after_days"] = int(cfg.get("archive_after_days", 1))
    return cfg


def inbox_address(acct, db):
    """The Gmail address behind a token name (asked once from Gmail, then cached)."""
    row = db.execute("SELECT email FROM addresses WHERE account=?", (acct,)).fetchone()
    if row:
        return row[0]
    svc = _service(acct, modify=False)
    if svc is None:
        return accounts_config.gmail_addresses().get(acct)
    email = svc.users().getProfile(userId="me").execute()["emailAddress"].lower()
    db.execute("INSERT OR REPLACE INTO addresses VALUES (?,?)", (acct, email))
    db.commit()
    return email


def state_db():
    c = sqlite3.connect(STATE_DB)
    c.execute("""CREATE TABLE IF NOT EXISTS organized (
                   account TEXT NOT NULL, msg_id TEXT NOT NULL, label TEXT NOT NULL,
                   labeled_at TEXT NOT NULL, archived_at TEXT,
                   PRIMARY KEY (account, msg_id))""")
    c.execute("CREATE TABLE IF NOT EXISTS addresses (account TEXT PRIMARY KEY, email TEXT NOT NULL)")
    return c


def search(svc, query):
    ids, page = [], None
    while True:
        resp = svc.users().messages().list(userId="me", q=query, pageToken=page,
                                           maxResults=500).execute(num_retries=5)
        ids += [m["id"] for m in resp.get("messages", [])]
        page = resp.get("nextPageToken")
        if not page:
            return ids


def ensure_labels(svc, names):
    """Label name → id, creating missing labels (and their parents, so Gmail nests them)."""
    existing = {l["name"]: l["id"] for l in svc.users().labels().list(userId="me").execute()["labels"]}
    wanted = []
    for n in names:
        parts = n.split("/")
        wanted += ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    for n in dict.fromkeys(wanted):
        if n not in existing:
            body = {"name": n, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
            existing[n] = svc.users().labels().create(userId="me", body=body).execute()["id"]
            log(f"  created label {n}")
    return existing


def modify(svc, ids, add=None, remove=None):
    """batchModify in chunks of 1,000; returns the ids Gmail accepted. One message Gmail refuses
    ('Precondition check failed' — e.g. a chat or a message gone meanwhile) fails its whole
    batch, so a refused batch is split until the bad message is isolated and skipped."""
    from googleapiclient.errors import HttpError

    body = {}
    if add:
        body["addLabelIds"] = add
    if remove:
        body["removeLabelIds"] = remove

    def run(chunk, tries=0):
        try:
            svc.users().messages().batchModify(userId="me", body={**body, "ids": chunk}).execute()
            time.sleep(0.2)
            return list(chunk)
        except HttpError as e:
            if e.resp.status in (429, 500, 502, 503, 504) and tries < 5:  # Gmail hiccup: back off
                time.sleep(5 * 2 ** tries)
                return run(chunk, tries + 1)
            if e.resp.status != 400:
                raise
            if len(chunk) == 1:
                log(f"  skipped {chunk[0]}: {str(e)[-80:]}")
                return []
            half = len(chunk) // 2
            return run(chunk[:half]) + run(chunk[half:])

    ok = []
    for i in range(0, len(ids), 1000):
        ok += run(ids[i:i + 1000])
    return ok


def organize(acct, cfg, days, apply, db, sources=None):
    """sources: for the hub inbox, [(folder name, address)] of the Gmails that forward into it —
    its mail is then labelled <prefix>/<folder>/<rule>; None = plain <prefix>/<rule>."""
    svc = _service(acct, modify=True)
    if svc is None:
        if apply:
            return None  # not authorized for changes: skipped quietly
        svc = _service(acct, modify=False)
        if svc is None:
            log(f"{acct}: no usable token — skipped")
            return None
    prefix, grace_days = cfg["label_prefix"], cfg["archive_after_days"]
    window = f" newer_than:{days}d" if days else ""
    done = {r[0]: (r[1], r[2]) for r in db.execute(
        "SELECT msg_id, label, archived_at FROM organized WHERE account=?", (acct,))}
    if sources:  # skip the forwarders that sent nothing in the window
        sources = [(n, a) for n, a in sources
                   if svc.users().messages().list(userId="me", q=f"deliveredto:{a}{window}",
                                                  maxResults=1).execute().get("messages")]

    # 1) first matching rule wins: each new mail gets exactly one label; in the hub the
    #    forwarding Gmail picks the folder and the mail delivered to none of them is the hub's own
    assigned, new, rules = {m: lab for m, (lab, _) in done.items()}, {}, []
    own = cfg.get("source_names", {}).get(acct, acct) if sources is not None else None
    for r in cfg["rules"]:
        base = f"{prefix}/{r['label']}"
        rule = {"query": r["query"], "archive": bool(r.get("archive")), "labels": {base}}
        q = f"({r['query']}) -in:sent -in:drafts{window}"
        for name, addr in (sources or []) + ([(own, None)] if own else []):
            label = f"{prefix}/{name}/{r['label']}"
            rule["labels"].add(label)
            for m in search(svc, q + (f" deliveredto:{addr}" if addr else "")):
                if m not in assigned:
                    assigned[m] = new[m] = label
        if not own:
            for m in search(svc, q):
                if m not in assigned:
                    assigned[m] = new[m] = base
        rules.append(rule)
    by_label = Counter(new.values())

    # 2) archive: mail in the inbox, past the grace period, whose own rule archives, and that
    #    the organizer has not archived before
    archive_window = f" newer_than:{max(days or 0, 30)}d" if days else ""
    older = f" older_than:{grace_days}d" if grace_days > 0 else ""
    to_archive = {}
    for r in rules:
        if not r["archive"]:
            continue
        for m in search(svc, f"in:inbox ({r['query']}){older}{archive_window}"):
            if assigned.get(m) in r["labels"] and not (done.get(m) or (None, None))[1]:
                to_archive[m] = assigned[m]

    if apply and (new or to_archive):
        ids = ensure_labels(svc, set(new.values()))
        now = datetime.now().isoformat(timespec="seconds")
        for lab in by_label:
            batch = modify(svc, [m for m, l in new.items() if l == lab], add=[ids[lab]])
            db.executemany("INSERT OR IGNORE INTO organized (account, msg_id, label, labeled_at) "
                           "VALUES (?,?,?,?)", [(acct, m, lab, now) for m in batch])
            db.commit()
        arch = modify(svc, list(to_archive), remove=["INBOX"])
        db.executemany("UPDATE organized SET archived_at=? WHERE account=? AND msg_id=?",
                       [(now, acct, m) for m in arch])
        db.commit()

    verb = "labelled" if apply else "would label"
    detail = ", ".join(f"{l.split('/', 1)[1]} {n}" for l, n in by_label.most_common(12))
    log(f"{acct}: {verb} {len(new)}" + (f" ({detail})" if detail else "")
        + f"; {'archived' if apply else 'would archive'} {len(to_archive)}")
    return len(new), len(to_archive)


def main():
    ap = argparse.ArgumentParser(description="Label and archive the store inboxes")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true", help="make the changes")
    mode.add_argument("--dry-run", action="store_true", help="only report what would change")
    mode.add_argument("--authorize", metavar="INBOX", help="create INBOX's gmail.modify token")
    ap.add_argument("--account", action="append", help="only this inbox (repeatable)")
    ap.add_argument("--days", type=int, default=3, help="look back this many days (0 = all mail)")
    a = ap.parse_args()

    if a.authorize:
        sys.exit(0 if authorize(a.authorize) else 1)

    got, holder = accounts_config.acquire_lock("gmail_organizer")
    if not got:
        log(f"already running (pid {holder})")
        sys.exit(0)
    try:
        cfg = load_rules()
        db = state_db()
        everyone = all_inboxes()
        hub = cfg.get("hub_inbox")
        names = cfg.get("source_names", {})
        failed = 0
        inboxes = a.account or everyone
        for acct in inboxes:
            try:
                sources = None
                if acct == hub:
                    sources = [(names.get(x, x), inbox_address(x, db)) for x in everyone if x != hub]
                    sources = [(n, ad) for n, ad in sources if ad]
                organize(acct, cfg, a.days, a.apply, db, sources)
            except Exception as e:
                failed += 1
                msg = str(e)
                if "insufficient" in msg.lower() or "403" in msg:
                    msg = "token lacks gmail.modify — run --authorize " + acct
                log(f"{acct}: ✗ {msg[:200]}")
        if a.apply and not any(os.path.exists(modify_token_path(x)) for x in inboxes):
            log("no inbox is authorized yet — run --authorize <inbox> first")
        sys.exit(1 if failed and failed == len(inboxes) else 0)
    finally:
        accounts_config.release_lock("gmail_organizer")


if __name__ == "__main__":
    main()
