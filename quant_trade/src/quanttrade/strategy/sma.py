from __future__ import annotations

from datetime import datetime
from typing import List

import pandas as pd

from quanttrade.common.models import Bar, Order, OrderType, PortfolioState, Position, Side
from quanttrade.strategy.base import Strategy


class SMAStrategy(Strategy):
    name = "sma"
    version = "1.0.0"

    def __init__(self, short: int = 3, long: int = 5, order_size: float = 100):
        self.short = short
        self.long = long
        self.order_size = order_size
        self.history: list[Bar] = []

    def on_start(self, ts: datetime, portfolio: PortfolioState):
        return []

    def on_bar(self, bar: Bar, portfolio: PortfolioState) -> List[Order]:
        self.history.append(bar)
        if len(self.history) < self.long:
            return []
        df = pd.DataFrame([b.model_dump() for b in self.history])
        short_ma = df["close"].tail(self.short).mean()
        long_ma = df["close"].tail(self.long).mean()
        orders: List[Order] = []
        last_close = df["close"].iloc[-1]
        # Cross logic simplified
        if short_ma > long_ma and portfolio.cash >= last_close * self.order_size:
            orders.append(
                Order(
                    id=f"buy-{bar.ts.timestamp()}",
                    symbol=bar.symbol,
                    ts=bar.ts,
                    side=Side.BUY,
                    qty=self.order_size,
                    order_type=OrderType.MARKET,
                )
            )
        elif short_ma < long_ma:
            pos = portfolio.positions.get(bar.symbol, Position(symbol=bar.symbol, qty=0.0, avg_cost=0.0))
            if pos.qty > 0:
                orders.append(
                    Order(
                        id=f"sell-{bar.ts.timestamp()}",
                        symbol=bar.symbol,
                        ts=bar.ts,
                        side=Side.SELL,
                        qty=pos.qty,
                        order_type=OrderType.MARKET,
                    )
                )
        return orders

    def on_risk(self, events, portfolio):
        # Could react to risk alerts (e.g., reduce size); placeholder
        return None
