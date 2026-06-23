from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(slots=True)
class DataConfig:
    sources: List[str] = field(default_factory=lambda: ["csv://data/sample_ticks.csv"])
    timezone: str = "UTC"
    currency: str = "USD"
    clickhouse_url: Optional[str] = None  # e.g. "http://localhost:8123"
    clickhouse_user: str = "default"
    clickhouse_password: Optional[str] = "1234"
    clickhouse_database: str = "default"
    clickhouse_table: str = "bars"


@dataclass(slots=True)
class BacktestConfig:
    initial_cash: float = 1_000_000.0
    commission_bps: float = 2.0  # basis points per notional
    slippage_bps: float = 5.0
    bar_seconds: int = 60


@dataclass(slots=True)
class RiskConfig:
    max_position_pct: float = 0.2
    max_single_order_notional: float = 100_000.0
    max_drawdown_pct: float = 0.1


@dataclass(slots=True)
class ExecutionConfig:
    venue: str = "SIM"
    max_order_rate_per_min: int = 120
    default_algo: str = "TWAP"


@dataclass(slots=True)
class ClearingConfig:
    report_path: str = "data/clearing_report.csv"

@dataclass(slots=True)
class FactorConfig:
    lookbacks: list[int] = field(default_factory=lambda: [5, 10, 20])
    horizons: list[int] = field(default_factory=lambda: [1, 3, 5])
    output_path: str = "artifacts/factors.csv"
    top_n: int = 5


@dataclass(slots=True)
class QuantConfig:
    env: str = "dev"
    data: DataConfig = field(default_factory=DataConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    clearing: ClearingConfig = field(default_factory=ClearingConfig)
    factors: FactorConfig = field(default_factory=FactorConfig)
    log_level: str = "INFO"
    enable_telemetry: bool = True
    metrics_endpoint: Optional[str] = None
