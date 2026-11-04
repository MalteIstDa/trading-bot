import numpy as np
import pandas as pd

from tradingbot.config import load_config
from tradingbot.data.feed import synthetic
from tradingbot.learning import research
from tradingbot.strategies.ict import IctSweepFvg, SilverBullet, TjrSweepBos, _swings
from tradingbot.strategies.momentum import MOMENTUM_STRATEGIES


def _day(events: dict[str, tuple]) -> pd.DataFrame:
    """Ein Handelstag in 5m-Kerzen (NY-Zeit), ruhig um 100, plus gezielte Kerzen {"HH:MM": (o, h, l, c)}."""
    idx = pd.date_range("2026-03-02 18:00", "2026-03-03 12:00", freq="5min", tz="America/New_York")
    rows = []
    for i, ts in enumerate(idx):
        wig = 0.1 if i % 2 else 0.08
        rows.append(events.get(ts.strftime("%H:%M"), (100.0, 100.0 + wig, 100.0 - wig, 100.0)))
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx.tz_convert("UTC"))
    df["volume"] = 100.0
    return df


SWEEP_DOWN_MOVE = {"09:00": (100.0, 100.6, 99.95, 100.3),   # ueber das Asia-Hoch (100.1) -> Sweep
                   "09:05": (100.3, 100.35, 99.95, 100.0),  # zurueck unter das Level
                   "09:10": (100.0, 100.0, 99.5, 99.6),     # Schluss unter dem letzten Swing-Tief -> Strukturbruch
                   "09:15": (99.6, 99.65, 99.2, 99.3),      # Luecke zu 09:05 (Tief 99.95) -> bearishe FVG
                   "09:20": (99.3, 99.8, 99.25, 99.5)}      # Ruecklauf in die FVG -> Short-Einstieg


def _when(sig: pd.Series):
    hits = sig[sig != 0]
    return [(ts.tz_convert("America/New_York").strftime("%H:%M"), int(v)) for ts, v in hits.items()]


def test_sweep_mss_entry_after_break_of_structure():
    df = _day(SWEEP_DOWN_MOVE)
    sig = IctSweepFvg(levels="asia", killzone="ny", entry="mss").signals(df)
    assert _when(sig) == [("09:10", -1)]


def test_sweep_fvg_entry_on_retrace():
    df = _day(SWEEP_DOWN_MOVE)
    assert _when(IctSweepFvg(levels="asia", killzone="ny", entry="fvg").signals(df)) == [("09:20", -1)]
    assert _when(TjrSweepBos(entry="fvg").signals(df)) == [("09:20", -1)]


def test_no_signal_outside_killzone():
    moved = {k.replace("09:", "12:"): v for k, v in SWEEP_DOWN_MOVE.items()}
    assert _when(IctSweepFvg(levels="asia", killzone="ny", entry="mss").signals(_day(moved))) == []


def test_swings_have_no_lookahead():
    df = synthetic(300, 3)
    full = _swings(df, 2)[0]
    part = _swings(df.iloc[:150], 2)[0]
    assert np.allclose(full[:150], part, equal_nan=True)


def test_signals_do_not_change_when_future_is_cut():
    df = synthetic(4000, 5).tz_localize("UTC")
    for strat in research.candidates("all"):
        a = strat.signals(df).iloc[:2500].to_numpy()
        b = strat.signals(df.iloc[:2500]).to_numpy()
        assert (a == b).all(), repr(strat)


def test_silver_bullet_and_momentum_produce_signals():
    df = synthetic(8000, 7).tz_localize("UTC")
    assert any((SilverBullet(window=w).signals(df) != 0).any() for w in ("london", "am", "pm"))
    for cls in MOMENTUM_STRATEGIES:
        assert set(np.unique(cls().signals(df))) <= {-1, 0, 1}


def test_research_round_writes_results(tmp_path):
    cfg = load_config()
    cfg.markets = cfg.markets[:2]
    fetch = {m["symbol"]: (lambda s=i: synthetic(6000, s).tz_localize("UTC")) for i, m in enumerate(cfg.markets)}
    res = research.run(cfg, "ict", tmp_path, rounds=1, fetch=fetch, min_trades=3)
    assert (tmp_path / "research" / "latest.json").exists()
    assert res["candidates"] == len(research.candidates("ict")) and res["leaderboard"]
    assert (tmp_path / "archive").exists()
