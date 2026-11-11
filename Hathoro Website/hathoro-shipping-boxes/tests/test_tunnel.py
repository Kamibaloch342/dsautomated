"""Tests for import_via_tunnel.py. Run with `python tests/test_tunnel.py` (or pytest).

A real Cloudflare quick tunnel needs internet access to Cloudflare, so the end-to-end test
stands in for it with a local https proxy and plays Shopify by downloading the images.
"""

import csv
import http.server
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import import_via_tunnel as tun  # noqa: E402
from hathoro.validate import VALIDATOR_AGENT  # noqa: E402

PIPELINE_ARGS = ["--fixture", "test_data/G0507745.json", "--csv-name", "t.csv", "--status", "draft"]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def no_proxy_env(**extra):
    env = {k: v for k, v in os.environ.items() if k.lower() not in ("https_proxy", "http_proxy")}
    env.update(NO_PROXY="localhost,127.0.0.1", no_proxy="localhost,127.0.0.1", **extra)
    return env


def local_get(url, agent="Mozilla/5.0"):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"User-Agent": agent})
    try:
        with opener.open(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def test_server_serves_only_image_files_and_counts_shopify():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "upload"
        folder.mkdir()
        (folder / "1_a.jpg").write_bytes(b"jpegdata")
        (Path(tmp) / "secret.txt").write_text("no")
        downloads = tun.Downloads()
        port = free_port()
        srv = tun.QuietServer(("127.0.0.1", port), tun.make_handler(folder, downloads))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{port}"
        try:
            assert local_get(base + tun.HEALTH) == (200, b"ok")
            assert local_get(base + "/1_a.jpg", VALIDATOR_AGENT) == (200, b"jpegdata")
            assert downloads.names() == set()                  # our own checks don't count
            assert local_get(base + "/1_a.jpg?v=2", "Shopify") == (200, b"jpegdata")
            for _ in range(50):                  # counted right after the response is sent
                if downloads.names():
                    break
                time.sleep(0.05)
            assert downloads.names() == {"1_a.jpg"}
            for bad in ("/", "/../secret.txt", "/%2e%2e/secret.txt", "/missing.jpg"):
                assert local_get(base + bad)[0] == 404, bad
        finally:
            srv.shutdown()


def fake_cloudflared(tmp, lines, sleep=30):
    path = Path(tmp) / "cloudflared"
    body = "".join(f"print({line!r}, flush=True)\n" for line in lines)
    path.write_text(f"#!{sys.executable}\nimport time\n{body}time.sleep({sleep})\n")
    path.chmod(0o755)
    return str(path)


def test_tunnel_link_is_read_from_cloudflared_output():
    with tempfile.TemporaryDirectory() as tmp:
        exe = fake_cloudflared(tmp, [
            'ERR failed to reach "https://api.trycloudflare.com/tunnel", retrying',
            "INF |  Your quick Tunnel has been created! Visit it at:  |",
            "INF |  https://brave-sun-quick-test.trycloudflare.com    |",
        ])
        proc, url, _ = tun.start_tunnel(exe, 8080, timeout=10)
        proc.terminate()
        assert url == "https://brave-sun-quick-test.trycloudflare.com"


def test_dead_tunnel_stops_before_writing_a_csv():
    with tempfile.TemporaryDirectory() as tmp:
        exe = fake_cloudflared(tmp, ["INF |  https://hathoro-no-such-tunnel-x9.trycloudflare.com  |"])
        out = Path(tmp) / "out"
        res = subprocess.run([sys.executable, str(ROOT / "import_via_tunnel.py"),
                              "--cloudflared", exe, "--port", str(free_port()),
                              "--tunnel-timeout", "8", "--out-dir", str(out), *PIPELINE_ARGS],
                             cwd=ROOT, capture_output=True, text=True, timeout=120)
        assert res.returncode == 1, res.stdout + res.stderr
        assert "Tunnel problem" in res.stdout
        assert not (out / "t.csv").exists()


def test_quick_tunnel_request_is_retried():
    with tempfile.TemporaryDirectory() as tmp:
        counter = Path(tmp) / "calls"
        exe = Path(tmp) / "cloudflared"
        exe.write_text(f"""#!{sys.executable}
import sys, time
from pathlib import Path
if sys.argv[1:] == ["--version"]:
    print("cloudflared version test"); sys.exit(0)
c = Path({str(counter)!r}); n = int(c.read_text()) + 1 if c.exists() else 1; c.write_text(str(n))
if n == 1:
    print('failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": '
          'context deadline exceeded', flush=True); sys.exit(1)
print("INF |  https://second-try-works.trycloudflare.com  |", flush=True); time.sleep(30)
""")
        exe.chmod(0o755)
        res = subprocess.run([sys.executable, str(ROOT / "import_via_tunnel.py"),
                              "--cloudflared", str(exe), "--port", str(free_port()),
                              "--tunnel-timeout", "6", "--out-dir", str(Path(tmp) / "out"),
                              *PIPELINE_ARGS], cwd=ROOT, capture_output=True, text=True,
                             timeout=120)
        assert "attempt 2/" in res.stdout, res.stdout
        assert "Tunnel link: https://second-try-works.trycloudflare.com" in res.stdout
        assert counter.read_text() == "2"


def https_proxy_to(port, cert, key):
    """Local stand-in for Cloudflare's edge: https on a free port -> http://127.0.0.1:port."""
    class Relay(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            status, body = local_get(f"http://127.0.0.1:{port}{self.path}",
                                     self.headers.get("User-Agent", ""))
            self.send_response(status)
            self.send_header("Content-Type", "image/jpeg" if self.path.endswith(".jpg")
                             else "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    edge_port = free_port()
    srv = tun.QuietServer(("127.0.0.1", edge_port), Relay)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, edge_port


def test_end_to_end_csv_points_at_tunnel_and_waits_for_shopify():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        cert, key = tmp / "cert.pem", tmp / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
                        "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
        local_port = free_port()
        edge, edge_port = https_proxy_to(local_port, cert, key)
        out = tmp / "out"
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "import_via_tunnel.py"), "--port", str(local_port),
             "--tunnel-url", f"https://localhost:{edge_port}", "--grace", "1",
             "--out-dir", str(out), *PIPELINE_ARGS],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env=no_proxy_env(SSL_CERT_FILE=str(cert)))
        log = []
        shopify = ssl.create_default_context(cafile=str(cert))
        try:
            for line in proc.stdout:
                log.append(line)
                if "NOW IMPORT IN SHOPIFY" in line:       # play Shopify: fetch every image
                    with open(out / "t.csv", newline="") as fh:
                        urls = [r["Product image URL"] for r in csv.DictReader(fh)]
                    for u in urls:
                        req = urllib.request.Request(u, headers={"User-Agent": "Shopify"})
                        opener = urllib.request.build_opener(
                            urllib.request.ProxyHandler({}),
                            urllib.request.HTTPSHandler(context=shopify))
                        with opener.open(req, timeout=10) as r:
                            assert r.status == 200 and r.read()[:2] == b"\xff\xd8"  # JPEG
            rc = proc.wait(timeout=120)
        finally:
            if proc.poll() is None:
                proc.kill()
            edge.shutdown()
        text = "".join(log)
        assert rc == 0, text
        assert "PASSED" in text and "reachable" in text           # validator fetched every URL
        assert "Shopify has downloaded 5/5 images" in text
        assert "All images downloaded by Shopify" in text
        rows = list(csv.DictReader(open(out / "t.csv", newline="")))
        assert all(r["Product image URL"].startswith(f"https://localhost:{edge_port}/")
                   for r in rows) and len(rows) == 5


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
