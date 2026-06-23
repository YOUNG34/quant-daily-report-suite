"""净值曲线 dashboard — 2 个 subplot（净值 + 行业暴露）。

用 matplotlib（已装在 quant-env），输出 PNG。
可被 paper_trader/ETF trader 复用。
"""
from __future__ import annotations
from pathlib import Path
from typing import Mapping
from datetime import datetime

import matplotlib
matplotlib.use("Agg")  # 无 GUI 后端
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.figure import Figure


# 中文字体兜底（macOS 默认 PingFang/Heiti）
plt.rcParams["font.sans-serif"] = ["PingFang SC", "Heiti SC", "STHeiti", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def render_nav_dashboard(
    history_rows: list[dict],
    industry_breakdown: Mapping[str, float],
    init_capital: float,
    init_date: str,
    title: str = "纸交组合净值曲线",
) -> Figure:
    """生成 dashboard Figure（不落盘，调用者 savefig）。

    Args:
        history_rows: [{"date": "2026-05-26", "time": "17:02:32", "nav": 1000000.0,
                        "nav_return_pct": 0.0}, ...]
        industry_breakdown: {"银行": 0.10, "电子": 0.19, ...}
        init_capital: 初始资金（画 benchmark 平线用）
        init_date: 建仓日期（标题显示）
        title: 主标题
    """
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(12, 8), dpi=100,
        gridspec_kw={"height_ratios": [2, 1]},
    )

    # ── 子图 1：净值曲线 ──
    if history_rows:
        dates = [datetime.strptime(r["date"], "%Y-%m-%d") for r in history_rows]
        navs = [r["nav"] for r in history_rows]

        ax1.plot(dates, navs, "o-", color="#1976d2", linewidth=2, markersize=6, label="组合净值")
        # benchmark 平线（初始资金）
        ax1.axhline(init_capital, color="#9e9e9e", linestyle="--", linewidth=1, alpha=0.6, label=f"初始资金 {init_capital:,.0f}")

        # 数据点标注
        for d, n, r in zip(dates, navs, history_rows):
            ret = r.get("nav_return_pct", 0.0)
            color = "#d32f2f" if ret < 0 else "#388e3c"
            ax1.annotate(
                f"{n:,.0f}\n({ret:+.2f}%)",
                xy=(d, n),
                xytext=(0, 10),
                textcoords="offset points",
                ha="center", fontsize=8, color=color,
            )

        ax1.set_ylabel("净值（元）", fontsize=11)
        ax1.set_title(f"{title}  (建仓 {init_date})", fontsize=13, fontweight="bold")
        ax1.legend(loc="upper right", framealpha=0.9)
        ax1.grid(True, alpha=0.3, linestyle=":")
        # x 轴日期格式
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        ax1.xaxis.set_major_locator(mdates.AutoDateLocator())
        fig.autofmt_xdate()
    else:
        ax1.text(0.5, 0.5, "暂无历史数据", ha="center", va="center", transform=ax1.transAxes)
        ax1.set_title(title, fontsize=13)

    # ── 子图 2：行业暴露 ──
    if industry_breakdown:
        # 按占比降序
        sorted_items = sorted(industry_breakdown.items(), key=lambda x: -x[1])
        inds = [k for k, _ in sorted_items]
        weights = [v for _, v in sorted_items]

        # 配色：超限标红，否则蓝灰
        colors = ["#d32f2f" if w > 0.30 else "#1976d2" if w > 0.10 else "#90a4ae" for w in weights]
        bars = ax2.barh(inds, [w * 100 for w in weights], color=colors, edgecolor="white")

        # 数值标注
        for bar, w in zip(bars, weights):
            ax2.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                     f"{w*100:.1f}%", va="center", fontsize=9)

        # 30% 上限线
        ax2.axvline(30, color="#d32f2f", linestyle="--", linewidth=1, alpha=0.5, label="行业上限 30%")
        ax2.set_xlabel("权重 (%)", fontsize=11)
        ax2.set_title("行业暴露分布", fontsize=12, fontweight="bold")
        ax2.set_xlim(0, max(weights) * 100 * 1.15 + 1)
        ax2.legend(loc="lower right", framealpha=0.9)
        ax2.invert_yaxis()  # 大的在上面
        ax2.grid(True, axis="x", alpha=0.3, linestyle=":")
    else:
        ax2.text(0.5, 0.5, "暂无行业数据", ha="center", va="center", transform=ax2.transAxes)

    plt.tight_layout()
    return fig


def save_dashboard(
    fig: Figure,
    output_path: str | Path,
    dpi: int = 120,
) -> Path:
    """落盘 PNG。返回绝对路径。"""
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(p, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return p.absolute()


__all__ = ["render_nav_dashboard", "save_dashboard"]
