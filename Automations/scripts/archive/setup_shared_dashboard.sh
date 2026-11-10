#!/usr/bin/env bash
# setup_shared_dashboard.sh — run the CRM dashboard in the background (24/7 while the Mac is on)
# and print the Tailscale link to share with your colleague. Run:
#     bash Automations/scripts/setup_shared_dashboard.sh
#
# NOTE: the project lives under ~/Desktop, which macOS privacy (TCC) blocks background launchd
# agents from reading. So we run it from THIS Terminal's context (which has Desktop access) via
# nohup+caffeinate — it keeps running after you close Terminal and keeps the Mac awake.
# It does NOT survive a reboot; just re-run this one command after a reboot (rare on an always-on Mac).
set -euo pipefail

PORT=8000
AUTO="$(cd "$(dirname "$0")/.." && pwd)"          # .../Automations
ROOT="$(cd "$AUTO/.." && pwd)"                     # .../Development_Unit
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY="$(command -v python3)"
LOG="$AUTO/data/dashboard_service.log"
TS="/Applications/Tailscale.app/Contents/MacOS/Tailscale"

# --install-autostart → also install a LaunchAgent so the dashboard auto-starts every login/reboot
# (needs a one-time Full Disk Access grant on Python, printed at the end).
AUTOSTART=0
for _a in "$@"; do [ "$_a" = "--install-autostart" ] && AUTOSTART=1; done
PLIST="$HOME/Library/LaunchAgents/com.zuhra.dashboard.plist"

# Unload any previous agent first (avoids crash-loop / duplicate).
launchctl bootout "gui/$(id -u)/com.dropship.dashboard" 2>/dev/null || true
launchctl bootout "gui/$(id -u)/com.zuhra.dashboard" 2>/dev/null || true

if [ "$AUTOSTART" = "1" ]; then
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.zuhra.dashboard</string>
  <!-- NO caffeinate (cannot assert under launchd → exit 78) and logs must live
       OUTSIDE ~/Downloads (TCC blocks launchd from opening them → exit 78 with
       no output). Both together crash-looped the old agent 648 times. -->
  <key>ProgramArguments</key><array>
    <string>$PY</string><string>$AUTO/dashboard/server.py</string><string>$PORT</string>
  </array>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>WorkingDirectory</key><string>$AUTO</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/zuhra-dashboard.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/zuhra-dashboard.log</string>
</dict></plist>
PLISTEOF
  launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  echo "🔧 Auto-start installed → $PLIST (loads on every login/reboot; restarts on crash)."
else
  rm -f "$PLIST"
fi

# Stop any dashboard already on the port, then start fresh.
lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | xargs kill 2>/dev/null || true
pkill -f "dashboard/server.py" 2>/dev/null || true
sleep 1

cd "$AUTO"; mkdir -p data
if [ "$AUTOSTART" = "1" ]; then
  # launchd owns the process (KeepAlive restarts it); starting a second copy here
  # would just fight it for port $PORT.
  echo "▶ dashboard started by launchd agent com.zuhra.dashboard"
  sleep 4
else
  echo "▶ starting dashboard: $PY dashboard/server.py $PORT"
  nohup caffeinate -s "$PY" dashboard/server.py "$PORT" > "$LOG" 2>&1 &
  sleep 2
fi

if curl -fsS -m 5 "http://localhost:$PORT/" >/dev/null 2>&1; then
  echo "✅ Dashboard is UP on port $PORT (stays up while the Mac is on)."
else
  echo "⚠️  Not reachable yet — last log lines:"; tail -8 "$LOG"; exit 1
fi

echo
if [ -x "$TS" ]; then
  HOST="$("$TS" status --json 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)"
  IP="$("$TS" ip -4 2>/dev/null | head -1 || true)"
  if [ -n "${HOST:-}" ]; then
    echo "🔗 Share EITHER of these with your colleague (she needs Tailscale + your invite):"
    echo "     http://$HOST:$PORT"
    [ -n "${IP:-}" ] && echo "     http://$IP:$PORT"
  else
    echo "🔗 Tailscale app found but not logged in — open it, sign in, then re-run this script."
  fi
else
  echo "🔗 Install Tailscale (tailscale.com/download or 'brew install --cask tailscale'), log in, invite her, then re-run."
fi
# ── Tailscale Funnel: PUBLIC https link at the machine's PERMANENT name.
# Nothing to install on the viewer's side, and the URL never changes (it
# replaced the rotating trycloudflare quick tunnels on 2026-08-14). The
# dashboard's password gate covers public visitors.
# Served on ALL THREE Funnel-capable ports (443/8443/10000): some ISPs' DPI
# resets TLS to unknown domains on 443 (a viewer in PK hit ERR_CONNECTION_CLOSED
# on 443 while the same Funnel served fine from other networks, 2026-08-17) —
# the alternate ports usually slip through. Same dashboard on each.
if [ -x "$TS" ]; then
  "$TS" funnel --bg "http://localhost:$PORT" >/dev/null 2>&1 || true
  for _fp in 8443 10000; do
    "$TS" funnel --bg --https=$_fp "http://localhost:$PORT" >/dev/null 2>&1 || true
  done
  FURL="$("$TS" funnel status 2>/dev/null | grep -oE 'https://[a-z0-9.-]+\.ts\.net$' | head -1)"
  if [ -n "${FURL:-}" ]; then
    echo "🌍 PUBLIC links (any browser, nothing to install — password required):"
    echo "     $FURL              ← try this first"
    echo "     $FURL:8443         ← if the first one won't connect"
    echo "     $FURL:10000        ← last resort"
    echo "     password: $(cat "$AUTO/data/dashboard_password.txt" 2>/dev/null || echo '<set on first dashboard start>')"
    echo "     (permanent URLs — survive restarts and reboots)"
  else
    echo "⚠️  Funnel not confirmed — run: \"$TS\" funnel --bg http://localhost:$PORT"
  fi
fi

# ── Cloudflare quick tunnel: the fallback for viewers whose ISP filters *.ts.net
# outright (all three Funnel ports failed for a PK viewer on 2026-08-17).
# Best-effort — Cloudflare rate-limits quick-tunnel creation, so a failure here
# must NOT fail the whole setup; the Tailscale links above still work.
if [ -x "$AUTO/bin/cloudflared" ]; then
  echo
  bash "$AUTO/scripts/public_tunnel.sh" 2>&1 | tail -4 || \
    echo "⚠️  Cloudflare tunnel unavailable right now — retry: bash $AUTO/scripts/public_tunnel.sh"
fi

echo
if [ "$AUTOSTART" = "1" ]; then
  echo "════════════════════════════════════════════════════════════════════"
  echo "✅ Reboot survival is ACTIVE — no Full Disk Access grant needed."
  echo "    (The old agent's exit-78 crash-loop was caffeinate + a log path inside"
  echo "     ~/Downloads that launchd may not open, NOT a permissions grant.)"
  echo "    Agent log: ~/Library/Logs/zuhra-dashboard.log"
  echo "    Reset if ever needed:  launchctl bootout gui/\$(id -u)/com.zuhra.dashboard; rm \"$PLIST\""
  echo "════════════════════════════════════════════════════════════════════"
fi
echo "Stop:  pkill -f dashboard/server.py        Logs:  tail -f \"$LOG\""
