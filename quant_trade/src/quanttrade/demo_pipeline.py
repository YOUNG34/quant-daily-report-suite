from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from quanttrade.backtest.engine import BacktestEngine, simple_moving_average_strategy
from quanttrade.backtest.metrics import performance_stats
from quanttrade.clearing.clearing import ClearingEngine
from quanttrade.common.models import PortfolioState
from quanttrade.config import QuantConfig
from quanttrade.data.ingestion import DataIngestion
from quanttrade.data.etl import quotes_to_bars
from quanttrade.data.storage import BarStorage
from quanttrade.data.quality import basic_quality_checks
from quanttrade.ops.monitoring import MetricsSink
from quanttrade.ops.rollout import RolloutController, RolloutPlan, Stage
from quanttrade.strategy.registry import StrategyRegistry
from quanttrade.strategy.base import StrategyMetadata
from quanttrade.strategy.sma import SMAStrategy
from quanttrade.factors.miner import auto_mine_factors

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def main() -> None:
    cfg = QuantConfig()

    # 1) 数据采集
    ingestion = DataIngestion(cfg.data.sources)
    quotes = list(ingestion.load())
    quality_issues = basic_quality_checks(quotes)
    if quality_issues:
        logging.warning("Quality issues found: %s", quality_issues[:5])

    # 2) ETL -> Bars + 持久化
    bars = quotes_to_bars(quotes, cfg.backtest.bar_seconds)
    store = BarStorage(cfg=cfg.data)
    store.save(bars, name="demo")

    # 2.5) 因子自动挖掘（示例）：输出 IC 报告
    factor_ic = auto_mine_factors(bars, cfg.factors)
    logging.info("Factor IC summary (top3): %s", dict(list(factor_ic.items())[:3]))

    # 3) 注册策略并回测
    registry = StrategyRegistry()
    registry.register(
        name=SMAStrategy.name,
        version=SMAStrategy.version,
        factory=lambda: SMAStrategy(short=3, long=5, order_size=100),
        meta=StrategyMetadata(name=SMAStrategy.name, version=SMAStrategy.version, owner="team", tags=["demo", "sma"]),
    )
    strategy = registry.get("sma")  # latest active
    engine = BacktestEngine(cfg.backtest)
    pnl, fills = engine.run(bars, lambda bar, ctx: strategy.on_bar(bar, ctx.portfolio))

    stats = performance_stats(pnl["equity"]) if not pnl.empty else {}
    logging.info("Backtest stats: %s", stats)

    # 4) 风控示例（事后）
    portfolio = PortfolioState(cash=pnl["cash"].iloc[-1] if not pnl.empty else cfg.backtest.initial_cash, positions={}, equity=pnl["equity"].iloc[-1] if not pnl.empty else cfg.backtest.initial_cash)

    # 5) 清算与报表
    clearing = ClearingEngine(cfg.clearing)
    report_path = clearing.generate_report(fills)

    # 6) 灰度发布与监控指标
    metrics = MetricsSink(enabled=cfg.enable_telemetry, endpoint=cfg.metrics_endpoint)
    if stats:
        metrics.emit("return", stats.get("return", 0), tags={"env": cfg.env})
        metrics.emit("sharpe", stats.get("sharpe", 0), tags={"env": cfg.env})
    rollout = RolloutController(stats, realtime_metrics={"max_drawdown": abs(stats.get("max_drawdown", 0))})
    plan = RolloutPlan(strategy=strategy.name, version=strategy.version, min_sharpe=0.0, max_drawdown=0.2)
    next_stage = rollout.next_stage(Stage.SIM, plan)
    green_cap = rollout.capital_for_stage(plan, Stage.GREEN, total_capital=cfg.backtest.initial_cash)
    logging.info("Rollout next stage: %s, GREEN capital %.0f", next_stage, green_cap)

    print("Demo complete. Report:", report_path)
    if not pnl.empty:
        print(pnl.tail())


if __name__ == "__main__":
    main()
