"""Paper-Trading-Schleife: holt regelmaessig neue Kurse, prueft Signale, handelt mit Spielgeld.
Verpasste Kerzen (z. B. nach Ruhezustand) werden nachtraeglich der Reihe nach verarbeitet."""
from __future__ import annotations

import csv
import json
import logging
import time
from datetime import datetime, timezone

from tradingbot.config import ROOT, Config
from tradingbot.data.feed import load_history
from tradingbot.execution.paper import PaperBroker
from tradingbot.risk.manager import RiskManager
from tradingbot.strategies import all_candidates
from tradingbot.strategies.base import atr

log = logging.getLogger("tradingbot")
DATA = ROOT / "data"
SELECTION_FILE = DATA / "selection.json"
STATE_FILE = DATA / "paper_state.json"
TRADES_CSV = DATA / "paper_trades.csv"


def load_selection() -> dict:
    """{"timeframe": ..., "strategies": {symbol: repr der Strategie}} (geschrieben von `tradingbot select`)."""
    if SELECTION_FILE.exists():
        return json.loads(SELECTION_FILE.read_text())
    return {}


def _log_trade(row: dict) -> None:
    new = not TRADES_CSV.exists()
    with TRADES_CSV.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["time", *row.keys()])
        if new:
            w.writeheader()
        w.writerow({"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), **row})


def run_paper(cfg: Config, poll_seconds: int = 60, strategies: dict[str, str] | None = None) -> None:
    """strategies: {symbol: strategie-name}. Ohne Angabe wird die Auswahl aus `tradingbot select` genutzt."""
    if cfg.mode != "paper":
        raise SystemExit("Live-Modus ist noch nicht freigeschaltet. Erst Paper Trading.")
    by_name = {repr(s): s for s in all_candidates()}
    if not strategies:
        saved = load_selection()
        strategies = saved.get("strategies", {})
        cfg.timeframe = saved.get("timeframe", cfg.timeframe)
        cfg.risk.exit_mode = saved.get("exit_mode", cfg.risk.exit_mode)
    if not strategies:
        raise SystemExit("Keine Strategie ausgewaehlt. Entweder `tradingbot select` ausfuehren oder "
                         "`tradingbot paper --strategy \"breakout(lookback=20)\"` als Testlauf.")
    unknown = [s for s in strategies.values() if s not in by_name]
    if unknown:
        raise SystemExit(f"Unbekannte Strategie(n): {unknown}. Verfuegbar: {sorted(by_name)}")

    broker = PaperBroker(cfg.start_capital, {m["symbol"]: m["point_value"] for m in cfg.markets},
                         cfg.fee_per_trade, state_file=STATE_FILE)
    rm = RiskManager(cfg.risk)
    last_bar: dict[str, object] = {}
    period = "5d" if cfg.timeframe in ("1m", "2m", "5m") else "60d"
    log.info("Paper Trading (%s-Kerzen, Ausstieg %s) mit %s | Konto: %.2f", cfg.timeframe, cfg.risk.exit_mode,
             strategies, broker.equity())

    while True:
        for symbol, strat_name in strategies.items():
            strat = by_name[strat_name]
            try:
                df = load_history(symbol, cfg.timeframe, period, use_cache=False)
            except Exception as e:  # Netzwerkfehler duerfen den Bot nicht beenden
                log.warning("%s: Daten-Fehler %s", symbol, e)
                continue
            closed_bars = df.iloc[:-1]  # letzte Kerze ist noch nicht fertig
            if symbol not in last_bar:  # Start: nur ab jetzt handeln, nicht die Vergangenheit
                last_bar[symbol] = closed_bars.index[-1]
                continue
            new_idx = [i for i, ts in enumerate(closed_bars.index) if ts > last_bar[symbol]]
            if not new_idx:
                continue
            sig_all = strat.signals(closed_bars)
            atr_all = atr(closed_bars, cfg.risk.atr_period)
            for i in new_idx:
                bar = closed_bars.iloc[i]
                for closed in broker.on_price(symbol, bar["high"], bar["low"], bar["close"]):
                    _log_trade(closed)
                    log.info("Trade geschlossen %s: %s, PnL %.2f", symbol, closed["reason"].upper(), closed["pnl"])
                rm.new_bar(closed_bars.index[i].date(), broker.equity())
                sig = int(sig_all.iloc[i])
                if sig and symbol not in broker.open_positions():
                    entry = float(df["open"].iloc[i + 1])  # Einstieg zur Eroeffnung der naechsten Kerze
                    plan = rm.plan(sig, entry, float(atr_all.iloc[i]), broker.equity(),
                                   cfg.market(symbol)["point_value"], len(broker.open_positions()))
                    if plan:
                        broker.place_bracket(symbol, plan)
                        log.info("Neuer Trade %s %s @ %.2f | SL %.2f | TP %.2f | %d Kontrakt(e)", symbol,
                                 "LONG" if plan.side > 0 else "SHORT", plan.entry, plan.stop_loss,
                                 plan.take_profit, plan.contracts)
            last_bar[symbol] = closed_bars.index[-1]
        log.info("Konto: %.2f | offen: %s", broker.equity(), list(broker.open_positions()) or "-")
        time.sleep(poll_seconds)
