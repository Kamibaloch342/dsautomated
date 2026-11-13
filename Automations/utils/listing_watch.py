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
PAGES_DIR = os.path.join(_AUTO, "data", "listing_watch_pages")   # refused / held pages, for a person to look at
KEEP_PAGES = 40                                                   # per store and run

# Our own brands as Amazon shows them on the product page ("Visit the HOANK Store", "Brand: Hathoro").
# Wrenchly since 13 Nov 2026: without it the listing guard took the Wrenchly-brand B0HR7QZ18R for
# somebody else's product and set its quantity 492 → 0.
PL_BRANDS = ("hoank", "hathoro", "wrenchly")
# Sellers allowed on our PL listings: our three stores, under every name Amazon has shown for them.
# (The same list as dashboard/repricer_engine.py AUTHORIZED_PL_SELLERS — keep the two in step.)
AUTHORIZED_SELLERS = ("hathoro", "hoank", "wrenchly", "qurtdis", "hypersale", "hyper sale", "abecomllc26")
MAX_MESSAGES = 25         # WhatsApp messages per store and run (one per alert); the rest are counted in the last


# ── reading the page ───────────────────────────────────────────────────────────────────────────
def facts_from_html(html: str) -> Dict[str, Any]:
    """{'brand', 'rating', 'reviews', 'category'} from an Amazon product page; None for what is not there."""
    out: Dict[str, Any] = {"brand": None, "rating": None, "reviews": None, "category": None, "page_asin": None}
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

    # Which product the page really is (the hidden ASIN field of the buy form, else the canonical link)
    pa = soup.select_one("input#ASIN")
    page_asin = str(pa.get("value") or "").strip() if pa else ""
    if not page_asin:
        can = soup.select_one('link[rel="canonical"]')
        m = re.search(r"/dp/([A-Z0-9]{10})", str(can.get("href") or "")) if can else None
        page_asin = m.group(1) if m else ""
    out["page_asin"] = page_asin.upper() or None

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


def pool_swing(n0, n1) -> bool:
    """True when the number of ratings jumped too far to be new reviews. Real reviews move a listing
    by a few per 12 h. Until 12 Nov 2026 every such jump was a SCRAPING error: the scraper read a
    page without our offer on it — another product's page — and took its numbers (B0HH6PP866
    "303 ratings, Motor Flushers" vs its own 7, Antifreezes; HOANK's pothole repair "43,466").
    _observe now refuses those pages, so a jump that still gets through is unusual: check it."""
    if n0 is None or n1 is None:
        return False
    lo, hi = sorted((int(n0), int(n1)))
    return hi - lo >= 10 and hi >= 1.5 * max(lo, 1)


def _ratings(n) -> str:
    return f"{int(n):,} rating" + ("" if int(n) == 1 else "s")


def _short_category(cat: Optional[str]) -> str:
    """The last two levels: 'Oils & Fluids › Antifreezes & Coolants'."""
    parts = [p.strip() for p in str(cat or "").split("›") if p.strip()]
    return " › ".join(parts[-2:])


def brand_is_ours(brand: Optional[str]) -> bool:
    b = (brand or "").strip().lower()
    return bool(b) and any(p in b for p in PL_BRANDS)


def sku_is_pl(sku: Optional[str], store: str = "") -> bool:
    """The SKU-prefix rule: the answer when the page's brand line could not be read, and the listing
    guard's second opinion when the brand says somebody else's (engine/listing_guard.classify)."""
    s = (sku or "").strip().lower()
    if not s:
        return False
    st = (store or "").lower()
    if "hath" in st:
        return "hath" in s or s.startswith(("ht_", "ht-", "ht "))
    if "wren" in st:
        return s.startswith("wrench")
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
        self.checked = self.pl = self.skipped = self.held = self.kept_pages = 0
        self.new_sellers: List[Dict[str, Any]] = []
        self.present_sellers: List[Dict[str, Any]] = []   # first look only
        self.changes: List[Dict[str, Any]] = []  # rating / number of ratings / category, one dict per listing
        self.events: List[tuple] = []            # (kind, asin, sku, title, text) → the dashboard's Insights

    @staticmethod
    def _title(title: str) -> str:
        """The product title up to its first '|', cut at a word near 60 characters."""
        t = re.split(r"\s+\|\s+", (title or "").strip(), maxsplit=1)[0].strip(" ,-–")
        if len(t) > 60:
            t = t[:60].rsplit(" ", 1)[0].rstrip(" ,-–(") + "…"
        return t

    def _keep_page(self, asin: str, html: str, why: str) -> None:
        """A refused or held page, kept so a person can see what Amazon served:
        data/listing_watch_pages/<store>_<ASIN>.html.gz (latest one per listing, at most
        KEEP_PAGES per run). Never in the way of the scrape."""
        if not html or self.kept_pages >= KEEP_PAGES:
            return
        try:
            import gzip
            os.makedirs(PAGES_DIR, exist_ok=True)
            path = os.path.join(PAGES_DIR, f"{self.store}_{asin}.html.gz")
            with gzip.open(path + ".tmp", "wt", encoding="utf-8") as fh:
                fh.write(f"<!-- {time.strftime('%Y-%m-%d %H:%M')} · {why} -->\n{html}")
            os.replace(path + ".tmp", path)
            self.kept_pages += 1
        except Exception:
            pass

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
        # Readings that are not our listing's (12 Nov 2026). B0HH6PP866 was read three runs running
        # from a page with NO offer on it and recorded 303 ratings, 4.4 ★, "Motor Flushers"; every
        # page with our $69.99 offer shows its own 7 ratings, 4.8 ★, Antifreezes. HOANK's pothole
        # repair read 43,466 the same way. But offer-less pages are common (12 % to 80 % of a run)
        # and most still show our own numbers (the shipping boxes' 115), so:
        #   • the page is another ASIN (input#ASIN / canonical)        → nothing from it is used
        #   • no offer and no product id on the page                     → nothing is used
        #   • no offer, our ASIN: a first reading, a big jump in the number of ratings or a category
        #     change waits for a page that shows an offer — small changes are used as they are.
        no_offer = offers is not None and not offers
        why, held = "", ""
        if f.get("page_asin") and f["page_asin"] != asin:
            why = f"the page was {f['page_asin']}, not this listing"
        elif no_offer and not f.get("page_asin"):
            why = "the page showed no offer and no product id"
        elif no_offer and any(f[k] is not None for k in ("rating", "reviews", "category")):
            p = prev or {}
            if p.get("rating") is None and p.get("reviews") is None:
                held = "first reading"
            elif pool_swing(p.get("reviews"), f["reviews"]):
                held = f"ratings {p.get('reviews')} → {f['reviews']}"
            elif p.get("category") and f["category"] and p["category"] != f["category"]:
                held = "category change"
        if why or held:
            print(f"    [listing watch] {asin}: " + (f"{why} — rating, ratings and category not used" if why else
                  f"{held} on a page without an offer — waits for a page that shows one"))
            if why:
                self.skipped += 1
            else:
                self.held += 1
            for k in (("brand", "rating", "reviews", "category") if why else ("rating", "reviews", "category")):
                f[k] = None
            self._keep_page(asin, html, why or held)
        brand = f["brand"] or (prev or {}).get("brand")
        pl = is_pl_listing(brand, sku or (prev or {}).get("sku"), self.store)
        others = other_sellers(offers) if pl else []
        self.checked += 1
        self.pl += 1 if pl else 0
        sku = sku or str((prev or {}).get("sku") or "")
        title = title or str((prev or {}).get("title") or "")
        base = {"asin": asin, "sku": sku, "title": self._title(title)}

        def price_of(name):
            for o in offers or []:
                if str(o.get("seller_name") or "").strip() == name:
                    p = o.get("total_price") or o.get("item_price")
                    return str(p) if p not in (None, "") else ""
            return ""

        def seller_text(names):
            return ", ".join(n + (f" ({price_of(n)})" if price_of(n) else "") for n in names[:4]) + \
                (f" +{len(names) - 4}" if len(names) > 4 else "")

        swing = flip = False
        if prev is None:
            if pl and others:
                self.present_sellers.append(dict(base, sellers=[(n, price_of(n)) for n in others]))
                self.events.append(("own_brand_other_seller", asin, sku, title,
                                    f"Other seller on our own-brand listing: {seller_text(others)}"))
        elif pl:
            fresh = [n for n in others if n not in (prev.get("others") or [])]
            if fresh:
                self.new_sellers.append(dict(base, sellers=[(n, price_of(n)) for n in fresh]))
                self.events.append(("own_brand_other_seller", asin, sku, title,
                                    f"NEW seller on our own-brand listing: {seller_text(fresh)}"))
            r0, r1 = prev.get("rating"), f["rating"]
            n0, n1 = prev.get("reviews"), f["reviews"]
            c0, c1 = prev.get("category"), f["category"]
            r_moved = r0 is not None and r1 is not None and abs(float(r0) - float(r1)) >= 0.05
            n_moved = n0 is not None and n1 is not None and n0 != n1
            c_moved = bool(c0 and c1 and c0 != c1)
            swing = pool_swing(n0, n1)
            # back to the pool it showed before the last swing (the towel bars went 3,912 → 25 on
            # 11 Nov and 25 → 3,913 on 12 Nov): Amazon is switching it back and forth
            pf = prev.get("pool_from")
            flip = swing and pf is not None and abs(int(n1) - int(pf)) <= max(3, 0.05 * int(pf))
            if r_moved or n_moved or c_moved:
                self.changes.append(dict(base, r0=r0, r1=r1, n0=n0, n1=n1,
                                         c0=c0 if c_moved else None, c1=c1 if c_moved else None,
                                         swing=swing, flip=flip))
            if swing:
                txt = (f"Amazon now shows {n1:,} ratings ({r1} ★) instead of {n0:,} ({r0} ★) — a different "
                       f"product's review pool, not new reviews")
                self.events.append(("own_brand_reviews", asin, sku, title, txt))
            elif r_moved:
                cnt = f", {n0} → {n1} ratings" if n_moved else ""
                self.events.append(("own_brand_rating", asin, sku, title, f"Star rating {r0} → {r1}{cnt}"))
            elif n_moved:
                star = f" ({r1} ★)" if r1 is not None else ""
                self.events.append(("own_brand_reviews", asin, sku, title, f"Number of ratings {n0} → {n1}{star}"))
            if c_moved:
                self.events.append(("own_brand_category", asin, sku, title, f"Category “{c0}” → “{c1}”"))

        keep = dict(prev or {})
        if swing:
            keep["pool_from"] = prev.get("reviews")       # the pool it showed before this swing
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

    # ── the messages: ONE WhatsApp message per alert ─────────────────────────────────────────
    # Owner, 12 Nov 2026: one long message per run was "stuffed, not clear" (twice). Now every alert
    # is its own short message — a bold line saying what happened, the product, then one fact per
    # bullet. Listings that share one review pool (the 16 shipping-box listings) are ONE alert. A jump
    # too big for new reviews (pool_swing) has its own alert asking for a look — such jumps used to be
    # scraping errors (pages without our offer), which _observe now refuses.
    def _head(self, emoji: str, what: str, c: Dict[str, Any]) -> List[str]:
        lines = [f"{emoji} *{self.store}: {what}*"]
        if c.get("title"):
            lines.append(c["title"])
        lines.append(f"• ASIN: {c['asin']}")
        if c.get("sku"):
            lines.append(f"• SKU: {c['sku']}")
        return lines

    def _seller_messages(self) -> List[str]:
        out = []
        for c, what in ([(c, "new seller on our listing") for c in self.new_sellers]
                        + [(c, "other seller already on our listing") for c in self.present_sellers]):
            lines = self._head("🚨", what, c)
            for name, price in c["sellers"][:6]:
                lines.append(f"• Seller: {name}" + (f" at {price}" if price else ""))
            if len(c["sellers"]) > 6:
                lines.append(f"• …and {len(c['sellers']) - 6} more sellers")
            out.append("\n".join(lines))
        return out

    def _rating_messages(self) -> List[str]:
        real = [c for c in self.changes if not c["swing"] and (c["n0"] != c["n1"] or
                (c["r0"] is not None and c["r1"] is not None and abs(float(c["r0"]) - float(c["r1"])) >= 0.05))]
        groups: Dict[tuple, List[Dict[str, Any]]] = {}
        for c in real:
            # the same before/after on several listings = one shared pool (only for real counts;
            # two unrelated listings can both go 6 → 7)
            key = (c["r0"], c["r1"], c["n0"], c["n1"]) if (c["n1"] or 0) >= 10 else (c["asin"],)
            groups.setdefault(key, []).append(c)
        rows = []
        for g in groups.values():
            c = g[0]
            r0, r1, n0, n1 = c["r0"], c["r1"], c["n0"], c["n1"]
            moved = r0 is not None and r1 is not None and abs(float(r0) - float(r1)) >= 0.05
            d = (n1 - n0) if (n0 is not None and n1 is not None) else 0
            what = ("new rating" if d == 1 else "new ratings" if d > 1 else
                    "ratings went down" if d < 0 else "star rating changed")
            lines = self._head("⭐", what, c)
            if n1 is not None:
                lines.append(f"• Ratings: {n0:,} → {n1:,}" + (f" ({d:+d})".replace("-", "−") if d else "")
                             if n0 is not None else f"• Ratings: {n1:,}")
            if r1 is not None:
                lines.append(f"• Stars: {r0} → {r1} " + ("📉" if float(r1) < float(r0) else "📈")
                             if moved else f"• Stars: {r1}")
            if len(g) > 1:
                k = len(g) - 1
                lines.append(f"• The same reviews show on {k} more of our listing{'s' if k > 1 else ''}")
            rows.append((0 if moved else 1, -len(g), "\n".join(lines)))
        return [r[2] for r in sorted(rows, key=lambda r: (r[0], r[1]))]

    def _swing_messages(self) -> List[str]:
        out = []
        for c in self.changes:
            if not c["swing"]:
                continue
            lines = self._head("🔀", "big jump in ratings", c)
            lines.append(f"• Now: {_ratings(c['n1'])} · {c['r1']} ★")
            lines.append(f"• Before: {_ratings(c['n0'])} · {c['r0']} ★")
            if c["c1"]:
                lines.append(f"• Category now: {_short_category(c['c1'])}")
                lines.append(f"• Category before: {_short_category(c['c0'])}")
            lines.append("• Too big a jump for new reviews: please open the listing and check"
                         + (" (it keeps switching between these two numbers)" if c["flip"] else ""))
            out.append("\n".join(lines))
        return out

    def _category_messages(self) -> List[str]:
        out = []
        for c in self.changes:
            if c["c1"] and not c["swing"]:
                lines = self._head("📂", "category changed", c)
                lines.append(f"• Now: {_short_category(c['c1'])}")
                lines.append(f"• Before: {_short_category(c['c0'])}")
                out.append("\n".join(lines))
        return out

    def messages(self) -> List[str]:
        """Every alert of this run, one message each, most important first; capped at MAX_MESSAGES
        with the rest counted in a last message."""
        msgs = (self._seller_messages() + self._rating_messages()
                + self._swing_messages() + self._category_messages())
        if len(msgs) > MAX_MESSAGES:
            rest = len(msgs) - (MAX_MESSAGES - 1)
            msgs = msgs[:MAX_MESSAGES - 1] + [f"🏷️ *{self.store}: {rest} more listing changes*\n"
                                              f"• See the Repricer tab on the dashboard"]
        return msgs

    def message(self) -> str:
        """All of this run's alerts as one text (logs / tests); the group gets messages() one by one."""
        return "\n\n".join(self.messages())

    def finish(self, send: bool = True) -> str:
        """Save what was seen and send one message per alert. Returns them joined ('' when nothing changed)."""
        text = ""
        try:
            if self.checked:
                latest = _load()                     # the other store's run may have saved meanwhile
                latest[self.store] = self.state
                _save(latest)
            msgs = self.messages()
            text = "\n\n".join(msgs)
            print(f"[listing watch] {self.store}: {self.checked} listings, {self.pl} own-brand"
                  + (f", {self.skipped} page(s) not used (other ASIN / no product id)" if self.skipped else "")
                  + (f", {self.held} reading(s) held for a page with an offer" if self.held else "") + " — "
                  + (f"{len(self.new_sellers) + len(self.present_sellers)} with other sellers, "
                     f"{len(self._rating_messages())} rating alert(s), {len(self._swing_messages())} review-pool "
                     f"swing(s), {len(self._category_messages())} category change(s) → {len(msgs)} message(s)"
                     if msgs else "no change" + (" (first look: values recorded)" if self.first_run else "")))
            if msgs and send:
                from utils.whatsapp import get_whatsapp_group, send_whatsapp_message
                group = get_whatsapp_group(self.store, kind="insights")
                for m in msgs:
                    send_whatsapp_message(m, to=group, background=False)
            if send:                                 # the same changes, one by one, on the dashboard's Insights tab
                from utils.listing_events import record
                for kind, asin, sku, title, msg in self.events:
                    record(self.store, asin, kind, msg, sku=sku, title=title)
        except Exception as e:
            print(f"[listing watch] {self.store}: could not finish ({type(e).__name__}: {e})")
        return text
