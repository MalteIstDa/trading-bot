import pytest


@pytest.fixture(autouse=True)
def _no_live_feed(monkeypatch):
    """Tests sollen nie eine echte WebSocket-Verbindung aufbauen."""
    from tradingbot.traders import hyperliquid as hl
    monkeypatch.setattr(hl.LiveFeed, "available", staticmethod(lambda: False))


@pytest.fixture(autouse=True)
def _no_vault(monkeypatch):
    """Tests sollen nie ins echte Obsidian-Logbuch schreiben."""
    from tradingbot import vault
    monkeypatch.setattr(vault, "_path", lambda: None)
