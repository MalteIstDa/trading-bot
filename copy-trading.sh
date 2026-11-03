#!/bin/sh
# Copy-Trading der besten Hyperliquid-Trader mit Spielgeld.
#   ./copy-trading.sh scan        beste Trader der letzten 30 Tage finden
#   ./copy-trading.sh test        ehrlicher Test: haette Kopieren frueher funktioniert?
#   ./copy-trading.sh watch       Paper-Copy-Trading starten (Mac bleibt wach, Trader werden taeglich neu gewaehlt)
#   ./copy-trading.sh dashboard   Dashboard dazu auf http://localhost:8052
#   ./copy-trading.sh status      Kontostand, offene Positionen, letzte Trades im Terminal
# Weitere Optionen werden durchgereicht, z. B.: ./copy-trading.sh dashboard --no-browser
cd "$(dirname "$0")"
. .venv/bin/activate
export TRADINGBOT_DATA=data/copy
ACTION="${1:-scan}"
[ $# -gt 0 ] && shift
if [ "$ACTION" = "dashboard" ]; then exec tradingbot dashboard --port 8052 "$@"; fi
if [ "$ACTION" = "status" ]; then exec tradingbot status --config config/hyperliquid.yaml "$@"; fi
if [ "$ACTION" = "watch" ] && command -v caffeinate >/dev/null 2>&1; then
  exec caffeinate -i tradingbot traders watch --config config/hyperliquid.yaml "$@"
fi
exec tradingbot traders "$ACTION" --config config/hyperliquid.yaml "$@"
