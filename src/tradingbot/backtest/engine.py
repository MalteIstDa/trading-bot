"""Bar-basierter Backtester. Nutzt denselben Risk Manager wie das Paper/Live Trading."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Callable

import pandas as pd

from tradingbot.config import Config
from tradingbot.risk.manager import RiskManager
from tradingbot.strategies.base import Strategy, atr


@dataclass
class Trade:
    symbol: str
    strategy: str
    side: int
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry: float
    exit: float
    stop_loss: float
    take_profit: float
    contracts: float
    pnl: float
    r_multiple: float     # Ergebnis in Vielfachen des Risikos (Verlust = -1R, Ziel = +3R)
    reason: str           # "tp", "sl", "trail" oder "end"
    hour: int
    atr_pct: float        # Volatilitaet beim Einstieg

    def to_dict(self) -> dict:
        return asdict(self)


# Filter entscheidet anhand (Zeitpunkt, ATR in %) ob ein Signal genommen wird; kommt aus learning/filters.py
TradeFilter = Callable[[pd.Timestamp, float], bool]


def run_backtest(df: pd.DataFrame, strategy: Strategy, cfg: Config, symbol: str = "TEST",
                 trade_filter: TradeFilter | None = None, tick: float = 0.0) -> tuple[list[Trade], pd.Series]:
    point_value = cfg.market(symbol)["point_value"]
    lot_step = cfg.market(symbol).get("lot_step", 1.0)
    rm = RiskManager(cfg.risk)
    sig = strategy.signals(df).fillna(0).astype(int).to_numpy()
    a = atr(df, cfg.risk.atr_period).to_numpy()
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    idx = df.index
    slip = tick * cfg.slippage_ticks

    equity = cfg.start_capital
    curve = []
    trades: list[Trade] = []
    pos = None  # (plan, entry_i, atr_pct)

    for i in range(1, len(df)):
        rm.new_bar(idx[i].date(), equity)
        # 1) Einstieg zur Eroeffnung, falls Signal auf vorheriger Bar
        if pos is None and sig[i - 1] != 0:
            atr_pct = a[i - 1] / c[i - 1] * 100 if c[i - 1] else 0.0
            if trade_filter is None or trade_filter(idx[i], atr_pct):
                entry = o[i] + sig[i - 1] * slip
                plan = rm.plan(sig[i - 1], entry, a[i - 1], equity, point_value, 0, lot_step)
                if plan:
                    pos = (plan, i, atr_pct)
        # 2) SL/TP pruefen (trifft beides in einer Bar: konservativ SL annehmen)
        if pos is not None:
            plan, ei, atr_pct = pos
            res = plan.check_exit(h[i], l[i])
            last = i == len(df) - 1
            if res or last:
                reason, exit_px = res if res else ("end", c[i])
                exit_px -= plan.side * slip
                pnl = ((exit_px - plan.entry) * plan.side * plan.contracts * point_value
                       - cfg.fee(plan.entry, plan.contracts, point_value) - cfg.fee(exit_px, plan.contracts, point_value))
                risk_money = plan.initial_risk * plan.contracts * point_value
                equity += pnl
                trades.append(Trade(symbol, repr(strategy), plan.side, idx[ei], idx[i], plan.entry, exit_px,
                                    plan.entry - plan.side * plan.initial_risk, plan.take_profit, plan.contracts, pnl,
                                    pnl / risk_money if risk_money else 0.0, reason, idx[ei].hour, atr_pct))
                pos = None
            else:
                plan.trail(h[i], l[i])
        curve.append(equity)
    return trades, pd.Series(curve, index=idx[1:])
