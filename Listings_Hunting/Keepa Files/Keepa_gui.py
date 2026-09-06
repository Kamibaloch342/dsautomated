"""
Keepa Price Checker — GUI
==========================
Simple Tkinter GUI to look up live Buy Box prices and all seller offers
for any Amazon ASIN via the Keepa API.

Usage:
  python3.12 Keepa_gui.py
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timezone, timedelta

try:
    import requests
except ImportError:
    print("❌ 'requests' library not found. Install: pip install requests")
    sys.exit(1)


# ─── Configuration ───────────────────────────────────────────────────────────

KEEPA_API_KEY = os.environ.get(
    "KEEPA_API_KEY",
    "b2m5s4sjiv6i5c6tvhrn0ktapi77dmuuuiu841buheto3h3b4q9g0fgpu1bukhkm",
)
AMAZON_DOMAIN = 1  # Amazon.com (US)
KEEPA_API_BASE = "https://api.keepa.com"
KEEPA_EPOCH = datetime(2011, 1, 1, tzinfo=timezone.utc)

# CSV indices
CSV_AMAZON = 0
CSV_NEW = 1
CSV_NEW_FBM = 7
CSV_NEW_FBA = 10
CSV_COUNT_NEW = 11
CSV_SALES_RANK = 3
CSV_LIST_PRICE = 5
CSV_BUY_BOX = 18

CONDITION_MAP = {
    1: "New",
    2: "Used – Like New",
    3: "Used – Very Good",
    4: "Used – Good",
    5: "Used – Acceptable",
    6: "Refurbished",
}

# ─── Color Palette ───────────────────────────────────────────────────────────

BG_DARK = "#0f1117"
BG_CARD = "#1a1d27"
BG_INPUT = "#252836"
BG_HOVER = "#2d3040"
BG_TABLE_ROW = "#1e2130"
BG_TABLE_ALT = "#252836"
BG_HIGHLIGHT = "#1b3a2d"

FG_PRIMARY = "#e4e6ef"
FG_SECONDARY = "#8b8fa3"
FG_ACCENT = "#6c63ff"
FG_GREEN = "#2dd4a0"
FG_YELLOW = "#f5c842"
FG_RED = "#ff6b6b"
FG_CYAN = "#38bdf8"
FG_ORANGE = "#fb923c"

BORDER_COLOR = "#2d3040"


# ─── API Helpers ─────────────────────────────────────────────────────────────

def keepa_time_to_dt(km):
    return KEEPA_EPOCH + timedelta(minutes=km)

def to_dollars(v):
    if v is None or v < 0:
        return None
    return v / 100.0


def fetch_product(asin: str) -> tuple[dict | None, str | None, int | None]:
    """Returns (product_dict, error_string, tokens_left)."""
    params = {
        "key": KEEPA_API_KEY,
        "domain": AMAZON_DOMAIN,
        "asin": asin.strip(),
        "stats": 1,
        "buybox": 1,
        "offers": 20,
    }
    try:
        r = requests.get(f"{KEEPA_API_BASE}/product", params=params, timeout=30)
        data = r.json()
    except Exception as e:
        return None, str(e), None

    tokens = data.get("tokensLeft")

    if r.status_code != 200:
        # Retry without buybox/offers
        try:
            r2 = requests.get(
                f"{KEEPA_API_BASE}/product",
                params={"key": KEEPA_API_KEY, "domain": AMAZON_DOMAIN,
                        "asin": asin.strip(), "stats": 1},
                timeout=30,
            )
            data = r2.json()
            tokens = data.get("tokensLeft")
            if r2.status_code != 200:
                return None, data.get("error", {}).get("message", "API error"), tokens
        except Exception as e:
            return None, str(e), tokens

    products = data.get("products", [])
    if not products:
        return None, f"No product found for ASIN {asin}", tokens
    return products[0], None, tokens


# ─── GUI ─────────────────────────────────────────────────────────────────────

class KeepaApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Keepa Price Checker")
        self.configure(bg=BG_DARK)
        self.geometry("980x820")
        self.minsize(800, 650)

        # ── Style ──
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self._configure_styles()

        # ── Header ──
        self._build_header()
        # ── Results area (scrollable) ──
        self._build_results_area()
        # ── Status bar ──
        self._build_status_bar()

        self.bind("<Return>", lambda e: self._on_search())

    # ── Styles ───────────────────────────────────────────────────────────────

    def _configure_styles(self):
        s = self.style
        s.configure(".", background=BG_DARK, foreground=FG_PRIMARY,
                     fieldbackground=BG_INPUT, font=("Helvetica Neue", 12))

        # Treeview
        s.configure("Offers.Treeview",
                     background=BG_TABLE_ROW, foreground=FG_PRIMARY,
                     fieldbackground=BG_TABLE_ROW, rowheight=30,
                     font=("Menlo", 11), borderwidth=0)
        s.configure("Offers.Treeview.Heading",
                     background=BG_INPUT, foreground=FG_ACCENT,
                     font=("Helvetica Neue", 11, "bold"), borderwidth=0,
                     relief="flat")
        s.map("Offers.Treeview",
               background=[("selected", "#3b3f5c")],
               foreground=[("selected", FG_GREEN)])
        s.map("Offers.Treeview.Heading",
               background=[("active", BG_HOVER)])

        # Scrollbar
        s.configure("Vertical.TScrollbar",
                     background=BG_CARD, troughcolor=BG_DARK,
                     arrowcolor=FG_SECONDARY, borderwidth=0)

    # ── Header ───────────────────────────────────────────────────────────────

    def _build_header(self):
        hdr = tk.Frame(self, bg=BG_CARD, pady=16, padx=20)
        hdr.pack(fill="x")

        # Title row
        title_frame = tk.Frame(hdr, bg=BG_CARD)
        title_frame.pack(fill="x")

        tk.Label(title_frame, text="🔍", font=("Apple Color Emoji", 22),
                 bg=BG_CARD, fg=FG_PRIMARY).pack(side="left", padx=(0, 8))
        tk.Label(title_frame, text="Keepa Price Checker",
                 font=("Helvetica Neue", 20, "bold"),
                 bg=BG_CARD, fg=FG_PRIMARY).pack(side="left")

        self.tokens_label = tk.Label(
            title_frame, text="Tokens: —",
            font=("Menlo", 11), bg=BG_CARD, fg=FG_SECONDARY)
        self.tokens_label.pack(side="right", padx=(0, 4))

        # Search row
        search_frame = tk.Frame(hdr, bg=BG_CARD)
        search_frame.pack(fill="x", pady=(12, 0))

        tk.Label(search_frame, text="ASIN",
                 font=("Helvetica Neue", 12, "bold"),
                 bg=BG_CARD, fg=FG_SECONDARY).pack(side="left", padx=(0, 10))

        self.asin_var = tk.StringVar()
        self.asin_entry = tk.Entry(
            search_frame, textvariable=self.asin_var,
            font=("Menlo", 14), bg=BG_INPUT, fg=FG_PRIMARY,
            insertbackground=FG_ACCENT, relief="flat",
            highlightthickness=2, highlightbackground=BORDER_COLOR,
            highlightcolor=FG_ACCENT, bd=8)
        self.asin_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.asin_entry.focus_set()

        self.search_btn = tk.Button(
            search_frame, text="  Search  ",
            font=("Helvetica Neue", 13, "bold"),
            bg=FG_ACCENT, fg="#ffffff", activebackground="#5a52e0",
            activeforeground="#ffffff", relief="flat", bd=0,
            cursor="hand2", command=self._on_search, padx=20, pady=6)
        self.search_btn.pack(side="left")

    # ── Results area ─────────────────────────────────────────────────────────

    def _build_results_area(self):
        container = tk.Frame(self, bg=BG_DARK)
        container.pack(fill="both", expand=True, padx=16, pady=(10, 0))

        canvas = tk.Canvas(container, bg=BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical",
                                  command=canvas.yview,
                                  style="Vertical.TScrollbar")
        self.results_frame = tk.Frame(canvas, bg=BG_DARK)

        self.results_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        canvas.create_window((0, 0), window=self.results_frame, anchor="nw",
                             tags="inner")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Resize inner frame width with canvas
        def _on_canvas_resize(event):
            canvas.itemconfig("inner", width=event.width)
        canvas.bind("<Configure>", _on_canvas_resize)

        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.canvas = canvas

        # Mouse scroll
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # Placeholder
        self.placeholder = tk.Label(
            self.results_frame,
            text="Enter an ASIN above and press Search\nor hit Enter",
            font=("Helvetica Neue", 15), bg=BG_DARK, fg=FG_SECONDARY,
            justify="center", pady=120)
        self.placeholder.pack(fill="x")

    # ── Status bar ───────────────────────────────────────────────────────────

    def _build_status_bar(self):
        bar = tk.Frame(self, bg=BG_CARD, pady=6, padx=16)
        bar.pack(fill="x", side="bottom")
        self.status_label = tk.Label(
            bar, text="Ready", font=("Menlo", 10),
            bg=BG_CARD, fg=FG_SECONDARY, anchor="w")
        self.status_label.pack(fill="x")

    # ── Actions ──────────────────────────────────────────────────────────────

    def _set_status(self, text, color=FG_SECONDARY):
        self.status_label.config(text=text, fg=color)

    def _set_loading(self, loading: bool):
        if loading:
            self.search_btn.config(text="  Loading…  ", state="disabled",
                                   bg="#4a4370")
            self._set_status("⏳ Querying Keepa API…", FG_YELLOW)
        else:
            self.search_btn.config(text="  Search  ", state="normal",
                                   bg=FG_ACCENT)

    def _on_search(self):
        asin = self.asin_var.get().strip().upper()
        if not asin:
            self._set_status("⚠️  Enter an ASIN first", FG_YELLOW)
            return
        if len(asin) != 10:
            self._set_status("⚠️  ASIN should be exactly 10 characters", FG_YELLOW)
            return

        self._set_loading(True)
        threading.Thread(target=self._fetch_thread, args=(asin,),
                         daemon=True).start()

    def _fetch_thread(self, asin):
        product, error, tokens = fetch_product(asin)
        # Schedule UI update on main thread
        self.after(0, self._display_results, asin, product, error, tokens)

    # ── Display ──────────────────────────────────────────────────────────────

    def _clear_results(self):
        for w in self.results_frame.winfo_children():
            w.destroy()

    def _display_results(self, asin, product, error, tokens):
        self._set_loading(False)
        self._clear_results()

        if tokens is not None:
            color = FG_GREEN if tokens > 20 else (FG_YELLOW if tokens > 5 else FG_RED)
            self.tokens_label.config(text=f"Tokens: {tokens}", fg=color)

        if error:
            self._set_status(f"❌ {error}", FG_RED)
            tk.Label(self.results_frame, text=f"❌  {error}",
                     font=("Helvetica Neue", 14), bg=BG_DARK, fg=FG_RED,
                     pady=60, wraplength=700).pack(fill="x")
            return

        stats = product.get("stats", {})
        title = product.get("title", "Unknown")

        # ── Product Card ──
        card = tk.Frame(self.results_frame, bg=BG_CARD, padx=20, pady=16)
        card.pack(fill="x", pady=(0, 10))

        tk.Label(card, text=title, font=("Helvetica Neue", 13, "bold"),
                 bg=BG_CARD, fg=FG_PRIMARY, wraplength=880,
                 justify="left", anchor="w").pack(fill="x")
        tk.Label(card, text=f"ASIN: {asin}",
                 font=("Menlo", 11), bg=BG_CARD, fg=FG_SECONDARY,
                 anchor="w").pack(fill="x", pady=(4, 0))

        # ── Price Cards Row ──
        price_row = tk.Frame(self.results_frame, bg=BG_DARK)
        price_row.pack(fill="x", pady=(0, 10))

        bb_price = to_dollars(stats.get("buyBoxPrice", -1))
        bb_ship = to_dollars(stats.get("buyBoxShipping", -1))
        bb_total = (bb_price or 0) + (bb_ship or 0) if bb_price else None

        self._price_card(price_row, "Buy Box Price",
                         f"${bb_price:.2f}" if bb_price else "N/A",
                         FG_GREEN, side="left")

        self._price_card(price_row, "Shipping",
                         f"${bb_ship:.2f}" if bb_ship and bb_ship > 0
                         else "FREE" if bb_price else "N/A",
                         FG_CYAN if bb_ship and bb_ship > 0 else FG_GREEN,
                         side="left")

        self._price_card(price_row, "Total (Price+Ship)",
                         f"${bb_total:.2f}" if bb_total else "N/A",
                         FG_YELLOW, side="left")

        # Sales rank
        current = stats.get("current", [])
        rank = current[CSV_SALES_RANK] if len(current) > CSV_SALES_RANK else -1
        self._price_card(price_row, "Sales Rank",
                         f"#{rank:,}" if rank and rank > 0 else "N/A",
                         FG_ORANGE, side="left")

        # ── Details Grid ──
        details = tk.Frame(self.results_frame, bg=BG_CARD, padx=20, pady=14)
        details.pack(fill="x", pady=(0, 10))

        bb_seller = stats.get("buyBoxSellerId", "")
        is_fba = stats.get("buyBoxIsFBA", False)
        is_amazon = bb_seller.upper() in ("ATVPDKIKX0DER", "A2R2RITDJNW1Q6")
        seller_type = "Amazon" if is_amazon else ("FBA" if is_fba else "FBM")

        # Lowest New 3P
        new_3p = to_dollars(current[CSV_NEW]) if len(current) > CSV_NEW else None
        fbm_p = to_dollars(current[CSV_NEW_FBM]) if len(current) > CSV_NEW_FBM else None
        fba_p = to_dollars(current[CSV_NEW_FBA]) if len(current) > CSV_NEW_FBA else None
        count_new = current[CSV_COUNT_NEW] if len(current) > CSV_COUNT_NEW and current[CSV_COUNT_NEW] >= 0 else None
        is_prime = stats.get("buyBoxIsPrimeEligible", False)

        detail_items = [
            ("Buy Box Seller", f"{bb_seller}  ({seller_type})" if bb_seller else "N/A"),
            ("Prime Eligible", "✅ Yes" if is_prime else "❌ No"),
            ("Lowest New (incl. ship)", f"${new_3p:.2f}" if new_3p else "N/A"),
            ("Lowest FBA (incl. ship)", f"${fba_p:.2f}" if fba_p else "N/A"),
            ("Lowest FBM (incl. ship)", f"${fbm_p:.2f}" if fbm_p else "N/A"),
            ("New Offer Count", str(count_new) if count_new is not None else "N/A"),
        ]

        # Last update
        lu = product.get("lastUpdate", 0)
        if lu and lu > 0:
            dt = keepa_time_to_dt(lu)
            detail_items.append(("Last Keepa Update", dt.strftime("%Y-%m-%d %H:%M UTC")))

        for i, (label, value) in enumerate(detail_items):
            row_bg = BG_CARD if i % 2 == 0 else BG_INPUT
            r = tk.Frame(details, bg=row_bg, pady=3, padx=6)
            r.pack(fill="x")
            tk.Label(r, text=label, font=("Helvetica Neue", 11),
                     bg=row_bg, fg=FG_SECONDARY, width=26,
                     anchor="w").pack(side="left")
            tk.Label(r, text=value, font=("Menlo", 11, "bold"),
                     bg=row_bg, fg=FG_PRIMARY, anchor="w").pack(side="left",
                                                                 fill="x")

        # ── Offers Table ──
        offers = product.get("offers", [])
        parsed = self._parse_offers(offers, bb_seller)

        if parsed:
            section_label = tk.Frame(self.results_frame, bg=BG_DARK)
            section_label.pack(fill="x", pady=(6, 4))
            tk.Label(section_label,
                     text=f"🏪  All Seller Offers  ({len(parsed)} active)",
                     font=("Helvetica Neue", 14, "bold"),
                     bg=BG_DARK, fg=FG_PRIMARY).pack(side="left")

            # Treeview
            cols = ("cond", "fba", "prime", "price", "ship", "total", "seller", "note")
            tree_frame = tk.Frame(self.results_frame, bg=BG_CARD)
            tree_frame.pack(fill="x", pady=(0, 10))

            tree = ttk.Treeview(tree_frame, columns=cols, show="headings",
                                height=min(len(parsed), 12),
                                style="Offers.Treeview")

            headings = {
                "cond": ("Condition", 150),
                "fba": ("FBA", 50),
                "prime": ("Prime", 55),
                "price": ("Price", 90),
                "ship": ("Shipping", 80),
                "total": ("Total", 95),
                "seller": ("Seller ID", 170),
                "note": ("Note", 160),
            }
            for col, (text, w) in headings.items():
                tree.heading(col, text=text, anchor="w")
                tree.column(col, width=w, minwidth=40, anchor="w")

            for i, o in enumerate(parsed):
                tag = "lowest" if i == 0 else ("even" if i % 2 == 0 else "odd")
                tree.insert("", "end", values=(
                    o["condition_label"],
                    "✅" if o["is_fba"] else "❌",
                    "✅" if o["is_prime"] else "❌",
                    f"${o['item_price']:.2f}",
                    "FREE" if o["shipping"] == 0 else f"${o['shipping']:.2f}",
                    f"${o['total']:.2f}",
                    o["seller_id"],
                    o["note"],
                ), tags=(tag,))

            tree.tag_configure("lowest", background="#1b3a2d",
                               foreground=FG_GREEN)
            tree.tag_configure("even", background=BG_TABLE_ROW)
            tree.tag_configure("odd", background=BG_TABLE_ALT)

            tree.pack(fill="x", padx=2, pady=2)

            # ── Summary cards ──
            new_offers = [o for o in parsed if o["condition"] == 1]
            used_offers = [o for o in parsed if o["condition"] != 1]

            summary_row = tk.Frame(self.results_frame, bg=BG_DARK)
            summary_row.pack(fill="x", pady=(0, 10))

            if new_offers:
                lo = new_offers[0]
                ff = "FBA" if lo["is_fba"] else "FBM"
                ship_txt = "FREE" if lo["shipping"] == 0 else f"${lo['shipping']:.2f}"
                self._summary_card(
                    summary_row,
                    f"⬇️  Lowest NEW  ({ff})",
                    f"${lo['item_price']:.2f}",
                    f"Shipping: {ship_txt}  •  Total: ${lo['total']:.2f}\n"
                    f"Seller: {lo['seller_id']}",
                    FG_GREEN, side="left")

            if used_offers:
                lo = used_offers[0]
                ff = "FBA" if lo["is_fba"] else "FBM"
                ship_txt = "FREE" if lo["shipping"] == 0 else f"${lo['shipping']:.2f}"
                self._summary_card(
                    summary_row,
                    f"⬇️  Lowest USED  ({lo['condition_label']})",
                    f"${lo['item_price']:.2f}",
                    f"Shipping: {ship_txt}  •  Total: ${lo['total']:.2f}\n"
                    f"Seller: {lo['seller_id']}",
                    FG_CYAN, side="left")

        else:
            tk.Label(self.results_frame,
                     text="ℹ️  No individual seller offers returned",
                     font=("Helvetica Neue", 12), bg=BG_DARK,
                     fg=FG_SECONDARY, pady=10).pack(fill="x")

        self._set_status(f"✅ Done — {asin}", FG_GREEN)

        # Scroll to top
        self.canvas.yview_moveto(0)

    # ── Widgets ──────────────────────────────────────────────────────────────

    def _price_card(self, parent, label, value, color, **pack_kw):
        card = tk.Frame(parent, bg=BG_CARD, padx=16, pady=12)
        card.pack(fill="both", expand=True, padx=(0, 8), **pack_kw)
        tk.Label(card, text=label, font=("Helvetica Neue", 10),
                 bg=BG_CARD, fg=FG_SECONDARY).pack(anchor="w")
        tk.Label(card, text=value, font=("Menlo", 20, "bold"),
                 bg=BG_CARD, fg=color).pack(anchor="w", pady=(4, 0))

    def _summary_card(self, parent, title, price, detail, color, **pack_kw):
        card = tk.Frame(parent, bg=BG_CARD, padx=16, pady=12)
        card.pack(fill="both", expand=True, padx=(0, 8), **pack_kw)
        tk.Label(card, text=title, font=("Helvetica Neue", 12, "bold"),
                 bg=BG_CARD, fg=color).pack(anchor="w")
        tk.Label(card, text=price, font=("Menlo", 18, "bold"),
                 bg=BG_CARD, fg=color).pack(anchor="w", pady=(2, 2))
        tk.Label(card, text=detail, font=("Menlo", 10),
                 bg=BG_CARD, fg=FG_SECONDARY, justify="left").pack(anchor="w")

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _parse_offers(self, offers, bb_seller_id):
        parsed = []
        for offer in offers:
            seller_id = offer.get("sellerId", "Unknown")
            condition = offer.get("condition", -1)
            cond_label = CONDITION_MAP.get(condition, f"Cond {condition}")
            is_fba = offer.get("isFBA", False)
            is_prime = offer.get("isPrime", False)
            is_bb = offer.get("isBuyBoxWinner", False)

            csv = offer.get("offerCSV", [])
            if not csv or len(csv) < 3:
                continue
            raw_price = csv[-2]
            raw_ship = csv[-1]
            item_price = to_dollars(raw_price)
            shipping = to_dollars(raw_ship)
            if item_price is None:
                continue

            total = item_price + (shipping or 0)

            note_parts = []
            if is_bb:
                note_parts.append("🏆 BUY BOX")
            if seller_id == bb_seller_id and not is_bb:
                note_parts.append("🏆 BB WINNER")

            parsed.append({
                "seller_id": seller_id,
                "condition": condition,
                "condition_label": cond_label,
                "is_fba": is_fba,
                "is_prime": is_prime,
                "is_buy_box": is_bb,
                "item_price": item_price,
                "shipping": shipping or 0,
                "total": total,
                "note": "  ".join(note_parts),
            })

        parsed.sort(key=lambda x: x["total"])
        if parsed:
            parsed[0]["note"] = ("⬇️ LOWEST  " + parsed[0]["note"]).strip()
        return parsed


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = KeepaApp()
    app.mainloop()
