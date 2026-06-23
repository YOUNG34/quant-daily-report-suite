from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List

from quanttrade.common.models import Order, PortfolioState, RiskEvent
from quanttrade.config import RiskConfig

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RiskEngine:
    cfg: RiskConfig

    def pre_trade_checks(self, order: Order, portfolio: PortfolioState) -> List[RiskEvent]:
        events: List[RiskEvent] = []
        notional = order.qty * (order.limit_price or 0)
        if order.limit_price is None:
            notional = 0  # market order notional unknown at check time
        if notional > self.cfg.max_single_order_notional:
            events.append(
                RiskEvent(ts=order.ts, code="LIMIT_ORDER_NOTIONAL", message="Order notional exceeds limit")
            )
        position = portfolio.positions.get(order.symbol)
        pos_qty = position.qty if position else 0
        projected = abs(pos_qty + (order.qty if order.side == order.side.BUY else -order.qty))
        if portfolio.equity > 0 and (projected * (order.limit_price or 1)) / portfolio.equity > self.cfg.max_position_pct:
            events.append(RiskEvent(ts=order.ts, code="POSITION_LIMIT", message="Position sizing too large"))
        return events

    def post_trade_checks(self, portfolio: PortfolioState) -> List[RiskEvent]:
        events: List[RiskEvent] = []
        if portfolio.equity <= 0:
            events.append(
                RiskEvent(ts=datetime.utcnow(), code="LOSS", message="Equity depleted", severity="CRITICAL")
            )
        return events
