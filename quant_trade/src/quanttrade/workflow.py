from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Tuple

from quanttrade.backtest.engine import BacktestEngine
from quanttrade.backtest.metrics import performance_stats
from quanttrade.clearing.clearing import ClearingEngine
from quanttrade.common.models import PortfolioState
from quanttrade.config import QuantConfig
from quanttrade.data.etl import quotes_to_bars
from quanttrade.data.ingestion import DataIngestion
from quanttrade.data.quality import basic_quality_checks
from quanttrade.data.storage import BarStorage
from quanttrade.factors.miner import auto_mine_factors
from quanttrade.ops.monitoring import MetricsSink
from quanttrade.ops.rollout import RolloutController, RolloutPlan, Stage
from quanttrade.strategy.base import StrategyMetadata
from quanttrade.strategy.registry import StrategyRegistry
from quanttrade.strategy.sma import SMAStrategy


logger = logging.getLogger(__name__)


def run_workflow(cfg: QuantConfig | None = None) -> Dict[str, object]:
    """
    End-to-end workflow:
    data ingest -> quality check -> ETL -> storage -> factor mining -> backtest -> rollout -> clearing.
    Returns a summary dictionary for programmatic use.
    """
    cfg = cfg or QuantConfig()

    # 1) Ingest
    quotes = list(DataIngestion(cfg.data.sources).load())
    quality_issues = basic_quality_checks(quotes)
    if quality_issues:
        logger.warning("Quality issues found: %s", quality_issues[:5])

    # 2) ETL & storage
    bars = quotes_to_bars(quotes, cfg.backtest.bar_seconds)
    store = BarStorage(cfg=cfg.data)
    cache_path = store.save(bars, name="workflow")

    # 3) Factor mining
    factor_ic = auto_mine_factors(bars, cfg.factors)

    # 4) Strategy registry & backtest
    registry = StrategyRegistry()
    registry.register(
        name=SMAStrategy.name,
        version=SMAStrategy.version,
        factory=lambda: SMAStrategy(short=3, long=5, order_size=100),
        meta=StrategyMetadata(name=SMAStrategy.name, version=SMAStrategy.version, owner="team", tags=["demo", "sma"]),
    )
    strategy = registry.get("sma")
    engine = BacktestEngine(cfg.backtest)
    pnl, fills = engine.run(bars, lambda bar, ctx: strategy.on_bar(bar, ctx.portfolio))
    stats = performance_stats(pnl["equity"]) if not pnl.empty else {}

    # 5) Rollout decision
    rollout = RolloutController(stats, realtime_metrics={"max_drawdown": abs(stats.get("max_drawdown", 0))})
    plan = RolloutPlan(strategy=strategy.name, version=strategy.version, min_sharpe=0.0, max_drawdown=0.2)
    next_stage = rollout.next_stage(Stage.SIM, plan)
    green_cap = rollout.capital_for_stage(plan, Stage.GREEN, total_capital=cfg.backtest.initial_cash)

    # 6) Clearing
    clearing = ClearingEngine(cfg.clearing)
    report_path = clearing.generate_report(fills)

    # 7) Metrics
    metrics = MetricsSink(enabled=cfg.enable_telemetry, endpoint=cfg.metrics_endpoint)
    if stats:
        metrics.emit("return", stats.get("return", 0), tags={"env": cfg.env})
        metrics.emit("sharpe", stats.get("sharpe", 0), tags={"env": cfg.env})

    summary: Dict[str, object] = {
        "quality_issues": len(quality_issues),
        "cache_path": Path(cache_path),
        "factor_ic": factor_ic,
        "stats": stats,
        "next_stage": next_stage.value if hasattr(next_stage, "value") else next_stage,
        "green_capital": green_cap,
        "clearing_report": Path(report_path),
    }
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    summary = run_workflow()
    print("Workflow summary:")
    for k, v in summary.items():
        print(f" - {k}: {v}")


if __name__ == "__main__":
    main()
