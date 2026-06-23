"""A 股行业分类（keyword-based，零依赖）。

设计原则：
1. 不依赖 akshare / tushare / 申万行业数据。
2. holdings.name 字符串 substring 匹配行业关键词。
3. 关键词按"先长后短"匹配，避免短词覆盖长词（如「高速公路」优先于「高速」）。
4. 行业分类按"主要暴露"映射——一个公司可能横跨多个细分赛道（地产+金融），
   这里只取第一个最具体的关键词命中。

已知局限：
- 股名里没有行业暗示的大盘蓝筹可能漏检（"中国平安"、"比亚迪"、"贵州茅台"）。
  用关键字列表中的特殊股（"中国平安"→"保险"、"比亚迪"→"汽车"）兜底。
- 同一公司跨多个行业时（如"招商系"覆盖银行/证券/公路），只取第一个命中的关键词。
  这是"主要暴露"近似，不替代申万/中信真实行业分类。

可扩展点：未来若要精确度更高，可在此模块后加 akshare adapter，
保持 `classify(name) -> str` 接口不变。
"""
from __future__ import annotations
from typing import Iterable, Mapping


# (keyword, industry) — keyword 优先用"具体且无歧义"的字符串
# 注意顺序不重要，匹配时按 keyword 长度 DESC 排序
_INDUSTRY_RULES: list[tuple[str, str]] = [
    # ── 金融 ──
    ("银行", "银行"),
    ("保险", "保险"),
    ("证券", "证券"),
    ("信托", "多元金融"),
    ("金控", "多元金融"),
    # 特殊股（keyword 漏检兜底）
    ("中国平安", "保险"),
    # ── 医药 ──
    ("医药", "医药"),
    ("制药", "医药"),
    ("生物", "医药"),
    ("健康", "医药"),
    ("医院", "医药"),
    ("医疗", "医药"),
    # ── 科技/电子 ──
    ("半导体", "电子"),
    ("芯片", "电子"),
    ("集成电路", "电子"),
    ("长电", "电子"),       # 长电科技
    ("鹏鼎", "电子"),       # 鹏鼎控股
    ("兆易", "电子"),       # 兆易创新
    ("电子", "电子"),
    # ── 软件/互联网 ──
    ("用友", "软件"),       # 用友网络
    ("软件", "软件"),
    ("网络", "软件"),
    ("互联网", "软件"),
    # ── 交运/物流 ──
    ("高速公路", "交运"),   # 长词优先
    ("高速", "交运"),
    ("高铁", "交运"),
    ("铁路", "交运"),
    ("机场", "交运"),
    ("航空", "交运"),
    ("港口", "交运"),
    ("海运", "交运"),
    ("航运", "交运"),
    ("物流", "交运"),
    ("公路", "交运"),
    ("公交", "交运"),
    # ── 电力/公用 ──
    ("电网", "电力"),
    ("国电", "电力"),       # 国电南瑞
    ("南瑞", "电力"),
    ("京能", "电力"),       # 京能电力
    ("华能", "电力"),
    ("水电", "电力"),
    ("核电", "电力"),
    ("电力", "电力"),
    ("燃气", "公用"),
    # ── 食品/消费 ──
    ("白酒", "食品"),
    ("啤酒", "食品"),
    ("乳业", "食品"),
    ("海天", "食品"),       # 海天味业
    ("味业", "食品"),
    ("食品", "食品"),
    ("饮料", "食品"),
    # ── 汽车 ──
    ("汽车", "汽车"),
    ("客车", "汽车"),
    ("重卡", "汽车"),
    ("轮胎", "汽车"),
    # 特殊股（keyword 漏检兜底——股名无行业暗示的大盘蓝筹）
    ("比亚迪", "汽车"),
    ("宁德时代", "电池"),
    # ── 地产/建筑 ──
    ("地产", "地产"),
    ("置业", "地产"),
    ("万科", "地产"),
    ("保利", "地产"),
    ("建筑", "建筑"),
    ("工程", "建筑"),
    ("建材", "建筑"),
    ("水泥", "建筑"),
    ("玻璃", "建筑"),
    # ── 钢铁/材料/化工 ──
    ("钢铁", "钢铁"),
    ("有色", "有色"),
    ("矿业", "有色"),
    ("化学", "化工"),
    ("化工", "化工"),
    ("石化", "化工"),
    ("巨石", "建材"),       # 中国巨石
    # ── 军工 ──
    ("军工", "军工"),
    ("航空", "军工"),       # 注意：与"机场/航空"重叠，按长度排序后机场优先
    ("航天", "军工"),
    ("船舶", "军工"),
    # ── 传媒/文娱 ──
    ("传媒", "传媒"),
    ("影视", "传媒"),
    ("出版", "传媒"),
    ("游戏", "传媒"),
    # ── 农业 ──
    ("农业", "农业"),
    ("牧业", "农业"),
    ("种业", "农业"),
    ("水产", "农业"),
]

# 按 keyword 长度 DESC 排序（最长优先），同长度按 INDSUTRY_RULES 原顺序
# 这样「高速公路」会比「高速」先匹配
_SORTED_RULES: list[tuple[str, str]] = sorted(
    _INDUSTRY_RULES, key=lambda r: (-len(r[0]), _INDUSTRY_RULES.index(r))
)


def classify(name: str) -> str:
    """根据股票名称返回行业分类。未命中返回 '其他'。

    匹配策略：最长 keyword 优先（避免短词覆盖长词）。
    例：
        classify("招商公路") → "交运"   # 命中 "公路"
        classify("长电科技") → "电子"   # 命中 "长电"
        classify("杭州银行") → "银行"
        classify("ZZZ")      → "其他"
    """
    if not name:
        return "其他"
    for kw, ind in _SORTED_RULES:
        if kw in name:
            return ind
    return "其他"


def get_industry_breakdown(
    holdings: Mapping[str, dict],
    total_value: float,
) -> dict[str, float]:
    """计算各行业在组合中的市值占比。

    Args:
        holdings: {std_code: {name, market_value, ...}}
        total_value: 组合总市值

    Returns:
        {industry: weight_pct} — 同一行业内多只股票累加
        weight_pct = sum(market_value) / total_value，保留 4 位小数
    """
    if total_value <= 0:
        return {}
    by_ind: dict[str, float] = {}
    for h in holdings.values():
        name = h.get("name", "")
        mv = h.get("market_value", 0.0) or 0.0
        if mv == 0:
            continue
        ind = classify(name)
        by_ind[ind] = by_ind.get(ind, 0.0) + mv
    return {k: round(v / total_value, 4) for k, v in by_ind.items()}


def get_exposure_violations(
    holdings: Mapping[str, dict],
    total_value: float,
    single_stock_cap: float = 0.10,
    industry_cap: float = 0.30,
) -> dict:
    """检查暴露上限违规。

    Returns:
        {
            "single_stock": [(name, code, weight, excess), ...],
            "industry": [(industry, weight, excess), ...],
        }
    """
    if total_value <= 0:
        return {"single_stock": [], "industry": []}

    violations: dict = {"single_stock": [], "industry": []}

    # 单股
    for std_code, h in holdings.items():
        mv = h.get("market_value", 0.0) or 0.0
        w = mv / total_value
        if w > single_stock_cap + 1e-6:
            violations["single_stock"].append(
                (h.get("name", std_code), h.get("code", std_code),
                 round(w, 4), round(w - single_stock_cap, 4))
            )

    # 行业
    breakdown = get_industry_breakdown(holdings, total_value)
    for ind, w in breakdown.items():
        if w > industry_cap + 1e-6:
            violations["industry"].append(
                (ind, w, round(w - industry_cap, 4))
            )

    return violations


__all__ = ["classify", "get_industry_breakdown", "get_exposure_violations"]
