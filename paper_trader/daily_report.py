#!/usr/bin/env python3
"""
纸交日报生成器
- 读 portfolio.json + history.csv + llm_signals/{today}.jsonl
- 生成 Markdown 格式日报
- 写入 $HOME/Projects/<your-knowledge-base>/Agent/quant/dailyRepo/YYYY-MM-DD.md
- 推 Telegram
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# 共享配置（向前兼容）
# 仓库根加到 sys.path，让 import quant_core.* 能工作
# 兼容两种部署：(1) 仓库在 $HOME/Projects/quant-daily-report-suite
#               (2) 仓库在任意位置（设置 QUANT_PROJECTS 后 paper_trader 仍能找到自己）
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from quant_core.config import get_config
    from quant_core.logger import get_logger
    _cfg = get_config()
    _log = get_logger("daily_report")
    BASE_DIR = Path(_cfg.paths.paper_trader_dir)
    PORTFOLIO_FILE = BASE_DIR / "portfolio.json"
    HISTORY_FILE = BASE_DIR / "history.csv"
    DAILY_REPO_DIR = Path(_cfg.report.output_dir)
    # llm_signals 与 dailyRepo 同级(从 quant_config.yaml 的 output_dir 派生)
    SIG_DIR = DAILY_REPO_DIR.parent / "llm_signals"
    _DISCLAIMER = _cfg.report.disclaimer
    _HISTORY_ROWS = _cfg.report.include_history_rows
except Exception as _e:
    _log = None
    _cfg = None
    BASE_DIR = Path(__file__).parent
    PORTFOLIO_FILE = BASE_DIR / "portfolio.json"
    HISTORY_FILE = BASE_DIR / "history.csv"
    # fallback:用环境变量 QUANT_KB_NAME(知识库根目录名),默认占位符
    _KB_NAME = os.environ.get("QUANT_KB_NAME", "<your-knowledge-base>")
    DAILY_REPO_DIR = Path.home() / "Projects" / _KB_NAME / "Agent" / "quant" / "dailyRepo"
    SIG_DIR = Path.home() / "Projects" / _KB_NAME / "Agent" / "quant" / "llm_signals"
    _DISCLAIMER = "⚠️ 以上为模拟结果，不构成投资建议"
    _HISTORY_ROWS = 10


log = get_logger(__name__)  # type: ignore[name-defined]  # noqa

def load_portfolio() -> dict:
    if not PORTFOLIO_FILE.exists():
        raise FileNotFoundError(f"{PORTFOLIO_FILE} 不存在，请先初始化组合")
    with open(PORTFOLIO_FILE) as f:
        return json.load(f)


def load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    rows = []
    with open(HISTORY_FILE) as f:
        header = f.readline().strip().split(",")
        for line in f:
            parts = line.strip().split(",")
            if len(parts) == len(header):
                rows.append(dict(zip(header, parts)))
    return rows


def fmt_pct(x: float) -> str:
    return f"{x:+.2f}%"


# ── LLM 情报段 ─────────────────────────────
def load_llm_signals(date_str: str) -> list[dict]:
    """读 llm_signals/{date_str}.jsonl，返回 dict 列表（容忍空 / 缺文件）"""
    p = SIG_DIR / f"{date_str}.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def ensure_llm_signals(date_str: str, base_dir: Path) -> bool:
    """当日 JSONL 不存在时，子进程跑一次 extractor（不阻塞日报主流程）

    返回 True 表示有 JSONL 可用，False 表示 extractor 失败 / 跳过
    """
    target = SIG_DIR / f"{date_str}.jsonl"
    if target.exists() and target.stat().st_size > 0:
        return True

    extractor = base_dir / "llm_signal_extractor.py"
    if not extractor.exists():
        if _log:
            _log.warning("extractor 不存在: {}", extractor)
        return False

    # 用环境变量 PYTHON_BIN 或当前 Python 解释器，避免硬编码 venv 路径
    py = os.environ.get("PYTHON_BIN") or sys.executable
    try:
        r = subprocess.run(
            [py, str(extractor), "--days", "3"],
            cwd=str(base_dir),
            timeout=180,
            capture_output=True,
            text=True,
        )
        if _log:
            _log.info("extractor exit={}, stderr={}", r.returncode, r.stderr[-200:])
        return target.exists() and target.stat().st_size > 0
    except subprocess.TimeoutExpired:
        if _log:
            _log.warning("extractor 超时 (180s)")
        return False
    except Exception as e:
        if _log:
            _log.warning("extractor 异常: {}", e)
        return False


def render_llm_section(signals: list[dict]) -> str:
    """渲染 '## LLM 情报摘要' 段"""
    if not signals:
        return ""

    # 按 signal 排序：positive_bias → neutral → negative_bias
    order = {"positive_bias": 0, "neutral": 1, "negative_bias": 2}
    signals_sorted = sorted(
        signals,
        key=lambda s: (order.get(s.get("trading_signal", ""), 9),
                       -s.get("impact_strength", 0)),
    )

    md = ["## LLM 情报摘要（公告 · GLM-5.1）", ""]
    md.append(f"基于近 3 天公告分析持仓，共 {len(signals)} 只出现公告事件。")
    md.append("")
    md.append("| 代码 | 名称 | 信号 | 情感 | 影响强度 | 公告数 | 置信度 | 一句话解读 |")
    md.append("|------|------|------|------|----------|--------|--------|------------|")

    emoji = {"positive_bias": "🟢", "neutral": "⚪", "negative_bias": "🔴"}
    for s in signals_sorted:
        code = s.get("stock_code", "-")
        name = s.get("stock_name", "-")
        sig = s.get("trading_signal", "neutral")
        sent = s.get("overall_sentiment", 0.0)
        impact = s.get("impact_strength", 0.0)
        ann = s.get("announcement_count", 0)
        conf = s.get("confidence", 0.0)
        reasoning = (s.get("reasoning") or "").replace("\n", " ").strip()
        # 截断过长
        if len(reasoning) > 60:
            reasoning = reasoning[:60] + "…"
        md.append(
            f"| {code} | {name} | {emoji.get(sig, '⚪')} {sig} | "
            f"{sent:+.2f} | {impact:.2f} | {ann} | {conf:.2f} | {reasoning} |"
        )

    # 重点事件：positive_bias 且 confidence >= 0.5 或 impact_strength >= 0.5
    key_events = [
        s for s in signals
        if (s.get("trading_signal") == "positive_bias"
            and (s.get("confidence", 0) >= 0.5 or s.get("impact_strength", 0) >= 0.5))
        or (s.get("trading_signal") == "negative_bias"
            and (s.get("confidence", 0) >= 0.5 or s.get("impact_strength", 0) >= 0.5))
    ]
    if key_events:
        md.append("")
        md.append("### 🎯 重点事件")
        md.append("")
        for s in key_events:
            md.append(f"- **{s.get('stock_name', s.get('stock_code'))}** "
                      f"({s.get('trading_signal')}, conf={s.get('confidence'):.2f})")
            for ev in s.get("key_events", [])[:3]:
                md.append(f"  - {ev.get('title','?')} → {ev.get('summary','')}")
            md.append("")

    md.append("")
    return "\n".join(md)


def render_markdown(portfolio: dict, history: list[dict], llm_signals: list[dict]) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    init_capital = portfolio["init_capital"]
    nav = portfolio["last_nav"]
    return_pct = portfolio.get("nav_return_pct", (nav / init_capital - 1) * 100)
    cash = portfolio.get("cash_remaining", 0)
    total_invested = portfolio.get("total_invested", nav - cash)

    md = [
        f"# 纸交日报 — {today}",
        "",
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 组合概况",
        "",
        f"- **建仓日期**: {portfolio.get('init_date', '-')}",
        f"- **初始资金**: ¥{init_capital:,.0f}",
        f"- **当前净值**: ¥{nav:,.2f}",
        f"- **累计收益**: {fmt_pct(return_pct)}",
        f"- **总投入**: ¥{total_invested:,.0f}",
        f"- **现金余额**: ¥{cash:,.0f}",
        f"- **持仓数**: {len(portfolio.get('holdings', {}))}",
        "",
        "## 持仓明细",
        "",
        "| 标的 | 代码 | 持仓(股) | 成本价 | 现价 | 市值(¥) | 盈亏(¥) | 盈亏% |",
        "|------|------|----------|--------|------|---------|---------|-------|",
    ]

    for std_code, h in portfolio.get("holdings", {}).items():
        code = std_code.split(".")[0]
        name = h.get("name", "-")
        shares = h["shares"]
        cost = h["cost_price"]
        # trader.py 写的是 last_price（current_price 不存在）
        price = h.get("last_price", h.get("current_price", cost))
        market_value = shares * price
        pnl = (price - cost) * shares
        pnl_pct = (price / cost - 1) * 100 if cost else 0
        md.append(
            f"| {name} | {code} | {shares:,} | {cost:.2f} | {price:.2f} | {market_value:,.0f} | {pnl:+,.0f} | {pnl_pct:+.2f}% |"
        )

    md.append("")
    md.append("## 净值历史")
    md.append("")
    if history:
        md.append("| 日期 | 时间 | 净值(¥) | 累计收益% |")
        md.append("|------|------|----------|-----------|")
        for row in history[-_HISTORY_ROWS:]:  # 最近 N 条（从 config 读）
            md.append(
                f"| {row['date']} | {row['time']} | {float(row['nav']):,.2f} | {row['nav_return_pct']}% |"
            )
    else:
        md.append("（暂无历史数据）")

    # LLM 情报段（如果有信号数据）
    llm_section = render_llm_section(llm_signals)
    if llm_section:
        md.append("")
        md.append(llm_section)

    md.append("")
    md.append("---")
    md.append("")
    md.append(f"_{_DISCLAIMER}_")
    md.append("")

    return "\n".join(md)


def save_daily_repo(md_content: str) -> Path:
    """写入知识库 dailyRepo"""
    DAILY_REPO_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    target = DAILY_REPO_DIR / f"{date_str}.md"
    target.write_text(md_content, encoding="utf-8")
    return target


def send_telegram(md_content: str) -> bool:
    """推 Telegram（占位 — TODO: 接入 hermes send_message 或 Telegram Bot API）"""
    # 简化：用 Markdown 摘要推 Telegram
    # 用户没明确用什么通道 — hermes send_message 是首选
    # 但 cron job 本身用 deliver='origin' 自动回传到当前 chat
    # 所以这里不需要单独 send_telegram
    return True


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        portfolio = load_portfolio()
        history = load_history()
    except FileNotFoundError as e:
        if _log:
            _log.error("加载失败: {}", e)
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    # LLM 情报：当日 JSONL 不存在 → 跑一次 extractor（不阻塞日报）
    llm_signals: list[dict] = []
    try:
        if ensure_llm_signals(today, BASE_DIR):
            llm_signals = load_llm_signals(today)
            if _log:
                _log.info("LLM 信号加载: {} 条", len(llm_signals))
        else:
            if _log:
                _log.warning("LLM 信号不可用，跳过该段")
    except Exception as e:
        if _log:
            _log.warning("LLM 段异常: {}", e)

    md = render_markdown(portfolio, history, llm_signals)
    target = save_daily_repo(md)
    if _log:
        _log.info("日报已保存: {} ({} bytes)", target, target.stat().st_size)
    log.info("✅ 日报已保存: {target}")
    log.info("文件大小: {target.stat().st_size} bytes")
    print()
    print(md)


if __name__ == "__main__":
    main()
