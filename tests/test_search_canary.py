"""Gegenprobe: Findet die Suche einen echten Vorteil, und laesst sie reinen Zufall durchfallen?
Ohne diese Probe waere jedes "nicht bestanden" wertlos (die Pruefung koennte einfach alles ablehnen)."""
import numpy as np
import pandas as pd

from tradingbot.config import load_config
from tradingbot.learning import research
from tradingbot.strategies.base import Strategy
from test_ict import synthetic


class Canary(Strategy):
    """Schummelt absichtlich: kennt den Kurs in `horizon` Kerzen. Hat damit einen echten, grossen Vorteil."""
    name = "kanarienvogel"
    grid = {"horizon": [6, 12], "every": [40, 80]}

    def __init__(self, horizon=6, every=40):
        super().__init__(horizon=horizon, every=every)

    def signals(self, df):
        fut = df["close"].shift(-self.params["horizon"]) - df["open"].shift(-1)
        sig = np.sign(fut).fillna(0).astype(int)
        keep = np.arange(len(df)) % self.params["every"] == 0
        return pd.Series(np.where(keep, sig, 0), index=df.index)


class Noise(Strategy):
    """Reiner Zufall ohne jeden Vorteil."""
    name = "zufallsrauschen"
    grid = {"seed": [1, 2], "every": [40, 80]}

    def __init__(self, seed=1, every=40):
        super().__init__(seed=seed, every=every)

    def signals(self, df):
        rng = np.random.default_rng(self.params["seed"])
        sig = rng.choice([-1, 1], len(df))
        keep = np.arange(len(df)) % self.params["every"] == 0
        return pd.Series(np.where(keep, sig, 0), index=df.index)


def _run(monkeypatch, tmp_path, cls):
    monkeypatch.setattr(research, "families", lambda: {"probe": [cls]})
    cfg = load_config()
    cfg.markets = cfg.markets[:2]
    cfg.risk.reward_risk, cfg.risk.max_daily_loss_pct = 2.0, 100.0
    frames = {m["symbol"]: synthetic(12000, 20 + i).tz_localize("UTC") for i, m in enumerate(cfg.markets)}
    return research.explore(frames, cfg, "probe", tmp_path / f"{cls.name}.json", n_new=4, min_trades=10, seed=3)


def test_search_finds_real_edge(monkeypatch, tmp_path):
    res = _run(monkeypatch, tmp_path, Canary)
    assert res["best"] and res["passed"], res


def test_search_rejects_noise(monkeypatch, tmp_path):
    res = _run(monkeypatch, tmp_path, Noise)
    assert not res["passed"], res
