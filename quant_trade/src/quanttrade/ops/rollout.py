from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

import json

from quanttrade.common.models import RiskEvent


class Stage(str, Enum):
    PAPER = "paper"      # 回测/纸上
    SIM = "sim"          # 模拟撮合
    GREEN = "green"      # 实盘小额
    BLUE = "blue"        # 实盘放量


@dataclass(slots=True)
class FundTranche:
    name: str
    allocation_pct: float  # 0-1 fraction


@dataclass(slots=True)
class RolloutPlan:
    strategy: str
    version: str
    stages: List[Stage] = field(default_factory=lambda: [Stage.PAPER, Stage.SIM, Stage.GREEN, Stage.BLUE])
    fund_tranches: List[FundTranche] = field(default_factory=lambda: [FundTranche("green", 0.1), FundTranche("blue", 0.9)])
    min_sharpe: float = 0.0
    max_drawdown: float = 0.2

    def validate(self) -> None:
        total_alloc = sum(t.allocation_pct for t in self.fund_tranches)
        if abs(total_alloc - 1.0) > 1e-6:
            raise ValueError("Fund tranches must sum to 1.0")


class RolloutController:
    """Determines whether a strategy/version may advance and what capital can be used."""

    def __init__(self, baseline_metrics: Dict[str, float], realtime_metrics: Optional[Dict[str, float]] = None):
        """
        baseline_metrics: metrics from CI/backtest snapshots (keys: return, sharpe, max_drawdown)
        realtime_metrics: live metrics (e.g., intraday PnL, realized DD) to gate advancement
        """
        self.baseline_metrics = baseline_metrics
        self.realtime_metrics = realtime_metrics or {}

    def can_advance(self, plan: RolloutPlan, current_stage: Stage) -> bool:
        plan.validate()
        meets_baseline = (
            self.baseline_metrics.get("sharpe", 0) >= plan.min_sharpe
            and abs(self.baseline_metrics.get("max_drawdown", 0)) <= plan.max_drawdown
        )
        # Realtime guardrails: block if live drawdown worse than baseline max DD
        live_dd = abs(self.realtime_metrics.get("max_drawdown", 0))
        live_ok = (live_dd or 0) <= plan.max_drawdown
        return meets_baseline and live_ok

    def capital_for_stage(self, plan: RolloutPlan, stage: Stage, total_capital: float) -> float:
        plan.validate()
        tranche = next((t for t in plan.fund_tranches if t.name == stage.value), None)
        if tranche is None:
            return 0.0
        return total_capital * tranche.allocation_pct

    def next_stage(self, current_stage: Stage, plan: RolloutPlan) -> Stage:
        """Return the next stage if allowed, else stick to current."""
        if not self.can_advance(plan, current_stage):
            return current_stage
        order = plan.stages
        try:
            idx = order.index(current_stage)
        except ValueError:
            return current_stage
        return order[min(idx + 1, len(order) - 1)]

    def derisk_factor_from_risk(self, events: List[RiskEvent]) -> float:
        """Compute a multiplicative factor to reduce capital/order size based on risk events."""
        if not events:
            return 1.0
        severities = {e.severity.upper() for e in events if getattr(e, "severity", None)}
        if "CRITICAL" in severities:
            return 0.0  # hard stop
        if "ERROR" in severities:
            return 0.2
        if "WARN" in severities:
            return 0.5
        return 1.0

    @staticmethod
    def load_baseline_history(path: Path) -> Dict[str, Dict[str, float]]:
        """Load baseline metrics history from JSON; returns empty dict if missing."""
        if not path.exists():
            return {}
        return json.loads(path.read_text())
