"""Transient network faults: tell them apart, and skip one unit of work instead of a whole run.

21 Oct 2026: a tracker cycle walks ~350 orders oldest-first and takes 1–2 hours. One
"EOF occurred in violation of protocol" from the Gmail API at order 250 ended the cycle (the
cancellation check only caught HttpError); the next cycle started again from the oldest order,
so the newest shipments waited hours for their tracking upload.

    is_network_error(exc)  → True for SSL / connection / DNS / socket-timeout faults and Google
                             API 429/5xx; never for a Selenium/WebDriver error (a broken browser
                             is the caller's business, as before).
    NetSkipGuard           → skip() logs the skipped item and re-raises once `limit` network
                             faults come in a row (the network is down: end the run and back off
                             as before); ok() after any item that got through resets the count.
"""
import http.client
import socket
import ssl

# Phrases seen in production for faults that arrive wrapped in other exception types.
_NETWORK_TEXT = (
    "eof occurred in violation of protocol", "unable to find the server", "connection reset",
    "connection aborted", "connection refused", "remote end closed connection", "broken pipe",
    "temporary failure in name resolution", "nodename nor servname", "name or service not known",
    "network is unreachable", "read operation timed out",
)


def is_network_error(exc) -> bool:
    try:
        from selenium.common.exceptions import WebDriverException
        if isinstance(exc, WebDriverException):
            return False
    except Exception:
        pass
    if isinstance(exc, (ssl.SSLError, ConnectionError, TimeoutError, socket.timeout, socket.gaierror,
                        http.client.HTTPException)):
        return True
    try:
        import httplib2
        if isinstance(exc, httplib2.HttpLib2Error):
            return True
    except Exception:
        pass
    try:
        from googleapiclient.errors import HttpError
        if isinstance(exc, HttpError):
            status = str(getattr(getattr(exc, "resp", None), "status", "") or "")
            return status in ("429", "500", "502", "503", "504")
    except Exception:
        pass
    try:
        import requests
        if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
            return True
    except Exception:
        pass
    text = str(exc).lower()
    return any(s in text for s in _NETWORK_TEXT)


class NetSkipGuard:
    """Per-run counter of consecutive network skips."""

    def __init__(self, limit: int = 3, log=print):
        self.limit = max(1, int(limit))
        self.in_row = 0
        self.skipped = []
        self._log = log

    def ok(self):
        self.in_row = 0

    def skip(self, key, exc, what: str):
        """Record a skipped item; re-raise `exc` when this makes `limit` in a row."""
        self.in_row += 1
        self.skipped.append(key)
        self._log(f"     🌐  Network error during {what} for {key} "
                  f"({type(exc).__name__}: {str(exc)[:90]}) — skipped, retried next cycle "
                  f"({self.in_row}/{self.limit} in a row)")
        if self.in_row >= self.limit:
            self._log(f"     ⛔  {self.in_row} network errors in a row — the network looks down; ending this run")
            raise exc
