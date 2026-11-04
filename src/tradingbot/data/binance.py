"""Kostenlose Krypto-Kerzen von Binance (oeffentliche Marktdaten, kein Konto noetig).

Alte Monate kommen als ZIP von data.binance.vision (schnell, einmal geladen und gespeichert),
der laufende Monat ueber die oeffentliche Kline-Schnittstelle data-api.binance.vision.
Ergebnis: DataFrame open/high/low/close/volume mit UTC-Zeitindex (Beginn der Kerze)."""
from __future__ import annotations

import io
import json
import logging
import ssl
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

log = logging.getLogger("tradingbot")
VISION = "https://data.binance.vision/data/spot/monthly/klines/{s}/{i}/{s}-{i}-{m}.zip"
API = "https://data-api.binance.vision/api/v3/klines"


def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _get(url: str, retries: int = 3) -> bytes | None:
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "trading-bot (paper research)"})
            with urllib.request.urlopen(req, timeout=60, context=_ctx()) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (418, 429):  # zu viele Anfragen: laenger warten, nicht draengeln
                time.sleep(60 * (k + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(5 * (k + 1))
    raise RuntimeError(f"Download fehlgeschlagen: {url}")


def _frame(rows) -> pd.DataFrame:
    df = pd.DataFrame([r[:6] for r in rows], columns=["t", "open", "high", "low", "close", "volume"])
    t = pd.to_numeric(df["t"])
    unit = "us" if t.max() > 10**14 else "ms"  # seit 2025 liefert Binance Mikrosekunden in den ZIPs
    df.index = pd.DatetimeIndex(pd.to_datetime(t, unit=unit, utc=True))
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def parse_zip(blob: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        text = z.read(z.namelist()[0]).decode()
    rows = [line.split(",") for line in text.splitlines() if line and line[0].isdigit()]  # ohne Kopfzeile
    return _frame(rows)


def history(symbol: str, interval: str = "5m", since: str = "2022-01-01", cache_dir: Path | None = None,
            pause: float = 0.3) -> pd.DataFrame:
    cache_dir = Path(cache_dir or "data/raw/binance") / symbol / interval
    cache_dir.mkdir(parents=True, exist_ok=True)
    now = pd.Timestamp.now(tz="UTC")
    parts = []
    month = pd.Period(str(since)[:7], freq="M")
    this_month = pd.Period(now.strftime("%Y-%m"), freq="M")
    while month < this_month:
        m = str(month)
        f = cache_dir / f"{m}.csv.gz"
        if f.exists():
            parts.append(pd.read_csv(f, index_col=0, parse_dates=True))
        else:
            blob = _get(VISION.format(s=symbol, i=interval, m=m))
            if blob:
                df = parse_zip(blob)
                df.to_csv(f, compression="gzip")
                parts.append(df)
                log.info("Binance %s %s %s: %d Kerzen", symbol, interval, m, len(df))
                time.sleep(pause)
        month += 1
    # laufender Monat (und evtl. noch nicht als ZIP veroeffentlichter Vormonat) ueber die API
    start = (parts[-1].index[-1] if parts else pd.Timestamp(since, tz="UTC"))
    start_ms = int((start - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)) + 1
    while True:
        blob = _get(f"{API}?symbol={symbol}&interval={interval}&limit=1000&startTime={start_ms}")
        rows = json.loads(blob or b"[]")
        if not rows:
            break
        parts.append(_frame(rows))
        start_ms = int(rows[-1][0]) + 1
        if len(rows) < 1000:
            break
        time.sleep(pause)
    if not parts:
        raise RuntimeError(f"Keine Binance-Daten fuer {symbol}")
    df = pd.concat(parts)
    df.index = pd.DatetimeIndex(pd.to_datetime(df.index, utc=True))
    df = df[~df.index.duplicated(keep="last")].sort_index()
    step = pd.Timedelta(interval.replace("m", "min"))
    return df[df.index + step <= now]  # nur fertige Kerzen
