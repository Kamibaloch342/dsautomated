"""
reauth_gmail.py — re-authorize one (or more) dead Gmail token(s).

Use when a token fails with 'invalid_grant: Token has been expired or revoked.'
The normal authenticate_gmail_account() won't self-heal these because the stored
access-token expiry is in the future (so creds.valid==True) — it only discovers
the revocation on a live API call. This script FORCES a fresh browser OAuth:
backs up the dead token, runs the consent flow, saves + verifies the new one.

MUST be run on the Mac (a browser window opens for Google sign-in).

Usage:
  ../.venv/bin/python scripts/reauth_gmail.py abecomllc02
  ../.venv/bin/python scripts/reauth_gmail.py abecomllc02 excurellc   # several
"""
import os
import sys
import shutil
from datetime import datetime

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _AUTO)

from utils.config import TOKEN_DIRECTORY, MASTER_CREDENTIALS_FILE, SCOPES
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


def reauth(account: str) -> bool:
    token_file = os.path.join(TOKEN_DIRECTORY, f"{account}_token.json")
    print(f"\n=== {account} ===")

    # 1) back up the existing (dead) token so nothing is lost
    if os.path.exists(token_file):
        bak = token_file + f".dead-{datetime.now():%Y%m%d-%H%M%S}.bak"
        shutil.copy2(token_file, bak)
        print(f"  backed up old token → {os.path.basename(bak)}")
        os.remove(token_file)

    # 2) fresh consent flow (opens a browser)
    print("  🔑 Starting local OAuth server and opening browser for Google sign-in...", flush=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(MASTER_CREDENTIALS_FILE), SCOPES)
    creds = flow.run_local_server(
        port=0,
        authorization_prompt_message=(
            "\n=======================================================\n"
            "👉 Please visit this URL to authorize on your Mac:\n"
            "{url}\n"
            "=======================================================\n"
        ),
        success_message="Authentication successful! You can close this browser tab.",
        open_browser=True
    )
    with open(token_file, "w") as f:
        f.write(creds.to_json())
    print("  ✓ new token saved", flush=True)
    if account in ("wrenchly", "wrenchlyllc"):
        alt_name = "wrenchly" if account == "wrenchlyllc" else "wrenchlyllc"
        alt_file = os.path.join(TOKEN_DIRECTORY, f"{alt_name}_token.json")
        shutil.copy2(token_file, alt_file)
        print(f"  ✓ mirrored to {os.path.basename(alt_file)}")

    # 3) verify with a real API call
    try:
        svc = build("gmail", "v1", credentials=creds)
        prof = svc.users().getProfile(userId="me").execute()
        print(f"  ✅ VERIFIED — {prof.get('emailAddress')} "
              f"({prof.get('messagesTotal')} messages)")
        return True
    except Exception as e:
        print(f"  ❌ still failing after re-auth: {e}")
        return False


if __name__ == "__main__":
    accounts = sys.argv[1:] or ["abecomllc02"]
    ok = [a for a in accounts if reauth(a)]
    print(f"\nDone — {len(ok)}/{len(accounts)} re-authorized: {', '.join(ok) or 'none'}")
    sys.exit(0 if len(ok) == len(accounts) else 1)
