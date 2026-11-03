"""Befehle: tradingbot traders scan | test | watch"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pandas as pd

from tradingbot import bot
from tradingbot.config import Config
from tradingbot.traders import hyperliquid as hl
from tradingbot.traders.analysis import (DAY_MS, close_times, open_signals, prefilter, score_trader,
                                         simulate_copy, summarize_r)

log = logging.getLogger("tradingbot")


def _dir() -> Path:
    d = bot.DATA / "traders"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now_ms() -> int:
    return int(time.time() * 1000)


def _short(addr: str) -> str:
    return addr[:6] + "…" + addr[-4:]


def _fmt(s: dict) -> str:
    return (f"Trades {s['trades']:4d} | WR {s['win_rate']:.0%} | PF {s['profit_factor']:.2f} | "
            f"Exp {s['expectancy_r']:+.2f}R | Einstieg im Schnitt {s['avg_slippage_pct']:+.2f}% schlechter")


def _candidates(rank_by: str, top: int) -> list[dict]:
    print("Lade Leaderboard von Hyperliquid …")
    rows = hl.leaderboard()
    cands = prefilter(rows, top=10_000)
    if rank_by == "account_value":
        cands.sort(key=lambda r: r["account_value"], reverse=True)
    cands = cands[:top]
    print(f"{len(rows)} Konten im Leaderboard, {len(cands)} Kandidaten nach Filter "
          f"(Konto >= 50.000 $, Gewinn im Monat und insgesamt, kein Hochfrequenz-Handel)")
    return cands


def _load_fills(cands: list[dict], start_ms: int, end_ms: int) -> dict[str, list]:
    out = {}
    for i, c in enumerate(cands, 1):
        try:
            f = hl.fills(c["address"], start_ms, end_ms, cache_dir=_dir())
        except Exception as e:
            print(f"  {_short(c['address'])}: Fehler {e}")
            continue
        if len(f) >= hl.MAX_FILLS:
            print(f"  [{i}/{len(cands)}] {_short(c['address'])}: zu viele Trades (Hochfrequenz), uebersprungen")
            continue
        out[c["address"]] = f
        print(f"  [{i}/{len(cands)}] {_short(c['address'])}: {len(f)} Fills")
        time.sleep(0.3)
    return out


def scan(cfg: Config, days: int = 30, top: int = 40, keep: int = 5) -> list[dict]:
    """Bewertet die Kandidaten der letzten `days` Tage und speichert die besten fuer `watch`."""
    end = _now_ms()
    start = end - days * DAY_MS
    cands = _candidates("month_pnl", top)
    fills = _load_fills(cands, start, end)
    scores = sorted((score_trader(a, f, start, end) for a, f in fills.items()), key=lambda s: s.score, reverse=True)
    print(f"\n== Bewertung der letzten {days} Tage ==")
    print(f"{'Konto':14s} {'Gewinn $':>12s} {'Gewinn-Tage':>11s} {'Gewinn-Wochen':>13s} {'t-Wert':>7s} "
          f"{'Eroeffn./Tag':>12s} {'Score':>6s}")
    for s in scores[:15]:
        print(f"{_short(s.address):14s} {s.pnl:12,.0f} {s.profitable_days:11.0%} {s.profitable_weeks:13.0%} "
              f"{s.t_stat:7.2f} {s.opens_per_day:12.1f} {s.score:6.2f}")
    chosen = [s.to_dict() for s in scores if s.t_stat >= 2 and s.opens_per_day >= 0.2][:keep]
    (_dir() / "selected.json").write_text(json.dumps({"created": end, "days": days, "traders": chosen}, indent=2))
    print(f"\nAusgewaehlt fuer `tradingbot traders watch`: {len(chosen)} Konten "
          f"(t-Wert >= 2, mind. 1 neue Position alle 5 Tage)")
    print("Wichtig: Gut in der Vergangenheit heisst nicht gut in Zukunft. Erst `tradingbot traders test` ausfuehren.")
    return chosen


def test(cfg: Config, train_days: int = 30, test_days: int = 10, windows: int = 5, top: int = 40,
         keep: int = 5, interval: str = "15m", delay_s: int = 60, min_t: float = 1.5) -> dict:
    """Walk-Forward: Trader nur mit vergangenen Daten auswaehlen, ihre Trades danach mit Verzoegerung kopieren."""
    end = _now_ms()
    test_start = end - windows * test_days * DAY_MS
    start = test_start - train_days * DAY_MS
    cands = _candidates("account_value", top)
    fills = _load_fills(cands, start, end)
    if not fills:
        raise SystemExit("Keine Trade-Daten geladen. Bitte die Ausgabe oben pruefen.")
    total_fills = sum(len(f) for f in fills.values())
    first = min((f[0]["time"] for f in fills.values() if f), default=end)
    print(f"Geladen: {len(fills)} Konten, {total_fills} Fills, aeltester Fill vom "
          f"{pd.to_datetime(first, unit='ms'):%Y-%m-%d} (benoetigt ab {pd.to_datetime(start, unit='ms'):%Y-%m-%d})")
    closes = {a: close_times(f) for a, f in fills.items()}
    candle_cache: dict[str, pd.DataFrame] = {}

    def get_candles(coin: str) -> pd.DataFrame:
        if coin not in candle_cache:
            try:
                candle_cache[coin] = hl.candles(coin, interval, test_start - 2 * DAY_MS, end)
                time.sleep(0.2)
            except Exception as e:
                print(f"  Kerzen fuer {coin} fehlen: {e}")
                candle_cache[coin] = pd.DataFrame()
        return candle_cache[coin]

    all_trades, own_pnl, rows = [], [], []
    for w in range(windows):
        tr_start = test_start + w * test_days * DAY_MS - train_days * DAY_MS
        te_start = test_start + w * test_days * DAY_MS
        te_end = te_start + test_days * DAY_MS
        scores = sorted((score_trader(a, f, tr_start, te_start) for a, f in fills.items()),
                        key=lambda s: s.score, reverse=True)
        chosen = [s for s in scores if s.t_stat >= min_t and s.opens_per_day >= 0.2][:keep]
        if not chosen and scores:  # Diagnose: warum niemand bestanden hat
            best = scores[:3]
            print(f"  Fenster {w + 1}: niemand erfuellt t-Wert >= {min_t} und >= 0,2 Eroeffnungen/Tag. Beste: " +
                  ", ".join(f"{_short(x.address)} t={x.t_stat:.2f} Eroeffn./Tag={x.opens_per_day:.2f} "
                            f"Gewinn={x.pnl:,.0f}$" for x in best))
        w_trades, w_own = [], 0.0
        for s in chosen:
            sig = open_signals(fills[s.address], te_start, te_end)
            coins = {x["coin"] for x in sig}
            cdl = {c: get_candles(c) for c in coins}
            w_trades += simulate_copy(sig, closes[s.address], cdl, cfg, s.address, delay_s * 1000)
            w_own += score_trader(s.address, fills[s.address], te_start, te_end).pnl
        all_trades += w_trades
        own_pnl.append(w_own)
        label = pd.to_datetime(te_start, unit="ms").strftime("%Y-%m-%d")
        rows.append((label, len(chosen), w_own, summarize_r(w_trades)))

    print(f"\n== Walk-Forward: {train_days} Tage auswaehlen, {test_days} Tage kopieren, "
          f"{delay_s}s Verzoegerung, {interval}-Kerzen ==")
    for label, n, own, st in rows:
        print(f"  ab {label}: {n} Trader | deren eigener Gewinn danach {own:+12,.0f} $ | Kopie: {_fmt(st)}")
    total = summarize_r(all_trades)
    print(f"\n  Kopieren gesamt: {_fmt(total)}")
    positive_windows = sum(1 for o in own_pnl if o > 0)
    print(f"  Ausgewaehlte Trader waren danach in {positive_windows} von {windows} Fenstern selbst im Plus")
    print("\nHinweis: Das Leaderboard zeigt nur heute existierende Konten. Wer pleite ging, fehlt "
          "(Survivorship Bias). Echte Ergebnisse waeren eher etwas schlechter.")
    verdict = total["trades"] >= 30 and total["expectancy_r"] > 0 and positive_windows > windows / 2
    print("  -> BESTANDEN: lohnt sich fuer Paper Trading" if verdict else
          "  -> NICHT BESTANDEN: Kopieren bringt hier nach Gebuehren und Verzoegerung keinen Vorteil")
    (_dir() / "test_result.json").write_text(json.dumps({"total": total, "windows": [
        {"from": l, "traders": n, "own_pnl": o, "copy": s} for l, n, o, s in rows], "passed": verdict}, indent=2))
    return {"total": total, "passed": verdict}


def watch(cfg: Config, poll_seconds: int = 60, interval: str = "15m") -> None:
    """Paper-Copy-Trading: verfolgt die Positionen der ausgewaehlten Konten und kopiert neue Positionen mit Spielgeld.
    Unser Risk Manager setzt SL/TP. Schliesst der Trader, schliessen wir auch."""
    from tradingbot.execution.paper import PaperBroker
    from tradingbot.risk.manager import RiskManager
    from tradingbot.strategies.base import atr

    sel_file = _dir() / "selected.json"
    if not sel_file.exists():
        raise SystemExit("Noch keine Trader ausgewaehlt. Erst `tradingbot traders scan` ausfuehren.")
    traders = [t["address"] for t in json.loads(sel_file.read_text())["traders"]]
    if not traders:
        raise SystemExit("Der Scan hat keine Trader ausgewaehlt, die die Kriterien erfuellen.")
    broker = PaperBroker(cfg.start_capital, {}, cfg.fee_per_trade, state_file=bot.STATE_FILE, fee_pct=cfg.fee_pct)
    rm = RiskManager(cfg.risk)
    decimals = hl.size_decimals()
    owner_file = _dir() / "copy_owner.json"
    # coin -> {"trader": adresse, "opened": zeit in ms}; bleibt bei Neustart erhalten
    owner: dict[str, dict] = json.loads(owner_file.read_text()) if owner_file.exists() else {}
    checked: dict[str, int] = {}  # coin -> letzte gepruefte 1m-Kerze (ms)
    last: dict[str, dict] = {}
    frames: dict[str, pd.DataFrame] = {}
    min_notional = 10.0  # Hyperliquid: Mindestgroesse einer Order 10 $
    log.info("Copy-Paper-Trading gestartet: %s | Konto %.2f", [_short(t) for t in traders], broker.equity())

    while True:
        for t in traders:
            try:
                pos = hl.positions(t)
            except Exception as e:
                log.warning("%s: %s", _short(t), e)
                continue
            prev = last.get(t)
            last[t] = pos
            if prev is None:  # Start: bestehende Positionen nicht nachkaufen, nur neue kopieren
                continue
            for coin, p in pos.items():
                side = 1 if p["size"] > 0 else -1
                was = prev.get(coin)
                is_new = was is None or (was["size"] > 0) != (p["size"] > 0)
                if not is_new or coin in broker.open_positions():
                    continue
                df = hl.candles(coin, interval, _now_ms() - 3 * DAY_MS)
                frames[coin] = df
                if len(df) < cfg.risk.atr_period + 2:
                    continue
                entry = float(df["close"].iloc[-1])
                step = 10 ** -decimals.get(coin, 3)
                plan = rm.plan(side, entry, float(atr(df, cfg.risk.atr_period).iloc[-1]), broker.equity(), 1.0,
                               len(broker.open_positions()), step)
                if plan is None:
                    log.info("%s kopiert %s %s nicht: Konto zu klein fuer das Risiko", _short(t), coin, side)
                    continue
                if plan.contracts * entry < min_notional:
                    log.info("%s %s uebersprungen: Order %.2f $ unter Mindestgroesse %.0f $ (echtes Konto ginge nicht)",
                             _short(t), coin, plan.contracts * entry, min_notional)
                    continue
                broker.place_bracket(coin, plan)
                owner[coin] = {"trader": t, "opened": _now_ms()}
                owner_file.write_text(json.dumps(owner))
                log.info("KOPIERT von %s: %s %s @ %.4f | SL %.4f | TP %.4f | Menge %g", _short(t), coin,
                         "LONG" if side > 0 else "SHORT", entry, plan.stop_loss, plan.take_profit, plan.contracts)
            for coin, plan in list(broker.open_positions().items()):
                if owner.get(coin, {}).get("trader") != t:
                    continue
                p = pos.get(coin)
                if p is None or (p["size"] > 0) != (plan.side > 0):  # Trader hat geschlossen/gedreht
                    df = hl.candles(coin, interval, _now_ms() - 3 * DAY_MS)
                    frames[coin] = df
                    closed = broker.close(coin, float(df["close"].iloc[-1]), "trader_exit")
                    if closed:
                        bot._log_trade(closed)
                        log.info("Trader hat %s geschlossen, wir auch: PnL %.2f", coin, closed["pnl"])
            time.sleep(0.3)
        # SL/TP pruefen: nur abgeschlossene 1m-Kerzen nach dem Einstieg, jede nur einmal
        for coin in list(broker.open_positions()):
            since = max(checked.get(coin, 0), owner.get(coin, {}).get("opened", _now_ms() - 10 * 60_000))
            try:
                df = hl.candles(coin, "1m", since)
                frames[coin] = hl.candles(coin, interval, _now_ms() - 3 * DAY_MS)
            except Exception as e:
                log.warning("%s Kerzen: %s", coin, e)
                continue
            done = df[(df.index.astype("int64") // 1_000_000 + 60_000) <= _now_ms()]
            done = done[done.index.astype("int64") // 1_000_000 >= since]
            for ts, bar in done.iterrows():
                checked[coin] = int(ts.value // 1_000_000) + 60_000
                for closed in broker.on_price(coin, bar["high"], bar["low"], bar["close"]):
                    bot._log_trade(closed)
                    log.info("%s geschlossen (%s): PnL %.2f", coin, closed["reason"], closed["pnl"])
        log.info("Konto: %.2f | offen: %s", broker.equity(), list(broker.open_positions()) or "-")
        strategies = {c: f"Kopie von {_short(owner.get(c, {}).get('trader', '?'))}" for c in broker.open_positions()}
        try:
            bot.write_dashboard(cfg, broker, strategies, frames)
        except Exception as e:
            log.warning("Dashboard: %s", e)
        time.sleep(poll_seconds)
