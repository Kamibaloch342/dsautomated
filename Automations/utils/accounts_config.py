"""
accounts_config.py — loader for config/accounts.json, the SINGLE source of truth
for account/token/inbox lists, plus the shared default-months helper.

Every script that needs an account list imports from here instead of hardcoding.
If the JSON is missing/corrupt the built-in fallbacks (a frozen copy of the
lists as of 2026-07) keep everything running — but fix the JSON, don't rely
on the fallback.
"""
import os
import json
import datetime as _dt

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(_AUTO, "config", "accounts.json")

_FALLBACK = {
    "accounts": {
        "HOANK":   {"report_port": 9331, "fedex_port": 9332, "sc_port": 9331, "pd_key": "hoank",
                    "earnings_inbox": "qurtdis",
                    "zoro_preferred": ["salesmercentile1", "inhur_llc"]},
        "Hathoro": {"report_port": 9333, "fedex_port": 9334, "sc_port": 9333, "pd_key": "hathoro",
                    "earnings_inbox": "abecomllc26",
                    "zoro_preferred": ["salesmercentile", "wrenchlyllc"]},
    },
    "gmail_tokens": ["qurtdis", "abecomllc26", "asad_amazon_us", "inhur_llc", "wrenchlyllc"],
    "gmail_accounts": ["qurtdis", "abecomllc26", "asad_amazon_us", "inhur_llc", "wrenchlyllc",
                       "abecomllc02"],
    "zoro_accounts": ["salesmercentile", "salesmercentile1", "abecomllc26", "asad_amazon_us",
                      "inhur_llc", "wrenchlyllc", "toolzsolutionsllc220", "qurtdis", "abecomllc02",
                      "kamibaloch342", "kamran_va_342", "kamranwork342", "robstark771",
                      "salestreendfitinc", "toolzsolutionsllc"],
    "walmart": {"zoro_port": 9361, "profile_dir": "port_9361",
                "sc_port": 9362, "sc_profile_dir": "port_9362"},
    # Carrier API credentials. These keys MUST be listed here: _load() keeps only the keys
    # of _FALLBACK, so before 26 Sep 2026 a fedex_api block pasted into accounts.json was
    # silently dropped and only the FEDEX_* environment variables ever worked.
    "fedex_api": {"client_id": "", "client_secret": ""},
    "ups_api": {"client_id": "", "client_secret": ""},
    # Gmail token name → the mailbox address behind it (Zoro's guest order lookup needs the
    # e-mail of the Zoro account that placed the order). Resolved from each token's Gmail
    # profile on 27 Sep 2026.
    "gmail_addresses": {
        "abecomllc02": "abecomllc02@gmail.com", "abecomllc26": "abecomllc26@gmail.com",
        "asad_amazon_us": "asad.amazon.us@gmail.com", "excurellc": "excurellc@gmail.com",
        "inhur_llc": "inuhrecomllc5@gmail.com", "kamibaloch342": "kamibaloch342@gmail.com",
        "kamran_va_342": "kamran.va.342@gmail.com", "kamranwork342": "kamranwork342@gmail.com",
        "qurtdis": "qurtdistribution.llc@gmail.com", "robstark771": "robstark771@gmail.com",
        "salesmercentile1": "salesmercentile1@gmail.com", "salesmercentile": "salesmercentile@gmail.com",
        "salestreendfitinc": "salestreendfitinc@gmail.com", "toolzsolutionsllc220": "toolzsolutionsllc220@gmail.com",
        "toolzsolutionsllc": "toolzsolutionsllc@gmail.com", "wrenchly": "wrenchlysolutionsllc@gmail.com",
        "wrenchlyllc": "wrenchlysolutionsllc@gmail.com",
    },
    # Placer profile → the Zoro inbox (token name) that profile buys with (owner, 27 Sep 2026).
    "zoro_profile_inboxes": {"HS_COUPON": "inhur_llc", "HS_RESELLER": "asad_amazon_us",
                             "DM_RESELLER": "abecomllc26", "DM_COUPON": "salesmercentile"},
    # Zoro guest tracking sweep (engine/zoro_tracking.py, scripts/zoro_delivery_check.py).
    "zoro_tracking": {"transport": "auto", "port": 9225, "delay_s": 0.4, "workers": 4, "days": 60,
                      "recheck_hours": 20, "late_days": 2, "stalled_days": 3},
}

_cache = None


def _load():
    global _cache
    if _cache is None:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            _cache = {k: data.get(k) or _FALLBACK[k] for k in _FALLBACK}
        except Exception as e:
            print(f"[accounts_config] ⚠️ could not read {CONFIG_PATH} ({e}) — using built-in fallback")
            _cache = dict(_FALLBACK)
    return _cache


def accounts() -> dict:
    """{'HOANK': {report_port, fedex_port, sc_port, pd_key}, ...}"""
    return _load()["accounts"]


def fedex_api_credentials() -> dict:
    """Returns {'client_id': '...', 'client_secret': '...'} if configured in accounts.json or env."""
    cfg = _load().get("fedex_api") or {}
    client_id = os.environ.get("FEDEX_API_KEY") or os.environ.get("FEDEX_CLIENT_ID") or cfg.get("client_id", "")
    client_secret = os.environ.get("FEDEX_SECRET_KEY") or os.environ.get("FEDEX_CLIENT_SECRET") or cfg.get("client_secret", "")
    return {"client_id": str(client_id).strip(), "client_secret": str(client_secret).strip()}


def ups_api_credentials() -> dict:
    """UPS Track API (developer.ups.com) OAuth client credentials, from accounts.json → ups_api
    or the UPS_CLIENT_ID / UPS_CLIENT_SECRET environment variables. Empty strings when unset."""
    cfg = _load().get("ups_api") or {}
    client_id = os.environ.get("UPS_CLIENT_ID") or cfg.get("client_id", "")
    client_secret = os.environ.get("UPS_CLIENT_SECRET") or cfg.get("client_secret", "")
    return {"client_id": str(client_id).strip(), "client_secret": str(client_secret).strip()}


def gmail_addresses() -> dict:
    """{token name: mailbox address} merged over the built-in table."""
    out = dict(_FALLBACK["gmail_addresses"])
    out.update(_load().get("gmail_addresses") or {})
    return out


def zoro_profile_inboxes() -> dict:
    """{placer profile: Gmail token name of the Zoro account it buys with}."""
    out = dict(_FALLBACK["zoro_profile_inboxes"])
    out.update(_load().get("zoro_profile_inboxes") or {})
    return out


def zoro_tracking_cfg() -> dict:
    """Settings for the Zoro guest tracking sweep, merged over the defaults."""
    out = dict(_FALLBACK["zoro_tracking"])
    out.update(_load().get("zoro_tracking") or {})
    return out


def active_locks(prefixes=None) -> list:
    """[(name, pid)] for every data/<name>.lock whose holder pid is alive. Used to answer
    "is a closing / bulk / messaging run in progress?" before doing DB maintenance."""
    out = []
    try:
        d = os.path.join(_AUTO, "data")
        for fn in os.listdir(d):
            if not fn.endswith(".lock"):
                continue
            name = fn[:-5]
            if prefixes and not name.startswith(tuple(prefixes)):
                continue
            try:
                pid = int((open(os.path.join(d, fn)).read() or "0").strip() or 0)
            except Exception:
                pid = 0
            if pid and _pid_alive(pid):
                out.append((name, pid))
    except Exception:
        pass
    return out



def gmail_tokens() -> list:
    """Token names for Gmail auth (messaging / debug .eml capture)."""
    return list(_load()["gmail_tokens"])


def gmail_accounts() -> list:
    """Amazon-side inboxes (earnings / returns / buyer messages, keyed by Amazon id)."""
    return list(_load()["gmail_accounts"])


def zoro_accounts() -> list:
    """Zoro-side inboxes (order confirmations / cost, keyed by WB id)."""
    return list(_load()["zoro_accounts"])


def walmart() -> dict:
    """Walmart→Zoro pipeline config, merged over the built-in fallback so a
    JSON that predates a new key (e.g. sc_port) still gets its default:
      zoro_port/profile_dir   — the dedicated Zoro-account Chrome (checkout)
      sc_port/sc_profile_dir  — the Walmart Seller Center Chrome (scraper)
    {'zoro_port': 9361, 'profile_dir': 'port_9361',
     'sc_port': 9362, 'sc_profile_dir': 'port_9362'}."""
    out = dict(_FALLBACK["walmart"])
    out.update(_load()["walmart"] or {})
    return out


def earnings_inboxes() -> list:
    """Every store's earnings inbox ('Sold, ship now' recipients) — the archive
    build fetches earnings from ALL of these (it used to fetch only qurtdis,
    which left Hathoro/abecomllc26 earnings unarchived)."""
    seen, out = set(), []
    for cfg in _load()["accounts"].values():
        e = cfg.get("earnings_inbox")
        if e and e not in seen:
            seen.add(e); out.append(e)
    return out


def preferred_inboxes(account: str) -> dict:
    """{'earnings': [...], 'zoro': [...]} preference tiers for one store.
    Live-Gmail lookups search these FIRST (the inboxes this store actually
    uses), then fall back to every inbox — so the common case costs 1-2
    searches, not 16, but nothing is ever unreachable."""
    cfg = _load()["accounts"].get(account, {})
    earn = [cfg["earnings_inbox"]] if cfg.get("earnings_inbox") else []
    return {"earnings": earn, "zoro": list(cfg.get("zoro_preferred") or [])}


def default_months(n: int = 2) -> str:
    """Last `n` calendar months incl. the current one, e.g. '2026-06,2026-07'.
    Replaces the hardcoded month defaults that silently excluded every new
    month's orders from FedEx re-check / SC scrape."""
    d = _dt.date.today().replace(day=1)
    months = []
    for _ in range(n):
        months.append(f"{d:%Y-%m}")
        d = (d - _dt.timedelta(days=1)).replace(day=1)
    return ",".join(reversed(months))


def _pid_alive(pid: int) -> bool:
    # os.kill(pid, 0) is a *probe* on Unix but on Windows it TERMINATES the process —
    # which would kill the very lock-holder we're checking. Use the cross-platform helper.
    from utils.proc import pid_exists
    return pid_exists(pid)


def acquire_lock(name: str):
    """Single-instance pid lockfile in data/. Returns (acquired, holder_pid).
    A dead holder's lock is taken over; a live holder blocks the new run.
    (The July-1 duplicate-order incident had 'no local lock' as a root cause.)"""
    path = os.path.join(_AUTO, "data", f"{name}.lock")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            try:
                pid = int((open(path).read() or "0").strip() or 0)
            except ValueError:
                pid = 0
            if pid and pid != os.getpid() and _pid_alive(pid):
                return False, pid
        with open(path, "w") as f:
            f.write(str(os.getpid()))
        return True, os.getpid()
    except Exception as e:
        # Lock bookkeeping must never break the pipeline itself — but say so loudly.
        print(f"[accounts_config] ⚠️ lock '{name}' error ({e}) — continuing WITHOUT lock")
        return True, os.getpid()


def release_lock(name: str):
    path = os.path.join(_AUTO, "data", f"{name}.lock")
    try:
        pid = int((open(path).read() or "0").strip() or 0)
        if pid == os.getpid():
            os.remove(path)
    except Exception:
        pass
