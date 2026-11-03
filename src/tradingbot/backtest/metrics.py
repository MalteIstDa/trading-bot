"""Kennzahlen. Wichtigste Zahl ist die Expectancy (Erwartungswert pro Trade in R), nicht die Win Rate."""
from __future__ import annotations

import pandas as pd


def summarize(trades: list, curve: pd.Series | None = None) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "expectancy_r": 0.0,
                "net_pnl": 0.0, "max_drawdown_pct": 0.0}
    pnl = [t.pnl for t in trades]
    wins = [p for p in pnl if p > 0]
    losses = [-p for p in pnl if p <= 0]
    out = {
        "trades": n,
        "win_rate": len(wins) / n,
        "profit_factor": (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else float("inf"),
        "expectancy_r": sum(t.r_multiple for t in trades) / n,
        "net_pnl": sum(pnl),
        "max_drawdown_pct": 0.0,
    }
    if curve is not None and len(curve):
        dd = curve / curve.cummax() - 1
        out["max_drawdown_pct"] = float(-dd.min() * 100)
    return out


def breakeven_win_rate(reward_risk: float) -> float:
    """Ab welcher Trefferquote eine Strategie mit diesem RR (vor Gebuehren) Gewinn macht."""
    return 1 / (1 + reward_risk)
