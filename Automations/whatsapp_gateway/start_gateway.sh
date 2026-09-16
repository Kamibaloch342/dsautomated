#!/bin/bash
# start_gateway.sh — Start ZUHRA WhatsApp Gateway in background
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if pgrep -f "whatsapp_gateway/server.js" > /dev/null; then
    echo "WhatsApp Gateway is already running."
else
    nohup node "$DIR/server.js" > "$DIR/gateway.log" 2>&1 &
    echo "WhatsApp Gateway started in background (PID: $!)."
fi
