"""Trade-Journal in SQLite: jeder Trade (Backtest, Paper, spaeter Live) wird gespeichert,
damit der Bot aus Verlusttrades lernen kann."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from tradingbot.config import ROOT

DB = ROOT / "data" / "journal.sqlite"


def save(trades: list, source: str, db: Path = DB) -> None:
    if not trades:
        return
    df = pd.DataFrame([t.to_dict() for t in trades])
    df["source"] = source
    db.parent.mkdir(exist_ok=True)
    with sqlite3.connect(db) as con:
        df.to_sql("trades", con, if_exists="append", index=False)


def load(db: Path = DB) -> pd.DataFrame:
    if not db.exists():
        return pd.DataFrame()
    with sqlite3.connect(db) as con:
        return pd.read_sql("SELECT * FROM trades", con, parse_dates=["entry_time", "exit_time"])
