"""
listing_watch.py — what changed on our own-brand (PL) Amazon listings since the last look.

The repricer scrape (scripts/generate_hoank_offers_report.py / generate_hathoro_offers_report.py,
started every 12 h by the dashboard) already opens the Amazon page of every listing that sold in the
last 30 days and reads its offers. While that page is open this module also reads the brand line,
the star rating, the number of ratings and the category, remembers them, and reports the
differences in ONE WhatsApp message per store and run to the store's Insights group, and one by one
on the dashboard's Insights tab (utils/listing_events.py) — owner, 10 Nov 2026:

  • a seller on one of our PL listings who is not one of our stores (new since the last look),
  • the star rating moved,
  • the number of ratings changed,
  • the category changed.

PL or not — the owner's rule: open the product and read the brand; "HOANK" or "Hathoro" means it is
ours. Only when the brand line could not be read does the SKU decide (HO-/HOA… for HOANK, HATH… for
Hathoro, the same prefixes the Repricer tab uses). A listing that is not PL is remembered but never
alerted on.

The first look at a listing only records it (nothing to compare with), except that other sellers
already sitting on a PL listing are listed once. A value that could not be read keeps the remembered
one, so a page Amazon served half-empty never looks like a change.

State: data/pl_watch_state.json (written to a .tmp file and renamed, never truncated in place).
Nothing here can stop the scrape: every entry point swallows its own errors.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(_AUTO, "data", "pl_watch_state.json")

# Our own brands as Amazon shows them on the product page ("Visit the HOANK Store", "Brand: Hathoro").
PL_BRANDS = ("hoank", "hathoro")
# Sellers allowed on our PL listings: our three stores, under every name Amazon has shown for them.
# (The same list as dashboard/repricer_engine.py AUTHORIZED_PL_SELLERS — keep the two in step.)
AUTHORIZED_SELLERS = ("hathoro", "hoank", "wrenchly", "qurtdis", "hypersale", "hyper sale", "abecomllc26")
MAX_LINES = 15            # per section of the message; the rest is a "+N more" line


# ── reading the page ───────────────────────────────────────────────────────────────────────────
def facts_from_html(html: str) -> Dict[str, Any]:
    """{'brand', 'rating', 'reviews', 'category'} from an Amazon product page; None for what is not there."""
    out: Dict[str, Any] = {"brand": None, "rating": None, "reviews": None, "category": None}
    if not html:
        return out
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return out

    def text(sel: str) -> str:
        el = soup.select_one(sel)
        return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""

    by = text("#bylineInfo")
    if by:
        m = re.search(r"Visit the (.+?) Store\b", by, re.I) or re.search(r"Brand\s*:\s*(.+)", by, re.I)
        out["brand"] = (m.group(1) if m else by).strip()[:80] or None

    pop = soup.select_one("#acrPopover")
    cand = [(pop.get("title") or "") if pop else "", text("#acrPopover .a-icon-alt"),
            text("#averageCustomerReviews .a-icon-alt"), text('span[data-hook="rating-out-of-text"]')]
    for c in cand:
        m = re.search(r"(\d(?:[.,]\d)?)\s*out of\s*5", c or "", re.I)
        if m:
            try:
                out["rating"] = float(m.group(1).replace(",", "."))
                break
            except ValueError:
                pass

    rv = soup.select_one("#acrCustomerReviewText")
    for c in ([rv.get_text(" ", strip=True), rv.get("aria-label") or ""] if rv else []):
        m = re.search(r"(\d[\d,]*)", c or "")
        if m:
            out["reviews"] = int(m.group(1).replace(",", ""))
            break
    if out["reviews"] is None and soup.select_one("#acrNoReviewText"):
        out["reviews"] = 0                           # "No customer reviews yet"

    crumbs = [re.sub(r"\s+", " ", a.get_text(" ", strip=True))
              for a in soup.select("#wayfinding-breadcrumbs_feature_div a, #wayfinding-breadcrumbs_container a")]
    crumbs = [c for i, c in enumerate(crumbs) if c and c not in crumbs[:i]]
    if crumbs:
        out["category"] = " › ".join(crumbs)[:200]
    return out


def brand_is_ours(brand: Optional[str]) -> bool:
    b = (brand or "").strip().lower()
    return bool(b) and any(p in b for p in PL_BRANDS)


def sku_is_pl(sku: Optional[str], store: str = "") -> bool:
    """The SKU-prefix rule, used only when the page's brand line could not be read."""
    s = (sku or "").strip().lower()
    if not s:
        return False
    if "hath" in (store or "").lower():
        return "hath" in s or s.startswith(("ht_", "ht-", "ht "))
    return s.startswith(("hoa", "ho-", "ho_", "ho ", "hon")) or "hoank" in s


def is_pl_listing(brand: Optional[str], sku: Optional[str], store: str = "") -> bool:
    """Brand on the page decides; without one, the SKU prefix."""
    return brand_is_ours(brand) if (brand or "").strip() else sku_is_pl(sku, store)


def other_sellers(offers: Optional[List[Dict[str, Any]]]) -> List[str]:
    """Sellers on the listing who are none of our stores, in the order Amazon lists them."""
    out: List[str] = []
    for o in offers or []:
        name = str(o.get("seller_name") or "").strip()
        low = name.lower()
        if name and not any(a in low for a in AUTHORIZED_SELLERS) and name not in out:
            out.append(name)
    return out


# ── remembering and comparing ──────────────────────────────────────────────────────────────────
def _load() -> Dict[str, Any]:
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(state: Dict[str, Any]) -> None:
    tmp = f"{STATE_FILE}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


class ListingWatch:
    """One scrape run of one store: observe() per listing, finish() once at the end."""

    def __init__(self, store: str):
        self.store = store
        self.all = _load()
        self.state: Dict[str, Any] = self.all.setdefault(store, {})
        self.first_run = not self.state
        self.checked = self.pl = 0
        self.new_sellers: List[str] = []
        self.present_sellers: List[str] = []     # first look only
        self.rating: List[str] = []
        self.reviews: List[str] = []
        self.category: List[str] = []
        self.events: List[tuple] = []            # (kind, asin, sku, title, text) → the dashboard's Insights

    @staticmethod
    def _name(asin: str, sku: str, title: str) -> str:
        t = (title or "").strip()
        return f"`{asin}`" + (f" `{sku}`" if sku else "") + (f" {t[:48]}{'…' if len(t) > 48 else ''}" if t else "")

    def observe(self, asin: str, html: str = "", offers: Optional[List[Dict[str, Any]]] = None,
                sku: str = "", title: str = "") -> None:
        try:
            self._observe(str(asin or "").strip().upper(), html, offers, str(sku or "").strip(), str(title or "").strip())
        except Exception as e:                       # never in the way of the scrape
            print(f"    [listing watch] {asin}: {type(e).__name__}: {e}")

    def _observe(self, asin, html, offers, sku, title):
        if not asin:
            return
        f = facts_from_html(html)
        prev = self.state.get(asin)
        brand = f["brand"] or (prev or {}).get("brand")
        pl = is_pl_listing(brand, sku or (prev or {}).get("sku"), self.store)
        others = other_sellers(offers) if pl else []
        self.checked += 1
        self.pl += 1 if pl else 0
        who = self._name(asin, sku, title)

        def price_of(name):
            for o in offers or []:
                if str(o.get("seller_name") or "").strip() == name:
                    p = o.get("total_price") or o.get("item_price")
                    return f" ({p})" if p not in (None, "") else ""
            return ""

        if prev is None:
            if pl and others:
                names = ", ".join(n + price_of(n) for n in others[:4]) + (f" +{len(others) - 4}" if len(others) > 4 else "")
                self.present_sellers.append(f"• {who} — {names}")
                self.events.append(("own_brand_other_seller", asin, sku, title, f"Other seller on our own-brand listing: {names}"))
        elif pl:
            fresh = [n for n in others if n not in (prev.get("others") or [])]
            if fresh:
                names = ", ".join(n + price_of(n) for n in fresh[:4]) + (f" +{len(fresh) - 4}" if len(fresh) > 4 else "")
                self.new_sellers.append(f"• {who} — {names}")
                self.events.append(("own_brand_other_seller", asin, sku, title, f"NEW seller on our own-brand listing: {names}"))
            r0, r1 = prev.get("rating"), f["rating"]
            n0, n1 = prev.get("reviews"), f["reviews"]
            if r0 is not None and r1 is not None and abs(float(r0) - float(r1)) >= 0.05:
                arrow = "📉" if r1 < r0 else "📈"
                cnt = f" ({n0} → {n1} ratings)" if (n0 is not None and n1 is not None and n0 != n1) else ""
                self.rating.append(f"• {arrow} {who} — {r0} → {r1} ★{cnt}")
                self.events.append(("own_brand_rating", asin, sku, title, f"Star rating {r0} → {r1}{cnt}"))
            elif n0 is not None and n1 is not None and n0 != n1:
                star = f" ({r1} ★)" if r1 is not None else ""
                self.reviews.append(f"• {who} — {n0} → {n1} ratings{star}")
                self.events.append(("own_brand_reviews", asin, sku, title, f"Number of ratings {n0} → {n1}{star}"))
            c0, c1 = prev.get("category"), f["category"]
            if c0 and c1 and c0 != c1:
                self.category.append(f"• {who} — “{c0}” → “{c1}”")
                self.events.append(("own_brand_category", asin, sku, title, f"Category “{c0}” → “{c1}”"))

        keep = dict(prev or {})
        keep.update({"pl": pl, "seen": time.strftime("%Y-%m-%dT%H:%M")})
        for k in ("brand", "rating", "reviews", "category"):
            if f[k] is not None:                     # an unread value keeps the remembered one
                keep[k] = f[k]
        if offers and pl:                            # no offers read at all = unread, not "nobody else"
            keep["others"] = others
        if sku:
            keep["sku"] = sku
        if title:
            keep["title"] = title[:120]
        self.state[asin] = keep

    def message(self) -> str:
        def block(head, lines):
            if not lines:
                return ""
            more = f"\n… +{len(lines) - MAX_LINES} more (Repricer tab)" if len(lines) > MAX_LINES else ""
            return f"\n\n{head} ({len(lines)})\n" + "\n".join(lines[:MAX_LINES]) + more

        body = (block("🚨 *New seller on our listing*", self.new_sellers)
                + block("🚨 *Other sellers already on our listings* — first look", self.present_sellers)
                + block("⭐ *Star rating moved*", self.rating)
                + block("💬 *Number of ratings changed*", self.reviews)
                + block("📂 *Category changed*", self.category))
        if not body:
            return ""
        return (f"🏷️ *{self.store} — own-brand listings: what changed*\n"
                f"{self.checked} listings opened, {self.pl} of them own-brand" + body)

    def finish(self, send: bool = True) -> str:
        """Save what was seen and send the one message. Returns the message ('' when nothing changed)."""
        text = ""
        try:
            if self.checked:
                latest = _load()                     # the other store's run may have saved meanwhile
                latest[self.store] = self.state
                _save(latest)
            text = self.message()
            print(f"[listing watch] {self.store}: {self.checked} listings, {self.pl} own-brand — "
                  + (f"{len(self.new_sellers) + len(self.present_sellers)} with other sellers, "
                     f"{len(self.rating)} rating, {len(self.reviews)} ratings-count, {len(self.category)} category change(s)"
                     if text else "no change" + (" (first look: values recorded)" if self.first_run else "")))
            if text and send:
                from utils.whatsapp import get_whatsapp_group, send_whatsapp_message
                send_whatsapp_message(text, to=get_whatsapp_group(self.store, kind="insights"), background=False)
            if send:                                 # the same changes, one by one, on the dashboard's Insights tab
                from utils.listing_events import record
                for kind, asin, sku, title, msg in self.events:
                    record(self.store, asin, kind, msg, sku=sku, title=title)
        except Exception as e:
            print(f"[listing watch] {self.store}: could not finish ({type(e).__name__}: {e})")
        return text
