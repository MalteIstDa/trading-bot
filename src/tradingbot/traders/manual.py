"""Manuelles Schliessen aus dem Dashboard: das Dashboard legt eine Anfrage ab, der laufende Copy-Bot schliesst
die Position beim naechsten Preis-Tick (spaetestens nach etwa einer Sekunde) zum aktuellen Kurs."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

NAME = "close_requests.json"


def request(traders_dir: Path, coin: str) -> list[str]:
    f = Path(traders_dir) / NAME
    f.parent.mkdir(parents=True, exist_ok=True)
    coins = pending(traders_dir)
    if coin not in coins:
        coins.append(coin)
    tmp = f.with_name(f"{NAME}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(coins))
    tmp.replace(f)
    return coins


def pending(traders_dir: Path) -> list[str]:
    try:
        v = json.loads((Path(traders_dir) / NAME).read_text())
        return [str(c) for c in v] if isinstance(v, list) else []
    except (OSError, ValueError):
        return []


def take(traders_dir: Path) -> list[str]:
    """Anfragen abholen und loeschen (nur der Bot ruft das auf)."""
    f = Path(traders_dir) / NAME
    coins = pending(traders_dir)
    if coins:
        try:
            f.unlink()
        except OSError:
            pass
    return coins
