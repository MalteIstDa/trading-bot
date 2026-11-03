"""Basis fuer Strategien. Eine Strategie liefert nur Einstiegssignale.
SL und TP setzt immer der Risk Manager (RR 1:3), nicht die Strategie."""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    name: str = "base"

    def __init__(self, **params):
        self.params = params

    @abstractmethod
    def signals(self, df: pd.DataFrame) -> pd.Series:
        """Pro Bar: +1 = Long-Einstieg, -1 = Short-Einstieg, 0 = nichts.
        Das Signal gilt am Schluss der Bar, eingestiegen wird zur naechsten Eroeffnung."""

    def __repr__(self) -> str:
        p = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.name}({p})"


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def crossed(a: pd.Series, b: pd.Series) -> pd.Series:
    """+1 wenn a von unten durch b kreuzt, -1 von oben, sonst 0."""
    up = (a > b) & (a.shift(1) <= b.shift(1))
    down = (a < b) & (a.shift(1) >= b.shift(1))
    return up.astype(int) - down.astype(int)


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Trendstaerke (0-100). Ueber ~25 = Trendphase, unter ~20 = Seitwaertsphase."""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = atr(df, 1)
    a = 1 / period
    atr_s = tr.ewm(alpha=a).mean()
    plus_di = 100 * plus_dm.ewm(alpha=a).mean() / atr_s
    minus_di = 100 * minus_dm.ewm(alpha=a).mean() / atr_s
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    return dx.ewm(alpha=a).mean()
