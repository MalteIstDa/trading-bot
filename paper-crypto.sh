#!/bin/sh
# Paper-Testlauf mit Krypto (Bitcoin, Ethereum, Solana), 20 USD Spielgeld, 5-Minuten-Kerzen.
# Mac: caffeinate verhindert den Ruhezustand. Beenden mit Ctrl + C.
cd "$(dirname "$0")"
. .venv/bin/activate
export TRADINGBOT_DATA=data/crypto   # eigener Spielgeld-Stand, getrennt von den Futures
STRATEGY="${1:-trend_breakout(lookback=20, adx_min=25)}"
if command -v caffeinate >/dev/null 2>&1; then KEEP_AWAKE="caffeinate -i"; else KEEP_AWAKE=""; fi
exec $KEEP_AWAKE tradingbot paper --config config/crypto.yaml --strategy "$STRATEGY" --timeframe 5m
