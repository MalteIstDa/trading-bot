"""Risk Manager: setzt SL/TP automatisch (RR 1:3), berechnet Positionsgroesse, stoppt bei Tagesverlust."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from tradingbot.config import RiskConfig


@dataclass
class OrderPlan:
    side: int          # +1 long, -1 short
    entry: float
    stop_loss: float
    take_profit: float
    contracts: int


class RiskManager:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg
        self.day: date | None = None
        self.day_start_equity = 0.0
        self.halted = False

    def new_bar(self, today: date, equity: float) -> None:
        """Zu Beginn jeder Bar aufrufen. Setzt das Tageslimit am neuen Tag zurueck."""
        if today != self.day:
            self.day, self.day_start_equity, self.halted = today, equity, False
        elif equity <= self.day_start_equity * (1 - self.cfg.max_daily_loss_pct / 100):
            self.halted = True  # Kill Switch fuer den Rest des Tages

    def plan(self, side: int, entry: float, atr_value: float, equity: float,
             point_value: float, open_positions: int) -> OrderPlan | None:
        """Erzeugt einen Order-Plan oder None, wenn der Trade gegen eine Regel verstoesst."""
        if self.halted or side == 0 or open_positions >= self.cfg.max_open_positions:
            return None
        if not atr_value or math.isnan(atr_value) or atr_value <= 0:
            return None
        sl_dist = atr_value * self.cfg.sl_atr_mult
        tp_dist = sl_dist * self.cfg.reward_risk
        risk_money = equity * self.cfg.risk_per_trade_pct / 100
        contracts = int(risk_money // (sl_dist * point_value))
        if contracts < 1:
            return None  # Konto zu klein fuer dieses Risiko: lieber kein Trade
        return OrderPlan(side, entry, entry - side * sl_dist, entry + side * tp_dist, contracts)
