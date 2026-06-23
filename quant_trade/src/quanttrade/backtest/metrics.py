from __future__ import annotations

import numpy as np
import pandas as pd


def performance_stats(equity_curve: pd.Series) -> dict[str, float]:
    if equity_curve.empty:
        return {"return": 0.0, "vol": 0.0, "sharpe": 0.0, "max_drawdown": 0.0}

    returns = equity_curve.pct_change().dropna()
    total_return = equity_curve.iloc[-1] / equity_curve.iloc[0] - 1
    vol = returns.std() * np.sqrt(252)  # dailyized assuming 1 bar per day equivalent
    sharpe = returns.mean() / returns.std() * np.sqrt(252) if returns.std() > 0 else 0.0
    running_max = equity_curve.cummax()
    drawdown = (equity_curve - running_max) / running_max
    max_dd = drawdown.min() if not drawdown.empty else 0.0
    return {
        "return": float(total_return),
        "vol": float(vol if not np.isnan(vol) else 0.0),
        "sharpe": float(sharpe if not np.isnan(sharpe) else 0.0),
        "max_drawdown": float(max_dd),
    }
