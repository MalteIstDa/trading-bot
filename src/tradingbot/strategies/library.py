"""Startbibliothek einfacher Strategien. Neue Strategien hier hinzufuegen und in STRATEGIES eintragen."""
from __future__ import annotations

import itertools

import pandas as pd

from tradingbot.strategies.base import Strategy, crossed


class EmaCross(Strategy):
    """Trendfolge: schnelle EMA kreuzt langsame EMA, nur in Richtung der Trend-EMA."""
    name = "ema_cross"
    grid = {"fast": [9, 20], "slow": [50, 100], "trend": [200]}

    def signals(self, df: pd.DataFrame) -> pd.Series:
        c = df["close"]
        fast = c.ewm(span=self.params["fast"]).mean()
        slow = c.ewm(span=self.params["slow"]).mean()
        trend = c.ewm(span=self.params["trend"]).mean()
        sig = crossed(fast, slow)
        sig[(sig > 0) & (c < trend)] = 0
        sig[(sig < 0) & (c > trend)] = 0
        return sig


class Breakout(Strategy):
    """Ausbruch ueber Hoch/Tief der letzten N Bars (Donchian)."""
    name = "breakout"
    grid = {"lookback": [20, 50, 100]}

    def signals(self, df: pd.DataFrame) -> pd.Series:
        n = self.params["lookback"]
        hi = df["high"].rolling(n).max().shift(1)
        lo = df["low"].rolling(n).min().shift(1)
        sig = pd.Series(0, index=df.index)
        sig[df["close"] > hi] = 1
        sig[df["close"] < lo] = -1
        return sig.where(sig != sig.shift(1), 0)  # nur erstes Signal einer Serie


class RsiReversion(Strategy):
    """Rueckkehr zur Mitte: RSI verlaesst ueberverkauft/ueberkauft-Zone."""
    name = "rsi_reversion"
    grid = {"period": [7, 14], "level": [20, 30]}

    def signals(self, df: pd.DataFrame) -> pd.Series:
        p, lvl = self.params["period"], self.params["level"]
        d = df["close"].diff()
        gain = d.clip(lower=0).ewm(alpha=1 / p).mean()
        loss = (-d.clip(upper=0)).ewm(alpha=1 / p).mean()
        rsi = 100 - 100 / (1 + gain / loss)
        sig = pd.Series(0, index=df.index)
        sig[(rsi > lvl) & (rsi.shift(1) <= lvl)] = 1
        sig[(rsi < 100 - lvl) & (rsi.shift(1) >= 100 - lvl)] = -1
        return sig


STRATEGIES: list[type[Strategy]] = [EmaCross, Breakout, RsiReversion]


def all_candidates() -> list[Strategy]:
    """Alle Strategien mit allen Parameterkombinationen aus ihrem grid."""
    out = []
    for cls in STRATEGIES:
        keys = list(cls.grid)
        for values in itertools.product(*(cls.grid[k] for k in keys)):
            out.append(cls(**dict(zip(keys, values))))
    return out
