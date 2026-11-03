from datetime import date

from tradingbot.config import RiskConfig
from tradingbot.risk.manager import RiskManager


def test_sl_tp_follow_rr_1_to_3():
    rm = RiskManager(RiskConfig(risk_per_trade_pct=1, reward_risk=3, sl_atr_mult=1.0))
    plan = rm.plan(side=1, entry=100, atr_value=2, equity=10_000, point_value=10, open_positions=0)
    assert plan.stop_loss == 98 and plan.take_profit == 106
    assert plan.contracts == 5  # 100 EUR Risiko / (2 Punkte * 10)


def test_short_levels_are_mirrored():
    rm = RiskManager(RiskConfig(sl_atr_mult=1.0))
    plan = rm.plan(-1, 100, 2, 10_000, 10, 0)
    assert plan.stop_loss == 102 and plan.take_profit == 94


def test_too_small_account_means_no_trade():
    rm = RiskManager(RiskConfig())
    assert rm.plan(1, 20000, 30, equity=20, point_value=2, open_positions=0) is None


def test_daily_loss_kill_switch():
    rm = RiskManager(RiskConfig(max_daily_loss_pct=3))
    rm.new_bar(date(2026, 1, 1), 10_000)
    rm.new_bar(date(2026, 1, 1), 9_600)
    assert rm.halted and rm.plan(1, 100, 2, 9_600, 10, 0) is None
    rm.new_bar(date(2026, 1, 2), 9_600)
    assert not rm.halted


def test_trailing_stop_follows_price_and_locks_profit():
    rm = RiskManager(RiskConfig(exit_mode="trailing", trail_atr_mult=2))
    plan = rm.plan(1, 100, 1, 10_000, 10, 0)
    assert plan.stop_loss == 98 and plan.take_profit == float("inf")
    assert plan.check_exit(high=101, low=99) is None
    plan.trail(high=110, low=105)       # Kurs steigt -> Stop auf 108
    assert plan.stop_loss == 108
    plan.trail(high=104, low=103)       # Kurs faellt -> Stop bleibt
    assert plan.stop_loss == 108
    assert plan.check_exit(high=109, low=107) == ("trail", 108)


def test_trailing_short():
    rm = RiskManager(RiskConfig(exit_mode="trailing", trail_atr_mult=2))
    plan = rm.plan(-1, 100, 1, 10_000, 10, 0)
    plan.trail(high=95, low=90)
    assert plan.stop_loss == 92
    assert plan.check_exit(high=93, low=91) == ("trail", 92)


def test_crypto_fractional_size():
    rm = RiskManager(RiskConfig(risk_per_trade_pct=2, sl_atr_mult=1.0))
    plan = rm.plan(1, 70_000, 500, equity=20, point_value=1, open_positions=0, lot_step=0.00001)
    assert plan is not None
    assert abs(plan.contracts - 0.0008) < 1e-12  # 0,40 Risiko / 500 Abstand = 0.0008 BTC


def test_percentage_fee():
    from tradingbot.config import Config
    cfg = Config(fee_per_trade=0, fee_pct=0.1)
    assert abs(cfg.fee(70_000, 0.001, 1) - 0.07) < 1e-12
