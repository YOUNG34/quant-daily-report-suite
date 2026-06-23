#!/usr/bin/env python3
"""纸交组合净值 dashboard 生成器。

读 history.csv + portfolio.json → 渲染双图 PNG → 写入 dailyRepo。

用法:
    python build_dashboard.py
    python build_dashboard.py --output /custom/path.png
"""
import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

# 共享代码：仓库根（paper_trader 的父目录）加到 sys.path
# 这样无论仓库被 clone 到 ~/Projects 还是 /home/alice/foo,
# 都能找到 quant_core/ 兄弟目录
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from quant_core.config import get_config
from quant_core.dashboard import render_nav_dashboard, save_dashboard
from quant_core.logger import get_logger

log = get_logger("build_dashboard")

# paper_trader 自身路径 = 当前脚本所在目录
PROJECT_DIR = Path(__file__).resolve().parent


def load_history(csv_path: Path) -> list[dict]:
    """读 history.csv → list[dict]。空文件返回 []。"""
    if not csv_path.exists():
        log.warning("history.csv 不存在: {}", csv_path)
        return []
    rows = []
    with open(csv_path) as f:
        for r in csv.DictReader(f):
            try:
                rows.append({
                    "date": r["date"],
                    "time": r.get("time", ""),
                    "nav": float(r["nav"]),
                    "nav_return_pct": float(r.get("nav_return_pct", 0)),
                })
            except (KeyError, ValueError) as e:
                log.warning("跳过异常行 {}: {}", r, e)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", type=str,
                        default=str(PROJECT_DIR / "history.csv"))
    parser.add_argument("--portfolio", type=str,
                        default=str(PROJECT_DIR / "portfolio.json"))
    parser.add_argument("--output", type=str, default=None,
                        help="输出 PNG 路径（默认写到 dailyRepo）")
    args = parser.parse_args()

    cfg = get_config()

    # 读历史
    history = load_history(Path(args.history))
    log.info("读历史: {} 行 ({} → {})",
             len(history),
             history[0]["date"] if history else "N/A",
             history[-1]["date"] if history else "N/A")

    # 读 portfolio
    pf_path = Path(args.portfolio)
    if not pf_path.exists():
        log.error("portfolio.json 不存在: {}", pf_path)
        sys.exit(1)
    with open(pf_path) as f:
        portfolio = json.load(f)
    log.info("组合净值: {:.2f} ({} 持仓)", portfolio.get("last_nav", 0), len(portfolio.get("holdings", {})))

    # 行业暴露（优先 portfolio 里的 industry_breakdown，否则现场算）
    breakdown = portfolio.get("industry_breakdown")
    if not breakdown:
        from quant_core.industry import get_industry_breakdown
        total = portfolio.get("last_nav", 0)
        breakdown = get_industry_breakdown(portfolio.get("holdings", {}), total)
        log.info("现场算行业暴露: {}", breakdown)

    # 把 portfolio 的最后一行（如果今日有）也补进 history（避免 dashboard 只显示到昨天）
    last_date = history[-1]["date"] if history else None
    today = datetime.now().strftime("%Y-%m-%d")
    if last_date != today and portfolio.get("last_nav"):
        history.append({
            "date": today,
            "time": datetime.now().strftime("%H:%M:%S"),
            "nav": portfolio["last_nav"],
            "nav_return_pct": portfolio.get("nav_return_pct", 0),
        })
        log.info("追加今日净值到曲线: {} = {:.2f}", today, portfolio["last_nav"])

    # 输出路径
    if args.output:
        out_path = Path(args.output)
    else:
        date_str = today
        out_path = Path(cfg.paths.daily_repo) / f"nav_dashboard_{date_str}.png"

    # 渲染
    fig = render_nav_dashboard(
        history_rows=history,
        industry_breakdown=breakdown,
        init_capital=portfolio.get("init_capital", 1_000_000),
        init_date=portfolio.get("init_date", "?"),
        title="纸交组合净值曲线",
    )
    saved = save_dashboard(fig, out_path)
    log.info("✅ Dashboard 落盘: {}", saved)
    print(saved)  # stdout 给 cron 收集


if __name__ == "__main__":
    main()
