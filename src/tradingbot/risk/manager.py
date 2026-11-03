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
    take_profit: float  # inf/-inf = kein festes Ziel (Trailing-Modus)
    contracts: float   # Kontrakte (Futures) oder Coins (Krypto, auch Bruchteile)
    initial_risk: float = 0.0  # Abstand Einstieg -> erster Stop (fuer R-Berechnung)
    trail_dist: float = 0.0    # > 0: Stop wird mit dem Kurs nachgezogen

    def __post_init__(self):
        if not self.initial_risk:
            self.initial_risk = abs(self.entry - self.stop_loss)

    def check_exit(self, high: float, low: float) -> tuple[str, float] | None:
        """Prueft eine Kerze. Gibt (Grund, Ausstiegspreis) zurueck oder None.
        Treffen SL und TP in derselben Kerze, wird konservativ der SL angenommen."""
        hit_sl = low <= self.stop_loss if self.side > 0 else high >= self.stop_loss
        hit_tp = high >= self.take_profit if self.side > 0 else low <= self.take_profit
        if hit_sl:
            moved = abs(self.stop_loss - (self.entry - self.side * self.initial_risk)) > 1e-9
            return ("trail" if moved else "sl"), self.stop_loss
        if hit_tp:
            return "tp", self.take_profit
        return None

    def trail(self, high: float, low: float) -> None:
        """Nach einer Kerze ohne Ausstieg: Stop nachziehen (nur in Gewinnrichtung). Wirkt ab der naechsten Kerze."""
        if self.trail_dist <= 0:
            return
        if self.side > 0:
            self.stop_loss = max(self.stop_loss, high - self.trail_dist)
        else:
            self.stop_loss = min(self.stop_loss, low + self.trail_dist)


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
             point_value: float, open_positions: int, lot_step: float = 1.0) -> OrderPlan | None:
        """Erzeugt einen Order-Plan oder None, wenn der Trade gegen eine Regel verstoesst."""
        if self.halted or side == 0 or open_positions >= self.cfg.max_open_positions:
            return None
        if not atr_value or math.isnan(atr_value) or atr_value <= 0:
            return None
        trailing = self.cfg.exit_mode == "trailing"
        sl_dist = atr_value * (self.cfg.trail_atr_mult if trailing else self.cfg.sl_atr_mult)
        risk_money = equity * self.cfg.risk_per_trade_pct / 100
        # auf die kleinste handelbare Menge abrunden (Futures: 1 Kontrakt, Krypto z. B. 0.0001 BTC)
        contracts = math.floor(risk_money / (sl_dist * point_value) / lot_step + 1e-9) * lot_step
        contracts = round(contracts, 10)
        if contracts < lot_step:
            return None  # Konto zu klein fuer dieses Risiko: lieber kein Trade
        tp = side * math.inf if trailing else entry + side * sl_dist * self.cfg.reward_risk
        return OrderPlan(side, entry, entry - side * sl_dist, tp, contracts,
                         trail_dist=sl_dist if trailing else 0.0)
