"""Echte CME-Futures-Kerzen von Databento (historische 1-Minuten-Kerzen, nach Datenmenge bezahlt).

Einrichten: Konto auf databento.com anlegen, API-Schluessel in `.env` im Ordner trading-bot eintragen
(wird NICHT hochgeladen):
    DATABENTO_API_KEY=db-...
Vor jedem Download fragt `cost()` den Preis ab (kostet nichts). Geladene Monate werden gespeichert und
nie doppelt bezahlt. Fortlaufende Kontrakte: "NQ.v.0" = jeweils der Kontrakt mit dem meisten Volumen.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import ssl
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

from tradingbot.config import ROOT

log = logging.getLogger("tradingbot")
API = "https://hist.databento.com/v0/"
DATASET = "GLBX.MDP3"


def api_key() -> str | None:
    key = os.environ.get("DATABENTO_API_KEY")
    f = ROOT / ".env"
    if not key and f.exists():
        for line in f.read_text().splitlines():
            if line.strip().startswith("DATABENTO_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    return key or None


def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _call(endpoint: str, params: dict, key: str | None = None, timeout: int = 300) -> bytes:
    key = key or api_key()
    if not key:
        raise RuntimeError("DATABENTO_API_KEY fehlt in .env")
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(API + endpoint, data=data, headers={
        "Authorization": "Basic " + base64.b64encode(f"{key}:".encode()).decode(),
        "User-Agent": "trading-bot (paper research)"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
        return r.read()


def _params(symbol: str, start: str, end: str, schema: str = "ohlcv-1m") -> dict:
    return {"dataset": DATASET, "symbols": symbol, "stype_in": "continuous", "schema": schema,
            "start": start, "end": end}


def cost(symbol: str, start: str, end: str, schema: str = "ohlcv-1m") -> float:
    """Preis in USD fuer diesen Abruf (Abfrage ist kostenlos)."""
    return float(json.loads(_call("metadata.get_cost", _params(symbol, start, end, schema))))


def parse_csv(raw: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(raw))
    if df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"], dtype=float)
    t = df["ts_event"]
    idx = (pd.to_datetime(t.astype("int64"), unit="ns", utc=True) if pd.api.types.is_numeric_dtype(t)
           else pd.to_datetime(t, utc=True))
    out = df[["open", "high", "low", "close", "volume"]].astype(float)
    out.index = pd.DatetimeIndex(idx)
    return out[~out.index.duplicated(keep="last")].sort_index()


def resample(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    rule = interval.replace("m", "min") if interval.endswith("m") else interval
    if rule in ("1min",):
        return df
    agg = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return agg.dropna(subset=["open"])


def history(symbol: str, interval: str = "5m", since: str = "2021-01-01", cache_dir: Path | None = None,
            max_cost: float = 10.0, refresh_hours: float = 6) -> pd.DataFrame:
    """Laedt Monat fuer Monat (gespeichert in cache_dir), resampelt auf `interval`.
    Bricht ab, bevor die noch fehlenden Monate zusammen mehr als max_cost USD kosten wuerden."""
    cache_dir = Path(cache_dir or ROOT / "data" / "raw" / "databento") / symbol.replace(".", "_")
    cache_dir.mkdir(parents=True, exist_ok=True)
    now = pd.Timestamp.now(tz="UTC").floor("min") - pd.Timedelta(hours=1)  # juengste Daten oft noch nicht da
    months = pd.period_range(str(since)[:7], now.strftime("%Y-%m"), freq="M")
    todo = []
    for m in months:
        f = cache_dir / f"{m}.csv.gz"
        last = m == months[-1]
        if f.exists() and (not last or pd.Timestamp.now().timestamp() - f.stat().st_mtime < refresh_hours * 3600):
            continue  # fertige Monate nie neu, den laufenden hoechstens alle refresh_hours
        start = m.start_time.tz_localize("UTC")
        end = min(m.end_time.tz_localize("UTC") + pd.Timedelta(microseconds=1), now)
        todo.append((m, f, start, end))
    if todo:
        total = sum(cost(symbol, s.isoformat(), e.isoformat()) for _, _, s, e in todo)
        log.info("Databento %s: %d Monate fehlen, Kosten %.2f $", symbol, len(todo), total)
        if total > max_cost:
            raise RuntimeError(f"Databento {symbol}: {total:.2f} $ waeren mehr als das Limit {max_cost:.2f} $")
        for m, f, s, e in todo:
            raw = _call("timeseries.get_range", {**_params(symbol, s.isoformat(), e.isoformat()),
                                                 "encoding": "csv", "pretty_px": "true", "pretty_ts": "true"})
            parse_csv(raw).to_csv(f, compression="gzip")
    parts = [pd.read_csv(f, index_col=0, parse_dates=True) for f in sorted(cache_dir.glob("*.csv.gz"))]
    if not parts:
        raise RuntimeError(f"Keine Databento-Daten fuer {symbol}")
    df = pd.concat(parts)
    df.index = pd.DatetimeIndex(pd.to_datetime(df.index, utc=True))
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return resample(df, interval)
