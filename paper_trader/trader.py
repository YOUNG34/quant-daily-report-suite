#!/usr/bin/env python3
"""
纸交模拟器 — 基于腾讯财经实时行情
用法:
  python trader.py              # 拉行情、更新净值、输出报告
  python trader.py --init       # 初始化建仓（只用一次）
  python trader.py --init --capital 1000000 --stocks stocks.csv
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import copy
from datetime import datetime
from pathlib import Path

# === schema 版本控制 ===
# v0: 原始（无 schema_version 字段，依赖隐式约定）
# v1: 显式 schema_version + last_rebalance_snapshot + migration_log
_CURRENT_SCHEMA_VERSION = 1

# 共享配置（向前兼容：config 加载失败用原硬编码值）
try:
    # 仓库根加到 sys.path，让 import quant_core.* 能工作（无论仓库放哪里）
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from quant_core.config import get_config
    from quant_core.logger import get_logger
    from quant_core.market_rules import pre_trade_check, get_limit_pct
    from quant_core.data_source import fetch_tencent_quotes_with_retry, fetch_klines_with_retry
    from quant_core.industry import classify, get_industry_breakdown, get_exposure_violations
    from quant_core.atr_stop import calc_holdings_stops, check_atr_alerts
    _cfg = get_config()
    _log = get_logger("trader")
    log = _log  # 别名，兼容历史代码 log.X
    _STOP_LOSS_PCT = _cfg.risk.stop_loss_pct
    _MIN_TURNOVER_PCT = _cfg.risk.min_turnover_pct
    _LOT_SIZE = _cfg.paper_a.lot_size
    _INIT_CAPITAL = _cfg.paper_a.init_capital
    _ALERT_NAV_DROP = _cfg.alerts.nav_daily_drop_pct
    # Step 10 交易成本
    _COMMISSION_RATE = _cfg.paper_a.commission_rate
    _STAMP_TAX_RATE = _cfg.paper_a.stamp_tax_rate
    _MIN_COMMISSION = _cfg.paper_a.min_commission
    # Step 9 暴露上限
    _SINGLE_STOCK_CAP_PCT = _cfg.risk.single_stock_cap_pct
    _INDUSTRY_CAP_PCT = _cfg.risk.industry_cap_pct
    # Step 13 ATR 动态止损
    _ATR_PERIOD = _cfg.risk.atr_period
    _ATR_K = _cfg.risk.atr_k
    _ATR_FALLBACK_PCT = _cfg.risk.atr_fallback_pct
except Exception as _e:
    # 回退硬编码（保持向后兼容）
    _log = None
    log = None
    _cfg = None
    _STOP_LOSS_PCT = 0.08
    _MIN_TURNOVER_PCT = 0.30
    _LOT_SIZE = 100
    _INIT_CAPITAL = 1_000_000
    _ALERT_NAV_DROP = 0.03
    _COMMISSION_RATE = 0.00025
    _STAMP_TAX_RATE = 0.001
    _MIN_COMMISSION = 5.0
    # Step 9 默认暴露上限
    _SINGLE_STOCK_CAP_PCT = 0.10
    _INDUSTRY_CAP_PCT = 0.30
    # Step 13 ATR 默认值
    _ATR_PERIOD = 14
    _ATR_K = 2.5
    _ATR_FALLBACK_PCT = 0.08

# === 配置 ===
BASE_DIR = Path(__file__).parent
PORTFOLIO_FILE = BASE_DIR / "portfolio.json"
HISTORY_FILE = BASE_DIR / "history.csv"

# 代码 → 交易所前缀映射
def to_tencent_code(code: str) -> str:
    """002463 → sz002463, 600183 → sh600183"""
    if code.startswith(("60", "68")):
        return f"sh{code}"
    elif code.startswith(("00", "30", "00")):
        return f"sz{code}"
    return f"sz{code}"  # fallback

def to_standard_code(code: str) -> str:
    """002463 → 002463.SZ, 600183 → 600183.SH"""
    if code.startswith(("60", "68")):
        return f"{code}.SH"
    else:
        return f"{code}.SZ"


# === 交易成本计算 (Step 10) ===
def calc_commission(amount: float) -> float:
    """双边：max(amount * rate, MIN)"""
    return max(amount * _COMMISSION_RATE, _MIN_COMMISSION)


def calc_buy_cost(amount: float) -> float:
    """买入总成本 = 成交价 + 佣金"""
    return amount + calc_commission(amount)


def calc_sell_proceeds(amount: float) -> float:
    """卖出净到手 = 成交价 - 佣金 - 印花税（印花税仅卖出）"""
    return amount - calc_commission(amount) - amount * _STAMP_TAX_RATE


def calc_round_trip_cost(amount: float) -> float:
    """一次完整买卖的成本（用于 PnL 估算）"""
    return calc_commission(amount) * 2 + amount * _STAMP_TAX_RATE

# 初始持仓 — 10 只剔除下跌的因子 Top 10（2026-05-26 选股结果）
DEFAULT_HOLDINGS = {
    "002463.SZ": {"name": "沪电股份", "code": "002463"},
    "603986.SH": {"name": "兆易创新", "code": "603986"},
    "000960.SZ": {"name": "锡业股份", "code": "000960"},
    "600183.SH": {"name": "生益科技", "code": "600183"},
    "002916.SZ": {"name": "深南电路", "code": "002916"},
    "600110.SH": {"name": "诺德股份", "code": "600110"},
    "000725.SZ": {"name": "京东方A",  "code": "000725"},
    "600578.SH": {"name": "京能电力", "code": "600578"},
    "600176.SH": {"name": "中国巨石", "code": "600176"},
    "002938.SZ": {"name": "鹏鼎控股", "code": "002938"},
}


# === 组合管理 ===
def init_portfolio(capital: float, holdings: dict):
    """初始化组合：按今日价格等权买入"""
    codes = [h["code"] for h in holdings.values()]
    quotes = fetch_tencent_quotes_with_retry(codes, portfolio_file=PORTFOLIO_FILE, context="init")
    
    if not quotes:
        log.error("无法获取行情，初始化失败")
        sys.exit(1)
    
    weight_per_stock = capital / len(holdings)
    
    portfolio = {
        "schema_version": _CURRENT_SCHEMA_VERSION,
        "init_date": datetime.now().strftime("%Y-%m-%d"),
        "init_capital": capital,
        "holdings": {},
        "last_nav": capital,
        "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_rebalance_snapshot": None,
        "migration_log": [],
        "rebalance_log": [],
        "total_commission": 0.0,  # Step 10
        "total_stamp_tax": 0.0,   # Step 10
        "transaction_log": [],     # Step 10
    }

    total_cost = 0
    total_commission_init = 0
    init_date = datetime.now().strftime("%Y-%m-%d")
    for std_code, info in holdings.items():
        code = info["code"]
        q = quotes.get(code)
        if not q:
            log.error("无法获取 {info['name']}({code}) 行情")
            sys.exit(1)

        # 按 1 手取整（lot_size 从 config 读）
        shares = int(weight_per_stock / q["price"] / _LOT_SIZE) * _LOT_SIZE
        cost = shares * q["price"]
        commission = calc_commission(cost)  # Step 10: 建仓也扣佣金

        portfolio["holdings"][std_code] = {
            "name": info["name"],
            "code": code,
            "shares": shares,
            "cost_price": round(q["price"], 3),
            "total_cost": round(cost, 2),
            "weight": weight_per_stock / capital,
        }
        total_cost += cost + commission
        total_commission_init += commission
        portfolio["transaction_log"].append({
            "action": "buy", "code": code, "shares": shares,
            "price": q["price"], "gross": cost,
            "commission": commission, "stamp_tax": 0.0,
            "net": cost + commission, "date": init_date, "trigger": "init",
        })
        log.info("{info['name']}({code}) 买入 {shares} 股 @{q['price']:.2f} = {cost:,.0f} (佣金 {commission:.2f})")
    portfolio["total_invested"] = round(total_cost, 2)
    portfolio["cash_remaining"] = round(capital - total_cost, 2)
    portfolio["last_nav"] = round(total_cost, 2)
    portfolio["total_commission"] = round(total_commission_init, 2)

    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2, ensure_ascii=False)

    log.info("\n总投资: {total_cost:,.0f} (含佣金 {c:,.2f}) / 总资金: {capital:,.0f}", c=total_commission_init)
    return portfolio


def _migrate(portfolio: dict) -> dict:
    """迁移旧 schema 到当前版本。

    - v0（无 schema_version）→ v1：加 schema_version=1, migration_log=[], last_rebalance_snapshot=None
    - v1 → v1: noop
    - vN（N>1）→ 报错（需要显式升级路径）
    """
    v = portfolio.get("schema_version")
    if v is None:
        # v0 → v1
        portfolio.setdefault("migration_log", [])
        portfolio["migration_log"].append({
            "from": 0,
            "to": 1,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reason": "init schema_version=1 + snapshot/migration_log fields",
        })
        portfolio["schema_version"] = 1
        if "last_rebalance_snapshot" not in portfolio:
            portfolio["last_rebalance_snapshot"] = None
        log.warning("portfolio.json 缺 schema_version, 已自动迁移 v0→v1")
    elif v == 1:
        return portfolio
    else:
        log.error("portfolio.json schema_version={} > 当前={}, 请先升级代码", v, _CURRENT_SCHEMA_VERSION)
        sys.exit(1)
    return portfolio


def load_portfolio() -> dict:
    """加载组合状态 → 迁移到当前 schema → 写回（如果是 v0）

    Step 10 兼容：v1 缺 total_commission/total_stamp_tax/transaction_log 时自动补 0
    """
    if not PORTFOLIO_FILE.exists():
        return None
    with open(PORTFOLIO_FILE) as f:
        portfolio = json.load(f)
    if portfolio.get("schema_version") != _CURRENT_SCHEMA_VERSION:
        _migrate(portfolio)
        # 写回（v0 → v1 时持久化）
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(portfolio, f, indent=2, ensure_ascii=False)
    # Step 10: 给老 v1 补 0 字段（不破坏 schema_version=1）
    portfolio.setdefault("total_commission", 0.0)
    portfolio.setdefault("total_stamp_tax", 0.0)
    portfolio.setdefault("transaction_log", [])
    return portfolio


def rebalance_portfolio(portfolio: dict, new_codes: list[str], capital: float = None, position_pct: float = 1.0, timing_info: dict = None, min_turnover_pct: float = None) -> dict:
    """根据新选股列表重建持仓
    - 卖出当前不在新列表的（按当前 last_price）
    - 等权买入新选股（按现价，按 lot_size 取整，从 config.paper_a.lot_size 读）
    - position_pct: 大盘择时仓位（1.0=满仓, 0.5=半仓, 0.0=空仓）
    - capital: 默认为当前 total_invested + cash_remaining
    - min_turnover_pct: 换手率下限（默认从 config.risk.min_turnover_pct 读）
    """
    if min_turnover_pct is None:
        min_turnover_pct = _MIN_TURNOVER_PCT
    if _log:
        _log.info("rebalance: {} 只, 换手约束 {:.0%}, 仓位 {:.0%}",
                  len(new_codes), min_turnover_pct, position_pct)
    old_holdings = portfolio["holdings"]
    # === 关键：在改 holdings 之前存完整快照 ===
    portfolio["last_rebalance_snapshot"] = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trigger": "monthly_rebalance",
        "holdings": copy.deepcopy(old_holdings),
    }
    old_codes = {h["code"] for h in old_holdings.values()}
    new_code_set = set(new_codes)

    # 计算可用资金（capital 默认 = init_capital）
    if capital is None:
        capital = portfolio.get("init_capital", _INIT_CAPITAL)

    # 最小换手约束：计算换手率（卖出 + 买入 都算）
    if old_codes:
        to_sell = old_codes - new_code_set
        to_buy = new_code_set - old_codes
        turnover_pct = (len(to_sell) + len(to_buy)) / (len(old_codes) + len(new_code_set))
        if turnover_pct < min_turnover_pct:
            if _log:
                _log.info("⏭️ 换手 {turnover:.1%} < {th:.0%} 跳过", turnover=turnover_pct, th=min_turnover_pct)
            log.info("⏭️ 换手率 {turnover_pct:.1%} < {min_turnover_pct:.0%}，跳过调仓（保留现有持仓）")
            portfolio.setdefault("rebalance_log", []).append({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "skipped": True,
                "reason": f"turnover {turnover_pct:.1%} < {min_turnover_pct:.0%}",
                "to_sell": sorted(to_sell),
                "to_buy": sorted(to_buy),
                "old_codes": sorted(old_codes),
                "new_codes": sorted(new_code_set),
            })
            with open(PORTFOLIO_FILE, "w") as f:
                json.dump(portfolio, f, indent=2, ensure_ascii=False)
            return portfolio

    # 卖出：不在新选股中的 → 按 last_price 卖，资金入账
    # 涨跌停/T+1 规则：跌停或 T+1 锁定 → 保留到下一轮
    # Step 10: sell_proceeds 是扣佣金+印花税后的净到手
    sell_proceeds = 0
    sold = []
    kept = []  # 因规则无法卖出的
    today_str = datetime.now().strftime("%Y-%m-%d")
    total_commission_round = 0
    total_stamp_tax_round = 0
    tx_log_round = []
    for std_code, h in list(old_holdings.items()):
        if h["code"] not in new_code_set:
            # 拉该股当日行情做 T+1/涨跌停检查（如果不在新选股里，可能没在 batch 中）
            code = h["code"]
            q = quotes.get(code) if 'quotes' in locals() else None
            if q is None:
                # 单独拉这只
                try:
                    single_quote = fetch_tencent_quotes_with_retry(
                        [code], portfolio_file=PORTFOLIO_FILE, context="rebalance-single"
                    )
                    q = single_quote.get(code)
                except Exception:
                    q = None
            if q is None:
                # 拉不到行情 → 保守策略：假设可卖（避免因行情接口问题永久卡死）
                if _log:
                    _log.warning("卖出 {} 拉不到行情, 假设可卖", code)
                price = h.get("last_price", h.get("cost_price", 0))
                gross = h["shares"] * price
                commission = calc_commission(gross)
                stamp_tax = gross * _STAMP_TAX_RATE
                sell_proceeds += gross - commission - stamp_tax
                total_commission_round += commission
                total_stamp_tax_round += stamp_tax
                tx_log_round.append({"action": "sell", "code": code, "shares": h["shares"],
                                     "price": price, "gross": gross,
                                     "commission": commission, "stamp_tax": stamp_tax,
                                     "net": gross - commission - stamp_tax, "force": True})
                sold.append(f"{h['name']}({code})")
                continue
            check = pre_trade_check(
                code=code,
                pct_chg=q.get("pct_chg"),
                price=q.get("price"),
                prev_close=q.get("prev_close"),
                buy_date=h.get("buy_date"),
                today=today_str,
            )
            if not check.can_sell:
                kept.append(f"{h['name']}({code}) ← {','.join(check.reasons)}")
                if _log:
                    _log.info("保留 {} 不卖: {}", code, ','.join(check.reasons))
                continue
            price = h.get("last_price", h.get("cost_price", 0))
            gross = h["shares"] * price
            commission = calc_commission(gross)
            stamp_tax = gross * _STAMP_TAX_RATE
            sell_proceeds += gross - commission - stamp_tax
            total_commission_round += commission
            total_stamp_tax_round += stamp_tax
            tx_log_round.append({"action": "sell", "code": code, "shares": h["shares"],
                                 "price": price, "gross": gross,
                                 "commission": commission, "stamp_tax": stamp_tax,
                                 "net": gross - commission - stamp_tax})
            sold.append(f"{h['name']}({code})")
    if sold:
        log.info("[卖出] {' / '.join(sold)}")
    if kept:
        log.info("[保留] {' / '.join(kept)}（下次再卖）")
    # 把卖出扣的费用计入 portfolio
    portfolio.setdefault("total_commission", 0.0)
    portfolio.setdefault("total_stamp_tax", 0.0)
    portfolio.setdefault("transaction_log", [])
    portfolio["total_commission"] += total_commission_round
    portfolio["total_stamp_tax"] += total_stamp_tax_round
    for tx in tx_log_round:
        tx["date"] = today_str
        tx["trigger"] = "rebalance"
        portfolio["transaction_log"].append(tx)
    # 计算可买资金
    cash_pool = sell_proceeds + portfolio.get("cash_remaining", 0)
    # 仍持有的市值也算可买
    held_market_value = sum(
        h["shares"] * h.get("last_price", h.get("cost_price", 0))
        for std_code, h in old_holdings.items()
        if h["code"] in new_code_set
    )
    cash_pool += held_market_value
    log.info("可用资金池: {cash_pool:,.0f}")
    # 拉新选股行情
    quotes = fetch_tencent_quotes_with_retry(
        list(new_code_set), portfolio_file=PORTFOLIO_FILE, context="rebalance"
    )
    if not quotes:
        log.error("无法获取新选股行情，rebalance 失败")
        return portfolio

    weight_per = cash_pool * position_pct / len(new_code_set) if new_code_set else 0
    new_holdings = {}
    total_cost = 0
    skipped = []  # 因规则不能买入的
    for code in new_code_set:
        q = quotes.get(code)
        if not q:
            log.warning("跳过 {code}（无行情）")
            continue
        # 涨跌停/停牌检查
        check = pre_trade_check(
            code=code,
            pct_chg=q.get("pct_chg"),
            price=q.get("price"),
            prev_close=q.get("prev_close"),
        )
        if not check.can_buy:
            skipped.append(f"{q.get('name', code)}({code}) ← {','.join(check.reasons)}")
            if _log:
                _log.info("跳过买入 {}: {}", code, ','.join(check.reasons))
            log.info("[跳过] {q.get('name', code)}({code}) ({','.join(check.reasons)})")
            continue
        shares = int(weight_per / q["price"] / _LOT_SIZE) * _LOT_SIZE
        if shares == 0:
            log.warning("跳过 {code}（资金不足 1 手）")
            continue
        cost = shares * q["price"]
        commission = calc_commission(cost)
        std_code = to_standard_code(code)
        new_holdings[std_code] = {
            "name": q.get("name", code),
            "code": code,
            "shares": shares,
            "cost_price": round(q["price"], 3),
            "total_cost": round(cost, 2),
            "weight": weight_per / capital,
            "last_price": q["price"],
            "market_value": round(cost, 2),
            "pnl": 0.0,
            "pnl_pct": 0.0,
            "buy_date": today_str,  # T+1 用
        }
        total_cost += cost + commission  # Step 10: 扣佣金
        portfolio.setdefault("total_commission", 0.0)
        portfolio.setdefault("total_stamp_tax", 0.0)
        portfolio.setdefault("transaction_log", [])
        portfolio["total_commission"] += commission
        portfolio["transaction_log"].append({
            "action": "buy", "code": code, "shares": shares,
            "price": q["price"], "gross": cost,
            "commission": commission, "stamp_tax": 0.0,
            "net": cost + commission, "date": today_str, "trigger": "rebalance",
        })
        log.info("[买入] {q.get('name', code)}({code}) {shares} 股 @{q['price']:.2f} = {cost:,.0f} (佣金 {commission:.2f})")
    portfolio["holdings"] = new_holdings
    portfolio["total_invested"] = round(total_cost, 2)
    portfolio["cash_remaining"] = round(cash_pool - total_cost, 2)
    portfolio["last_nav"] = round(total_cost, 2)
    portfolio["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    portfolio["position_pct"] = position_pct  # 记录当前择时仓位
    if timing_info:
        portfolio["timing_info"] = timing_info
    # 记录 rebalance 历史
    if "rebalance_log" not in portfolio:
        portfolio["rebalance_log"] = []
    portfolio["rebalance_log"].append({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "sold_count": len(sold),
        "new_count": len(new_holdings),
        "old_codes": sorted(old_codes),
        "new_codes": sorted(new_code_set),
        "sold_list": sold,
    })

    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2, ensure_ascii=False)

    return portfolio


def check_drawdown_stop(portfolio: dict, current_nav: float, threshold: float = None) -> bool:
    """最大回撤止损：HWM -X% 强制清仓（X 从 config.risk.stop_loss_pct 读）
    Returns True if stop-loss triggered
    """
    if threshold is None:
        threshold = _STOP_LOSS_PCT
    peak = portfolio.get("peak_nav", portfolio.get("init_capital", 1_000_000))
    if peak <= 0:
        return False
    dd = current_nav / peak - 1
    if dd < -threshold and portfolio.get("holdings"):
        # 触发止损 → 按当前价清仓（Step 10: 扣佣金 + 印花税）
        sell_proceeds = 0
        sold_list = []
        total_commission_stop = 0
        total_stamp_tax_stop = 0
        stop_tx = []
        stop_date = datetime.now().strftime("%Y-%m-%d")
        for std_code, h in list(portfolio["holdings"].items()):
            price = h.get("last_price", h.get("cost_price", 0))
            gross = h["shares"] * price
            commission = calc_commission(gross)
            stamp_tax = gross * _STAMP_TAX_RATE
            net = gross - commission - stamp_tax
            sell_proceeds += net
            total_commission_stop += commission
            total_stamp_tax_stop += stamp_tax
            stop_tx.append({"action": "sell", "code": h["code"], "shares": h["shares"],
                            "price": price, "gross": gross,
                            "commission": commission, "stamp_tax": stamp_tax,
                            "net": net, "date": stop_date, "trigger": "stop_loss"})
            sold_list.append(f"{h['name']}({h['code']})")
        portfolio["cash_remaining"] = round(portfolio.get("cash_remaining", 0) + sell_proceeds, 2)
        portfolio["holdings"] = {}
        portfolio["last_nav"] = round(portfolio["cash_remaining"], 2)
        portfolio.setdefault("total_commission", 0.0)
        portfolio.setdefault("total_stamp_tax", 0.0)
        portfolio.setdefault("transaction_log", [])
        portfolio["total_commission"] += total_commission_stop
        portfolio["total_stamp_tax"] += total_stamp_tax_stop
        portfolio["transaction_log"].extend(stop_tx)
        portfolio["stopped_out"] = True
        portfolio["stopped_out_at"] = {
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "nav_at_stop": current_nav,
            "peak": peak,
            "dd_pct": round(dd * 100, 2),
            "sold": sold_list,
            "stop_cost_commission": round(total_commission_stop, 2),
            "stop_cost_stamp_tax": round(total_stamp_tax_stop, 2),
        }
        log.info("🛑 触发最大回撤止损: dd={dd:.2%} (peak={peak:,.0f} → {current_nav:,.0f})")
        log.info("清仓: {', '.join(sold_list)} | 扣佣金 {c:,.2f} / 印花税 {s:,.2f}",
                 c=total_commission_stop, s=total_stamp_tax_stop)
        return True
    return False


def update_nav(portfolio: dict) -> dict:
    """拉实时行情，更新净值"""
    holdings = portfolio["holdings"]
    codes = [h["code"] for h in holdings.values()]
    quotes = fetch_tencent_quotes_with_retry(
        codes, portfolio_file=PORTFOLIO_FILE, context="update_nav"
    )
    
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    
    total_value = portfolio.get("cash_remaining", 0)
    details = []
    
    for std_code, h in holdings.items():
        code = h["code"]
        q = quotes.get(code)
        if not q:
            log.warning("无法获取 {h['name']}({code}) 行情，使用上次价格")
            current_price = h.get("last_price", h["cost_price"])
            pnl = 0
            pct = 0
        else:
            current_price = q["price"]
            pnl = (current_price - h["cost_price"]) * h["shares"]
            pct = (current_price / h["cost_price"] - 1) * 100
        
        market_value = h["shares"] * current_price
        total_value += market_value
        
        # 更新持仓
        h["last_price"] = round(current_price, 3)
        h["market_value"] = round(market_value, 2)
        h["pnl"] = round(pnl, 2)
        h["pnl_pct"] = round(pct, 2)
        
        details.append({
            "name": h["name"],
            "code": code,
            "shares": h["shares"],
            "cost": h["cost_price"],
            "price": h["last_price"],
            "value": h["market_value"],
            "pnl": h["pnl"],
            "pnl_pct": h["pnl_pct"],
        })
    
    prev_nav = portfolio["last_nav"]
    nav_return = (total_value / portfolio["init_capital"] - 1) * 100
    # Step 10: 扣费后真实收益（mark-to-market 净值 - 累计费用）
    init_cap = portfolio["init_capital"]
    total_cost = portfolio.get("total_commission", 0.0) + portfolio.get("total_stamp_tax", 0.0)
    realized_value = total_value - total_cost
    realized_return = (realized_value / init_cap - 1) * 100

    portfolio["last_nav"] = round(total_value, 2)
    portfolio["last_update"] = f"{date_str} {time_str}"
    portfolio["nav_return_pct"] = round(nav_return, 2)
    portfolio["realized_return_pct"] = round(realized_return, 2)  # Step 10
    portfolio["accumulated_cost"] = round(total_cost, 2)  # Step 10

    # Step 9: 行业暴露 + 违规检测
    industry_breakdown = get_industry_breakdown(holdings, total_value)
    violations = get_exposure_violations(
        holdings, total_value,
        single_stock_cap=_SINGLE_STOCK_CAP_PCT,
        industry_cap=_INDUSTRY_CAP_PCT,
    )
    portfolio["industry_breakdown"] = industry_breakdown
    portfolio["exposure_violations"] = {
        "single_stock": violations["single_stock"],
        "industry": violations["industry"],
    }

    # Step 13: ATR 动态止损（拉 14 日 K 线 → 算 ATR → 写 dynamic_stops + alerts）
    atr_klines_map: dict[str, list] = {}
    if holdings:
        try:
            # 拉每只持仓的 _ATR_PERIOD 日 K 线
            atr_codes = [h["code"] for h in holdings.values()]
            atr_klines_map = fetch_klines_with_retry(atr_codes, period=_ATR_PERIOD)
        except Exception as _ae:
            if log:
                log.warning("ATR K 线拉取失败: {}", _ae)
            atr_klines_map = {}

    # 把 K 线喂给 calc_holdings_stops（无 K 线的持仓会自动 fallback）
    dynamic_stops = calc_holdings_stops(
        holdings, atr_klines_map, k=_ATR_K
    )
    # 修正 fallback_pct（用配置的兜底值）
    for _code in list(dynamic_stops.keys()):
        _s = dynamic_stops[_code]
        if _s.get("method") == "fallback":
            _s["stop_price"] = round(_s["last_price"] * (1 - _ATR_FALLBACK_PCT), 3)
            _s["distance_pct"] = round(-_ATR_FALLBACK_PCT, 4)

    # 检查触发：合并新触发 + 已触发（累计 days_below）
    previously_triggered = {
        a["code"] for a in portfolio.get("atr_stop_alerts", [])
    }
    atr_alerts = check_atr_alerts(dynamic_stops, previously_triggered=previously_triggered)
    # 已不触发的从 alerts 移除（rebalance 后或价格回升）
    triggered_codes = {a["code"] for a in atr_alerts}
    for old_code in previously_triggered - triggered_codes:
        if log:
            log.info("ATR 止损解除: {}", old_code)
    portfolio["dynamic_stops"] = dynamic_stops
    portfolio["atr_stop_alerts"] = atr_alerts
    if log and atr_alerts:
        log.warning("⚠️ ATR 止损触发: {} 只 ({})",
                    len(atr_alerts),
                    ", ".join(f"{a['name']}({a['code']})" for a in atr_alerts))

    # HWM 更新 + 最大回撤止损检查
    init_cap = portfolio["init_capital"]
    portfolio["peak_nav"] = round(max(portfolio.get("peak_nav", init_cap), total_value), 2)
    portfolio["drawdown_pct"] = round((total_value / portfolio["peak_nav"] - 1) * 100, 2)
    if check_drawdown_stop(portfolio, total_value):
        # 止损触发后重算 total_value（现金 = last_nav）
        total_value = portfolio["last_nav"]
        nav_return = (total_value / init_cap - 1) * 100
        portfolio["nav_return_pct"] = round(nav_return, 2)
        # 止损事件也写进 history
        with open(HISTORY_FILE, "a") as _f:
            _f.write(f"{date_str},{time_str},{total_value:.2f},{nav_return:.2f},STOPPED_OUT\n")

    # 保存
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2, ensure_ascii=False)

    # 追加历史
    history_exists = HISTORY_FILE.exists()
    with open(HISTORY_FILE, "a") as f:
        if not history_exists:
            f.write("date,time,nav,nav_return_pct\n")
        f.write(f"{date_str},{time_str},{total_value:.2f},{nav_return:.2f}\n")
    
    return {
        "nav": total_value,
        "return_pct": nav_return,
        "realized_return_pct": realized_return,  # Step 10: 扣累计成本后的真实收益
        "accumulated_cost": total_cost,           # Step 10: 累计佣金+印花税
        "total_commission": portfolio.get("total_commission", 0.0),
        "total_stamp_tax": portfolio.get("total_stamp_tax", 0.0),
        "industry_breakdown": industry_breakdown,  # Step 9
        "exposure_violations": violations,         # Step 9
        "single_stock_cap": _SINGLE_STOCK_CAP_PCT, # Step 9
        "industry_cap": _INDUSTRY_CAP_PCT,         # Step 9
        "dynamic_stops": dynamic_stops,            # Step 13: ATR 动态止损
        "atr_stop_alerts": atr_alerts,             # Step 13: 触发的告警
        "atr_k": _ATR_K,                           # Step 13: ATR 倍数
        "atr_period": _ATR_PERIOD,                 # Step 13: ATR 窗口
        "details": details,
        "init_capital": portfolio["init_capital"],
        "init_date": portfolio["init_date"],
    }


def print_report(result: dict):
    """打印绩效报告"""
    print()
    print("=" * 68)
    print("  📊 纸交组合 — 绩效报告")
    print("=" * 68)
    print(f"  建仓日期: {result['init_date']}")
    print(f"  初始资金: {result['init_capital']:,.0f}")
    print(f"  当前净值: {result['nav']:,.2f}")
    print(f"  累计收益: {result['return_pct']:+.2f}%")
    print(f"  扣费后收益: {result['realized_return_pct']:+.2f}%")
    print(f"  累计成本: {result['accumulated_cost']:,.2f} (佣金 {result['total_commission']:,.2f} + 印花税 {result['total_stamp_tax']:,.2f})")
    print("-" * 68)

    # Step 9: 行业暴露 + 违规告警
    breakdown = result.get("industry_breakdown", {})
    violations = result.get("exposure_violations", {})
    ssc = result.get("single_stock_cap", 0.10)
    ic = result.get("industry_cap", 0.30)
    if breakdown:
        print(f"  📊 行业暴露 (单股 ≤ {ssc*100:.0f}% / 行业 ≤ {ic*100:.0f}%):")
        for ind, w in sorted(breakdown.items(), key=lambda x: -x[1]):
            flag = " ⚠️" if w > ic + 1e-6 else ""
            bar = "█" * int(w * 30)
            print(f"    {ind:<8} {w*100:5.2f}%  {bar}{flag}")
        # 违规告警
        if violations.get("single_stock") or violations.get("industry"):
            print()
            print("  🚨 暴露违规:")
            for name, code, w, excess in violations.get("single_stock", []):
                print(f"    ⚠️ 单股 {name}({code}) {w*100:.2f}% 超 {ssc*100:.0f}% 上限 {excess*100:+.2f}%")
            for ind, w, excess in violations.get("industry", []):
                print(f"    ⚠️ 行业「{ind}」{w*100:.2f}% 超 {ic*100:.0f}% 上限 {excess*100:+.2f}%")
        print("-" * 68)

    print(f"  {'标的':<8} {'代码':<8} {'持仓':>6} {'成本':>8} {'现价':>8} {'市值':>10} {'盈亏':>10} {'盈亏%':>7}")
    print("-" * 68)

    total_pnl = 0
    for d in result["details"]:
        print(f"  {d['name']:<8} {d['code']:<8} {d['shares']:>6} {d['cost']:>8.2f} {d['price']:>8.2f} {d['value']:>10,.0f} {d['pnl']:>+10,.0f} {d['pnl_pct']:>+6.2f}%")
        total_pnl += d["pnl"]

    print("-" * 68)
    print(f"  总盈亏: {total_pnl:+,.0f}")

    # Step 13: ATR 动态止损表
    dynamic_stops = result.get("dynamic_stops", {})
    atr_alerts = result.get("atr_stop_alerts", [])
    atr_k = result.get("atr_k", 2.5)
    atr_period = result.get("atr_period", 14)
    if dynamic_stops:
        print()
        print(f"  🛡️ ATR 动态止损 (window={atr_period}日, k={atr_k}σ):")
        print(f"  {'标的':<8} {'现价':>7} {'ATR':>7} {'止损价':>7} {'距离':>7}  {'方法':<8}")
        print("  " + "-" * 64)
        for code, s in dynamic_stops.items():
            method_tag = "fallback" if s.get("method") == "fallback" else f"ATR×{s.get('k', atr_k)}"
            atr_v = s.get("atr") or 0
            print(f"  {s['name']:<8} {s['last_price']:>7.2f} {atr_v:>7.3f} "
                  f"{s['stop_price']:>7.2f} {s['distance_pct']*100:>+6.2f}%  {method_tag}")
        # 触发告警
        if atr_alerts:
            print()
            print(f"  🚨 ATR 止损触发 ({len(atr_alerts)} 只):")
            for a in atr_alerts:
                flag = f"已 {a['days_below']} 天" if a.get('days_below', 0) > 1 else "新触发"
                print(f"    ⚠️ {a['name']}({a['code']}) 现价 {a['current_price']:.2f} < "
                      f"止损 {a['stop_price']:.2f} ({a['distance_pct']*100:+.2f}%) [{flag}]")
        else:
            print()
            print("  ✅ 当前无 ATR 止损触发")
        print("-" * 68)

    print()
    print("  ⚠️ 以上为模拟结果，不构成投资建议")
    print()


def main():
    parser = argparse.ArgumentParser(description="纸交模拟器")
    parser.add_argument("--init", action="store_true", help="初始化建仓")
    parser.add_argument("--capital", type=float, default=1_000_000, help="初始资金（默认 100 万）")
    parser.add_argument("--stocks", type=str, help="自定义股票列表 CSV（code,name）")
    parser.add_argument("--rebalance", action="store_true", help="根据 quant_pipeline 选股结果重建持仓")
    parser.add_argument("--rebalance-file", type=str, default=None, help="指定 selected_*.json 文件路径（默认取最新一个）")
    args = parser.parse_args()

    if args.init:
        if PORTFOLIO_FILE.exists():
            log.warning("portfolio.json 已存在，覆盖前请备份")
            resp = input("确认重新初始化? (y/N): ")
            if resp.lower() != "y":
                sys.exit(0)

        holdings = DEFAULT_HOLDINGS
        if args.stocks:
            holdings = {}
            with open(args.stocks) as f:
                for line in f:
                    code, name = line.strip().split(",")
                    std = to_standard_code(code)
                    holdings[std] = {"name": name, "code": code}

        log.info("\n🚀 初始化纸交组合 (资金: {args.capital:,.0f}):")
        print("-" * 50)
        init_portfolio(args.capital, holdings)
        log.info("\n✅ 建仓完成！之后运行 python trader.py 更新净值\n")
    elif args.rebalance:
        from pathlib import Path as _P
        out_dir = _P(os.environ.get("PIPELINE_OUTPUT", str(Path.home() / "Projects" / "pipeline_output")))
        if args.rebalance_file:
            sel_file = _P(args.rebalance_file)
        else:
            sel_files = sorted(out_dir.glob("selected_*.json"), reverse=True)
            if not sel_files:
                log.error("{out_dir} 下没有 selected_*.json，先跑 quant_pipeline.py 选股")
                sys.exit(1)
            sel_file = sel_files[0]
        with open(sel_file) as f:
            sel = json.load(f)
        new_codes = [s["code"] for s in sel["stocks"]]
        position_pct = sel.get("position_pct", 1.0)  # 大盘择时仓位，默认满仓
        timing_info = sel.get("timing_signal")
        log.info("\n🔄 Rebalance — 来源: {sel_file.name} ({sel.get('date')})")
        log.info("新选股 {len(new_codes)} 只: {', '.join(new_codes)}")
        if timing_info:
            log.info("📊 大盘择时: ratio={timing_info['ratio']:.4f} → 仓位 {position_pct:.0%} ({timing_info['action']})")
        else:
            log.info("📊 仓位: {position_pct:.0%}（无择时信号）")
        print("-" * 50)
        portfolio = load_portfolio()
        if portfolio is None:
            log.error("portfolio.json 不存在，请先 --init 初始化")
            sys.exit(1)
        rebalance_portfolio(portfolio, new_codes, position_pct=position_pct, timing_info=timing_info)
        log.info("\n✅ Rebalance 完成 — {} 只持仓，现金余额: {:,.0f}\n", len(portfolio['holdings']), portfolio.get('cash_remaining', 0))
    else:
        portfolio = load_portfolio()
        if portfolio is None:
            log.error("portfolio.json 不存在，请先 --init 初始化")
            sys.exit(1)
        log.info("📡 拉取实时行情中...")
        result = update_nav(portfolio)
        print_report(result)


if __name__ == "__main__":
    main()
