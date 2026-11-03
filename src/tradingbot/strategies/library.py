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


def _ny(df: pd.DataFrame):
    """Zeitstempel in New Yorker Zeit: (Handelstag, Minute des Tages)."""
    idx = df.index if df.index.tz is not None else df.index.tz_localize("UTC")
    ny = idx.tz_convert("America/New_York")
    return pd.Series(ny.date, index=df.index), pd.Series(ny.hour * 60 + ny.minute, index=df.index)


US_OPEN = 9 * 60 + 30   # 9:30 New York = 15:30 deutsche Zeit
US_NOON = 12 * 60


class OpeningRangeBreakout(Strategy):
    """Ausbruch aus der Spanne der ersten Minuten nach US-Boersenoeffnung.
    Maximal ein Trade pro Tag, nur bis Mittag New York."""
    name = "us_open_breakout"
    grid = {"minutes": [15, 30, 60]}

    def signals(self, df: pd.DataFrame) -> pd.Series:
        day, minute = _ny(df)
        in_range = (minute >= US_OPEN) & (minute < US_OPEN + self.params["minutes"])
        after = (minute >= US_OPEN + self.params["minutes"]) & (minute < US_NOON)
        hi = df["high"].where(in_range).groupby(day).transform("max")
        lo = df["low"].where(in_range).groupby(day).transform("min")
        raw = pd.Series(0, index=df.index)
        raw[after & (df["close"] > hi)] = 1
        raw[after & (df["close"] < lo)] = -1
        first = (raw != 0).astype(int).groupby(day).cumsum() == 1  # nur das erste Signal des Tages
        return raw.where(first & (raw != 0), 0)


class VwapTrend(Strategy):
    """Kurs kreuzt den Tages-VWAP (volumengewichteter Durchschnittspreis) in Richtung des Trends,
    nur waehrend der US-Handelszeit."""
    name = "vwap_trend"
    grid = {"trend": [50, 200]}

    def signals(self, df: pd.DataFrame) -> pd.Series:
        day, minute = _ny(df)
        typical = (df["high"] + df["low"] + df["close"]) / 3
        vol = df["volume"].replace(0, 1)
        vwap = (typical * vol).groupby(day).cumsum() / vol.groupby(day).cumsum()
        trend = df["close"].ewm(span=self.params["trend"]).mean()
        sig = crossed(df["close"], vwap)
        session = (minute >= US_OPEN + 15) & (minute < 15 * 60 + 30)
        sig[~session] = 0
        sig[(sig > 0) & (df["close"] < trend)] = 0
        sig[(sig < 0) & (df["close"] > trend)] = 0
        return sig


STRATEGIES: list[type[Strategy]] = [EmaCross, Breakout, RsiReversion, OpeningRangeBreakout, VwapTrend]


def all_candidates() -> list[Strategy]:
    """Alle Strategien mit allen Parameterkombinationen aus ihrem grid."""
    out = []
    for cls in STRATEGIES:
        keys = list(cls.grid)
        for values in itertools.product(*(cls.grid[k] for k in keys)):
            out.append(cls(**dict(zip(keys, values))))
    return out
