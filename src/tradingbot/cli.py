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
            f"Exp {stats['expectancy_r']:+.2f}R | PnL {stats['net_pnl']:+.2f} | MaxDD {stats['max_drawdown_pct']:.1f}%")


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
    selection, all_trades = {}, []
    for m in cfg.markets:
        df = _data(args, cfg, m["symbol"])
        res = walk_forward(df, cfg, m["symbol"])
        print(f"\n== {m['name']} ({m['symbol']}) Walk-Forward ==")
        for w in res["windows"]:
            print(f"  ab {w['from'][:16]}: {w['strategy'] or 'keine Strategie besteht die Kriterien'}")
        print(f"  Out-of-Sample: {_fmt(res['out_of_sample'])}")
        print(f"  Strategiewechsel: {res['switches']} von {len(res['windows'])} Fenstern")
        journal.save(res["trades"], source="walk_forward")
        all_trades += res["trades"]
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
    from tradingbot.learning.selector import equity_curve
    all_trades.sort(key=lambda t: t.exit_time)
    total = summarize(all_trades, equity_curve(all_trades, cfg.start_capital * max(len(cfg.markets), 1)))
    print(f"\n== Alle Maerkte zusammen (Out-of-Sample) ==\n  {_fmt(total)}")
    SELECTION_FILE.write_text(json.dumps({"timeframe": cfg.timeframe, "exit_mode": cfg.risk.exit_mode,
                                          "strategies": selection}, indent=2))
    print(f"\nAusgewaehlt fuer Paper Trading: {selection or 'nichts (kein Markt hat out-of-sample bestanden)'}")


def cmd_paper(args, cfg):
    from tradingbot.bot import run_paper
    strategies = None
    if args.strategy:
        symbols = args.symbols or [m["symbol"] for m in cfg.markets]
        strategies = {s: args.strategy for s in symbols}
        print("TESTLAUF: Strategie manuell gewaehlt, sie hat die Pruefung nicht bestanden. Kein Gewinn zu erwarten.")
    run_paper(cfg, args.poll, strategies)


def cmd_dashboard(args, cfg):
    from tradingbot.dashboard.server import serve
    serve(args.port, not args.no_browser)


def cmd_traders(args, cfg):
    from tradingbot.traders import runner
    if args.action == "scan":
        runner.scan(cfg, days=args.days, top=args.top, keep=args.keep)
    elif args.action == "test":
        runner.test(cfg, train_days=args.days, test_days=args.test_days, windows=args.windows,
                    top=args.top, keep=args.keep, delay_s=args.delay, min_t=args.min_t)
    else:
        runner.watch(cfg, poll_seconds=args.poll, rescan_hours=args.rescan_hours)


def cmd_learn(args, cfg):
    from tradingbot import bot
    from tradingbot.learning import research
    import os
    every = args.every
    try:
        os.nice(10)  # niedrige Prioritaet: Uni, Netflix usw. gehen vor
    except (AttributeError, OSError):
        pass
    print(f"Lernschleife: Familie {args.family}, {len(research.candidates(args.family))} Strategien, "
          f"{len(cfg.markets)} Maerkte, {cfg.timeframe}-Kerzen, alle {every:g} h eine neue Runde. "
          f"Daten/Ergebnisse in {bot.DATA}")
    research.run(cfg, args.family, bot.DATA, every_hours=every, rounds=args.rounds, folds=args.folds,
                 min_trades=args.min_trades or 20, n_new=args.new,
                 workers=args.workers if args.workers > 0 else max(1, (os.cpu_count() or 2) // 2))


def cmd_data_cost(args, cfg):
    """Was wuerde der Download bei Databento kosten? (Abfrage ist kostenlos, laedt nichts)"""
    import pandas as pd
    from tradingbot.data import databento
    if not databento.api_key():
        print("DATABENTO_API_KEY fehlt in .env (siehe src/tradingbot/data/databento.py)")
        return
    end = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=1)).isoformat()
    start = pd.Timestamp(cfg.history_period, tz="UTC").isoformat()
    total = 0.0
    for m in cfg.markets:
        if m.get("source") != "databento":
            continue
        c = databento.cost(m["databento"], start, end)
        total += c
        print(f"{m['symbol']:4s} {m['databento']:8s} {start[:10]} bis {end[:10]}: {c:8.2f} $")
    print(f"Zusammen: {total:.2f} $ (Startguthaben bei Databento: 125 $)")


def cmd_status(args, cfg):
    """Kurzer Ueberblick im Terminal: Konto, offene Positionen, letzte Trades und Ereignisse."""
    import csv
    import json
    from tradingbot import bot
    print(f"Datenordner: {bot.DATA}")
    if bot.STATE_FILE.exists():
        st = json.loads(bot.STATE_FILE.read_text())
        print(f"Konto (Spielgeld): {st['equity']:.2f} $ (Start {cfg.start_capital:g} $)")
        for sym, p in st["positions"].items():
            print(f"  offen: {sym} {'LONG' if p['side'] > 0 else 'SHORT'} {p['contracts']:g} @ {p['entry']:.4g} "
                  f"| SL {p['stop_loss']:.4g} | TP {p['take_profit']:.4g}")
        if not st["positions"]:
            print("  keine offenen Positionen")
    else:
        print("Noch kein Paper-Konto in diesem Ordner.")
    if bot.TRADES_CSV.exists():
        with bot.TRADES_CSV.open(newline="") as f:
            rows = list(csv.DictReader(f))
        wins = sum(1 for r in rows if float(r["pnl"]) > 0)
        print(f"Abgeschlossene Trades: {len(rows)} | Gewinner: {wins} | "
              f"Summe: {sum(float(r['pnl']) for r in rows):+.2f} $")
        for r in rows[-5:]:
            print(f"  {r['time'][:16]} {r['symbol']:6s} {r['reason']:12s} {float(r['pnl']):+.2f} $")
    ev = bot.DATA / "traders" / "events.jsonl"
    if ev.exists():
        print("Letzte Ereignisse:")
        for line in ev.read_text().splitlines()[-8:]:
            e = json.loads(line)
            print(f"  {e['time'][:16]} {e['text']}")
    if bot.DASHBOARD_JSON.exists():
        print(f"Letztes Lebenszeichen des Bots: {json.loads(bot.DASHBOARD_JSON.read_text()).get('updated', '?')} (UTC)")


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS,
                        help="Pfad zur Konfiguration, z. B. config/crypto.yaml")
    p = argparse.ArgumentParser(prog="tradingbot", parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("backtest", "select"):
        sp = sub.add_parser(name, parents=[common])
        sp.add_argument("--synthetic", action="store_true", help="Zufallsdaten statt echter Kurse")
        sp.add_argument("--timeframe", help="Kerzengroesse, z. B. 5m, 15m, 1h (1h = 2 Jahre Historie)")
        sp.add_argument("--exit", choices=["fixed", "trailing"], help="fixed = SL+TP 1:3, trailing = nachgezogener Stop")
        sp.add_argument("--min-trades", type=int, help="Mindestanzahl Trades pro Fenster/Out-of-Sample (Standard 30)")
    sp = sub.add_parser("paper", parents=[common])
    sp.add_argument("--poll", type=int, default=60, help="Sekunden zwischen Abfragen")
    sp.add_argument("--strategy", help='Testlauf mit fester Strategie, z. B. "breakout(lookback=20)"')
    sp.add_argument("--symbols", nargs="+", help="Maerkte fuer den Testlauf, z. B. GC=F NQ=F")
    sp.add_argument("--timeframe", help="Kerzengroesse, z. B. 5m oder 1h")
    sp.add_argument("--exit", choices=["fixed", "trailing"], help="fixed = SL+TP 1:3, trailing = nachgezogener Stop")
    sp = sub.add_parser("dashboard", help="Live-Dashboard im Browser")
    sp.add_argument("--port", type=int, default=8050)
    sp.add_argument("--no-browser", action="store_true")
    sub.add_parser("data-cost", parents=[common], help="Kosten fuer den Databento-Download anzeigen (laedt nichts)")
    sp = sub.add_parser("learn", parents=[common], help="Dauer-Lernschleife: Strategien immer wieder ehrlich testen")
    sp.add_argument("--family", choices=["ict", "momentum", "all"], default="ict",
                    help="ict = ICT/TJR-Scalping (Futures), momentum = Momentum/Volumen/RSI (Krypto)")
    sp.add_argument("--every", type=float, default=6, help="Stunden zwischen zwei Lernrunden")
    sp.add_argument("--new", type=int, default=24, help="neue Varianten pro Runde")
    sp.add_argument("--workers", type=int, default=1, help="Prozessorkerne fuer die Suche (0 = die Haelfte, zwei Schleifen teilen sich den Rechner)")
    sp.add_argument("--rounds", type=int, help="Anzahl Runden (Standard: endlos)")
    sp.add_argument("--folds", type=int, default=4, help="Anzahl Testabschnitte im Walk-Forward")
    sp.add_argument("--min-trades", type=int, help="Mindest-Trades im Auswahlzeitraum (Standard 20)")
    sp.add_argument("--timeframe", help="Kerzengroesse, z. B. 5m oder 1h")
    sub.add_parser("status", parents=[common], help="Kontostand, offene Positionen und letzte Trades anzeigen")
    sp = sub.add_parser("traders", parents=[common], help="Top-Trader auf Hyperliquid analysieren und kopieren")
    sp.add_argument("action", choices=["scan", "test", "watch"],
                    help="scan = beste Trader finden, test = ehrlicher Kopier-Test, watch = Paper-Copy-Trading")
    sp.add_argument("--days", type=int, default=30, help="Tage fuer die Bewertung (Standard 30)")
    sp.add_argument("--test-days", type=int, default=10, help="test: Tage pro Kopier-Fenster")
    sp.add_argument("--windows", type=int, default=5, help="test: Anzahl Fenster")
    sp.add_argument("--top", type=int, default=80, help="Anzahl Kandidaten aus dem Leaderboard")
    sp.add_argument("--keep", type=int, default=5, help="Anzahl Trader, die kopiert werden")
    sp.add_argument("--delay", type=int, default=60, help="test: Sekunden Verzoegerung beim Kopieren")
    sp.add_argument("--poll", type=int, default=60, help="watch: Sekunden zwischen Abfragen")
    sp.add_argument("--rescan-hours", type=float, default=24,
                    help="watch: Trader alle N Stunden neu auswaehlen (0 = nie)")
    sp.add_argument("--min-t", type=float, default=0.5,
                    help="test: Mindest-t-Wert fuer die Auswahl (der Walk-Forward prueft danach, ob es Zufall war)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(getattr(args, "config", None))
    if getattr(args, "timeframe", None):
        cfg.timeframe = args.timeframe
        cfg.history_period = max_period(args.timeframe)
    if getattr(args, "min_trades", None):
        cfg.selection.min_trades = args.min_trades
    if getattr(args, "exit", None):
        cfg.risk.exit_mode = args.exit
    if args.cmd in ("backtest", "select"):
        print(f"Kerzen: {cfg.timeframe}, Historie: {cfg.history_period}, Ausstieg: {cfg.risk.exit_mode}")
    {"backtest": cmd_backtest, "select": cmd_select, "paper": cmd_paper,
     "dashboard": cmd_dashboard, "traders": cmd_traders, "status": cmd_status,
     "learn": cmd_learn, "data-cost": cmd_data_cost}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
