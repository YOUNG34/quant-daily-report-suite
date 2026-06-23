"""ATR (Average True Range) 动态止损。

设计目标：
- 替代/叠加固定 -8% HWM 止损，提供更"自适应"的离场信号
- 14 日 ATR × k 倍数（默认 2.5）作为动态止损价
- 不引新依赖（用 stdlib 计算）

边界：
- **检测 + 告警**而非实时强平（paper_trader 没有日内强平能力）
- 触发后写 `portfolio.atr_stop_alerts`，rebalance 段优先平仓
- 冷启动：建仓 < 14 日 → 用固定 8% 兜底

参考资料：
- Wilder (1978) New Concepts in Technical Trading Systems
- k=2.0 偏紧（日内 2σ），k=3.0 偏松（接近 3σ）
- A 股建议 k=2.5（中庸）

公式：
    True Range (TR_n) = max(H_n - L_n, |H_n - C_{n-1}|, |L_n - C_{n-1}|)
    ATR_14 = mean(TR_1..TR_14)            # 简单平均（不是 Wilder 平滑）
    stop_price = current_price - k * ATR_14
"""
from __future__ import annotations
from typing import Iterable, Mapping, Sequence

# k 默认值（A 股推荐 2.5）
DEFAULT_K = 2.5
DEFAULT_PERIOD = 14
# 兜底：建仓不足 14 日时用固定 8% 止损
FALLBACK_STOP_PCT = 0.08


def calc_true_range(high: float, low: float, prev_close: float) -> float:
    """单根 K 线的 True Range。

    TR = max(H-L, |H-prev_close|, |L-prev_close|)
    首日无 prev_close → TR = H - L
    """
    if prev_close is None:
        return high - low
    return max(
        high - low,
        abs(high - prev_close),
        abs(low - prev_close),
    )


def calc_atr(klines: Sequence[Sequence[float]], period: int = DEFAULT_PERIOD) -> float | None:
    """从 K 线序列算 period 日 ATR（简单平均）。

    Args:
        klines: [[date, open, close, high, low, volume], ...] 时间正序
                支持 [[o, c, h, l], ...] 简化形式
        period: 默认 14

    Returns:
        ATR 值（>0），数据不足返回 None
    """
    if not klines or len(klines) < 2:
        return None

    # 标准化 klines → [(high, low, close), ...]
    normalized: list[tuple[float, float, float]] = []
    for k in klines:
        if len(k) >= 5:
            _, o, c, h, low = k[0], k[1], k[2], k[3], k[4]
        elif len(k) == 4:
            o, c, h, low = k
        else:
            return None
        normalized.append((float(h), float(low), float(c)))

    # 取最近 period+1 根（要 prev_close，所以 period 根 TR 需要 period+1 根 K 线）
    recent = normalized[-(period + 1):]
    if len(recent) < 2:
        return None

    trs: list[float] = []
    for i in range(1, len(recent)):
        h, low, c = recent[i]
        prev_c = recent[i - 1][2]  # 上一根的 close
        trs.append(calc_true_range(h, low, prev_c))

    if not trs:
        return None
    return sum(trs) / len(trs)


def calc_dynamic_stop(
    current_price: float,
    atr: float | None,
    k: float = DEFAULT_K,
    fallback_pct: float = FALLBACK_STOP_PCT,
) -> dict:
    """计算动态止损价。

    Args:
        current_price: 当前价
        atr: 14 日 ATR（None → 兜底固定 %）
        k: ATR 倍数
        fallback_pct: ATR 不可用时的固定回撤 %

    Returns:
        {
            "stop_price": float,           # 止损价
            "distance_pct": float,         # (stop - price) / price（负数）
            "method": "atr" | "fallback",
            "k": float,                    # 实际使用的 k
            "atr": float | None,
        }
    """
    if atr is None or atr <= 0 or current_price <= 0:
        # 兜底：固定 %
        stop = current_price * (1 - fallback_pct)
        return {
            "stop_price": round(stop, 3),
            "distance_pct": round(-fallback_pct, 4),
            "method": "fallback",
            "k": k,
            "atr": None,
        }

    stop = current_price - k * atr
    distance = (stop - current_price) / current_price
    return {
        "stop_price": round(stop, 3),
        "distance_pct": round(distance, 4),
        "method": "atr",
        "k": k,
        "atr": round(atr, 4),
    }


def calc_holdings_stops(
    holdings: Mapping[str, dict],
    klines_map: Mapping[str, Sequence[Sequence[float]]],
    k: float = DEFAULT_K,
) -> dict[str, dict]:
    """批量算所有持仓的动态止损。

    Args:
        holdings: {std_code: {name, code, last_price, ...}} — std_code 可带
                  `.SZ/.SH` 后缀或纯代码；K 线查找优先用 `h["code"]`（无后缀），
                  fallback 到 std_code
        klines_map: {code: klines} — code 与 `h["code"]` 字段对应
        k: ATR 倍数

    Returns:
        {std_code: {name, last_price, stop_price, distance_pct, method, atr, k, ...}}
    """
    out: dict[str, dict] = {}
    for std_code, h in holdings.items():
        name = h.get("name", std_code)
        last_price = h.get("last_price", 0.0) or 0.0
        if last_price <= 0:
            continue
        # 优先用 h["code"]（如 "001965"）查 K 线，回退 std_code
        # 兼容裸 holdings dict（无 h["code"] 字段）→ 用 std_code
        lookup_keys = [h.get("code"), std_code]
        klines = []
        for lk in lookup_keys:
            if lk and lk in klines_map and klines_map[lk]:
                klines = klines_map[lk]
                break
        atr = calc_atr(klines) if klines else None
        stop_info = calc_dynamic_stop(last_price, atr, k=k)
        out[std_code] = {
            "name": name,
            "code": h.get("code", std_code),
            "last_price": last_price,
            **stop_info,
        }
    return out


def check_atr_alerts(
    stops: Mapping[str, dict],
    previously_triggered: Iterable[str] | None = None,
) -> list[dict]:
    """检测止损触发的持仓。

    Args:
        stops: calc_holdings_stops 输出
        previously_triggered: 之前已触发的 code 集合（用于累计 days_below）

    Returns:
        触发的列表: [{code, name, current_price, stop_price, distance_pct,
                      atr, k, method, days_below}]
    """
    prev_set = set(previously_triggered or [])
    alerts = []
    for code, s in stops.items():
        if s["last_price"] <= s["stop_price"]:
            # 触发了
            days_below = 1 if code not in prev_set else 2  # 简单：1=新触发，2+=持续
            alerts.append({
                "code": code,
                "name": s["name"],
                "current_price": s["last_price"],
                "stop_price": s["stop_price"],
                "distance_pct": s["distance_pct"],
                "atr": s.get("atr"),
                "k": s.get("k"),
                "method": s.get("method"),
                "triggered": True,
                "days_below": days_below,
            })
    return alerts


__all__ = [
    "DEFAULT_K", "DEFAULT_PERIOD", "FALLBACK_STOP_PCT",
    "calc_true_range", "calc_atr", "calc_dynamic_stop",
    "calc_holdings_stops", "check_atr_alerts",
]
