#!/usr/bin/env bash
# public_tunnel.sh — Cloudflare quick tunnel for the dashboard, for viewers whose
# ISP filters *.ts.net (a PK viewer got ERR_CONNECTION_CLOSED on all three
# Tailscale Funnel ports on 2026-08-17 while the same Funnel served fine from
# other networks). Cloudflare's domains are effectively never DPI-filtered.
#
#   bash Automations/scripts/public_tunnel.sh          # start + supervise (background)
#   bash Automations/scripts/public_tunnel.sh --link   # just print the current link
#   bash Automations/scripts/public_tunnel.sh --stop   # stop the tunnel + supervisor
#
# The quick-tunnel hostname is RANDOM and CHANGES whenever cloudflared restarts,
# so the supervisor rewrites data/public_link.txt every time — always read the
# link from that file (or --link) rather than remembering an old one.
#
# Liveness is tracked by PID FILE, never by pgrep on the command line: any shell
# that merely MENTIONS the cloudflared command (a pkill, a grep, this script's
# own docs) matches a loose pgrep pattern, which made an earlier version believe
# a dead tunnel was still alive and skip the restart.
set -uo pipefail

AUTO="$(cd "$(dirname "$0")/.." && pwd)"
CF="$AUTO/bin/cloudflared"
PORT=8000
LOG="$AUTO/data/cloudflared.log"
LINK_FILE="$AUTO/data/public_link.txt"
PID_FILE="$AUTO/data/cloudflared.pid"
SUP_PID_FILE="$AUTO/data/public_tunnel_supervisor.pid"
SUP_LOG="$AUTO/data/public_tunnel_supervisor.log"
METRICS="127.0.0.1:20241"   # cloudflared readiness endpoint (see tunnel_serving)

# True only if PID_FILE names a live process that really is our cloudflared.
tunnel_alive() {
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null)" || return 1
  [ -n "${pid:-}" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  ps -o command= -p "$pid" 2>/dev/null | grep -q "cloudflared" || return 1
  return 0
}

# True only when the PUBLISHED url actually answers from the public internet.
# A live PID is NOT proof of a working tunnel: on 2026-08-18 cloudflared ran for
# ~5 hours while every edge connection failed in a retry loop ("control stream
# encountered a failure while serving"), so the link was dead the whole time and
# the PID-only supervisor never restarted it. THIS is what to trust.
# Ask cloudflared ITSELF how many edge connections are live, via its local
# readiness endpoint. Probing the public URL from this Mac is NOT usable as a
# health signal: this ISP blackholes part of Cloudflare's IP range (104.16.231.x),
# so a perfectly good tunnel looks dead from here and the supervisor would rotate
# the URL forever. This check is local, instant, and reflects the real state.
tunnel_serving() {
  local body
  body="$(curl -s -m 5 "http://$METRICS/ready" 2>/dev/null)" || return 1
  [ -n "${body:-}" ] || return 1
  case "$body" in
    *'"readyConnections":0'*) return 1 ;;   # registered nothing → genuinely dead
    *'"readyConnections"'*)   return 0 ;;   # >=1 live edge connection
    *) return 1 ;;
  esac
}

stop_tunnel() {
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null)"
  [ -n "${pid:-}" ] && kill "$pid" 2>/dev/null
  rm -f "$PID_FILE"
}

case "${1:-}" in
  --link)
    [ -s "$LINK_FILE" ] && cat "$LINK_FILE" || echo "no tunnel link yet — run: bash $0"
    exit 0 ;;
  --stop)
    local_sup="$(cat "$SUP_PID_FILE" 2>/dev/null)"
    [ -n "${local_sup:-}" ] && kill "$local_sup" 2>/dev/null
    rm -f "$SUP_PID_FILE"
    stop_tunnel
    rm -f "$LINK_FILE"
    echo "■ tunnel stopped"
    exit 0 ;;
esac

# Start cloudflared, wait for it to publish a hostname, record PID + URL.
start_tunnel() {
  stop_tunnel
  sleep 1
  : > "$LOG"
  nohup "$CF" tunnel --url "http://localhost:$PORT" --no-autoupdate --metrics "$METRICS" >> "$LOG" 2>&1 &
  echo $! > "$PID_FILE"
  local url
  for _ in $(seq 1 40); do          # up to ~40s for the hostname to appear
    sleep 1
    # api.trycloudflare.com is Cloudflare's OWN endpoint and appears in the log's
    # error lines ("failed to request quick Tunnel: Post https://api.trycloudflare
    # .com/tunnel: …") — matching it would publish a link to Cloudflare's API
    # instead of the dashboard, so it is excluded explicitly.
    url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" \
           | grep -vx 'https://api.trycloudflare.com' | head -1)"
    if [ -n "${url:-}" ]; then
      printf '%s\n' "$url" > "$LINK_FILE"
      echo "$(date '+%F %T') tunnel up: $url"
      return 0
    fi
    if grep -q "failed to request quick Tunnel" "$LOG"; then
      echo "$(date '+%F %T') ⚠️ Cloudflare refused/timed out the tunnel request — will retry"
      stop_tunnel
      return 1
    fi
    tunnel_alive || break          # cloudflared died on startup — stop waiting
  done
  echo "$(date '+%F %T') ⚠️ cloudflared produced no URL — see $LOG"
  stop_tunnel
  return 1
}

# Supervisor: quick tunnels drop; restart and re-record the (new) hostname.
if [ "${1:-}" = "--supervise" ]; then
  echo $$ > "$SUP_PID_FILE"
  fails=0
  while true; do
    if ! tunnel_alive; then
      echo "$(date '+%F %T') tunnel process gone — restarting"
      start_tunnel; fails=0
    elif tunnel_serving; then
      fails=0
    else
      fails=$((fails + 1))
      echo "$(date '+%F %T') ⚠️ cloudflared reports no live edge connection (strike $fails/3)"
      if [ "$fails" -ge 3 ]; then     # three strikes ≈ 3 min, rides out ISP blips
        echo "$(date '+%F %T') ♻️ zombie tunnel (process up, link dead) — restarting"
        start_tunnel; fails=0
      fi
    fi
    sleep 60
  done
fi

[ -x "$CF" ] || { echo "❌ cloudflared missing at $CF"; exit 1; }

# Replace any previous supervisor before starting a new one.
prev_sup="$(cat "$SUP_PID_FILE" 2>/dev/null)"
[ -n "${prev_sup:-}" ] && kill "$prev_sup" 2>/dev/null
# Quick-tunnel registration is best-effort and times out under rapid restarts —
# retry a few times before giving up.
started=0
for attempt in 1 2 3; do
  if start_tunnel; then started=1; break; fi
  echo "   retrying tunnel (attempt $attempt failed)…"; sleep 5
done
[ "$started" = "1" ] || { echo "❌ could not open a Cloudflare tunnel — see $LOG"; exit 1; }
# caffeinate -s matches how the dashboard itself is kept alive on this Mac.
nohup caffeinate -s bash "$0" --supervise >> "$SUP_LOG" 2>&1 &
echo "🌍 PUBLIC link (works on networks that block .ts.net — password required):"
echo "     $(cat "$LINK_FILE")"
echo "     (random hostname — it CHANGES on restart; re-read it with: bash $0 --link)"
