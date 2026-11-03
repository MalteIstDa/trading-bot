"""Automatische Strategie-Auswahl per Walk-Forward:
Fenster fuer Fenster wird auf den Trainingsdaten die beste Strategie (inkl. gelerntem Filter) gewaehlt
und danach auf ungesehenen Testdaten gehandelt. Nur das Out-of-Sample-Ergebnis zaehlt."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from tradingbot.backtest.engine import run_backtest
from tradingbot.backtest.metrics import summarize
from tradingbot.config import Config
from tradingbot.learning.filters import LearnedFilter, learn_filter
from tradingbot.strategies import Strategy, all_candidates


@dataclass
class Choice:
    strategy: Strategy
    filter: LearnedFilter
    train_stats: dict


def passes(stats: dict, cfg: Config) -> bool:
    s = cfg.selection
    return (stats["trades"] >= s.min_trades and stats["profit_factor"] >= s.min_profit_factor
            and stats["win_rate"] >= s.min_win_rate and stats["expectancy_r"] > 0)


def pick_best(train: pd.DataFrame, cfg: Config, symbol: str) -> Choice | None:
    best: Choice | None = None
    for strat in all_candidates():
        raw_trades, _ = run_backtest(train, strat, cfg, symbol)
        filt = learn_filter(raw_trades)
        trades, curve = run_backtest(train, strat, cfg, symbol, trade_filter=filt)
        stats = summarize(trades, curve)
        if passes(stats, cfg) and (best is None or stats["expectancy_r"] > best.train_stats["expectancy_r"]):
            best = Choice(strat, filt, stats)
    return best


def walk_forward(df: pd.DataFrame, cfg: Config, symbol: str) -> dict:
    s = cfg.selection
    oos_trades, windows = [], []
    start = 0
    while start + s.train_bars + s.test_bars <= len(df):
        train = df.iloc[start:start + s.train_bars]
        test = df.iloc[start + s.train_bars:start + s.train_bars + s.test_bars]
        choice = pick_best(train, cfg, symbol)
        if choice:
            trades, _ = run_backtest(test, choice.strategy, cfg, symbol, trade_filter=choice.filter)
            oos_trades += trades
            windows.append({"from": str(test.index[0]), "strategy": repr(choice.strategy),
                            "filter": choice.filter.describe(), "train": choice.train_stats,
                            "test": summarize(trades)})
        else:
            windows.append({"from": str(test.index[0]), "strategy": None})
        start += s.test_bars
    return {"windows": windows, "out_of_sample": summarize(oos_trades), "trades": oos_trades}
