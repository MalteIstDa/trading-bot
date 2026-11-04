import json

import pandas as pd

from tradingbot.data import databento

CSV = ("ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol\n"
       "2024-01-02T14:30:00.000000000Z,33,1,1,100,101,99,100.5,10,NQ.v.0\n"
       "2024-01-02T14:31:00.000000000Z,33,1,1,100.5,102,100,101,5,NQ.v.0\n"
       "2024-01-02T14:35:00.000000000Z,33,1,1,101,103,100,102,7,NQ.v.0\n")


def test_parse_and_resample():
    df = databento.parse_csv(CSV.encode())
    assert len(df) == 3 and str(df.index.tz) == "UTC"
    five = databento.resample(df, "5m")
    assert list(five["high"]) == [102.0, 103.0] and list(five["volume"]) == [15.0, 7.0]


def test_history_checks_cost_and_caches(monkeypatch, tmp_path):
    calls = []

    def fake_call(endpoint, params, key=None, timeout=300):
        calls.append(endpoint)
        return json.dumps(0.12).encode() if endpoint == "metadata.get_cost" else CSV.encode()

    monkeypatch.setattr(databento, "_call", fake_call)
    since = (pd.Timestamp.now(tz="UTC") - pd.DateOffset(months=1)).strftime("%Y-%m-01")
    df = databento.history("NQ.v.0", "5m", since=since, cache_dir=tmp_path)
    assert len(df) == 2 and calls.count("timeseries.get_range") == 2
    n = len(calls)
    databento.history("NQ.v.0", "5m", since=since, cache_dir=tmp_path)
    assert len(calls) == n  # alles aus dem Zwischenspeicher, nichts erneut bezahlt


def test_history_refuses_when_too_expensive(monkeypatch, tmp_path):
    monkeypatch.setattr(databento, "_call", lambda e, p, key=None, timeout=300: json.dumps(50.0).encode())
    try:
        databento.history("NQ.v.0", "5m", since="2024-01-01", cache_dir=tmp_path, max_cost=10)
    except RuntimeError as e:
        assert "Limit" in str(e)
    else:
        raise AssertionError("haette abbrechen muessen")
