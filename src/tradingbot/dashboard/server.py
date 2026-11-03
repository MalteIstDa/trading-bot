"""Lokales Live-Dashboard: http://localhost:8050
Liest nur die Dateien, die `tradingbot paper` schreibt. Kein Zugriff von aussen (nur 127.0.0.1)."""
from __future__ import annotations

import csv
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tradingbot import bot

INDEX = Path(__file__).with_name("index.html")
CHARTS_JS = Path(__file__).parent / "vendor" / "lightweight-charts.js"  # lokal, damit es auch offline laeuft


def _read_csv(path: Path, limit: int) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[-limit:]


def build_state() -> dict:
    state = json.loads(bot.DASHBOARD_JSON.read_text()) if bot.DASHBOARD_JSON.exists() else {}
    trades = _read_csv(bot.TRADES_CSV, 500)
    equity = [{"time": r["time"], "equity": float(r["equity"])} for r in _read_csv(bot.EQUITY_CSV, 5000)]
    pnl = [float(t["pnl"]) for t in trades]
    rs = [float(t["r_multiple"]) for t in trades if t.get("r_multiple") not in (None, "")]
    wins = [p for p in pnl if p > 0]
    losses = [-p for p in pnl if p <= 0]
    state["trades"] = list(reversed(trades[-100:]))
    state["equity_curve"] = equity
    state["stats"] = {
        "trades": len(pnl),
        "win_rate": len(wins) / len(pnl) if pnl else None,
        "profit_factor": (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else None,
        "expectancy_r": (sum(rs) / len(rs)) if rs else None,
        "net_pnl": sum(pnl),
    }
    return state


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/state"):
            self._send(200, json.dumps(build_state()).encode(), "application/json")
        elif self.path == "/vendor/lightweight-charts.js":
            self._send(200, CHARTS_JS.read_bytes(), "application/javascript")
        elif self.path in ("/", "/index.html"):
            self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, *args):  # keine Log-Flut im Terminal
        pass


def serve(port: int = 8050, open_browser: bool = True) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://localhost:{port}"
    print(f"Dashboard laeuft auf {url}  (beenden mit Ctrl + C)")
    if not bot.DASHBOARD_JSON.exists():
        print("Hinweis: Noch keine Daten. Starte in einem anderen Fenster `./paper-test.sh` oder `tradingbot paper`.")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
