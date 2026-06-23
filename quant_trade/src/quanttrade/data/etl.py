from __future__ import annotations

import logging
from typing import Iterable, List

import pandas as pd

from quanttrade.common.models import Bar, Quote

logger = logging.getLogger(__name__)


def quotes_to_bars(quotes: Iterable[Quote], bar_seconds: int = 60) -> List[Bar]:
    """Simple resampler from tick quotes to OHLC bars."""
    rows = [q.model_dump() for q in quotes]
    if not rows:
        return []
    df = pd.DataFrame(rows)
    df["mid"] = (df["bid"] + df["ask"]) / 2
    df.set_index("ts", inplace=True)
    ohlc = df["mid"].resample(f"{bar_seconds}s").ohlc()
    volume = df["bid_size"].resample(f"{bar_seconds}s").sum().fillna(0)

    bars: List[Bar] = []
    for ts, row in ohlc.iterrows():
        if pd.isna(row["open"]):
            continue
        bars.append(
            Bar(
                symbol=df["symbol"].iloc[0],
                ts=ts.to_pydatetime(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(volume.loc[ts] if ts in volume.index else 0),
            )
        )
    logger.info("Generated %d bars", len(bars))
    return bars
