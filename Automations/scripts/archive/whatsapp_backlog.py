#!/usr/bin/env python3
"""
whatsapp_backlog.py — alerts that were held back from the WhatsApp queue (20 Oct 2026).

When the gateway went from one alert per ~20 s to instant, 883 alerts older than 30 minutes (some
8 h old) were set aside in whatsapp_gateway/backlog_*.json instead of being fired into the groups
in one burst. Nothing is sent unless asked.

    python scripts/whatsapp_backlog.py              # what is held, per group
    python scripts/whatsapp_backlog.py --send       # hand them to the gateway (oldest first)
    python scripts/whatsapp_backlog.py --discard    # delete the held file(s)
"""
import collections
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GW = os.path.join(os.path.dirname(HERE), "whatsapp_gateway")
sys.path.insert(0, os.path.dirname(HERE))

NAMES = {}
try:
    from utils.whatsapp import DEFAULT_GROUPS
    NAMES = {v: k for k, v in DEFAULT_GROUPS.items()}
except Exception:
    pass


def main():
    files = sorted(glob.glob(os.path.join(GW, "backlog_*.json")))
    if not files:
        print("nothing held")
        return 0
    items = []
    for f in files:
        items += [(f, m) for m in (json.load(open(f)).get("queue") or [])]
    c = collections.Counter(NAMES.get(m["to"], m["to"]) for _, m in items)
    print(f"{len(items)} held alert(s) in {len(files)} file(s):")
    for k, n in c.most_common():
        print(f"  {n:4d}  {k}")
    if "--discard" in sys.argv:
        for f in files:
            os.remove(f)
        print("deleted")
        return 0
    if "--send" not in sys.argv:
        print("nothing sent (--send to deliver them, --discard to delete them)")
        return 0
    import requests
    ok = 0
    for f, m in sorted(items, key=lambda fm: fm[1].get("at", 0)):
        body = {"to": m["to"], "text": m.get("text", ""), "caption": m.get("caption", ""),
                "priority": m.get("priority", 5)}
        if m.get("image_base64"):
            body["image_base64"] = m["image_base64"]
        r = requests.post("http://127.0.0.1:3005/send", json=body, timeout=30)
        ok += r.status_code == 200
        time.sleep(0.05)
    for f in files:
        os.rename(f, f + ".sent")
    print(f"handed {ok} of {len(items)} to the gateway")
    return 0


if __name__ == "__main__":
    sys.exit(main())
