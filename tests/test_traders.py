import json

import numpy as np
import pandas as pd
import pytest

from tradingbot.config import Config, RiskConfig
from tradingbot.traders import analysis as an
from tradingbot.traders import hyperliquid as hl

DAY = 86_400_000
T0 = 1_780_000_000_000  # fester Startzeitpunkt (ms)


def fill(t, coin, side, sz, start, px, pnl=0.0):
    return {"time": t, "coin": coin, "side": side, "sz": str(sz), "startPosition": str(start),
            "px": str(px), "closedPnl": str(pnl), "fee": "0.1", "tid": t, "hash": "h", "oid": t}


def test_leaderboard_parsing(monkeypatch):
    raw = {"leaderboardRows": [{"ethAddress": "0xABC", "accountValue": "120000.5", "displayName": None,
                                "windowPerformances": [["day", {"pnl": "10", "roi": "0.01", "vlm": "5"}],
                                                       ["month", {"pnl": "5000", "roi": "0.05", "vlm": "900000"}],
                                                       ["allTime", {"pnl": "80000", "roi": "1.2", "vlm": "9e6"}]]}]}
    monkeypatch.setattr(hl, "_request", lambda *a, **k: raw)
    rows = hl.leaderboard()
    assert rows[0]["address"] == "0xabc" and rows[0]["perf"]["month"]["pnl"] == 5000
    assert an.prefilter(rows)[0]["turnover"] == pytest.approx(900000 / 120000.5)


def test_prefilter_drops_market_makers_and_losers():
    base = {"address": "x", "name": None, "account_value": 100_000}
    mm = {**base, "perf": {"month": {"pnl": 1, "vlm": 1e9}, "allTime": {"pnl": 1}}}
    loser = {**base, "perf": {"month": {"pnl": -1, "vlm": 1}, "allTime": {"pnl": 1}}}
    good = {**base, "perf": {"month": {"pnl": 1, "vlm": 1e6}, "allTime": {"pnl": 1}}}
    assert an.prefilter([mm, loser, good]) == [{**good, "turnover": 10.0}]


def test_signals_only_new_positions_and_flips():
    fills = [fill(T0, "BTC", "B", 1, 0, 100),          # neu long  -> Signal
             fill(T0 + 1, "BTC", "B", 1, 1, 101),      # nachkaufen -> kein Signal
             fill(T0 + 2, "BTC", "A", 2, 2, 105, 9),   # schliessen
             fill(T0 + 3, "ETH", "A", 1, 0, 50),       # neu short -> Signal
             fill(T0 + 4, "ETH", "B", 3, -1, 48, 2)]   # dreht auf long -> Signal + Close
    sig = an.open_signals(fills)
    assert [(s["coin"], s["side"]) for s in sig] == [("BTC", 1), ("ETH", -1), ("ETH", 1)]
    cl = an.close_times(fills)
    assert [c[:2] for c in cl["BTC"]] == [(T0 + 2, 1)] and [c[:2] for c in cl["ETH"]] == [(T0 + 4, -1)]


def _trending_candles(n=600, start=T0, up=True):
    idx = pd.to_datetime(start + np.arange(n) * 900_000, unit="ms", utc=True)
    close = 100 * np.exp(np.cumsum(np.full(n, 0.002 if up else -0.002)))
    return pd.DataFrame({"open": close / 1.001, "high": close * 1.002, "low": close / 1.003,
                         "close": close, "volume": 1.0}, index=idx)


def test_copy_simulation_profits_when_trader_is_right():
    cfg = Config(fee_pct=0.045, risk=RiskConfig())
    sig = [{"time": T0 + 100 * 900_000, "coin": "BTC", "side": 1, "px": 120.0}]
    trades = an.simulate_copy(sig, {}, {"BTC": _trending_candles()}, cfg, "0xabc")
    assert len(trades) == 1 and trades[0].reason == "tp" and trades[0].r_multiple > 2.5


def test_score_rewards_consistency():
    steady = [fill(T0 + d * DAY + 1, "BTC", "A", 1, 1, 100, 100) for d in range(30)]
    lucky = [fill(T0 + 5 * DAY, "BTC", "A", 1, 1, 100, 3000)]
    s1 = an.score_trader("a", steady, T0, T0 + 30 * DAY)
    s2 = an.score_trader("b", lucky, T0, T0 + 30 * DAY)
    assert s1.t_stat > 2 > s2.t_stat
    assert s1.score > s2.score


def test_walk_forward_end_to_end(monkeypatch, tmp_path):
    from tradingbot import bot
    from tradingbot.traders import runner
    monkeypatch.setattr(bot, "DATA", tmp_path)
    now = T0 + 80 * DAY
    monkeypatch.setattr(runner, "_now_ms", lambda: now)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    rows = [{"address": "0xgood", "name": None, "account_value": 1e6,
             "perf": {"month": {"pnl": 1e5, "vlm": 1e6}, "allTime": {"pnl": 1e6}}}]
    monkeypatch.setattr(hl, "leaderboard", lambda: rows)
    # Trader eroeffnet jeden Tag long und schliesst mit Gewinn -> konstant
    fl = []
    for d in range(80):
        t = T0 + d * DAY
        fl += [fill(t, "BTC", "B", 1, 0, 100), fill(t + DAY // 2, "BTC", "A", 1, 1, 101, 50)]
    monkeypatch.setattr(hl, "fills", lambda addr, s, e, cache_dir=None: [f for f in fl if s <= f["time"] <= e])
    monkeypatch.setattr(hl, "candles", lambda coin, iv, s, e=None: _trending_candles(8000, s))
    res = runner.test(Config(fee_pct=0.045), train_days=30, test_days=10, windows=5)
    assert res["total"]["trades"] > 0
    assert json.loads((tmp_path / "traders" / "test_result.json").read_text())["windows"]


def test_watch_copies_and_follows_exit(monkeypatch, tmp_path):
    from tradingbot import bot
    from tradingbot.traders import runner
    for name, f in [("DATA", tmp_path), ("STATE_FILE", tmp_path / "s.json"), ("TRADES_CSV", tmp_path / "t.csv"),
                    ("EQUITY_CSV", tmp_path / "e.csv"), ("DASHBOARD_JSON", tmp_path / "d.json")]:
        monkeypatch.setattr(bot, name, f)
    (tmp_path / "traders").mkdir()
    (tmp_path / "traders" / "selected.json").write_text(json.dumps({"traders": [{"address": "0xgood"}]}))
    snapshots = iter([{}, {"BTC": {"size": 1.0, "entry": 100}}, {"BTC": {"size": 1.0, "entry": 100}}, {}])
    monkeypatch.setattr(hl, "positions", lambda a: next(snapshots))
    monkeypatch.setattr(hl, "size_decimals", lambda: {"BTC": 5})
    monkeypatch.setattr(hl, "candles", lambda coin, iv, s, e=None: _trending_candles(300, T0)
                        if iv != "1m" else _trending_candles(1, T0))
    polls = {"n": 0}

    class Stop(Exception):
        pass

    def fake_sleep(sec):
        if sec == 60:
            polls["n"] += 1
            if polls["n"] >= 4:
                raise Stop

    monkeypatch.setattr(runner.time, "sleep", fake_sleep)
    cfg = Config(start_capital=20, fee_per_trade=0, fee_pct=0.045,
                 risk=RiskConfig(risk_per_trade_pct=2, max_open_positions=2))
    with pytest.raises(Stop):
        runner.watch(cfg, poll_seconds=60)
    rows = (tmp_path / "t.csv").read_text().splitlines()
    assert len(rows) == 2 and "trader_exit" in rows[1]
    assert json.loads((tmp_path / "d.json").read_text())["equity"] > 0


@pytest.mark.parametrize("newest_first", [True, False])
def test_fills_pagination_both_directions(monkeypatch, newest_first):
    all_fills = [fill(T0 + i * 1000, "BTC", "B", 1, 0, 100) for i in range(1700)]

    def fake_post(payload):
        sel = [f for f in all_fills if payload["startTime"] <= f["time"] <= payload["endTime"]]
        return sel[-500:] if newest_first else sel[:500]

    monkeypatch.setattr(hl, "post", fake_post)
    monkeypatch.setattr(hl.time, "sleep", lambda s: None)
    got = hl.fills("0xabc", T0, T0 + 2_000_000)
    assert len(got) == 1700 and got[0]["time"] == T0


def test_partial_fills_of_one_order_are_one_signal_and_spot_is_ignored():
    # grosse Order in 3 Teil-Fills: startPosition bleibt bei 0 (so liefert es die API bei Spot/Teil-Fills)
    fl = [dict(fill(T0 + i, "BTC", "B", 1, 0, 100), oid=7) for i in range(3)]
    fl += [fill(T0 + 10, "@107", "B", 5, 0, 30), fill(T0 + 11, "xyz:GOLD", "B", 1, 0, 2000)]
    sig = an.open_signals(fl)
    assert [(s["coin"], s["side"]) for s in sig] == [("BTC", 1)]
    assert an.spot_share(fl) > 0.2


def test_copy_never_uses_prices_before_entry():
    cfg = Config(fee_pct=0.045, risk=RiskConfig())
    df = _trending_candles(up=False)
    sig = [{"time": T0 + 100 * 900_000 + 1, "coin": "BTC", "side": 1, "px": float(df["close"].iloc[100])}]
    tr = an.simulate_copy(sig, {}, {"BTC": df}, cfg, "0xabc")
    assert tr and pd.Timestamp(tr[0].entry_time) > df.index[100]
