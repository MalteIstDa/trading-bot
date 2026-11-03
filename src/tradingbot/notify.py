"""Optionale Handy-Benachrichtigungen per Telegram (Trade eroeffnet/geschlossen, Kill Switch, Tagesbilanz).

Einrichten (einmalig, kostenlos):
  1. In Telegram @BotFather anschreiben, /newbot, Namen vergeben -> du bekommst einen Token.
  2. Deinem neuen Bot eine Nachricht schicken, dann https://api.telegram.org/bot<TOKEN>/getUpdates oeffnen
     und dort "chat":{"id": ...} ablesen.
  3. Im Ordner trading-bot eine Datei `.env` anlegen (wird NICHT ins Repo hochgeladen):
        TELEGRAM_TOKEN=123456:ABC...
        TELEGRAM_CHAT_ID=987654321
Ohne diese Angaben macht das Modul einfach nichts. Fehler beim Senden stoppen den Bot nie.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request

from tradingbot.config import ROOT

log = logging.getLogger("tradingbot")


def _settings() -> tuple[str | None, str | None]:
    env = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    token = os.environ.get("TELEGRAM_TOKEN") or env.get("TELEGRAM_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID") or env.get("TELEGRAM_CHAT_ID")
    return token, chat


def enabled() -> bool:
    token, chat = _settings()
    return bool(token and chat)


def send(text: str) -> bool:
    """Schickt eine Nachricht. Gibt False zurueck, wenn nicht eingerichtet oder fehlgeschlagen."""
    token, chat = _settings()
    if not (token and chat):
        return False
    try:
        from tradingbot.traders.hyperliquid import _SSL  # gleiche Zertifikate (certifi) wie fuer Hyperliquid
        data = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=10, context=_SSL) as r:
            return bool(json.loads(r.read()).get("ok"))
    except Exception as e:  # Benachrichtigung darf den Bot nie stoppen
        log.warning("Telegram: %s", e)
        return False
