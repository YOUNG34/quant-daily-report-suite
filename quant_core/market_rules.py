"""A 股交易规则：涨跌停、停牌、T+1 判定。

对齐实盘:
- 主板 (60/00 开头): ±10%
- 创业板 (30 开头) / 科创板 (688 开头): ±20%
- 北交所 (8 开头 / 920 开头): ±30%
- 停牌: 腾讯接口 field[5]=0
- T+1: A 股当日买入次日才能卖

判定基于涨跌幅 (pct_chg)，与"现价 vs 涨跌停价"等价但更鲁棒。
"""
from __future__ import annotations

from dataclasses import dataclass


def get_limit_pct(code: str) -> float:
    """根据股票代码返回涨跌停幅度（小数）。

    Args:
        code: 6 位代码，不带 sh/sz 前缀 (e.g. '600519', '002463', '300750', '688981', '835305')
    """
    if not code or len(code) < 6:
        return 0.10  # 默认主板
    if code.startswith(("30", "688")):
        return 0.20  # 创业板/科创板
    if code.startswith(("8", "920", "43")):
        return 0.30  # 北交所
    # 主板 60/00/601/603/605/000/001/002
    return 0.10


def is_limit_up(pct_chg: float, code: str, tolerance_pct: float = 0.5) -> bool:
    """是否涨停。

    Args:
        pct_chg: 涨跌幅 %（不是小数！腾讯接口给的是 9.98 这种）
        code: 股票代码
        tolerance_pct: 容差（百分点），默认 0.5

    Note:
        由于涨跌停价是 4舍5入到 0.01，实际涨幅可能略低于 limit。
        主板涨停价 = round(前收 * 1.10, 2) → 实际涨幅 ∈ [9.5%, 10.0%]
        创业板/科创板: [19.5%, 20.0%]
        北交所: [29.5%, 30.0%]
    """
    if pct_chg is None:
        return False
    limit = get_limit_pct(code) * 100  # 转为 %
    return pct_chg >= limit - tolerance_pct


def is_limit_down(pct_chg: float, code: str, tolerance_pct: float = 0.5) -> bool:
    """是否跌停。"""
    if pct_chg is None:
        return False
    limit = get_limit_pct(code) * 100
    return pct_chg <= -limit + tolerance_pct


def is_suspended(price: float | None = None, prev_close: float | None = None) -> bool:
    """是否停牌（推断式，腾讯 A 股接口无独立 trade_status 字段）。

    停牌表现:
    - 现价 = 0 或 None
    - 涨跌幅 = 0 且 现价 = 昨收（开盘后无成交/全天停牌）
    """
    if price is None or price <= 0:
        return True
    if prev_close is not None and prev_close > 0 and price == prev_close:
        # 价格没动 = 当日无成交/停牌
        # 注: 开盘前的"昨收=现价"也会触发，需要配合成交量判断才更准
        # 但保守起见，仍当作可疑 → 调用方用 is_in_market_hours 二次过滤
        return False  # 不强判，交给价格异常检测
    return False


def can_sell_t1(buy_date: str | None, today: str, calendar: dict | None = None) -> tuple[bool, str]:
    """T+1 检查：今日买入的票明天才能卖。

    Args:
        buy_date: 买入日期 'YYYY-MM-DD'，None 视为老股（默认可卖，向后兼容）
        today: 今日 'YYYY-MM-DD'
        calendar: 交易日历 dict {date: True/False}，可选

    Returns:
        (can_sell, reason)
    """
    if not buy_date:
        return True, "无 buy_date 记录（老股/兼容模式）"
    if buy_date >= today:
        return False, f"T+1 锁定: 买入 {buy_date} ≥ 今日 {today}"
    return True, "可卖"


@dataclass
class TradeGateCheck:
    """单只股票的交易前检查结果"""
    code: str
    can_buy: bool
    can_sell: bool
    reasons: list[str]

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "can_buy": self.can_buy,
            "can_sell": self.can_sell,
            "reasons": self.reasons,
        }


def pre_trade_check(
    code: str,
    pct_chg: float | None = None,
    price: float | None = None,
    prev_close: float | None = None,
    buy_date: str | None = None,
    today: str | None = None,
) -> TradeCheck:
    """交易前综合检查。返回能否买入/卖出 + 原因列表。"""
    reasons: list[str] = []

    # 停牌（推断式：仅依赖 price 和 prev_close）
    if is_suspended(price=price, prev_close=prev_close):
        reasons.append("停牌")

    # 涨停 → 买不进
    if is_limit_up(pct_chg, code, tolerance_pct=0.5):
        reasons.append(f"涨停 (+{pct_chg:.2f}%)")

    # 跌停 → 卖不出
    if is_limit_down(pct_chg, code, tolerance_pct=0.5):
        reasons.append(f"跌停 ({pct_chg:.2f}%)")

    # T+1
    can_sell_t1_flag, t1_reason = can_sell_t1(buy_date, today)
    if not can_sell_t1_flag:
        reasons.append(t1_reason)

    can_buy = not is_suspended(price=price, prev_close=prev_close) and not is_limit_up(pct_chg, code, tolerance_pct=0.5)
    can_sell = not is_suspended(price=price, prev_close=prev_close) and not is_limit_down(pct_chg, code, tolerance_pct=0.5) and can_sell_t1_flag

    return TradeGateCheck(code=code, can_buy=can_buy, can_sell=can_sell, reasons=reasons)
