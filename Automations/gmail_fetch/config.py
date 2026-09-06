"""
config.py — self-contained config for the Gmail-archive FETCH pipeline, now living
INSIDE Automations/gmail_fetch/ (the old 'Final Automation' folder was deleted —
its historical data is already mirrored into automations_unified.db, so only the
fetch CODE needed to move here). Paths point at Automations/ and Automations/data/.

Account lists come from the SHARED Automations/config/accounts.json (single source
of truth). This replaces the old single-inbox earnings + missing-Zoro-account lists.
"""
import os
import json
from pathlib import Path

# gmail_fetch/ → parent = Automations/
AUTOMATIONS_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = AUTOMATIONS_DIR / "data"

# Tokens + credentials live in Automations/data/All_Tokens (where they actually are)
TOKEN_DIRECTORY = DATA_DIR / "All_Tokens"
MASTER_CREDENTIALS_FILE = TOKEN_DIRECTORY / "AB ECOM Credentials.json"
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# The archive DB now lives in Automations/data/ (fresh file — it rebuilds on fetch;
# unified already holds all history via the idempotent sync).
GMAIL_ARCHIVE_DB_PATH = DATA_DIR / "gmail_archive.db"
GMAIL_PREFETCH_DAYS = 150

# ── Account lists from the shared single source of truth ────────────────────
_SHARED_CFG = AUTOMATIONS_DIR / "config" / "accounts.json"
try:
    _shared = json.loads(_SHARED_CFG.read_text())
    # Every store's earnings inbox ('Sold, ship now'): qurtdis + abecomllc26
    EARNINGS_GMAIL_ACCOUNTS = []
    for _a in _shared["accounts"].values():
        _e = _a.get("earnings_inbox")
        if _e and _e not in EARNINGS_GMAIL_ACCOUNTS:
            EARNINGS_GMAIL_ACCOUNTS.append(_e)
    ZORO_ORDER_GMAIL_ACCOUNTS = list(_shared["zoro_accounts"])
    ALL_GMAIL_ACCOUNTS = sorted(set(_shared["gmail_accounts"]) | set(_shared["zoro_accounts"]))
except Exception as _e:
    print(f"[gmail_fetch/config] ⚠️ could not read {_SHARED_CFG} ({_e}) — using fallback lists")
    EARNINGS_GMAIL_ACCOUNTS = ["qurtdis", "abecomllc26"]
    ZORO_ORDER_GMAIL_ACCOUNTS = ["salesmercentile", "salesmercentile1", "abecomllc26",
        "asad_amazon_us", "inhur_llc", "wrenchlyllc", "toolzsolutionsllc220", "qurtdis",
        "abecomllc02", "kamibaloch342", "kamran_va_342", "kamranwork342", "robstark771",
        "salestreendfitinc", "toolzsolutionsllc"]
    ALL_GMAIL_ACCOUNTS = sorted(set(ZORO_ORDER_GMAIL_ACCOUNTS) | {"qurtdis", "abecomllc26"})

# Legacy single-inbox name (older callers) — the first earnings inbox.
EARNINGS_GMAIL_ACCOUNT = EARNINGS_GMAIL_ACCOUNTS[0]

# ── Chrome / misc symbols utils.py imports at module load (UNUSED by the fetch,
#    but must exist or the import fails). Harmless placeholders. ──────────────
CHROME_BINARY_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_PROFILE_PATH = str(DATA_DIR / ".uc_profile")
UC_PROFILE_DIR = str(DATA_DIR / ".uc_profile")
CHROME_DEBUGGING_PORT = 9222
DOWNLOAD_DIRECTORY = str(Path.home() / "Downloads")
TRACKING_ID_REGEX = r"\b(\d{12,22})\b"
