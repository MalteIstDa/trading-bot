"""Kommandozeile: tradingbot backtest | select | paper"""
from __future__ import annotations

import argparse
import json
import logging

from tradingbot.backtest.engine import run_backtest
from tradingbot.backtest.metrics import breakeven_win_rate, summarize
from tradingbot.config import load_config
from tradingbot.data.feed import load_history, max_period, synthetic
from tradingbot.learning import journal
from tradingbot.learning.selector import walk_forward


def _data(args, cfg, symbol):
    return synthetic() if args.synthetic else load_history(symbol, cfg.timeframe, cfg.history_period)




def _fmt(stats: dict) -> str:
    return (f"Trades {stats['trades']:4d} | WR {stats['win_rate']:.0%} | PF {stats['profit_factor']:.2f} | "
            f"Exp {stats['expectancy_r']:+.2f}R | PnL {stats['net_pnl']:+.0f} | MaxDD {stats['max_drawdown_pct']:.1f}%")


def cmd_backtest(args, cfg):
    from tradingbot.strategies import all_candidates
    for m in cfg.markets:
        df = _data(args, cfg, m["symbol"])
        print(f"\n== {m['name']} ({m['symbol']}), {len(df)} Bars ==")
        for strat in all_candidates():
            trades, curve = run_backtest(df, strat, cfg, m["symbol"])
            print(f"{repr(strat):40s} {_fmt(summarize(trades, curve))}")
    print(f"\nHinweis: Bei RR 1:{cfg.risk.reward_risk:g} liegt die Gewinnschwelle bei "
          f"{breakeven_win_rate(cfg.risk.reward_risk):.0%} Trefferquote (vor Gebuehren).")


def cmd_select(args, cfg):
    selection = {}
    for m in cfg.markets:
        df = _data(args, cfg, m["symbol"])
        res = walk_forward(df, cfg, m["symbol"])
        print(f"\n== {m['name']} ({m['symbol']}) Walk-Forward ==")
        for w in res["windows"]:
            print(f"  ab {w['from'][:16]}: {w['strategy'] or 'keine Strategie besteht die Kriterien'}")
        print(f"  Out-of-Sample: {_fmt(res['out_of_sample'])}")
        print(f"  Strategiewechsel: {res['switches']} von {len(res['windows'])} Fenstern")
        journal.save(res["trades"], source="walk_forward")
        oos, latest = res["out_of_sample"], res["windows"][-1] if res["windows"] else {}
        if oos["trades"] < cfg.selection.min_trades:
            print(f"  -> nicht ausgewaehlt: nur {oos['trades']} Out-of-Sample-Trades "
                  f"(mind. {cfg.selection.min_trades} noetig, sonst ist es Zufall)")
        elif oos["expectancy_r"] <= 0:
            print("  -> nicht ausgewaehlt: Out-of-Sample nicht im Plus")
        elif not latest.get("strategy"):
            print("  -> nicht ausgewaehlt: im aktuellsten Fenster besteht keine Strategie")
        else:
            selection[m["symbol"]] = latest["strategy"]
    from tradingbot.bot import SELECTION_FILE
    SELECTION_FILE.write_text(json.dumps({"timeframe": cfg.timeframe, "strategies": selection}, indent=2))
    print(f"\nAusgewaehlt fuer Paper Trading: {selection or 'nichts (kein Markt hat out-of-sample bestanden)'}")


def cmd_paper(args, cfg):
    from tradingbot.bot import run_paper
    strategies = None
    if args.strategy:
        symbols = args.symbols or [m["symbol"] for m in cfg.markets]
        strategies = {s: args.strategy for s in symbols}
        print("TESTLAUF: Strategie manuell gewaehlt, sie hat die Pruefung nicht bestanden. Kein Gewinn zu erwarten.")
    run_paper(cfg, args.poll, strategies)


def main(argv=None):
    p = argparse.ArgumentParser(prog="tradingbot")
    p.add_argument("--config", help="Pfad zur config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("backtest", "select"):
        sp = sub.add_parser(name)
        sp.add_argument("--synthetic", action="store_true", help="Zufallsdaten statt echter Kurse")
        sp.add_argument("--timeframe", help="Kerzengroesse, z. B. 5m, 15m, 1h (1h = 2 Jahre Historie)")
    sp = sub.add_parser("paper")
    sp.add_argument("--poll", type=int, default=60, help="Sekunden zwischen Abfragen")
    sp.add_argument("--strategy", help='Testlauf mit fester Strategie, z. B. "breakout(lookback=20)"')
    sp.add_argument("--symbols", nargs="+", help="Maerkte fuer den Testlauf, z. B. GC=F NQ=F")
    sp.add_argument("--timeframe", help="Kerzengroesse, z. B. 5m oder 1h")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)
    if getattr(args, "timeframe", None):
        cfg.timeframe = args.timeframe
        cfg.history_period = max_period(args.timeframe)
    if args.cmd != "paper":
        print(f"Kerzen: {cfg.timeframe}, Historie: {cfg.history_period}")
    {"backtest": cmd_backtest, "select": cmd_select, "paper": cmd_paper}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
