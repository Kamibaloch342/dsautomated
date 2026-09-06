#!/usr/bin/env bash
# start_tunnel.sh — High-Performance, Permanent Tunnel for the ZUHRA Dashboard
# Powered by Bore (Rust TCP tunnel) with fixed remote port for zero session limits.
#
# Usage:
#   bash scripts/start_tunnel.sh          # start in background with supervisor
#   bash scripts/start_tunnel.sh --link   # print current active link
#   bash scripts/start_tunnel.sh --status # check tunnel health
#   bash scripts/start_tunnel.sh --stop   # stop tunnel and supervisor
#
set -uo pipefail

AUTO="$(cd "$(dirname "$0")/.." && pwd)"
PORT=8000
REMOTE_PORT=48000
BORE="/opt/homebrew/bin/bore"
LOG="$AUTO/data/tunnel.log"
LINK_FILE="$AUTO/data/public_link.txt"
PID_FILE="$AUTO/data/tunnel.pid"
SUP_PID_FILE="$AUTO/data/tunnel_supervisor.pid"
SUP_LOG="$AUTO/data/tunnel_supervisor.log"

tunnel_alive() {
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null)" || return 1
  [ -n "${pid:-}" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  return 0
}

tunnel_serving() {
  local url
  url="$(cat "$LINK_FILE" 2>/dev/null)" || return 1
  [ -n "${url:-}" ] || return 1
  local resp
  resp="$(curl -s -m 5 "$url/" 2>/dev/null || true)"
  if echo "$resp" | grep -qE "(DS Dashboard|Orders|sign in|team password|kamran)"; then
    return 0
  fi
  return 1
}

stop_tunnel() {
  local sup_pid
  sup_pid="$(cat "$SUP_PID_FILE" 2>/dev/null)" || true
  if [ -n "${sup_pid:-}" ]; then
    kill "$sup_pid" 2>/dev/null || true
    rm -f "$SUP_PID_FILE"
  fi

  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null)" || true
  if [ -n "${pid:-}" ]; then
    kill "$pid" 2>/dev/null || true
    rm -f "$PID_FILE"
  fi

  pkill -f "bore local" 2>/dev/null || true
  pkill -f "ssh.*nokey@localhost.run" 2>/dev/null || true
  pkill -f "localtunnel.*--port $PORT" 2>/dev/null || true
}

case "${1:-}" in
  --stop)
    stop_tunnel
    echo "■ Tunnel stopped"
    exit 0
    ;;
  --link)
    if [ -s "$LINK_FILE" ]; then
      cat "$LINK_FILE"
    else
      echo "No active tunnel link — run: bash scripts/start_tunnel.sh"
    fi
    exit 0
    ;;
  --status)
    if tunnel_alive; then
      echo "✅ Tunnel process RUNNING (PID: $(cat "$PID_FILE"))"
      echo "URL: $(cat "$LINK_FILE" 2>/dev/null)"
      if tunnel_serving; then
        echo "🌐 HTTP status: ONLINE & RESPONDING DIRECTLY"
      else
        echo "⚠️ HTTP status: CONNECTING..."
      fi
    else
      echo "❌ Tunnel is NOT running"
    fi
    exit 0
    ;;
esac

start_single() {
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null)" || true
  [ -n "${pid:-}" ] && kill "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"

  mkdir -p "$AUTO/data"
  : > "$LOG"

  # Launch Bore client to bore.pub with fixed remote port
  nohup "$BORE" local "$PORT" --to bore.pub --port "$REMOTE_PORT" >> "$LOG" 2>&1 &
  local new_pid=$!
  echo "$new_pid" > "$PID_FILE"

  for _ in $(seq 1 15); do
    sleep 1
    if grep -q "listening at" "$LOG"; then
      local bound_port
      bound_port="$(grep -oE 'listening at bore.pub:[0-9]+' "$LOG" | head -1 | cut -d: -f2)"
      if [ -n "$bound_port" ]; then
        local url="http://bore.pub:$bound_port"
        printf '%s\n' "$url" > "$LINK_FILE"
        return 0
      fi
    fi
    kill -0 "$new_pid" 2>/dev/null || break
  done

  # If fixed port was taken, retry without fixed port
  nohup "$BORE" local "$PORT" --to bore.pub >> "$LOG" 2>&1 &
  new_pid=$!
  echo "$new_pid" > "$PID_FILE"
  for _ in $(seq 1 15); do
    sleep 1
    if grep -q "listening at" "$LOG"; then
      local bound_port
      bound_port="$(grep -oE 'listening at bore.pub:[0-9]+' "$LOG" | head -1 | cut -d: -f2)"
      if [ -n "$bound_port" ]; then
        local url="http://bore.pub:$bound_port"
        printf '%s\n' "$url" > "$LINK_FILE"
        return 0
      fi
    fi
    kill -0 "$new_pid" 2>/dev/null || break
  done

  return 1
}

# Supervisor loop
if [ "${1:-}" = "--supervise" ]; then
  echo $$ > "$SUP_PID_FILE"
  fails=0
  while true; do
    if ! tunnel_alive; then
      echo "$(date '+%F %T') tunnel process died — restarting" >> "$SUP_LOG"
      start_single || true
      fails=0
    elif ! tunnel_serving; then
      fails=$((fails + 1))
      echo "$(date '+%F %T') ⚠️ tunnel not responding (strike $fails/3)" >> "$SUP_LOG"
      if [ "$fails" -ge 3 ]; then
        echo "$(date '+%F %T') ♻️ restarting dead tunnel socket" >> "$SUP_LOG"
        start_single || true
        fails=0
      fi
    else
      fails=0
    fi
    sleep 20
  done
fi

# Main start flow
stop_tunnel
sleep 1

echo "Starting permanent high-speed tunnel for dashboard (port $PORT)..."

started=0
for attempt in 1 2 3; do
  if start_single; then
    started=1
    break
  fi
  echo "   retrying tunnel connection (attempt $attempt failed)…"
  sleep 2
done

if [ "$started" = "1" ]; then
  nohup caffeinate -s bash "$0" --supervise >> "$SUP_LOG" 2>&1 &
  url="$(cat "$LINK_FILE")"
  pw="$(cat "$AUTO/data/dashboard_password.txt" 2>/dev/null || echo "YourNewPassword")"

  echo ""
  echo "════════════════════════════════════════════════════════════════════"
  echo "✅ PERMANENT HIGH-SPEED TUNNEL IS ONLINE"
  echo "════════════════════════════════════════════════════════════════════"
  echo "🌍 Public Dashboard Link:"
  echo "   $url"
  echo ""
  echo "🔒 Dashboard Team Password:"
  echo "   $pw"
  echo "════════════════════════════════════════════════════════════════════"
  echo "• Permanent link on fixed port — NO 15-minute expiration."
  echo "• NO password/IP interstitial screen (opens directly)."
  echo "• Background supervisor is active with caffeinate (Mac stays awake)."
else
  echo "❌ Failed to start tunnel. Check logs: $LOG"
  exit 1
fi
