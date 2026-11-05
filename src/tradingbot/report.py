"""Bericht nach jedem Pausieren: was ist zwischen Start und Pause genau passiert?

Wird von control.stop() geschrieben (data/<bot>/reports/<zeit>.json) und im Dashboard als Bericht angezeigt."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path


def _ts(v) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _jsonl(path: Path) -> list[dict]:
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _log_issues(log: Path, started: datetime) -> dict:
    """Warnungen/Fehler im Protokoll seit dem letzten Start."""
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"warnings": 0, "errors": 0, "last": []}
    marker = text.rfind("===== Start ")
    part = text[marker:] if marker >= 0 else text[-200_000:]
    lines = part.splitlines()
    warn = [x for x in lines if "WARNING" in x]
    err = [x for x in lines if "Traceback" in x or "ERROR" in x]
    return {"warnings": len(warn), "errors": len(err), "last": (err or warn)[-3:]}


def build(bot_id: str, label: str, data: Path, started: str | None, stopped: str) -> dict:
    t0, t1 = _ts(started), _ts(stopped)
    rep = {"bot": bot_id, "label": label, "started": started, "stopped": stopped,
           "duration_s": int((t1 - t0).total_seconds()) if t0 and t1 else None, "highlights": []}
    inside = (lambda t: t is not None and (t0 is None or t >= t0) and t <= t1)

    # Trades (Copy-Bot / Paper-Bot)
    trades = []
    f = data / "paper_trades.csv"
    if f.exists():
        with f.open(newline="") as fh:
            trades = [r for r in csv.DictReader(fh) if inside(_ts(r.get("time")))]
    if trades or (data / "traders").exists():
        pnl = sum(_f(r.get("pnl")) for r in trades)
        wins = [r for r in trades if _f(r.get("pnl")) > 0]
        rs = [_f(r.get("r_multiple")) for r in trades]
        rep["trading"] = {
            "closed": len(trades), "wins": len(wins), "pnl": pnl, "sum_r": sum(rs),
            "best": max(trades, key=lambda r: _f(r.get("pnl")), default=None),
            "worst": min(trades, key=lambda r: _f(r.get("pnl")), default=None),
            "equity_end": _f(trades[-1].get("equity"), None) if trades else None,
            "trades": [{k: r.get(k) for k in ("time", "symbol", "side", "reason", "pnl", "r_multiple", "trader")}
                       for r in trades[-20:]]}
        acc, curve = 0.0, [0.0]
        for r in trades:
            acc += _f(r.get("pnl"))
            curve.append(round(acc, 4))
        rep["trading"]["curve"] = curve
        try:
            st = json.loads((data / "paper_state.json").read_text())
            rep["trading"]["equity_end"] = st.get("equity", rep["trading"]["equity_end"])
            rep["trading"]["open"] = list((st.get("positions") or {}).keys())
        except (OSError, ValueError, AttributeError):
            pass
        events = [e for e in _jsonl(data / "traders" / "events.jsonl") if inside(_ts(e.get("time")))]
        kinds = {}
        for e in events:
            kinds[e.get("type", "info")] = kinds.get(e.get("type", "info"), 0) + 1
        rep["events"] = {"count": len(events), "by_type": kinds, "recent": events[-12:]}
        if trades:
            rep["highlights"].append(f"{len(trades)} Trades geschlossen, {len(wins)} im Plus, "
                                     f"{pnl:+.2f} $ ({sum(rs):+.2f}R)")
        else:
            rep["highlights"].append("Keine Trades geschlossen")
        if kinds.get("copy"):
            rep["highlights"].append(f"{kinds['copy']} neue Positionen kopiert")

    # Lernschleife
    hist = [h for h in _jsonl(data / "research" / "history.jsonl") if inside(_ts(h.get("time")))]
    if hist:
        first, last = hist[0], hist[-1]
        before = [h for h in _jsonl(data / "research" / "history.jsonl") if (_ts(h.get("time")) or t1) < (t0 or t1)]
        prev = (before[-1].get("search_tried") or 0) if before else 0
        session = 0
        for h in hist:  # faellt der Zaehler (Suche neu begonnen, z. B. andere Kerzengroesse), zaehlt er ab 0 weiter
            cur = h.get("search_tried") or 0
            session += cur - prev if cur >= prev else cur
            prev = cur
        rep["learning"] = {
            "rounds": len(hist), "tried_total": last.get("search_tried"),
            "tried_session": session,
            "best": last.get("search_best"), "best_holdout_r": last.get("search_holdout_r"),
            "promising": last.get("search_promising"), "passed": any(h.get("search_passed") or h.get("passed") for h in hist),
            "bars": last.get("bars"), "oos": last.get("oos"), "random_beaten": last.get("random_beaten"),
            "curve": [h.get("search_tried") or 0 for h in hist][-200:]}
        L = rep["learning"]
        rep["highlights"].append(f"{L['rounds']} Lernrunden, " + f"{L['tried_session']:,}".replace(",", ".") + " neue Varianten getestet")
        rep["highlights"].append("Strategie gefunden, die die Prüfung besteht!" if L["passed"]
                                 else "Noch keine Strategie hat die Prüfung bestanden")
    try:
        found = json.loads((data / "research" / "found.json").read_text())
        if inside(_ts(found.get("time"))):
            rep["found"] = found
    except (OSError, ValueError):
        pass

    rep["log"] = _log_issues(data / "bot.log", t0) if t0 else {"warnings": 0, "errors": 0, "last": []}
    if rep["log"]["errors"]:
        rep["highlights"].append(f"{rep['log']['errors']} Fehler im Protokoll")
    return rep


def save(rep: dict, data: Path) -> Path:
    folder = data / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    name = (rep.get("stopped") or "bericht").replace(":", "-") + ".json"
    path = folder / name
    path.write_text(json.dumps(rep, default=str, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def latest(data: Path, n: int = 20) -> list[dict]:
    folder = data / "reports"
    out = []
    for p in sorted(folder.glob("*.json"), reverse=True)[:n] if folder.exists() else []:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return out
