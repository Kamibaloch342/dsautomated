#!/usr/bin/env python3
"""
live_gmail_enrichment.py — Query live Gmail API for returns, refunds, and messages.

Used for recent orders where the nightly archive may be stale.
Processes ALL configured Gmail accounts in one run.

Usage:
    python engine/live_gmail_enrichment.py [--days 7] [--dry-run]
"""

from __future__ import annotations

import os
import sys
import re
import argparse
from datetime import datetime, timedelta

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from utils.unified_db import get_conn, upsert_closing_order, log_enrichment

# Gmail accounts to check (from config)
GMAIL_ACCOUNTS = [
    "abecomllc26",      # Hathoro earnings
    "qurtdis",          # HOANK earnings
    "asad_amazon_us",   # HOANK duplicates
    "inhur_llc",        # HOANK duplicates
    "wrenchlyllc",      # Hathoro duplicates
]

RUN_ID_PREFIX = "live_gmail"


def _get_gmail_service(account_name: str):
    """Authenticate and return Gmail API service."""
    try:
        from platforms.gmail import authenticate_gmail_account
        return authenticate_gmail_account(account_name)
    except Exception as e:
        print(f"  ⚠️  Could not auth {account_name}: {e}")
        return None


def _search_messages(service, query: str, max_results: int = 500):
    """Search Gmail and return list of message dicts."""
    results = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    return results.get("messages", [])


def _get_message_body(service, msg_id: str) -> str:
    """Fetch and return plain text body of a message."""
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    payload = msg.get("payload", {})
    parts = payload.get("parts", [])
    
    # Try to get text/plain body
    body_text = ""
    for part in parts:
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                import base64
                body_text = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
                break
    
    if not body_text:
        # Fallback: get snippet
        body_text = msg.get("snippet", "")
    
    return body_text


def _extract_amazon_order_id(text: str) -> str | None:
    """Extract Amazon order ID (e.g., 112-1234567-1234567) from text."""
    match = re.search(r"\b(\d{3}-\d{7}-\d{7})\b", text)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Enrichment functions
# ---------------------------------------------------------------------------

def enrich_returns_and_refunds(days: int = 7, dry_run: bool = False, verbose: bool = True) -> dict:
    """
    Search live Gmail for return/refund emails and update closing_orders.
    Returns stats dict.
    """
    stats = {"returns_found": 0, "refunds_found": 0, "errors": 0}
    run_id = f"{RUN_ID_PREFIX}_returns_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    # Build search query for returns
    # Amazon return emails typically contain "return" and the order ID
    after_date = (datetime.now() - timedelta(days=days)).strftime("%Y/%m/%d")
    query = f"from:amazon.com return after:{after_date}"
    
    for account in GMAIL_ACCOUNTS:
        if verbose:
            print(f"Checking returns for {account}...")
        
        service = _get_gmail_service(account)
        if not service:
            continue
        
        try:
            messages = _search_messages(service, query, max_results=200)
            for msg_meta in messages:
                try:
                    body = _get_message_body(service, msg_meta["id"])
                    amazon_id = _extract_amazon_order_id(body)
                    if not amazon_id:
                        continue
                    
                    # Determine if it's a return request or refund
                    body_lower = body.lower()
                    is_return = "return" in body_lower or "authorization" in body_lower
                    is_refund = "refund" in body_lower or "refunded" in body_lower
                    
                    with get_conn() as conn:
                        row = conn.execute(
                            "SELECT return_requested, refund_done FROM closing_orders WHERE amazon_order_id=?",
                            (amazon_id,)
                        ).fetchone()
                        
                        if not row:
                            continue
                        
                        updates = {"amazon_order_id": amazon_id}
                        changed = False
                        
                        if is_return and not row["return_requested"]:
                            updates["return_requested"] = "Yes"
                            changed = True
                            stats["returns_found"] += 1
                            if not dry_run:
                                log_enrichment(run_id, amazon_id, "live_gmail", "return_requested", row["return_requested"], "Yes", confidence=0.9)
                        
                        if is_refund and not row["refund_done"]:
                            updates["refund_done"] = "Yes"
                            changed = True
                            stats["refunds_found"] += 1
                            if not dry_run:
                                log_enrichment(run_id, amazon_id, "live_gmail", "refund_done", row["refund_done"], "Yes", confidence=0.9)
                        
                        if changed and not dry_run:
                            upsert_closing_order(updates)
                            
                except Exception as e:
                    stats["errors"] += 1
                    if verbose:
                        print(f"  ⚠️  Error processing message: {e}")
                        
        except Exception as e:
            stats["errors"] += 1
            if verbose:
                print(f"  ⚠️  Error searching {account}: {e}")
    
    if verbose:
        print(f"  Returns found: {stats['returns_found']} | Refunds found: {stats['refunds_found']} | Errors: {stats['errors']}")
    return stats


def enrich_customer_messages(days: int = 7, dry_run: bool = False, verbose: bool = True) -> dict:
    """
    Search live Gmail for customer message emails and update closing_orders.
    Returns stats dict.
    """
    stats = {"messages_found": 0, "errors": 0}
    run_id = f"{RUN_ID_PREFIX}_messages_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    after_date = (datetime.now() - timedelta(days=days)).strftime("%Y/%m/%d")
    query = f"from:amazon.com subject:message after:{after_date}"
    
    for account in GMAIL_ACCOUNTS:
        if verbose:
            print(f"Checking messages for {account}...")
        
        service = _get_gmail_service(account)
        if not service:
            continue
        
        try:
            messages = _search_messages(service, query, max_results=200)
            for msg_meta in messages:
                try:
                    body = _get_message_body(service, msg_meta["id"])
                    amazon_id = _extract_amazon_order_id(body)
                    if not amazon_id:
                        continue
                    
                    with get_conn() as conn:
                        row = conn.execute(
                            "SELECT customer_messaged FROM closing_orders WHERE amazon_order_id=?",
                            (amazon_id,)
                        ).fetchone()
                        
                        if not row or row["customer_messaged"]:
                            continue
                        
                        if not dry_run:
                            upsert_closing_order({
                                "amazon_order_id": amazon_id,
                                "customer_messaged": "Yes",
                            })
                            log_enrichment(run_id, amazon_id, "live_gmail", "customer_messaged", row["customer_messaged"], "Yes", confidence=0.8)
                        
                        stats["messages_found"] += 1
                        
                except Exception as e:
                    stats["errors"] += 1
                    if verbose:
                        print(f"  ⚠️  Error processing message: {e}")
                        
        except Exception as e:
            stats["errors"] += 1
            if verbose:
                print(f"  ⚠️  Error searching {account}: {e}")
    
    if verbose:
        print(f"  Messages found: {stats['messages_found']} | Errors: {stats['errors']}")
    return stats


def enrich_earnings_from_live_gmail(days: int = 7, dry_run: bool = False, verbose: bool = True) -> dict:
    """
    Search live Gmail for 'Sold, ship now' earnings emails and update closing_orders.
    Only processes orders currently missing earnings.
    Returns stats dict.
    """
    stats = {"earnings_found": 0, "errors": 0}
    run_id = f"{RUN_ID_PREFIX}_earnings_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    after_date = (datetime.now() - timedelta(days=days)).strftime("%Y/%m/%d")
    query = f"from:amazon.com 'Sold, ship now' after:{after_date}"
    
    for account in GMAIL_ACCOUNTS:
        if verbose:
            print(f"Checking earnings for {account}...")
        
        service = _get_gmail_service(account)
        if not service:
            continue
        
        try:
            messages = _search_messages(service, query, max_results=500)
            for msg_meta in messages:
                try:
                    body = _get_message_body(service, msg_meta["id"])
                    amazon_id = _extract_amazon_order_id(body)
                    if not amazon_id:
                        continue
                    
                    with get_conn() as conn:
                        row = conn.execute(
                            "SELECT earnings, amazon_fees, shipping_amt FROM closing_orders WHERE amazon_order_id=?",
                            (amazon_id,)
                        ).fetchone()
                        
                        if not row or row["earnings"]:
                            continue
                    
                    # Extract earnings using existing parser
                    from platforms.gmail import extract_email_details
                    details = extract_email_details(body)
                    
                    earnings = details.get("earnings")
                    fees = details.get("amazon_fees")
                    shipping = details.get("shipping")
                    
                    if earnings:
                        updates = {
                            "amazon_order_id": amazon_id,
                            "earnings": earnings,
                        }
                        if fees is not None:
                            updates["amazon_fees"] = fees
                        if shipping is not None:
                            updates["shipping_amt"] = shipping
                        
                        if not dry_run:
                            upsert_closing_order(updates)
                            log_enrichment(run_id, amazon_id, "live_gmail", "earnings", row["earnings"], earnings, confidence=0.95)
                        
                        stats["earnings_found"] += 1
                        
                except Exception as e:
                    stats["errors"] += 1
                    if verbose:
                        print(f"  ⚠️  Error processing message: {e}")
                        
        except Exception as e:
            stats["errors"] += 1
            if verbose:
                print(f"  ⚠️  Error searching {account}: {e}")
    
    if verbose:
        print(f"  Earnings found: {stats['earnings_found']} | Errors: {stats['errors']}")
    return stats


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def run_all_enrichment(days: int = 7, dry_run: bool = False, verbose: bool = True) -> dict:
    """Run all live Gmail enrichment checks."""
    if verbose:
        print(f"\n📧 LIVE GMAIL ENRICHMENT (last {days} days)")
        print("─" * 50)
    
    earnings_stats = enrich_earnings_from_live_gmail(days, dry_run, verbose)
    returns_stats = enrich_returns_and_refunds(days, dry_run, verbose)
    messages_stats = enrich_customer_messages(days, dry_run, verbose)
    
    total = {
        "earnings_found": earnings_stats["earnings_found"],
        "returns_found": returns_stats["returns_found"],
        "refunds_found": returns_stats["refunds_found"],
        "messages_found": messages_stats["messages_found"],
        "errors": earnings_stats["errors"] + returns_stats["errors"] + messages_stats["errors"],
    }
    
    if verbose:
        print(f"\n✅ Live Gmail enrichment complete:")
        print(f"  Earnings found: {total['earnings_found']}")
        print(f"  Returns found: {total['returns_found']}")
        print(f"  Refunds found: {total['refunds_found']}")
        print(f"  Messages found: {total['messages_found']}")
        print(f"  Errors: {total['errors']}")
    
    return total


def main():
    parser = argparse.ArgumentParser(description="Live Gmail enrichment for closing_orders")
    parser.add_argument("--days", type=int, default=7, help="Look back N days")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    parser.add_argument("--quiet", action="store_true", help="Minimal output")
    args = parser.parse_args()
    
    stats = run_all_enrichment(days=args.days, dry_run=args.dry_run, verbose=not args.quiet)
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
