"""Specification extraction from combined_reports.db long_description fields.

Zoro/Grainger descriptions come in two shapes:

1. Comma-separated attribute strings —
   "Character Height 5/32 in, Font Helvetica, Number of Bands 4, ..."
   Split each segment into (attribute name, value) using a lexicon of
   attribute names learned from the whole catalog at index-build time
   (see build_search_index.py). Segments that don't start with a known
   name are treated as continuations of the previous value, which keeps
   multi-value attributes intact ("Formula Aloe, Calendula").

2. HTML marketing copy — "<p>…</p><ul><li>…</li></ul>"
   Rendered as sanitized Product Details HTML; bullet points are also
   extracted so the product page can show a "Product Details" list.

Both shapes are complemented with baseline facts derived from the
structured catalog columns (brand, MPN, UPC, country, dimensions…), so
every product gets a real specifications table.
"""

import re
import html as html_mod

# ── lexicon (loaded once from catalog_index.db by catalog.py) ──

_LEXICON = set()
_MAX_NAME_WORDS = 5


def load_lexicon(names):
    global _LEXICON, _MAX_NAME_WORDS
    _LEXICON = set(names)
    _MAX_NAME_WORDS = max((len(n.split(" ")) for n in _LEXICON), default=5)


# ── HTML handling ──

_ALLOWED_TAGS = {"p", "ul", "ol", "li", "b", "strong", "i", "em", "br"}
_tag_re = re.compile(r"</?([a-zA-Z0-9]+)[^>]*>")
_li_re = re.compile(r"<li[^>]*>(.*?)</li>", re.S | re.I)
_strip_tags_re = re.compile(r"<[^>]+>")


def _sanitize_html(raw):
    """Keep a small whitelist of formatting tags, drop everything else
    (attributes included) so the frontend can safely render it."""

    def repl(m):
        tag = m.group(1).lower()
        if tag in _ALLOWED_TAGS:
            closing = "/" if m.group(0).startswith("</") else ""
            return f"<{closing}{tag}>"
        return " "

    out = _tag_re.sub(repl, raw)
    out = re.sub(r"<(script|style)\b.*?</\1>", " ", out, flags=re.S | re.I)
    return re.sub(r"\s{2,}", " ", out).strip()


def _html_bullets(raw):
    return [
        re.sub(r"\s{2,}", " ", _strip_tags_re.sub("", html_mod.unescape(b))).strip()
        for b in _li_re.findall(raw)
    ]


# ── comma-format parsing ──

def _split_segment(segment):
    """Return (name, value) using the longest known attribute-name prefix,
    or (None, segment) when no known name starts the segment."""
    words = segment.split(" ")
    for n in range(min(_MAX_NAME_WORDS, len(words) - 1), 0, -1):
        prefix = " ".join(words[:n])
        if prefix in _LEXICON:
            return prefix, " ".join(words[n:])
    return None, segment


def _parse_comma_specs(text):
    specs = []          # list of [name, value] — keeps first-seen order
    lead = []           # leading unnamed segments (usually the item name)
    for seg in text.split(", "):
        seg = seg.strip()
        if not seg:
            continue
        name, value = _split_segment(seg)
        if name:
            specs.append([name, value])
        elif specs:
            specs[-1][1] += ", " + seg
        else:
            lead.append(seg)
    return lead, specs


# ── baseline facts from structured columns ──

def _fmt_dim(v):
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else str(f)
    except (TypeError, ValueError):
        return None


def baseline_specs(row):
    """row is a dict of combined_reports.db columns."""
    out = {}
    if row.get("zoro_brand_name"):
        out["Brand"] = row["zoro_brand_name"]
    if row.get("manufacturer_part_number"):
        out["Mfr. Model Number"] = row["manufacturer_part_number"]
    if row.get("upc_code"):
        out["UPC"] = row["upc_code"]
    if row.get("country_of_origin"):
        out["Country of Origin"] = row["country_of_origin"]
    if row.get("uom"):
        out["Unit of Measure"] = row["uom"]
    minq = row.get("min_order_qty")
    if minq and str(minq) not in ("1", "1.0"):
        out["Minimum Order Quantity"] = str(minq)
    w = _fmt_dim(row.get("ship_pack_weight"))
    if w:
        out["Shipping Weight"] = f"{w} lb"
    dims = [_fmt_dim(row.get(k)) for k in
            ("ship_pack_length", "ship_pack_width", "ship_pack_height")]
    if all(dims):
        out["Package Dimensions"] = f'{dims[0]} x {dims[1]} x {dims[2]} in'
    return out


# ── public entry point ──

def extract(row):
    """Extract display data from a combined_reports.db row (as dict).

    Returns {
      specifications: {name: value},    # flat dict, "Item" first-class key
      details_html:   str | None,       # sanitized HTML for Product Details
      bullets:        [str],            # plain-text detail bullets
    }
    """
    ldesc = (row.get("long_description") or "").strip()
    specs = {}
    details_html = None
    bullets = []

    if ldesc.startswith("<"):
        details_html = _sanitize_html(ldesc)
        bullets = _html_bullets(ldesc)
    elif ldesc:
        lead, pairs = _parse_comma_specs(ldesc)
        if len(pairs) >= 3:
            if lead:
                specs["Item"] = ", ".join(lead)
            for name, value in pairs:
                specs.setdefault(name, value)
        else:
            # too unstructured to be a spec sheet — show as prose
            details_html = f"<p>{html_mod.escape(ldesc)}</p>"

    if not specs.get("Item") and row.get("short_description"):
        first = row["short_description"].split(",")[0].strip()
        if first:
            specs["Item"] = first

    for name, value in baseline_specs(row).items():
        specs.setdefault(name, value)

    return {
        "specifications": specs,
        "details_html": details_html,
        "bullets": bullets,
    }
