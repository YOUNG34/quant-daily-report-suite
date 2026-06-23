from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class Quote(BaseModel):
    symbol: str
    ts: datetime
    bid: float
    ask: float
    bid_size: float
    ask_size: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


class Bar(BaseModel):
    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class Order(BaseModel):
    id: str
    symbol: str
    ts: datetime
    side: Side
    qty: float
    order_type: OrderType
    limit_price: Optional[float] = None


class Fill(BaseModel):
    order_id: str
    symbol: str
    ts: datetime
    qty: float
    price: float
    commission: float = 0.0
    slippage: float = 0.0


class Position(BaseModel):
    symbol: str
    qty: float
    avg_cost: float


class PortfolioState(BaseModel):
    cash: float
    positions: dict[str, Position]
    equity: float


class RiskEvent(BaseModel):
    ts: datetime
    code: str = Field(..., description="Risk code identifier")
    message: str
    severity: str = Field(default="WARN")
