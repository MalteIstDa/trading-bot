"""Paper-Trading-Schleife: holt alle paar Minuten neue Kurse, prueft Signale, handelt mit Spielgeld."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from tradingbot.config import ROOT, Config
from tradingbot.data.feed import load_history
from tradingbot.execution.paper import PaperBroker
from tradingbot.risk.manager import RiskManager
from tradingbot.strategies import all_candidates
from tradingbot.strategies.base import atr

log = logging.getLogger("tradingbot")
SELECTION_FILE = ROOT / "data" / "selection.json"


def load_selection() -> dict[str, str]:
    """symbol -> repr der gewaehlten Strategie (geschrieben von `tradingbot select`)."""
    if SELECTION_FILE.exists():
        return json.loads(SELECTION_FILE.read_text())
    return {}


def run_paper(cfg: Config, poll_seconds: int = 300) -> None:
    if cfg.mode != "paper":
        raise SystemExit("Live-Modus ist noch nicht freigeschaltet. Erst Paper Trading.")
    by_name = {repr(s): s for s in all_candidates()}
    selection = load_selection()
    if not selection:
        raise SystemExit("Keine Strategie ausgewaehlt. Erst `tradingbot select` ausfuehren.")
    broker = PaperBroker(cfg.start_capital, {m["symbol"]: m["point_value"] for m in cfg.markets}, cfg.fee_per_trade)
    rm = RiskManager(cfg.risk)
    last_bar: dict[str, object] = {}
    log.info("Paper Trading gestartet mit %s", selection)

    while True:
        for symbol, strat_name in selection.items():
            strat = by_name.get(strat_name)
            if strat is None:
                continue
            try:
                df = load_history(symbol, cfg.timeframe, "5d", use_cache=False)
            except Exception as e:  # Netzwerkfehler duerfen den Bot nicht beenden
                log.warning("%s: Daten-Fehler %s", symbol, e)
                continue
            bar = df.iloc[-2]  # letzte abgeschlossene Bar
            for closed in broker.on_price(symbol, bar["high"], bar["low"], bar["close"]):
                log.info("Trade geschlossen: %s", closed)
            if last_bar.get(symbol) == df.index[-2]:
                continue
            last_bar[symbol] = df.index[-2]
            rm.new_bar(df.index[-2].date(), broker.equity())
            sig = int(strat.signals(df.iloc[:-1]).iloc[-1])
            if sig and symbol not in broker.open_positions():
                plan = rm.plan(sig, float(df["open"].iloc[-1]), float(atr(df.iloc[:-1], cfg.risk.atr_period).iloc[-1]),
                               broker.equity(), cfg.market(symbol)["point_value"], len(broker.open_positions()))
                if plan:
                    broker.place_bracket(symbol, plan)
                    log.info("Neuer Trade %s: %s", symbol, plan)
        log.info("Equity: %.2f | offen: %s", broker.equity(), list(broker.open_positions()))
        time.sleep(poll_seconds)
