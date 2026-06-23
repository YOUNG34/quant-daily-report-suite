"""
Run CI baseline backtests for all registered strategies and persist metrics.
This script is intended for CI pipelines; artifacts written to artifacts/baseline.json.
"""
from __future__ import annotations

import json
from pathlib import Path

from quanttrade.backtest.engine import BacktestEngine
from quanttrade.backtest.metrics import performance_stats
from quanttrade.common.models import PortfolioState
from quanttrade.config import QuantConfig
from quanttrade.data.etl import quotes_to_bars
from quanttrade.data.ingestion import DataIngestion
from quanttrade.strategy.registry import StrategyRegistry
from quanttrade.strategy.base import StrategyMetadata
from quanttrade.strategy.sma import SMAStrategy


def bootstrap_registry() -> StrategyRegistry:
    reg = StrategyRegistry()
    reg.register(
        name=SMAStrategy.name,
        version=SMAStrategy.version,
        factory=lambda: SMAStrategy(short=3, long=5, order_size=100),
        meta=StrategyMetadata(
            name=SMAStrategy.name,
            version=SMAStrategy.version,
            owner="team",
            tags=["sma", "demo"],
            description="Simple moving average crossover demo strategy",
        ),
    )
    return reg


def run_baseline() -> dict:
    cfg = QuantConfig()
    reg = bootstrap_registry()

    ingestion = DataIngestion(cfg.data.sources)
    quotes = list(ingestion.load())
    bars = quotes_to_bars(quotes, cfg.backtest.bar_seconds)
    engine = BacktestEngine(cfg.backtest)

    results = {}
    for sv in reg.list():
        strat = reg.get(sv.name, sv.version)
        pnl, _ = engine.run(bars, lambda bar, ctx, s=strat: s.on_bar(bar, ctx.portfolio))  # type: ignore[arg-type]
        metrics = performance_stats(pnl["equity"]) if not pnl.empty else {}
        results[f"{sv.name}@{sv.version}"] = metrics

    Path("artifacts").mkdir(exist_ok=True)
    out_path = Path("artifacts/baseline.json")
    out_path.write_text(json.dumps(results, indent=2))
    return results


if __name__ == "__main__":
    res = run_baseline()
    print(json.dumps(res, indent=2))
