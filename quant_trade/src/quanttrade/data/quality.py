from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, List

import pandas as pd

from quanttrade.common.models import Quote

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class QualityIssue:
    ts: pd.Timestamp
    code: str
    message: str


def basic_quality_checks(quotes: Iterable[Quote]) -> List[QualityIssue]:
    """Lightweight sanity checks for gaps, negatives, and duplicates."""
    df = pd.DataFrame([q.model_dump() for q in quotes])
    if df.empty:
        return []
    df.sort_values("ts", inplace=True)
    issues: List[QualityIssue] = []

    # Negative or zero prices
    bad_price = df[(df["bid"] <= 0) | (df["ask"] <= 0)]
    for _, row in bad_price.iterrows():
        issues.append(QualityIssue(ts=row.ts, code="NEG_PRICE", message="Bid/ask <= 0"))

    # Crossing markets (bid > ask)
    crossing = df[df["bid"] > df["ask"]]
    for _, row in crossing.iterrows():
        issues.append(QualityIssue(ts=row.ts, code="CROSS", message="Bid greater than ask"))

    # Duplicate timestamps
    dup_ts = df[df.duplicated(subset=["symbol", "ts"], keep=False)]
    for _, row in dup_ts.iterrows():
        issues.append(QualityIssue(ts=row.ts, code="DUP_TS", message="Duplicate timestamp for symbol"))

    # Time gaps larger than 5x median delta
    # Time gaps per symbol to avoid cross-name interference
    for sym, sub in df.groupby("symbol"):
        deltas = sub["ts"].diff().dropna().dt.total_seconds()
        if deltas.empty:
            continue
        med = deltas.median()
        if med == 0:
            continue
        gap_idx = deltas[deltas > med * 5].index
        for idx in gap_idx:
            issues.append(QualityIssue(ts=sub.loc[idx, "ts"], code="GAP", message=f"{sym}: Time gap >5x median"))

    logger.info("Quality check found %d issues", len(issues))
    return issues
