import io
import json
import zipfile

import pandas as pd

from tradingbot.data import binance


def _zip(rows):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.csv", "\n".join(",".join(map(str, r)) for r in rows))
    return buf.getvalue()


def _row(t, px):
    return [t, px, px + 1, px - 1, px + 0.5, 10, t + 299_999, 0, 0, 0, 0, 0]


def test_parse_zip_ms_and_microseconds():
    t = 1_704_067_200_000  # 2024-01-01 UTC in ms
    a = binance.parse_zip(_zip([_row(t, 100), _row(t + 300_000, 101)]))
    b = binance.parse_zip(_zip([_row(t * 1000, 100)]))
    assert a.index[0] == b.index[0] == pd.Timestamp("2024-01-01", tz="UTC")
    assert list(a.columns) == ["open", "high", "low", "close", "volume"] and a["close"].iloc[1] == 101.5


def test_history_merges_months_and_api(monkeypatch, tmp_path):
    now = pd.Timestamp.now(tz="UTC")
    prev = pd.Period(now.strftime("%Y-%m"), freq="M") - 1
    t0 = int((prev.start_time.tz_localize("UTC") - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1))
    api_t = int((now - pd.Timedelta(hours=1) - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1))
    calls = []

    def fake_get(url, retries=3):
        calls.append(url)
        if "monthly" in url:
            return _zip([_row(t0, 50), _row(t0 + 300_000, 51)]) if str(prev) in url else None
        return json.dumps([_row(api_t, 60)]).encode()

    monkeypatch.setattr(binance, "_get", fake_get)
    monkeypatch.setattr(binance.time, "sleep", lambda s: None)
    df = binance.history("BTCUSDT", "5m", since=str(prev.start_time.date()), cache_dir=tmp_path)
    assert len(df) == 3 and df.index.is_monotonic_increasing
    n = len(calls)
    binance.history("BTCUSDT", "5m", since=str(prev.start_time.date()), cache_dir=tmp_path)
    assert not any("monthly" in c for c in calls[n:])  # Monat kommt jetzt aus dem Zwischenspeicher
