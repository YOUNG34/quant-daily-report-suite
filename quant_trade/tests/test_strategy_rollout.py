from pathlib import Path

from quanttrade.ops.rollout import FundTranche, RolloutController, RolloutPlan, Stage
from quanttrade.strategy.registry import StrategyRegistry
from quanttrade.strategy.base import StrategyMetadata
from quanttrade.strategy.sma import SMAStrategy


def test_registry_and_rollout():
    reg = StrategyRegistry()
    reg.register(
        name=SMAStrategy.name,
        version="1.0.0",
        factory=lambda: SMAStrategy(short=2, long=4, order_size=10),
        meta=StrategyMetadata(name="sma", version="1.0.0", owner="qa", tags=["test"]),
    )
    strat = reg.get("sma")
    assert strat.name == "sma"

    metrics = {"sharpe": 1.2, "max_drawdown": 0.05}
    controller = RolloutController(metrics)
    plan = RolloutPlan(
        strategy="sma",
        version="1.0.0",
        min_sharpe=0.5,
        max_drawdown=0.1,
        fund_tranches=[FundTranche("green", 0.2), FundTranche("blue", 0.8)],
    )
    assert controller.can_advance(plan, Stage.SIM) is True
    assert controller.capital_for_stage(plan, Stage.GREEN, total_capital=1_000_000) == 200_000
    assert controller.next_stage(Stage.SIM, plan) == Stage.GREEN


def test_derisk_and_history(tmp_path: Path):
    metrics = {"sharpe": 1.0, "max_drawdown": 0.05}
    controller = RolloutController(metrics, realtime_metrics={"max_drawdown": 0.08})
    plan = RolloutPlan(strategy="sma", version="1.0.0", min_sharpe=0.5, max_drawdown=0.1)
    assert controller.can_advance(plan, Stage.SIM) is True

    # risk event severities drive factor
    from quanttrade.common.models import RiskEvent
    from datetime import datetime

    events = [RiskEvent(ts=datetime.utcnow(), code="TEST", message="warn", severity="WARN")]
    assert controller.derisk_factor_from_risk(events) == 0.5

    # baseline history load
    data = {"sma@1.0.0": metrics}
    f = tmp_path / "baseline.json"
    f.write_text('{"sma@1.0.0": {"sharpe": 1.0}}')
    loaded = controller.load_baseline_history(f)
    assert loaded["sma@1.0.0"]["sharpe"] == 1.0
