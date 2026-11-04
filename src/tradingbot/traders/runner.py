"""Befehle: tradingbot traders scan | test | watch"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from tradingbot import bot
from tradingbot.config import Config
from tradingbot.traders import hyperliquid as hl
from tradingbot.traders.analysis import (DAY_MS, close_times, open_signals, prefilter, score_trader,
                                         is_perp, simulate_copy, spot_share, summarize_r)

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
            f"Exp {s['expectancy_r']:+.2f}R")


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
        if f and spot_share(f) > 0.2:
            print(f"  [{i}/{len(cands)}] {_short(c['address'])}: handelt viel Spot (meist Hedge/Basis-Trade), "
                  "nicht kopierbar, uebersprungen")
            continue
        if len(f) >= hl.MAX_FILLS:
            print(f"  [{i}/{len(cands)}] {_short(c['address'])}: zu viele Trades (Hochfrequenz), uebersprungen")
            continue
        print(f"  [{i}/{len(cands)}] {_short(c['address'])}: {len(f)} Fills")
        if f:  # Konten ohne Trades im Zeitraum (z. B. nur Halten) bringen keine Signale
            out[c["address"]] = f
        time.sleep(1.0)  # Hyperliquid begrenzt die Anfragen pro Minute
    return out


# Gleiche Auswahlregeln fuer scan (live) und test (Walk-Forward), damit der Test genau das prueft, was watch kopiert
MIN_T, MIN_OPENS, RANK_BY = 0.5, 0.05, "account_value"
MIN_PNL = 1000.0  # $ Gewinn im Auswahlzeitraum: Konten, die praktisch nichts verdient haben, nicht kopieren


def choose(scores: list, keep: int, min_t: float = MIN_T, min_opens: float = MIN_OPENS) -> list:
    return [s for s in sorted(scores, key=lambda s: s.score, reverse=True)
            if s.t_stat >= min_t and s.opens_per_day >= min_opens and s.pnl >= MIN_PNL][:keep]


def scan(cfg: Config, days: int = 30, top: int = 80, keep: int = 5) -> list[dict]:
    """Bewertet die Kandidaten der letzten `days` Tage und speichert die besten fuer `watch`."""
    end = _now_ms()
    start = end - days * DAY_MS
    cands = _candidates(RANK_BY, top)
    fills = _load_fills(cands, start, end)
    scores = sorted((score_trader(a, f, start, end) for a, f in fills.items()), key=lambda s: s.score, reverse=True)
    print(f"\n== Bewertung der letzten {days} Tage ==")
    print(f"{'Konto':14s} {'Gewinn $':>12s} {'Gewinn-Tage':>11s} {'Gewinn-Wochen':>13s} {'t-Wert':>7s} "
          f"{'Eroeffn./Tag':>12s} {'Score':>6s}")
    for s in scores[:15]:
        print(f"{_short(s.address):14s} {s.pnl:12,.0f} {s.profitable_days:11.0%} {s.profitable_weeks:13.0%} "
              f"{s.t_stat:7.2f} {s.opens_per_day:12.1f} {s.score:6.2f}")
    chosen = [s.to_dict() for s in choose(scores, keep)]
    (_dir() / "selected.json").write_text(json.dumps({"created": end, "days": days, "traders": chosen}, indent=2))
    print(f"\nAusgewaehlt fuer `tradingbot traders watch`: {len(chosen)} Konten "
          f"(t-Wert >= {MIN_T}, mind. {MIN_PNL:,.0f} $ Gewinn, mind. {MIN_OPENS} neue Positionen/Tag; gleiche Regeln wie im Test)")
    print("Wichtig: Gut in der Vergangenheit heisst nicht gut in Zukunft. Erst `tradingbot traders test` ausfuehren.")
    return chosen


def test(cfg: Config, train_days: int = 30, test_days: int = 10, windows: int = 5, top: int = 40,
         keep: int = 5, interval: str = "15m", delay_s: int = 60, min_t: float = MIN_T,
         min_opens: float = MIN_OPENS) -> dict:
    """Walk-Forward: Trader nur mit vergangenen Daten auswaehlen, ihre Trades danach mit Verzoegerung kopieren."""
    end = _now_ms()
    test_start = end - windows * test_days * DAY_MS
    start = test_start - train_days * DAY_MS
    cands = _candidates(RANK_BY, top)
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
        chosen = choose(scores, keep, min_t, min_opens)
        if not chosen and scores:  # Diagnose: warum niemand bestanden hat
            best = scores[:3]
            print(f"  Fenster {w + 1}: niemand erfuellt t-Wert >= {min_t} und >= {min_opens} Eroeffnungen/Tag. Beste: " +
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
        by_trader: dict[str, list] = {}
        for t in w_trades:
            by_trader.setdefault(t.trader, []).append(t)
        for addr, ts in sorted(by_trader.items(), key=lambda kv: -len(kv[1])):
            reasons = pd.Series([t.reason for t in ts]).value_counts().to_dict()
            coins = pd.Series([t.coin for t in ts]).value_counts().head(3).to_dict()
            st = summarize_r(ts)
            print(f"    Fenster {w + 1} {_short(addr)}: {st['trades']} Trades, Exp {st['expectancy_r']:+.2f}R, "
                  f"Ausstiege {reasons}, Coins {coins}")
        label = pd.to_datetime(te_start, unit="ms").strftime("%Y-%m-%d")
        rows.append((label, len(chosen), w_own, summarize_r(w_trades)))

    print(f"\n== Walk-Forward: {train_days} Tage auswaehlen, {test_days} Tage kopieren, "
          f"{delay_s}s Verzoegerung + 0,05 % Aufschlag, nur Perps ==")
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
    pd.DataFrame([t.__dict__ for t in all_trades]).to_csv(_dir() / "copy_trades.csv", index=False)
    (_dir() / "test_result.json").write_text(json.dumps({"total": total, "windows": [
        {"from": l, "traders": n, "own_pnl": o, "copy": s} for l, n, o, s in rows], "passed": verdict}, indent=2))
    return {"total": total, "passed": verdict}


def _ms(index) -> "np.ndarray":
    """Zeitstempel eines DatetimeIndex in Millisekunden, egal ob pandas intern ns oder ms nutzt."""
    return ((index - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)).to_numpy()


def _event(kind: str, text: str, push: bool = True) -> None:  # kind: copy|close|risk|scan|info
    """Ereignis ins Log, in data/.../traders/events.jsonl (fuer das Dashboard) und optional aufs Handy."""
    from tradingbot import notify
    log.info(text)
    row = {"time": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"), "type": kind, "text": text}
    f = _dir() / "events.jsonl"
    with f.open("a") as fh:
        fh.write(json.dumps(row) + "\n")
    if push:
        notify.send(text)
    if kind in ("copy", "close", "risk", "scan"):
        from tradingbot import vault
        vault.add(f"Copy-Bot: {text}")


def _status(phase: str, **info) -> None:
    """Was der Bot gerade tut, fuer die Live-Ansicht im Dashboard (traders/status.json)."""
    row = {"updated": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"), "phase": phase, **info}
    f = _dir() / "status.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(row, default=str), encoding="utf-8")
    tmp.replace(f)


def recent_events(n: int = 50) -> list[dict]:
    f = _dir() / "events.jsonl"
    if not f.exists():
        return []
    lines = f.read_text().splitlines()[-n:]
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _load_selection() -> list[dict]:
    sel_file = _dir() / "selected.json"
    if not sel_file.exists():
        return []
    return json.loads(sel_file.read_text()).get("traders", [])


def watch(cfg: Config, poll_seconds: int = 60, interval: str = "15m", rescan_hours: float = 24,
          max_loops: int | None = None) -> None:
    """Paper-Copy-Trading: verfolgt die Positionen der ausgewaehlten Konten und kopiert neue Positionen mit Spielgeld.
    Unser Risk Manager setzt SL/TP (RR 1:3), begrenzt den Tagesverlust und die Anzahl offener Positionen.
    Schliesst der Trader, schliessen wir auch. Alle `rescan_hours` Stunden werden die Trader neu ausgewaehlt."""
    from datetime import datetime, timezone

    from tradingbot.execution.paper import PaperBroker
    from tradingbot.risk.manager import RiskManager
    from tradingbot.strategies.base import atr

    selection = _load_selection()
    sel_file = _dir() / "selected.json"
    if not sel_file.exists():
        raise SystemExit("Noch keine Trader ausgewaehlt. Erst `tradingbot traders scan` ausfuehren.")
    if not selection:
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
    watch_frames: dict[str, pd.DataFrame] = {}
    min_notional = 10.0  # Hyperliquid: Mindestgroesse einer Order 10 $
    last_scan = json.loads(sel_file.read_text()).get("created", _now_ms())
    was_halted = False
    loop = 0

    live = hl.LiveFeed([t["address"] for t in selection]).start() if hl.LiveFeed.available() else None
    if live is None:
        log.warning("Paket websocket-client fehlt: nur Abfrage alle %ss (pip install websocket-client)", poll_seconds)
    live_prices: dict[str, float] = {}

    def close_label(closed):
        label = {"tp": "Ziel (TP) erreicht", "sl": "Stop (SL) ausgeloest", "trail": "Trailing Stop"}
        return (f"{closed['symbol']}: {label.get(closed['reason'], closed['reason'])} | {closed['pnl']:+.2f} $ "
                f"({closed['r_multiple']:+.2f}R) | Konto {closed['equity']:.2f} $")

    def on_live_prices():
        """Jeder Preis-Tick: offene Positionen sofort gegen SL/TP pruefen (Preis = Mitte zwischen Kauf/Verkauf)."""
        for coin in list(broker.open_positions()):
            px = live.mids.get(coin)
            if px is None:
                continue
            live_prices[coin] = px
            for closed in broker.on_price(coin, px, px, px):
                bot._log_trade({**closed, "trader": owner.get(coin, {}).get("trader", ""),
                                "opened": owner.get(coin, {}).get("opened", "")})
                _event("close", close_label(closed) + " (live)")

    def write_live():
        if live is None:
            return
        opened = broker.open_positions()
        unreal = sum((live.mids.get(c, p.entry) - p.entry) * p.side * p.contracts for c, p in opened.items())
        row = {"updated": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"), "connected": live.connected,
               "error": live.error, "equity": broker.equity(), "unrealized": unreal,
               "prices": {c: live.mids.get(c) for c in list(opened) + list(watch_frames) if c in live.mids}}
        f = _dir() / "live.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(row))
        tmp.replace(f)

    last_konto = [None, 0.0]

    def candles_15m(coin):
        df = hl.candles(coin, interval, _now_ms() - 3 * DAY_MS)
        frames[coin] = df
        return df

    import signal

    def _on_term(*_):
        _event("info", "Copy-Bot pausiert (Konto und offene Positionen bleiben gespeichert)", push=False)
        _status("Pausiert", equity=broker.equity(), open=list(broker.open_positions()))
        raise SystemExit(0)

    try:
        signal.signal(signal.SIGTERM, _on_term)
    except ValueError:  # nicht im Haupt-Thread (z. B. in Tests)
        pass
    _event("info", f"Copy-Bot gestartet (Spielgeld): kopiere {', '.join(_short(t['address']) for t in selection)} "
                    f"| Konto {broker.equity():.2f} $", push=False)

    while max_loops is None or loop < max_loops:
        loop += 1
        # Trader taeglich neu auswaehlen. Wer noch eine von uns kopierte Position hat, wird weiter beobachtet.
        if rescan_hours and _now_ms() - last_scan > rescan_hours * 3_600_000:
            _status("Wähle die Trader neu aus (dauert einige Minuten)", loop=loop)
            try:
                fresh = scan(cfg)
                if fresh:
                    selection = fresh
                    if live:
                        live.set_users([t["address"] for t in selection])
                _event("scan", f"Trader neu ausgewaehlt: {', '.join(_short(t['address']) for t in selection)}")
            except Exception as e:
                log.warning("Neuauswahl fehlgeschlagen, behalte bisherige Trader: %s", e)
            last_scan = _now_ms()
        traders = [t["address"] for t in selection]
        traders += [o["trader"] for c, o in owner.items() if c in broker.open_positions() and o["trader"] not in traders]

        rm.new_bar(datetime.now(timezone.utc).date(), broker.equity())
        if rm.halted and not was_halted:
            _event("risk", f"Tagesverlust-Limit ({cfg.risk.max_daily_loss_pct:g} %) erreicht: heute keine neuen Trades mehr")
        was_halted = rm.halted

        checks: list[dict] = []
        for i, t in enumerate(traders, 1):
            _status(f"Prüfe Trader {i}/{len(traders)}: {_short(t)}", loop=loop, equity=broker.equity(),
                    open=list(broker.open_positions()), checks=checks)
            try:
                pos = hl.positions(t)
            except Exception as e:
                log.warning("%s: %s", _short(t), e)
                continue
            prev = last.get(t)
            last[t] = pos
            opened = [c for c in pos if prev is not None and (c not in prev or (prev[c]["size"] > 0) != (pos[c]["size"] > 0))]
            closed_c = [c for c in (prev or {}) if c not in pos]
            checks.append({"trader": _short(t), "positions": len(pos),
                           "coins": [f"{c} {'L' if p['size'] > 0 else 'S'}" for c, p in list(pos.items())[:6]],
                           "new": opened, "closed": closed_c})
            if prev is None:  # Start: bestehende Positionen nicht nachkaufen, nur neue kopieren
                continue
            for coin, p in pos.items():
                if not is_perp(coin):
                    continue
                side = 1 if p["size"] > 0 else -1
                was = prev.get(coin)
                is_new = was is None or (was["size"] > 0) != (p["size"] > 0)
                if not is_new or coin in broker.open_positions() or t not in [x["address"] for x in selection]:
                    continue
                try:
                    df = candles_15m(coin)
                except Exception as e:
                    log.warning("%s Kerzen: %s", coin, e)
                    continue
                if len(df) < cfg.risk.atr_period + 2:
                    continue
                entry = float(df["close"].iloc[-1])
                step = 10 ** -decimals.get(coin, 3)
                plan = rm.plan(side, entry, float(atr(df, cfg.risk.atr_period).iloc[-1]), broker.equity(), 1.0,
                               len(broker.open_positions()), step)
                name = f"{coin} {'LONG' if side > 0 else 'SHORT'}"
                if plan is None:
                    why = ("Tageslimit erreicht" if rm.halted else
                           "schon max. Positionen offen" if len(broker.open_positions()) >= cfg.risk.max_open_positions
                           else "Konto zu klein fuer das Risiko")
                    _event("info", f"{_short(t)} eroeffnet {name}, nicht kopiert: {why}", push=False)
                    continue
                if plan.contracts * entry < min_notional:
                    _event("info", f"{_short(t)} eroeffnet {name}, nicht kopiert: Order {plan.contracts * entry:.2f} $ "
                                   f"unter Mindestgroesse {min_notional:.0f} $", push=False)
                    continue
                broker.place_bracket(coin, plan)
                owner[coin] = {"trader": t, "opened": _now_ms()}
                owner_file.write_text(json.dumps(owner))
                _event("copy", f"KOPIERT von {_short(t)}: {name} @ {entry:.4g} | SL {plan.stop_loss:.4g} | "
                               f"TP {plan.take_profit:.4g} | Menge {plan.contracts:g}")
            for coin, plan in list(broker.open_positions().items()):
                if owner.get(coin, {}).get("trader") != t:
                    continue
                p = pos.get(coin)
                if p is None or (p["size"] > 0) != (plan.side > 0):  # Trader hat geschlossen/gedreht
                    try:
                        df = candles_15m(coin)
                    except Exception as e:
                        log.warning("%s Kerzen: %s", coin, e)
                        continue
                    closed = broker.close(coin, float(df["close"].iloc[-1]), "trader_exit")
                    if closed:
                        bot._log_trade({**closed, "trader": owner.get(coin, {}).get("trader", ""), "opened": owner.get(coin, {}).get("opened", "")})
                        _event("close", f"{coin}: Trader ist ausgestiegen, wir auch | {closed['pnl']:+.2f} $ "
                                        f"({closed['r_multiple']:+.2f}R) | Konto {closed['equity']:.2f} $")
            time.sleep(0.3)
        # SL/TP pruefen: nur abgeschlossene 1m-Kerzen nach dem Einstieg, jede nur einmal
        for coin in list(broker.open_positions()):
            _status(f"Prüfe Stop-Loss und Take-Profit für {coin}", loop=loop, equity=broker.equity(),
                    open=list(broker.open_positions()), checks=checks)
            since = max(checked.get(coin, 0), owner.get(coin, {}).get("opened", _now_ms() - 10 * 60_000))
            try:
                df = hl.candles(coin, "1m", since)
                candles_15m(coin)
            except Exception as e:
                log.warning("%s Kerzen: %s", coin, e)
                continue
            if df.empty:
                continue
            ms = _ms(df.index)
            done = df[(ms + 60_000 <= _now_ms()) & (ms >= since)]
            for ts, bar in zip(_ms(done.index), done.itertuples()):
                checked[coin] = int(ts) + 60_000
                for closed in broker.on_price(coin, bar.high, bar.low, bar.close):
                    bot._log_trade({**closed, "trader": owner.get(coin, {}).get("trader", ""), "opened": owner.get(coin, {}).get("opened", "")})
                    _event("close", close_label(closed))
        # Watchlist fuer die Charts: was die kopierten Trader gerade halten (alle 5 Runden aktualisiert)
        held = {}
        for t in [x["address"] for x in selection]:
            for coin, p in last.get(t, {}).items():
                if is_perp(coin):
                    held[coin] = held.get(coin, 0) + abs(p["size"] * p["entry"])
        coins = [c for c, _ in sorted(held.items(), key=lambda kv: -kv[1]) if c not in broker.open_positions()][:4]
        if loop % 5 == 1:
            for coin in coins:
                try:
                    watch_frames[coin] = hl.candles(coin, interval, _now_ms() - 3 * DAY_MS)
                except Exception as e:
                    log.warning("%s Kerzen: %s", coin, e)
        konto = (round(broker.equity(), 2), tuple(broker.open_positions()))
        if konto != last_konto[0] or time.time() - last_konto[1] > 600:  # Log nicht bei jeder Live-Runde fluten
            log.info("Konto: %.2f | offen: %s", broker.equity(), list(broker.open_positions()) or "-")
            last_konto[:] = [konto, time.time()]
        strategies = {c: f"Kopie von {_short(owner.get(c, {}).get('trader', '?'))}" for c in broker.open_positions()}
        extra = {
            "mode": "copy", "currency": "USD", "halted": rm.halted,
            "traders": [{**x, "short": _short(x["address"]), "positions": last.get(x["address"], {})}
                        for x in selection],
            "watchlist": {c: watch_frames[c] for c in coins if c in watch_frames},
            "events": recent_events(50),
        }
        try:
            try:
                bot.write_dashboard(cfg, broker, strategies, frames, extra=extra)
            except TypeError:  # aeltere write_dashboard-Version ohne extra
                bot.write_dashboard(cfg, broker, strategies, frames)
        except Exception as e:
            log.warning("Dashboard: %s", e)
        nxt = pd.Timestamp.now(tz="UTC") + pd.Timedelta(seconds=poll_seconds)
        mode = ("live verbunden: reagiert sofort auf Trades der Trader" if live and live.connected else
                "Live-Verbindung wird aufgebaut" if live else "nur Abfrage")
        _status("Wartet auf die nächste Runde" if not (live and live.connected) else "Live: wartet auf Trades der Trader",
                loop=loop, equity=broker.equity(), open=list(broker.open_positions()),
                checks=checks, next_round=nxt.isoformat(timespec="seconds"), poll_seconds=poll_seconds,
                halted=rm.halted, traders=len(traders), live=mode)
        if max_loops is not None and loop >= max_loops:
            break
        if live is None:
            time.sleep(poll_seconds)
            continue
        # Live warten: Preis-Ticks pruefen SL/TP sofort; ein Fill eines Traders startet sofort eine neue Runde.
        deadline, earliest = time.time() + poll_seconds, time.time() + 15
        last_write, traded = 0.0, False
        import queue as _q
        while time.time() < deadline:
            try:
                ev = live.events.get(timeout=max(0.1, min(1.0, deadline - time.time())))
            except _q.Empty:
                ev = None
            if ev and ev[0] == "mids":
                on_live_prices()
            elif ev and ev[0] == "fill":
                if not traded:
                    log.info("Live: %s hat gerade gehandelt (%s)", _short(ev[1] or "?"), ", ".join(ev[2][:3]))
                traded = True
            if traded and time.time() >= earliest:  # volle Runde hoechstens alle 15 s, SL/TP laufen weiter live
                break
            if time.time() - last_write > 2:
                write_live()
                last_write = time.time()
    if live:
        live.stop()
