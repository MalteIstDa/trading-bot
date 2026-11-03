"""Aus Verlusttrades lernen: findet Bedingungen (Uhrzeit, Volatilitaet), unter denen eine Strategie
systematisch verliert, und sperrt sie. Wird nur auf Trainingsdaten gelernt und dann out-of-sample geprueft,
sonst lernt der Bot nur die Vergangenheit auswendig (Overfitting)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

VOL_BUCKETS = [0, 0.05, 0.1, 0.2, 0.4, np.inf]  # ATR in % vom Preis


@dataclass
class LearnedFilter:
    blocked_hours: set[int] = field(default_factory=set)
    blocked_vol: set[int] = field(default_factory=set)

    def __call__(self, ts: pd.Timestamp, atr_pct: float) -> bool:
        vb = int(np.digitize(atr_pct, VOL_BUCKETS)) - 1
        return ts.hour not in self.blocked_hours and vb not in self.blocked_vol

    def describe(self) -> str:
        return f"gesperrte Stunden={sorted(self.blocked_hours)}, gesperrte Vola-Stufen={sorted(self.blocked_vol)}"


def learn_filter(trades: list, min_samples: int = 8, max_expectancy_r: float = -0.3) -> LearnedFilter:
    """Sperrt Gruppen mit genug Trades und deutlich negativem Erwartungswert."""
    f = LearnedFilter()
    if not trades:
        return f
    df = pd.DataFrame([t.to_dict() for t in trades])
    df["vol_bucket"] = np.digitize(df["atr_pct"], VOL_BUCKETS) - 1
    for col, target in (("hour", f.blocked_hours), ("vol_bucket", f.blocked_vol)):
        g = df.groupby(col)["r_multiple"].agg(["count", "mean"])
        for key, row in g.iterrows():
            if row["count"] >= min_samples and row["mean"] <= max_expectancy_r:
                target.add(int(key))
    return f
