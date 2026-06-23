#!/usr/bin/env python3
"""
大盘择时模块：基于沪深 300 指数 (sh000300) 算 MA60，给出仓位信号
- ratio = Close / MA60
- ratio >= 1.00: 满仓（cash 0%）
- 0.95 <= ratio < 1.00: 半仓（cash 50%）
- ratio < 0.95: 空仓（cash 100%）
"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd

# 共享配置（向前兼容：config 加载失败用原硬编码值）
# 仓库根加到 sys.path，让 import quant_core.* 能工作（无论仓库放哪里）
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from quant_core.config import get_config
    from quant_core.logger import get_logger
    _cfg = get_config()
    _log = get_logger("market_timing")
    _MA_WINDOW = _cfg.timing.ma_window
    _FULL_THRESHOLD = _cfg.timing.full_threshold
    _HALF_THRESHOLD = _cfg.timing.half_threshold
    CACHE_FILE = Path(_cfg.timing.kline_cache) if _cfg.timing.kline_cache else Path.home() / "Projects" / "paper_trader" / "sh000300_kline.pkl"
    KLINE_START = _cfg.timing.kline_start
except Exception as _e:
    # 回退硬编码（保持向后兼容）
    log.warning("config 加载失败, 用默认值: {_e}")
    _log = None
    _MA_WINDOW = 60
    _FULL_THRESHOLD = 1.00
    _HALF_THRESHOLD = 0.95
    CACHE_FILE = Path.home() / "Projects" / "paper_trader" / "sh000300_kline.pkl"
    KLINE_START = "2020-01-01"


log = get_logger(__name__)

def fetch_sh000300(start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """从腾讯拉 sh000300 日线 (前复权)"""
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000300,day,{start_date},{end_date},640,qfq"
    r = urllib.request.urlopen(url, timeout=15)
    data = json.loads(r.read())
    rows = data["data"]["sh000300"].get("qfqday") or data["data"]["sh000300"].get("day")
    df = pd.DataFrame(rows, columns=["date", "open", "close", "high", "low", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "close", "high", "low", "volume"]:
        df[c] = df[c].astype(float)
    df = df.set_index("date").sort_index()
    return df


def load_or_fetch(start_date: str = None) -> pd.DataFrame:
    if start_date is None:
        start_date = KLINE_START
    """加载缓存，缺失则拉取"""
    if CACHE_FILE.exists():
        df = pd.read_pickle(CACHE_FILE)
        last = df.index[-1]
        if last < datetime.now() - timedelta(days=7):
            log.info("缓存截止 {last.date()}，重新拉取...")
            df = fetch_sh000300(start_date)
            df.to_pickle(CACHE_FILE)
    else:
        log.info("首次拉取 sh000300 ({start_date} ~ now)...")
        df = fetch_sh000300(start_date)
        df.to_pickle(CACHE_FILE)
    return df


def get_position_signal(target_date: str | pd.Timestamp, ma_window: int = None) -> dict:
    """
    给定目标日期，返回仓位信号
    返回: {date, close, ma, ratio, position_pct, action}
    """
    if ma_window is None:
        ma_window = _MA_WINDOW
    if isinstance(target_date, str):
        target_date = pd.Timestamp(target_date)
    df = load_or_fetch()
    # 取到 target_date 之前（含）所有数据
    df = df[df.index <= target_date]
    if len(df) < ma_window:
        return {"date": target_date, "position_pct": 1.0, "action": f"数据不足{len(df)}<{ma_window}→满仓"}

    close = df["close"].iloc[-1]
    ma = df["close"].iloc[-ma_window:].mean()
    ratio = close / ma

    if ratio >= _FULL_THRESHOLD:
        pos = 1.0
        action = f"趋势向上 (ratio≥{_FULL_THRESHOLD}) → 满仓"
    elif ratio >= _HALF_THRESHOLD:
        pos = 0.5
        action = f"震荡区间 ({_HALF_THRESHOLD}≤ratio<{_FULL_THRESHOLD}) → 半仓"
    else:
        pos = 0.0
        action = f"趋势向下 (ratio<{_HALF_THRESHOLD}) → 空仓"

    if _log:
        _log.info("timing {}: close={:.2f} ma{}({})={:.2f} ratio={:.4f} → {:.0%}",
                  target_date, close, ma_window, ma_window, ma, ratio, pos)

    return {
        "date": target_date,
        "close": round(close, 2),
        "ma60": round(ma, 2),
        "ratio": round(ratio, 4),
        "position_pct": pos,
        "action": action,
    }


if __name__ == "__main__":
    # 测试最近几个调仓日
    test_dates = ["2024-06-28", "2024-09-30", "2024-12-31", "2025-03-31", "2025-06-04"]
    for d in test_dates:
        sig = get_position_signal(d)
        log.info("{d}: 收盘={sig['close']:,.2f} MA60={sig['ma60']:,.2f} ratio={sig['ratio']:.4f} → {sig['position_pct']:.0%}  ({sig['action']})")