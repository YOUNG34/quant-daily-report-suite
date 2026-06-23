from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from quanttrade.common.models import Bar
from quanttrade.config import FactorConfig

logger = logging.getLogger(__name__)


def bars_to_df(bars: List[Bar]) -> pd.DataFrame:
    df = pd.DataFrame([b.model_dump() for b in bars])
    if df.empty:
        return df
    df = df.sort_values("ts").set_index("ts")
    return df


def compute_factors(df: pd.DataFrame, lookbacks: List[int]) -> pd.DataFrame:
    """Generate a richer factor library (price/vol/volume/volatility)."""
    for lb in lookbacks:
        df[f"ret_{lb}"] = df["close"].pct_change(lb)
        df[f"mom_{lb}"] = df["close"] / df["close"].shift(lb) - 1
        df[f"vol_{lb}"] = df["close"].pct_change().rolling(lb).std()
        df[f"rsi_{lb}"] = _rsi(df["close"], lb)
        df[f"zscore_{lb}"] = (df["close"] - df["close"].rolling(lb).mean()) / df["close"].rolling(lb).std()
        if "volume" in df:
            df[f"volmom_{lb}"] = df["volume"].pct_change(lb)
            df[f"vwap_dev_{lb}"] = (df["close"] - (df["close"] * df["volume"]).rolling(lb).sum() / df["volume"].rolling(lb).sum())
        df[f"atr_{lb}"] = _atr(df, lb)
        df[f"range_{lb}"] = (df["high"] - df["low"]).rolling(lb).mean()
        df[f"trend_{lb}"] = (df["close"] - df["open"]).rolling(lb).mean()
    return df


def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0).rolling(period).mean()
    down = -delta.clip(upper=0).rolling(period).mean()
    rs = up / down.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def forward_returns(df: pd.DataFrame, horizons: List[int]) -> pd.DataFrame:
    for h in horizons:
        df[f"fwd_ret_{h}"] = df["close"].shift(-h) / df["close"] - 1
    return df


def ic_table(df: pd.DataFrame, horizons: List[int], factor_cols: List[str]) -> pd.DataFrame:
    rows: List[Tuple[str, int, float]] = []
    for factor_col in factor_cols:
        for h in horizons:
            target = f"fwd_ret_{h}"
            if factor_col not in df or target not in df:
                continue
            valid = df[[factor_col, target]].dropna()
            if len(valid) < 20:  # require minimal overlap
                ic = np.nan
            else:
                ic, _ = spearmanr(valid[factor_col], valid[target])
            rows.append((factor_col, h, ic))
    return pd.DataFrame(rows, columns=["factor", "horizon", "ic"])


def auto_mine_factors(bars: List[Bar], cfg: FactorConfig) -> Dict[str, float]:
    """Compute factors, evaluate IC vs forward returns, and write CSV report."""
    df = bars_to_df(bars)
    if df.empty:
        logger.warning("No bars provided for factor mining")
        return {}
    df = compute_factors(df, cfg.lookbacks)
    df = forward_returns(df, cfg.horizons)
    factor_cols = [c for c in df.columns if any(c.startswith(prefix) for prefix in ["mom_", "rsi_", "vol_", "zscore_", "volmom_", "vwap_dev_", "atr_", "range_", "trend_"])]
    ic_df = ic_table(df, cfg.horizons, factor_cols)
    Path(cfg.output_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(Path(cfg.output_path).with_suffix(".full.csv"))
    ic_df.to_csv(Path(cfg.output_path), index=False)
    # Pick top-N by absolute IC average across horizons
    ic_df["abs_ic"] = ic_df["ic"].abs()
    top = (
        ic_df.groupby("factor")["abs_ic"]
        .mean()
        .sort_values(ascending=False)
        .head(cfg.top_n)
    )
    best = top.to_dict()
    logger.info("Top factors: %s", best)
    return {k: best[k] for k in best}


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()
