"""Tests for utils/net_errors.py — network-fault classification and the skip guard.
No network, no Chrome:  ../.venv/bin/python scripts/test_net_errors.py"""
import os
import socket
import ssl
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httplib2
from googleapiclient.errors import HttpError
from selenium.common.exceptions import NoSuchWindowException, TimeoutException

from utils.net_errors import NetSkipGuard, is_network_error

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")


def http_error(status):
    return HttpError(httplib2.Response({"status": str(status)}), b"")


# ── classification ─────────────────────────────────────────────────────────
CASES = [
    ("SSL EOF (tracker, 21 Oct 01:19)", ssl.SSLEOFError(8, "EOF occurred in violation of protocol (_ssl.c:2427)"), True),
    ("connection reset", ConnectionResetError(54, "Connection reset by peer"), True),
    ("socket read timeout", socket.timeout("The read operation timed out"), True),
    ("DNS failure", socket.gaierror(8, "nodename nor servname provided, or not known"), True),
    ("httplib2 server not found (20 Oct)", httplib2.ServerNotFoundError("Unable to find the server at gmail.googleapis.com"), True),
    ("Google API 503", http_error(503), True),
    ("Google API 429", http_error(429), True),
    ("Google API 404", http_error(404), False),
    ("Google API 400", http_error(400), False),
    ("wrapped SSL text", Exception("EOF occurred in violation of protocol (_ssl.c:2427)"), True),
    ("Selenium renderer timeout", TimeoutException("Timed out receiving message from renderer: 60.000"), False),
    ("Selenium window closed", NoSuchWindowException("no such window: target window already closed"), False),
    ("plain ValueError", ValueError("could not convert string to float"), False),
    ("KeyError", KeyError("Tracking ID"), False),
]
for name, exc, want in CASES:
    check(name, is_network_error(exc), want)

# ── guard ──────────────────────────────────────────────────────────────────
logged = []
g = NetSkipGuard(limit=3, log=logged.append)
err = ssl.SSLEOFError(8, "EOF occurred in violation of protocol")
g.skip("A", err, "the Gmail cancellation check")
g.skip("B", err, "the Gmail cancellation check")
check("two in a row do not raise", g.in_row, 2)
g.ok()
check("ok() resets the run", g.in_row, 0)
g.skip("C", err, "the Gmail search")
g.skip("D", err, "the Gmail search")
try:
    g.skip("E", err, "the Gmail search")
    FAILS.append("third skip in a row did not raise")
except ssl.SSLError as e:
    check("third skip re-raises the same error", e is err, True)
check("every skipped item is recorded", g.skipped, ["A", "B", "C", "D", "E"])
check("the stop is logged", any("in a row" in s and "ending this run" in s for s in logged), True)
check("limit is never below 1", NetSkipGuard(limit=0).limit, 1)

if FAILS:
    print(f"FAILED {len(FAILS)} check(s):")
    for f in FAILS:
        print("  ✗", f)
    sys.exit(1)
print(f"OK — {len(CASES)} classification cases + guard checks passed")
