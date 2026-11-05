from dataclasses import replace

import numpy as np
import pandas as pd

from tradingbot.backtest.engine import run_backtest
from tradingbot.config import load_config
from tradingbot.learning import research
from tradingbot.strategies.base import Strategy


class EveryN(Strategy):
    name = "alle_n"

    def __init__(self, n=50):
        super().__init__(n=n)

    def signals(self, df):
        s = np.zeros(len(df), dtype=int)
        s[30::self.params["n"]] = 1
        return pd.Series(s, index=df.index)


def _flat(n=400, step=0.0):
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    c = 100 + np.sin(np.arange(n) / 3) * 0.3 + np.arange(n) * step
    return pd.DataFrame({"open": c, "high": c + 0.2, "low": c - 0.2, "close": c, "volume": 1.0}, index=idx)


def _cfg():
    cfg = load_config("config/crypto-swing.yaml")
    cfg.risk.max_daily_loss_pct = 100.0
    return cfg


def test_time_exit_closes_after_max_bars():
    cfg = _cfg()
    cfg.risk = replace(cfg.risk, max_bars=5, sl_atr_mult=50, min_stop_cost_mult=0)
    trades, _ = run_backtest(_flat(), EveryN(), cfg, "BTCUSDT")
    assert trades and all(t.reason in ("time", "end") for t in trades)
    t = trades[0]
    assert (t.exit_time - t.entry_time) == pd.Timedelta(hours=4)


def test_cost_filter_skips_too_tight_stops():
    cfg = _cfg()
    cfg.risk = replace(cfg.risk, min_stop_cost_mult=5.0)  # Stop ~0,6 % < 5 x 0,09 % ? nein -> erlaubt
    allowed, _ = run_backtest(_flat(), EveryN(), cfg, "BTCUSDT")
    cfg.risk = replace(cfg.risk, min_stop_cost_mult=50.0)  # verlangt Stop >= 4,5 % -> alles zu eng
    blocked, _ = run_backtest(_flat(), EveryN(), cfg, "BTCUSDT")
    assert allowed and not blocked


def test_with_exit_changes_risk_and_repr():
    s = research.WithExit(EveryN(), rr=2.0, sl=1.0, mode="trailing", max_bars=24)
    r = s.risk(_cfg().risk)
    assert r.exit_mode == "trailing" and r.trail_atr_mult == 1.0 and r.max_bars == 24
    assert "Trailing" in repr(s) and "max 24" in repr(s) and s.params == {"n": 50}
