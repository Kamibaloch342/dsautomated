#!/usr/bin/env python3
"""
utils/r2_store.py — Cloudflare R2 (S3-compatible) object store used as the shared
"cloud bus" between every machine that runs ZUHRA and the dashboard.

Why R2: the team has a 10 GB bucket (`dsautomated`). Everything that must be
visible from another computer goes through it — the slim dashboard DB, machine
heartbeats, launcher control commands, live console logs and CRM events. Raw
logs stay LOCAL only (data/raw_logs.txt, data/console_log.txt) and are never
uploaded.

Key layout (all under the bucket root):

  dashboard.db                  slim DB snapshot, every 5 min  (legacy key kept
                                so the Render deployment keeps working)
  db/master_backup.db.gz        gzip of the master DB (hourly, once slimmed)
  db/manifest.json              {size, sha1, uploaded_at, host, git_sha}

  state/machines/<host>.json    heartbeat every 15 s: launcher up?, placers,
                                closing, memory, git sha, last placement
  state/active.json             placement LEASE — which machine may run placers.
                                Another machine refuses to start placers while a
                                fresh lease is held elsewhere (duplicate-order
                                protection across computers).

  control/queue/<ts>_<id>.json  commands written by the dashboard
                                (stop_launcher, stop_placer, start_placer,
                                run_closing, run_priority_check ...)
  control/done/<id>.json        acknowledgement + result written by the agent

  logs/live/<host>.json         last ~400 launcher console lines, every 10 s
  crm/events/<ts>_<id>.json     CRM writes made on the cloud dashboard,
                                replayed into the master DB by the agent
  crm/applied/<id>.json         replay receipts

Credentials (never committed): looked up in this order
  1. env  R2_ACCESS_KEY / R2_SECRET_KEY / R2_BUCKET / R2_ENDPOINT
  2. data/All_Tokens/r2.json  {"access_key","secret_key","bucket","endpoint"}
  3. macOS LaunchAgent plists (com.zuhra.agent / com.zuhra.cloudsync)
     EnvironmentVariables — so the agent and the launcher share one source.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any, Iterable

_HERE = os.path.dirname(os.path.abspath(__file__))
AUTOMATIONS_DIR = os.path.dirname(_HERE)
TOKEN_FILE = os.path.join(AUTOMATIONS_DIR, "data", "All_Tokens", "r2.json")
_PLISTS = ("com.zuhra.agent", "com.zuhra.cloudsync")

# ── key layout ─────────────────────────────────────────────────────────────
KEY_SLIM_DB = "dashboard.db"
KEY_MASTER_BACKUP = "db/master_backup.db.gz"
KEY_DB_MANIFEST = "db/manifest.json"
PREFIX_MACHINES = "state/machines/"
KEY_ACTIVE = "state/active.json"
PREFIX_CMD_QUEUE = "control/queue/"
PREFIX_CMD_DONE = "control/done/"
PREFIX_LIVE_LOGS = "logs/live/"
PREFIX_CRM_EVENTS = "crm/events/"
PREFIX_CRM_APPLIED = "crm/applied/"


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_MACHINE_ID_FILE = os.path.join(AUTOMATIONS_DIR, "data", "machine_id.txt")
_machine_id_cache: str | None = None


def _clean_name(name: str) -> str:
    name = (name or "").split(".")[0]
    return re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-").lower()


def machine_id() -> str:
    """Stable, filename-safe id for this computer. Resolution order:
    ZUHRA_MACHINE env → data/machine_id.txt → macOS ComputerName → hostname
    (unless it is an IP-style name like '192.168.1.5', which some networks hand
    out) → a generated 'mac-xxxxxx'. Whatever is chosen is written to
    data/machine_id.txt so the name never changes between runs."""
    global _machine_id_cache
    if _machine_id_cache:
        return _machine_id_cache
    env = _clean_name(os.environ.get("ZUHRA_MACHINE", ""))
    if env:
        _machine_id_cache = env
        return env
    try:
        with open(_MACHINE_ID_FILE, "r", encoding="utf-8") as f:
            saved = _clean_name(f.read().strip())
        if saved:
            _machine_id_cache = saved
            return saved
    except Exception:
        pass
    name = ""
    try:
        import platform
        import subprocess
        if platform.system() == "Darwin":
            r = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=3)
            name = _clean_name(r.stdout) if r.returncode == 0 else ""
    except Exception:
        name = ""
    host = socket.gethostname() or ""
    if not name and host and not re.match(r"^\d{1,3}(\.\d{1,3}){1,3}$", host):
        name = _clean_name(host)
    if not name or name.isdigit():
        name = "mac-" + hashlib.sha1((host + os.path.expanduser("~")).encode()).hexdigest()[:6]
    try:
        os.makedirs(os.path.dirname(_MACHINE_ID_FILE), exist_ok=True)
        with open(_MACHINE_ID_FILE, "w", encoding="utf-8") as f:
            f.write(name + "\n")
    except Exception:
        pass
    _machine_id_cache = name
    return name


def _load_plist_env() -> dict:
    """macOS only: reuse the EnvironmentVariables of the ZUHRA LaunchAgents so the
    R2 keys live in exactly one place. Returns {} on any other OS / failure."""
    try:
        import plistlib
    except Exception:
        return {}
    for label in _PLISTS:
        p = os.path.expanduser(f"~/Library/LaunchAgents/{label}.plist")
        if not os.path.exists(p):
            continue
        try:
            with open(p, "rb") as f:
                env = plistlib.load(f).get("EnvironmentVariables", {}) or {}
            if env.get("R2_ACCESS_KEY") and env.get("R2_SECRET_KEY"):
                return env
        except Exception:
            continue
    return {}


def load_credentials() -> dict | None:
    """Return {"access_key","secret_key","bucket","endpoint"} or None."""
    env = os.environ
    if env.get("R2_ACCESS_KEY") and env.get("R2_SECRET_KEY"):
        return {"access_key": env["R2_ACCESS_KEY"], "secret_key": env["R2_SECRET_KEY"],
                "bucket": env.get("R2_BUCKET", "dsautomated"), "endpoint": env.get("R2_ENDPOINT", "")}
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            if d.get("access_key") and d.get("secret_key"):
                return {"access_key": d["access_key"], "secret_key": d["secret_key"],
                        "bucket": d.get("bucket", "dsautomated"), "endpoint": d.get("endpoint", "")}
        except Exception:
            pass
    penv = _load_plist_env()
    if penv:
        return {"access_key": penv["R2_ACCESS_KEY"], "secret_key": penv["R2_SECRET_KEY"],
                "bucket": penv.get("R2_BUCKET", "dsautomated"), "endpoint": penv.get("R2_ENDPOINT", "")}
    return None


class R2Store:
    """Thin, thread-safe wrapper over boto3 for the key layout above.

    Every method swallows nothing: callers decide how to handle failures, but
    `available()` lets code degrade gracefully when there are no credentials
    (e.g. a colleague's machine that only runs the launcher offline).
    """

    def __init__(self, creds: dict | None = None):
        self._creds = creds or load_credentials()
        self._client = None
        self._xfer = None
        self._lock = threading.Lock()

    # ── plumbing ──────────────────────────────────────────────────────────
    def available(self) -> bool:
        return bool(self._creds and self._creds.get("endpoint"))

    @property
    def bucket(self) -> str:
        return (self._creds or {}).get("bucket", "dsautomated")

    def _c(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    if not self.available():
                        raise RuntimeError("R2 credentials not configured (env / data/All_Tokens/r2.json / LaunchAgent)")
                    import boto3
                    from botocore.config import Config
                    self._client = boto3.client(
                        "s3",
                        endpoint_url=self._creds["endpoint"],
                        aws_access_key_id=self._creds["access_key"],
                        aws_secret_access_key=self._creds["secret_key"],
                        region_name="auto",
                        config=Config(retries={"max_attempts": 4, "mode": "standard"},
                                      connect_timeout=15, read_timeout=120),
                    )
        return self._client

    def _transfer_cfg(self):
        """Transfer settings for get_file/put_file.

        boto3's defaults fetch a large object as ten concurrent 8 MB parts, so a 32 MB download
        can hold ~80 MB of buffers — more than the BytesIO this replaced. Two parts at a time is
        plenty for a file this size and keeps the ceiling near 16 MB.
        """
        if self._xfer is None:
            from boto3.s3.transfer import TransferConfig
            self._xfer = TransferConfig(multipart_chunksize=8 * 1024 * 1024,
                                        multipart_threshold=8 * 1024 * 1024,
                                        max_concurrency=2, use_threads=True)
        return self._xfer

    # ── generic object ops ────────────────────────────────────────────────
    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream",
                  metadata: dict | None = None) -> None:
        kw = {"Bucket": self.bucket, "Key": key, "Body": data, "ContentType": content_type}
        if metadata:
            kw["Metadata"] = {k: str(v) for k, v in metadata.items()}
        self._c().put_object(**kw)

    def get_bytes(self, key: str) -> bytes | None:
        try:
            r = self._c().get_object(Bucket=self.bucket, Key=key)
            return r["Body"].read()
        except self._c().exceptions.NoSuchKey:
            return None
        except Exception as e:  # botocore ClientError 404
            if "NoSuchKey" in str(e) or "Not Found" in str(e) or "404" in str(e):
                return None
            raise

    def put_json(self, key: str, obj: Any) -> None:
        self.put_bytes(key, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                       "application/json")

    def get_json(self, key: str, default: Any = None) -> Any:
        b = self.get_bytes(key)
        if b is None:
            return default
        try:
            return json.loads(b.decode("utf-8"))
        except Exception:
            return default

    def put_file(self, key: str, path: str, content_type: str = "application/octet-stream",
                 gzip_it: bool = False, metadata: dict | None = None) -> int:
        """Upload a local file (multipart for big ones). Returns bytes uploaded."""
        extra = {"ContentType": content_type}
        if metadata:
            extra["Metadata"] = {k: str(v) for k, v in metadata.items()}
        if gzip_it:
            buf = io.BytesIO()
            with open(path, "rb") as src, gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    gz.write(chunk)
            buf.seek(0)
            self._c().upload_fileobj(buf, self.bucket, key, ExtraArgs=extra)
            return buf.getbuffer().nbytes
        self._c().upload_file(path, self.bucket, key, ExtraArgs=extra)
        return os.path.getsize(path)

    def get_file(self, key: str, path: str, gunzip: bool = False) -> bool:
        """Download to `path` atomically (tmp + rename). False if the key is missing."""
        tmp = path + ".part"
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        gz_tmp = path + ".gz.part"
        try:
            if gunzip:
                # Download to a file, not to an io.BytesIO. The dashboard DB is ~147 MB and
                # compresses to ~32 MB, so buffering it in memory meant a 32 MB spike every
                # time the cloud refresher saw a new fingerprint — on a 512 MB Render instance
                # that is a real share of the budget, taken every couple of minutes.
                self._c().download_file(self.bucket, key, gz_tmp, Config=self._transfer_cfg())
                with gzip.open(gz_tmp, "rb") as gz, open(tmp, "wb") as out:
                    for chunk in iter(lambda: gz.read(1 << 20), b""):
                        out.write(chunk)
                os.remove(gz_tmp)
            else:
                self._c().download_file(self.bucket, key, tmp, Config=self._transfer_cfg())
        except Exception as e:
            for junk in (tmp, gz_tmp):
                if os.path.exists(junk):
                    try:
                        os.remove(junk)
                    except OSError:
                        pass
            if "NoSuchKey" in str(e) or "Not Found" in str(e) or "404" in str(e):
                return False
            raise
        os.replace(tmp, path)
        return True

    def head(self, key: str) -> dict | None:
        try:
            r = self._c().head_object(Bucket=self.bucket, Key=key)
            return {"size": r.get("ContentLength"), "modified": r.get("LastModified"),
                    "metadata": r.get("Metadata", {})}
        except Exception as e:
            if "404" in str(e) or "Not Found" in str(e):
                return None
            raise

    def list(self, prefix: str, max_keys: int = 1000) -> list[dict]:
        """[{key,size,modified}] sorted by key (our keys start with a timestamp)."""
        out: list[dict] = []
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": min(max_keys, 1000)}
            if token:
                kw["ContinuationToken"] = token
            r = self._c().list_objects_v2(**kw)
            for o in r.get("Contents", []) or []:
                out.append({"key": o["Key"], "size": o["Size"], "modified": o["LastModified"]})
            if not r.get("IsTruncated") or len(out) >= max_keys:
                break
            token = r.get("NextContinuationToken")
        out.sort(key=lambda d: d["key"])
        return out

    def delete(self, key: str) -> None:
        self._c().delete_object(Bucket=self.bucket, Key=key)

    def delete_many(self, keys: Iterable[str]) -> int:
        keys = [k for k in keys if k]
        n = 0
        for i in range(0, len(keys), 1000):
            batch = keys[i:i + 1000]
            self._c().delete_objects(Bucket=self.bucket,
                                     Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True})
            n += len(batch)
        return n

    def copy(self, src_key: str, dst_key: str) -> None:
        """Server-side copy (no re-upload) — used to mirror the slim DB to a second key."""
        self._c().copy_object(Bucket=self.bucket, Key=dst_key,
                              CopySource={"Bucket": self.bucket, "Key": src_key})

    # ── bus helpers ───────────────────────────────────────────────────────
    @staticmethod
    def new_id() -> str:
        return hashlib.sha1(f"{time.time_ns()}-{os.getpid()}-{machine_id()}".encode()).hexdigest()[:12]

    def enqueue_command(self, action: str, target: str | None = None, args: dict | None = None,
                        author: str = "dashboard") -> dict:
        """Dashboard → agent. `target` is a machine id or None (= active machine)."""
        cid = self.new_id()
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        cmd = {"id": cid, "action": action, "target": target, "args": args or {},
               "author": author, "created_at": utcnow()}
        self.put_json(f"{PREFIX_CMD_QUEUE}{ts}_{cid}.json", cmd)
        return cmd

    def pending_commands(self) -> list[dict]:
        out = []
        for o in self.list(PREFIX_CMD_QUEUE):
            d = self.get_json(o["key"])
            if isinstance(d, dict):
                d["_key"] = o["key"]
                out.append(d)
        return out

    def ack_command(self, cmd: dict, ok: bool, result: str = "", host: str | None = None) -> None:
        self.put_json(f"{PREFIX_CMD_DONE}{cmd['id']}.json",
                      {"id": cmd["id"], "action": cmd.get("action"), "ok": ok, "result": result,
                       "host": host or machine_id(), "done_at": utcnow(), "created_at": cmd.get("created_at")})
        if cmd.get("_key"):
            try:
                self.delete(cmd["_key"])
            except Exception:
                pass

    def command_results(self, limit: int = 50) -> list[dict]:
        rows = self.list(PREFIX_CMD_DONE)
        rows = rows[-limit:]
        out = []
        for o in rows:
            d = self.get_json(o["key"])
            if isinstance(d, dict):
                out.append(d)
        out.sort(key=lambda d: d.get("done_at", ""), reverse=True)
        return out

    def heartbeat(self, payload: dict) -> None:
        payload = dict(payload)
        payload.setdefault("host", machine_id())
        payload["at"] = utcnow()
        self.put_json(f"{PREFIX_MACHINES}{payload['host']}.json", payload)

    def machines(self) -> list[dict]:
        out = []
        for o in self.list(PREFIX_MACHINES):
            d = self.get_json(o["key"])
            if isinstance(d, dict):
                try:
                    age = (datetime.now(timezone.utc) - datetime.strptime(d.get("at", ""), "%Y-%m-%dT%H:%M:%SZ")
                           .replace(tzinfo=timezone.utc)).total_seconds()
                except Exception:
                    age = None
                d["age_s"] = None if age is None else int(age)
                d["online"] = age is not None and age < 90
                out.append(d)
        out.sort(key=lambda d: (not d.get("online"), d.get("host", "")))
        return out

    def push_live_log(self, lines: list[str], host: str | None = None, extra: dict | None = None) -> None:
        self.put_json(f"{PREFIX_LIVE_LOGS}{host or machine_id()}.json",
                      {"host": host or machine_id(), "at": utcnow(), "lines": lines[-400:], **(extra or {})})

    def live_log(self, host: str) -> dict | None:
        return self.get_json(f"{PREFIX_LIVE_LOGS}{host}.json")

    def push_crm_event(self, event: dict) -> dict:
        """Cloud dashboard → master DB. event = {table, op, key, data, author}."""
        eid = self.new_id()
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        ev = {"id": eid, "created_at": utcnow(), **event}
        self.put_json(f"{PREFIX_CRM_EVENTS}{ts}_{eid}.json", ev)
        return ev

    def pending_crm_events(self) -> list[dict]:
        out = []
        for o in self.list(PREFIX_CRM_EVENTS):
            d = self.get_json(o["key"])
            if isinstance(d, dict):
                d["_key"] = o["key"]
                out.append(d)
        return out

    def mark_crm_applied(self, ev: dict, ok: bool, note: str = "") -> None:
        self.put_json(f"{PREFIX_CRM_APPLIED}{ev['id']}.json",
                      {"id": ev["id"], "ok": ok, "note": note, "applied_at": utcnow(), "host": machine_id()})
        if ev.get("_key"):
            try:
                self.delete(ev["_key"])
            except Exception:
                pass

    # ── placement lease (cross-machine duplicate protection) ──────────────
    def read_lease(self) -> dict | None:
        return self.get_json(KEY_ACTIVE)

    def lease_holder(self, fresh_s: int = 120) -> str | None:
        """Machine id currently allowed to run placers, or None if no fresh lease."""
        d = self.read_lease()
        if not isinstance(d, dict):
            return None
        try:
            at = datetime.strptime(d.get("at", ""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except Exception:
            return None
        if (datetime.now(timezone.utc) - at).total_seconds() > fresh_s:
            return None
        return d.get("host")

    def renew_lease(self, host: str | None = None, placers: list[str] | None = None) -> None:
        self.put_json(KEY_ACTIVE, {"host": host or machine_id(), "at": utcnow(), "placers": placers or []})

    def release_lease(self, host: str | None = None) -> None:
        d = self.read_lease()
        if isinstance(d, dict) and d.get("host") == (host or machine_id()):
            try:
                self.delete(KEY_ACTIVE)
            except Exception:
                pass

    # ── housekeeping ──────────────────────────────────────────────────────
    def prune(self, prefix: str, keep: int) -> int:
        rows = self.list(prefix)
        if len(rows) <= keep:
            return 0
        return self.delete_many(r["key"] for r in rows[:-keep])

    def usage(self) -> dict:
        total = 0
        per: dict[str, int] = {}
        for o in self.list("", max_keys=100000):
            total += o["size"]
            top = o["key"].split("/")[0] if "/" in o["key"] else "(root)"
            per[top] = per.get(top, 0) + o["size"]
        return {"total_mb": round(total / 1048576, 1),
                "by_prefix_mb": {k: round(v / 1048576, 1) for k, v in sorted(per.items())}}


_singleton: R2Store | None = None


def store() -> R2Store:
    """Process-wide shared instance."""
    global _singleton
    if _singleton is None:
        _singleton = R2Store()
    return _singleton


if __name__ == "__main__":  # quick manual check:  python utils/r2_store.py
    s = store()
    print("R2 configured:", s.available(), "| bucket:", s.bucket, "| machine:", machine_id())
    if s.available():
        print(json.dumps(s.usage(), indent=1))
        print("machines:", [m.get("host") for m in s.machines()])
        print("lease holder:", s.lease_holder())
