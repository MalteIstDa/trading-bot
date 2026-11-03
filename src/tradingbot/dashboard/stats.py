"""Kennzahlen fuer das Dashboard. Reine Funktionen ohne Dateizugriff, damit sie leicht zu testen sind."""
from __future__ import annotations

R_BIN = 0.5
R_MIN, R_MAX = -2.0, 3.0  # alles darueber oder darunter landet im Randbereich


def _f(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def _profit_factor(pnl: list[float]) -> float | None:
    gains, losses = sum(p for p in pnl if p > 0), -sum(p for p in pnl if p <= 0)
    return gains / losses if losses > 0 else None


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _group(trades: list[dict], key: str) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for t in trades:
        k = t.get(key)
        if k not in (None, ""):
            groups.setdefault(str(k), []).append(t)
    out = []
    for k, rows in groups.items():
        pnl = [t["_pnl"] for t in rows]
        rs = [t["_r"] for t in rows if t["_r"] is not None]
        out.append({"key": k, "trades": len(rows), "win_rate": sum(p > 0 for p in pnl) / len(pnl),
                    "pnl": sum(pnl), "expectancy_r": _mean(rs), "profit_factor": _profit_factor(pnl)})
    return sorted(out, key=lambda g: g["pnl"], reverse=True)


def r_histogram(rs: list[float]) -> list[dict]:
    """Verteilung der Trades in R, in Schritten von 0,5 R. Die Raender sammeln alle Ausreisser."""
    n = int((R_MAX - R_MIN) / R_BIN)
    counts = [0] * n
    for r in rs:
        counts[min(n - 1, max(0, int((r - R_MIN) // R_BIN)))] += 1
    return [{"from": R_MIN + i * R_BIN, "to": R_MIN + (i + 1) * R_BIN, "count": c} for i, c in enumerate(counts)]


def trade_stats(trades: list[dict]) -> dict:
    rows = []
    for t in trades:
        pnl = _f(t.get("pnl"))
        if pnl is not None:
            rows.append({**t, "_pnl": pnl, "_r": _f(t.get("r_multiple"))})
    pnl = [t["_pnl"] for t in rows]
    rs = [t["_r"] for t in rows if t["_r"] is not None]
    wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p <= 0]
    streak = worst_streak = 0
    for p in pnl:
        streak = 0 if p > 0 else streak + 1
        worst_streak = max(worst_streak, streak)
    daily: dict[str, float] = {}
    for t in rows:
        day = str(t.get("time", ""))[:10]
        if day:
            daily[day] = daily.get(day, 0.0) + t["_pnl"]

    def pick(row: dict | None) -> dict | None:
        return None if row is None else {"symbol": row.get("symbol"), "pnl": row["_pnl"], "r": row["_r"],
                                         "time": row.get("time")}

    return {
        "trades": len(pnl),
        "win_rate": len(wins) / len(pnl) if pnl else None,
        "profit_factor": _profit_factor(pnl),
        "expectancy_r": _mean(rs),
        "net_pnl": sum(pnl),
        "avg_win": _mean(wins),
        "avg_loss": _mean(losses),
        "best": pick(max(rows, key=lambda t: t["_pnl"], default=None)),
        "worst": pick(min(rows, key=lambda t: t["_pnl"], default=None)),
        "max_loss_streak": worst_streak,
        "by_symbol": _group(rows, "symbol"),
        "by_reason": _group(rows, "reason"),
        "by_trader": _group(rows, "trader"),
        "r_histogram": r_histogram(rs),
        "daily_pnl": [{"date": d, "pnl": daily[d]} for d in sorted(daily)],
    }


def equity_stats(curve: list[dict], start: float | None) -> dict:
    """Maximaler Rueckgang vom bisherigen Hoechststand, in Prozent und in $."""
    peak = start or 0.0
    max_pct = max_abs = current = 0.0
    points = []
    for p in curve:
        eq = p["equity"]
        peak = max(peak, eq)
        current = (peak - eq) / peak if peak > 0 else 0.0
        max_pct = max(max_pct, current)
        max_abs = max(max_abs, peak - eq)
        points.append({"time": p["time"], "drawdown": -current})
    return {"max_drawdown_pct": max_pct, "max_drawdown": max_abs, "current_drawdown_pct": current,
            "peak": peak, "drawdown_curve": points}


def thin(rows: list, limit: int) -> list:
    """Duennt lange Verlaeufe gleichmaessig aus. Der letzte Punkt bleibt immer erhalten."""
    if len(rows) <= limit:
        return rows
    step = len(rows) / (limit - 1)
    return [rows[int(i * step)] for i in range(limit - 1)] + [rows[-1]]
