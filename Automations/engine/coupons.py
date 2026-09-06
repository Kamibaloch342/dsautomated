"""
==========================================================================
  ZORO COUPON GENERATOR — Interactive
==========================================================================
  Flow:
    1. Create temp email via mail.tm API
    2. Show email → you sign up on Zoro footer manually
    3. Script polls mail.tm for confirmation email → opens link
    4. Script polls for coupon email → extracts code
    5. Uploads to Google Sheet

  Usage:
    python3 coupon_generator.py              # 5 coupons, one-by-one
    python3 coupon_generator.py --count 10   # 10 coupons
    python3 coupon_generator.py --bulk       # all emails upfront
==========================================================================
"""

import os
import re
import json
import time
import random
import socket
import string
import argparse
import subprocess
import webbrowser
import threading
import sys
import select
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests as http_requests
from google.oauth2 import service_account
from googleapiclient.discovery import build as gbuild

try:
    from rich.console import Console
    from rich.panel import Panel
    console = Console()
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    class _Fake:
        def print(self, *a, **k): print(*[re.sub(r'\[.*?\]', '', str(x)) for x in a])
    console = _Fake()

# ── Concurrency plumbing ───────────────────────────────────────────────────
# Bulk mode runs one worker per temp mailbox, so every line of output has to say
# which mailbox it came from or the log is unreadable. The tag is thread-local,
# which lets the existing console.print() calls stay untouched.
_LOG_CTX = threading.local()


class _TaggedConsole:
    """Prefixes each printed line with the calling thread's worker tag."""

    def __init__(self, inner):
        self._inner = inner

    def print(self, *args, **kwargs):
        tag = getattr(_LOG_CTX, "tag", "")
        if tag and args and isinstance(args[0], str):
            args = (f"{tag} {args[0]}",) + args[1:]
        return self._inner.print(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


console = _TaggedConsole(console)

# ===========================================================================
#  CONFIGURATION
# ===========================================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTOMATIONS_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_ROOT = os.path.dirname(AUTOMATIONS_DIR)  # Development_Unit/

# Google Sheets config
GOOGLE_SHEET_ID = "1aNMSPkBjmUUgZNO45KXtMd88ddeBYJbqDxOknaEVooM"
GOOGLE_SHEET_TAB = "Coupons"

# Reuse the same service-account path resolution that the order bot uses.
# This avoids hardcoding "Final Automation" and works on macOS + Windows.
import importlib.util as _imp_util
_config_spec = _imp_util.spec_from_file_location("config", os.path.join(AUTOMATIONS_DIR, "utils", "config.py"))
_config_mod = _imp_util.module_from_spec(_config_spec)
# We need these globals for the config module's top-level code
import sys as _sys
_sys.modules["config"] = _config_mod
_config_spec.loader.exec_module(_config_mod)
GOOGLE_SA_FILE = getattr(_config_mod, "GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE", None)
if not GOOGLE_SA_FILE:
    # Fallback if import fails — search common locations
    _fallback_paths = [
        os.path.join(AUTOMATIONS_DIR, "gen-lang-client-0493912626-fbfdbabc0441.json"),
        os.path.join(PROJECT_ROOT, "Final Automation", "gen-lang-client-0493912626-fbfdbabc0441.json"),
        os.path.join(PROJECT_ROOT, "gen-lang-client-0493912626-fbfdbabc0441.json"),
    ]
    GOOGLE_SA_FILE = next((p for p in _fallback_paths if os.path.exists(p)), _fallback_paths[0])
GOOGLE_SA_FILE = os.path.normpath(GOOGLE_SA_FILE)

# Coupon extraction patterns
COUPON_CODE_RE = re.compile(r"Enter code at checkout:\s*([A-Z0-9]+)", re.IGNORECASE)
COUPON_FALLBACK_RE = re.compile(r"\b([A-Z0-9]{8,14})\b")

# ===========================================================================
#  TEMP-EMAIL PROVIDERS  (multi-provider, pure HTTP — no browser)
# ===========================================================================

class DeadMailboxError(RuntimeError):
    """The provider says this mailbox does not exist.

    Distinct from a transient network/API error: no amount of polling brings
    the mailbox back, so the caller must stop waiting rather than burn its
    whole timeout re-asking the same question.
    """


# Domain → "ok" | "dead" | "unknown". Cached for the whole run so bulk mode
# probes each domain once, not once per mailbox.
_MX_CACHE = {}


def _domain_mx_status(domain, timeout=6):
    """Can this domain's mail server be reached on port 25?

    "ok"      — TCP connect accepted: the server is up and can take mail.
    "dead"    — connection refused or domain doesn't resolve: NOTHING can
                deliver there, a mailbox on this domain is a black hole.
    "unknown" — connect timed out: many networks block outbound port 25, so a
                timeout proves nothing about the server. Treat as usable.
    """
    if domain in _MX_CACHE:
        return _MX_CACHE[domain]
    host = None
    try:
        out = subprocess.run(["dig", "+short", "MX", domain],
                             capture_output=True, text=True, timeout=8).stdout.split()
        if len(out) >= 2:
            host = out[1].rstrip(".")
    except Exception:
        pass
    host = host or f"mail.{domain}"
    try:
        with socket.create_connection((host, 25), timeout=timeout):
            status = "ok"
    except socket.timeout:
        status = "unknown"
    except OSError:
        status = "dead"
    _MX_CACHE[domain] = status
    return status


# ===========================================================================
#  DOMAIN SUCCESS TRACKING
# ===========================================================================
# A live mail server only proves the mailbox CAN receive mail — it says nothing
# about whether Zoro will actually send to it. Zoro silently drops signups on
# domains it has blocklisted: the form submits fine and no confirmation is ever
# sent (verified 2026-08-07 by SMTP-delivering to inboxes that had "received
# nothing" — delivery took ~6s, so the mailbox was never the problem).
#
# The only way to know a domain is burned is to measure it, so every attempt is
# recorded here and domains that stop converting are dropped from selection.
_DOMAIN_STATS_FILE = os.path.join(AUTOMATIONS_DIR, "data", "coupon_domain_stats.json")
_STATS_LOCK = threading.RLock()
_RECENT_WINDOW = 25      # attempts per domain kept for the "recent" rate
_MIN_SAMPLE = 8          # don't judge a domain on fewer attempts than this
_BURNED_RATE = 0.15      # recent success below this ⇒ treat as burned
_BATCH_WARN_SIZE = 20    # warn above this many signups in one burst


def _load_domain_stats():
    # Only a missing/corrupt file is tolerated — a broad except here previously
    # hid a NameError and silently reported "no stats" on a perfectly good file.
    try:
        with open(_DOMAIN_STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _record_domain_result(email, success):
    """Record one signup→coupon outcome for the address's domain."""
    domain = (email or "").split("@")[-1].strip().lower()
    if not domain:
        return
    with _STATS_LOCK:
        stats = _load_domain_stats()
        d = stats.setdefault(domain, {"ok": 0, "fail": 0, "recent": []})
        d["ok" if success else "fail"] = d.get("ok" if success else "fail", 0) + 1
        recent = list(d.get("recent", []))
        recent.append(1 if success else 0)
        d["recent"] = recent[-_RECENT_WINDOW:]
        try:
            os.makedirs(os.path.dirname(_DOMAIN_STATS_FILE), exist_ok=True)
            with open(_DOMAIN_STATS_FILE, "w", encoding="utf-8") as f:
                json.dump(stats, f, indent=2)
        except Exception as e:
            console.print(f"[dim]   (could not save domain stats: {e})[/dim]")


def _domain_recent_rate(domain, stats=None):
    """(rate, sample_size) over the recent window. rate is None below _MIN_SAMPLE."""
    stats = _load_domain_stats() if stats is None else stats
    recent = (stats.get(domain) or {}).get("recent", [])
    if len(recent) < _MIN_SAMPLE:
        return None, len(recent)
    return sum(recent) / len(recent), len(recent)


def _is_domain_burned(domain, stats=None):
    rate, _ = _domain_recent_rate(domain, stats)
    return rate is not None and rate < _BURNED_RATE


def print_domain_report():
    """Print the per-domain conversion table (also shown at end of a run)."""
    stats = _load_domain_stats()
    if not stats:
        console.print("[dim]   (no domain stats recorded yet)[/dim]")
        return
    console.print(f"\n[bold cyan]📊 Domain conversion (signup → coupon)[/bold cyan]")
    console.print(f"[dim]   {'domain':22s} {'ok':>5s} {'fail':>6s} {'all':>6s} {'recent':>8s}[/dim]")
    rows = sorted(stats.items(), key=lambda kv: -(kv[1].get("ok", 0) + kv[1].get("fail", 0)))
    for dom, d in rows:
        ok, fail = d.get("ok", 0), d.get("fail", 0)
        total = ok + fail
        if not total:
            continue
        rate, n = _domain_recent_rate(dom, stats)
        recent_s = f"{rate:.0%} /{n}" if rate is not None else f"—   /{n}"
        flag = "  ⛔ burned" if _is_domain_burned(dom, stats) else ""
        console.print(f"   {dom:22s} {ok:5d} {fail:6d} {100*ok/total:5.0f}% {recent_s:>8s}{flag}")


def _pick_live_tempmailio_domain():
    """Return a temp-mail.io domain whose mail server is confirmed reachable.

    temp-mail.io keeps offering domains whose inbound mail server is down
    (seen live 2026-08-04: gmeenramy.com and olipii.com refused every SMTP
    connection while /email/new happily minted addresses on them). Zoro's
    confirmation mail to such an address can never arrive, so the domain must
    be vetted BEFORE the address is signed up anywhere.

    Domains Zoro has stopped mailing (see _record_domain_result) are skipped
    while any non-burned domain remains; if every domain is burned the least-bad
    one is still used, since a low rate beats no coupons at all.

    Returns None when nothing is confirmed "ok" but some domains are merely
    "unknown" (this network probably blocks outbound port 25 — filtering on a
    blind probe would reject everything). Raises when every domain is
    confirmed dead, so create_temp_email() moves to the next provider.
    """
    r = http_requests.get("https://api.internal.temp-mail.io/api/v3/domains", timeout=15)
    r.raise_for_status()
    domains = [d["name"] for d in r.json().get("domains", [])
               if d.get("type", "public") == "public"]
    random.shuffle(domains)
    stats = _load_domain_stats()

    live, burned = [], []
    for d in domains:
        st = _domain_mx_status(d)
        if st == "dead":
            console.print(f"[dim]   (skipping domain {d} — its mail server is down)[/dim]")
            continue
        if st == "ok":
            (burned if _is_domain_burned(d, stats) else live).append(d)

    if live:
        # Weighted random, NOT always-the-best: piling every signup onto one
        # domain burns it faster and stops the others ever being re-sampled
        # (a domain Zoro un-blocks would stay unused forever). Unmeasured
        # domains weigh 0.5 so new ones get tried; the floor keeps a weak
        # domain in the rotation at low volume so its stats stay current.
        def _weight(dom):
            rate, _ = _domain_recent_rate(dom, stats)
            return max(0.05, rate if rate is not None else 0.5)
        weights = [_weight(d) for d in live]
        return random.choices(live, weights=weights, k=1)[0]

    if burned:
        best = max(burned, key=lambda d: (_domain_recent_rate(d, stats)[0] or 0))
        console.print(f"[yellow]   ⚠️  Every live domain is converting poorly with Zoro — "
                      f"using best of a bad set: {best}[/yellow]")
        return best

    if domains and all(_MX_CACHE.get(d) == "dead" for d in domains):
        raise RuntimeError("temp-mail.io: every offered domain's mail server is down")
    return None


def _create_tempmailio():
    """Create email via temp-mail.io API on a domain that can receive mail."""
    domain = _pick_live_tempmailio_domain()
    if domain:
        local = ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))
        r = http_requests.post("https://api.internal.temp-mail.io/api/v3/email/new",
                               json={"name": local, "domain": domain}, timeout=15)
    else:
        # Port 25 blocked on this network — can't vet domains, take pot luck.
        r = http_requests.post("https://api.internal.temp-mail.io/api/v3/email/new", timeout=15)
    r.raise_for_status()
    data = r.json()
    email = data["email"]
    return {
        "provider": "tempmailio",
        "email": email,
        "token": data["token"],
        "password": "CpnAuto2025!",
    }


def _create_guerrilla():
    """Create email via GuerrillaMail API. Returns account dict."""
    r = http_requests.get(
        "https://api.guerrillamail.com/ajax.php?f=get_email_address",
        timeout=15)
    r.raise_for_status()
    data = r.json()
    email = data["email_addr"]
    sid = data["sid_token"]
    # Set a custom alias so we get a predictable email
    local = ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))
    r = http_requests.get(
        f"https://api.guerrillamail.com/ajax.php?f=set_email_user&email_user={local}&sid_token={sid}",
        timeout=15)
    r.raise_for_status()
    alias_data = r.json()
    email = alias_data.get("email_addr", email)
    sid = alias_data.get("sid_token", sid)
    return {
        "provider": "guerrilla",
        "email": email,
        "sid": sid,
        "password": "CpnAuto2025!",
    }


def _create_mailtm():
    """Create email via mail.tm API. Returns account dict."""
    api_base = "https://api.mail.tm"
    password = "CpnAuto2025!"

    r = http_requests.get(f"{api_base}/domains", timeout=15)
    r.raise_for_status()
    domains = r.json().get("hydra:member", [])
    if not domains:
        raise RuntimeError("mail.tm: no domains available")
    # Same dead-mail-server vetting as temp-mail.io ("unknown" passes: it just
    # means this network blocks outbound port 25, not that the server is down).
    domain = next((d["domain"] for d in domains
                   if _domain_mx_status(d["domain"]) != "dead"), None)
    if domain is None:
        raise RuntimeError("mail.tm: every offered domain's mail server is down")

    local = ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))
    email = f"{local}@{domain}"

    r = http_requests.post(f"{api_base}/accounts",
                           json={"address": email, "password": password},
                           timeout=15)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"mail.tm: account creation {r.status_code}")

    r = http_requests.post(f"{api_base}/token",
                           json={"address": email, "password": password},
                           timeout=15)
    r.raise_for_status()
    token = r.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    return {
        "provider": "mailtm",
        "email": email,
        "headers": headers,
        "password": password,
    }


def create_temp_email():
    """Try temp-email providers in order of preference.
    Returns account dict with keys: provider, email, password, + provider-specific keys.

    Every candidate mailbox is polled once before it is handed back. temp-mail.io
    will happily return 200 + a plausible address from /email/new for a mailbox it
    never actually provisioned; every later poll then fails with "Email not found".
    Creation is the only useful moment to catch that — once the address has been
    registered on Zoro the confirmation mail is bound to it, so a dead mailbox
    discovered later is simply a lost coupon with no recovery.
    """
    # mail.tm is preferred over guerrillamail as the fallback: its domains
    # (e.g. web-library.net) are far less likely to be rejected by Zoro's signup
    # form than guerrillamail's transparently disposable guerrillamailblock.com.
    errors = []
    for fn, name in [(_create_tempmailio, "temp-mail.io"),
                     (_create_mailtm, "mail.tm"),
                     (_create_guerrilla, "guerrillamail.com")]:
        try:
            account = fn()
        except Exception as e:
            errors.append(f"{name}: {e}")
            console.print(f"[yellow]   ⚠️  {name} failed, trying next...[/yellow]")
            continue

        try:
            _verify_mailbox(account)
        except Exception as e:
            errors.append(f"{name}: {account['email']} not pollable ({e})")
            console.print(
                f"[yellow]   ⚠️  {name} returned an unusable mailbox "
                f"({account['email']}: {e}) — trying next provider...[/yellow]")
            continue

        console.print(f"[green]   ✅ Temp email: {account['email']}  ({name})[/green]")
        return account
    raise RuntimeError(f"All temp email providers failed: {'; '.join(errors)}")


def _poll_tempmailio(account, exclude_ids):
    """Poll temp-mail.io inbox. Returns normalized message or None."""
    email = account["email"]
    r = http_requests.get(
        f"https://api.internal.temp-mail.io/api/v3/email/{email}/messages",
        timeout=15)
    # 400 + code 101 ("Email not found") means the mailbox does not exist server
    # side — it was never provisioned, or has been purged. Signal that as fatal
    # so callers stop polling instead of retrying for the full timeout.
    if r.status_code == 400:
        try:
            body = r.json()
        except ValueError:
            body = {}
        if body.get("code") == 101:
            raise DeadMailboxError(
                f"temp-mail.io does not have a mailbox for {email}")
    r.raise_for_status()
    msgs = r.json()
    for msg in msgs:
        if msg["id"] in exclude_ids:
            continue
        return {
            "id": msg["id"],
            "subject": msg.get("subject", ""),
            "from": msg.get("from", ""),
        }
    return None


def _poll_guerrilla(account, exclude_ids):
    """Poll GuerrillaMail inbox. Returns normalized message or None."""
    sid = account["sid"]
    r = http_requests.get(
        f"https://api.guerrillamail.com/ajax.php?f=get_email_list&offset=0&sid_token={sid}",
        timeout=15)
    r.raise_for_status()
    msgs = r.json().get("list", [])
    for msg in msgs:
        mid = str(msg["mail_id"])
        if mid in exclude_ids:
            continue
        return {
            "id": mid,
            "subject": msg.get("mail_subject", ""),
            "from": msg.get("mail_from", ""),
        }
    return None


def _poll_mailtm(account, exclude_ids):
    """Poll mail.tm inbox. Returns normalized message or None."""
    headers = account["headers"]
    r = http_requests.get("https://api.mail.tm/messages", headers=headers, timeout=15)
    r.raise_for_status()
    msgs = r.json().get("hydra:member", [])
    for msg in msgs:
        if msg["id"] in exclude_ids:
            continue
        return {
            "id": msg["id"],
            "subject": msg.get("subject", ""),
            "from": msg.get("from", ""),
        }
    return None


_POLL_FUNCS = {
    "tempmailio": _poll_tempmailio,
    "guerrilla": _poll_guerrilla,
    "mailtm": _poll_mailtm,
}


def _verify_mailbox(account):
    """Poll a freshly created mailbox once to prove it really exists.

    Raises whatever the provider's poll raises (notably DeadMailboxError) so
    create_temp_email() can reject the address and move to the next provider.
    """
    poll_fn = _POLL_FUNCS.get(account["provider"])
    if poll_fn is None:
        raise RuntimeError(f"Unknown provider: {account['provider']}")
    poll_fn(account, set())


def wait_for_email(account, subject_contains=None, timeout=120, exclude_ids=None):
    """Poll inbox until a new email arrives. Returns normalized message dict or None."""
    if exclude_ids is None:
        exclude_ids = set()
    provider = account["provider"]
    poll_fn = _POLL_FUNCS.get(provider)
    if not poll_fn:
        raise RuntimeError(f"Unknown provider: {provider}")

    filter_desc = f"'{subject_contains}'" if subject_contains else "any new email"
    console.print(f"[cyan]   ⏳ Polling inbox for {filter_desc} (up to {timeout}s)...[/cyan]")
    start = time.time()
    poll_count = 0
    while time.time() - start < timeout:
        try:
            msg = poll_fn(account, exclude_ids)
            poll_count += 1
            if msg:
                subj = msg["subject"]
                console.print(f"[green]   ✅ Email arrived: {subj}[/green]")
                return msg
            if poll_count % 5 == 0:
                elapsed = int(time.time() - start)
                console.print(f"[dim]   ... {elapsed}s elapsed, still waiting[/dim]")
        except DeadMailboxError as e:
            # Permanent: the mailbox is gone, so the mail can never land there.
            # Bail out now instead of re-asking for the rest of the timeout.
            console.print(f"[red]   ❌ Dead mailbox: {e}[/red]")
            console.print("[red]      Giving up on this address — polling cannot fix it.[/red]")
            return None
        except Exception as e:
            console.print(f"[yellow]   ⚠️ Poll error: {e}[/yellow]")
        time.sleep(8)

    console.print(f"[red]   ❌ Timed out waiting for email ({timeout}s)[/red]")
    return None


def get_email_body(account, msg_id):
    """Fetch full message body (HTML) from the provider."""
    provider = account["provider"]
    if provider == "tempmailio":
        r = http_requests.get(
            f"https://api.internal.temp-mail.io/api/v3/message/{msg_id}",
            timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("body_html") or data.get("body_text", "")

    elif provider == "guerrilla":
        sid = account["sid"]
        r = http_requests.get(
            f"https://api.guerrillamail.com/ajax.php?f=fetch_email&email_id={msg_id}&sid_token={sid}",
            timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("mail_body", "")

    elif provider == "mailtm":
        headers = account["headers"]
        r = http_requests.get(f"https://api.mail.tm/messages/{msg_id}", headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        html_parts = data.get("html", [])
        if html_parts:
            return html_parts[0] if isinstance(html_parts, list) else str(html_parts)
        return data.get("text", "")

    else:
        raise RuntimeError(f"Unknown provider: {provider}")


def _clean_anchor_text(raw):
    """Strip HTML tags and decode common entities from anchor inner HTML."""
    text = re.sub(r'<[^>]+>', '', raw)
    text = text.replace('&nbsp;', ' ').replace('&#160;', ' ')
    text = re.sub(r'&[a-z]+;', ' ', text)
    return ' '.join(text.split()).strip().lower()


def mailtm_extract_confirm_link(html_body):
    """Find the Zoro email confirmation link using a scoring system."""
    anchor_pattern = re.compile(
        r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL
    )
    all_anchors = anchor_pattern.findall(html_body)
    console.print(f"[dim]   Found {len(all_anchors)} links in confirmation email[/dim]")

    best_link = None
    best_score = -1
    best_text = ""

    nav_words = ['deals', 'power tools', 'accessories', 'create account',
                 'most orders', 'shop now', 'categories', 'brands',
                 'unsubscribe', 'privacy', 'terms', 'contact']

    for href, anchor_text in all_anchors:
        if 'click.e.zoro.com' not in href:
            continue
        text = _clean_anchor_text(anchor_text)
        score = 0

        if 'confirm' in text and 'email' in text:
            score += 100
        elif 'verify' in text and 'email' in text:
            score += 95
        elif 'confirm' in text:
            score += 50
        elif 'verify' in text:
            score += 45
        elif 'activate' in text:
            score += 40

        if '10%' in text or '10% off' in text:
            score += 20
        if 'save' in text:
            score += 30
        if 'subscribe' in text:
            score += 15

        if 'below' in text or 'looking' in text:
            score -= 15

        word_count = len(text.split()) if text else 0
        if word_count >= 4:
            score += 10
        elif word_count < 1:
            score -= 20

        if any(nw in text for nw in nav_words):
            score -= 30

        if score > best_score:
            best_score = score
            best_link = href
            best_text = text

    if best_link and best_score > 0:
        console.print(f"[dim]   Selected link (score {best_score}): '{best_text[:60]}'[/dim]")
        return best_link

    # Fallback: skip first click.e.zoro.com link (logo), take second
    zoro_click_links = [href for href, _ in all_anchors if 'click.e.zoro.com' in href]
    if len(zoro_click_links) >= 2:
        return zoro_click_links[1]
    if zoro_click_links:
        return zoro_click_links[0]

    # Last resort: any zoro.com link with confirm/verify in URL
    for href, _ in all_anchors:
        if 'zoro.com' in href and ('confirm' in href.lower() or 'verify' in href.lower()):
            return href

    # Plain-href fallback
    plain_links = re.findall(r'href=["\']([^"\']+)["\']', html_body, re.IGNORECASE)
    for link in plain_links:
        if 'click.e.zoro.com' in link:
            return link

    return None


def _get_cta_candidate_links(html_body, already_tried):
    """Return a short list of CTA-likely links from the email, excluding already-tried."""
    anchor_pattern = re.compile(
        r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL
    )
    cta_keywords = ['confirm', 'verify', 'activate', 'subscribe', '10%', 'email']
    candidates = []
    for href, anchor_text in anchor_pattern.findall(html_body):
        if href in already_tried or 'click.e.zoro.com' not in href:
            continue
        text = _clean_anchor_text(anchor_text)
        if any(kw in text for kw in cta_keywords):
            candidates.append((href, text))
    return candidates


# Common English words that look like coupon codes but aren't
_FALSE_POSITIVE_WORDS = {
    "EXCLUSIONS", "CHECKOUT", "SHIPPING", "DISCOUNT", "PURCHASE",
    "CUSTOMER", "DELIVERY", "PRODUCTS", "BUSINESS", "SERVICES",
    "INCLUDED", "EXCLUDES", "REGISTER", "UNSUBSCRIBE", "SUBSCRIBE",
    "CONTINUE", "COMPLETE", "EXPIRING", "SELECTED", "CATEGORY",
    "EVERYDAY", "SUPPLIES", "ORIGINAL", "GRAINGER", "AMERICAS",
    "RESERVED", "CLICKING", "RECEIVED", "REQUIRED", "DOWNLOAD",
    "SETTINGS", "SECURITY", "FEEDBACK", "OVERVIEW", "FEATURES",
    "HELVETICA", "VERDANAW", "ARIALBLK",
}


def mailtm_extract_coupon_code(html_body):
    """Extract the coupon code from the Zoro coupon email HTML."""
    text = re.sub(r'<[^>]+>', ' ', html_body)
    text = re.sub(r'\s+', ' ', text)

    match = COUPON_CODE_RE.search(text)
    if match:
        return match.group(1).strip()

    candidates = COUPON_FALLBACK_RE.findall(text)
    for c in candidates:
        has_letter = any(ch.isalpha() for ch in c)
        has_digit = any(ch.isdigit() for ch in c)
        if (has_letter and has_digit
                and c.upper() not in _FALSE_POSITIVE_WORDS
                and len(c) >= 8
                and not c.startswith("HTTP")):
            return c

    return None


# ===========================================================================
#  CONFIRMATION LINK — just HTTP GET, no browser needed
# ===========================================================================

# Opening the confirm link in a real browser is a last-resort backup. Under bulk
# concurrency an unlucky run would otherwise fling one tab per mailbox at the
# user, so the whole run shares a small budget.
_BROWSER_LOCK = threading.Lock()
_MAX_BROWSER_FALLBACKS = 5
_browser_fallbacks = 0


def _browser_fallback(url):
    """Open `url` in a browser if the run's tab budget allows. Returns True if opened."""
    global _browser_fallbacks
    with _BROWSER_LOCK:
        if _browser_fallbacks >= _MAX_BROWSER_FALLBACKS:
            console.print(
                f"[dim]   (browser fallback budget of {_MAX_BROWSER_FALLBACKS} used up — "
                f"not opening another tab)[/dim]")
            return False
        _browser_fallbacks += 1
    webbrowser.open(url)
    time.sleep(5)
    return True


def visit_confirm_link(url):
    """Visit the Zoro confirmation link via HTTP request."""
    console.print(f"[cyan]   🔗 Confirming: {url[:80]}...[/cyan]")
    try:
        r = http_requests.get(url, timeout=30, allow_redirects=True,
                              headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                                                     "Chrome/146.0.0.0 Safari/537.36"})
        body = r.text.lower()
        if any(kw in body for kw in ["subscribed", "confirmed", "inbox awesome", "email confirmed"]):
            console.print("[green]   ✅ Email confirmed![/green]")
            return True, r.text
        else:
            console.print("[yellow]   ⚠️ HTTP confirm got unclear response — opening in browser as backup[/yellow]")
            _browser_fallback(url)
            return True, r.text
    except Exception as e:
        console.print(f"[yellow]   ⚠️ HTTP request failed ({e}) — opening in browser[/yellow]")
        _browser_fallback(url)
        return True, ""


# ===========================================================================
#  GOOGLE SHEETS — sole output
# ===========================================================================

_sheets_svc = None
_pending_upload_file = os.path.join(SCRIPT_DIR, "coupon_upload_pending.txt")

# The googleapiclient service wraps a single httplib2.Http connection, which is
# NOT thread-safe — concurrent .execute() calls corrupt it. Bulk mode runs many
# workers, so every use of the service (and the pending-file append that backs
# it up) is serialised. Writes are rare and fast, so this costs nothing.
_SHEET_LOCK = threading.RLock()


def _get_sheets_service():
    global _sheets_svc
    if _sheets_svc is None:
        creds = service_account.Credentials.from_service_account_file(
            GOOGLE_SA_FILE,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        _sheets_svc = gbuild("sheets", "v4", credentials=creds, cache_discovery=False)
    return _sheets_svc


def _append_coupon_to_sheet(code):
    global _sheets_svc
    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        try:
            svc = _get_sheets_service()
            svc.spreadsheets().values().append(
                spreadsheetId=GOOGLE_SHEET_ID,
                range=f"{GOOGLE_SHEET_TAB}!A1",
                valueInputOption="RAW",
                insertDataOption="OVERWRITE",
                body={"values": [[code]]},
            ).execute()
            console.print(f"[green]   📊 Uploaded to Google Sheet[/green]")
            return True
        except Exception as e:
            err = str(e).lower()
            retriable = (
                isinstance(e, (BrokenPipeError, ConnectionError, TimeoutError, OSError))
                or "broken pipe" in err
                or "connection" in err
                or "timed out" in err
                or "ssl" in err
            )
            if retriable and attempt < max_attempts:
                wait_s = min(2 ** attempt, 10)
                # Force a fresh auth/http session on retry.
                _sheets_svc = None
                console.print(
                    f"[yellow]   ⚠️ Sheet upload failed (attempt {attempt}/{max_attempts}): {e} — retrying in {wait_s}s[/yellow]"
                )
                time.sleep(wait_s)
                continue
            raise


def _count_sheet_coupons():
    """Return how many coupons are already in the sheet."""
    try:
        svc = _get_sheets_service()
        result = svc.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"{GOOGLE_SHEET_TAB}!A:A",
        ).execute()
        rows = result.get("values", [])
        return len(rows)
    except Exception:
        return 0


def save_coupon(code, email_used):
    """Upload coupon directly to Google Sheet."""
    console.print(f"[green]   💾 Coupon: {code}[/green]")
    with _SHEET_LOCK:
        try:
            _append_coupon_to_sheet(code)
        except Exception as e:
            # Don't kill the whole run because Sheets had a transient network issue.
            with open(_pending_upload_file, "a", encoding="utf-8") as f:
                f.write(f"{code}\t{email_used}\t{int(time.time())}\n")
            console.print(
                f"[yellow]   ⚠️ Sheet upload failed ({e}). Saved locally to {os.path.basename(_pending_upload_file)}[/yellow]"
            )


def _stdin_allows_prompt():
    try:
        return bool(sys.stdin) and sys.stdin.isatty()
    except Exception:
        return False


# ===========================================================================
#  PROCESS ONE EMAIL (confirmation + coupon extraction)
# ===========================================================================

def wait_for_enter_or_email(account, timeout=300, allow_enter=True):
    """Wait for EITHER user pressing Enter OR a new email arriving."""
    if not allow_enter or not _stdin_allows_prompt():
        console.print(f"[dim]   Non-interactive mode: waiting for email only (up to {timeout}s)[/dim]")
        return wait_for_email(account, subject_contains=None, timeout=timeout)

    result = {"msg": None, "done": False}
    poll_count = [0]

    def poll_inbox():
        start = time.time()
        while not result["done"] and time.time() - start < timeout:
            try:
                msg = wait_for_email(account, subject_contains=None, timeout=0)
                poll_count[0] += 1
                elapsed = int(time.time() - start)
                if msg:
                    result["msg"] = msg
                    result["done"] = True
                    console.print(f"\n[green]   📬 Email received: {msg.get('subject', '')}! Auto-proceeding...[/green]")
                    return
                elif poll_count[0] % 3 == 0:
                    console.print(f"[dim]   ... waiting {elapsed}s — no email yet (polling every 6s)[/dim]")
            except Exception as e:
                console.print(f"[yellow]   ⚠️ Poll error: {e}[/yellow]")
            time.sleep(6)
        if not result["done"]:
            console.print(f"[yellow]   ⏰ Polling timed out after {timeout}s[/yellow]")
            result["done"] = True

    t = threading.Thread(target=poll_inbox, daemon=True)
    t.start()

    console.print(f"[dim]   (Will auto-proceed when email arrives, or press ENTER to continue)[/dim]")

    while not result["done"]:
        ready, _, _ = select.select([sys.stdin], [], [], 1.0)
        if ready:
            sys.stdin.readline()
            if not result["done"]:
                console.print(f"[cyan]   ⏎ Enter pressed — continuing...[/cyan]")
                result["done"] = True
            break

    t.join(timeout=2)
    return result["msg"]


def process_one_email(email, password, account, prefetched_msg=None):
    """Wrapper that records the signup→coupon outcome for the address's domain."""
    code = _process_one_email_inner(email, password, account, prefetched_msg)
    _record_domain_result(email, bool(code))
    return code


def _process_one_email_inner(email, password, account, prefetched_msg=None):
    """After user signed up this email on Zoro footer:
    wait for confirmation → open link → wait for coupon → extract.
    Returns coupon code or None."""

    if prefetched_msg:
        confirm_msg = prefetched_msg
        console.print(f"[cyan]   📬 Using already-received email...[/cyan]")
    else:
        console.print(f"[cyan]   📬 Waiting for confirmation email...[/cyan]")
        confirm_msg = wait_for_email(account, subject_contains=None, timeout=180)
    if not confirm_msg:
        console.print("[red]   ❌ No email from Zoro arrived (3 min timeout)[/red]")
        return None

    msg_id = confirm_msg["id"]
    html_body = get_email_body(account, msg_id)
    confirm_link = mailtm_extract_confirm_link(html_body)

    if not confirm_link:
        console.print("[red]   ❌ No confirmation link found in email[/red]")
        return None

    confirmed, page_text = visit_confirm_link(confirm_link)

    if not confirmed:
        tried = {confirm_link}
        candidates = _get_cta_candidate_links(html_body, tried)
        for href, text in candidates[:3]:
            confirmed, page_text = visit_confirm_link(href)
            if confirmed:
                break

    if page_text:
        page_match = re.search(r'(?:code|coupon)[:\s]+([A-Z0-9]{8,14})', page_text, re.IGNORECASE)
        if page_match:
            candidate = page_match.group(1).upper()
            if candidate not in _FALSE_POSITIVE_WORDS:
                console.print(f"[bold green]   🎉 COUPON on page: {candidate}[/bold green]")
                save_coupon(candidate, email)
                return candidate

    seen_ids = {msg_id}
    console.print("[cyan]   🎟️  Waiting for coupon code email (up to 10 min)...[/cyan]")
    coupon_msg = wait_for_email(account, subject_contains=None,
                                timeout=600, exclude_ids=seen_ids)
    if not coupon_msg:
        console.print("[red]   ❌ Coupon email never arrived[/red]")
        return None

    coupon_html = get_email_body(account, coupon_msg["id"])
    coupon_code = mailtm_extract_coupon_code(coupon_html)

    if coupon_code:
        console.print(f"[bold green]   🎉 COUPON CODE: {coupon_code}[/bold green]")
        save_coupon(coupon_code, email)
        return coupon_code
    else:
        console.print("[red]   ❌ Could not extract coupon code from email[/red]")
        text = re.sub(r'<[^>]+>', ' ', coupon_html)[:500]
        console.print(f"[dim]   Preview: {text}[/dim]")
        return None


# ===========================================================================
#  INTERACTIVE MODE (one-by-one)
# ===========================================================================

def mode_interactive(count, allow_enter=True, max_email_retries=3):
    success = 0
    fail = 0

    for i in range(count):
        console.print(f"\n{'='*60}")
        console.print(f"[bold]  COUPON {i+1}/{count}  (✅ {success} | ❌ {fail})[/bold]")
        console.print(f"{'='*60}")

        code = None
        for attempt in range(max_email_retries):
            if attempt > 0:
                console.print(f"[yellow]   🔄 Retry {attempt + 1}/{max_email_retries}: creating fresh temp email...[/yellow]")

            console.print("[cyan]   📧 Creating temp email...[/cyan]")
            try:
                account = create_temp_email()
            except Exception as e:
                console.print(f"[red]   ❌ Temp email creation failed: {e}[/red]")
                break

            email = account["email"]
            password = account["password"]
            console.print(f"\n[bold yellow]   👉 EMAIL: {email}[/bold yellow]")
            console.print(f"[bold yellow]   Go to zoro.com footer → paste this email → click Sign Up[/bold yellow]")

            # Copy to clipboard (macOS pbcopy / Windows clip)
            try:
                clip_cmd = "clip" if sys.platform == "win32" else "pbcopy"
                subprocess.run([clip_cmd], input=email.encode(), check=True)
                console.print(f"[green]   📋 Copied to clipboard![/green]")
            except Exception:
                pass

            prefetched = wait_for_enter_or_email(account, allow_enter=allow_enter)

            code = process_one_email(email, password, account, prefetched_msg=prefetched)
            if code:
                success += 1
                break  # Success — exit retry loop

            # No code — either no confirmation email or no coupon email
            if attempt < max_email_retries - 1:
                console.print(f"[yellow]   ⚠️  No email received with {email} — trying new domain...[/yellow]")
            else:
                console.print(f"[red]   ❌ Failed after {max_email_retries} attempts[/red]")
                fail += 1

    return success, fail


# ===========================================================================
#  BULK MODE (all emails upfront)
# ===========================================================================

def _process_account_worker(idx, total, acc):
    """Run one mailbox end-to-end. Returns the coupon code or None.

    Never raises: one mailbox blowing up must not take down the pool, since the
    other workers are mid-flight on mailboxes that are already signed up.
    """
    # No square brackets in the tag — rich would parse them as markup.
    _LOG_CTX.tag = f"[dim]#{idx:>2}/{total}[/dim]"
    try:
        console.print(f"[cyan]▶ Start {acc['email']}[/cyan]")
        return process_one_email(acc["email"], acc["password"], acc)
    except Exception as e:
        console.print(f"[red]   ❌ Worker failed for {acc['email']}: {e}[/red]")
        return None
    finally:
        _LOG_CTX.tag = ""


def mode_bulk(count, allow_enter=True, bulk_signup_wait=180, max_workers=10):
    console.print(f"\n[bold cyan]📧 Phase 1: Creating {count} temp emails...[/bold cyan]")
    accounts = []
    for i in range(count):
        try:
            account = create_temp_email()
            accounts.append(account)
            console.print(f"   {i+1}. {account['email']}")
        except Exception as e:
            console.print(f"[red]   {i+1}. FAILED: {e}[/red]")

    if not accounts:
        console.print("[red]❌ No emails created[/red]")
        return 0, 0

    console.print(f"\n{'='*60}")
    console.print(f"[bold yellow]📋 SIGN UP THESE {len(accounts)} EMAILS ON ZORO.COM FOOTER:[/bold yellow]")
    console.print(f"{'='*60}")
    for i, acc in enumerate(accounts, 1):
        console.print(f"   {i}. {acc['email']}")

    # Zoro throttles bursts of signups from one IP: measured over this script's
    # own history, the first fifth of a batch converts ~57% while the rest drop
    # to 27-35%. Large batches don't get more coupons, just more failures.
    if len(accounts) > _BATCH_WARN_SIZE:
        console.print(
            f"\n[bold yellow]   ⚠️  {len(accounts)} signups in one burst: Zoro rate-limits "
            f"per IP, and later signups in a big batch convert far worse than early "
            f"ones.[/bold yellow]")
        console.print(
            f"[bold yellow]      Several runs of ~{_BATCH_WARN_SIZE} (spaced apart) "
            f"typically yield more coupons for the same effort.[/bold yellow]")

    if allow_enter and _stdin_allows_prompt():
        input(f"\n   Press ENTER after you've signed up ALL {len(accounts)} emails on Zoro... ")
    else:
        wait_seconds = max(0, int(bulk_signup_wait))
        # Every address not signed up before processing begins can only ever time
        # out — the confirmation mail it is waiting for was never requested.
        min_sane = 5 * len(accounts)
        if wait_seconds < min_sane:
            console.print(
                f"\n[bold red]   ⚠️  {wait_seconds}s is not enough to sign up {len(accounts)} "
                f"emails on Zoro (allow ~{min_sane}s, i.e. ~5s each).[/bold red]")
            console.print(
                "[bold red]      Addresses you don't sign up in time will just time out "
                "and be counted as failures — raise 'bulk wait' in the launcher.[/bold red]")
        if wait_seconds:
            console.print(
                f"\n[bold yellow]   Launcher mode: you have {wait_seconds}s to sign up all emails on Zoro before processing starts.[/bold yellow]"
            )
            step = 30 if wait_seconds > 60 else 10
            remaining = wait_seconds
            while remaining > 0:
                console.print(f"[dim]   Processing starts in {remaining}s...[/dim]")
                pause = min(step, remaining)
                time.sleep(pause)
                remaining -= pause
        else:
            console.print("[yellow]   Launcher mode: starting bulk processing immediately.[/yellow]")

    total = len(accounts)
    workers = max(1, min(int(max_workers), total))
    console.print(
        f"\n[bold cyan]🔄 Phase 3: Processing {total} emails "
        f"({workers} at a time)...[/bold cyan]")
    if workers > 1:
        console.print(
            "[dim]   Mailboxes are watched in parallel, so output from different "
            "emails interleaves — each line is tagged with its number.[/dim]")
    success = 0
    fail = 0
    done = 0

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="coupon") as pool:
        futures = {
            pool.submit(_process_account_worker, i, total, acc): i
            for i, acc in enumerate(accounts, 1)
        }
        for fut in as_completed(futures):
            if fut.result():
                success += 1
            else:
                fail += 1
            done += 1
            console.print(
                f"[bold]   ▸ progress: {done}/{total} finished "
                f"(✅ {success}  ❌ {fail})[/bold]")

    return success, fail


# ===========================================================================
#  MAIN
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(description="Zoro Coupon Generator")
    parser.add_argument("--count", type=int, default=5, help="Number of coupons")
    parser.add_argument("--bulk", action="store_true", help="Bulk mode: all emails upfront")
    parser.add_argument("--launcher-mode", action="store_true",
                        help="Disable stdin prompts for launcher use")
    parser.add_argument("--bulk-signup-wait", type=int, default=180,
                        help="Seconds to wait for manual signups in bulk launcher mode")
    parser.add_argument("--workers", type=int, default=10,
                        help="Bulk mode: mailboxes to watch concurrently (default 10). "
                             "Higher finishes sooner but polls the temp-mail API harder.")
    parser.add_argument("--domain-report", action="store_true",
                        help="Print per-domain signup→coupon conversion and exit")
    args = parser.parse_args()

    if args.domain_report:
        print_domain_report()
        return

    mode = "BULK" if args.bulk else "INTERACTIVE (one-by-one)"
    if HAS_RICH:
        console.print(Panel.fit(
            f"[bold cyan]🎟️  ZORO COUPON GENERATOR[/bold cyan]\n"
            f"Mode: {mode} | Count: {args.count}",
            border_style="blue"
        ))
    else:
        console.print(f"🎟️  ZORO COUPON GENERATOR — {mode} | Count: {args.count}")

    existing = _count_sheet_coupons()
    console.print(f"[cyan]📋 Existing coupons in sheet: {existing}[/cyan]")

    try:
        if args.bulk:
            success, fail = mode_bulk(
                args.count,
                allow_enter=not args.launcher_mode,
                bulk_signup_wait=args.bulk_signup_wait,
                max_workers=args.workers,
            )
        else:
            success, fail = mode_interactive(args.count, allow_enter=not args.launcher_mode)
    except KeyboardInterrupt:
        console.print(f"\n[bold yellow]⏹  Stopped by user.[/bold yellow]")
        success = fail = 0

    console.print(f"\n{'='*60}")
    console.print(f"[bold green]🏁 Done![/bold green]")
    console.print(f"   ✅ Generated: {success}")
    console.print(f"   ❌ Failed: {fail}")
    console.print(f"   📊 All coupons uploaded to Google Sheet")
    print_domain_report()


if __name__ == "__main__":
    main()
