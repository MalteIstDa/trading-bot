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
                 state_file: Path | None = None, fee_pct: float = 0.0):
        self._equity = start_capital
        self.point_values = point_values
        self.fee = fee
        self.fee_pct = fee_pct
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

    def close(self, symbol: str, price: float, reason: str = "manual") -> dict | None:
        """Position sofort zum angegebenen Preis schliessen (z. B. wenn der kopierte Trader aussteigt)."""
        plan = self.positions.get(symbol)
        if not plan:
            return None
        return self._settle(symbol, plan, price, reason)

    def on_price(self, symbol: str, high: float, low: float, close: float) -> list[dict]:
        plan = self.positions.get(symbol)
        if not plan:
            return []
        res = plan.check_exit(high, low)
        if not res:
            plan.trail(high, low)
            self.save()
            return []
        reason, exit_px = res
        return [self._settle(symbol, plan, exit_px, reason)]

    def _settle(self, symbol: str, plan: OrderPlan, exit_px: float, reason: str) -> dict:
        pv = self.point_values.get(symbol, 1.0)
        fees = 2 * self.fee + self.fee_pct / 100 * (plan.entry + exit_px) * plan.contracts * pv
        pnl = (exit_px - plan.entry) * plan.side * plan.contracts * pv - fees
        risk_money = plan.initial_risk * plan.contracts * pv
        self._equity += pnl
        del self.positions[symbol]
        self.save()
        return {"symbol": symbol, "side": plan.side, "entry": plan.entry, "exit": exit_px,
                "stop_loss": plan.stop_loss, "take_profit": plan.take_profit, "contracts": plan.contracts,
                "reason": reason, "pnl": round(pnl, 2),
                "r_multiple": round(pnl / risk_money, 3) if risk_money else 0.0, "equity": round(self._equity, 2)}
