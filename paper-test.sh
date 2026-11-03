#!/bin/sh
# Startet den Paper-Testlauf mit Spielgeld (Gold + Nasdaq, 5-Minuten-Kerzen).
# Mac: caffeinate verhindert den Ruhezustand. Beenden mit Ctrl + C.
cd "$(dirname "$0")"
. .venv/bin/activate
STRATEGY="${1:-trend_breakout(lookback=20, adx_min=25)}"
if command -v caffeinate >/dev/null 2>&1; then KEEP_AWAKE="caffeinate -i"; else KEEP_AWAKE=""; fi
exec $KEEP_AWAKE tradingbot paper --strategy "$STRATEGY" --symbols GC=F NQ=F --timeframe 5m
