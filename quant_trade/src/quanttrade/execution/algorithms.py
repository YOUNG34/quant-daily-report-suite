from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, List

from quanttrade.common.models import Order, OrderType, Side


@dataclass(slots=True)
class AlgoSlice:
    ts: datetime
    qty: float


class TWAP:
    def __init__(self, duration_seconds: int = 300, slices: int = 5):
        self.duration_seconds = duration_seconds
        self.slices = slices

    def schedule(self, parent: Order) -> Iterable[Order]:
        slice_qty = parent.qty / self.slices
        start = parent.ts
        gap = timedelta(seconds=self.duration_seconds / self.slices)
        for i in range(self.slices):
            yield Order(
                id=f"{parent.id}-twap-{i}",
                symbol=parent.symbol,
                ts=start + gap * i,
                side=parent.side,
                qty=slice_qty,
                order_type=OrderType.LIMIT if parent.order_type == OrderType.LIMIT else OrderType.MARKET,
                limit_price=parent.limit_price,
            )


def pov(parent: Order, participation_rate: float = 0.1, forecast_volume: float = 1_000_000) -> List[Order]:
    qty = parent.qty
    est_child_qty = max(qty, forecast_volume * participation_rate)
    child = Order(
        id=f"{parent.id}-pov",
        symbol=parent.symbol,
        ts=parent.ts,
        side=parent.side,
        qty=est_child_qty,
        order_type=OrderType.MARKET,
    )
    return [child]
