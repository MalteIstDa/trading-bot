import pandas as pd

from tradingbot.data.feed import read_csv


def test_cache_with_mixed_timezone_offsets(tmp_path):
    # Yahoo-Daten ueber die Zeitumstellung hinweg haben -04:00 und -05:00 gemischt
    f = tmp_path / "c.csv"
    f.write_text("Datetime,open,high,low,close,volume\n"
                 "2026-10-30 10:00:00-04:00,1,2,0.5,1.5,10\n"
                 "2026-11-03 10:00:00-05:00,1,2,0.5,1.5,10\n")
    df = read_csv(f)
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index[1].date().isoformat() == "2026-11-03"
    assert str(df.index.tz) == "UTC"
