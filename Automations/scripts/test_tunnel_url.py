#!/usr/bin/env python3
"""test_tunnel_url.py — the dashboard's public address must be the real one, and must reach the owner.

21 Sep 2026: data/cloudflared.url said https://api.trycloudflare.com (cloudflared's control
endpoint, matched first by the old regex); the quick tunnel itself logged 8,343 QUIC errors that
day; localtunnel changed its address 79 times and nothing told anyone.

Run:  python scripts/test_tunnel_url.py
"""
import importlib.util
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail and not cond else ""))


def main():
    sp = importlib.util.spec_from_file_location("cloud_agent", os.path.join(HERE, "cloud_agent.py"))
    CA = importlib.util.module_from_spec(sp); sp.loader.exec_module(CA)
    src = open(os.path.join(HERE, "cloud_agent.py"), encoding="utf-8").read()

    print("\n── the address is the tunnel's, never the control endpoint ──")
    log = ("2026-09-21T08:00:01Z INF Requesting new quick Tunnel on trycloudflare.com...\n"
           "2026-09-21T08:00:02Z INF +--------------------------------------------------------+\n"
           "2026-09-21T08:00:02Z INF |  https://asks-surrounded-retailer-passion.trycloudflare.com  |\n"
           "2026-09-21T08:00:03Z ERR failed to dial to edge ... https://api.trycloudflare.com/tunnel\n")
    check("cloudflared: the real quick-tunnel host is picked", CA.pick_tunnel_url("cloudflared", log) == "https://asks-surrounded-retailer-passion.trycloudflare.com")
    check("cloudflared: api.trycloudflare.com alone is not an address", CA.pick_tunnel_url("cloudflared", "ERR https://api.trycloudflare.com/tunnel") == "")
    check("the LAST address wins when a log holds several", CA.pick_tunnel_url("cloudflared", log + "INF |  https://new-name-here.trycloudflare.com  |\n") == "https://new-name-here.trycloudflare.com")
    lt = "your url is: https://kamran-ds-dashboard.loca.lt\nyour url is: https://heavy-starfish-24.loca.lt\n"
    check("localtunnel: the address it actually gave, not the one we asked for", CA.pick_tunnel_url("localtunnel", lt) == "https://heavy-starfish-24.loca.lt")
    check("an unknown tunnel name yields nothing", CA.pick_tunnel_url("ngrok", lt) == "")

    print("\n── a changed address is announced once, on Telegram ──")
    sent = []
    CA._tg_send = lambda text: sent.append(text)
    agent = types.SimpleNamespace(state={}, _save_state=lambda: None)
    cls = getattr(CA, [n for n in dir(CA) if isinstance(getattr(CA, n), type) and hasattr(getattr(CA, n), "_announce_tunnel")][0])
    cls._announce_tunnel(agent, "cloudflared", "https://a.trycloudflare.com")
    cls._announce_tunnel(agent, "cloudflared", "https://a.trycloudflare.com")
    cls._announce_tunnel(agent, "cloudflared", "https://b.trycloudflare.com")
    check("two distinct addresses → two messages, the repeat is silent", len(sent) == 2 and "b.trycloudflare.com" in sent[1], str(sent))
    check("the message names the tunnel and the address", sent and "Dashboard address (cloudflared)" in sent[0])
    check("the last address is remembered in the agent state", agent.state.get("cloudflared_last_url") == "https://b.trycloudflare.com")

    print("\n── the process itself ──")
    check("cloudflared runs over HTTP/2 (QUIC failed ~600 times an hour)", '"--protocol", "http2"' in src)
    check("only the new process's output is scanned (start_at) and both tunnels are scraped",
          "start_at = os.path.getsize(logp)" in src and 'args=(name, logp, start_at)' in src)
    check("the old first-match scrape is gone", "_scrape_cf_url" not in src)

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL)); sys.exit(1)


if __name__ == "__main__":
    main()
