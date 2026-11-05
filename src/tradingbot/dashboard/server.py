"""Lokales Live-Dashboard: http://localhost:8050 (nur 127.0.0.1, kein Zugriff von aussen).
Liest die Dateien der Paper-Bots. Fuer Charts und die Positionen der kopierten Trader fragt es zusaetzlich die
oeffentliche Hyperliquid-API ab, nur lesend. Es kann selbst keine Orders ausloesen, nur den Paper-Bot
starten und pausieren (POST /api/control, nur von dieser Seite aus)."""
from __future__ import annotations

import csv
import json
import re
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from tradingbot import bot, control
from tradingbot.config import ROOT
from tradingbot.dashboard.stats import equity_stats, thin, trade_stats
from tradingbot.traders import hyperliquid as hl

INDEX = Path(__file__).with_name("index.html")
CHARTS_JS = Path(__file__).parent / "vendor" / "lightweight-charts.js"  # lokal, damit es auch offline laeuft
BOTS = {"futures": ("Futures", ROOT / "data"), "crypto": ("Krypto", ROOT / "data" / "crypto"),
        "copy": ("Copy-Trading", ROOT / "data" / "copy")}
COIN_RE = re.compile(r"^[A-Za-z0-9:_@.-]{1,24}$")
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
CANDLE_BARS = 300
CACHE_SECONDS = 30
_cache: dict[tuple, tuple[float, object]] = {}
_lock = threading.Lock()


def _cached(key: tuple, fn):
    """Haelt API-Antworten kurz vor, damit mehrere offene Tabs Hyperliquid nicht unnoetig belasten."""
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    value = fn()
    with _lock:
        _cache[key] = (now, value)
    return value


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def data_dir(name: str | None) -> Path:
    """Unbekannte Namen fallen auf den Ordner zurueck, mit dem das Dashboard gestartet wurde."""
    return BOTS[name][1] if name in BOTS else bot.DATA


def current_bot() -> str | None:
    return next((k for k, (_, path) in BOTS.items() if path.resolve() == bot.DATA.resolve()), None)


def available_bots() -> list[dict]:
    current = current_bot()
    return [{"id": k, "label": label} for k, (label, path) in BOTS.items()
            if k == current or k in control.COMMANDS or (path / bot.DASHBOARD_JSON.name).exists()]


def _test_by_trader(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        try:
            value = float(r["r_multiple"])
        except (KeyError, TypeError, ValueError):
            continue
        t = out.setdefault(r.get("trader", ""), {"test_trades": 0, "test_sum_r": 0.0})
        t["test_trades"] += 1
        t["test_sum_r"] += value
    for t in out.values():
        t["test_expectancy_r"] = t.pop("test_sum_r") / t["test_trades"]
    return out


def copy_info(data: Path, live_traders: list | None = None) -> dict | None:
    """Ausgewaehlte Trader, ihr Abschneiden im Copy-Test und das letzte Walk-Forward-Ergebnis."""
    folder = data / "traders"
    selected = _read_json(folder / "selected.json")
    test = _read_json(folder / "test_result.json")
    if selected is None and test is None:
        return None
    tested = _test_by_trader(_read_csv(folder / "copy_trades.csv"))
    live = {t.get("address"): t for t in (live_traders or []) if isinstance(t, dict)}
    traders = [{**t, **live.get(t.get("address"), {}), **tested.get(t.get("address"), {})}
               for t in (selected or {}).get("traders", [])]
    chosen = {t.get("address") for t in traders}
    others = sorted(({"address": a, **v} for a, v in tested.items() if a not in chosen),
                    key=lambda t: t["test_trades"] * t["test_expectancy_r"], reverse=True)
    return {"selected_at": (selected or {}).get("created"), "traders": traders, "tested_only": others,
            "test": test, "owners": _read_json(folder / "copy_owner.json", {})}


def _events(state: dict, trades: list[dict]) -> list[dict]:
    events = [e for e in state.get("events") or [] if isinstance(e, dict) and e.get("text")]
    if events:
        return sorted(events, key=lambda e: str(e.get("time", "")), reverse=True)[:50]
    return [{"time": t.get("time"), "type": "close",  # Ohne Feed vom Bot: abgeschlossene Trades zeigen
             "text": f"{t.get('symbol')} geschlossen ({t.get('reason')}), {float(t.get('pnl') or 0):+.2f} $"}
            for t in reversed(trades[-20:])]


def build_state(name: str | None = None) -> dict:
    data = data_dir(name)
    state = _read_json(data / bot.DASHBOARD_JSON.name, {}) or {}
    trades = _read_csv(data / bot.TRADES_CSV.name)
    equity = [{"time": r["time"], "equity": float(r["equity"])} for r in _read_csv(data / bot.EQUITY_CSV.name)]
    eq_stats = equity_stats(equity, state.get("start_capital"))
    state["drawdown_curve"] = thin(eq_stats.pop("drawdown_curve"), 1500)
    state["equity_curve"] = thin(equity, 1500)
    state["trades"] = list(reversed(trades[-500:]))
    state["stats"] = {**trade_stats(trades), **eq_stats}
    state["events"] = _events(state, trades)
    state["copy"] = copy_info(data, state.pop("traders", None))
    state["bots"] = available_bots()
    state["bot"] = name if name in BOTS else current_bot()
    state["control"] = control.status(state["bot"] or "")
    state["activity"] = _read_json(data / "traders" / "status.json")
    state["live"] = _read_json(data / "traders" / "live.json")
    research = _read_json(data / "research" / "latest.json")
    learn_status = _read_json(data / "research" / "status.json")
    if research or learn_status:
        state["research"] = {"result": research, "status": learn_status}
        if state["bot"] != "copy":
            state["activity"] = learn_status
    state["control"]["label"] = control.LABELS.get(state["bot"] or "", "Bot")
    return state


def candles(coin: str, interval: str) -> list[list]:
    start = int(time.time() * 1000) - CANDLE_BARS * hl.INTERVAL_MS[interval]
    df = hl.candles(coin, interval, start)
    return [[int(ts.timestamp()), bot._num(r.open), bot._num(r.high), bot._num(r.low), bot._num(r.close)]
            for ts, r in zip(df.index, df.itertuples())]


def trader_positions(addresses: list[str]) -> dict[str, dict]:
    return {a: hl.positions(a) for a in addresses}


def _finite(v):
    """Browser-JSON kennt kein Infinity/NaN (z. B. Profit Factor ohne Verlusttrade): als null senden."""
    if isinstance(v, float):
        return v if v == v and abs(v) != float("inf") else None
    if isinstance(v, dict):
        return {k: _finite(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_finite(x) for x in v]
    return v


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value, code: int = 200) -> None:
        self._send(code, json.dumps(_finite(value), allow_nan=False).encode(), "application/json")

    def _api(self, key: tuple, fn) -> None:
        try:
            self._json(_cached(key, fn))
        except Exception as e:  # Netz weg oder Rate Limit: Das Dashboard soll weiterlaufen
            self._json({"error": str(e)}, 502)

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/api/state":
            self._json(build_state(q.get("bot")))
        elif url.path == "/api/candles":
            coin, interval = q.get("coin", ""), q.get("interval", "15m")
            if not COIN_RE.match(coin) or interval not in hl.INTERVAL_MS:
                self._json({"error": "ungueltiger Coin oder Intervall"}, 400)
            else:
                self._api(("candles", coin, interval), lambda: candles(coin, interval))
        elif url.path == "/api/positions":
            addresses = sorted(set(q.get("addresses", "").split(",")) - {""})[:10]
            if not all(ADDRESS_RE.match(a) for a in addresses):
                self._json({"error": "ungueltige Adresse"}, 400)
            else:
                self._api(("positions", *addresses), lambda: trader_positions(addresses))
        elif url.path == "/api/control":
            self._json(control.status(q.get("bot") or current_bot() or ""))
        elif url.path == "/api/log":
            self._json({"lines": control.log_tail(q.get("bot") or current_bot() or "", 120)})
        elif url.path == "/api/power":
            from tradingbot import power
            self._json({"current": power.current(), "levels": power.levels()})
        elif url.path == "/api/reports":
            from tradingbot import report
            b = q.get("bot") or current_bot() or ""
            folder = control.COMMANDS[b][0] if b in control.COMMANDS else data_dir(q.get("bot"))
            self._json({"reports": report.latest(folder)})
        elif url.path == "/api/trades.csv":
            path = data_dir(q.get("bot")) / bot.TRADES_CSV.name
            self._send(200, path.read_bytes() if path.exists() else b"", "text/csv; charset=utf-8",
                       {"Content-Disposition": 'attachment; filename="paper_trades.csv"'})
        elif url.path == "/vendor/lightweight-charts.js":
            self._send(200, CHARTS_JS.read_bytes(), "application/javascript")
        elif url.path in ("/", "/index.html"):
            self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def _local_request(self) -> bool:
        """Schutz davor, dass eine fremde Webseite im Browser den Bot startet/stoppt: nur eigene Seite,
        nur mit eigenem Header (den fremde Seiten ohne Erlaubnis nicht mitschicken koennen)."""
        host = (self.headers.get("Host") or "").split(":")[0]
        origin = self.headers.get("Origin")
        same_origin = origin is None or urlparse(origin).hostname in ("127.0.0.1", "localhost")
        return host in ("127.0.0.1", "localhost") and same_origin and self.headers.get("X-Trading-Bot") == "1"

    def do_POST(self):  # noqa: N802
        url = urlparse(self.path)
        try:  # Body immer zuerst lesen: sonst kann eine Ablehnung (403/404) per Verbindungs-Reset verloren gehen
            raw = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 10_000))
        except (ValueError, OSError):
            raw = b""
        if url.path not in ("/api/control/start", "/api/control/stop", "/api/power", "/api/close"):
            return self._send(404, b"not found", "text/plain")
        if not self._local_request():
            return self._json({"error": "nicht erlaubt"}, 403)
        try:
            body = json.loads(raw or b"{}")
            if url.path == "/api/power":
                from tradingbot import power
                return self._json({"current": power.set_level(str(body.get("level") or "")), "levels": power.levels()})
            if url.path == "/api/close":
                return self._json(self._close(body))
            bot_id = str(body.get("bot") or current_bot() or "")
            fn = control.start if url.path.endswith("start") else control.stop
            self._json(fn(bot_id))
        except Exception as e:
            self._json({"error": str(e)}, 400)

    def _close(self, body: dict) -> dict:
        """Manuelles Schliessen einer Copy-Position (Spielgeld). Der laufende Copy-Bot fuehrt es aus."""
        from tradingbot.traders import manual
        coin = str(body.get("coin") or "")
        if not COIN_RE.match(coin):
            raise ValueError("Ungueltiger Coin")
        d = data_dir("copy")
        state = _read_json(d / "paper_state.json", {}) or {}
        if coin not in (state.get("positions") or {}):
            raise ValueError(f"{coin} ist nicht offen")
        if not control.status("copy").get("running"):
            raise ValueError("Der Copy-Bot laeuft nicht. Starte ihn, dann wird die Position geschlossen.")
        return {"queued": manual.request(d / "traders", coin)}

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
