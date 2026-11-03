"""Trader bewerten, Copy-Signale ableiten und ehrlich testen.

Grundidee: Ein Trader ist nur dann interessant, wenn
  1. er ueber viele Tage konstant Gewinn macht (nicht ein Gluckstreffer),
  2. er nicht zu oft handelt (Market Maker / Hochfrequenz kann man nicht kopieren),
  3. seine Trades auch NACH der Auswahl und MIT Verzoegerung beim Kopieren noch Gewinn bringen.
Punkt 3 prueft der Walk-Forward-Test: Auswahl nur mit vergangenen Daten, Test nur mit spaeteren."""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from tradingbot.config import Config
from tradingbot.risk.manager import RiskManager
from tradingbot.strategies.base import atr

DAY_MS = 86_400_000


# ---------- 1) Vorauswahl aus dem Leaderboard ----------

def prefilter(rows: list[dict], min_account: float = 50_000, max_turnover: float = 150, top: int = 40) -> list[dict]:
    """Kandidaten: grosses Konto, Gewinn im Monat UND insgesamt, kein Hochfrequenz-Handel.
    turnover = Monatsvolumen / Kontowert (Market Maker liegen oft bei mehreren hundert)."""
    out = []
    for r in rows:
        m, a = r["perf"].get("month", {}), r["perf"].get("allTime", {})
        av = r["account_value"]
        if av < min_account or m.get("pnl", 0) <= 0 or a.get("pnl", 0) <= 0:
            continue
        turnover = m.get("vlm", 0) / av if av else math.inf
        if turnover > max_turnover:
            continue
        out.append({**r, "turnover": turnover})
    out.sort(key=lambda r: r["perf"]["month"]["pnl"], reverse=True)
    return out[:top]


# ---------- 2) Bewertung anhand echter Fills ----------

def daily_pnl(fills: list[dict], start_ms: int, end_ms: int) -> pd.Series:
    """Realisierter Gewinn pro Tag (closedPnl minus Gebuehren)."""
    days = pd.date_range(pd.to_datetime(start_ms, unit="ms", utc=True).normalize(),
                         pd.to_datetime(end_ms, unit="ms", utc=True).normalize(), freq="D")
    s = pd.Series(0.0, index=days)
    for f in fills:
        if start_ms <= f["time"] <= end_ms:
            d = pd.to_datetime(f["time"], unit="ms", utc=True).normalize()
            s[d] = s.get(d, 0.0) + float(f.get("closedPnl", 0)) - float(f.get("fee", 0))
    return s


@dataclass
class TraderScore:
    address: str
    pnl: float
    days: int
    active_days: int
    profitable_days: float     # Anteil Tage mit Gewinn (nur aktive Tage)
    profitable_weeks: float
    t_stat: float              # Mittelwert / Streuung * Wurzel(Tage): > 2 heisst "eher kein Zufall"
    max_drawdown: float        # groesster Rueckgang des kumulierten Gewinns (in USD)
    opens_per_day: float
    score: float

    def to_dict(self):
        return asdict(self)


def score_trader(address: str, fills: list[dict], start_ms: int, end_ms: int) -> TraderScore:
    d = daily_pnl(fills, start_ms, end_ms)
    active = d[d != 0]
    weeks = d.resample("W").sum()
    weeks = weeks[weeks != 0]
    std = d.std()
    t = (d.mean() / std * math.sqrt(len(d))) if std and std > 0 else 0.0
    cum = d.cumsum()
    dd = float((cum.cummax() - cum).max()) if len(cum) else 0.0
    opens = len(open_signals(fills, start_ms, end_ms))
    n_days = max(len(d), 1)
    opd = opens / n_days
    pw = float((weeks > 0).mean()) if len(weeks) else 0.0
    # Score: statistische Sicherheit, belohnt konstante Wochen, bestraft zu viel Handel
    score = t * (0.5 + pw) * (1.0 if opd <= 20 else 20 / opd)
    return TraderScore(address, float(d.sum()), n_days, int(len(active)),
                       float((active > 0).mean()) if len(active) else 0.0, pw, float(t), dd, opd, float(score))


# ---------- 3) Copy-Signale ----------

def is_perp(coin: str) -> bool:
    """Nur normale Perpetuals kopieren. '@107' / 'PURR/USDC' sind Spot, 'xyz:...' sind HIP-3-Maerkte."""
    return not (coin.startswith("@") or "/" in coin or ":" in coin)


def spot_share(fills: list[dict]) -> float:
    """Anteil des Handelsvolumens auf Spot. Hoch = oft ein Hedge (Spot long + Perp short), nicht kopierbar."""
    spot = total = 0.0
    for f in fills:
        n = abs(float(f["sz"]) * float(f["px"]))
        total += n
        if not is_perp(f["coin"]):
            spot += n
    return spot / total if total else 0.0


def _flat(x: float, scale: float) -> bool:
    return abs(x) <= 1e-6 * max(scale, 1e-12)


def position_events(fills: list[dict]) -> list[dict]:
    """Rekonstruiert die Position je Coin selbst, statt startPosition jedes Fills zu vertrauen
    (bei grossen Orders bleibt startPosition ueber viele Teil-Fills gleich).
    Teil-Fills derselben Order (oid) werden zu einem Ereignis zusammengefasst."""
    events: list[dict] = []
    pos: dict[str, float] = {}
    cur = None
    for f in sorted(fills, key=lambda x: (x["time"], str(x.get("tid", "")))):
        coin = f["coin"]
        if not is_perp(coin):
            continue
        signed = float(f["sz"]) * (1 if f["side"] == "B" else -1)
        px = float(f["px"])
        if cur and cur["coin"] == coin and f.get("oid") is not None and cur["oid"] == f.get("oid"):
            cur["size"] += signed
            cur["notional"] += abs(signed) * px
            continue
        if cur:
            events.append(cur)
        cur = {"time": f["time"], "coin": coin, "oid": f.get("oid"), "size": signed, "notional": abs(signed) * px,
               "start": float(f.get("startPosition", 0))}
    if cur:
        events.append(cur)
    out = []
    for e in events:
        before = pos.get(e["coin"], e["start"])  # erste Sicht auf den Coin: Startposition der API
        after = before + e["size"]
        pos[e["coin"]] = after
        scale = max(abs(before), abs(e["size"]))
        out.append({"time": e["time"], "coin": e["coin"], "before": 0.0 if _flat(before, scale) else before,
                    "after": 0.0 if _flat(after, scale) else after,
                    "px": e["notional"] / abs(e["size"]) if e["size"] else 0.0})
    return out


def open_signals(fills: list[dict], start_ms: int = 0, end_ms: int | None = None) -> list[dict]:
    """Neue Positionen (vorher flach -> Long/Short) und Positionswechsel.
    Nachkaeufe in eine bestehende Position zaehlen NICHT als neues Signal."""
    out = []
    for e in position_events(fills):
        if e["time"] < start_ms or (end_ms and e["time"] > end_ms):
            continue
        b, a = e["before"], e["after"]
        if (b == 0 and a != 0) or (b * a < 0):
            out.append({"time": e["time"], "coin": e["coin"], "side": 1 if a > 0 else -1, "px": e["px"]})
    return out


def close_times(fills: list[dict]) -> dict[str, list[tuple[int, int, float]]]:
    """Wann ein Trader eine Position komplett schliesst oder dreht: {coin: [(zeit, seite_vorher, preis)]}"""
    out: dict[str, list] = {}
    for e in position_events(fills):
        b, a = e["before"], e["after"]
        if b != 0 and (a == 0 or b * a < 0):
            out.setdefault(e["coin"], []).append((e["time"], 1 if b > 0 else -1, e["px"]))
    return out


# ---------- 4) Kopieren simulieren ----------

@dataclass
class CopyTrade:
    trader: str
    coin: str
    side: int
    signal_time: int
    entry_time: str
    entry: float
    exit: float
    reason: str
    r_multiple: float
    slippage_pct: float  # angenommener Aufschlag gegenueber dem Preis des Traders (in %)


def simulate_copy(signals: list[dict], closes: dict, candles: dict[str, pd.DataFrame], cfg: Config,
                  trader: str, delay_ms: int = 60_000, follow_exit: bool = True,
                  slippage_pct: float = 0.05) -> list[CopyTrade]:
    """Kopiert jedes Signal `delay_ms` spaeter zum Preis des Traders plus `slippage_pct` Aufschlag
    (Annahme fuer Kursbewegung in der Verzoegerung). SL/TP prueft erst ab der naechsten vollen Kerze,
    damit keine Kurse von VOR dem Einstieg zaehlen. SL/TP setzt unser Risk Manager (RR 1:3).
    follow_exit: zusaetzlich aussteigen, wenn der Trader schliesst (zu seinem Preis plus Aufschlag)."""
    rm = RiskManager(cfg.risk)
    out = []
    slip = slippage_pct / 100
    for s in signals:
        df = candles.get(s["coin"])
        if df is None or df.empty:
            continue
        t_entry_ms = s["time"] + delay_ms
        starts = ((df.index - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)).to_numpy()  # unabhaengig von ns/ms
        after = np.nonzero(starts >= t_entry_ms)[0]
        if not len(after):
            continue
        i0 = int(after[0])
        if i0 < cfg.risk.atr_period + 1:
            continue
        a = atr(df.iloc[:i0], cfg.risk.atr_period).iloc[-1]
        entry = s["px"] * (1 + s["side"] * slip)
        plan = rm.plan(s["side"], entry, float(a), 1e9, 1.0, 0, 1e-12)  # Groesse egal, wir rechnen in R
        if plan is None:
            continue
        exit_ms = exit_px_trader = None
        if follow_exit:
            nxt = [(t, px) for t, side, px in closes.get(s["coin"], []) if t > s["time"] and side == s["side"]]
            if nxt:
                exit_ms, exit_px_trader = nxt[0][0] + delay_ms, nxt[0][1]
        reason, exit_px = "end", float(df["close"].iloc[-1])
        for i in range(i0, len(df)):
            if exit_ms is not None and starts[i] >= exit_ms:
                reason, exit_px = "trader_exit", exit_px_trader * (1 - s["side"] * slip)
                break
            bar = df.iloc[i]
            res = plan.check_exit(float(bar["high"]), float(bar["low"]))
            if res:
                reason, exit_px = res
                break
            plan.trail(float(bar["high"]), float(bar["low"]))
        fees = cfg.fee_pct / 100 * (entry + exit_px)
        r = ((exit_px - entry) * s["side"] - fees) / plan.initial_risk
        out.append(CopyTrade(trader, s["coin"], s["side"], s["time"], str(df.index[i0]), entry, exit_px, reason,
                             float(r), slippage_pct))
    return out


def summarize_r(trades: list[CopyTrade]) -> dict:
    if not trades:
        return {"trades": 0, "win_rate": 0.0, "expectancy_r": 0.0, "profit_factor": 0.0, "avg_slippage_pct": 0.0}
    r = np.array([t.r_multiple for t in trades])
    gains, losses = r[r > 0].sum(), -r[r <= 0].sum()
    return {"trades": len(r), "win_rate": float((r > 0).mean()), "expectancy_r": float(r.mean()),
            "profit_factor": float(gains / losses) if losses > 0 else float("inf"),
            "avg_slippage_pct": float(np.mean([t.slippage_pct for t in trades]))}
