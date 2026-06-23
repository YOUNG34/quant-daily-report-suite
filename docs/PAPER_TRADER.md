# Paper Trader 详解

> 纸交模拟器是这套系统的核心:8 个文件,跑了 12+ 天真实产出。本文讲透每个文件做什么、数据怎么流、规则怎么落地。

## 1. 定位

**Paper Trader = 模拟盘**,不接券商 API,不花钱,目标是用真实行情 + 真实规则验证"我的策略实盘能跑出什么曲线"。

| 维度 | 实盘 | 纸交 (本系统) |
|---|---|---|
| 资金 | 真实人民币 | `init_capital: 1_000_000` 配置项 |
| 委托 | 通过券商客户端下单 | 脚本直接改 `portfolio.json` |
| 撮合 | 交易所 | 收盘价即成交价(无滑点) |
| 涨跌停 | 真实涨跌停 | `market_rules.pre_trade_check` 预检 |
| 交易成本 | 佣金 + 印花税 | **都算了**,见 §6 |
| 停牌 | 真实停牌 | 沿用最新价,T+0 算 |
| 风控 | 人工/券商强平 | `risk:` 配置 + ATR 动态止损 |

> **关键限制**:纸交**不考虑滑点**。实盘小票(成交额 < 1 亿)滑点可能吃 1–3%,本系统**不模拟这个**。这是已知偏差,见 §8。

---

## 2. 文件清单

```
paper_trader/
├── trader.py              ← 主程序,日常跑
├── daily_report.py        ← Markdown 日报生成
├── build_dashboard.py     ← matplotlib 净值图
├── llm_signal_extractor.py ← LLM 信号抽取
├── market_timing.py       ← 大盘择时
├── portfolio.json         ← 当前持仓(敏感,gitignore)
├── history.csv            ← 净值历史(敏感,gitignore)
├── sh000300_kline.pkl     ← K 线缓存(31K)
└── .stock_name_cache.json ← 股票名缓存(gitignore)
```

### 2.1 `trader.py` (846 行)

**职责**:拉行情 → 算净值 → 检查风控 → 写状态文件。

**关键流程**:
```
1. load portfolio.json (持仓 + 成本价)
2. fetch_tencent_quotes_with_retry (10 只票 + 大盘)
3. 算每个持仓的:
   - 当前市值
   - 浮动盈亏 (市值 - 成本)
   - 持仓权重 (市值 / 总净值)
4. 累计净值:
   - 今日: Σ 持仓市值 + cash
   - 累计收益率: (今日 - init_capital) / init_capital
5. 风险检查 (任一触发即行动):
   a. 单只票从 HWM 回撤 > stop_loss_pct (8%) → 标记 + 不自动清仓
   b. 持仓权重 > single_stock_cap_pct (10%) → 警告
   c. 行业暴露 > industry_cap_pct (30%) → 警告
   d. ATR 动态止损触发 → 记录 alerts
6. 写 portfolio.json (更新 last_nav / last_update / holdings)
7. 追加 history.csv (date, time, nav, pnl)
8. 输出报告 (stdout 简版 + 详细在 portfolio.json)
```

**rebalance 模式** (`--rebalance`):
```
1. 读 pipeline_output/selected_*.json (上一步选股)
2. 对比当前持仓,计算:
   - to_buy: 新入选但未持有
   - to_sell: 持有但新未入选
   - to_hold: 都在
3. 算订单 (考虑 lot_size 100,涨跌停预检)
4. 算交易成本 (commission + stamp_tax + min_commission)
5. 模拟执行 (假设收盘价成交)
6. 写 portfolio.json (新持仓 + cash 扣减)
7. 写 last_rebalance_snapshot (回滚用)
```

### 2.2 `daily_report.py` (313 行)

**职责**:把今日持仓 + 净值 + LLM 信号拼成 Markdown。

**读**:
- `portfolio.json` (今日净值 + 持仓明细)
- `history.csv` (历史净值 → include_history_rows 行)
- `llm_signals/{date}.jsonl` (LLM 信号,可选,缺失跳过)

**写**:`dailyRepo/{date}.md`

**样例** (见 `samples/2026-06-22.md`):
```markdown
# 纸交日报 — 2026-06-22

## 组合概况
- **当前净值**: ¥984,466.00
- **累计收益**: -1.55%
- **持仓数**: 10

## 持仓明细
| 标的 | 代码 | 持仓(股) | 成本价 | 现价 | 盈亏% |
| ... 10 rows ...

## 净值历史
| 日期 | 净值(¥) | 累计收益% |
| ... 10 rows ...

📅 今日净值 ¥984,466.00,盈亏 -1.55%,持仓 10 只
_⚠️ 以上为模拟结果，不构成投资建议_
```

### 2.3 `build_dashboard.py`

**职责**:matplotlib 三线图,写到 `dailyRepo/nav_dashboard_*.png`。

- 持仓组合净值 (蓝)
- 沪深 300 基准 (橙,自动归一化到同起点)
- 单只持仓 (灰底,subplot)

### 2.4 `llm_signal_extractor.py` (381 行)

**职责**:把公告变成 LLM 信号。

```
读 portfolio.json 持仓
   ↓
akshare 拉每只近 N 天公告 (默认 3 天)
   ↓
拼 prompt: 持仓上下文 + 公告全文
   ↓
GLM-5.1 (Anthropic Messages API)
   ↓
JSON parser: { stock_code, stock_name, sentiment, signal, reasoning }
   ↓
写 llm_signals/YYYY-MM-DD.jsonl (一行一个 signal)
```

**配置**:GLM 凭据从 `daily_stock_analysis/.env` 读,统一来源。

**降级**:LLM 调用失败时,该票 signal 字段 = "❓ LLM failed",不影响其他票。

### 2.5 `market_timing.py` (123 行)

**职责**:沪深 300 MA60 大盘择时。

```
fetch_sh000300 (腾讯) → 日线
   ↓
load_or_fetch (本地 pkl 缓存,7 天内不重拉)
   ↓
ratio = close / MA60
   ↓
ratio ≥ 1.00 → 满仓
0.95 ≤ ratio < 1.00 → 半仓
ratio < 0.95 → 空仓
```

**输出**:`{date, close, ma60, ratio, position_pct, action}`

> **注意**:本系统的 `trader.py` **没接入** market_timing 输出。`position_pct` 只在 daily_report 里展示,不实际调整持仓。**已知 gap**:见 §8。

---

## 3. 数据 Schema

### 3.1 `portfolio.json`

```json
{
  "schema_version": 1,
  "init_capital": 1000000.0,
  "created_at": "2026-05-26T...",
  "last_update": "2026-06-22T15:31:10",
  "last_nav": 984466.00,
  "last_pnl_pct": -1.55,
  "cash": 15027.0,
  "holdings": {
    "001965": {
      "name": "招商公路",
      "shares": 10200,
      "cost": 9.50,
      "last_price": 10.04,
      "mkt_value": 102408.0,
      "pnl": +5508.0,
      "pnl_pct": +5.68,
      "weight_pct": 10.40,
      "buy_date": "2026-05-26"
    },
    ... 9 more ...
  },
  "last_data_error": null,
  "migration_log": []
}
```

**字段含义**:
- `schema_version`: 升级时检查,见 `trader.py:23`
- `last_data_error`: 连续失败标记,业务侧可见
- `migration_log`: schema 升级历史

### 3.2 `history.csv`

```
date,time,nav,pnl_pct,cash
2026-06-08,14:33:43,972295.00,-2.77,15000
2026-06-08,15:32:36,971840.00,-2.82,15000
...
```

每次 `trader.py` 跑追加一行。**注意**:同一天可能有多行(15:30 + 盘后 + 月度 rebalance)。

### 3.3 `llm_signals/YYYY-MM-DD.jsonl`

每行一个 signal:
```json
{"stock_code":"600519","stock_name":"贵州茅台","analysis_date":"2026-06-22","announcements_count":3,"sentiment":"positive","signal":"hold","confidence":0.78,"reasoning":"..."}
```

---

## 4. 怎么独立跑

```bash
# 1. 初始化(只用一次)
python paper_trader/trader.py --init --capital 1000000

# 2. 日常跑(更新净值 + 输出报告)
python paper_trader/trader.py

# 3. 月度 rebalance
python paper_trader/trader.py --rebalance

# 4. 单独出 dashboard
python paper_trader/build_dashboard.py

# 5. 单独出日报
python paper_trader/daily_report.py

# 6. 单独 LLM 信号
python paper_trader/llm_signal_extractor.py

# 7. 大盘择时
python paper_trader/market_timing.py

# 8. LLM 信号调试 (拉公告不调 LLM)
python paper_trader/llm_signal_extractor.py --dry-run
```

---

## 5. 风险控制全景

| 规则 | 配置项 | 触发动作 | 实测触发 |
|---|---|---|---|
| 单只止损 | `risk.stop_loss_pct: 0.08` | 标记 + 警告 | — |
| 单只上限 | `risk.single_stock_cap_pct: 0.10` | rebalance 时限制 | — |
| 行业上限 | `risk.industry_cap_pct: 0.30` | rebalance 时限制 | — |
| 流动性下限 | `risk.liquidity_floor: 1.0e8` | rebalance 过滤 | 跳过小票 |
| PE/PB 过滤 | `risk.pe_max: 100` / `pb_max: 15` | rebalance 过滤 | 跳过垃圾股 |
| 换手下限 | `risk.min_turnover_pct: 0.30` | rebalance 跳过 | 月初可能跳过 |
| ATR 动态止损 | `risk.atr_period/k/fallback` | 写 alerts,不自动清 | 警报用 |
| 涨跌停过滤 | `market_rules.pre_trade_check` | rebalance 跳过 | 防止买在涨停 |

---

## 6. 交易成本模型

```python
# 买入
cost_buy = max(shares * price * commission_rate, min_commission)

# 卖出
cost_sell = max(shares * price * commission_rate, min_commission) \
          + shares * price * stamp_tax_rate
```

| 参数 | 默认值 | 含义 |
|---|---|---|
| `commission_rate` | 0.00025 | 万 2.5 佣金,双边 |
| `stamp_tax_rate` | 0.001 | 千 1 印花税,**仅卖出** |
| `min_commission` | 5.0 | 最低 5 元/笔 |

**100 万组合、单边 10% 换手** = 交易 10 万 × 0.00025 = 25 元(单边),双边 50 元。**可以忽略**。
**重仓调仓、单边 50% 换手** = 50 万 × 0.00025 + 印花税 = 125 + 500 = 625 元/次,一年 12 次 ≈ 0.75% 成本。**该算**。

---

## 7. 调仓流程(`--rebalance`)

```
1. 读 pipeline_output/selected_*.json
   例: ["600519","000858","601318",...] (top 10)
2. 读 portfolio.json 当前持仓
3. 算 diff:
   - to_buy: new symbols not in current
   - to_sell: current not in new
4. 算每只新票目标市值 = total_equity / 10
5. 算每只新票目标股数 = floor(target_mkt_value / price / 100) * 100
6. 涨跌停预检: 涨停 → 跳过;跌停 → 不卖
7. 行业暴露检查: 超 30% → 跳过
8. 单只上限检查: 超 10% → 调整股数
9. 算总成本 (commission + stamp_tax)
10. 检查 cash 够不够
11. 模拟执行 (假设收盘价成交)
12. 写 portfolio.json + 写 last_rebalance_snapshot
13. 输出报告
```

---

## 8. 已知限制 / 偏差

| 限制 | 量级 | 缓解 |
|---|---|---|
| **不模拟滑点** | 小票 1–3% | 仅交易大票(`liquidity_floor` 1 亿) |
| **不模拟涨跌停无法买入** | rebalance 时 | `pre_trade_check` 跳过 |
| **不接入大盘择时** | 100% | **已知 gap** — market_timing 输出没接到 trader |
| **单只止损不自动清仓** | — | 只标记,人工 review |
| **纸交 ≠ 实盘** | 心理+流动性 | 仅作策略验证,非下单工具 |
| **history.csv 同日多行** | dashboard 跳点 | 取当日最后一行 |
| **LLM 信号当日才有效** | — | 隔日 signal 不用 |

---

## 9. 故障排查

| 症状 | 原因 | 排查命令 |
|---|---|---|
| 净值 NaN | 数据源失败 | `cat portfolio.json \| jq .last_data_error` |
| 持仓成本价异常 | migration 出错 | `cat portfolio.json \| jq .migration_log` |
| dashboard 不更新 | build_dashboard 没跑 | `ls -la dailyRepo/nav_dashboard_*.png \| tail -3` |
| 日报缺 LLM 信号 | extractor 没跑 | `ls llm_signals/ \| tail -5` |
| 涨跌幅爆 11% | 涨跌停预检漏了 | 检查 stock 代码前缀(60/00/30 主板) |
| rebalance 不下单 | 换手 < 30% | 检查 `risk.min_turnover_pct` |

---

## 10. 联动

- 上游: [CRON_SETUP.md](./CRON_SETUP.md) — 16 个 cron 怎么装
- 配合: [CONFIG_REFERENCE.md](./CONFIG_REFERENCE.md) — 13 区段配置怎么调
- 集成: `skills/quant-daily-suite/SKILL.md` — Hermes skill 入口
