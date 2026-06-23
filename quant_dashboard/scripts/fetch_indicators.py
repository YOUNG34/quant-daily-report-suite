# 路径派生 — 通过环境变量覆盖,默认走 ~/Projects/quant-dashboard
BASE = Path(os.environ.get("QUANT_DASHBOARD_HOME", str(Path.home() / "Projects" / "quant-dashboard")))

#!/usr/bin/env python3
"""
每日指标采集器 — 从 baostock 拉数据，计算 5 维度指标，写入 SQLite。

数据源：
  - baostock: K 线 + PE/PB + 换手率（主力）
  - akshare: 北向资金、主力净流入（fallback 可空）

运行模式：
  - python3 scripts/fetch_indicators.py         # 全量更新
  - python3 scripts/fetch_indicators.py --years  # 指定 PE 分位数回看年数
"""
from __future__ import annotations

import sys
import time
import json
import math
import sqlite3
import argparse
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

DB_PATH = Path(BASE / "data" / "indicators.db")
BAOSTOCK_LOOKBACK_YEARS = 3  # 保证 PE 分位计算有足够历史

# ── 工具函数 ──────────────────────────────

def _baostock_login() -> Any:
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_msg}")
    return bs


def _to_sh(symbol: str) -> str:
    """601816 → sh.601816"""
    m = "sh" if symbol.startswith(("6", "9", "5")) else "sz"
    return f"{m}.{symbol}"


def _get_end_date() -> str:
    return date.today().strftime("%Y-%m-%d")


def _get_start_date(years: int = 3) -> str:
    return (date.today() - timedelta(days=int(years * 365 * 1.1 + 10))).strftime("%Y-%m-%d")


# ── 技术指标计算 ─────────────────────────

def _ema(series: np.ndarray, period: int) -> np.ndarray:
    """EMA 计算（pandas 更快但用 numpy 避免重依赖）"""
    out = np.full_like(series, np.nan)
    multiplier = 2.0 / (period + 1)
    out[0] = series[0]
    for i in range(1, len(series)):
        if np.isnan(series[i]):
            out[i] = out[i-1]
        else:
            out[i] = (series[i] - out[i-1]) * multiplier + out[i-1]
    return out


def compute_trend_indicators(df: pd.DataFrame) -> dict[str, Any]:
    """计算趋势相关指标"""
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    out = {}

    # MA
    for p, k in [(5, "ma5"), (10, "ma10"), (20, "ma20"), (60, "ma60")]:
        if len(closes) >= p:
            out[k] = float(np.mean(closes[-p:]))
        else:
            out[k] = None

    # MA20 斜率（最近 5 日的线性回归斜率，标准化）
    if len(closes) >= 20:
        ma20_series = pd.Series(closes).rolling(20).mean().values
        ma20_slice = ma20_series[-5:]
        valid = ~np.isnan(ma20_slice)
        if valid.sum() >= 3:
            x = np.arange(len(ma20_slice))[valid]
            y = ma20_slice[valid]
            slope = np.polyfit(x, y, 1)[0]
            # 标准化：除以均价
            out["ma20_slope"] = float(slope / np.mean(y) * 100) if np.mean(y) != 0 else 0
        else:
            out["ma20_slope"] = None
    else:
        out["ma20_slope"] = None

    # ADX(14)
    out["adx14"] = _compute_adx(highs, lows, closes, 14)

    return out


def _compute_adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float | None:
    """ADX (Average Directional Index)"""
    n = len(highs)
    if n < period * 2:
        return None

    # TR
    tr = np.full(n, np.nan)
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i-1])
        lc = abs(lows[i] - closes[i-1])
        tr[i] = max(hl, hc, lc)

    # ±DM
    plus_dm = np.full(n, np.nan)
    minus_dm = np.full(n, np.nan)
    for i in range(1, n):
        up = highs[i] - highs[i-1]
        down = lows[i-1] - lows[i]
        if up > down and up > 0:
            plus_dm[i] = up
        else:
            plus_dm[i] = 0
        if down > up and down > 0:
            minus_dm[i] = down
        else:
            minus_dm[i] = 0

    # Wilder's smoothing (RMA — Running Moving Average)
    def wilder_smooth(src: np.ndarray, p: int) -> np.ndarray:
        out = np.full_like(src, np.nan, dtype=float)
        # 起点：前 p 个值的简单平均
        seed = np.nanmean(src[1:p+1])
        if np.isnan(seed):
            return out
        out[p] = seed
        for i in range(p+1, n):
            x = src[i] if not np.isnan(src[i]) else 0.0
            out[i] = (out[i-1] * (p - 1) + x) / p
        return out

    atr = wilder_smooth(tr, period)
    pdi_raw = wilder_smooth(plus_dm, period)
    ndi_raw = wilder_smooth(minus_dm, period)
    # 防止 0 除
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = np.where(atr > 0, pdi_raw / atr * 100, np.nan)
        ndi = np.where(atr > 0, ndi_raw / atr * 100, np.nan)

    # DX → ADX
    dx = np.full(n, np.nan)
    for i in range(period, n):
        pdi_v, ndi_v = pdi[i], ndi[i]
        if not (np.isnan(pdi_v) or np.isnan(ndi_v)):
            diff = abs(pdi_v - ndi_v)
            summ = pdi_v + ndi_v
            dx[i] = diff / summ * 100 if summ > 0 else 0

    adx = wilder_smooth(dx, period) if n > period * 2 else np.full(n, np.nan)
    last = adx[-1]
    return float(last) if not np.isnan(last) else None


def compute_momentum_indicators(df: pd.DataFrame) -> dict[str, Any]:
    """计算动量指标"""
    closes = df["close"].values
    out = {}

    # RSI(14)
    if len(closes) >= 15:
        gains = np.maximum(np.diff(closes), 0)
        losses = np.maximum(-np.diff(closes), 0)
        avg_gain = np.mean(gains[-14:])
        avg_loss = np.mean(losses[-14:])
        if avg_loss == 0:
            out["rsi14"] = 100.0
        else:
            rs = avg_gain / avg_loss
            out["rsi14"] = float(100 - 100 / (1 + rs))
    else:
        out["rsi14"] = None

    # MACD
    if len(closes) >= 26:
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        dif = ema12 - ema26
        dea = _ema(dif, 9)
        out["macd_diff"] = float(dif[-1])
        out["macd_dea"] = float(dea[-1])
        out["macd_hist"] = float(dif[-1] - dea[-1])
    else:
        out["macd_diff"] = out["macd_dea"] = out["macd_hist"] = None

    # ROC(20)
    if len(closes) >= 21 and closes[-21] > 0:
        out["roc20"] = float((closes[-1] / closes[-21] - 1) * 100)
    else:
        out["roc20"] = None

    return out


def compute_volume_indicators(df: pd.DataFrame) -> dict[str, Any]:
    """计算量能指标"""
    closes = df["close"].values
    volumes = df["volume"].values
    highs = df["high"].values
    lows = df["low"].values
    out = {}

    # 量比（当日量 / 20日均量）
    if len(volumes) >= 21:
        avg_vol = np.mean(volumes[-21:-1])  # 前 20 日
        today_vol = volumes[-1]
        out["vol_ratio"] = float(today_vol / avg_vol) if avg_vol > 0 else None
    else:
        out["vol_ratio"] = None

    # OBV
    obv = 0
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv += volumes[i]
        elif closes[i] < closes[i-1]:
            obv -= volumes[i]
        # else unchanged → obv stays
    out["obv"] = float(obv)

    # 量价配合
    if len(closes) >= 2:
        pct = (closes[-1] / closes[-2] - 1) * 100
        vol = volumes[-1]
        avg_vol = np.mean(volumes[-6:-1]) if len(volumes) >= 6 else volumes[-1]
        vol_ratio = vol / avg_vol if avg_vol > 0 else 1
        if pct > 1 and vol_ratio > 1.5:
            out["vol_price_match"] = "healthy_up"
        elif pct > 1 and vol_ratio < 0.7:
            out["vol_price_match"] = "fake_up"
        elif pct < -1 and vol_ratio > 1.5:
            out["vol_price_match"] = "fake_down"  # 放量下跌 → 危险
        elif pct < -1 and vol_ratio < 0.7:
            out["vol_price_match"] = "healthy_down"  # 缩量下跌 → 企稳信号
        else:
            out["vol_price_match"] = "neutral"
    else:
        out["vol_price_match"] = "neutral"

    return out


def compute_valuation_indicators(df: pd.DataFrame, all_df: pd.DataFrame) -> dict[str, Any]:
    """计算估值指标，PE/PB 分位基于 all_df（3年数据）"""
    out = {}
    if "peTTM" not in df.columns or "pbMRQ" not in df.columns:
        return {"pe_ttm": None, "pb": None, "pe_pct3y": None, "pb_pct3y": None}

    # 当日 PE/PB
    pe = df["peTTM"].values[-1]
    pb = df["pbMRQ"].values[-1]
    out["pe_ttm"] = float(pe) if pe and pe > 0 else None
    out["pb"] = float(pb) if pb and pb > 0 else None

    # 3年分位
    all_pe = all_df["peTTM"].values
    all_pb = all_df["pbMRQ"].values

    valid_pe = all_pe[(all_pe > 0) & (~np.isnan(all_pe))]
    valid_pb = all_pb[(all_pb > 0) & (~np.isnan(all_pb))]

    def _percentile(arr, val):
        if len(arr) < 30 or val is None:
            return None
        return float((arr < val).sum() / len(arr) * 100)

    out["pe_pct3y"] = _percentile(valid_pe, pe)
    out["pb_pct3y"] = _percentile(valid_pb, pb)

    return out


def compute_score(d: dict) -> tuple[dict, str]:
    """打分系统（0-100），输出 verdict"""
    s: dict[str, float] = {}
    weights = {"trend": 0.25, "momentum": 0.25, "volume": 0.20, "valuation": 0.20, "sentiment": 0.10}

    # 趋势
    score = 50
    if d.get("adx14") is not None and d["adx14"] > 25:
        score += 15  # 趋势明确是好事（只要方向对，m 方向判断需要趋势+斜率)
    if d.get("ma20_slope") is not None:
        score += min(max(d["ma20_slope"] * 20, -20), 20)
    s["trend"] = min(max(score, 0), 100)

    # 动量
    score = 50
    if d.get("rsi14") is not None:
        if d["rsi14"] > 70:
            score -= 20  # 超买
        elif d["rsi14"] < 30:
            score -= 20  # 超卖（动量空头中，但可能反转）
        elif d["rsi14"] > 60:
            score += 15
        elif d["rsi14"] < 40:
            score -= 10
        else:
            score += 5
    if d.get("macd_hist") is not None:
        score += 10 if d["macd_hist"] > 0 else -10
    if d.get("roc20") is not None:
        score += min(max(d["roc20"] * 2, -15), 15)
    s["momentum"] = min(max(score, 0), 100)

    # 量能
    score = 50
    vpm = d.get("vol_price_match")
    if vpm == "healthy_up":
        score += 25
    elif vpm == "fake_up":
        score -= 15
    elif vpm == "healthy_down":
        score += 10  # 缩量回调
    elif vpm == "fake_down":
        score -= 25  # 放量杀跌
    if d.get("vol_ratio") is not None:
        if d["vol_ratio"] > 2:
            score += 5  # 放量（方向靠 vol_price_match 判断）
        elif d["vol_ratio"] < 0.5:
            score -= 5
    s["volume"] = min(max(score, 0), 100)

    # 估值
    score = 50
    if d.get("pe_pct3y") is not None:
        p = d["pe_pct3y"]
        if p > 90:
            score -= 20  # 极度高估
        elif p > 70:
            score -= 10
        elif p < 10:
            score += 20  # 极度低估
        elif p < 30:
            score += 10
        else:
            score += 5
    if d.get("pb_pct3y") is not None:
        p = d["pb_pct3y"]
        if p > 90:
            score -= 15
        elif p < 10:
            score += 15
    s["valuation"] = min(max(score, 0), 100)

    # 情绪（暂设中性，待数据源补充）
    s["sentiment"] = 50.0

    # 加权总分
    total = sum(s[k] * weights[k] for k in weights)
    s["total"] = round(total, 1)

    # 判定
    if total >= 65:
        verdict = "bullish"
    elif total >= 40:
        verdict = "neutral"
    else:
        verdict = "bearish"

    return s, verdict


# ── 大盘环境 ─────────────────────────────

def fetch_market_context(bs: Any) -> dict | None:
    """拉上证+创业板数据计算大盘环境"""
    try:
        end = _get_end_date()
        start = _get_start_date(1)  # 1年足够算波动率
        rs = bs.query_history_k_data_plus(
            "sh.000001", "date,close,volume,amount,pctChg",
            start_date=start, end_date=end, frequency="d", adjustflag="2",
        )
        rows, fields = [], rs.fields
        while rs.next():
            rows.append(rs.get_row_data())
        sh_df = pd.DataFrame(rows, columns=fields)
        for c in ["close", "pctChg"]:
            sh_df[c] = pd.to_numeric(sh_df[c], errors="coerce")

        out: dict = {}
        if len(sh_df) > 0:
            closes = sh_df["close"].values
            pct = sh_df["pctChg"].values
            out["sh_close"] = float(closes[-1])
            out["sh_pct_chg"] = float(pct[-1])
            out["sh_ma60"] = float(np.mean(closes[-60:])) if len(closes) >= 60 else None

            # 20日波动率（年化）
            if len(pct) >= 21:
                daily_std = np.nanstd(pct[-21:])
                out["volatility20"] = float(daily_std * math.sqrt(252))
            else:
                out["volatility20"] = None

            # 市场状态判定
            if out["volatility20"] is not None:
                out["regime"] = (
                    "high_vol" if out["volatility20"] > 30 else
                    "trending_up" if len(closes) >= 60 and float(np.mean(pct[-20:])) > 0.3 else
                    "trending_down" if len(closes) >= 60 and float(np.mean(pct[-20:])) < -0.3 else
                    "range"
                )
            else:
                out["regime"] = "range"
        else:
            return None

        # 创业板
        rs2 = bs.query_history_k_data_plus(
            "sz.399006", "date,close,pctChg",
            start_date=start, end_date=end, frequency="d", adjustflag="2",
        )
        cyb_rows = []
        while rs2.next():
            cyb_rows.append(rs2.get_row_data())
        cyb_df = pd.DataFrame(cyb_rows, columns=rs2.fields)
        for c in ["close", "pctChg"]:
            cyb_df[c] = pd.to_numeric(cyb_df[c], errors="coerce")
        if len(cyb_df) > 0:
            out["cyb_close"] = float(cyb_df["close"].values[-1])
            out["cyb_pct_chg"] = float(cyb_df["pctChg"].values[-1])
            out["cyb_ma60"] = float(np.mean(cyb_df["close"].values[-60:])) if len(cyb_df) >= 60 else None

        return out
    except Exception as e:
        print(f"  ⚠ market context error: {e}", file=sys.stderr)
        return None


# ── 北向资金（akshare fallback） ─────────

def fetch_north_flow() -> tuple[float | None, float | None]:
    """北向资金 + 两融（尝试 akshare，归 gracefully）"""
    try:
        import akshare as ak
        df = ak.stock_hsgt_north_net_flow_in_em(symbol="沪股通")
        if len(df) > 0:
            recent = df["value"].values[-1]
            return float(recent), None
    except Exception:
        pass
    return None, None


# ── 主力净流入（akshare fallback） ────────

def fetch_main_flow(code: str, bs: Any) -> tuple[float | None, float | None]:
    """单只股票的主力净流入、大单占比"""
    try:
        import akshare as ak
        df = ak.stock_individual_fund_flow(stock=code, market="sh" if code.startswith(("6", "9")) else "sz")
        if len(df) > 0:
            net = df["主力净流入-净额"].values[-1] if "主力净流入-净额" in df.columns else None
            pct = df["主力净流入-净额占比"].values[-1] if "主力净流入-净额占比" in df.columns else None
            return (float(net) if net else None, float(pct) if pct else None)
    except Exception:
        pass
    return None, None


# ── 主流程 ─────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="量化日报指标采集器")
    parser.add_argument("--years", type=int, default=BAOSTOCK_LOOKBACK_YEARS,
                        help="PE/PB 分位回看年数（默认 3）")
    args = parser.parse_args()

    db = sqlite3.connect(DB_PATH)
    today = _get_end_date()
    looks = _get_start_date(args.years)
    start_time = time.time()

    # 1. 读取 watchlist
    stocks = db.execute("SELECT code, name, market FROM watchlist ORDER BY code").fetchall()
    print(f"📊 指标采集器 | {today} | {len(stocks)} 只股票\n")

    # 2. 登录 baostock
    bs = _baostock_login()
    print("🔌 baostock logged in")

    n_success = n_failed = 0
    failed_codes = []

    # 3. 遍历每只股票
    for code, name, market_prefix in stocks:
        symbol = _to_sh(code)
        try:
            rs = bs.query_history_k_data_plus(
                symbol,
                "date,open,high,low,close,volume,amount,turn,pctChg,peTTM,pbMRQ",
                start_date=looks, end_date=today, frequency="d", adjustflag="2",
            )
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            fields = ["date", "open", "high", "low", "close", "volume",
                      "amount", "turn", "pctChg", "peTTM", "pbMRQ"]

            if not rows:
                print(f"  ⚠ {code} {name}: 无数据")
                n_failed += 1
                failed_codes.append(code)
                continue

            df = pd.DataFrame(rows, columns=fields)
            for c in ["open", "high", "low", "close", "volume", "amount",
                      "turn", "pctChg", "peTTM", "pbMRQ"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")

            print(f"  {code} {name}: {len(df)} 条记录")

            # 只处理最新一行
            latest = df.iloc[-1]
            d = {
                "open": float(latest["open"]), "high": float(latest["high"]),
                "low": float(latest["low"]), "close": float(latest["close"]),
                "volume": float(latest["volume"]), "amount": float(latest["amount"]),
                "turnover": float(latest["turn"]), "pct_chg": float(latest["pctChg"]),
            }

            # 计算指标
            trend = compute_trend_indicators(df)
            momentum = compute_momentum_indicators(df)
            volume = compute_volume_indicators(df)
            valuation = compute_valuation_indicators(df, df)

            # 主力资金（akshare，允许失败）
            net_inflow, big_order = fetch_main_flow(code, bs)
            sentiment = {"net_inflow_main": net_inflow, "big_order_ratio": big_order}

            # 组合
            full = {**d, **trend, **momentum, **volume, **valuation, **sentiment}

            # 打分
            scores, verdict = compute_score(full)

            # 写入 DB（upsert）— 37 columns，updated_at 由 DEFAULT 处理
            db.execute("""
                INSERT INTO daily_indicators(
                    code, date, open, high, low, close, volume, amount,
                    pct_chg, turnover,
                    ma5, ma10, ma20, ma60, ma20_slope, adx14,
                    rsi14, macd_diff, macd_dea, macd_hist, roc20,
                    vol_ratio, obv, vol_price_match,
                    pe_ttm, pb, pe_pct3y, pb_pct3y,
                    net_inflow_main, big_order_ratio,
                    score_trend, score_momentum, score_volume,
                    score_valuation, score_sentiment, score_total,
                    verdict
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(code, date) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, volume=excluded.volume, amount=excluded.amount,
                    pct_chg=excluded.pct_chg, turnover=excluded.turnover,
                    ma5=excluded.ma5, ma10=excluded.ma10, ma20=excluded.ma20,
                    ma60=excluded.ma60, ma20_slope=excluded.ma20_slope, adx14=excluded.adx14,
                    rsi14=excluded.rsi14, macd_diff=excluded.macd_diff,
                    macd_dea=excluded.macd_dea, macd_hist=excluded.macd_hist, roc20=excluded.roc20,
                    vol_ratio=excluded.vol_ratio, obv=excluded.obv,
                    vol_price_match=excluded.vol_price_match,
                    pe_ttm=excluded.pe_ttm, pb=excluded.pb,
                    pe_pct3y=excluded.pe_pct3y, pb_pct3y=excluded.pb_pct3y,
                    net_inflow_main=excluded.net_inflow_main,
                    big_order_ratio=excluded.big_order_ratio,
                    score_trend=excluded.score_trend, score_momentum=excluded.score_momentum,
                    score_volume=excluded.score_volume, score_valuation=excluded.score_valuation,
                    score_sentiment=excluded.score_sentiment, score_total=excluded.score_total,
                    verdict=excluded.verdict, updated_at=datetime('now')
            """, (
                code, today,
                full["open"], full["high"], full["low"], full["close"],
                full["volume"], full["amount"],
                full["pct_chg"], full["turnover"],
                full.get("ma5"), full.get("ma10"), full.get("ma20"), full.get("ma60"),
                full.get("ma20_slope"), full.get("adx14"),
                full.get("rsi14"), full.get("macd_diff"), full.get("macd_dea"), full.get("macd_hist"),
                full.get("roc20"),
                full.get("vol_ratio"), full.get("obv"), full.get("vol_price_match"),
                full.get("pe_ttm"), full.get("pb"), full.get("pe_pct3y"), full.get("pb_pct3y"),
                full.get("net_inflow_main"), full.get("big_order_ratio"),
                scores["trend"], scores["momentum"], scores["volume"],
                scores["valuation"], scores["sentiment"], scores["total"],
                verdict,
            ))
            db.commit()
            n_success += 1

        except Exception as e:
            print(f"  ❌ {code} {name}: {e}", file=sys.stderr)
            n_failed += 1
            failed_codes.append(code)
            db.rollback()

    # 4. 大盘环境
    print("\n📈 大盘环境")
    ctx = fetch_market_context(bs)
    if ctx:
        ctx["date"] = today
        ctx["north_inflow"], _ = fetch_north_flow()
        db.execute("""
            INSERT INTO market_context(date, sh_close, sh_pct_chg, sh_ma60,
                cyb_close, cyb_pct_chg, cyb_ma60, volatility20, regime,
                north_inflow, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(date) DO UPDATE SET
                sh_close=excluded.sh_close, sh_pct_chg=excluded.sh_pct_chg,
                sh_ma60=excluded.sh_ma60, cyb_close=excluded.cyb_close,
                cyb_pct_chg=excluded.cyb_pct_chg, cyb_ma60=excluded.cyb_ma60,
                volatility20=excluded.volatility20, regime=excluded.regime,
                north_inflow=excluded.north_inflow, updated_at=datetime('now')
        """, (
            today, ctx.get("sh_close"), ctx.get("sh_pct_chg"), ctx.get("sh_ma60"),
            ctx.get("cyb_close"), ctx.get("cyb_pct_chg"), ctx.get("cyb_ma60"),
            ctx.get("volatility20"), ctx.get("regime"),
            ctx.get("north_inflow"),
        ))
        db.commit()
        print(f"  上证: {ctx.get('sh_close', 'N/A'):.2f} ({ctx.get('sh_pct_chg', 0):+.2f}%) | "
              f"市场: {ctx.get('regime', 'N/A')}")
        if ctx.get("north_inflow"):
            print(f"  北向: {ctx['north_inflow']:.1f}亿")
    else:
        print("  ⚠ 大盘数据获取失败")

    # 5. 写日志
    elapsed = time.time() - start_time
    db.execute("""
        INSERT INTO fetch_log(date, started_at, finished_at, n_stocks, n_success, n_failed, duration_sec, notes)
        VALUES (?, datetime('now'), datetime('now'), ?, ?, ?, ?, ?)
    """, (today, len(stocks), n_success, n_failed, round(elapsed, 1),
          f"failed: {','.join(failed_codes[:5])}" if failed_codes else "ok"))
    db.commit()

    # 6. 报表
    print(f"\n{'='*50}")
    print(f"✅ 完成 | {n_success}/{len(stocks)} 成功 | {elapsed:.1f}s")
    if failed_codes:
        print(f"❌ 失败: {', '.join(failed_codes)}")

    bs.logout()
    db.close()


if __name__ == "__main__":
    main()