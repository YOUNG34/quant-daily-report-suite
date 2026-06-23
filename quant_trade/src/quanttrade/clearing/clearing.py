from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import pandas as pd

from quanttrade.common.models import Fill
from quanttrade.config import ClearingConfig

logger = logging.getLogger(__name__)


class ClearingEngine:
    def __init__(self, cfg: ClearingConfig):
        self.cfg = cfg
        Path(self.cfg.report_path).parent.mkdir(parents=True, exist_ok=True)

    def generate_report(self, fills: Iterable[Fill]) -> Path:
        df = pd.DataFrame([f.model_dump() for f in fills])
        if df.empty:
            logger.warning("No fills provided to clearing")
            path = Path(self.cfg.report_path)
            path.touch()
            return path
        grouped = df.groupby("symbol").agg({"qty": "sum", "price": "mean", "commission": "sum", "slippage": "sum"})
        grouped.rename(columns={"qty": "net_qty", "price": "avg_price"}, inplace=True)
        path = Path(self.cfg.report_path)
        grouped.to_csv(path)
        logger.info("Clearing report written to %s", path)
        return path
