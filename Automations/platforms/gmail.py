"""
order_gmail.py — Gmail authentication, earnings lookup, and duplicate detection.
"""

import re
import html
import time
import base64
import functools

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.exceptions import RefreshError

from utils.config import TOKEN_DIRECTORY, MASTER_CREDENTIALS_FILE, SCOPES

print = functools.partial(print, flush=True)

# Sentinel returned by check_if_address_already_ordered_gmail when the search could NOT
# be completed (rate-limit / API error after retries). The caller must FAIL CLOSED and
# NOT place the order — silently returning None here is what let a rate-limited check
# "find no duplicate" and keep re-buying.
DUP_CHECK_FAILED = "__DUP_CHECK_FAILED__"


def authenticate_gmail_account(account_name, interactive=False):
    """Authenticate a Gmail account via OAuth2 token file. Returns API service or None.
    
    If the token is expired, revoked, or missing, logs a warning and returns None
    so the launcher and background processes can continue uninterrupted.
    """
    token_file = f"{TOKEN_DIRECTORY}/{account_name}_token.json"
    creds = None

    import os
    if os.path.exists(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        except Exception as e:
            print(f"⚠️  [GMAIL WARNING] Corrupt token file for '{account_name}': {e}. Skipping.")
            creds = None
    else:
        if not interactive:
            print(f"⚠️  [GMAIL WARNING] Token for '{account_name}' not found ({account_name}_token.json). Skipping without stopping launcher.")
            return None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                with open(token_file, "w") as token:
                    token.write(creds.to_json())
            except RefreshError as e:
                print(f"⚠️  [GMAIL WARNING] Token for '{account_name}' is revoked or expired: {e}")
                print(f"   Continuing launcher without '{account_name}'. (Run 'scripts/reauth_gmail.py {account_name}' to re-auth).")
                try:
                    os.remove(token_file)
                except OSError:
                    pass
                creds = None
            except Exception as e:
                print(f"⚠️  [GMAIL WARNING] Token refresh error for '{account_name}': {e}. Continuing without it.")
                creds = None

        if not creds:
            if interactive:
                try:
                    flow = InstalledAppFlow.from_client_secrets_file(MASTER_CREDENTIALS_FILE, SCOPES)
                    creds = flow.run_local_server(port=0)
                    with open(token_file, "w") as token:
                        token.write(creds.to_json())
                except Exception as e:
                    print(f"❌ [GMAIL] Interactive auth failed for '{account_name}': {e}")
                    return None
            else:
                return None

    try:
        return build("gmail", "v1", credentials=creds)
    except Exception as e:
        print(f"⚠️  [GMAIL WARNING] Failed to build Gmail service for '{account_name}': {e}. Continuing without it.")
        return None


def clean_html(raw_html):
    """Strip HTML tags and decode entities."""
    clean = re.sub(r'<(style|script).*?</\1>', '', raw_html, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<[^>]+>', ' ', clean)
    return re.sub(r'\s+', ' ', html.unescape(clean)).strip()


def extract_email_details(body):
    """Extract earnings, original SKU, shipping, and Amazon fees from
    'Sold, ship now' email plain text.

    Returns dict with keys: earnings, original_sku, shipping, amazon_fees
    All money values are floats (or None if not found).
    """
    result = {"earnings": None, "original_sku": None, "shipping": None, "amazon_fees": None}

    # Your earnings: $X.XX  (multi-item emails may have multiple lines — sum them)
    earning_matches = re.findall(r"Your earnings: \$(\d+\.\d{2})", body, re.IGNORECASE)
    if earning_matches:
        result["earnings"] = round(sum(float(m) for m in earning_matches), 2)

    # Original SKU: extract text between "SKU:" and "Quantity:"
    sku_match = re.search(r"SKU:\s*(.+?)\s+Quantity:", body, re.IGNORECASE)
    if sku_match:
        result["original_sku"] = sku_match.group(1).strip()

    # Shipping: $X.XX (may be missing if free shipping)
    ship_match = re.search(r"Shipping:\s*\$(\d+\.\d{2})", body, re.IGNORECASE)
    if ship_match:
        result["shipping"] = float(ship_match.group(1))

    # Amazon fees: -$X.XX
    fee_match = re.search(r"Amazon fees:\s*-\$(\d+\.\d{2})", body, re.IGNORECASE)
    if fee_match:
        result["amazon_fees"] = float(fee_match.group(1))

    return result


def get_amazon_earnings_from_gmail(service, order_id):
    """Look up earnings (and extra details) for an Amazon order from
    'Sold, ship now' email.

    Returns a dict with keys: earnings, original_sku, shipping, amazon_fees
    or None if the email is not found.
    """
    try:
        query = f'"{order_id}" subject:"Sold, ship now:"'
        response = service.users().messages().list(userId='me', q=query, maxResults=1).execute()
        messages = response.get('messages', [])
        if not messages:
            return None

        msg = service.users().messages().get(userId='me', id=messages[0]['id'], format='full').execute()
        payload = msg.get('payload', {})
        data = ""
        if 'parts' in payload:
            for part in payload['parts']:
                if part.get('mimeType') == 'text/html':
                    data = part['body'].get('data', '')
                    break
        if not data:
            data = payload.get('body', {}).get('data', '')

        html_body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
        details = extract_email_details(clean_html(html_body))
        # Only return if we found earnings (the most critical field)
        return details if details.get("earnings") else None
    except Exception:
        return None


def check_if_address_already_ordered_gmail(services_list, address_line, zip_code="", customer_name="",
                                            current_asin=None, current_sku=None, current_amazon_id=None):
    """Search ALL duplicate-check Gmail accounts for recent order to same recipient.

    Strategy:
    1. Check LOCAL records (placer log + zoro archive) with smart repeat-customer (>72h)
       and multi-item (different ASIN/SKU) filtering.
    2. Fall back to live Gmail search ONLY if archive is stale AND local check found nothing.
    3. Search both Zoro ("Has Been Received") and Grainger ("Shipping Confirmation")
       emails with a 3-day (72h) lookback.
    4. Verify zip code in the email body to prevent false positives.

    Returns WB/WW order number if found, or None if no duplicate.
    """
    # ── tier 0/1: LOCAL records first (placer log = zero lag, zoro archive =
    # ≤10 min). Exact street+zip match — no name-relevance guessing, no
    # first-receipt-wins. The live-Gmail keyword search below returned WRONG WBs
    # when many receipts matched, which is what stamped 56 orders with a
    # stranger's "<WB> - D" (2026-08-19/20 incident). Gmail now runs ONLY when
    # local records say nothing AND the archive is too stale to trust a 'no'.
    try:
        from utils.dup_check_local import check_address_local, archive_is_fresh
        _local = check_address_local(address_line, zip_code,
                                     current_asin=current_asin,
                                     current_sku=current_sku,
                                     current_amazon_id=current_amazon_id)
        if _local:
            if len(_local["all_wbs"]) > 1:
                print(f"   [DUP_LOCAL] address matches {len(_local['all_wbs'])} WBs "
                      f"{_local['all_wbs']} — using most recent")
            print(f"   [DUP_LOCAL] duplicate via {_local['source']} ({_local['when']}): {_local['wb']}")
            return _local["wb"]
        if archive_is_fresh():
            print("   [DUP_LOCAL] no local duplicate (or safe repeat/multi-item) + archive fresh — no duplicate (Gmail skipped)")
            return None
        print("   [DUP_LOCAL] no local match but archive is STALE — falling back to live Gmail")
    except Exception as _le:
        print(f"   [DUP_LOCAL] error ({_le}) — falling back to live Gmail")

    # Gmail stores ZIP+4 without hyphens (e.g., 180175940 not 18017-5940)
    zip_search = (zip_code or "").replace('-', '').strip()

    # Build search queries — name-based is primary, address-based is fallback
    queries = []
    if customer_name:
        clean_name = re.sub(r'[^a-zA-Z\s]', '', customer_name).strip()
        if clean_name:
            name_query = f'"{clean_name}"'
            if zip_search:
                name_query = f'"{clean_name}" "{zip_search}"'
            queries.append(name_query)

    # Fallback: address-based search (original logic, widened window)
    clean_addr = re.sub(r'[^a-zA-Z0-9\s]', '', address_line).strip()
    if clean_addr:
        addr_query = f'"{clean_addr}"'
        if zip_search:
            addr_query = f'"{clean_addr}" {zip_search}'
        queries.append(addr_query)

    print(f"   [DUP_CHECK] queries={queries}, zip={zip_code}, accounts={len(services_list)}")

    if not queries:
        return None

    search_failed = False   # True if ANY account's search could not complete (fail-closed)

    for idx, service in enumerate(services_list):
        for search_term in queries:
            # Search confirmation emails within 3-day (72h) lookback
            query = (f'{search_term} newer_than:3d '
                     f'(subject:"Has Been Received" OR subject:"Shipping Confirmation" OR subject:"Sales Receipt")')
            # Retry transient API/rate-limit errors with backoff before giving up.
            messages = None
            last_err = None
            for _try in range(3):
                try:
                    print(f"   [DUP_CHECK] acct={idx} query={query[:120]}")
                    response = service.users().messages().list(
                        userId='me', q=query, maxResults=10).execute()
                    messages = response.get('messages', [])
                    print(f"   [DUP_CHECK] acct={idx} found={len(messages)} messages")
                    break
                except Exception as e:
                    last_err = e
                    print(f"   [DUP_CHECK] acct={idx} search error (try {_try+1}/3): {e}")
                    time.sleep(2 * (_try + 1))
            if messages is None:
                # Could not complete this search after retries — mark fail-closed and move on.
                print(f"   [DUP_CHECK] acct={idx} SEARCH FAILED after retries: {last_err}")
                search_failed = True
                continue
            try:

                for msg_info in messages:
                    # Get subject + full body
                    msg = service.users().messages().get(
                        userId='me', id=msg_info['id'], format='full'
                    ).execute()
                    headers = msg.get('payload', {}).get('headers', [])
                    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '')

                    payload = msg.get('payload', {})
                    data = ""
                    if 'parts' in payload:
                        for part in payload['parts']:
                            if part.get('mimeType') == 'text/html':
                                data = part['body'].get('data', '')
                                break
                    if not data:
                        data = payload.get('body', {}).get('data', '')

                    body_text = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                    body_clean = clean_html(body_text)

                    # Verify zip code is in the email body (prevents false positives)
                    # Compare against both hyphenated and unhyphenated forms
                    zip_clean = (zip_code or "").strip()
                    zip_no_hyphen = zip_clean.replace('-', '')
                    if zip_clean and zip_clean not in body_clean and zip_no_hyphen not in body_clean:
                        print(f"   [DUP_CHECK] SKIP — zip '{zip_code}' / '{zip_no_hyphen}' not in body")
                        continue

                    # Extract WB or WW order number from subject or body
                    order_match = re.search(r'#?((?:WB|WW)\S+)', subject)
                    if order_match:
                        print(f"   [DUP_CHECK] MATCH in subject: {order_match.group(1)}")
                        return order_match.group(1)

                    order_match = re.search(r'#?((?:WB|WW)\S+)', body_clean)
                    if order_match:
                        print(f"   [DUP_CHECK] MATCH in body: {order_match.group(1)}")
                        return order_match.group(1)
                    else:
                        print(f"   [DUP_CHECK] MATCH (no WB/WW id found)")
                        return "Duplicate"

            except Exception as e:
                print(f"   [DUP_CHECK] acct={idx} ERROR: {e}")
                search_failed = True
                continue

    if search_failed:
        # We could not conclusively rule out a duplicate — FAIL CLOSED.
        print(f"   [DUP_CHECK] ⚠️  search incomplete (rate-limit/error) — returning FAILED (fail-closed)")
        return DUP_CHECK_FAILED
    print(f"   [DUP_CHECK] no match across all accounts")
    return None


def batch_check_addresses_gmail(services_list, orders):
    """Batched form of check_if_address_already_ordered_gmail for RUSH mode:
    ONE Gmail search per account covering EVERY order in the batch (names +
    addresses OR-ed into a single query), message bodies fetched once, then
    each order verified locally with the same name/zip/WB logic.

    orders: [{"key", "name", "address", "zip"}] (key = amazon_id)

    Returns {key: "" | "WB..." | "Duplicate"} — "" means CONFIDENTLY no
    duplicate (every account's search completed and nothing matched). Keys are
    OMITTED whenever the batch could not verify them (search error, result
    overflow) so the caller falls back to the per-order fail-closed check.
    Protection is therefore never weaker than the classic path — only faster.
    """
    # ── LOCAL tier first, same as the per-order check: exact street+zip against
    # placer records (zero lag) + zoro archive (≤10 min). Only unresolved orders
    # go to the Gmail sweep, and with a fresh archive none do.
    resolved = {}
    remaining = []
    try:
        from utils.dup_check_local import check_address_local, archive_is_fresh
        fresh = archive_is_fresh()
        for o in orders:
            _l = check_address_local(o.get("address") or "", o.get("zip") or "",
                                     current_asin=o.get("asin"),
                                     current_sku=o.get("sku"),
                                     current_amazon_id=o.get("key"))
            if _l:
                resolved[o["key"]] = _l["wb"]
                print(f"   [DUP_LOCAL] {o['key']}: duplicate via {_l['source']} → {_l['wb']}")
            elif fresh:
                resolved[o["key"]] = ""          # confident no — archive is current
            else:
                remaining.append(o)
        if not remaining:
            return resolved
        orders = remaining
        print(f"   [DUP_LOCAL] {len(orders)} order(s) unresolved locally (stale archive) → Gmail sweep")
    except Exception as _le:
        print(f"   [DUP_LOCAL] batch error ({_le}) — full Gmail sweep")
        resolved = {}

    specs = {}
    for o in orders:
        terms = []
        nm = re.sub(r'[^a-zA-Z\s]', '', o.get("name") or '').strip()
        ad = re.sub(r'[^a-zA-Z0-9\s]', '', o.get("address") or '').strip()
        if nm:
            terms.append(nm)
        if ad:
            terms.append(ad)
        if terms:
            specs[o["key"]] = {"terms": terms, "zip": (o.get("zip") or "").strip()}
    if not specs:
        return resolved          # keep the locally-resolved answers

    all_terms = sorted({t for s in specs.values() for t in s["terms"]})
    or_block = " OR ".join(f'"{t}"' for t in all_terms)
    query = (f'({or_block}) newer_than:3d '
             f'(subject:"Has Been Received" OR subject:"Shipping Confirmation" OR subject:"Sales Receipt")')

    bodies = []       # (subject, body_clean)
    complete = True   # every search AND every body fetch succeeded
    for idx, service in enumerate(services_list):
        messages = None
        for _try in range(3):
            try:
                print(f"   [DUP_BATCH] acct={idx} one query for {len(specs)} orders")
                resp = service.users().messages().list(
                    userId='me', q=query, maxResults=50).execute()
                messages = resp.get('messages', [])
                if resp.get('nextPageToken'):
                    complete = False      # more hits than we fetched — can't be sure
                break
            except Exception as e:
                print(f"   [DUP_BATCH] acct={idx} search error (try {_try+1}/3): {e}")
                time.sleep(2 * (_try + 1))
        if messages is None:
            complete = False
            continue
        for mi in messages:
            try:
                msg = service.users().messages().get(
                    userId='me', id=mi['id'], format='full').execute()
                headers = msg.get('payload', {}).get('headers', [])
                subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '')
                payload = msg.get('payload', {})
                data = ""
                if 'parts' in payload:
                    for part in payload['parts']:
                        if part.get('mimeType') == 'text/html':
                            data = part['body'].get('data', '')
                            break
                if not data:
                    data = payload.get('body', {}).get('data', '')
                body_clean = clean_html(
                    base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore'))
                bodies.append((subject, body_clean))
            except Exception as e:
                print(f"   [DUP_BATCH] body fetch error: {e}")
                complete = False

    # Normalize bodies the same way the search terms were normalized
    # (punctuation stripped) — otherwise "O'Brien" in a body never matches the
    # stripped term "OBrien" and a real duplicate slips through.
    norm_bodies = []
    for subject, body_clean in bodies:
        low = re.sub(r'[^a-z0-9\s]', '', body_clean.lower())
        low = re.sub(r'\s+', ' ', low)
        norm_bodies.append((subject, body_clean, low))

    out = {}
    for key, spec in specs.items():
        zip_clean = spec["zip"]
        zip_nh = zip_clean.replace('-', '')
        terms_n = [re.sub(r'\s+', ' ', t.lower()) for t in spec["terms"]]
        verdict = None
        for subject, body_clean, low in norm_bodies:
            if not any(t in low for t in terms_n):
                continue
            if zip_clean and zip_clean not in body_clean and zip_nh not in body_clean:
                continue
            m = re.search(r'#?((?:WB|WW)\S+)', subject) or re.search(r'#?((?:WB|WW)\S+)', body_clean)
            verdict = m.group(1) if m else "Duplicate"
            print(f"   [DUP_BATCH] MATCH for {key}: {verdict}")
            break
        if verdict is not None:
            out[key] = verdict            # positive matches always usable
        elif complete:
            out[key] = ""                 # confident no-dup only on a clean sweep
    return {**resolved, **out}
