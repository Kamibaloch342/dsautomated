#!/usr/bin/env python3
"""
gmail_organizer.py — one label tree in every Gmail inbox; routine mail never reaches the inbox.
Built 31 Oct 2026, rebuilt 13 Nov 2026 at the owner's request ("whatever I receive a lot
regularly shouldn't come to my inbox").

config/gmail_rules.json holds the rules: a Gmail search, a label and three switches —
  inbox: true    labelled and kept in the inbox: a person has to act on it
  inbox: false   labelled and kept OUT of the inbox (read: true also marks it read)
  filter: false  hourly run only (a Gmail filter cannot match category:promotions)
The same rules work in three ways:
  --sync-filters       writes them as Gmail filters, so new mail is labelled and skips the inbox
                       the moment it arrives (needs a token with gmail.settings.basic)
  --apply              labels what no filter did: old mail, mail from before a filter existed,
                       promotions (first matching rule wins); hourly from auto_sync_10min.py
  --delete-old-labels  removes the labels of the first organizer (old_label_roots: Kamran/…,
                       ZUHRA/…) and the filters that pointed at them
Hub inbox (hub_inbox, mkamrankhan342): every store Gmail forwards into it, so each mail there also
gets Mailbox/<name> for the Gmail it was delivered to first (Gmail's deliveredto: search).

Safe for the live stack: no reader searches by inbox, label or unread (grep 13 Nov 2026), so mail
out of the inbox or marked read is still found by every automation. Nothing is ever deleted or
trashed. A mail that already carries its label but sits in the inbox was put back by a person and
is left there; data/gmail_organizer.db remembers what was handled, so nothing is done twice.

Tokens: changes use data/All_Tokens/<inbox>_modify_token.json (made by --authorize: gmail.modify +
gmail.settings.basic). Filters may also use the stack's <inbox>_token.json when the owner gave it
gmail.settings.basic (forwarding portal) — refreshed in memory only, never written back.

Usage:
  ~/.zuhra/venv/bin/python scripts/gmail_organizer.py --authorize mkamrankhan342   # opens a browser
  ~/.zuhra/venv/bin/python scripts/gmail_organizer.py --dry-run --account qurtdis --days 30
  ~/.zuhra/venv/bin/python scripts/gmail_organizer.py --sync-filters [--dry-run]
  ~/.zuhra/venv/bin/python scripts/gmail_organizer.py --apply --days 0           # all mail, once
  ~/.zuhra/venv/bin/python scripts/gmail_organizer.py --delete-old-labels [--dry-run]
  ~/.zuhra/venv/bin/python scripts/gmail_organizer.py --apply                    # last 3 days (hourly)
Off with bot_config.json "gmail_organizer": {"enabled": false} (the hourly run; filters stay).
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _AUTO)

from utils import accounts_config  # noqa: E402

TOKEN_DIR = os.path.join(_AUTO, "data", "All_Tokens")
CLIENT_FILE = os.path.join(TOKEN_DIR, "AB ECOM Credentials.json")
RULES_FILE = os.path.join(_AUTO, "config", "gmail_rules.json")
STATE_DB = os.path.join(_AUTO, "data", "gmail_organizer.db")
MODIFY = "https://www.googleapis.com/auth/gmail.modify"
SETTINGS = "https://www.googleapis.com/auth/gmail.settings.basic"
FULL = "https://mail.google.com/"
SKIP = " -in:sent -in:drafts -in:chats"


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


def stack_token_path(acct):
    return os.path.join(TOKEN_DIR, f"{acct}_token.json")


def _scopes(path):
    try:
        with open(path) as f:
            return set(json.load(f).get("scopes") or [])
    except Exception:
        return set()


def _write_token(path, creds):
    # write a temp file and swap it in, so a crash never leaves a half-written token; the temp
    # name is per process and thread (back-fill workers may refresh at the same moment)
    import threading
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w") as f:
        f.write(creds.to_json())
    os.replace(tmp, path)


def _build(path, save):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not os.path.exists(path):
        return None
    creds = Credentials.from_authorized_user_file(path)  # the file's own scopes, never narrowed
    if not creds.valid:
        if not (creds.expired and creds.refresh_token):
            return None
        creds.refresh(Request())
        if save:  # the stack's tokens are refreshed in memory only
            _write_token(path, creds)
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp
    # Gmail can take over a minute for a 1,000-mail batchModify: wait up to 3 minutes
    http = AuthorizedHttp(creds, http=httplib2.Http(timeout=180))
    return build("gmail", "v1", http=http, cache_discovery=False)


def _service(acct, need):
    """need: 'read' (look only), 'modify' (labels / archive), 'settings' (filters)."""
    own, stack = modify_token_path(acct), stack_token_path(acct)
    if need == "read":
        order = [(stack, False), (own, True)]
    elif need == "modify":
        order = [(own, True)]
    else:
        order = [(p, save) for p, save in ((own, True), (stack, False)) if SETTINGS in _scopes(p)]
    for path, save in order:
        svc = _build(path, save)
        if svc is not None:
            return svc
    return None


def authorize(acct):
    """Sign this inbox in once with gmail.modify + gmail.settings.basic. The Google account chosen
    in the browser must be the same mailbox as the existing read-only token, or nothing is saved."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    ro = _service(acct, "read")
    expected = ro.users().getProfile(userId="me").execute()["emailAddress"] if ro else \
        accounts_config.gmail_addresses().get(acct)
    log(f"{acct}: sign in as {expected or '(unknown address)'} in the browser window and allow "
        f"reading/organizing mail and managing filters")
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_FILE, [MODIFY, SETTINGS])
    kwargs = {"port": 0, "prompt": "consent", "access_type": "offline"}
    if expected:
        kwargs["login_hint"] = expected
    creds = flow.run_local_server(**kwargs)
    got = build("gmail", "v1", credentials=creds, cache_discovery=False) \
        .users().getProfile(userId="me").execute()["emailAddress"]
    if expected and got.lower() != expected.lower():
        log(f"{acct}: ✗ signed in as {got}, expected {expected} — token NOT saved")
        return False
    _write_token(modify_token_path(acct), creds)
    log(f"{acct}: ✓ token saved for {got} (organize + filters)")
    return True


def load_rules():
    with open(RULES_FILE) as f:
        cfg = json.load(f)
    prefix = (cfg.get("label_prefix") or "").strip("/")
    for r in cfg["rules"]:
        if "inbox" not in r:  # the first organizer's key
            r["inbox"] = not r.get("archive", False)
        r["label"] = f"{prefix}/{r['label']}" if prefix else r["label"]
        r["read"] = bool(r.get("read")) and not r["inbox"]
        r["filter"] = r.get("filter", True) is not False
    cfg["mailbox_label"] = (cfg.get("mailbox_label") or "").strip("/")  # "" = no Mailbox/… folders
    cfg["old_label_roots"] = cfg.get("old_label_roots") or []
    return cfg


def state_db():
    c = sqlite3.connect(STATE_DB, timeout=120)
    c.execute("""CREATE TABLE IF NOT EXISTS labelled (
                   account TEXT NOT NULL, msg_id TEXT NOT NULL, kind TEXT NOT NULL,
                   label TEXT NOT NULL, at TEXT NOT NULL,
                   PRIMARY KEY (account, msg_id, kind))""")
    c.execute("CREATE TABLE IF NOT EXISTS addresses (account TEXT PRIMARY KEY, email TEXT NOT NULL)")
    return c


def inbox_address(acct, db):
    """The Gmail address behind a token name (asked once from Gmail, then cached)."""
    row = db.execute("SELECT email FROM addresses WHERE account=?", (acct,)).fetchone()
    if row:
        return row[0]
    svc = _service(acct, "read")
    if svc is None:
        return accounts_config.gmail_addresses().get(acct)
    email = svc.users().getProfile(userId="me").execute()["emailAddress"].lower()
    db.execute("INSERT OR REPLACE INTO addresses VALUES (?,?)", (acct, email))
    db.commit()
    return email


def hub_sources(cfg, db, everyone):
    """[(Mailbox folder name, address)] of the Gmails that forward into the hub."""
    hub, names = cfg.get("hub_inbox"), cfg.get("mailbox_names", {})
    out = []
    for x in everyone:
        if x == hub:
            continue
        addr = inbox_address(x, db)
        if addr:
            out.append((names.get(x, x), addr))
    return out


def search(svc, query, label_ids=None):
    ids, page = [], None
    while True:
        kw = {"userId": "me", "pageToken": page, "maxResults": 500}
        if query:
            kw["q"] = query
        if label_ids:
            kw["labelIds"] = label_ids
        resp = svc.users().messages().list(**kw).execute(num_retries=5)
        ids += [m["id"] for m in resp.get("messages", [])]
        page = resp.get("nextPageToken")
        if not page:
            return ids


def label_map(svc):
    return {l["name"]: l["id"] for l in svc.users().labels().list(userId="me").execute()["labels"]}


def ensure_labels(svc, names, colors=None):
    """Label name → id, creating missing labels (and their parents, so Gmail nests them)."""
    from googleapiclient.errors import HttpError

    existing = label_map(svc)
    wanted = []
    for n in names:
        parts = n.split("/")
        wanted += ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    for n in dict.fromkeys(wanted):
        if n in existing:
            continue
        body = {"name": n, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
        if colors and colors.get(n):
            body["color"] = colors[n]
        try:
            existing[n] = svc.users().labels().create(userId="me", body=body).execute()["id"]
        except HttpError as e:
            if "color" not in body:
                raise
            body.pop("color")  # a colour Gmail does not offer: plain label rather than none
            existing[n] = svc.users().labels().create(userId="me", body=body).execute()["id"]
            log(f"  colour refused for {n}: {str(e)[-80:]}")
        log(f"  created label {n}")
    return existing


def modify(svc, ids, add=None, remove=None, factory=None, workers=2):
    """batchModify in chunks of 1,000; returns the ids Gmail accepted. One message Gmail refuses
    ('Precondition check failed' — e.g. a chat or a message gone meanwhile) fails its whole
    batch, so a refused batch is split until the bad message is isolated and skipped.
    Gmail takes ~15 s per 1,000, so with a factory (a function returning a new service — the
    client is not thread-safe) big lists run on `workers` threads, each with its own client."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from googleapiclient.errors import HttpError

    local = threading.local()

    def client():
        if factory is None:
            return svc
        if not hasattr(local, "svc"):
            local.svc = factory()
        return local.svc

    body = {}
    if add:
        body["addLabelIds"] = add
    if remove:
        body["removeLabelIds"] = remove

    def run(chunk, tries=0):
        try:
            client().users().messages().batchModify(userId="me", body={**body, "ids": chunk}).execute()
            time.sleep(0.2)
            return list(chunk)
        except (TimeoutError, ConnectionError, OSError) as e:  # network blip: wait and retry
            if tries >= 5:
                raise
            log(f"  network: {str(e)[:60]} — retrying")
            time.sleep(5 * 2 ** tries)
            return run(chunk, tries + 1)
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

    chunks = [ids[i:i + 1000] for i in range(0, len(ids), 1000)]
    if factory is None or len(chunks) < 3:
        return [m for c in chunks for m in run(c)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return [m for got in pool.map(run, chunks) for m in got]


def _remove_for(rule):
    return (["INBOX"] if not rule["inbox"] else []) + (["UNREAD"] if rule["read"] else [])


def organize(acct, cfg, days, apply, db, sources=None, inbox_only=False):
    """Label (and take out of the inbox) the mail of the last `days` days (0 = all mail) that no
    filter handled. sources: the hub's forwarders, [(Mailbox name, address)]. inbox_only: only
    mail still in the inbox (the quick first pass of a back-fill)."""
    svc = _service(acct, "modify")
    if svc is None:
        if apply:
            return None  # not authorized for changes: skipped quietly
        svc = _service(acct, "read")
        if svc is None:
            log(f"{acct}: no usable token — skipped")
            return None
    window = (f" newer_than:{days}d" if days else "") + (" in:inbox" if inbox_only else "")
    done = {r[0] for r in db.execute(
        "SELECT msg_id FROM labelled WHERE account=? AND kind='rule'", (acct,))}
    have = label_map(svc)

    # 1) first matching rule wins; a mail that already carries its label (a filter, or a person
    #    who moved it back to the inbox) is only recorded, never changed
    first, overlaps = {}, Counter()
    plan, record = defaultdict(list), []
    for r in cfg["rules"]:
        q = f"({r['query']}){SKIP}{window}"
        hits = search(svc, q)
        fresh = []
        for m in hits:
            if m in first:  # only filters can clash: Gmail applies every one that matches
                if first[m][0] != r["label"] and first[m][1] and r["filter"]:
                    overlaps[(first[m][0], r["label"])] += 1
                continue
            first[m] = (r["label"], r["filter"])
            if m not in done:
                fresh.append(m)
        if not fresh:
            continue
        lid = have.get(r["label"])
        already = set(search(svc, q, label_ids=[lid])) if lid else set()
        todo = [m for m in fresh if m not in already]
        # backfill: old mail of a to-do label (out of the inbox, left unread) is marked read, so
        # the label's unread count is what waits now, not years of history
        old_days = int(cfg.get("read_older_than_days") or 0)
        if old_days and not r["inbox"] and not r["read"] and (not days or days > old_days):
            old = set(search(svc, f"({r['query']}){SKIP}{window} older_than:{old_days}d"))
            plan[(r["label"], False, True)] += [m for m in todo if m in old]
            todo = [m for m in todo if m not in old]
        plan[(r["label"], r["inbox"], r["read"])] += todo
        record += [(m, r["label"]) for m in fresh if m in already]

    # 2) hub: Mailbox/<name> for the Gmail each mail was delivered to first
    mplan = defaultdict(list)
    if sources:
        mdone = {r[0] for r in db.execute(
            "SELECT msg_id FROM labelled WHERE account=? AND kind='mailbox'", (acct,))}
        mseen = set()
        for name, addr in sources:
            lab = f"{cfg['mailbox_label']}/{name}"
            for m in search(svc, f"deliveredto:{addr}{SKIP}{window}"):
                if m not in mseen:
                    mseen.add(m)
                    if m not in mdone:
                        mplan[lab].append(m)

    n_label = sum(len(v) for v in plan.values())
    n_out = sum(len(v) for (lab, inbox, read), v in plan.items() if not inbox)
    by_label = Counter()
    for (lab, inbox, read), v in plan.items():
        by_label[lab] += len(v)

    if apply and (plan or mplan or record):
        ids = ensure_labels(svc, set(by_label) | set(mplan), cfg.get("label_colors"))
        now = datetime.now().isoformat(timespec="seconds")
        put = "INSERT OR REPLACE INTO labelled (account, msg_id, kind, label, at) VALUES (?,?,?,?,?)"
        db.executemany(put, [(acct, m, "rule", lab, now) for m, lab in record])
        db.commit()
        factory = lambda: _service(acct, "modify")  # noqa: E731
        # mail still in the inbox first, so the inbox empties early in a long back-fill
        in_inbox = set(search(svc, "in:inbox")) if n_out else set()
        for first_pass in (True, False):
            for (lab, inbox, read), ms in plan.items():
                part = [m for m in ms if (m in in_inbox) == first_pass]
                if not part:
                    continue
                rule = {"inbox": inbox, "read": read}
                ok = modify(svc, part, add=[ids[lab]], remove=_remove_for(rule) or None,
                            factory=factory)
                db.executemany(put, [(acct, m, "rule", lab, now) for m in ok])
                db.commit()
            if first_pass and in_inbox:
                log(f"{acct}: inbox cleared of rule mail")
        for lab, ms in mplan.items():
            ok = modify(svc, ms, add=[ids[lab]], factory=factory)
            db.executemany(put, [(acct, m, "mailbox", lab, now) for m in ok])
            db.commit()

    verb = "labelled" if apply else "would label"
    detail = ", ".join(f"{l} {n}" for l, n in by_label.most_common(30))
    log(f"{acct}: {verb} {n_label}, {n_out} of them kept out of the inbox"
        + (f" ({detail})" if detail else ""))
    if mplan:
        log(f"{acct}: Mailbox labels {'added' if apply else 'to add'}: "
            + ", ".join(f"{l.split('/', 1)[1]} {len(v)}" for l, v in
                        sorted(mplan.items(), key=lambda kv: -len(kv[1]))))
    for (a, b), n in overlaps.most_common(10):
        log(f"{acct}: overlap — {n} mail match both '{a}' (wins) and '{b}'")
    return n_label, n_out


def _filter_key(criteria, action, id2name):
    return (criteria.get("query", ""),
            tuple(sorted(id2name.get(x, x) for x in action.get("addLabelIds", []))),
            tuple(sorted(action.get("removeLabelIds", []))),
            action.get("forward") or "")


def sync_filters(acct, cfg, apply, sources=None):
    """Make the inbox's Gmail filters match the rules: create what is missing, delete our filters
    that no rule asks for any more. Filters that are not ours (or that forward mail) are kept."""
    svc = _service(acct, "settings")
    if svc is None:
        log(f"{acct}: no token with gmail.settings.basic — run --authorize {acct}")
        return None
    want = []
    for r in cfg["rules"]:
        if r["filter"]:
            want.append(({"query": r["query"]}, r["label"], _remove_for(r)))
    for name, addr in sources or []:
        want.append(({"query": f"deliveredto:{addr}"}, f"{cfg['mailbox_label']}/{name}", []))
    managed = {lab for _, lab, _ in want}

    have = label_map(svc)
    id2name = {v: k for k, v in have.items()}
    existing = svc.users().settings().filters().list(userId="me").execute().get("filter", [])
    ours = {}
    for f in existing:
        names = [id2name.get(x, "") for x in f.get("action", {}).get("addLabelIds", [])]
        if f.get("action", {}).get("forward"):
            continue
        if any(n in managed for n in names):
            ours[_filter_key(f.get("criteria", {}), f.get("action", {}), id2name)] = f["id"]
    wanted = {(c["query"], (lab,), tuple(sorted(rm)), ""): (c, lab, rm) for c, lab, rm in want}
    create = [v for k, v in wanted.items() if k not in ours]
    drop = [fid for k, fid in ours.items() if k not in wanted]
    log(f"{acct}: filters — {len(wanted) - len(create)} in place, "
        f"{'creating' if apply else 'would create'} {len(create)}, "
        f"{'deleting' if apply else 'would delete'} {len(drop)} of ours "
        f"({len(existing) - len(ours)} others kept)")
    if not apply:
        return len(create), len(drop)
    ids = ensure_labels(svc, {lab for _, lab, _ in create}, cfg.get("label_colors"))
    for fid in drop:
        svc.users().settings().filters().delete(userId="me", id=fid).execute()
    for crit, lab, rm in create:
        action = {"addLabelIds": [ids[lab]]}
        if rm:
            action["removeLabelIds"] = rm
        svc.users().settings().filters().create(
            userId="me", body={"criteria": crit, "action": action}).execute()
        time.sleep(0.3)
    return len(create), len(drop)


def tree_labels(cfg, sources=None):
    """Every label the rules (and, in the hub, the Mailbox folders) use, parents included."""
    names = {r["label"] for r in cfg["rules"]}
    names |= {f"{cfg['mailbox_label']}/{n}" for n, _ in (sources or [])}
    out = set()
    for n in names:
        parts = n.split("/")
        out |= {"/".join(parts[:i]) for i in range(1, len(parts) + 1)}
    return out


def sync_colors(acct, cfg, apply):
    """Give every label of the tree its colour from label_colors (owner, 14 Nov 2026)."""
    svc = _service(acct, "modify")
    if svc is None:
        return None
    want = cfg.get("label_colors") or {}
    changed = 0
    for l in svc.users().labels().list(userId="me").execute()["labels"]:
        c = want.get(l["name"])
        if not c or l.get("color") == c:
            continue
        changed += 1
        if apply:
            svc.users().labels().patch(userId="me", id=l["id"], body={"color": c}).execute()
    log(f"{acct}: colours — {'set' if apply else 'would set'} {changed}")
    return changed


def delete_old_labels(acct, cfg, apply, db, sources=None):
    """Delete the first organizer's labels (old_label_roots: Kamran/…, ZUHRA/…) and the owner's
    hand-made ones folded into the tree (old_labels_also). A hand-made label is deleted only when
    every mail under it already carries a label of the new tree; otherwise it stays and the count
    is reported. A hand-made label that is also a parent in the new tree (Amazon, Zoro, Shopify)
    is taken off its mail instead of deleted. Filters that add a deleted label go too — except a
    filter that forwards mail, which is re-created without that label. Mail itself is never
    deleted."""
    svc = _service(acct, "modify")
    if svc is None:
        log(f"{acct}: no modify token — skipped")
        return None
    roots = cfg["old_label_roots"]
    extra = set(cfg.get("old_labels_also", {}).get(acct, []))
    tree = tree_labels(cfg, sources)
    labels = svc.users().labels().list(userId="me").execute()["labels"]
    by_name = {l["name"]: l["id"] for l in labels}
    ours = {by_name[n] for n in tree if n in by_name}
    done = {r[0] for r in db.execute(
        "SELECT msg_id FROM labelled WHERE account=? AND kind='rule'", (acct,))}

    victims, strip, kept = {}, {}, {}
    for l in labels:
        name = l["name"]
        if l["type"] != "user" or name in tree:
            if l["type"] == "user" and name in extra and name in tree:
                strip[l["id"]] = name  # a hand-made label that is now a parent of the tree
            continue
        if any(name == r or name.startswith(r + "/") for r in roots):
            victims[l["id"]] = name
        elif name in extra:
            ids = search(svc, None, label_ids=[l["id"]])
            rest = [m for m in ids if m not in done]
            bad = []
            # mail the back-fill did not record (arrived since, labelled by a filter): ask Gmail;
            # more than 300 of them = the back-fill has not covered this label yet → it stays
            for m in rest[:300]:
                got = svc.users().messages().get(userId="me", id=m, format="minimal").execute()
                if not set(got.get("labelIds", [])) & ours:
                    bad.append(m)
            bad += rest[300:]
            if bad:
                kept[l["id"]] = (name, len(bad), len(ids))
            else:
                victims[l["id"]] = name

    fsvc = _service(acct, "settings")
    drop, reforward = [], []
    if fsvc is not None and (victims or strip):
        gone = set(victims) | set(strip)
        for f in fsvc.users().settings().filters().list(userId="me").execute().get("filter", []):
            act = f.get("action", {})
            if set(act.get("addLabelIds", [])) & gone:
                (reforward if act.get("forward") else drop).append(f)

    log(f"{acct}: {'deleting' if apply else 'would delete'} {len(victims)} old labels"
        + (f" ({sum(1 for n in victims.values() if not any(n == r or n.startswith(r + '/') for r in roots))} hand-made)")
        + (f", clearing {len(strip)} that are now parents ({', '.join(strip.values())})" if strip else "")
        + f", {len(drop)} filters that fed them"
        + (f"; re-creating {len(reforward)} forwarding filter(s) without the old label" if reforward else ""))
    for name, n_bad, n_all in kept.values():
        log(f"{acct}: kept '{name}': {n_bad} of its {n_all} mails match no rule of the new tree")
    if not apply:
        return len(victims)

    for f in reforward:
        act = dict(f["action"])
        act["addLabelIds"] = [x for x in act.get("addLabelIds", []) if x not in victims and x not in strip]
        if not act["addLabelIds"]:
            act.pop("addLabelIds")
        fsvc.users().settings().filters().create(
            userId="me", body={"criteria": f["criteria"], "action": act}).execute()
        fsvc.users().settings().filters().delete(userId="me", id=f["id"]).execute()
    for f in drop:
        fsvc.users().settings().filters().delete(userId="me", id=f["id"]).execute()
    for lid in strip:
        modify(svc, search(svc, None, label_ids=[lid]), remove=[lid])
    # children first, so nothing is re-parented on the way
    for lid, name in sorted(victims.items(), key=lambda kv: -kv[1].count("/")):
        svc.users().labels().delete(userId="me", id=lid).execute()
        time.sleep(0.1)
    return len(victims)


def main():
    ap = argparse.ArgumentParser(description="Label the store inboxes and keep routine mail out")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true", help="label / archive mail (hourly)")
    mode.add_argument("--dry-run", action="store_true", help="only report what --apply would do")
    mode.add_argument("--sync-filters", action="store_true", help="write the rules as Gmail filters")
    mode.add_argument("--delete-old-labels", action="store_true", help="remove the old labels")
    mode.add_argument("--sync-colors", action="store_true", help="colour the tree's labels")
    mode.add_argument("--authorize", metavar="INBOX", help="create INBOX's organize+filters token")
    ap.add_argument("--check", action="store_true",
                    help="with --sync-filters / --delete-old-labels: report only")
    ap.add_argument("--account", action="append", help="only this inbox (repeatable)")
    ap.add_argument("--days", type=int, default=3, help="look back this many days (0 = all mail)")
    ap.add_argument("--inbox-only", action="store_true",
                    help="with --apply / --dry-run: only mail still in the inbox (fast first pass)")
    a = ap.parse_args()

    if a.authorize:
        sys.exit(0 if authorize(a.authorize) else 1)

    # one run per inbox set: a back-fill of several inboxes can run side by side
    lock = "gmail_organizer" + ("_" + "_".join(sorted(a.account)) if a.account else "")
    got, holder = accounts_config.acquire_lock(lock)
    if not got:
        log(f"already running (pid {holder})")
        sys.exit(0)
    try:
        cfg = load_rules()
        db = state_db()
        everyone = all_inboxes()
        hub = cfg.get("hub_inbox")
        inboxes = a.account or everyone
        failed = 0
        for acct in inboxes:
            try:
                sources = hub_sources(cfg, db, everyone) if acct == hub and cfg["mailbox_label"] else None
                if sources and not (a.apply or a.dry_run) or sources and a.days:
                    # only the Gmails that really forward (anything in the window, or 30 days
                    # for filters / label clean-up), so no empty Mailbox folders
                    look = a.days if (a.apply or a.dry_run) else 30
                    rsvc = _service(acct, "read")
                    sources = [(n, ad) for n, ad in sources if rsvc.users().messages().list(
                        userId="me", q=f"deliveredto:{ad} newer_than:{look}d",
                        maxResults=1).execute().get("messages")]
                if a.sync_colors:
                    sync_colors(acct, cfg, not a.check)
                elif a.sync_filters:
                    sync_filters(acct, cfg, not a.check, sources)
                elif a.delete_old_labels:
                    delete_old_labels(acct, cfg, not a.check, db, sources)
                else:
                    organize(acct, cfg, a.days, a.apply, db, sources, a.inbox_only)
            except Exception as e:
                failed += 1
                msg = str(e)
                if "insufficient" in msg.lower() or "403" in msg:
                    msg = "token lacks the permission — run --authorize " + acct
                log(f"{acct}: ✗ {msg[:200]}")
        if a.apply and not any(os.path.exists(modify_token_path(x)) for x in inboxes):
            log("no inbox is authorized yet — run --authorize <inbox> first")
        sys.exit(1 if failed and failed == len(inboxes) else 0)
    finally:
        accounts_config.release_lock(lock)


if __name__ == "__main__":
    main()
