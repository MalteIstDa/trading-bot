"""Paper-Trading-Schleife: holt regelmaessig neue Kurse, prueft Signale, handelt mit Spielgeld.
Verpasste Kerzen (z. B. nach Ruhezustand) werden nachtraeglich der Reihe nach verarbeitet."""
from __future__ import annotations

import csv
import json
import logging
import os
import time
from pathlib import Path
from datetime import datetime, timezone

from tradingbot.config import ROOT, Config
from tradingbot.data.feed import load_history
from tradingbot.execution.paper import PaperBroker
from tradingbot.risk.manager import RiskManager
from tradingbot.strategies import all_candidates
from tradingbot.strategies.base import atr

log = logging.getLogger("tradingbot")
# Eigener Ordner pro Testlauf moeglich (z. B. Krypto und Futures gleichzeitig): TRADINGBOT_DATA=data/crypto
DATA = Path(os.environ.get("TRADINGBOT_DATA", ROOT / "data"))
if not DATA.is_absolute():
    DATA = ROOT / DATA
DATA.mkdir(parents=True, exist_ok=True)
SELECTION_FILE = DATA / "selection.json"
STATE_FILE = DATA / "paper_state.json"
TRADES_CSV = DATA / "paper_trades.csv"
EQUITY_CSV = DATA / "paper_equity.csv"
DASHBOARD_JSON = DATA / "dashboard.json"


def load_selection() -> dict:
    """{"timeframe": ..., "strategies": {symbol: repr der Strategie}} (geschrieben von `tradingbot select`)."""
    if SELECTION_FILE.exists():
        return json.loads(SELECTION_FILE.read_text())
    return {}


def _log_trade(row: dict) -> None:
    fields = ["time", *row.keys()]
    if TRADES_CSV.exists():
        with TRADES_CSV.open() as f:
            header = f.readline().strip().split(",")
        if header != fields:  # altes Format: beiseitelegen statt Spalten zu vermischen
            TRADES_CSV.rename(TRADES_CSV.with_name("paper_trades_alt.csv"))
    new = not TRADES_CSV.exists()
    with TRADES_CSV.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow({"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), **row})


def _num(x: float):
    return None if x is None or x != x or abs(x) == float("inf") else round(float(x), 4)


def _candles(df) -> list[list]:
    tail = df.tail(200)
    return [[int(ts.timestamp()), _num(r.open), _num(r.high), _num(r.low), _num(r.close)]
            for ts, r in zip(tail.index, tail.itertuples())]


def write_dashboard(cfg: Config, broker, strategies: dict[str, str], frames: dict, extra: dict | None = None) -> None:
    """Schreibt den aktuellen Stand fuer `tradingbot dashboard` (data/dashboard.json + Kontoverlauf).
    extra: optionale Zusatzdaten, z. B. {"traders": [...], "watchlist": {coin: df}, "events": [...]}."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    markets = {}
    unrealized = 0.0
    for symbol, strat_name in strategies.items():
        df = frames.get(symbol)
        pos = broker.open_positions().get(symbol)
        candles = [] if df is None else _candles(df)
        last = candles[-1][4] if candles else None
        open_pnl = None
        if pos is not None and last is not None:
            open_pnl = (last - pos.entry) * pos.side * pos.contracts * broker.point_values.get(symbol, 1.0)
            unrealized += open_pnl
        markets[symbol] = {
            "name": cfg.market(symbol)["name"], "strategy": strat_name, "candles": candles,
            "position": None if pos is None else {
                "side": pos.side, "entry": _num(pos.entry), "stop_loss": _num(pos.stop_loss),
                "take_profit": _num(pos.take_profit), "contracts": pos.contracts,
                "last": last, "unrealized": _num(open_pnl)},
        }
    state = {"updated": now, "mode": "paper", "timeframe": cfg.timeframe, "exit_mode": cfg.risk.exit_mode,
             "start_capital": cfg.start_capital, "equity": round(broker.equity(), 2),
             "unrealized": round(unrealized, 2), "markets": markets}
    state.update(extra or {})
    if "watchlist" in state:
        state["watchlist"] = {coin: {"candles": _candles(df)} for coin, df in state["watchlist"].items()}
    DASHBOARD_JSON.write_text(json.dumps(state))
    new = not EQUITY_CSV.exists()
    with EQUITY_CSV.open("a", newline="") as f:
        if new:
            f.write("time,equity\n")
        f.write(f"{now},{broker.equity():.2f}\n")


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
                         cfg.fee_per_trade, state_file=STATE_FILE, fee_pct=cfg.fee_pct)
    rm = RiskManager(cfg.risk)
    last_bar: dict[str, object] = {}
    period = "5d" if cfg.timeframe in ("1m", "2m", "5m") else "60d"
    log.info("Paper Trading (%s-Kerzen, Ausstieg %s) mit %s | Konto: %.2f", cfg.timeframe, cfg.risk.exit_mode,
             strategies, broker.equity())

    frames: dict = {}
    while True:
        for symbol, strat_name in strategies.items():
            strat = by_name[strat_name]
            try:
                df = load_history(symbol, cfg.timeframe, period, use_cache=False)
            except Exception as e:  # Netzwerkfehler duerfen den Bot nicht beenden
                log.warning("%s: Daten-Fehler %s", symbol, e)
                continue
            frames[symbol] = df
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
                                   cfg.market(symbol)["point_value"], len(broker.open_positions()),
                                   cfg.market(symbol).get("lot_step", 1.0))
                    if plan:
                        broker.place_bracket(symbol, plan)
                        log.info("Neuer Trade %s %s @ %.2f | SL %.2f | TP %.2f | Menge %g", symbol,
                                 "LONG" if plan.side > 0 else "SHORT", plan.entry, plan.stop_loss,
                                 plan.take_profit, plan.contracts)
            last_bar[symbol] = closed_bars.index[-1]
        log.info("Konto: %.2f | offen: %s", broker.equity(), list(broker.open_positions()) or "-")
        try:
            write_dashboard(cfg, broker, strategies, frames)
        except Exception as e:  # Dashboard darf den Bot nie stoppen
            log.warning("Dashboard-Daten konnten nicht geschrieben werden: %s", e)
        time.sleep(poll_seconds)
