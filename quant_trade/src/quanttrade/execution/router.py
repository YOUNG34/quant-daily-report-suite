from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, List

from quanttrade.common.models import Fill, Order
from quanttrade.config import ExecutionConfig
from quanttrade.execution.algorithms import TWAP

logger = logging.getLogger(__name__)


class ExecutionRouter:
    def __init__(self, cfg: ExecutionConfig):
        self.cfg = cfg
        self.twap = TWAP()

    def route(self, orders: Iterable[Order]) -> List[Fill]:
        fills: List[Fill] = []
        for order in orders:
            child_orders = list(self.twap.schedule(order)) if self.cfg.default_algo == "TWAP" else [order]
            for child in child_orders:
                # Placeholder: immediate fill at limit/market mid-price  
                price = child.limit_price or 1.0
                fills.append(
                    Fill(
                        order_id=child.id,
                        symbol=child.symbol,
                        ts=datetime.utcnow(),
                        qty=child.qty if child.side == child.side.BUY else -child.qty,
                        price=price,
                    )
                )
                logger.debug("Filled child order %s at %.4f", child.id, price)
        return fills
