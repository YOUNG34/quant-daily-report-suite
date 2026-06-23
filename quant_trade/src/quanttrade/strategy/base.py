from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Protocol

from quanttrade.common.models import Bar, Order, PortfolioState, RiskEvent


class Strategy(Protocol):
    """Interface all strategies must implement."""

    name: str
    version: str

    def on_start(self, ts: datetime, portfolio: PortfolioState) -> List[Order]:
        ...

    def on_bar(self, bar: Bar, portfolio: PortfolioState) -> List[Order]:
        ...

    def on_risk(self, events: List[RiskEvent], portfolio: PortfolioState) -> None:
        ...


@dataclass(slots=True)
class StrategyMetadata:
    name: str
    version: str
    owner: str
    tags: List[str]
    description: str = ""
