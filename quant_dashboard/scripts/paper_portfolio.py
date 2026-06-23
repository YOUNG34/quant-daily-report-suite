# 路径派生 — 通过环境变量覆盖,默认走 ~/Projects/quant-dashboard
BASE = Path(os.environ.get("QUANT_DASHBOARD_HOME", str(Path.home() / "Projects" / "quant-dashboard")))

#!/usr/bin/env python3
"""
个股 Paper Portfolio 模拟器
- 100 万起步，12 只票配置
- 风控规则：
  机构票  止损 -7% / 止盈 trailing(8%或12%) / 时间止损 20 天
  中小盘  止损 -8% / 时间止损 15 天 / 目标 +10%
- 组合熔断：月回撤 -10% 减半，-15% 清仓
- 数据：东财快照（实时收盘价）
用法:
  python3 paper_portfolio.py --init     # 用今天收盘价建仓
  python3 paper_portfolio.py            # 每日 EOD 更新 + 风控检查
  python3 paper_portfolio.py --status   # 看当前组合
"""
import argparse
import json
import csv
import os
import sys
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path

BASE = Path(BASE / "data")
PORTFOLIO = BASE / "paper_portfolio.json"
HISTORY = BASE / "paper_history.csv"
TRADES = BASE / "paper_trades.csv"

INITIAL_CASH = 1_000_000

# 组合定义：12 只 × 仓位金额
HOLDINGS_PLAN = [
    # 机构票 70 万 × 7 只 × 10 万
    {"code": "002938", "name": "鹏鼎控股",   "alloc": 100000, "type": "core", "industry": "PCB"},
    {"code": "600584", "name": "长电科技",   "alloc": 100000, "type": "core", "industry": "半导体"},
    {"code": "600926", "name": "杭州银行",   "alloc": 100000, "type": "core", "industry": "银行"},
    {"code": "603288", "name": "海天味业",   "alloc": 100000, "type": "core", "industry": "食品"},
    {"code": "601607", "name": "上海医药",   "alloc": 100000, "type": "core", "industry": "医药"},
    {"code": "600406", "name": "国电南瑞",   "alloc": 100000, "type": "core", "industry": "电力设备"},
    {"code": "001965", "name": "招商公路",   "alloc": 100000, "type": "core", "industry": "公路"},
    # 中小盘 20 万 × 5 只 × 4 万
    {"code": "603733", "name": "仙鹤股份",   "alloc": 40000,  "type": "small", "industry": "特种纸"},
    {"code": "600458", "name": "时代新材",   "alloc": 40000,  "type": "small", "industry": "轨交材料"},
    {"code": "002832", "name": "比音勒芬",   "alloc": 40000,  "type": "small", "industry": "高端男装"},
    {"code": "002745", "name": "木林森",     "alloc": 40000,  "type": "small", "industry": "LED"},
    {"code": "600906", "name": "财达证券",   "alloc": 40000,  "type": "small", "industry": "券商"},
]

# 风控规则
RULES = {
    "core":  {"stop_loss": -0.07, "trailing_high_pe": 0.08, "trailing_low_pe": 0.12, "max_hold_days": 20},
    "small": {"stop_loss": -0.08, "take_profit": 0.10, "max_hold_days": 15},
}
PORTFOLIO_HALT_50 = -0.10  # 月回撤 -10% 减半
PORTFOLIO_HALT_100 = -0.15 # 月回撤 -15% 全清

COMMISSION = 0.0003  # 万3
STAMP_TAX = 0.001    # 千1（卖出）


def fetch_prices(codes):
    """新浪批量行情，最稳"""
    sina_codes = []
    for c in codes:
        prefix = "sh" if c.startswith(("6", "9")) else "sz"
        sina_codes.append(f"{prefix}{c}")
    url = f"https://hq.sinajs.cn/list={','.join(sina_codes)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.sina.com.cn"
    })
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                text = r.read().decode("gbk", errors="ignore")
            break
        except Exception as e:
            last_err = e
            time.sleep(1 + attempt)
    else:
        raise last_err

    result = {}
    for line, sc, c in zip(text.strip().split("\n"), sina_codes, codes):
        # var hq_str_sh600406="国电南瑞,23.10,23.15,23.17,...";
        if '"' not in line:
            continue
        body = line.split('"', 2)[1]
        parts = body.split(",")
        if len(parts) < 5:
            continue
        try:
            name = parts[0]
            prev_close = float(parts[2])
            cur = float(parts[3])
            if cur == 0:
                cur = prev_close
            pct = (cur - prev_close) / prev_close * 100 if prev_close > 0 else 0
            result[c] = {
                "price": round(cur, 2),
                "pct_chg": round(pct, 2),
                "name": name,
            }
        except (ValueError, IndexError):
            continue
    return result


def shares_for_alloc(alloc, price):
    """按拨款金额买几手（100 股一手），不超额"""
    raw = alloc / price
    lots = int(raw // 100)
    return lots * 100


def cost_with_fee(shares, price, side):
    """带费的成本（买）或回款（卖）"""
    gross = shares * price
    commission = max(gross * COMMISSION, 5)
    if side == "buy":
        return gross + commission
    else:  # sell
        return gross - commission - gross * STAMP_TAX


def init_portfolio():
    """用今天的价格建仓"""
    if PORTFOLIO.exists():
        print(f"❌ 已有持仓 {PORTFOLIO}，请先备份/删除")
        return

    codes = [h["code"] for h in HOLDINGS_PLAN]
    print(f"=== 拉取 {len(codes)} 只票实时报价 ===")
    prices = fetch_prices(codes)

    missing = [c for c in codes if c not in prices]
    if missing:
        print(f"❌ 未取到价格：{missing}")
        return

    today = date.today().isoformat()
    cash = INITIAL_CASH
    positions = {}
    trades = []

    for h in HOLDINGS_PLAN:
        code = h["code"]
        price = prices[code]["price"]
        shares = shares_for_alloc(h["alloc"], price)
        if shares == 0:
            print(f"⚠️  {code} {h['name']} 价格 {price} 太高，跳过")
            continue
        cost = cost_with_fee(shares, price, "buy")
        cash -= cost
        positions[code] = {
            "name": h["name"],
            "type": h["type"],
            "industry": h["industry"],
            "shares": shares,
            "entry_price": price,
            "entry_date": today,
            "high_price": price,  # 浮动最高，用于 trailing
            "alloc_target": h["alloc"],
        }
        trades.append({
            "date": today, "code": code, "name": h["name"],
            "side": "buy", "shares": shares, "price": price,
            "cost": round(cost, 2), "reason": "init_position"
        })
        print(f"  ✅ {code} {h['name']:8s} @{price:7.2f} × {shares:4d} = {round(cost,0):>10.0f} 元")

    portfolio = {
        "init_date": today,
        "init_cash": INITIAL_CASH,
        "cash": round(cash, 2),
        "positions": positions,
        "halt_level": 0,           # 0=正常 1=减半 2=清仓
        "peak_value": INITIAL_CASH, # 历史最高净值
        "month_start_value": INITIAL_CASH,
        "month_start_date": today,
    }

    BASE.mkdir(parents=True, exist_ok=True)
    with open(PORTFOLIO, "w") as f:
        json.dump(portfolio, f, ensure_ascii=False, indent=2)

    # 写交易记录
    with open(TRADES, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "code", "name", "side", "shares", "price", "cost", "reason"])
        w.writeheader()
        for t in trades:
            w.writerow(t)

    total_position = sum(p["entry_price"] * p["shares"] for p in positions.values())
    print(f"\n=== 建仓完成 ===")
    print(f"  持仓:    {total_position:>12,.0f} 元 ({total_position/INITIAL_CASH*100:.1f}%)")
    print(f"  现金:    {cash:>12,.2f} 元 ({cash/INITIAL_CASH*100:.1f}%)")
    print(f"  总值:    {total_position + cash:>12,.2f} 元")
    print(f"  持仓数:  {len(positions)} 只")


def status():
    """查看当前组合"""
    if not PORTFOLIO.exists():
        print("❌ 尚未建仓，先运行 --init")
        return

    with open(PORTFOLIO) as f:
        p = json.load(f)

    codes = list(p["positions"].keys())
    prices = fetch_prices(codes)
    today = date.today().isoformat()

    rows = []
    total_mv = 0
    total_cost = 0
    total_pnl = 0
    for code, pos in p["positions"].items():
        if code not in prices:
            continue
        cur = prices[code]["price"]
        mv = cur * pos["shares"]
        cost = pos["entry_price"] * pos["shares"]
        pnl = mv - cost
        pnl_pct = pnl / cost * 100
        days = (date.fromisoformat(today) - date.fromisoformat(pos["entry_date"])).days
        total_mv += mv
        total_cost += cost
        total_pnl += pnl
        rows.append({
            "code": code, "name": pos["name"], "type": pos["type"],
            "entry": pos["entry_price"], "cur": cur, "shares": pos["shares"],
            "mv": mv, "pnl": pnl, "pnl_pct": pnl_pct, "days": days,
            "industry": pos["industry"]
        })

    rows.sort(key=lambda x: x["pnl_pct"], reverse=True)

    total_value = total_mv + p["cash"]
    total_ret = (total_value - p["init_cash"]) / p["init_cash"] * 100

    print(f"\n=== Paper Portfolio @ {today} ===")
    print(f"初始: {p['init_cash']:,.0f}  现值: {total_value:,.0f}  回报: {total_ret:+.2f}%  现金: {p['cash']:,.0f}\n")
    print(f"{'代码':6} {'名称':9} {'类型':5} {'行业':10} {'入':>7} {'现':>7} {'数量':>5} {'市值':>10} {'盈亏':>9} {'%':>7} {'天':>3}")
    print("-" * 110)
    for r in rows:
        ind_str = r["industry"][:10] if r["industry"] else ""
        print(f"{r['code']:6} {r['name']:9} {r['type']:5} {ind_str:10} {r['entry']:>7.2f} {r['cur']:>7.2f} {r['shares']:>5d} {r['mv']:>10,.0f} {r['pnl']:>+9,.0f} {r['pnl_pct']:>+6.1f}% {r['days']:>3d}")
    print("-" * 110)
    print(f"{'合计':<40} {'':>7} {'':>7} {'':>5} {total_mv:>10,.0f} {total_pnl:>+9,.0f} {total_pnl/total_cost*100:>+6.1f}%")

    # 风控状态
    print(f"\n熔断:  level={p.get('halt_level',0)}  月初值={p.get('month_start_value',0):,.0f}")


def daily_update():
    """EOD 更新：算盈亏、检查风控、写历史"""
    if not PORTFOLIO.exists():
        print("❌ 尚未建仓")
        return

    with open(PORTFOLIO) as f:
        p = json.load(f)

    codes = list(p["positions"].keys())
    if not codes:
        print("无持仓")
        return

    prices = fetch_prices(codes)
    today = date.today().isoformat()
    today_d = date.fromisoformat(today)

    # 1. 更新浮动最高
    for code, pos in p["positions"].items():
        if code in prices and prices[code]["price"] > pos["high_price"]:
            pos["high_price"] = prices[code]["price"]

    # 2. 检查各票风控
    exits = []  # 触发平仓的票
    for code, pos in list(p["positions"].items()):
        if code not in prices:
            continue
        cur = prices[code]["price"]
        entry = pos["entry_price"]
        high = pos["high_price"]
        pct = (cur - entry) / entry
        days = (today_d - date.fromisoformat(pos["entry_date"])).days
        rule = RULES[pos["type"]]

        reason = None
        # 硬止损
        if pct <= rule["stop_loss"]:
            reason = f"stop_loss {pct*100:.1f}%"
        # 时间止损
        elif days >= rule["max_hold_days"]:
            reason = f"time_stop {days}d"
        # 中小盘止盈
        elif pos["type"] == "small" and pct >= rule.get("take_profit", 999):
            reason = f"take_profit {pct*100:.1f}%"
        # 机构票 trailing
        elif pos["type"] == "core":
            # 简化：假设 PE 已在建仓时打标（这里跳过 PE 判定，统一用 8%）
            trail_threshold = rule["trailing_high_pe"]
            if pct >= trail_threshold:
                drop_from_high = (cur - high) / high
                if drop_from_high <= -0.05:  # 从最高回撤 5% 启动 trailing 平仓
                    reason = f"trailing {drop_from_high*100:.1f}% (high={high})"

        if reason:
            # 平仓
            proceeds = cost_with_fee(pos["shares"], cur, "sell")
            p["cash"] += proceeds
            exits.append({
                "date": today, "code": code, "name": pos["name"],
                "side": "sell", "shares": pos["shares"], "price": cur,
                "cost": round(proceeds, 2), "reason": reason,
                "pnl": round(proceeds - entry * pos["shares"], 2),
            })
            del p["positions"][code]
            print(f"  🔴 EXIT {code} {pos['name']} @{cur} ({reason})")

    # 3. 算总值
    total_mv = sum(prices[c]["price"] * pos["shares"]
                   for c, pos in p["positions"].items() if c in prices)
    total_value = total_mv + p["cash"]

    # 4. 更新 peak / 月起点
    if total_value > p.get("peak_value", 0):
        p["peak_value"] = total_value

    # 月初切换
    if today_d.day == 1 or p.get("month_start_date", today) != today and \
       date.fromisoformat(p.get("month_start_date", today)).month != today_d.month:
        p["month_start_value"] = total_value
        p["month_start_date"] = today

    # 5. 组合熔断
    month_ret = (total_value - p.get("month_start_value", total_value)) / p.get("month_start_value", total_value)
    if month_ret <= PORTFOLIO_HALT_100 and p.get("halt_level", 0) < 2:
        # 全清
        print(f"  🔥 月回撤 {month_ret*100:.1f}% < -15%，全部清仓")
        for code, pos in list(p["positions"].items()):
            if code in prices:
                cur = prices[code]["price"]
                proceeds = cost_with_fee(pos["shares"], cur, "sell")
                p["cash"] += proceeds
                exits.append({
                    "date": today, "code": code, "name": pos["name"],
                    "side": "sell", "shares": pos["shares"], "price": cur,
                    "cost": round(proceeds, 2), "reason": f"portfolio_halt month_ret={month_ret*100:.1f}%",
                    "pnl": round(proceeds - pos["entry_price"] * pos["shares"], 2),
                })
                del p["positions"][code]
        p["halt_level"] = 2
    elif month_ret <= PORTFOLIO_HALT_50 and p.get("halt_level", 0) < 1:
        print(f"  ⚠️ 月回撤 {month_ret*100:.1f}% < -10%，触发减半提醒（人工执行）")
        p["halt_level"] = 1

    # 6. 写历史
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    is_new = not HISTORY.exists()
    with open(HISTORY, "a", newline="") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(["date", "total_value", "cash", "positions_mv", "n_positions", "month_ret", "total_ret"])
        total_ret = (total_value - p["init_cash"]) / p["init_cash"]
        w.writerow([today, round(total_value, 2), round(p["cash"], 2),
                    round(total_mv, 2), len(p["positions"]),
                    round(month_ret * 100, 2), round(total_ret * 100, 2)])

    # 7. 写 trades
    if exits:
        is_new = not TRADES.exists()
        with open(TRADES, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["date", "code", "name", "side", "shares", "price", "cost", "reason", "pnl"])
            if is_new:
                w.writeheader()
            for t in exits:
                w.writerow(t)

    # 8. 保存
    with open(PORTFOLIO, "w") as f:
        json.dump(p, f, ensure_ascii=False, indent=2)

    total_ret = (total_value - p["init_cash"]) / p["init_cash"] * 100
    print(f"\n=== {today} EOD ===")
    print(f"总值: {total_value:,.0f}  现金: {p['cash']:,.0f}  持仓 {len(p['positions'])} 只")
    print(f"回报: {total_ret:+.2f}%  月内: {month_ret*100:+.2f}%  熔断 lv{p.get('halt_level',0)}")
    if exits:
        print(f"今日平仓: {len(exits)} 笔")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true", help="用今天价格建仓")
    ap.add_argument("--status", action="store_true", help="看当前组合")
    args = ap.parse_args()

    if args.init:
        init_portfolio()
    elif args.status:
        status()
    else:
        daily_update()
