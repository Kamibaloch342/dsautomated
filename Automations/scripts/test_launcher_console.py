#!/usr/bin/env python3
"""
test_launcher_console.py — the launcher's console badges and the headless toggle,
without opening a Tk window.

Why this exists: with four placers running, the console interleaved their lines with no
way to tell which profile said what, so the only readable view was one profile at a time.
Every line now carries a short colour-coded badge under the multi-process views.

Run:  python scripts/test_launcher_console.py
"""
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail and not cond else ""))


class Stub:
    """Just the palette + the badge methods lifted off the real class — no Tk."""
    CYAN, GREEN, PURPLE, AMBER, ACCENT, DIM = "#7dcfff", "#73daca", "#bb9af7", "#e0af68", "#7aa2f7", "#565f89"


def load_methods():
    """Pull the badge helpers out of launcher.py without importing Tk."""
    src = open(os.path.join(AUTO, "gui", "launcher.py"), encoding="utf-8").read()
    out = {}
    for name in ("_source_badges", "_badge_for"):
        m = re.search(rf"\n    def {name}\(self.*?\n(?=    def |    _[A-Z_]+ = |\n    # )", src, re.S)
        assert m, f"{name} not found in launcher.py"
        body = "\n".join(l[4:] if l.startswith("    ") else l for l in m.group(0).splitlines())
        exec(compile(body, "launcher.py", "exec"), out)
    m = re.search(r"_MULTI_SOURCE_VIEWS = \(([^)]*)\)", src)
    assert m, "_MULTI_SOURCE_VIEWS not found"
    out["_MULTI_SOURCE_VIEWS"] = tuple(x.strip().strip('"\'') for x in m.group(1).split(",") if x.strip())
    return out


def main():
    ns = load_methods()
    for k in ("_source_badges", "_badge_for"):
        setattr(Stub, k, ns[k])
    s = Stub()

    # every placer and tracker is distinguishable
    badges = {src: s._badge_for(src) for src in
              ("orders_DM_COUPON", "orders_DM_RESELLER", "orders_HS_COUPON", "orders_HS_RESELLER",
               "tracking_hoank", "tracking_hathoro")}
    labels = [b[0] for b in badges.values()]
    tags = [b[1] for b in badges.values()]
    check("every placer and tracker gets a badge", all(b is not None for b in badges.values()), str(badges))
    check("the four placers are distinguishable from each other", len(set(labels[:4])) == 4, str(labels[:4]))
    check("all six sources have distinct labels AND distinct colour tags",
          len(set(labels)) == 6 and len(set(tags)) == 6, str(labels))
    check("badges are a fixed width so the column lines up", len({len(l) for l in labels}) == 1, str({l: len(l) for l in labels}))

    # closing / other sources
    check("closing runs are badged per account",
          s._badge_for("bulk_hoank")[0].endswith("HO") and s._badge_for("messages_hathoro")[0].endswith("HA"),
          str((s._badge_for("bulk_hoank"), s._badge_for("messages_hathoro"))))
    check("an unknown profile still gets a readable badge rather than nothing",
          s._badge_for("orders_XX_NEW") is not None, str(s._badge_for("orders_XX_NEW")))
    check("a blank source gets no badge", s._badge_for("") is None and s._badge_for(None) is None)

    # the views that mix processes
    check("badges are shown under All / Orders / Tracking / Closing",
          set(ns["_MULTI_SOURCE_VIEWS"]) == {"All", "Orders", "Tracking", "Closing"}, str(ns["_MULTI_SOURCE_VIEWS"]))

    # 'Orders' really is the all-four-placers view (the filter predicate in launcher.py)
    src = open(os.path.join(AUTO, "gui", "launcher.py"), encoding="utf-8").read()
    check("the 'Orders' view matches every placer, so all four stream together",
          'if filt == "Orders":\n            return source.startswith("orders_")' in src)

    # headless toggle: config round-trip through the launcher's own helpers
    import importlib.util
    spec = importlib.util.spec_from_file_location("lp", os.path.join(AUTO, "gui", "launcher.py"))
    # the module imports Tk at import time, so read/write the file the same way it does instead
    cfg_path = os.path.join(AUTO, "bot_config.json")
    before = json.load(open(cfg_path))
    tmp = os.path.join(tempfile.mkdtemp(), "bot_config.json")
    json.dump(before, open(tmp, "w"), indent=4)
    d = json.load(open(tmp))
    d.setdefault("chrome", {})["headless"] = True
    json.dump(d, open(tmp, "w"), indent=4)
    back = json.load(open(tmp))
    check("headless writes into chrome.headless and survives a round-trip",
          back["chrome"]["headless"] is True and back["profiles"] == before["profiles"],
          "profile settings must not be disturbed")
    check("the real bot_config still parses and keeps its profiles",
          isinstance(before.get("profiles"), dict) and len(before["profiles"]) == 4)

    # the toggle must not restart anything
    check("toggling headless does not stop or start a process",
          "_toggle_headless" in src and "_stop" not in src.split("def _toggle_headless")[1].split("def ")[0])
    check("the remote set_headless command keeps the checkbox in sync",
          "_headless_var.set(bool(args.get(\"on\")))" in src)

    # ── attributed checkout switch ───────────────────────────────────────
    import os as _os
    sys.path.insert(0, AUTO)
    from utils.helpers import page_checkout_wanted
    _os.environ["ZUHRA_PAGE_CHECKOUT"] = "1"
    check("attributed checkout: env var turns it on", page_checkout_wanted() is True)
    _os.environ["ZUHRA_PAGE_CHECKOUT"] = "0"
    check("attributed checkout: env var turns it off even if config says on", page_checkout_wanted() is False)
    _os.environ.pop("ZUHRA_PAGE_CHECKOUT")
    check("attributed checkout: default comes from bot_config checkout.page_checkout",
          page_checkout_wanted() == bool((json.load(open(os.path.join(AUTO, "bot_config.json"))).get("checkout") or {}).get("page_checkout")))

    api = open(os.path.join(AUTO, "checkout", "true_api.py"), encoding="utf-8").read()
    i, j = api.index("if page_checkout_wanted():"), api.index('elif "RESELLER" in active_profile:')
    check("the switch is evaluated BEFORE the profile branches", i < j)
    check("with the switch on, the order API submit is skipped entirely",
          "_submit_order_api" not in api[i:j])
    check("the page path still receives the payment method (Net30 resellers keep their terms)",
          "payment_method=payment_method" in api[i:j])
    check("cart, coupons, shipping and address still go through the API (unchanged)",
          all(k in api for k in ("/v1/cart/{cart_id}/items", "/v1/cart/{cart_id}/promo",
                                 "/v1/cart/{cart_id}/shippingmethod", "/v1/cart/{cart_id}/shippingaddress")))

    check("the launcher exposes the toggle next to Headless",
          "_pagechk_var" in src and "Attributed checkout" in src and "_toggle_page_checkout" in src)
    check("toggling attributed checkout restarts nothing",
          "_stop" not in src.split("def _toggle_page_checkout")[1].split("def ")[0])
    check("it writes to checkout.page_checkout, not chrome.*",
          'cfg.setdefault("checkout", {})["page_checkout"]' in src)

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
