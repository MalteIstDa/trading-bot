from tradingbot.backtest.engine import run_backtest
from tradingbot.backtest.metrics import breakeven_win_rate, summarize
from tradingbot.config import Config
from tradingbot.data.feed import synthetic
from tradingbot.learning.selector import walk_forward
from tradingbot.strategies import all_candidates


def test_every_strategy_runs_and_respects_rr():
    df, cfg = synthetic(1500), Config()
    for strat in all_candidates():
        trades, curve = run_backtest(df, strat, cfg)
        assert len(curve) == len(df) - 1
        for t in trades:
            if t.reason == "tp":
                assert abs(abs(t.take_profit - t.entry) / abs(t.stop_loss - t.entry) - 3) < 1e-9


def test_breakeven():
    assert breakeven_win_rate(3) == 0.25


def test_walk_forward_only_reports_unseen_data():
    df, cfg = synthetic(3000), Config()
    res = walk_forward(df, cfg, "TEST")
    first_test_start = df.index[cfg.selection.train_bars]
    assert all(t.entry_time >= first_test_start for t in res["trades"])
    assert "trades" in summarize(res["trades"])
