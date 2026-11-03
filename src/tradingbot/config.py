"""Laedt die YAML-Konfiguration."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class RiskConfig:
    risk_per_trade_pct: float = 1.0
    reward_risk: float = 3.0
    atr_period: int = 14
    sl_atr_mult: float = 1.5
    max_daily_loss_pct: float = 3.0
    max_open_positions: int = 2


@dataclass
class SelectionConfig:
    train_bars: int = 2000
    test_bars: int = 500
    min_trades: int = 30
    min_profit_factor: float = 1.2
    min_win_rate: float = 0.0


@dataclass
class Config:
    mode: str = "paper"
    markets: list[dict] = field(default_factory=list)
    timeframe: str = "5m"
    history_period: str = "60d"
    start_capital: float = 10_000.0
    fee_per_trade: float = 1.0
    slippage_ticks: int = 1
    risk: RiskConfig = field(default_factory=RiskConfig)
    selection: SelectionConfig = field(default_factory=SelectionConfig)

    def market(self, symbol: str) -> dict:
        for m in self.markets:
            if m["symbol"] == symbol:
                return m
        return {"symbol": symbol, "name": symbol, "point_value": 1.0}


def load_config(path: str | Path | None = None) -> Config:
    if path is None:
        path = ROOT / "config" / "config.yaml"
        if not path.exists():
            path = ROOT / "config" / "config.example.yaml"
    raw = yaml.safe_load(Path(path).read_text()) or {}
    acc = raw.get("account", {})
    return Config(
        mode=raw.get("mode", "paper"),
        markets=raw.get("markets", []),
        timeframe=raw.get("timeframe", "5m"),
        history_period=raw.get("history_period", "60d"),
        start_capital=acc.get("start_capital", 10_000.0),
        fee_per_trade=acc.get("fee_per_trade", 1.0),
        slippage_ticks=acc.get("slippage_ticks", 1),
        risk=RiskConfig(**raw.get("risk", {})),
        selection=SelectionConfig(**raw.get("selection", {})),
    )
