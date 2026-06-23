from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, List

import pandas as pd

from quanttrade.common.models import Bar, Fill, Order, OrderType, PortfolioState, Position, Side
from quanttrade.config import BacktestConfig

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StrategyContext:
    config: BacktestConfig
    portfolio: PortfolioState


def simple_moving_average_strategy(bar: Bar, short: int, long: int, history: List[Bar]) -> List[Order]:
    history.append(bar)
    if len(history) < long:
        return []
    df = pd.DataFrame([b.model_dump() for b in history])
    short_ma = df["close"].tail(short).mean()
    long_ma = df["close"].tail(long).mean()
    orders: List[Order] = []
    if short_ma > long_ma and df["close"].iloc[-2] <= df["close"].tail(long + 1).iloc[:-1].mean():
        orders.append(
            Order(
                id=f"buy-{bar.ts.timestamp()}",
                symbol=bar.symbol,
                ts=bar.ts,
                side=Side.BUY,
                qty=100,
                order_type=OrderType.MARKET,
            )
        )
    elif short_ma < long_ma and df["close"].iloc[-2] >= df["close"].tail(long + 1).iloc[:-1].mean():
        orders.append(
            Order(
                id=f"sell-{bar.ts.timestamp()}",
                symbol=bar.symbol,
                ts=bar.ts,
                side=Side.SELL,
                qty=100,
                order_type=OrderType.MARKET,
            )
        )
    return orders


class BacktestEngine:
    def __init__(self, cfg: BacktestConfig):
        self.cfg = cfg

    def run(
        self,
        bars: Iterable[Bar],
        strategy_fn: Callable[[Bar, StrategyContext], List[Order]],
    ) -> tuple[pd.DataFrame, List[Fill]]:
        cash = self.cfg.initial_cash
        positions: dict[str, Position] = {}
        fills: List[Fill] = []
        pnl_records: List[dict] = []

        history: List[Bar] = []
        ctx = StrategyContext(config=self.cfg, portfolio=PortfolioState(cash=cash, positions=positions, equity=cash))

        for bar in bars:
            # generate orders
            orders = strategy_fn(bar, ctx)
            # naive matching at close price
            for order in orders:
                fill_price = bar.close
                notional = fill_price * order.qty
                commission = notional * self.cfg.commission_bps / 10_000
                slippage = notional * self.cfg.slippage_bps / 10_000
                cash_change = -notional - commission - slippage if order.side == Side.BUY else notional - commission - slippage

                cash += cash_change
                pos = positions.get(order.symbol, Position(symbol=order.symbol, qty=0.0, avg_cost=0.0))
                signed_qty = order.qty if order.side == Side.BUY else -order.qty
                new_qty = pos.qty + signed_qty
                if new_qty != 0:
                    pos.avg_cost = ((pos.avg_cost * pos.qty) + (fill_price * signed_qty)) / new_qty
                pos.qty = new_qty
                positions[order.symbol] = pos

                fills.append(
                    Fill(
                        order_id=order.id,
                        symbol=order.symbol,
                        ts=datetime.utcnow(),
                        qty=signed_qty,
                        price=fill_price,
                        commission=commission,
                        slippage=slippage,
                    )
                )

            mark_to_market = sum(pos.qty * bar.close for pos in positions.values())
            equity = cash + mark_to_market
            pnl_records.append({"ts": bar.ts, "equity": equity, "cash": cash})
            history.append(bar)
            ctx.portfolio = PortfolioState(cash=cash, positions=positions.copy(), equity=equity)

        pnl_df = pd.DataFrame(pnl_records).set_index("ts")
        logger.info("Backtest finished with equity %.2f", pnl_df["equity"].iloc[-1] if not pnl_df.empty else cash)
        return pnl_df, fills
