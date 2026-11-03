"""Paper Broker: simuliert Orders mit Spielgeld. Kein echtes Geld, kein Risiko.
Der Stand wird in einer JSON-Datei gespeichert, damit ein Neustart des Laptops nichts verliert."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from tradingbot.execution.broker import Broker
from tradingbot.risk.manager import OrderPlan


class PaperBroker(Broker):
    def __init__(self, start_capital: float, point_values: dict[str, float], fee: float = 1.0,
                 state_file: Path | None = None):
        self._equity = start_capital
        self.point_values = point_values
        self.fee = fee
        self.positions: dict[str, OrderPlan] = {}
        self.state_file = state_file
        if state_file and state_file.exists():
            st = json.loads(state_file.read_text())
            self._equity = st["equity"]
            self.positions = {k: OrderPlan(**v) for k, v in st["positions"].items()}

    def save(self) -> None:
        if self.state_file:
            self.state_file.parent.mkdir(exist_ok=True)
            self.state_file.write_text(json.dumps(
                {"equity": self._equity, "positions": {k: asdict(v) for k, v in self.positions.items()}}, indent=2))

    def equity(self) -> float:
        return self._equity

    def open_positions(self) -> dict[str, OrderPlan]:
        return dict(self.positions)

    def place_bracket(self, symbol: str, plan: OrderPlan) -> None:
        if symbol in self.positions:
            raise ValueError(f"{symbol}: Position bereits offen")
        self.positions[symbol] = plan
        self.save()

    def on_price(self, symbol: str, high: float, low: float, close: float) -> list[dict]:
        plan = self.positions.get(symbol)
        if not plan:
            return []
        hit_sl = low <= plan.stop_loss if plan.side > 0 else high >= plan.stop_loss
        hit_tp = high >= plan.take_profit if plan.side > 0 else low <= plan.take_profit
        if not (hit_sl or hit_tp):
            return []
        exit_px = plan.stop_loss if hit_sl else plan.take_profit  # beides in einer Kerze: konservativ SL
        pnl = (exit_px - plan.entry) * plan.side * plan.contracts * self.point_values.get(symbol, 1.0) - 2 * self.fee
        self._equity += pnl
        del self.positions[symbol]
        self.save()
        return [{"symbol": symbol, "side": plan.side, "entry": plan.entry, "exit": exit_px,
                 "stop_loss": plan.stop_loss, "take_profit": plan.take_profit, "contracts": plan.contracts,
                 "reason": "sl" if hit_sl else "tp", "pnl": round(pnl, 2), "equity": round(self._equity, 2)}]
