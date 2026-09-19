#!/usr/bin/env python3
"""
totp_now.py — the current Seller Central 2SV code, live, with its countdown.

For the moment Amazon asks you to confirm a new authenticator, and any time you need to log
in by hand. Codes last 30 seconds, so this refreshes in place rather than printing one number
that is stale by the time you read it.

  python scripts/totp_now.py hathoro     # key from the saved credentials
  python scripts/totp_now.py             # not saved yet → asks for the key (hidden)

Ctrl-C to stop.
"""
import getpass
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
CREDS = os.path.join(AUTO, "data", "All_Tokens", "seller_central_creds.json")


def secret_for(account):
    """The stored key for an account, or None."""
    try:
        with open(CREDS, encoding="utf-8") as f:
            acct = (json.load(f).get("accounts") or {}).get(account) or {}
    except Exception:
        return None
    s = (acct.get("totp_secret") or "").strip()
    return None if (not s or "FILL_IN" in s) else s


def main():
    account = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    secret = secret_for(account) if account else None

    if not secret:
        if account:
            print(f"No 2SV key saved for '{account}' yet — paste it for this session only.")
        secret = getpass.getpass("2SV key (hidden, spaces ok): ")
    secret = secret.replace(" ", "").replace("-", "").strip().upper()
    if not secret:
        print("❌ No key given.")
        sys.exit(1)

    try:
        import pyotp
        totp = pyotp.TOTP(secret)
        totp.now()
    except Exception as e:
        print(f"❌ That key does not work: {type(e).__name__}: {e}")
        print("   Amazon's key is base32 — A-Z and 2-7 only, no 0/1/8/9.")
        sys.exit(1)

    print("\n  Ctrl-C to stop.\n")
    try:
        while True:
            left = 30 - (int(time.time()) % 30)
            bar = "█" * left + "·" * (30 - left)
            sys.stdout.write(f"\r   {totp.now()}   {bar} {left:2d}s ")
            sys.stdout.flush()
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n")


if __name__ == "__main__":
    main()
