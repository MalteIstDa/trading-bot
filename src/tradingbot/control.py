"""Bot starten und pausieren (fuer die Knoepfe im Dashboard und die Mac-App).

Der Bot laeuft als eigener Hintergrund-Prozess weiter, auch wenn das Dashboard geschlossen wird.
Pausieren beendet den Prozess sauber; Kontostand und offene Positionen bleiben in den Dateien erhalten
und werden beim naechsten Start weitergefuehrt. Auf dem Mac verhindert `caffeinate`, dass er einschlaeft,
solange der Bot laeuft (Zuklappen pausiert trotzdem)."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from tradingbot.config import ROOT

CLI = [sys.executable, "-c", "from tradingbot.cli import main; main()"]
# Welche Bots per Knopf gestartet werden koennen: id -> (Datenordner, Befehl)
COMMANDS = {
    "copy": (ROOT / "data" / "copy", [*CLI, "traders", "watch", "--config", str(ROOT / "config" / "hyperliquid.yaml")]),
    "futures": (ROOT / "data", [*CLI, "learn", "--family", "ict"]),
    "crypto": (ROOT / "data" / "crypto", [*CLI, "learn", "--family", "momentum",
                                          "--config", str(ROOT / "config" / "crypto-swing.yaml")]),
}
LABELS = {"copy": "Copy-Bot", "futures": "Lernschleife", "crypto": "Lernschleife"}


def _pid_file(data: Path) -> Path:
    return data / "bot.pid"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # Pruefen, dass die PID wirklich unser Bot ist (PIDs werden nach einem Neustart neu vergeben)
    try:
        cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return True
    return "tradingbot" in cmd or "trading-bot" in cmd


def status(bot_id: str) -> dict:
    if bot_id not in COMMANDS:
        return {"bot": bot_id, "controllable": False, "running": False}
    data = COMMANDS[bot_id][0]
    info = {}
    try:
        info = json.loads(_pid_file(data).read_text())
    except (OSError, ValueError):
        pass
    running = bool(info.get("pid")) and _alive(int(info["pid"]))
    return {"bot": bot_id, "controllable": True, "running": running,
            "pid": info.get("pid") if running else None, "started": info.get("started") if running else None,
            "stopped": info.get("stopped")}


def start(bot_id: str) -> dict:
    if bot_id not in COMMANDS:
        raise ValueError(f"Bot {bot_id!r} kann nicht per Knopf gestartet werden")
    st = status(bot_id)
    if st["running"]:
        return st
    data, args = COMMANDS[bot_id]
    data.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TRADINGBOT_DATA": str(data), "PYTHONUNBUFFERED": "1"}
    log = (data / "bot.log").open("a")
    log.write(f"\n===== Start {datetime.now().isoformat(timespec='seconds')} =====\n")
    log.flush()
    proc = subprocess.Popen(args,
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            start_new_session=True)  # laeuft weiter, auch wenn das Dashboard beendet wird
    if shutil.which("caffeinate"):  # Mac: wach bleiben, solange der Bot laeuft; endet automatisch mit ihm
        subprocess.Popen(["caffeinate", "-i", "-w", str(proc.pid)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    _pid_file(data).write_text(json.dumps({"pid": proc.pid, "started": _now()}))
    time.sleep(1.5)
    if proc.poll() is not None:
        raise RuntimeError(f"Bot ist sofort beendet worden (Code {proc.returncode}), siehe {data / 'bot.log'}")
    return status(bot_id)


def stop(bot_id: str, timeout: float = 15) -> dict:
    st = status(bot_id)
    if not st.get("running"):
        return st
    data = COMMANDS[bot_id][0]
    pid = int(st["pid"])
    os.kill(pid, signal.SIGTERM)
    end = time.time() + timeout
    while time.time() < end and _alive(pid):
        time.sleep(0.3)
    if _alive(pid):
        os.kill(pid, signal.SIGKILL)
    stopped = _now()
    _pid_file(data).write_text(json.dumps({"pid": None, "stopped": stopped, "last_started": st.get("started")}))
    try:  # Bericht ueber die Laufzeit; darf das Pausieren nie verhindern
        from tradingbot import report
        report.save(report.build(bot_id, LABELS.get(bot_id, bot_id), data, st.get("started"), stopped), data)
    except Exception:
        pass
    return status(bot_id)


def log_tail(bot_id: str, lines: int = 80) -> list[str]:
    if bot_id not in COMMANDS:
        return []
    f = COMMANDS[bot_id][0] / "bot.log"
    if not f.exists():
        return []
    with f.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - 64_000))
        text = fh.read().decode("utf-8", "replace")
    return text.splitlines()[-lines:]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
