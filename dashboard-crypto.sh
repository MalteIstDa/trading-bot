#!/bin/sh
# Dashboard fuer den Krypto-Testlauf (Port 8051, damit es neben dem Futures-Dashboard laufen kann).
cd "$(dirname "$0")"
. .venv/bin/activate
export TRADINGBOT_DATA=data/crypto
exec tradingbot dashboard --port 8051
