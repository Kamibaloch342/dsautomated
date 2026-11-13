#!/usr/bin/env python3
"""
One-off, 14 Nov 2026 — owner's label tree for the Gmail organizer.

The owner asked for ONLY three label groups in every inbox — Amazon (10), Zoro (7), MaxWarehouse (2),
all out of the inbox — every other label the organizers created removed (in the hub mkamrankhan342:
every label), and the rest of the mail in the inbox. config/gmail_rules.json already holds the new
rules; this script moves each inbox from the 13 Nov tree to the new one:

  1. rename the 13 Nov labels to the owner's names (a rename keeps every mail on the label) and merge
     the ones that fold together (Cancel requests → Cancelled Orders, Late shipment warnings →
     Account Health; a label that already exists under the new name is merged into);
  2. re-file the mail of labels that split or leave the tree with the new rules (first match wins);
  3. take every mail of the new tree out of the inbox (old unread to-do mail older than 7 days read);
  4. put back into the inbox the mail that leaves the tree AND that an organizer took out of the inbox:
     Gmail's own history since the 13 Nov survey (INBOX removed — UNREAD back too when it went in the
     same change), mail that arrived after the 13 Nov filters, and the 31 Oct organizer's archive log;
  5. delete the filters that fed removed labels (a forwarding filter is re-created without the label)
     and the labels themselves (retried; a label Gmail refuses to delete is stripped off its mail first).
The owner's own hand-made labels that were folded on 13 Nov go too, their few unmatched mails to the
inbox; one with more than 100 unmatched mails stays (reported). Mail is never deleted.

  ~/.zuhra/venv/bin/python scripts/archive/gmail_restructure_2026_11_14.py --dry-run qurtdis
  ~/.zuhra/venv/bin/python scripts/archive/gmail_restructure_2026_11_14.py --apply qurtdis abecomllc26 …
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from collections import Counter

_AUTO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _AUTO)
sys.path.insert(0, os.path.join(_AUTO, "scripts"))
import gmail_organizer as g  # noqa: E402

HUB = "mkamrankhan342"
SURVEY_DIR = "/private/tmp/claude-501/-Users-kamibaloch342--claude-spares-spare-BWz5ym/d454697b-60f7-469d-b6d7-9cc8ab20a39b/scratchpad"
FILTERS_AT = 1794566700  # 13 Nov 2026 15:45 PKT — the 13 Nov filters went live a few minutes later
OLD_RULES = os.path.expanduser("~/.zuhra/backups/gmail_rules_2026-11-14_before_owner_tree.json")

RENAME = {
    "Amazon/Sold - Ship now": "Amazon/Sold, Ship Now",
    "Amazon/Messages sent to customers": "Amazon/Customer Messages Sent",
    "Amazon/Customer messages": "Amazon/Customer Messages Received",
    "Amazon/Returns & refunds": "Amazon/Returns",
    "Amazon/Listings": "Amazon/Listings Created & Updates",
    "Amazon/Cancelled orders": "Amazon/Cancelled Orders",
    "Amazon/Cancel requests": "Amazon/Cancelled Orders",
    "Amazon/Account health": "Amazon/Account Health",
    "Amazon/Late shipment warnings": "Amazon/Account Health",
    "Amazon/A-to-z claims": "Amazon/Claims (A-to-z & Chargebacks)",
    "Zoro/Order received": "Zoro/Orders Received",
    "Zoro/Shipped": "Zoro/Orders Shipped",
    "Zoro/Receipts & invoices": "Zoro/Invoices & Cash Sales Receipts",
    "Zoro/Order changes & cancellations": "Zoro/Order Cancellations",
    "Zoro/Refunds & credit memos": "Zoro/Refunds & Credit Memos",
}
# new-tree labels whose 13 Nov content splits under the new rules
SPLIT = {"Amazon/Returns", "Amazon/Account Health", "Zoro/Order Cancellations"}
# the owner's hand-made labels folded on 13 Nov (from the survey); the hub loses every label
HAND_MADE = {
    "qurtdis": ["Sold Amazon Emails", "Refunds Initiated", "Email Sent"],
    "abecomllc26": ["Amazon/Amazon Emails", "Amazon/Captain Amz Reviews", "Amazon/Listings created",
                    "Amazon/Refunnd initiated", "Amazon/Return Authorizations", "Grainger Orders",
                    "Zoro/Net30 Payments", "Zoro/Zoro CC Receipt", "Zoro/Zoro Delivered Notifs",
                    "Zoro/Zoro Order Placed", "Zoro/Zoro Shipment Notifcation", "Zoro/Zoro Cases", "Shopify"],
    "asad_amazon_us": ["Zoro Order's", "Zoro Shipment's", "Zoro invoice's", "zoro refund",
                       "Credit Services", "Zoro Cases"],
}
KEEP_OWNER = {"abecomllc26": {"kamran_emails"}, "asad_amazon_us": {"inquiries@rajarstore.com"}}
HAND_MADE_MAX_LEFTOVER = 100
# 31 Oct organizer: its archive=true rules whose mail now leaves the tree (suffix of its label)
OLD_ORG_OUT = ("/Promotions", "/Reports", "/Walmart/New orders", "/Zoro/Case receipts")


def log(m):
    print(m, flush=True)


def retry(fn, what, tries=6):
    from googleapiclient.errors import HttpError
    for t in range(tries):
        try:
            return fn()
        except (HttpError, TimeoutError, ConnectionError, OSError) as e:
            status = getattr(getattr(e, "resp", None), "status", None)
            if isinstance(e, HttpError) and status not in (429, 500, 502, 503, 504):
                raise
            if t == tries - 1:
                raise
            log(f"   {what}: {str(e)[:90]} — retry in {10 * 2 ** t}s")
            time.sleep(10 * 2 ** t)


def start_history_id(acct, svc):
    """A historyId from before the 13 Nov rebuild: the survey's for the four main inboxes, else the
    smallest historyId among mail delivered on 12 Nov (a message's historyId only grows)."""
    p = os.path.join(SURVEY_DIR, f"survey_{acct}.json")
    if os.path.exists(p):
        return int(json.load(open(p))["profile"]["historyId"])
    # only mail no organizer ever touched (no user label at all) still carries its delivery-time
    # historyId; anything labelled since has a newer one
    ids = g.search(svc, "after:2026/11/08 before:2026/11/13")
    hs = []
    for m in ids[:3000]:
        got = svc.users().messages().get(userId="me", id=m, format="minimal").execute()
        if not any(x.startswith("Label_") for x in got.get("labelIds", [])):
            hs.append(int(got["historyId"]))
            if len(hs) >= 40:
                break
    return min(hs) if hs else None


def inbox_history(svc, start):
    """{msg: unread_removed_too} for every mail whose INBOX label was removed since `start`."""
    out, page, n = {}, None, 0
    while True:
        kw = {"userId": "me", "startHistoryId": start, "historyTypes": ["labelRemoved"],
              "maxResults": 500}
        if page:
            kw["pageToken"] = page
        r = retry(lambda: svc.users().history().list(**kw).execute(), "history")
        for h in r.get("history", []):
            for lr in h.get("labelsRemoved", []):
                labs = lr.get("labelIds", [])
                if "INBOX" in labs:
                    mid = lr["message"]["id"]
                    out[mid] = out.get(mid, False) or ("UNREAD" in labs)
        n += 1
        page = r.get("nextPageToken")
        if not page:
            return out


def first_match(svc, rules, label_id):
    """msg → new label, for the mail of one label, by the new rules (first match wins)."""
    got = {}
    for r in rules:
        for m in g.search(svc, f"({r['query']}){g.SKIP}", label_ids=[label_id]):
            got.setdefault(m, r)
    return got


def restructure(acct, apply, cfg):
    t0 = time.time()
    svc = g._service(acct, "modify")
    if svc is None:
        log(f"{acct}: no modify token — skipped")
        return
    rules = cfg["rules"]
    tree = g.tree_labels(cfg)
    old_cfg = json.load(open(OLD_RULES))
    old_read = {r["label"]: bool(r.get("read")) for r in old_cfg["rules"]}
    labels = svc.users().labels().list(userId="me").execute()["labels"]
    by_name = {l["name"]: l["id"] for l in labels if l["type"] == "user"}

    # ---- 0) what an organizer took out of the inbox (needed before anything moves)
    start = start_history_id(acct, svc)
    hist = inbox_history(svc, start) if start else {}
    db = sqlite3.connect(g.STATE_DB, timeout=120)
    old_arch = {m for m, lab in db.execute(
        "SELECT msg_id, label FROM organized WHERE account=? AND archived_at IS NOT NULL", (acct,))
        if lab.endswith(OLD_ORG_OUT)}
    inbox_now = set(g.search(svc, None, label_ids=["INBOX"]))
    log(f"{acct}: history since {start}: {len(hist)} mails left the inbox; 31-Oct organizer archive "
        f"(now outside the tree) {len(old_arch)}; in inbox now {len(inbox_now)}")

    # ---- 1) renames / merges (plan)
    renames, merges = [], []
    for old, new in RENAME.items():
        if old not in by_name:
            continue
        if new in by_name or any(n == new for _, n in renames):
            merges.append((old, new))
        else:
            renames.append((old, new))

    # labels that leave: everything outside the tree, except the owner's own labels we keep
    hand = set(HAND_MADE.get(acct, []))
    keep_owner = KEEP_OWNER.get(acct, set())
    leaving = [n for n in by_name if n not in tree and n not in RENAME and n not in keep_owner]
    # mail that must be re-filed: split labels, labels that leave (categories only — not Mailbox/…,
    # and from the 31 Oct organizer only its categories that leave the tree), hand-made labels
    def is_category(n):
        if n.startswith("Mailbox"):
            return False
        if n.startswith(("Kamran", "ZUHRA")):
            return n.endswith(OLD_ORG_OUT) or n.endswith(("/Money", "/Walmart", "/Shopify", "/Zoro/Support"))
        return True
    refile_src = [n for n in leaving if is_category(n)]
    log(f"{acct}: rename {len(renames)}, merge {len(merges)}, labels to remove {len(leaving)} "
        f"(re-file {len(refile_src)}), owner labels kept {sorted(keep_owner & set(by_name))}")

    if apply:
        for old, new in renames:
            retry(lambda: svc.users().labels().patch(userId="me", id=by_name[old], body={"name": new}).execute(),
                  f"rename {old}")
            by_name[new] = by_name.pop(old)
            log(f"   renamed {old} → {new}")
        ids = g.ensure_labels(svc, {r["label"] for r in rules}, cfg.get("label_colors"))
        by_name.update({k: v for k, v in ids.items() if k in tree})
        for old, new in merges:
            src, dst = by_name[old], by_name[new]
            have = set(g.search(svc, None, label_ids=[dst]))
            move = [m for m in g.search(svc, None, label_ids=[src]) if m not in have]
            g.modify(svc, move, add=[dst], factory=lambda: g._service(acct, "modify"))
            log(f"   merged {old} → {new}: {len(move)} mails")
            leaving.append(old)
    else:
        for old, new in renames + merges:
            log(f"   would {'rename' if (old, new) in renames else 'merge'} {old} → {new}")

    # ---- 2) re-file by the new rules
    out_of_tree, hand_rest, moved = set(), set(), Counter()
    rev = {}
    for o, n in RENAME.items():
        rev.setdefault(n, o)
    for name in sorted(set(refile_src) | SPLIT):
        lid = by_name.get(name) or (None if apply else by_name.get(rev.get(name, "")))
        if not lid:
            continue
        members = set(g.search(svc, None, label_ids=[lid]))
        if not members:
            continue
        got = first_match(svc, rules, lid)
        groups = {}
        for m, r in got.items():
            if r["label"] != name:
                groups.setdefault(r["label"], []).append(m)
        rest = members - set(got)
        if name in SPLIT:  # a kept label: what no rule wants any more leaves it
            out_of_tree |= rest
        elif name in hand and len(rest) > HAND_MADE_MAX_LEFTOVER:
            log(f"   keeping owner label '{name}': {len(rest)} of its {len(members)} mails fit none of the 19 labels")
            leaving.remove(name)
            continue
        else:
            out_of_tree |= rest
            if name in hand:
                hand_rest |= rest
        for target, ms in groups.items():
            tid = by_name.get(target)
            if tid and name not in SPLIT:  # leaving label: mail already carrying its target needs nothing
                both = set(g.search(svc, None, label_ids=[lid, tid]))
                ms = [m for m in ms if m not in both]
            moved[target] += len(ms)
            if apply and ms:
                rm = [lid] if name in SPLIT else None
                g.modify(svc, ms, add=[tid], remove=rm, factory=lambda: g._service(acct, "modify"))
        if apply and name in SPLIT and rest:
            g.modify(svc, list(rest), remove=[lid], factory=lambda: g._service(acct, "modify"))
        log(f"   {name}: {len(members)} mails → " + (", ".join(f"{t.split('/', 1)[-1]} {len(v)}" for t, v in groups.items()) or "—")
            + f"; outside the tree {len(rest)}")

    # ---- 3) new-tree mail out of the inbox
    to_archive, to_read = [], []
    unread_rules = {r["label"] for r in rules if not r.get("read")}
    for name in sorted({r["label"] for r in rules}):
        lid = by_name.get(name) or (None if apply else by_name.get(rev.get(name, "")))
        if not lid:
            continue
        inb = g.search(svc, None, label_ids=["INBOX", lid])
        if not inb:
            continue
        old = set(g.search(svc, "older_than:7d", label_ids=["INBOX", lid])) if name in unread_rules else set(inb)
        to_read += [m for m in inb if m in old]
        to_archive += [m for m in inb if m not in old]
    log(f"   new-tree mail still in the inbox: {len(to_archive) + len(to_read)} (of them marked read: {len(to_read)})")

    # ---- 4) back to the inbox
    after_filters = set()
    if acct in ("qurtdis", "abecomllc26", "asad_amazon_us", HUB):
        for name in refile_src:
            if name in by_name and not name.startswith(("Kamran", "ZUHRA")):
                after_filters |= set(g.search(svc, f"after:{FILTERS_AT}", label_ids=[by_name[name]]))
    back = {m for m in out_of_tree - inbox_now if m in hist or m in after_filters or m in old_arch}
    # the owner's hand-made labels that go: their unmatched mail would vanish → inbox
    back |= (hand_rest - inbox_now)
    unread_back = {m for m in back if hist.get(m)}
    for name in refile_src:
        if old_read.get(name) and name in by_name:
            unread_back |= back & set(g.search(svc, f"after:{FILTERS_AT}", label_ids=[by_name[name]]))
    log(f"   back to the inbox: {len(back)} (unread again: {len(unread_back)}); "
        f"re-filed into the tree: {dict(moved)}")

    # ---- 5) filters + labels
    fsvc = g._service(acct, "settings")
    gone_ids = {by_name[n] for n in leaving if n in by_name}
    drop, reforward = [], []
    if fsvc is not None:
        for f in fsvc.users().settings().filters().list(userId="me").execute().get("filter", []):
            act = f.get("action", {})
            if set(act.get("addLabelIds", [])) & gone_ids:
                (reforward if act.get("forward") else drop).append(f)
    log(f"   filters: delete {len(drop)}, re-create without the old label {len(reforward)}; "
        f"labels: delete {len(leaving)}")

    if apply:
        fac = lambda: g._service(acct, "modify")  # noqa: E731
        g.modify(svc, to_archive, remove=["INBOX"], factory=fac)
        g.modify(svc, to_read, remove=["INBOX", "UNREAD"], factory=fac)
        g.modify(svc, list(back & unread_back), add=["INBOX", "UNREAD"], factory=fac)
        g.modify(svc, list(back - unread_back), add=["INBOX"], factory=fac)
        for f in reforward:
            act = dict(f["action"])
            act["addLabelIds"] = [x for x in act.get("addLabelIds", []) if x not in gone_ids]
            if not act["addLabelIds"]:
                act.pop("addLabelIds")
            fsvc.users().settings().filters().create(userId="me", body={"criteria": f["criteria"], "action": act}).execute()
            fsvc.users().settings().filters().delete(userId="me", id=f["id"]).execute()
        from googleapiclient.errors import HttpError
        for f in drop:
            try:
                retry(lambda: fsvc.users().settings().filters().delete(userId="me", id=f["id"]).execute(), "filter delete")
            except HttpError as e:  # already gone / Gmail refuses ("Precondition check failed"): note it, go on
                log(f"   filter {f['id'][:12]}… {f.get('criteria')} not deleted: {str(e)[-70:]}")
        for name in sorted(set(leaving), key=lambda n: -n.count("/")):
            lid = by_name.get(name)
            if not lid:
                continue
            try:
                retry(lambda: svc.users().labels().delete(userId="me", id=lid).execute(), f"delete {name}", tries=4)
            except Exception as e:  # Gmail keeps refusing a big label: strip it off its mail, then delete
                log(f"   {name}: delete refused ({str(e)[:60]}) — taking it off its mail first")
                g.modify(svc, g.search(svc, None, label_ids=[lid]), remove=[lid], factory=fac)
                retry(lambda: svc.users().labels().delete(userId="me", id=lid).execute(), f"delete {name}")
        log(f"{acct}: done in {int(time.time() - t0)} s")
    else:
        log(f"{acct}: dry run ({int(time.time() - t0)} s) — would remove: "
            + ", ".join(sorted(n for n in leaving if not n.startswith(('Kamran', 'Mailbox', 'ZUHRA'))))
            + f" + Kamran/ZUHRA {sum(n.startswith(('Kamran', 'ZUHRA')) for n in leaving)}"
            + f" + Mailbox {sum(n.startswith('Mailbox') for n in leaving)}")


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    ap.add_argument("accounts", nargs="+")
    a = ap.parse_args()
    cfg = g.load_rules()
    for acct in a.accounts:
        try:
            restructure(acct, a.apply, cfg)
        except Exception as e:
            log(f"{acct}: ✗ {str(e)[:300]}")


if __name__ == "__main__":
    main()
