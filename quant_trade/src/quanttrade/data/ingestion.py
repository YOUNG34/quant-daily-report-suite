from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, List

import pandas as pd

from quanttrade.common.models import Quote

logger = logging.getLogger(__name__)


class DataIngestion:
    """Fetch and normalize raw market data into Quote objects."""

    def __init__(self, sources: List[str]):
        self.sources = sources

    def load(self) -> Iterable[Quote]:
        for src in self.sources:
            if src.startswith("csv://"):
                yield from self._load_csv(Path(src.replace("csv://", "")))
            else:
                logger.warning("Unsupported source %s; skipping", src)

    def _load_csv(self, path: Path) -> Iterable[Quote]:
        if not path.exists():
            logger.error("CSV source %s not found", path)
            return
        df = pd.read_csv(path, parse_dates=["ts"])
        required = {"symbol", "ts", "bid", "ask", "bid_size", "ask_size"}
        missing = required.difference(df.columns)
        if missing:
            logger.error("CSV %s missing columns %s", path, missing)
            return
        for row in df.itertuples(index=False):
            yield Quote(
                symbol=row.symbol,
                ts=row.ts,
                bid=float(row.bid),
                ask=float(row.ask),
                bid_size=float(row.bid_size),
                ask_size=float(row.ask_size),
            )
