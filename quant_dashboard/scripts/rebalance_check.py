# 路径派生 — 通过环境变量覆盖,默认走 ~/Projects/quant-dashboard
BASE = Path(os.environ.get("QUANT_DASHBOARD_HOME", str(Path.home() / "Projects" / "quant-dashboard")))

#!/usr/bin/env python3
"""
换仓决策引擎
读取 paper_portfolio.json + small_cap_candidates.json，输出：
1. 持仓中小盘评分排名（含是否掉出 top30）
2. 新候选里 PE<10 + 评分>70 的“估值洼地”
3. 建议：哪些该换、换什么
用法：
  python3 rebalance_check.py
  python3 rebalance_check.py --json  # JSON 输出
"""
import json
import sys
from pathlib import Path

BASE = Path(BASE / "data")
PORT = BASE / "paper_portfolio.json"
CAND = BASE / "small_cap_candidates.json"

def load():
    if not PORT.exists():
        print("❌ paper_portfolio.json 不存在")
        sys.exit(1)
    if not CAND.exists():
        print("❌ small_cap_candidates.json 不存在，先跑扫描")
        sys.exit(1)
    with open(PORT) as f: port = json.load(f)
    with open(CAND) as f: cand = json.load(f)
    return port, cand

def check(port, cand, json_out=False):
    top30 = cand.get("top30", [])
    top30_codes = {s["code"] for s in top30}
    top30_map = {s["code"]: s for s in top30}

    # 持仓里的小盘票
    positions = port.get("positions", {})
    small_holdings = {c: p for c, p in positions.items() if p["type"] == "small"}

    # 1. 持仓排名
    holdings_report = []
    alerts = []
    for code, pos in small_holdings.items():
        if code in top30_map:
            rank = next(i+1 for i, s in enumerate(top30) if s["code"] == code)
            s = top30_map[code]
            holdings_report.append((code, pos["name"], rank, s["total_score"], s["pe"], "✅"))
        else:
            holdings_report.append((code, pos["name"], 0, 0, 0, "🔴 掉出 top30"))
            alerts.append(f"⚠️ {code} {pos['name']} 掉出 top30，考虑换仓")

    # 2. 估值洼地（PE<10 且评分>70 的新票，且不在持仓中）
    bargains = []
    for s in top30:
        if s["code"] in positions:
            continue
        if s["pe"] < 10 and s["total_score"] >= 70:
            bargains.append(s)

    if bargains:
        alerts.append(f"💡 发现 {len(bargains)} 只估值洼地（PE<10, 评分>70）")

    # 3. 建议换仓
    recommendations = []
    for code, pos in small_holdings.items():
        if code in top30_map:
            s = top30_map[code]
            if s["pe"] > 25 and bargains:
                # 这只 PE 高，有低估值新票可以换
                rec = bargains[0]
                recommendations.append(
                    f"🔄 {code} {pos['name']}（PE={s['pe']}, 评分={s['total_score']}）"
                    f" → {rec['code']} {rec['name']}（PE={rec['pe']}, 评分={rec['total_score']}）"
                )
        else:
            # 掉出 top30，找个替换
            if bargains:
                rec = bargains[0]
                recommendations.append(
                    f"🔄 {code} {pos['name']}（掉出 top30）"
                    f" → {rec['code']} {rec['name']}（PE={rec['pe']}, 评分={rec['total_score']}）"
                )
            elif top30:
                rec = top30[0]
                if rec["code"] not in positions:
                    recommendations.append(
                        f"🔄 {code} {pos['name']}（掉出 top30）"
                        f" → {rec['code']} {rec['name']}（PE={rec['pe']}, 评分={rec['total_score']}）"
                    )

    if json_out:
        return {
            "scan_date": cand.get("scan_date"),
            "holdings": [{"code": c, "name": n, "rank": r, "score": s, "pe": p, "status": st}
                         for c, n, r, s, p, st in holdings_report],
            "bargains": [{"code": b["code"], "name": b["name"], "pe": b["pe"],
                          "score": b["total_score"], "industry": b.get("industry", "")}
                         for b in bargains],
            "alerts": alerts,
            "recommendations": recommendations,
        }

    # 文本输出
    print(f"=== 换仓检查 @ {cand.get('scan_date', '?')} ===\n")
    print("持仓小盘票排名：")
    print(f"{'代码':6} {'名称':9} {'排名':>4} {'评分':>6} {'PE':>6} 状态")
    print("-" * 50)
    for c, n, r, s, p, st in holdings_report:
        print(f"{c:6} {n:9} {r:>4d} {s:>6.1f} {p:>6.1f} {st}")

    print(f"\n估值洼地（PE<10, 评分>70, 不在持仓）：")
    if bargains:
        for b in bargains[:5]:
            print(f"  {b['code']} {b['name']:9s} PE={b['pe']} 评分={b['total_score']} 行业={b.get('industry','?')}")
    else:
        print("  （无）")

    if alerts:
        print(f"\n⚠️ 告警：")
        for a in alerts:
            print(f"  {a}")

    if recommendations:
        print(f"\n建议换仓：")
        for r in recommendations:
            print(f"  {r}")
    else:
        print(f"\n✅ 无需换仓，持仓稳定")

    return None


if __name__ == "__main__":
    port, cand = load()
    if "--json" in sys.argv:
        result = check(port, cand, json_out=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        check(port, cand)