"""Optional: wichtige Ereignisse automatisch ins Obsidian-Logbuch schreiben (neueste oben).

In `.env` im Ordner trading-bot (wird NICHT hochgeladen) den Pfad zur Logbuch-Notiz setzen, z. B.
    OBSIDIAN_LOGBOOK=/Users/<name>/Documents/Projekte/Trading-Bot/Trading-Bot – Logbuch.md
Ohne diese Angabe macht das Modul nichts. Fehler beim Schreiben stoppen den Bot nie.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from pathlib import Path

from tradingbot.config import ROOT

log = logging.getLogger("tradingbot")
_lock = threading.Lock()
MARKER = "<!-- bot-eintraege -->"


def _path() -> Path | None:
    value = os.environ.get("OBSIDIAN_LOGBOOK")
    f = ROOT / ".env"
    if not value and f.exists():
        for line in f.read_text().splitlines():
            if line.strip().startswith("OBSIDIAN_LOGBOOK="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
    return Path(value).expanduser() if value else None


def add(text: str, path: Path | None = None) -> bool:
    """Fuegt '- 2026-11-04 17:05 | text' oben ins Logbuch ein (unter der Markierung bzw. der ersten Ueberschrift)."""
    path = path or _path()
    if path is None or not path.parent.exists():
        return False
    line = f"- {datetime.now().strftime('%Y-%m-%d %H:%M')} | {text}"
    try:
        with _lock:
            old = path.read_text(encoding="utf-8") if path.exists() else "# Trading-Bot – Logbuch\n"
            if MARKER not in old:
                lines = old.splitlines()
                at = next((i + 1 for i, x in enumerate(lines) if x.startswith("# ")), 0)
                lines[at:at] = ["", "## Automatisch vom Bot", MARKER]
                old = "\n".join(lines) + "\n"
            new = old.replace(MARKER, MARKER + "\n" + line, 1)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(new, encoding="utf-8")
            tmp.replace(path)
        return True
    except OSError as e:
        log.warning("Obsidian-Logbuch nicht geschrieben: %s", e)
        return False
