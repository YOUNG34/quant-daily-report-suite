"""Initialize the SQLite schema for the indicator dashboard.

# 路径派生 — 通过环境变量覆盖,默认走 ~/Projects/quant-dashboard
BASE = Path(os.environ.get("QUANT_DASHBOARD_HOME", str(Path.home() / "Projects" / "quant-dashboard")))

Schema 设计原则:
- 一行 = 一只股票 + 一个交易日 (复合主键 code+date)
- 5 维度指标全部 flat 存（不嵌套 JSON，方便 SQL 排序/筛选/聚合）
- 大盘指标单独一张表（环境上下文）
- 持仓表单独一张（关注名单 + 是否实际持有）
- 所有数值用 REAL，缺数留 NULL
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

DB_PATH = Path(BASE / "data" / "indicators.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
-- 关注/持仓列表（手动维护）
CREATE TABLE IF NOT EXISTS watchlist (
    code TEXT PRIMARY KEY,        -- 6位代码 e.g. 601816
    name TEXT,                    -- 股票名
    industry TEXT,                -- 行业（自动分类）
    market TEXT,                  -- sh / sz / bj
    is_holding INTEGER DEFAULT 0, -- 是否实际持仓
    cost REAL,                    -- 持仓成本（可选）
    shares INTEGER,               -- 持仓数量（可选）
    notes TEXT,                   -- 备注
    added_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 日度指标主表（每股每日一行）
CREATE TABLE IF NOT EXISTS daily_indicators (
    code TEXT NOT NULL,
    date TEXT NOT NULL,           -- 'YYYY-MM-DD'

    -- 基础 OHLCV
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, amount REAL,
    pct_chg REAL,                 -- 涨跌幅 %
    turnover REAL,                -- 换手率 %

    -- 维度 1：趋势
    ma5 REAL, ma10 REAL, ma20 REAL, ma60 REAL,
    ma20_slope REAL,              -- MA20 5日斜率（标准化后的方向值）
    adx14 REAL,                   -- ADX 趋势强度（>25 趋势市 / <20 震荡）

    -- 维度 2：动量
    rsi14 REAL,                   -- RSI(14)
    macd_diff REAL,               -- MACD DIF
    macd_dea REAL,                -- MACD DEA
    macd_hist REAL,               -- MACD 柱
    roc20 REAL,                   -- 20日动量

    -- 维度 3：量能
    vol_ratio REAL,               -- 量比（今日量 / 5日均量）
    obv REAL,                     -- OBV 累积
    vol_price_match TEXT,         -- 'healthy_up' / 'healthy_down' / 'fake_up' / 'fake_down' / 'neutral'

    -- 维度 4：估值
    pe_ttm REAL,
    pb REAL,
    pe_pct3y REAL,                -- 近3年 PE 分位（百分位 0-100）
    pb_pct3y REAL,                -- 近3年 PB 分位

    -- 维度 5：情绪/资金（个股层面）
    net_inflow_main REAL,         -- 主力净流入（万元）
    big_order_ratio REAL,         -- 大单占比（可选，留 NULL）

    -- 综合评分
    score_trend REAL,             -- 0-100
    score_momentum REAL,
    score_volume REAL,
    score_valuation REAL,
    score_sentiment REAL,
    score_total REAL,             -- 加权总分
    verdict TEXT,                 -- 'bullish' / 'neutral' / 'bearish'

    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);

CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_indicators(date);
CREATE INDEX IF NOT EXISTS idx_daily_score ON daily_indicators(date, score_total DESC);

-- 大盘环境（每日一行）
CREATE TABLE IF NOT EXISTS market_context (
    date TEXT PRIMARY KEY,
    sh_close REAL,                -- 上证指数
    sh_pct_chg REAL,
    sh_ma60 REAL,
    sh_adx14 REAL,                -- 大盘 ADX
    cyb_close REAL,               -- 创业板指
    cyb_pct_chg REAL,
    cyb_ma60 REAL,
    volatility20 REAL,            -- 20日波动率（标准差年化）
    regime TEXT,                  -- 'trending_up' / 'trending_down' / 'range' / 'high_vol'
    north_inflow REAL,            -- 北向资金净流入（亿元）
    margin_balance REAL,          -- 两融余额（亿元）
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 数据采集日志
CREATE TABLE IF NOT EXISTS fetch_log (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    started_at TEXT,
    finished_at TEXT,
    n_stocks INTEGER,
    n_success INTEGER,
    n_failed INTEGER,
    duration_sec REAL,
    notes TEXT
);
"""


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()

    # 默认填充 watchlist（来自 daily_stock_analysis/.env 的 STOCK_LIST）
    default = [
        ("601816", "京沪高铁", "交运", "sh"),
        ("002938", "鹏鼎控股", "电子", "sz"),
        ("600584", "长电科技", "电子", "sh"),
        ("600588", "用友网络", "软件", "sh"),
        ("600926", "杭州银行", "银行", "sh"),
        ("001965", "招商公路", "交运", "sz"),
        ("600377", "宁沪高速", "交运", "sh"),
        ("601607", "上海医药", "医药", "sh"),
        ("603288", "海天味业", "食品", "sh"),
        ("600406", "国电南瑞", "电力", "sh"),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO watchlist(code, name, industry, market, is_holding) VALUES (?,?,?,?,1)",
        default,
    )
    conn.commit()

    # 报表
    n = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
    print(f"✓ Schema initialized at {DB_PATH}")
    print(f"✓ Watchlist: {n} stocks")

    # 列出所有表
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    print("\nTables:")
    for t in tables:
        cnt = conn.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
        print(f"  - {t[0]} ({cnt} rows)")

    conn.close()


if __name__ == "__main__":
    main()
