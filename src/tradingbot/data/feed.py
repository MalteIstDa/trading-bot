"""Marktdaten: Yahoo Finance (kostenlos) mit lokalem CSV-Cache, plus synthetische Daten fuer Tests."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from tradingbot.config import ROOT

CACHE = ROOT / "data"
COLUMNS = ["open", "high", "low", "close", "volume"]


def load_history(symbol: str, interval: str = "5m", period: str = "60d", use_cache: bool = True) -> pd.DataFrame:
    """OHLCV-Daten laden. Gibt DataFrame mit Spalten open/high/low/close/volume zurueck."""
    cache_file = CACHE / f"{symbol.replace('=', '_')}_{interval}_{period}.csv"
    if use_cache and cache_file.exists():
        return pd.read_csv(cache_file, index_col=0, parse_dates=True)

    import yfinance as yf

    df = yf.download(symbol, interval=interval, period=period, progress=False, auto_adjust=False)
    if df.empty:
        raise RuntimeError(f"Keine Daten fuer {symbol} erhalten")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[COLUMNS].dropna()
    CACHE.mkdir(exist_ok=True)
    df.to_csv(cache_file)
    return df


def synthetic(n: int = 3000, seed: int = 0, start: float = 2000.0) -> pd.DataFrame:
    """Zufallsdaten mit Trendphasen, nur fuer Tests und Demos."""
    rng = np.random.default_rng(seed)
    drift = np.repeat(rng.normal(0, 0.0004, n // 200 + 1), 200)[:n]
    rets = drift + rng.normal(0, 0.002, n)
    close = start * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[start], close[:-1]])
    spread = np.abs(rng.normal(0, 0.0015, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    idx = pd.date_range("2026-01-01", periods=n, freq="5min")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": rng.integers(100, 1000, n)}, index=idx)
