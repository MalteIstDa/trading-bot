"""Paper Broker: simuliert Orders mit Spielgeld. Kein echtes Geld, kein Risiko."""
from __future__ import annotations

from tradingbot.execution.broker import Broker
from tradingbot.risk.manager import OrderPlan


class PaperBroker(Broker):
    def __init__(self, start_capital: float, point_values: dict[str, float], fee: float = 1.0):
        self._equity = start_capital
        self.point_values = point_values
        self.fee = fee
        self.positions: dict[str, OrderPlan] = {}

    def equity(self) -> float:
        return self._equity

    def open_positions(self) -> dict[str, OrderPlan]:
        return dict(self.positions)

    def place_bracket(self, symbol: str, plan: OrderPlan) -> None:
        if symbol in self.positions:
            raise ValueError(f"{symbol}: Position bereits offen")
        self.positions[symbol] = plan

    def on_price(self, symbol: str, high: float, low: float, close: float) -> list[dict]:
        plan = self.positions.get(symbol)
        if not plan:
            return []
        hit_sl = low <= plan.stop_loss if plan.side > 0 else high >= plan.stop_loss
        hit_tp = high >= plan.take_profit if plan.side > 0 else low <= plan.take_profit
        if not (hit_sl or hit_tp):
            return []
        exit_px = plan.stop_loss if hit_sl else plan.take_profit
        pnl = (exit_px - plan.entry) * plan.side * plan.contracts * self.point_values.get(symbol, 1.0) - 2 * self.fee
        self._equity += pnl
        del self.positions[symbol]
        return [{"symbol": symbol, "side": plan.side, "entry": plan.entry, "exit": exit_px,
                 "reason": "sl" if hit_sl else "tp", "pnl": pnl}]
