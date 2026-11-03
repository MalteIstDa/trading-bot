import pytest

from tradingbot import bot
from tradingbot.config import Config
from tradingbot.data.feed import synthetic
from tradingbot.execution.paper import PaperBroker
from tradingbot.risk.manager import OrderPlan


class Stop(Exception):
    pass


def test_paper_loop_trades_and_persists(tmp_path, monkeypatch):
    full = synthetic(1500, seed=3)
    state = {"n": 400}
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(bot, "TRADES_CSV", tmp_path / "trades.csv")
    monkeypatch.setattr(bot, "load_history", lambda *a, **k: full.iloc[: state["n"]])

    def fake_sleep(_):
        state["n"] += 60  # simuliert: zwischen zwei Abfragen sind 60 neue Kerzen entstanden (Ruhezustand)
        if state["n"] > len(full):
            raise Stop

    monkeypatch.setattr(bot.time, "sleep", fake_sleep)
    cfg = Config(markets=[{"symbol": "GC=F", "name": "Gold", "point_value": 10.0}])
    with pytest.raises(Stop):
        bot.run_paper(cfg, 0, {"GC=F": "breakout(lookback=20)"})
    assert (tmp_path / "state.json").exists()
    assert (tmp_path / "trades.csv").exists()


def test_paper_state_survives_restart(tmp_path):
    f = tmp_path / "s.json"
    b = PaperBroker(1000, {"X": 1.0}, state_file=f)
    b.place_bracket("X", OrderPlan(1, 100, 99, 103, 1))
    b2 = PaperBroker(1000, {"X": 1.0}, state_file=f)
    assert "X" in b2.open_positions()
    closed = b2.on_price("X", 104, 100, 103.5)
    assert closed[0]["reason"] == "tp"
    assert PaperBroker(0, {}, state_file=f).equity() == pytest.approx(1000 + 3 - 2)
