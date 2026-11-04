"""Scalping-Modelle im Stil von ICT / TJR, als feste Regeln programmiert (5m-Kerzen).

Grundidee aller Modelle (Smart-Money-Konzepte):
  1. Liquiditaet: Ueber alten Hochs / unter alten Tiefs liegen Stopps (z. B. Hoch/Tief der Asia- oder
     London-Session oder des Vortags). Wird so ein Level kurz ueberschritten und der Kurs kommt zurueck,
     heisst das "Sweep" (Liquiditaet abgeholt).
  2. Strukturbruch (Market Structure Shift / Break of Structure): nach dem Sweep schliesst der Kurs ueber
     dem letzten Swing-Hoch (Long) bzw. unter dem letzten Swing-Tief (Short).
  3. Fair Value Gap (FVG): Luecke zwischen Kerze 1 und Kerze 3 einer starken Bewegung. Einstieg, wenn der
     Kurs in diese Luecke zurueckkommt.
  4. Nur in den "Killzones" handeln (London 02-05 Uhr, New York 08:30-11 Uhr New Yorker Zeit).

Wichtig: ICT/TJR sind Konzepte mit viel Ermessensspielraum. Hier stehen eindeutige Regeln, damit der Bot
sie ehrlich testen kann. Keine Garantie, dass sie dem entsprechen, was ein Mensch im Chart sehen wuerde.
SL/TP setzt wie immer der Risk Manager (RR 1:3). Hoechstens ein Trade pro Modell und Tag.
Alle Signale nutzen nur Kerzen bis zur aktuellen (kein Blick in die Zukunft).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingbot.strategies.base import Strategy, atr

NY = "America/New_York"
# Minuten seit Mitternacht New Yorker Zeit
SESSIONS = {
    "asia": (20 * 60, 24 * 60),          # 20:00-24:00 (Vortag-Abend)
    "london": (2 * 60, 5 * 60),          # 02:00-05:00
    "ny": (8 * 60 + 30, 11 * 60),        # 08:30-11:00
}
SILVER_BULLET = {"london": (3 * 60, 4 * 60), "am": (10 * 60, 11 * 60), "pm": (14 * 60, 15 * 60)}


def _clock(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """(Minute des Tages in NY, Handelstag-Nummer). Ein Handelstag beginnt um 18:00 NY (wie bei Futures)."""
    idx = df.index if df.index.tz is not None else df.index.tz_localize("UTC")
    ny = idx.tz_convert(NY)
    minute = (ny.hour * 60 + ny.minute).to_numpy()
    day = (ny + pd.Timedelta(hours=6)).normalize()
    day_id = pd.factorize(day)[0]
    return minute, day_id


def _in(minute: int, window: tuple[int, int]) -> bool:
    return window[0] <= minute < window[1]


def _session_levels(df: pd.DataFrame, minute: np.ndarray, day_id: np.ndarray, levels: str) -> tuple[np.ndarray, np.ndarray]:
    """Pro Bar das gueltige Liquiditaets-Hoch/-Tief (NaN solange die Session des Tages noch laeuft)."""
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    n = len(df)
    hi, lo = np.full(n, np.nan), np.full(n, np.nan)
    names = ["asia", "london"] if levels == "asia_london" else [levels]
    cur_hi: dict = {}
    cur_lo: dict = {}
    done: dict = {}
    prev_day_hl = (np.nan, np.nan)
    day_hl = [-np.inf, np.inf]
    last_day = -1
    for i in range(n):
        d, m = day_id[i], minute[i]
        if d != last_day:
            if last_day >= 0:
                prev_day_hl = (day_hl[0], day_hl[1])
            day_hl = [-np.inf, np.inf]
            cur_hi, cur_lo, done = {}, {}, {}
            last_day = d
        if levels == "prev_day":
            hi[i], lo[i] = prev_day_hl
        else:
            bh, bl = -np.inf, np.inf
            ready = False
            for name in names:
                w = SESSIONS[name]
                if _in(m, w):
                    cur_hi[name] = max(cur_hi.get(name, -np.inf), h[i])
                    cur_lo[name] = min(cur_lo.get(name, np.inf), l[i])
                elif name in cur_hi:
                    done[name] = True
                if done.get(name):
                    ready = True
                    bh, bl = max(bh, cur_hi[name]), min(bl, cur_lo[name])
            if ready:
                hi[i], lo[i] = bh, bl
        day_hl[0], day_hl[1] = max(day_hl[0], h[i]), min(day_hl[1], l[i])
    return hi, lo


def _swings(df: pd.DataFrame, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Letztes BESTAETIGTES Swing-Hoch/-Tief pro Bar (ein Swing bei i ist erst bei i+n bekannt)."""
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    size = len(df)
    last_hi, last_lo = np.full(size, np.nan), np.full(size, np.nan)
    sh = sl = np.nan
    for i in range(size):
        j = i - n
        if j - n >= 0:
            if h[j] == h[j - n:j + n + 1].max():
                sh = h[j]
            if l[j] == l[j - n:j + n + 1].min():
                sl = l[j]
        last_hi[i], last_lo[i] = sh, sl
    return last_hi, last_lo


class IctSweepFvg(Strategy):
    """Sweep eines Session-Levels in der Killzone, dann Strukturbruch, dann Einstieg
    sofort (entry=mss) oder beim Ruecklauf in die Fair Value Gap (entry=fvg)."""
    name = "ict_sweep_fvg"
    grid = {"levels": ["asia", "london", "prev_day", "asia_london"], "killzone": ["london", "ny"],
            "entry": ["fvg", "mss"]}

    def __init__(self, levels: str = "asia_london", killzone: str = "ny", entry: str = "fvg", swing: int = 2,
                 fvg_min: float = 0.2):
        super().__init__(levels=levels, killzone=killzone, entry=entry)
        self.opts = {"levels": levels, "killzone": killzone, "entry": entry}
        self.swing, self.fvg_min = swing, fvg_min

    @classmethod
    def valid(cls, params: dict) -> bool:
        # Levels muessen VOR der Killzone feststehen
        return not (params.get("killzone") == "london" and params.get("levels") in ("london", "asia_london"))

    def signals(self, df: pd.DataFrame) -> pd.Series:
        p = self.opts
        o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
        minute, day_id = _clock(df)
        lv_hi, lv_lo = _session_levels(df, minute, day_id, p["levels"])
        sw_hi, sw_lo = _swings(df, self.swing)
        a = atr(df, 14).to_numpy()
        kz = SESSIONS[p["killzone"]]
        out = np.zeros(len(df), dtype=int)
        state = None  # dict: side, extreme, fvg (lo, hi) oder None, mss bool
        traded_day = -1
        last_day = -1
        for i in range(3, len(df)):
            if day_id[i] != last_day:
                state, last_day = None, day_id[i]
            if traded_day == day_id[i] or not _in(minute[i], kz) or np.isnan(lv_hi[i]) or np.isnan(a[i]):
                if not _in(minute[i], kz):
                    state = None
                continue
            # 1) Sweep erkennen: Level ueberschritten (Docht oder Schluss) ...
            if state is None:
                if h[i] > lv_hi[i]:
                    state = {"side": -1, "extreme": h[i], "back": c[i] < lv_hi[i], "level": lv_hi[i], "mss": False, "fvg": None}
                elif l[i] < lv_lo[i]:
                    state = {"side": 1, "extreme": l[i], "back": c[i] > lv_lo[i], "level": lv_lo[i], "mss": False, "fvg": None}
                continue
            side = state["side"]
            # ... und der Kurs schliesst wieder innerhalb. Bis dahin waechst das Extrem mit.
            if not state["back"]:
                state["extreme"] = max(state["extreme"], h[i]) if side < 0 else min(state["extreme"], l[i])
                state["back"] = c[i] < state["level"] if side < 0 else c[i] > state["level"]
                continue
            # Ungueltig, wenn der Kurs danach ueber das Sweep-Extrem hinaus weiterlaeuft
            if (side < 0 and h[i] > state["extreme"]) or (side > 0 and l[i] < state["extreme"]):
                state = None
                continue
            # FVG in Richtung der neuen Bewegung merken
            new_gap = False
            if side > 0 and l[i] > h[i - 2] and l[i] - h[i - 2] >= self.fvg_min * a[i]:
                state["fvg"], new_gap = (h[i - 2], l[i]), True
            if side < 0 and h[i] < l[i - 2] and l[i - 2] - h[i] >= self.fvg_min * a[i]:
                state["fvg"], new_gap = (h[i], l[i - 2]), True
            # 2) Strukturbruch
            if not state["mss"]:
                if (side > 0 and not np.isnan(sw_hi[i]) and c[i] > sw_hi[i]) or \
                   (side < 0 and not np.isnan(sw_lo[i]) and c[i] < sw_lo[i]):
                    state["mss"] = True
                    if p["entry"] == "mss":
                        out[i], traded_day, state = side, day_id[i], None
                continue
            # 3) Einstieg beim Ruecklauf in die FVG (fruehestens eine Kerze nach ihrer Entstehung)
            fvg = state["fvg"]
            if new_gap:
                continue
            if fvg and ((side > 0 and l[i] <= fvg[1] and c[i] > fvg[0]) or (side < 0 and h[i] >= fvg[0] and c[i] < fvg[1])):
                out[i], traded_day, state = side, day_id[i], None
        return pd.Series(out, index=df.index)


class TjrSweepBos(IctSweepFvg):
    """Modell im Stil von TJR: Asia- und London-Hoch/Tief markieren, in der New-York-Session auf den Sweep
    eines dieser Levels warten, dann Break of Structure und Einstieg im Ruecklauf (FVG) bzw. direkt."""
    name = "tjr_sweep_bos"
    grid = {"entry": ["fvg", "mss"], "swing": [2, 3]}

    def __init__(self, entry: str = "fvg", swing: int = 2):
        super().__init__(levels="asia_london", killzone="ny", entry=entry, swing=swing)
        self.params = {"entry": entry, "swing": swing}  # fuer den Namen; die Regeln stehen in self.opts


class SilverBullet(Strategy):
    """ICT Silver Bullet: in einem 1-Stunden-Fenster (03-04, 10-11 oder 14-15 Uhr NY) wird zuerst Liquiditaet
    der letzten Stunde vor dem Fenster abgeholt, dann die erste FVG in Gegenrichtung gehandelt
    (Einstieg beim Ruecklauf in die Luecke)."""
    name = "silver_bullet"
    grid = {"window": ["london", "am", "pm"], "fvg_min": [0.1, 0.3]}

    def __init__(self, window: str = "am", fvg_min: float = 0.2):
        super().__init__(window=window, fvg_min=fvg_min)

    def signals(self, df: pd.DataFrame) -> pd.Series:
        w = SILVER_BULLET[self.params["window"]]
        fmin = self.params["fvg_min"]
        o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
        minute, day_id = _clock(df)
        a = atr(df, 14).to_numpy()
        out = np.zeros(len(df), dtype=int)
        pre_hi = pre_lo = np.nan
        bias = 0
        fvg = None
        traded = -1
        last_day = -1
        for i in range(3, len(df)):
            m, d = minute[i], day_id[i]
            if d != last_day:
                pre_hi = pre_lo = np.nan
                bias, fvg, last_day = 0, None, d
            if w[0] - 60 <= m < w[0]:  # Stunde vor dem Fenster: Liquiditaets-Level sammeln
                pre_hi = h[i] if np.isnan(pre_hi) else max(pre_hi, h[i])
                pre_lo = l[i] if np.isnan(pre_lo) else min(pre_lo, l[i])
                continue
            if not _in(m, w) or traded == d or np.isnan(pre_hi) or np.isnan(a[i]):
                continue
            if bias == 0:
                if l[i] < pre_lo:
                    bias = 1   # Liquiditaet unten abgeholt -> Long suchen
                elif h[i] > pre_hi:
                    bias = -1
            if bias > 0 and l[i] > h[i - 2] and l[i] - h[i - 2] >= fmin * a[i] and fvg is None:
                fvg = (h[i - 2], l[i])
                continue
            if bias < 0 and h[i] < l[i - 2] and l[i - 2] - h[i] >= fmin * a[i] and fvg is None:
                fvg = (h[i], l[i - 2])
                continue
            if fvg and ((bias > 0 and l[i] <= fvg[1] and c[i] > fvg[0]) or (bias < 0 and h[i] >= fvg[0] and c[i] < fvg[1])):
                out[i], traded = bias, d
        return pd.Series(out, index=df.index)


ICT_STRATEGIES = [IctSweepFvg, TjrSweepBos, SilverBullet]
