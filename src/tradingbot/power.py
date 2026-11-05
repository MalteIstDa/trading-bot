"""Leistungsstufe fuer die Lernschleifen, im Dashboard einstellbar.

Gespeichert in data/power.json. Die laufenden Lernschleifen lesen die Stufe vor jeder Runde neu,
eine Aenderung greift also ohne Neustart (spaetestens nach der laufenden Runde).
Die Stufe startet nie etwas: sie wirkt nur, wenn eine Lernschleife ohnehin laeuft.
Kerne gelten pro Lernschleife (laufen Futures und Krypto zusammen, zaehlen beide)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from tradingbot.config import ROOT

FILE = ROOT / "data" / "power.json"
DEFAULT = "normal"


def _cpu() -> int:
    return os.cpu_count() or 2


def levels() -> dict[str, dict]:
    n = _cpu()
    return {
        "eco": {"label": "Sparsam", "workers": 1, "new": 20, "pause_min": 10,
                "hint": "1 Kern, 10 Min. Pause: kaum spürbar, Mac bleibt kühl"},
        "normal": {"label": "Normal", "workers": max(1, min(3, n // 4)), "new": 40, "pause_min": 2,
                   "hint": "wenige Kerne, 2 Min. Pause: läuft nebenbei"},
        "high": {"label": "Hoch", "workers": max(1, n // 3), "new": 80, "pause_min": 1,
                 "hint": "ein Drittel der Kerne: Mac wird warm, besser am Netzteil"},
        "max": {"label": "Maximal", "workers": max(1, n // 2), "new": 120, "pause_min": 0.5,
                "hint": "halbe Kerne pro Schleife: volle Leistung, nur am Netzteil"},
    }


def current(path: Path | None = None) -> dict:
    path = path or FILE
    level = DEFAULT
    try:
        level = json.loads(path.read_text()).get("level", DEFAULT)
    except (OSError, ValueError, AttributeError):
        pass
    lv = levels()
    if level not in lv:
        level = DEFAULT
    return {"level": level, **lv[level]}


def set_level(level: str, path: Path | None = None) -> dict:
    if level not in levels():
        raise ValueError(f"Unbekannte Stufe {level!r}")
    path = path or FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"level": level}))
    tmp.replace(path)
    return current(path)


def changed_since(stamp: float, path: Path | None = None) -> bool:
    try:
        return (path or FILE).stat().st_mtime > stamp
    except OSError:
        return False
