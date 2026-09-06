"""
Amazon Live Product Scraper — GUI
===================================
Scrapes Amazon product pages to extract:
  - Buy Box price & shipping
  - BSR (Best Sellers Rank) + sub-categories
  - Bought in past month
  - Rating & review count
  - Brand, availability, category
  - Seller info (ships from / sold by)

Uses Selenium to load the full page (handles variations correctly).
Falls back to plain HTTP requests if Selenium is unavailable.

Usage:  python3.12 Amazon_Live_Scraper.py
"""

import os, sys, re, threading, time, subprocess
import tkinter as tk
from tkinter import ttk

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError as e:
    print(f"Missing: {e}. Install: pip install requests beautifulsoup4 lxml")
    sys.exit(1)

# Selenium (optional but preferred for variation listings)
_HAS_SELENIUM = False
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    _HAS_SELENIUM = True
except ImportError:
    pass

# ─── Color Palette ───────────────────────────────────────────────────────────

BG        = "#0f1117"
BG_CARD   = "#1a1d27"
BG_INPUT  = "#252836"
BG_ROW_A  = "#1a1d27"
BG_ROW_B  = "#252836"
FG        = "#e4e6ef"
FG_DIM    = "#8b8fa3"
ACCENT    = "#6c63ff"
GREEN     = "#2dd4a0"
YELLOW    = "#f5c842"
RED       = "#ff6b6b"
CYAN      = "#38bdf8"
ORANGE    = "#fb923c"
BORDER    = "#2d3040"

# ─── Scraper Core ────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
    "Cookie": "i18n-prefs=USD",
}

# Shared Selenium driver (reused across queries)
_driver = None
_driver_lock = threading.Lock()


def parse_asin(raw: str) -> str:
    """Extract ASIN from a URL or raw string."""
    raw = raw.strip()
    m = re.search(r"/(?:dp|product|gp/product)/([A-Z0-9]{10})", raw, re.I)
    if m:
        return m.group(1).upper()
    cleaned = raw.upper().strip()
    if re.fullmatch(r"[A-Z0-9]{10}", cleaned):
        return cleaned
    return ""


def _get_driver():
    """Get or create a headless Chrome driver."""
    global _driver
    with _driver_lock:
        if _driver is not None:
            try:
                _driver.title  # test if alive
                return _driver
            except Exception:
                try:
                    _driver.quit()
                except Exception:
                    pass
                _driver = None

        opts = ChromeOptions()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--lang=en-US")
        opts.add_argument(
            "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        )
        # Stealth tweaks
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)

        _driver = webdriver.Chrome(options=opts)
        _driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })
        _driver.implicitly_wait(3)
        return _driver


def _fetch_html_selenium(asin: str) -> str:
    """Fetch the fully-rendered Amazon page HTML via Selenium."""
    driver = _get_driver()
    url = f"https://www.amazon.com/dp/{asin}"
    driver.get(url)
    # Wait for the price element to appear (up to 8 seconds)
    try:
        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.CSS_SELECTOR,
                ".a-price-whole, #priceblock_ourprice, #corePrice_feature_div"))
        )
    except Exception:
        pass  # proceed anyway, we may still get useful data
    time.sleep(1.5)  # let remaining JS settle
    return driver.page_source


def _fetch_html_requests(asin: str) -> str:
    """Fetch Amazon page HTML via plain requests (fallback)."""
    url = f"https://www.amazon.com/dp/{asin}"
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.text


def _parse_html(html: str, asin: str) -> dict:
    """Parse product data from Amazon HTML."""
    soup = BeautifulSoup(html, "lxml")
    text = html
    result = {"asin": asin, "url": f"https://www.amazon.com/dp/{asin}", "error": None}

    # ── Title ──
    el = soup.find(id="productTitle")
    result["title"] = el.get_text(strip=True) if el else "Unknown"

    # ── Price (multiple strategies) ──
    price = None

    # Strategy 1: .a-offscreen inside the core price display
    for sel in [
        "#corePrice_feature_div .a-price .a-offscreen",
        "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
        "#apex_desktop .a-price .a-offscreen",
        "#price_inside_buybox",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#newBuyBoxPrice",
    ]:
        el = soup.select_one(sel)
        if el:
            t = el.get_text(strip=True)
            if t and "$" in t:
                price = t
                break

    # Strategy 2: a-price-whole + fraction
    if not price:
        pw = soup.find("span", class_="a-price-whole")
        pf = soup.find("span", class_="a-price-fraction")
        if pw:
            whole = pw.get_text(strip=True).rstrip(".")
            frac = pf.get_text(strip=True) if pf else "00"
            candidate = f"${whole}.{frac}"
            # Sanity check: reject prices > $9999 (likely page artifacts)
            try:
                val = float(whole.replace(",", "") + "." + frac)
                if val < 9999:
                    price = candidate
            except ValueError:
                pass

    # Strategy 3: regex for the price near "ourprice" or "dealprice"
    if not price:
        m = re.search(r'(?:ourprice|dealprice|price-large)[^$]*\$([\d,]+\.\d{2})', text, re.I)
        if m:
            price = f"${m.group(1)}"

    result["price"] = price or "N/A"

    # ── Shipping / Delivery ──
    ship_match = re.search(r"\$([\d.]+)\s*delivery", text, re.I)
    if ship_match:
        result["shipping"] = f"${ship_match.group(1)}"
    else:
        free_ship = re.search(r"FREE\s+delivery|free shipping|FREE Shipping", text, re.I)
        result["shipping"] = "FREE" if free_ship else "—"

    # ── BSR ──
    bsr_all = re.findall(r"#([\d,]+)\s+in\s+([^\(<\n\"]+)", text)
    if bsr_all:
        result["bsr"] = f"#{bsr_all[0][0]} in {bsr_all[0][1].strip()[:60]}"
        result["bsr_sub"] = [f"#{r} in {c.strip()[:60]}" for r, c in bsr_all[1:4]]
        result["bsr_num"] = bsr_all[0][0]
    else:
        result["bsr"] = "N/A"
        result["bsr_sub"] = []
        result["bsr_num"] = None

    # ── Bought in past month ──
    bought = re.search(r"([\d,]+\+?)\s+bought\s+in\s+past\s+month", text)
    result["bought_month"] = bought.group(1) if bought else "N/A"

    # ── Rating & Reviews ──
    rating_el = soup.select_one("#acrPopover")
    result["rating"] = rating_el.get("title", "N/A") if rating_el else "N/A"
    review_el = soup.select_one("#acrCustomerReviewText")
    result["reviews"] = review_el.get_text(strip=True) if review_el else "N/A"

    # ── Brand ──
    brand_el = soup.select_one("#bylineInfo")
    if brand_el:
        bt = brand_el.get_text(strip=True)
        bt = re.sub(r"^(Visit the|Brand:)\s*", "", bt)
        bt = re.sub(r"\s*Store$", "", bt)
        result["brand"] = bt
    else:
        result["brand"] = "N/A"

    # ── Seller / Ships from ──
    # Try tabular buybox first (Selenium renders this)
    sold_by = None
    ships_from = None

    # Method 1: tabular-buybox (rendered by JS)
    bb_rows = soup.select("#tabular-buybox .tabular-buybox-text")
    bb_labels = soup.select("#tabular-buybox .tabular-buybox-text-message")
    for i, lbl in enumerate(bb_labels):
        lbl_text = lbl.get_text(strip=True).lower()
        if i < len(bb_rows):
            val = bb_rows[i].get_text(strip=True)
            if "sold by" in lbl_text or "seller" in lbl_text:
                sold_by = val
            elif "ships from" in lbl_text:
                ships_from = val

    # Method 2: regex fallback
    if not sold_by:
        m = re.search(r"Sold\s+by[:\s]*</span>\s*<span[^>]*>([^<]+)", text)
        if m:
            sold_by = m.group(1).strip()
    if not ships_from:
        m = re.search(r"Ships\s+from[:\s]*</span>\s*<span[^>]*>([^<]+)", text)
        if m:
            ships_from = m.group(1).strip()

    # Method 3: merchant-info
    if not sold_by:
        mi = soup.find(id="merchant-info")
        if mi:
            mi_text = mi.get_text(strip=True)
            if mi_text:
                sold_by = mi_text

    # Method 4: Shipper/Seller line
    if not sold_by:
        m = re.search(r"Shipper\s*/\s*Seller[:\s]*([^<\n]+)", text)
        if m:
            sold_by = m.group(1).strip()

    result["sold_by"] = sold_by or "—"
    result["ships_from"] = ships_from or "—"

    # ── Availability ──
    avail_el = soup.select_one("#availability span")
    result["availability"] = avail_el.get_text(strip=True) if avail_el else "—"

    # ── Category ──
    cats = soup.select("#wayfinding-breadcrumbs_container a")
    result["category"] = " › ".join(c.get_text(strip=True) for c in cats) or "—"

    # ── Prime ──
    result["prime"] = bool(
        soup.select_one("#prime-widget, #bbPrimeBadge, .prime-icon, #priceBadging_feature_div")
        or re.search(r"FREE\s+delivery.*?Prime", text, re.I | re.DOTALL)
    )

    # ── Variations ──
    variations = []
    for tw in soup.select("#twister .swatchAvailable, .twisterSwatchWrapper"):
        v_title = tw.get("title", "") or tw.get_text(strip=True)
        v_price_el = tw.select_one(".a-size-mini")
        v_price = v_price_el.get_text(strip=True) if v_price_el else ""
        if v_title:
            variations.append(f"{v_title} {v_price}".strip()[:60])
    result["variations"] = variations[:10]

    return result


def scrape_product(asin: str) -> dict:
    """Scrape product — uses Selenium first, falls back to requests."""
    try:
        if _HAS_SELENIUM:
            html = _fetch_html_selenium(asin)
        else:
            html = _fetch_html_requests(asin)
        return _parse_html(html, asin)
    except Exception as e:
        # If Selenium fails, try requests fallback
        if _HAS_SELENIUM:
            try:
                html = _fetch_html_requests(asin)
                data = _parse_html(html, asin)
                data["_note"] = "Selenium failed, used HTTP fallback"
                return data
            except Exception:
                pass
        return {"asin": asin, "url": f"https://www.amazon.com/dp/{asin}",
                "error": str(e)}


# ─── GUI ─────────────────────────────────────────────────────────────────────

class AmazonScraperApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Amazon Live Product Scraper")
        self.configure(bg=BG)
        self.geometry("960x780")
        self.minsize(780, 600)

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, font=("Helvetica Neue", 12))
        style.configure("Vertical.TScrollbar", background=BG_CARD,
                         troughcolor=BG, arrowcolor=FG_DIM)

        self._build_header()
        self._build_results()
        self._build_status()
        self.bind("<Return>", lambda _: self._on_search())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        global _driver
        if _driver:
            try:
                _driver.quit()
            except Exception:
                pass
        self.destroy()

    # ── Header ───────────────────────────────────────────────────────────────

    def _build_header(self):
        hdr = tk.Frame(self, bg=BG_CARD, padx=20, pady=16)
        hdr.pack(fill="x")

        row1 = tk.Frame(hdr, bg=BG_CARD)
        row1.pack(fill="x")
        tk.Label(row1, text="🛒", font=("Apple Color Emoji", 22),
                 bg=BG_CARD, fg=FG).pack(side="left", padx=(0, 8))
        tk.Label(row1, text="Amazon Live Scraper",
                 font=("Helvetica Neue", 20, "bold"),
                 bg=BG_CARD, fg=FG).pack(side="left")

        mode = "Selenium" if _HAS_SELENIUM else "HTTP (install selenium for variations)"
        mode_color = GREEN if _HAS_SELENIUM else YELLOW
        tk.Label(row1, text=f"Mode: {mode}", font=("Menlo", 10),
                 bg=BG_CARD, fg=mode_color).pack(side="right")

        row2 = tk.Frame(hdr, bg=BG_CARD)
        row2.pack(fill="x", pady=(12, 0))
        tk.Label(row2, text="ASIN / URL", font=("Helvetica Neue", 12, "bold"),
                 bg=BG_CARD, fg=FG_DIM).pack(side="left", padx=(0, 10))

        self.input_var = tk.StringVar()
        self.entry = tk.Entry(row2, textvariable=self.input_var,
                              font=("Menlo", 13), bg=BG_INPUT, fg=FG,
                              insertbackground=ACCENT, relief="flat",
                              highlightthickness=2, highlightbackground=BORDER,
                              highlightcolor=ACCENT, bd=8)
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.entry.focus_set()

        self.btn = tk.Button(row2, text="  Scrape  ",
                             font=("Helvetica Neue", 13, "bold"),
                             bg=ACCENT, fg="#fff", activebackground="#5a52e0",
                             activeforeground="#fff", relief="flat", bd=0,
                             cursor="hand2", command=self._on_search,
                             padx=20, pady=6)
        self.btn.pack(side="left")

    # ── Results (scrollable) ─────────────────────────────────────────────────

    def _build_results(self):
        container = tk.Frame(self, bg=BG)
        container.pack(fill="both", expand=True, padx=16, pady=(10, 0))

        self.canvas = tk.Canvas(container, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(container, orient="vertical",
                           command=self.canvas.yview, style="Vertical.TScrollbar")

        self.inner = tk.Frame(self.canvas, bg=BG)
        self.inner.bind("<Configure>",
                        lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw", tags="inner")
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfig("inner", width=e.width))
        self.canvas.bind_all("<MouseWheel>",
                             lambda e: self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        tk.Label(self.inner,
                 text="Paste an ASIN or Amazon URL above and press Scrape\nor hit Enter",
                 font=("Helvetica Neue", 15), bg=BG, fg=FG_DIM,
                 justify="center", pady=120).pack(fill="x")

    # ── Status ───────────────────────────────────────────────────────────────

    def _build_status(self):
        bar = tk.Frame(self, bg=BG_CARD, pady=6, padx=16)
        bar.pack(fill="x", side="bottom")
        self.status = tk.Label(bar, text="Ready", font=("Menlo", 10),
                               bg=BG_CARD, fg=FG_DIM, anchor="w")
        self.status.pack(fill="x")

    # ── Actions ──────────────────────────────────────────────────────────────

    def _on_search(self):
        raw = self.input_var.get().strip()
        asin = parse_asin(raw)
        if not asin:
            self.status.config(text="⚠️  Enter a valid ASIN or Amazon URL", fg=YELLOW)
            return
        self.btn.config(text="  Scraping…  ", state="disabled", bg="#4a4370")
        self.status.config(text=f"⏳ Scraping {asin} (loading page…)", fg=YELLOW)
        threading.Thread(target=self._worker, args=(asin,), daemon=True).start()

    def _worker(self, asin):
        data = scrape_product(asin)
        self.after(0, self._render, data)

    # ── Render ───────────────────────────────────────────────────────────────

    def _render(self, d):
        self.btn.config(text="  Scrape  ", state="normal", bg=ACCENT)
        for w in self.inner.winfo_children():
            w.destroy()

        if d.get("error"):
            self.status.config(text=f"❌ {d['error']}", fg=RED)
            tk.Label(self.inner, text=f"❌  {d['error']}",
                     font=("Helvetica Neue", 14), bg=BG, fg=RED, pady=60,
                     wraplength=700).pack()
            return

        # ── Title Card ──
        card = tk.Frame(self.inner, bg=BG_CARD, padx=20, pady=14)
        card.pack(fill="x", pady=(0, 8))
        tk.Label(card, text=d.get("title", "?"), font=("Helvetica Neue", 13, "bold"),
                 bg=BG_CARD, fg=FG, wraplength=860, justify="left",
                 anchor="w").pack(fill="x")
        info = f"ASIN: {d['asin']}"
        if d.get("brand") and d["brand"] != "N/A":
            info += f"  •  Brand: {d['brand']}"
        tk.Label(card, text=info, font=("Menlo", 11),
                 bg=BG_CARD, fg=FG_DIM, anchor="w").pack(fill="x", pady=(4, 0))

        # ── Metric Cards ──
        row = tk.Frame(self.inner, bg=BG)
        row.pack(fill="x", pady=(0, 8))
        self._metric(row, "Buy Box Price", d.get("price", "N/A"), GREEN)
        self._metric(row, "Shipping", d.get("shipping", "—"), CYAN)
        self._metric(row, "BSR", f"#{d['bsr_num']}" if d.get("bsr_num") else "N/A", ORANGE)
        self._metric(row, "Bought / Month", d.get("bought_month", "N/A"), YELLOW)

        # ── Detail Grid ──
        details_frame = tk.Frame(self.inner, bg=BG_CARD, padx=20, pady=12)
        details_frame.pack(fill="x", pady=(0, 8))

        rows = [
            ("Buy Box Price", d.get("price", "N/A")),
            ("Shipping", d.get("shipping", "—")),
            ("BSR (Main)", d.get("bsr", "N/A")),
        ]
        for sub in d.get("bsr_sub", []):
            rows.append(("  Sub-Category", sub))
        rows += [
            ("Bought in Past Month", d.get("bought_month", "N/A")),
            ("Rating", d.get("rating", "N/A")),
            ("Reviews", d.get("reviews", "N/A")),
            ("Brand", d.get("brand", "N/A")),
            ("Sold By", d.get("sold_by", "—")),
            ("Ships From", d.get("ships_from", "—")),
            ("Prime", "✅ Yes" if d.get("prime") else "❌ No / Unknown"),
            ("Availability", d.get("availability", "—")),
            ("Category", d.get("category", "—")),
        ]

        for i, (label, value) in enumerate(rows):
            bg_c = BG_ROW_A if i % 2 == 0 else BG_ROW_B
            r = tk.Frame(details_frame, bg=bg_c, pady=4, padx=8)
            r.pack(fill="x")
            tk.Label(r, text=label, font=("Helvetica Neue", 11),
                     bg=bg_c, fg=FG_DIM, width=24, anchor="w").pack(side="left")
            vc = FG
            if label == "Buy Box Price": vc = GREEN
            elif "BSR" in label: vc = ORANGE
            elif label == "Bought in Past Month" and value != "N/A": vc = YELLOW
            elif label == "Sold By" and value != "—": vc = CYAN
            tk.Label(r, text=value, font=("Menlo", 11, "bold"),
                     bg=bg_c, fg=vc, anchor="w", wraplength=600,
                     justify="left").pack(side="left", fill="x")

        # ── Variations ──
        variations = d.get("variations", [])
        if variations:
            vf = tk.Frame(self.inner, bg=BG_CARD, padx=20, pady=12)
            vf.pack(fill="x", pady=(0, 8))
            tk.Label(vf, text=f"📦  Variations ({len(variations)})",
                     font=("Helvetica Neue", 13, "bold"),
                     bg=BG_CARD, fg=FG).pack(anchor="w")
            for v in variations:
                tk.Label(vf, text=f"  • {v}", font=("Menlo", 11),
                         bg=BG_CARD, fg=FG_DIM, anchor="w").pack(fill="x")

        # ── Note if fallback was used ──
        if d.get("_note"):
            tk.Label(self.inner, text=f"⚠️  {d['_note']}",
                     font=("Menlo", 10), bg=BG, fg=YELLOW, pady=4).pack(fill="x")

        # ── Amazon Link ──
        lf = tk.Frame(self.inner, bg=BG_CARD, padx=20, pady=10)
        lf.pack(fill="x", pady=(0, 8))
        ll = tk.Label(lf, text=f"🔗  {d['url']}", font=("Menlo", 10),
                      bg=BG_CARD, fg=ACCENT, cursor="hand2", anchor="w")
        ll.pack(fill="x")
        ll.bind("<Button-1>", lambda _: os.system(f"open '{d['url']}'"))

        self.status.config(text=f"✅ Done — {d['asin']}", fg=GREEN)
        self.canvas.yview_moveto(0)

    def _metric(self, parent, label, value, color):
        card = tk.Frame(parent, bg=BG_CARD, padx=16, pady=12)
        card.pack(side="left", fill="both", expand=True, padx=(0, 6))
        tk.Label(card, text=label, font=("Helvetica Neue", 10),
                 bg=BG_CARD, fg=FG_DIM).pack(anchor="w")
        tk.Label(card, text=value, font=("Menlo", 18, "bold"),
                 bg=BG_CARD, fg=color).pack(anchor="w", pady=(4, 0))


if __name__ == "__main__":
    AmazonScraperApp().mainloop()
