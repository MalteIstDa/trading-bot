"""Dauer-Lernschleife ("tradingbot learn"): testet eine Strategie-Familie immer wieder auf allen Maerkten,
waehlt nur mit vergangenen Daten aus und bewertet nur auf ungesehenen Daten.

Ablauf pro Runde:
  1. Kurse aktualisieren und an ein Archiv anhaengen (data/.../archive). So waechst die Historie mit jedem Tag,
     auch wenn Yahoo fuer 5m-Kerzen nur 60 Tage liefert.
  2. Jede Strategie einmal pro Markt backtesten (Trades in R).
  3. Walk-Forward ueber die Zeit: in jedem Abschnitt die Strategie waehlen, die VORHER (alle Maerkte zusammen)
     am besten war (genug Trades, Profit Factor, in beiden Haelften im Plus), inkl. gelerntem Filter
     (Stunden/Volatilitaet mit Verlusten sperren), und sie im naechsten Abschnitt pruefen.
  4. Vergleich mit Zufall: gleich viele Einstiege zu denselben Uhrzeiten, zufaellige Richtung, gleiche SL/TP.
     Nur wenn das Ergebnis klar besser ist als fast alle Zufallslaeufe, zaehlt es.
  5. Ergebnis nach data/.../research/latest.json (fuers Dashboard) und history.jsonl.
     Besteht eine Strategie, wird sie fuer den Paper-Bot als Auswahl gespeichert.
Danach warten und von vorn (mehr Daten = ehrlichere Antwort).
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from tradingbot.backtest.engine import Trade, run_backtest
from tradingbot.config import Config
from tradingbot.learning.filters import learn_filter
from tradingbot.strategies.base import Strategy

log = logging.getLogger("tradingbot")


def families() -> dict[str, list[type[Strategy]]]:
    from tradingbot.strategies.ict import ICT_STRATEGIES
    from tradingbot.strategies.library import STRATEGIES
    from tradingbot.strategies.momentum import MOMENTUM_STRATEGIES
    return {"ict": ICT_STRATEGIES, "momentum": MOMENTUM_STRATEGIES + STRATEGIES, "all": ICT_STRATEGIES + MOMENTUM_STRATEGIES + STRATEGIES}


def candidates(family: str) -> list[Strategy]:
    import itertools
    out = []
    for cls in families()[family]:
        keys = list(cls.grid)
        for values in itertools.product(*(cls.grid[k] for k in keys)):
            params = dict(zip(keys, values))
            if hasattr(cls, "valid") and not cls.valid(params):
                continue
            out.append(cls(**params))
    return out


# ---------- Daten: Archiv, das mit der Zeit waechst ----------

def update_archive(symbol: str, interval: str, period: str, folder: Path, fetch=None) -> pd.DataFrame:
    from tradingbot.data.feed import load_history, read_csv
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / f"{symbol.replace('=', '_').replace('/', '_')}_{interval}.csv"
    old = read_csv(f) if f.exists() else None
    try:
        new = (fetch or (lambda: load_history(symbol, interval, period, use_cache=False)))()
    except Exception as e:
        if old is None:
            raise
        log.warning("%s: keine neuen Daten (%s), nutze Archiv", symbol, e)
        return old
    df = new if old is None else pd.concat([old, new])
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.to_csv(f)
    return df


# ---------- Bewertung ----------

def stats(rs: list[float]) -> dict:
    n = len(rs)
    if not n:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "expectancy_r": 0.0, "sum_r": 0.0}
    wins = [r for r in rs if r > 0]
    losses = [-r for r in rs if r <= 0]
    pf = sum(wins) / sum(losses) if losses and sum(losses) > 0 else None  # None = kein Verlusttrade
    return {"trades": n, "win_rate": len(wins) / n, "profit_factor": pf, "expectancy_r": sum(rs) / n,
            "sum_r": sum(rs)}


def _ok(trades: list[Trade], start, end, min_trades: int, min_pf: float) -> bool:
    rs = [t.r_multiple for t in trades]
    st = stats(rs)
    if st["trades"] < min_trades or st["expectancy_r"] <= 0:
        return False
    if st["profit_factor"] is not None and st["profit_factor"] < min_pf:
        return False
    mid = start + (end - start) / 2
    a = sum(t.r_multiple for t in trades if t.entry_time < mid)
    b = sum(t.r_multiple for t in trades if t.entry_time >= mid)
    return a > 0 and b > 0  # in beiden Haelften im Plus, sonst eher Zufall


def _between(trades: list[Trade], start, end) -> list[Trade]:
    return [t for t in trades if start <= t.entry_time < end]


def random_benchmark(frames: dict[str, pd.DataFrame], trades: list[Trade], cfg: Config, runs: int = 200,
                     seed: int = 0) -> list[float]:
    """Expectancy von Zufalls-Einstiegen: gleiche Anzahl pro Markt, gleiche Stunde des Tages wie die echten
    Trades, zufaellige Richtung, derselbe Risk Manager. Ergebnis: Liste der Zufalls-Expectancies."""
    rng = np.random.default_rng(seed)
    out = []
    by_market: dict[str, list[Trade]] = {}
    for t in trades:
        by_market.setdefault(t.symbol, []).append(t)
    prepared = {}
    for sym, ts in by_market.items():
        df = frames[sym]
        hours = df.index.hour.to_numpy()
        pool = {h: np.nonzero(hours == h)[0] for h in {t.hour for t in ts}}
        prepared[sym] = (df, ts, pool)
    for _ in range(runs):
        rs = []
        for sym, (df, ts, pool) in prepared.items():
            sig = np.zeros(len(df), dtype=int)
            for t in ts:
                cand = pool[t.hour]
                if len(cand):
                    sig[rng.choice(cand)] = rng.choice([-1, 1])
            rs += [x.r_multiple for x in run_backtest(df, _Fixed(sig), cfg, sym, tick=cfg.market(sym).get("tick", 0.0))[0]]
        out.append(float(np.mean(rs)) if rs else 0.0)
    return out


class _Fixed(Strategy):
    name = "zufall"

    def __init__(self, sig):
        super().__init__()
        self.sig = sig

    def signals(self, df):
        return pd.Series(self.sig, index=df.index)


def evaluate(frames: dict[str, pd.DataFrame], cfg: Config, family: str, folds: int = 4, min_trades: int = 20,
             min_pf: float = 1.2, bench_runs: int = 100) -> dict:
    cands = candidates(family)
    all_trades: dict[str, list[Trade]] = {}
    board = []
    for strat in cands:
        ts: list[Trade] = []
        per_market = {}
        for sym, df in frames.items():
            tr, _ = run_backtest(df, strat, cfg, sym, tick=cfg.market(sym).get("tick", 0.0))
            ts += tr
            per_market[sym] = stats([t.r_multiple for t in tr])
        ts.sort(key=lambda t: t.entry_time)
        all_trades[repr(strat)] = ts
        board.append({"strategy": repr(strat), "family": strat.name, **stats([t.r_multiple for t in ts]),
                      "markets": per_market})
    start = min(df.index[0] for df in frames.values())
    end = max(df.index[-1] for df in frames.values())
    edges = [start + (end - start) * k / (folds + 1) for k in range(folds + 2)]
    windows, oos = [], []
    for k in range(1, folds + 1):
        tr_start, te_start, te_end = edges[0], edges[k], edges[k + 1]  # waechst: alles vor dem Testabschnitt
        best, best_exp, best_filter = None, -math.inf, None
        for name, ts in all_trades.items():
            train = _between(ts, tr_start, te_start)
            filt = learn_filter(train)
            train_f = [t for t in train if filt(t.entry_time, t.atr_pct)]
            if not _ok(train_f, tr_start, te_start, min_trades, min_pf):
                continue
            e = stats([t.r_multiple for t in train_f])["expectancy_r"]
            if e > best_exp:
                best, best_exp, best_filter = name, e, filt
        test = []
        if best:
            test = [t for t in _between(all_trades[best], te_start, te_end) if best_filter(t.entry_time, t.atr_pct)]
            oos += test
        windows.append({"from": str(te_start)[:16], "to": str(te_end)[:16], "strategy": best,
                        "train_expectancy_r": None if best is None else best_exp,
                        "filter": best_filter.describe() if best_filter else None,
                        "test": stats([t.r_multiple for t in test])})
    total = stats([t.r_multiple for t in oos])
    bench = random_benchmark(frames, oos, cfg, bench_runs) if oos else []
    beat = (sum(1 for b in bench if total["expectancy_r"] > b) / len(bench)) if bench else 0.0
    latest = windows[-1]["strategy"] if windows else None
    # Fuer den Live-Einsatz: Auswahl mit ALLEN Daten (gleiche Regeln)
    live_pick = None
    for name, ts in sorted(all_trades.items(), key=lambda kv: -stats([t.r_multiple for t in kv[1]])["expectancy_r"]):
        filt = learn_filter(ts)
        tf = [t for t in ts if filt(t.entry_time, t.atr_pct)]
        if _ok(tf, start, end, min_trades, min_pf):
            live_pick = name
            break
    passed = total["trades"] >= 30 and total["expectancy_r"] > 0 and beat >= 0.9 and live_pick is not None
    board.sort(key=lambda r: r["expectancy_r"], reverse=True)
    return {"family": family, "markets": {s: {"bars": len(df), "from": str(df.index[0])[:16], "to": str(df.index[-1])[:16]}
                                         for s, df in frames.items()},
            "candidates": len(cands), "windows": windows, "out_of_sample": total,
            "random_beaten": beat, "random_median_r": float(np.median(bench)) if bench else None,
            "latest_choice": latest, "live_pick": live_pick, "passed": passed, "leaderboard": board[:25],
            "recent_oos_trades": [{"time": str(t.entry_time)[:16], "symbol": t.symbol, "strategy": t.strategy,
                                   "side": t.side, "r": round(t.r_multiple, 2), "reason": t.reason}
                                  for t in oos[-30:]]}


# ---------- Schleife ----------

def run(cfg: Config, family: str, data_dir: Path, every_hours: float = 6, rounds: int | None = None,
        folds: int = 4, min_trades: int = 20, fetch=None) -> dict:
    """fetch: optional {symbol: callable} fuer Tests. rounds=None: endlos."""
    out_dir = data_dir / "research"
    out_dir.mkdir(parents=True, exist_ok=True)
    status = out_dir / "status.json"
    n = 0
    res = {}
    while rounds is None or n < rounds:
        n += 1
        t0 = time.time()
        _write(status, {"phase": f"Runde {n}: lade und archiviere Kurse", "updated": _now(), "round": n})
        frames = {}
        for m in cfg.markets:
            sym = m["symbol"]
            try:
                frames[sym] = update_archive(sym, cfg.timeframe, cfg.history_period, data_dir / "archive",
                                             (fetch or {}).get(sym))
            except Exception as e:
                log.warning("%s: %s", sym, e)
        if not frames:
            log.warning("Keine Kursdaten, naechster Versuch spaeter")
        else:
            _write(status, {"phase": f"Runde {n}: teste {len(candidates(family))} Strategien auf {len(frames)} Maerkten",
                            "updated": _now(), "round": n})
            res = evaluate(frames, cfg, family, folds=folds, min_trades=min_trades)
            res.update({"updated": _now(), "round": n, "seconds": round(time.time() - t0, 1),
                        "timeframe": cfg.timeframe, "start_capital": cfg.start_capital})
            _write(out_dir / "latest.json", res)
            with (out_dir / "history.jsonl").open("a") as f:
                f.write(json.dumps({"time": res["updated"], "passed": res["passed"], "oos": res["out_of_sample"],
                                    "random_beaten": res["random_beaten"], "live_pick": res["live_pick"],
                                    "bars": sum(v["bars"] for v in res["markets"].values())}) + "\n")
            if res["passed"]:
                (data_dir / "selection.json").write_text(json.dumps(
                    {"timeframe": cfg.timeframe, "exit_mode": cfg.risk.exit_mode,
                     "strategies": {s: res["live_pick"] for s in frames}, "source": "learn", "updated": res["updated"]},
                    indent=2))
            oos = res["out_of_sample"]
            log.info("Runde %d: OOS %d Trades, Exp %+.2fR, besser als %.0f%% der Zufallslaeufe -> %s", n,
                     oos["trades"], oos["expectancy_r"], 100 * res["random_beaten"],
                     "BESTANDEN" if res["passed"] else "nicht bestanden")
        if rounds is not None and n >= rounds:
            break
        nxt = datetime.now(timezone.utc).timestamp() + every_hours * 3600
        _write(status, {"phase": "Wartet auf die nächste Lernrunde", "updated": _now(), "round": n,
                        "next_round": datetime.fromtimestamp(nxt, timezone.utc).isoformat(timespec="seconds")})
        time.sleep(every_hours * 3600)
    return res


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write(path: Path, value: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, default=str), encoding="utf-8")
    tmp.replace(path)
