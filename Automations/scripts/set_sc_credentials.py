#!/usr/bin/env python3
"""
set_sc_credentials.py — fill in one account's Seller Central credentials, safely.

Prompts for the values instead of taking them as arguments, so nothing lands in your shell
history, in a log, or in a chat transcript. The password and the 2SV key are read with
getpass, so they are not echoed to the screen either.

It checks the 2SV key before saving by generating a code from it — a mistyped or truncated key
fails here, at the keyboard, rather than silently at 3am when the tracker halts on a sign-in
page. Amazon prints the key in groups of four; spaces are stripped for you.

Run:  python scripts/set_sc_credentials.py            (asks which account)
      python scripts/set_sc_credentials.py hathoro
"""
import getpass
import json
import os
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

CREDS = os.path.join(AUTO, "data", "All_Tokens", "seller_central_creds.json")


def mask(s):
    s = s or ""
    return f"{s[:2]}…{s[-2:]} ({len(s)} chars)" if len(s) > 6 else "…"


def main():
    if not os.path.exists(CREDS):
        print(f"❌ Credentials file not found: {CREDS}")
        sys.exit(1)
    with open(CREDS, encoding="utf-8") as f:
        data = json.load(f)
    accounts = data.setdefault("accounts", {})

    key = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    if not key:
        print("Accounts in this file:", ", ".join(accounts) or "(none)")
        key = input("Which account? ").strip().lower()
    if not key:
        print("❌ No account given."); sys.exit(1)

    existing = accounts.get(key, {})
    print(f"\nFilling in '{key}'. Press Enter to keep the current value.")
    print(f"  current email : {existing.get('email') or '(unset)'}")

    email = input("  email       : ").strip() or existing.get("email", "")
    password = getpass.getpass("  password    : (hidden) ") or existing.get("password", "")
    raw = getpass.getpass("  2SV key     : (hidden, spaces ok) ")
    secret = raw.replace(" ", "").replace("-", "").strip().upper() or existing.get("totp_secret", "")

    if not email or not password or not secret:
        print("❌ All three are needed (email, password, 2SV key).")
        sys.exit(1)
    if "FILL_IN" in email or "FILL_IN" in password or "FILL_IN" in secret:
        print("❌ That still looks like a placeholder.")
        sys.exit(1)

    # Prove the key works before writing it.
    try:
        import pyotp
        code = pyotp.TOTP(secret).now()
        if not (code.isdigit() and len(code) == 6):
            raise ValueError(f"got {code!r}")
        print(f"\n  ✅ 2SV key is valid — it generates a 6-digit code right now ({code[:2]}****)")
    except Exception as e:
        print(f"\n  ❌ That 2SV key does not work: {type(e).__name__}: {e}")
        print("     Amazon's key is base32 (A-Z and 2-7 only). Copy it again from")
        print("     Login Settings → Two-Step Verification → Add new app → "
              "\"Can't scan the barcode?\"")
        sys.exit(1)

    accounts[key] = {**existing, "email": email, "password": password, "totp_secret": secret}

    tmp = CREDS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)        # 0600 — owner only
    os.replace(tmp, CREDS)

    print(f"\n  ✅ Saved to {os.path.relpath(CREDS, AUTO)}  (permissions 0600, owner-only)")
    print(f"     {key}: email={email}  password={mask(password)}  totp_secret={mask(secret)}")
    print("\n  Next: restart the Hathoro tracker. It will auto-login the way HOANK does.")


if __name__ == "__main__":
    main()
