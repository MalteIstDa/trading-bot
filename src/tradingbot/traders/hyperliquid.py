"""Hyperliquid-Client (oeffentliche API, kein Konto noetig, nur Lesen).
Doku: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint"""
from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

API = "https://api.hyperliquid.xyz/info"
LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
HEADERS = {"Content-Type": "application/json", "User-Agent": "tradingbot/0.1"}
MAX_FILLS = 10_000


def _ssl_context() -> ssl.SSLContext:
    """Zertifikate aus certifi nutzen. Das Python von python.org auf dem Mac hat sonst keine
    und meldet CERTIFICATE_VERIFY_FAILED."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_SSL = _ssl_context()  # Hyperliquid liefert nur die letzten 10.000 Fills eines Kontos


def _request(url: str, payload: dict | None = None, retries: int = 4):
    data = None if payload is None else json.dumps(payload).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=HEADERS, method="POST" if data else "GET")
            with urllib.request.urlopen(req, timeout=30, context=_SSL) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:  # Rate Limit: kurz warten
                time.sleep(2 ** attempt)
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise


def post(payload: dict):
    return _request(API, payload)


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def leaderboard() -> list[dict]:
    """Alle Konten des Leaderboards, vereinheitlicht:
    {address, name, account_value, perf: {day|week|month|allTime: {pnl, roi, vlm}}}"""
    raw = _request(LEADERBOARD)
    rows = raw.get("leaderboardRows", raw) if isinstance(raw, dict) else raw
    out = []
    for r in rows:
        wp = r.get("windowPerformances", [])
        items = wp.items() if isinstance(wp, dict) else wp
        perf = {k: {m: _f(v.get(m)) for m in ("pnl", "roi", "vlm")} for k, v in items}
        out.append({"address": r.get("ethAddress", "").lower(), "name": r.get("displayName"),
                    "account_value": _f(r.get("accountValue")), "perf": perf})
    return out


def fills(address: str, start_ms: int, end_ms: int | None = None, cache_dir: Path | None = None) -> list[dict]:
    """Alle Fills (Ausfuehrungen) eines Kontos im Zeitraum, aelteste zuerst. Blaettert automatisch."""
    cache = cache_dir / f"fills_{address}.json" if cache_dir else None
    end_ms = end_ms or int(time.time() * 1000)
    if cache and cache.exists():
        c = json.loads(cache.read_text())
        if c["start"] <= start_ms and c["end"] >= end_ms - 6 * 3600 * 1000:  # hoechstens 6 h alt
            return [f for f in c["fills"] if start_ms <= f["time"] <= end_ms]
    out, cursor = [], start_ms
    while cursor < end_ms and len(out) < MAX_FILLS:
        page = post({"type": "userFillsByTime", "user": address, "startTime": cursor,
                     "endTime": end_ms, "aggregateByTime": True})
        if not page:
            break
        page = sorted(page, key=lambda f: f["time"])
        out += page
        nxt = page[-1]["time"] + 1
        if nxt <= cursor or len(page) < 500:
            break
        cursor = nxt
    seen, uniq = set(), []
    for f in out:
        key = (f.get("tid"), f.get("hash"), f["time"], f.get("oid"))
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"start": start_ms, "end": end_ms, "fills": uniq}))
    return uniq


def positions(address: str) -> dict[str, dict]:
    """Aktuelle Positionen: {coin: {size (+long/-short), entry}}"""
    st = post({"type": "clearinghouseState", "user": address})
    out = {}
    for ap in st.get("assetPositions", []):
        p = ap.get("position", {})
        size = _f(p.get("szi"))
        if size:
            out[p["coin"]] = {"size": size, "entry": _f(p.get("entryPx"))}
    return out


INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def candles(coin: str, interval: str, start_ms: int, end_ms: int | None = None) -> pd.DataFrame:
    """OHLCV-Kerzen von Hyperliquid (max. die letzten 5000 Kerzen je Intervall)."""
    end_ms = end_ms or int(time.time() * 1000)
    rows, cursor = [], start_ms
    step = INTERVAL_MS[interval] * 4900
    while cursor < end_ms:
        chunk = post({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval,
                                                        "startTime": cursor, "endTime": min(end_ms, cursor + step)}})
        rows += chunk or []
        cursor += step
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows).drop_duplicates("t").sort_values("t")
    df.index = pd.to_datetime(df["t"], unit="ms", utc=True)
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def size_decimals() -> dict[str, int]:
    """Wie viele Nachkommastellen eine Ordergroesse je Coin haben darf."""
    meta = post({"type": "meta"})
    return {a["name"]: int(a.get("szDecimals", 3)) for a in meta.get("universe", [])}
