#!/usr/bin/env python3
"""Build the Shopify CSV with image links served from this computer through a Cloudflare
quick tunnel, then stay open while Shopify downloads the images during the import.

    brew install cloudflared                       # once
    python import_via_tunnel.py --sku G0507745 --out-dir output/test_G0507745 \\
        --csv-name G0507745_shopify_import.csv --status draft

Steps:
 1. serves the run's upload_to_shopify_files/ folder on http://localhost:8080
    (only the image files in that folder, read-only, no directory listing)
 2. starts `cloudflared tunnel --url http://localhost:8080` and reads its
    https://<random>.trycloudflare.com link, then checks the link really works
 3. runs generate_shipping_boxes_catalog.py with --image-base-url <link> --check-urls,
    so every image link in the CSV is downloaded through the tunnel before you import
 4. tells you to import the CSV in Shopify and counts Shopify's image downloads
 5. once every image has been downloaded (plus a grace period), closes the tunnel;
    Ctrl+C stops it at any time

Every option not listed under "tunnel options" is passed to
generate_shipping_boxes_catalog.py (--sku, --fixture, --out-dir, --workers, ...).
The link changes on every run: import the CSV from this run, while this window is open.
"""

import argparse
import csv
import http.server
import queue
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from generate_shipping_boxes_catalog import run_paths  # noqa: E402
from hathoro.design import load_json  # noqa: E402
from hathoro.net import ssl_context  # noqa: E402
from hathoro.validate import VALIDATOR_AGENT  # noqa: E402

TUNNEL_RE = re.compile(r"https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com")
HEALTH = "/__hathoro_health"


class Downloads:
    """Counts image downloads by anyone except our own validator (i.e. by Shopify)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.counts = {}

    def add(self, name):
        with self.lock:
            self.counts[name] = self.counts.get(name, 0) + 1

    def names(self):
        with self.lock:
            return set(self.counts)


def make_handler(directory, downloads):
    directory = Path(directory)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def _file(self):
            name = self.path.split("?", 1)[0].lstrip("/")
            ok = name and "/" not in name and "\\" not in name and not name.startswith(".")
            return name if ok and (directory / name).is_file() else None

        def do_GET(self):
            if self.path == HEALTH:
                body = b"ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            name = self._file()
            if not name:
                self.send_error(404)
                return
            super().do_GET()
            if self.headers.get("User-Agent", "") != VALIDATOR_AGENT:
                downloads.add(name)

        def do_HEAD(self):
            if not self._file():
                self.send_error(404)
                return
            super().do_HEAD()

        def list_directory(self, path):
            self.send_error(404)
            return None

        def log_message(self, fmt, *args):
            pass

    return Handler


class QuietServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            super().handle_error(request, client_address)    # clients hanging up is normal


def start_tunnel(exe, port, timeout):
    """Start a quick tunnel; returns (process, url). Output is drained in a thread."""
    proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{port}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    lines = queue.Queue()
    log = []

    def drain():
        for line in proc.stdout:
            log.append(line.rstrip())
            lines.put(line)

    threading.Thread(target=drain, daemon=True).start()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            line = lines.get(timeout=1)
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue
        m = TUNNEL_RE.search(line)
        if m:
            return proc, m.group(0), log
    proc.terminate()
    tail = "\n".join(log[-15:])
    raise RuntimeError(f"cloudflared did not print a trycloudflare.com link.\n{tail}")


def wait_reachable(url, timeout):
    ctx = ssl_context()
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        req = urllib.request.Request(url + HEALTH, headers={"User-Agent": VALIDATOR_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                if resp.status == 200 and resp.read() == b"ok":
                    return
        except ssl.SSLCertVerificationError as e:
            raise RuntimeError(
                f"Python cannot verify https certificates ({e}). On a Mac run "
                "'/Applications/Python 3.x/Install Certificates.command' or 'pip install certifi'."
            ) from e
        except Exception as e:  # noqa: BLE001 - DNS not ready yet, 502/530 while connecting
            last = e
        time.sleep(3)
    raise RuntimeError(f"the tunnel link never answered ({last}). Check that this computer "
                       "allows outbound connections to Cloudflare (port 7844).")


def csv_image_names(csv_path):
    with open(csv_path, newline="", encoding="utf-8") as fh:
        return {row["Product image URL"].rsplit("/", 1)[-1].split("?")[0]
                for row in csv.DictReader(fh) if row.get("Product image URL")}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    t = ap.add_argument_group("tunnel options")
    t.add_argument("--port", type=int, default=8080)
    t.add_argument("--cloudflared", default="cloudflared", help="path to the cloudflared binary")
    t.add_argument("--tunnel-url", help="use an already running tunnel/host instead of starting "
                                        "a quick tunnel (it must serve this folder)")
    t.add_argument("--tunnel-timeout", type=int, default=120,
                   help="seconds to wait for the tunnel link to work")
    t.add_argument("--tunnel-attempts", type=int, default=4,
                   help="how often to retry when Cloudflare does not hand out a link")
    t.add_argument("--grace", type=int, default=60,
                   help="keep serving this long after the last image was downloaded")
    t.add_argument("--keep-open", action="store_true", help="only stop with Ctrl+C")
    args, passthrough = ap.parse_known_args()
    if "--image-base-url" in passthrough:
        ap.error("--image-base-url is set by this script")

    pp = argparse.ArgumentParser(add_help=False)
    pp.add_argument("--out-dir")
    pp.add_argument("--csv-name")
    pp.add_argument("--settings", default=str(ROOT / "catalog_settings.json"))
    known, _ = pp.parse_known_args(passthrough)
    paths = run_paths(load_json(known.settings), known.out_dir, known.csv_name)
    upload_dir = paths["upload_dir"]
    upload_dir.mkdir(parents=True, exist_ok=True)

    downloads = Downloads()
    try:
        server = QuietServer(("127.0.0.1", args.port), make_handler(upload_dir, downloads))
    except OSError as e:
        sys.exit(f"Port {args.port} is busy ({e}). Close the other server or use --port 8081.")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Serving {upload_dir} on http://localhost:{args.port}")

    proc = None
    try:
        if args.tunnel_url:
            url = args.tunnel_url.rstrip("/")
        else:
            exe = shutil.which(args.cloudflared)
            if not exe:
                sys.exit("cloudflared not found. Install it with:  brew install cloudflared")
            version = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout
            print(f"Using {exe} ({version.strip() or 'version unknown'})")
            for attempt in range(1, args.tunnel_attempts + 1):
                print(f"Starting Cloudflare tunnel (attempt {attempt}/{args.tunnel_attempts})...")
                try:
                    proc, url, _ = start_tunnel(exe, args.port, args.tunnel_timeout)
                    break
                except RuntimeError as e:
                    if attempt == args.tunnel_attempts:
                        raise RuntimeError(
                            f"{e}\n\nCloudflare never handed out a link. Check that "
                            "https://api.trycloudflare.com is reachable from this Mac "
                            "(VPN, firewall or network filters can block it).") from e
                    wait = 5 * attempt
                    print(f"  no link yet ({str(e).splitlines()[-1][:120]}); "
                          f"retrying in {wait}s")
                    time.sleep(wait)
        print(f"Tunnel link: {url}  (checking it works...)")
        wait_reachable(url, args.tunnel_timeout)
        print("Tunnel is live.\n")

        cmd = [sys.executable, str(ROOT / "generate_shipping_boxes_catalog.py"), *passthrough,
               "--image-base-url", url, "--check-urls"]
        rc = subprocess.call(cmd, cwd=ROOT)
        if rc != 0:
            print("\nThe pipeline or its validation failed (see above), so do not import this CSV.")
            return rc

        expected = csv_image_names(paths["csv"])
        bar = "=" * 78
        print(f"\n{bar}\nNOW IMPORT IN SHOPIFY (keep this window open):\n"
              f"  Products > Import > {paths['csv']}\n"
              "  (tick 'Overwrite products with matching handles' if the products already exist)\n"
              f"Waiting for Shopify to download {len(expected)} images. Ctrl+C to stop.\n{bar}")
        done_at, shown = None, -1
        while True:
            got = len(expected & downloads.names())
            if got != shown:
                print(f"  Shopify has downloaded {got}/{len(expected)} images")
                shown = got
            if expected and got == len(expected):
                done_at = done_at or time.time()
                if not args.keep_open and time.time() - done_at > args.grace:
                    print("All images downloaded by Shopify. Closing the tunnel.")
                    return 0
            if proc and proc.poll() is not None:
                print("cloudflared stopped unexpectedly; images not yet downloaded will be missing.")
                return 1
            time.sleep(2)
    except RuntimeError as e:
        print(f"\nTunnel problem: {e}")
        return 1
    except KeyboardInterrupt:
        got = len(downloads.names())
        print(f"\nStopped. Shopify downloaded {got} image file(s) during this run.")
        return 0
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
        server.shutdown()


if __name__ == "__main__":
    sys.exit(main())
