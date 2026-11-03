import json

import pandas as pd

from tradingbot import bot
from tradingbot.config import Config
from tradingbot.dashboard import server
from tradingbot.dashboard.stats import equity_stats, r_histogram, thin, trade_stats
from tradingbot.execution.paper import PaperBroker
from tradingbot.risk.manager import OrderPlan


def _trade(pnl, r, symbol="BTC", reason="tp", time="2026-11-01T10:00:00+00:00", **kw):
    return {"time": time, "symbol": symbol, "reason": reason, "pnl": str(pnl), "r_multiple": str(r), **kw}


def test_trade_stats_counts_everything():
    rows = [_trade(3, 3), _trade(-1, -1, "ETH", "sl"), _trade(-1, -1, "ETH", "sl", "2026-11-02T10:00:00+00:00"),
            _trade(2, 2, trader="0xabc", time="2026-11-02T11:00:00+00:00")]
    s = trade_stats(rows)
    assert s["trades"] == 4 and s["win_rate"] == 0.5
    assert s["profit_factor"] == 2.5 and s["expectancy_r"] == 0.75 and s["net_pnl"] == 3
    assert s["best"]["pnl"] == 3 and s["worst"]["symbol"] == "ETH"
    assert s["max_loss_streak"] == 2
    assert s["daily_pnl"] == [{"date": "2026-11-01", "pnl": 2.0}, {"date": "2026-11-02", "pnl": 1.0}]
    assert {g["key"]: g["trades"] for g in s["by_symbol"]} == {"BTC": 2, "ETH": 2}
    assert [g["key"] for g in s["by_trader"]] == ["0xabc"]


def test_trade_stats_handles_no_trades_and_bad_rows():
    s = trade_stats([{"pnl": ""}, {"pnl": "nan"}])
    assert s["trades"] == 0 and s["win_rate"] is None and s["best"] is None and s["profit_factor"] is None


def test_r_histogram_collects_outliers_at_the_edges():
    h = r_histogram([-9, -1.0, 0.1, 9])
    assert sum(b["count"] for b in h) == 4
    assert h[0]["count"] == 1 and h[-1]["count"] == 1


def test_equity_stats_measures_drawdown_from_peak():
    curve = [{"time": str(i), "equity": e} for i, e in enumerate([100, 120, 90, 110])]
    s = equity_stats(curve, 100)
    assert s["peak"] == 120 and s["max_drawdown"] == 30
    assert abs(s["max_drawdown_pct"] - 0.25) < 1e-9
    assert abs(s["current_drawdown_pct"] - 10 / 120) < 1e-9


def test_thin_keeps_last_point_and_limit():
    rows = list(range(10_000))
    out = thin(rows, 100)
    assert len(out) == 100 and out[-1] == 9999 and out[0] == 0
    assert thin([1, 2], 100) == [1, 2]


def _write_bot(folder):
    (folder / "traders").mkdir(parents=True)
    (folder / "dashboard.json").write_text(json.dumps({
        "updated": "2026-11-03T10:00:00+00:00", "start_capital": 20.0, "equity": 21.0, "markets": {},
        "traders": [{"address": "0xa", "note": "live"}],
        "events": [{"time": "2026-11-03T09:00:00+00:00", "type": "info", "text": "alt"},
                   {"time": "2026-11-03T10:00:00+00:00", "type": "copy", "text": "neu"}, "kaputt"]}))
    (folder / "paper_equity.csv").write_text("time,equity\n2026-11-03T09:00:00+00:00,20.00\n2026-11-03T10:00:00+00:00,21.00\n")
    (folder / "paper_trades.csv").write_text("time,symbol,pnl,r_multiple\n2026-11-03T10:00:00+00:00,BTC,1.0,1.0\n")
    (folder / "traders" / "selected.json").write_text(json.dumps(
        {"created": 1, "traders": [{"address": "0xa", "t_stat": 3.0}, {"address": "0xb", "t_stat": 2.0}]}))
    (folder / "traders" / "test_result.json").write_text(json.dumps({"total": {"trades": 1}, "windows": [], "passed": False}))
    (folder / "traders" / "copy_trades.csv").write_text("trader,r_multiple\n0xa,1.0\n0xa,-0.5\n0xc,2.0\n")


def test_build_state_merges_files(tmp_path, monkeypatch):
    _write_bot(tmp_path)
    monkeypatch.setattr(bot, "DATA", tmp_path)
    s = server.build_state()
    assert s["stats"]["trades"] == 1 and len(s["equity_curve"]) == 2
    assert [e["text"] for e in s["events"]] == ["neu", "alt"]
    a, b = s["copy"]["traders"]
    assert a["note"] == "live" and a["test_trades"] == 2 and a["test_expectancy_r"] == 0.25
    assert "test_trades" not in b  # im Test nicht vorgekommen: Das Dashboard warnt dann
    assert [t["address"] for t in s["copy"]["tested_only"]] == ["0xc"]
    assert s["copy"]["test"]["passed"] is False
    assert "traders" not in s


def test_build_state_without_any_files(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DATA", tmp_path)
    s = server.build_state()
    assert s["stats"]["trades"] == 0 and s["copy"] is None and s["events"] == []


def test_events_fall_back_to_closed_trades(tmp_path, monkeypatch):
    (tmp_path / "paper_trades.csv").write_text("time,symbol,reason,pnl\n2026-11-03T10:00:00+00:00,BTC,tp,1.5\n")
    monkeypatch.setattr(bot, "DATA", tmp_path)
    assert "BTC geschlossen (tp), +1.50 $" == server.build_state()["events"][0]["text"]


def test_unknown_bot_name_stays_in_own_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DATA", tmp_path)
    assert server.data_dir("../../etc") == tmp_path


def test_coin_and_address_validation():
    assert server.COIN_RE.match("BTC") and server.COIN_RE.match("xyz:SKHX") and server.COIN_RE.match("@107")
    assert not server.COIN_RE.match("BTC/../x") and not server.COIN_RE.match("")
    assert server.ADDRESS_RE.match("0x" + "a" * 40) and not server.ADDRESS_RE.match("0x123")


def test_write_dashboard_reports_open_pnl_and_extra(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DASHBOARD_JSON", tmp_path / "dashboard.json")
    monkeypatch.setattr(bot, "EQUITY_CSV", tmp_path / "equity.csv")
    idx = pd.date_range("2026-11-01", periods=3, freq="15min", tz="UTC")
    df = pd.DataFrame({"open": 100.0, "high": 112.0, "low": 99.0, "close": [100.0, 105.0, 110.0]}, index=idx)
    broker = PaperBroker(20.0, {})
    broker.place_bracket("BTC", OrderPlan(side=1, entry=100.0, stop_loss=90.0, take_profit=130.0, contracts=2, initial_risk=10.0))
    cfg = Config(markets=[{"symbol": "BTC", "name": "Bitcoin", "point_value": 1.0}])
    bot.write_dashboard(cfg, broker, {"BTC": "Kopie"}, {"BTC": df},
                        extra={"traders": [{"address": "0xa"}], "watchlist": {"ETH": df}, "events": []})
    s = json.loads((tmp_path / "dashboard.json").read_text())
    assert s["unrealized"] == 20.0 and s["markets"]["BTC"]["position"]["unrealized"] == 20.0
    assert s["traders"] == [{"address": "0xa"}]
    assert len(s["watchlist"]["ETH"]["candles"]) == 3
