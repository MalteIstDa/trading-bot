"""Momentum-, Volumen- und Oszillator-Strategien (vor allem fuer Krypto).
Jede Strategie liefert nur Einstiegssignale; SL/TP setzt der Risk Manager (RR 1:3).
Alle Indikatoren nutzen nur Kerzen bis zur aktuellen (kein Blick in die Zukunft)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingbot.strategies.base import Strategy, crossed


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / down.replace(0, np.nan))


def _first(cond: pd.Series) -> pd.Series:
    """Nur der Beginn einer Bedingung zaehlt als Signal (nicht jede Bar, in der sie gilt)."""
    return cond & ~cond.shift(1, fill_value=False)


class VolumeBreakout(Strategy):
    """Ausbruch ueber das N-Bar-Hoch/-Tief, aber nur mit deutlich erhoehtem Volumen (Bestaetigung)."""
    name = "volume_breakout"
    grid = {"lookback": [20, 50], "vol_mult": [1.5, 2.5]}

    def __init__(self, lookback: int = 20, vol_mult: float = 2.0):
        super().__init__(lookback=lookback, vol_mult=vol_mult)

    def signals(self, df):
        n, k = self.params["lookback"], self.params["vol_mult"]
        hi = df["high"].rolling(n).max().shift(1)
        lo = df["low"].rolling(n).min().shift(1)
        loud = df["volume"] > k * df["volume"].rolling(n).mean().shift(1)
        long = _first(df["close"] > hi) & loud
        short = _first(df["close"] < lo) & loud
        return long.astype(int) - short.astype(int)


class MomentumRoc(Strategy):
    """Momentum: Kursveraenderung ueber N Bars (Rate of Change) springt ueber eine Schwelle,
    nur in Richtung des langfristigen Trends (EMA 200)."""
    name = "momentum_roc"
    grid = {"period": [12, 48], "threshold": [2.0, 5.0]}

    def __init__(self, period: int = 24, threshold: float = 3.0):
        super().__init__(period=period, threshold=threshold)

    def signals(self, df):
        n, th = self.params["period"], self.params["threshold"]
        roc = df["close"].pct_change(n) * 100
        trend = df["close"] > ema(df["close"], 200)
        long = _first(roc > th) & trend
        short = _first(roc < -th) & ~trend
        return long.astype(int) - short.astype(int)


class RsiTrendPullback(Strategy):
    """Im Aufwaertstrend (Kurs ueber EMA 200) einen Ruecksetzer kaufen: RSI kommt von unter `low` zurueck.
    Im Abwaertstrend spiegelbildlich verkaufen."""
    name = "rsi_pullback"
    grid = {"low": [30, 40], "period": [7, 14]}

    def __init__(self, low: int = 35, period: int = 14):
        super().__init__(low=low, period=period)

    def signals(self, df):
        lo, n = self.params["low"], self.params["period"]
        r = rsi(df["close"], n)
        trend = df["close"] > ema(df["close"], 200)
        long = (r > lo) & (r.shift(1) <= lo) & trend
        short = (r < 100 - lo) & (r.shift(1) >= 100 - lo) & ~trend
        return long.astype(int) - short.astype(int)


class BollingerSqueeze(Strategy):
    """Nach einer ruhigen Phase (Bollinger-Baender ungewoehnlich eng) dem Ausbruch aus den Baendern folgen."""
    name = "bb_squeeze"
    grid = {"period": [20], "squeeze_q": [0.1, 0.2]}

    def __init__(self, period: int = 20, squeeze_q: float = 0.15):
        super().__init__(period=period, squeeze_q=squeeze_q)

    def signals(self, df):
        n, q = self.params["period"], self.params["squeeze_q"]
        mid = df["close"].rolling(n).mean()
        sd = df["close"].rolling(n).std()
        width = (4 * sd / mid)
        tight = width.shift(1) <= width.rolling(500, min_periods=100).quantile(q).shift(1)
        long = _first(df["close"] > mid + 2 * sd) & tight
        short = _first(df["close"] < mid - 2 * sd) & tight
        return long.astype(int) - short.astype(int)


class MacdTrend(Strategy):
    """MACD kreuzt seine Signallinie, nur in Richtung des Trends (EMA 200)."""
    name = "macd_trend"
    grid = {"fast": [12], "slow": [26], "signal": [9]}

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        super().__init__(fast=fast, slow=slow, signal=signal)

    def signals(self, df):
        p = self.params
        macd = ema(df["close"], p["fast"]) - ema(df["close"], p["slow"])
        sig = ema(macd, p["signal"])
        x = crossed(macd, sig)
        trend = df["close"] > ema(df["close"], 200)
        return ((x > 0) & trend).astype(int) - ((x < 0) & ~trend).astype(int)


class ObvTrend(Strategy):
    """Volumen-Trend: On-Balance-Volume kreuzt seinen Durchschnitt und der Kurs steht auf derselben Seite
    seines Durchschnitts (Kaeufer bzw. Verkaeufer uebernehmen)."""
    name = "obv_trend"
    grid = {"period": [20, 50]}

    def __init__(self, period: int = 20):
        super().__init__(period=period)

    def signals(self, df):
        n = self.params["period"]
        obv = (np.sign(df["close"].diff()).fillna(0) * df["volume"]).cumsum()
        x = crossed(obv, ema(obv, n))
        above = df["close"] > ema(df["close"], n)
        return ((x > 0) & above).astype(int) - ((x < 0) & ~above).astype(int)


class VolumeClimaxReversal(Strategy):
    """Gegenbewegung nach Ausverkauf/Kaufpanik: sehr hohes Volumen + grosse Kerze + RSI im Extrem,
    dann Einstieg in Gegenrichtung."""
    name = "volume_climax"
    grid = {"vol_mult": [3.0, 4.0]}

    def __init__(self, vol_mult: float = 3.0):
        super().__init__(vol_mult=vol_mult)

    def signals(self, df):
        k = self.params["vol_mult"]
        loud = df["volume"] > k * df["volume"].rolling(50).mean().shift(1)
        r = rsi(df["close"], 14)
        body = (df["close"] - df["open"]).abs()
        big = body > 2 * body.rolling(50).mean().shift(1)
        long = loud & big & (df["close"] < df["open"]) & (r < 25)
        short = loud & big & (df["close"] > df["open"]) & (r > 75)
        return long.astype(int) - short.astype(int)


MOMENTUM_STRATEGIES = [VolumeBreakout, MomentumRoc, RsiTrendPullback, BollingerSqueeze, MacdTrend, ObvTrend,
                       VolumeClimaxReversal]
