# Quant Dashboard

每日量化指标看板。一只股一天一行，5 个维度 28 个指标，本地 SQLite 存储，可视化 HTML 界面。

## 快速开始

```bash
# 1. 初始化数据库（仅首次）
python3 scripts/init_db.py

# 2. 拉取最新数据（每天运行一次，已自动 cron）
python3 scripts/fetch_indicators.py

# 3. 启动看板
./start.sh                # 默认 8765 端口，自动开浏览器
./start.sh 9000           # 指定端口
```

打开 http://127.0.0.1:8765

## 5 维度指标

| 维度 | 指标 | 含义 |
|------|------|------|
| 趋势 | MA5/10/20/60、MA20斜率、ADX(14) | 方向 + 强度 |
| 动量 | RSI(14)、MACD、ROC(20) | 上涨/下跌动力 |
| 量能 | 量比、OBV、量价配合 | 资金参与度 |
| 估值 | PE-TTM、PB、3年分位 | 贵不贵 |
| 资金 | 主力净流入、大单占比 | 谁在买 |

最后加权出综合分 0-100，判定 `bullish/neutral/bearish`。

## 自动更新

cron 任务（每个交易日 16:30）已配置，详见 `cronjob list`。

## 数据库

`data/indicators.db` SQLite，4 张表：
- `watchlist` — 关注/持仓列表（可手工编辑）
- `daily_indicators` — 每日每股指标
- `market_context` — 大盘环境
- `fetch_log` — 采集日志

## 增删股票

直接编辑 watchlist:
```bash
sqlite3 data/indicators.db "INSERT INTO watchlist(code,name,industry,market,is_holding) VALUES ('000001','平安银行','银行','sz',0)"
```
下次 fetch 自动生效。

## 数据源

- **baostock** (主)：K线、PE、PB、换手 — 免费，稳定
- **akshare** (辅)：主力资金流、北向 — 经常被东财 ban，失败 graceful
