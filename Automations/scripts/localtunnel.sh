#!/usr/bin/env bash
# localtunnel.sh — Resilient Localtunnel for the ZUHRA Dashboard
# Automatically detects network/IP changes, restarts dead sockets, and keeps serving.

set -uo pipefail

AUTO="$(cd "$(dirname "$0")/.." && pwd)"
PORT=8000
SUBDOMAIN="kamran-ds-dashboard"
LOG="$AUTO/data/localtunnel.log"
LINK_FILE="$AUTO/data/public_link.txt"
LT_URL_FILE="$AUTO/data/localtunnel.url"
PID_FILE="$AUTO/data/localtunnel.pid"
SUP_PID_FILE="$AUTO/data/localtunnel_supervisor.pid"
SUP_LOG="$AUTO/data/localtunnel_supervisor.log"
IP_FILE="$AUTO/data/public_ip.txt"

get_public_ip() {
  local ip
  ip="$(curl -s -m 4 https://api.ipify.org 2>/dev/null || curl -s -m 4 https://ifconfig.me 2>/dev/null || curl -s -m 4 https://icanhazip.com 2>/dev/null || true)"
  ip="$(echo "$ip" | tr -d '[:space:]')"
  if [ -n "$ip" ]; then
    printf '%s\n' "$ip" > "$IP_FILE"
    echo "$ip"
  elif [ -s "$IP_FILE" ]; then
    cat "$IP_FILE"
  else
    echo "unknown"
  fi
}

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
  resp="$(curl -s -m 6 -H "Bypass-Tunnel-Reminder: true" "$url" 2>/dev/null || true)"
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

  pkill -f "localtunnel.*--port $PORT" 2>/dev/null || true
  pkill -f "npx.*localtunnel" 2>/dev/null || true
}

case "${1:-}" in
  --stop)
    stop_tunnel
    echo "■ Localtunnel stopped"
    exit 0
    ;;
  --link)
    echo "🔗 Tunnel URL:"
    [ -s "$LINK_FILE" ] && cat "$LINK_FILE" || echo "https://${SUBDOMAIN}.loca.lt"
    echo "🔑 Localtunnel Bypass IP (enter this if prompted):"
    get_public_ip
    exit 0
    ;;
  --status)
    if tunnel_alive; then
      echo "✅ Localtunnel process RUNNING (PID: $(cat "$PID_FILE"))"
      echo "URL: $(cat "$LINK_FILE" 2>/dev/null)"
      echo "Host Public IP: $(get_public_ip)"
      if tunnel_serving; then
        echo "🌐 End-to-end HTTP health: RESPONDING OK"
      else
        echo "⚠️ End-to-end HTTP health: WAITING/CONNECTING"
      fi
    else
      echo "❌ Localtunnel is NOT running"
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

  # Run localtunnel
  nohup npx -y localtunnel --port "$PORT" --subdomain "$SUBDOMAIN" >> "$LOG" 2>&1 &
  local new_pid=$!
  echo "$new_pid" > "$PID_FILE"

  for _ in $(seq 1 15); do
    sleep 1
    local url
    url="$(grep -oE 'https://[a-zA-Z0-9-]+\.loca\.lt' "$LOG" | head -1)"
    if [ -n "${url:-}" ]; then
      printf '%s\n' "$url" > "$LINK_FILE"
      printf '%s\n' "$url" > "$LT_URL_FILE"
      return 0
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
    get_public_ip >/dev/null 2>&1 || true
    if ! tunnel_alive; then
      echo "$(date '+%F %T') localtunnel process gone — restarting" >> "$SUP_LOG"
      start_single || true
      fails=0
    elif ! tunnel_serving; then
      fails=$((fails + 1))
      echo "$(date '+%F %T') ⚠️ localtunnel not serving (strike $fails/3)" >> "$SUP_LOG"
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

current_ip="$(get_public_ip)"
echo "Starting Localtunnel for port $PORT..."

started=0
for attempt in 1 2 3; do
  if start_single; then
    started=1
    break
  fi
  echo "   retrying localtunnel connection (attempt $attempt failed)…"
  sleep 3
done

if [ "$started" = "1" ]; then
  nohup caffeinate -s bash "$0" --supervise >> "$SUP_LOG" 2>&1 &
  url="$(cat "$LINK_FILE")"
  pw="$(cat "$AUTO/data/dashboard_password.txt" 2>/dev/null || echo "YourNewPassword")"

  echo ""
  echo "════════════════════════════════════════════════════════════════════"
  echo "✅ TUNNEL IS ACTIVE & ONLINE"
  echo "════════════════════════════════════════════════════════════════════"
  echo "🌍 Public Dashboard Link:"
  echo "   $url"
  echo ""
  echo "🔑 Localtunnel Bypass Password (if prompted on first visit):"
  echo "   $current_ip"
  echo "   (This is your external IP address required by loca.lt security)"
  echo ""
  echo "🔒 Dashboard Login Password:"
  echo "   $pw"
  echo "════════════════════════════════════════════════════════════════════"
  echo "• Background supervisor is running with caffeinate (Mac stays awake)."
  echo "• Auto-heals if IP changes or socket drops."
else
  echo "❌ Failed to start Localtunnel. Check logs: $LOG"
  exit 1
fi
