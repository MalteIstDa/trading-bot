"""Schnittstelle fuer Broker. Ein echter Broker (z. B. Interactive Brokers, Tradovate) wird spaeter
als weitere Klasse mit denselben Methoden eingebaut, der Rest des Bots bleibt gleich."""
from __future__ import annotations

from abc import ABC, abstractmethod

from tradingbot.risk.manager import OrderPlan


class Broker(ABC):
    @abstractmethod
    def equity(self) -> float: ...

    @abstractmethod
    def open_positions(self) -> dict[str, OrderPlan]: ...

    @abstractmethod
    def place_bracket(self, symbol: str, plan: OrderPlan) -> None:
        """Einstieg plus gekoppelter SL- und TP-Order (Bracket Order)."""

    @abstractmethod
    def on_price(self, symbol: str, high: float, low: float, close: float) -> list[dict]:
        """Neue Preise verarbeiten, gibt geschlossene Trades zurueck."""
