#!/bin/bash
# install_zoro_schedule.sh — run the Zoro feed + listing refresh once a day.
#
# Uses launchd (not cron): on macOS launchd survives reboots, and with
# RunAtLoad + StartCalendarInterval it also catches up a run that was missed
# because the Mac was asleep or shut down at the scheduled time.
#
#   ./scripts/install_zoro_schedule.sh            # install, runs daily 06:00
#   ./scripts/install_zoro_schedule.sh 3 30       # daily at 03:30
#   ./scripts/install_zoro_schedule.sh --uninstall
#   ./scripts/install_zoro_schedule.sh --status
#   ./scripts/install_zoro_schedule.sh --run-now
set -euo pipefail

LABEL="com.zuhra.zoro-refresh"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

AUTOMATIONS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(cd "$AUTOMATIONS/.." && pwd)"
PY="$PROJECT_ROOT/.venv/bin/python"
SCRIPT="$AUTOMATIONS/scripts/refresh_zoro_and_listings.py"
LOGDIR="$AUTOMATIONS/data"

case "${1:-}" in
  --uninstall)
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "✅ Uninstalled ${LABEL}"
    exit 0
    ;;
  --status)
    echo "plist: $PLIST"
    [ -f "$PLIST" ] && echo "  installed" || { echo "  NOT installed"; exit 1; }
    launchctl print "gui/$(id -u)/${LABEL}" 2>/dev/null | grep -E "state|last exit|runs" || \
      launchctl list | grep "$LABEL" || echo "  (not currently loaded)"
    echo "--- last refresh log ---"
    tail -15 "$LOGDIR/zoro_refresh.log" 2>/dev/null || echo "  (no log yet)"
    exit 0
    ;;
  --run-now)
    launchctl kickstart -k "gui/$(id -u)/${LABEL}" 2>/dev/null \
      && echo "✅ Triggered — watch $LOGDIR/zoro_refresh.log" \
      || { echo "not loaded; running directly…"; "$PY" "$SCRIPT"; }
    exit 0
    ;;
esac

HOUR="${1:-6}"
MINUTE="${2:-0}"

[ -x "$PY" ]     || { echo "❌ venv python not found: $PY"; exit 1; }
[ -f "$SCRIPT" ] || { echo "❌ refresh script not found: $SCRIPT"; exit 1; }
mkdir -p "$LOGDIR" "$(dirname "$PLIST")"

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PY}</string>
        <string>${SCRIPT}</string>
    </array>
    <key>WorkingDirectory</key><string>${AUTOMATIONS}</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>${HOUR}</integer>
        <key>Minute</key><integer>${MINUTE}</integer>
    </dict>
    <!-- Catch up if the Mac was asleep/off at the scheduled time. -->
    <key>RunAtLoad</key><false/>
    <key>StandardOutPath</key><string>${LOGDIR}/zoro_refresh.out.log</string>
    <key>StandardErrorPath</key><string>${LOGDIR}/zoro_refresh.err.log</string>
    <key>ProcessType</key><string>Background</string>
    <!-- ~1.2 GB download + a 2M-row rebuild: never let launchd throttle it. -->
    <key>Nice</key><integer>5</integer>
</dict>
</plist>
PLISTEOF

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load "$PLIST"

printf '✅ Installed %s — runs daily at %02d:%02d\n' "$LABEL" "$HOUR" "$MINUTE"
echo "   log:       $LOGDIR/zoro_refresh.log"
echo "   run now:   $0 --run-now"
echo "   status:    $0 --status"
echo "   remove:    $0 --uninstall"
