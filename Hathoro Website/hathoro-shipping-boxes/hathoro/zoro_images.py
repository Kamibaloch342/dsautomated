"""Find or download each listing's original supplier photo (used for image 1)."""

import random
import time
import urllib.request
from io import BytesIO
from pathlib import Path

from PIL import Image

from .design import ROOT
from .net import ssl_context

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36",
]
EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _resolve(path):
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def image_url(name, template):
    name = name.strip()
    if name.startswith(("http://", "https://")):
        return name
    if name.startswith("//"):
        return "https:" + name
    return template.format(name=name.lstrip("/"))


def download(url, referer, attempts=4):
    last = None
    for i in range(attempts):
        req = urllib.request.Request(url, headers={
            "User-Agent": random.choice(USER_AGENTS),
            "Referer": referer,
            "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        try:
            with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as resp:
                data = resp.read()
            img = Image.open(BytesIO(data))
            img.load()
            return data, (img.format or "JPEG").lower()
        except Exception as e:  # noqa: BLE001 - network/HTTP/decoding errors all retry
            last = e
            time.sleep(2 ** (i + 1))
    raise RuntimeError(f"{url}: {last}")


def product_photo(li, settings):
    """Path of the supplier photo for a listing, downloading it if needed. None if unavailable.

    Order: drop-in file <folder>/<zoro_no>.<ext>, the row's own image_path, then a download
    of zoro_image_name (cached into the drop-in folder for next time).
    """
    cfg = settings["zoro_images"]
    folder = _resolve(cfg["folder"])
    for ext in EXTS:
        p = folder / f"{li.zoro_no}{ext}"
        if p.exists():
            return p
    if li.image_path:
        p = _resolve(li.image_path)
        if p.exists():
            return p
    if not li.zoro_image_name or not cfg.get("download", True):
        return None
    url = image_url(li.zoro_image_name, cfg["url_template"])
    data, fmt = download(url, cfg.get("referer", "https://www.zoro.com/"))
    folder.mkdir(parents=True, exist_ok=True)
    ext = {"jpeg": ".jpg", "png": ".png", "webp": ".webp"}.get(fmt, ".jpg")
    path = folder / f"{li.zoro_no}{ext}"
    path.write_bytes(data)
    time.sleep(cfg.get("delay_seconds", 0.5))
    return path
