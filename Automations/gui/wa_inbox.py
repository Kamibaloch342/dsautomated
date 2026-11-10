"""
wa_inbox.py — the launcher's 🔔: who wrote in the HOANK / Hathoro Orders Alerts WhatsApp groups.

The gateway records the messages (GET /inbox). This asks for them off the Tk thread and, for anything
new, opens one window, plays a sound and posts a macOS notification. Closing the window marks them
read. The unread count lives in the gateway, so it survives a launcher restart.
"""
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime

import requests

from utils.whatsapp import ensure_gateway_running, get_whatsapp_config, is_whatsapp_enabled

POLL_MS = 3000
WAKE_GATEWAY_EVERY_S = 60
SOUND = "/System/Library/Sounds/Glass.aiff"


def _gateway() -> str:
    return get_whatsapp_config().get("server_url", "http://127.0.0.1:3005").rstrip("/")


def who_wrote(m: dict) -> str:
    phone = f"+{m['phone']}" if m.get("phone") else ""
    return m.get("from") or phone or "Someone"


def ping(title: str, subtitle: str, body: str) -> None:
    """Sound + macOS notification. The text is someone's WhatsApp message, so it only ever travels as
    an argument: it is never part of the AppleScript source."""
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(["afplay", SOUND], timeout=5, capture_output=True)
    except Exception:
        pass
    try:
        subprocess.run(["osascript", "-e", "on run argv", "-e",
                        "display notification (item 3 of argv) with title (item 1 of argv) subtitle (item 2 of argv)",
                        "-e", "end run", title, subtitle, body],
                       timeout=10, capture_output=True)
    except Exception as e:
        print(f"  [WhatsApp inbox] ⚠️ notification failed: {e}")


class WhatsAppInbox:
    IDLE_TEXT = "🔔 Messages"

    def __init__(self, app, parent):
        self.app = app
        self.items = {}           # seq → message
        self.seq = None           # newest seq the gateway reported; None until its first answer
        self.seen = 0
        self.unread = 0
        self.win = None
        self.text = None
        self._result = None       # handed over from the fetch thread; Tk is only touched in _tick
        self._fetching = False
        self._woke_at = 0.0

        self.badge = tk.Label(parent, text=self.IDLE_TEXT, font=(app.FONT, 10, "bold"),
                              bg=app.CARD, fg=app.DIM, padx=8, cursor="hand2")
        self.badge.pack(side="left", padx=(0, 4))
        self.badge.bind("<Button-1>", lambda _e: self.open())
        app._tip(self.badge, "WHATSAPP GROUP MESSAGES:\n"
                             "Tells you when someone writes in HOANK Orders Alerts\n"
                             "or Hathoro Orders Alerts. Click to see who wrote what.")
        app.after(2500, self._tick)

    # ── polling ─────────────────────────────────────────────────────────────────────────────
    def _tick(self):
        try:
            result, self._result = self._result, None
            if result is not None:
                self._apply(result)
            if not self._fetching:
                self._fetching = True
                threading.Thread(target=self._fetch, args=(self.seq,), daemon=True).start()
        except Exception as e:
            print(f"  [WhatsApp inbox] ⚠️ {e}")
        finally:
            self.app.after(POLL_MS, self._tick)

    def _fetch(self, since):
        try:
            params = {} if since is None else {"since": since}
            r = requests.get(f"{_gateway()}/inbox", params=params, timeout=2)
            r.raise_for_status()
            self._result = r.json()
        except Exception:
            self._wake_gateway()
        finally:
            self._fetching = False

    def _wake_gateway(self):
        """Nothing starts the gateway until the first alert, and a stopped gateway hears no messages."""
        if time.time() - self._woke_at < WAKE_GATEWAY_EVERY_S or not is_whatsapp_enabled():
            return
        self._woke_at = time.time()
        ensure_gateway_running()

    def _apply(self, data):
        seq = int(data.get("seq") or 0)
        if self.seq is not None and seq < self.seq:      # the gateway's inbox was reset: start over
            self.items.clear()
            self.seq = None
            return
        first_answer = self.seq is None
        new = [m for m in data.get("items") or [] if m["seq"] not in self.items]
        for m in new:
            self.items[m["seq"]] = m
        for old in sorted(self.items)[:-200]:
            del self.items[old]
        self.seq = seq
        self.seen = int(data.get("seen") or 0)
        self.unread = int(data.get("unread") or 0)
        self._paint_badge()
        if new and not first_answer:      # opening the launcher shows the count, not a burst of alarms
            self._announce(new)

    # ── showing it ──────────────────────────────────────────────────────────────────────────
    def _paint_badge(self):
        busy = self.unread > 0
        self.badge.config(text=f"🔔 {self.unread} new" if busy else self.IDLE_TEXT,
                          fg=self.app.AMBER if busy else self.app.DIM)

    def _announce(self, new):
        self.open()
        latest = new[-1]
        more = len(new) - 1
        body = latest.get("text", "") + (f"\n+{more} more" if more else "")
        threading.Thread(target=ping, args=(latest.get("group", "WhatsApp"), who_wrote(latest), body),
                         daemon=True).start()

    def open(self):
        if self.win is None or not self.win.winfo_exists():
            self._build_window()
        self._render()
        win = self.win
        win.deiconify()
        win.lift()
        win.attributes("-topmost", True)     # in front once, not pinned there
        win.after(1500, lambda: win.winfo_exists() and win.attributes("-topmost", False))

    def _build_window(self):
        a = self.app
        win = tk.Toplevel(a)
        win.title("WhatsApp group messages")
        win.configure(bg=a.BG)
        x = a.winfo_rootx() + max(0, a.winfo_width() - 540)
        win.geometry(f"500x440+{x}+{a.winfo_rooty() + 70}")
        win.protocol("WM_DELETE_WINDOW", self.close)

        bottom = tk.Frame(win, bg=a.BG)
        bottom.pack(side="bottom", fill="x", padx=12, pady=(4, 10))
        tk.Button(bottom, text="Mark read and close", command=self.close, font=(a.FONT, 11),
                  relief="flat", cursor="hand2", highlightbackground=a.BG).pack(side="right")

        bar = tk.Scrollbar(win)
        bar.pack(side="right", fill="y")
        text = tk.Text(win, wrap="word", bg=a.CARD, fg=a.TEXT, relief="flat", bd=0, padx=14, pady=12,
                       font=(a.FONT, 12), spacing1=1, yscrollcommand=bar.set, state="disabled", cursor="arrow")
        text.pack(side="left", fill="both", expand=True)
        bar.config(command=text.yview)
        text.tag_configure("group", foreground=a.DIM, font=(a.FONT, 10))
        text.tag_configure("who", foreground=a.TEXT, font=(a.FONT, 13, "bold"))
        text.tag_configure("who_new", foreground=a.AMBER, font=(a.FONT, 13, "bold"))
        text.tag_configure("phone", foreground=a.DIM, font=(a.MONO, 10))
        text.tag_configure("body", spacing3=14)
        self.win, self.text = win, text

    def _render(self):
        t = self.text
        t.config(state="normal")
        t.delete("1.0", "end")
        if not self.items:
            t.insert("end", "No messages yet.", "group")
        for seq in sorted(self.items, reverse=True):
            m = self.items[seq]
            when = datetime.fromtimestamp(m.get("at", 0) / 1000).strftime("%d %b, %H:%M")
            phone = f"   +{m['phone']}" if m.get("phone") and m.get("from") else ""   # else it is the name
            t.insert("end", f"{m.get('group', '')}  ·  {when}\n", "group")
            t.insert("end", who_wrote(m), "who_new" if seq > self.seen else "who")
            t.insert("end", f"{phone}\n", "phone")
            t.insert("end", f"{m.get('text', '')}\n", "body")
        t.config(state="disabled")
        t.yview_moveto(0)

    def close(self):
        if self.win is not None and self.win.winfo_exists():
            self.win.destroy()
        self.win = self.text = None
        if self.seq is None or self.seq <= self.seen:
            return
        seq = self.seq
        self.seen = seq
        self.unread = 0
        self._paint_badge()
        threading.Thread(target=self._post_seen, args=(seq,), daemon=True).start()

    @staticmethod
    def _post_seen(seq):
        try:
            requests.post(f"{_gateway()}/inbox/seen", json={"seq": seq}, timeout=3)
        except Exception as e:
            print(f"  [WhatsApp inbox] ⚠️ could not mark read: {e}")
