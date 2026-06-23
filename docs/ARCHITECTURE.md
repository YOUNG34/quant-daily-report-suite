# 系统架构

> 本文配合 [README](../README.md) 阅读。目标读者:clone 仓库后想理解「这堆文件是怎么协作的」的开发者。

## 一句话总结

```
行情数据 ──> 选股 ──> 纸交模拟 ──> 净值/dashboard ──> 日报 ──> (Hermes 投递)
   │             │          │             │              │
腾讯/AKShare   Qlib    paper_trader/   build_dashboard   daily_report.py
                       8 个 Python
```

**核心设计原则**:把"选股"和"持仓"完全解耦。**选股**每月 1 号跑一次,产 5–10 只票的清单;**纸交**每天拉行情算净值,完全不知道选股用了什么模型。

---

## 系统组件

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Quant Daily Report Suite                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────┐    ┌──────────────┐    ┌────────────────────┐   │
│  │  行情数据层   │    │  决策层       │    │  运营层            │   │
│  │  (Data Src)   │    │  (Decision)  │    │  (Operations)      │   │
│  ├──────────────┤    ├──────────────┤    ├────────────────────┤   │
│  │ • 腾讯 gtimg │───>│ • Qlib 选股  │───>│ • trader.py 纸交  │   │
│  │   (实时)     │    │   月度调仓   │    │ • daily_report     │   │
│  │ • akshare     │    │ • 大盘择时   │    │ • build_dashboard  │   │
│  │   (财务/公告) │    │   sh000300   │    │ • llm_signal       │   │
│  │ • Qlib 数据   │    │   MA60       │    │   _extractor       │   │
│  │   (历史因子)  │    │ • 风险规则   │    │                    │   │
│  └──────────────┘    └──────────────┘    └────────────────────┘   │
│         ▲                  ▲                      │                │
│         │                  │                      ▼                │
│         │           ┌──────┴───────┐     ┌────────────────────┐   │
│         │           │  quant_core  │     │  输出 / 投递        │   │
│         └───────────│  (共享工具)   │     │  (Deliverables)     │   │
│                     │ • paths.py   │     │ • Markdown 日报     │   │
│                     │ • config.py  │     │ • PNG dashboard     │   │
│                     │ • logger.py  │     │ • LLM 信号 JSONL   │   │
│                     │ • data_source│     │ • Discord 投递     │   │
│                     │ • market_rules│    │ • cron 自动调度     │   │
│                     │ • atr_stop   │     │                    │   │
│                     │ • industry   │     │                    │   │
│                     └──────┬───────┘     └────────────────────┘   │
│                            │                                        │
└────────────────────────────┼────────────────────────────────────────┘
                             ▼
                    quant_config.yaml
                   (13 区段统一配置源)
```

---

## 数据流(每日 15:15–16:35)

```
15:15  cron  每日量化选股流水线    (cron 46f41b0ecf2a)
             │
             │ quant_pipeline.py → pipeline_output/selected_*.json
             │ (Qlib 模型 + Alpha158/162 因子 → top 5 票)
             ▼
15:30  cron  纸交每日净值更新       (cron 0ededed120d1)
             │
             │ trader.py
             │   ├─ 拉腾讯行情 (10 只持仓)
             │   ├─ 算今日涨跌 / 累计净值
             │   ├─ 检查止损 / 暴露上限
             │   ├─ 写 history.csv (追加)
             │   └─ 更新 portfolio.json
             ▼
15:30  cron  ETF 纸交净值更新       (cron c5174270002c + 740e39513247)
             │
             │ paper_trader_etf.py (AlphaGPT 项目独立子模块)
             ▼
15:35  cron  LLM 信号抽取          (LLM 信号提取,无独立 cron,由 daily_report 触发)
             │
             │ llm_signal_extractor.py
             │   ├─ 读 portfolio.json 持仓
             │   ├─ 拉每只近 3 天公告 (akshare)
             │   ├─ 喂 GLM-5.1 → 结构化 sentiment/signal
             │   └─ 写 llm_signals/YYYY-MM-DD.jsonl
             ▼
15:40  cron  dashboard 重建         (cron 64813bf42146)
             │
             │ build_dashboard.py
             │   ├─ 读 portfolio.json + history.csv
             │   ├─ matplotlib 三线图 (持仓 / 基准 / 单只)
             │   └─ 写 dailyRepo/nav_dashboard_*.png
             ▼
16:00  cron  纸交每日日报           (cron 85a8901828cc)
             │
             │ daily_report.py
             │   ├─ 读 portfolio.json + history.csv + llm_signals/*.jsonl
             │   ├─ 渲染 Markdown
             │   └─ 写 dailyRepo/YYYY-MM-DD.md
             ▼
16:30  cron  cron-failure-summary  (cron 3f0fc75960d1, no_agent)
             │
             │ 收集当天所有 cron 的 last_status / last_error
             ▼
16:35  cron  EOD health check       (cron 528938e4ef19, no_agent)
             │
             │ 检查 今日 5 个产物是否齐:selected / portfolio / history / report / dashboard
```

> **早盘 9:15** 还有一条平行线: `bf77c19b60b1` 早盘前瞻简报 → 拉 Reddit/HN/GitHub/Polymarket 摘要 → Discord + dailyRepo/

---

## 关键设计决策

| 决策 | 为什么 | 备选 |
|---|---|---|
| **配置中心化** (`quant_config.yaml`) | 13 个区段,改一处全系统生效 | 散落环境变量 / 每脚本独立 argparse |
| **加载失败回退** (try/except + 硬编码) | 配置 YAML 写错时,纸交仍能跑 | 抛异常,全部停摆 |
| **选股与持仓解耦** | trader.py 不知道 Qlib,只看 portfolio.json | 紧耦合,改模型影响全链路 |
| **schema_version 字段** | portfolio.json 升级时向后兼容 | 改 schema 直接 fail |
| **risk 单向门** (止损 / 暴露上限 / ATR) | 写死在 risk: 段,人工不调 | 写代码里,改不动 |
| **失败标记** (`last_data_error`) | 连续 N 次拉不到数据不崩溃,只标记 | 第一次失败就 raise |
| **脚本式 cron** (no_agent) | watchdog/healthcheck 这种不需要 LLM | 全用 LLM 调度,贵且慢 |

---

## 文件 ↔ 职责 映射

| 文件 | 行数 | 职责 | 调谁 |
|---|---|---|---|
| `quant_core/paths.py` | 128 | 路径常量 + 环境变量 fallback | — |
| `quant_core/config.py` | 216 | YAML 加载 + pydantic 校验 | paths |
| `quant_core/logger.py` | 60+ | loguru 风格日志 | — |
| `quant_core/data_source.py` | 13K | 腾讯 / akshare 拉数据 + retry | — |
| `quant_core/market_rules.py` | 4.8K | 涨跌停 / 停牌预检 | — |
| `quant_core/atr_stop.py` | 6.8K | ATR 动态止损 | — |
| `quant_core/industry.py` | 7.2K | 行业分类 + 暴露上限 | — |
| `quant_core/dashboard.py` | 4.6K | dashboard 渲染 | — |
| `paper_trader/trader.py` | 36K | 纸交主程序:拉行情 + 算净值 + 调仓 | data_source, market_rules, atr_stop, industry, config, logger |
| `paper_trader/daily_report.py` | 11K | 渲染 Markdown 日报 | config, portfolio.json, history.csv, llm_signals/ |
| `paper_trader/build_dashboard.py` | 4.3K | matplotlib 三线图 | dashboard, config |
| `paper_trader/market_timing.py` | 4.6K | sh000300 MA60 大盘择时 | config, data_source |
| `paper_trader/llm_signal_extractor.py` | 14K | 公告 → GLM-5.1 → 信号 | akshare, GLM API |

---

## 依赖图(无环,单向)

```
                    quant_config.yaml
                            │
                            ▼
                     quant_core/config
                            │
        ┌───────────────────┼────────────────────┐
        ▼                   ▼                    ▼
   market_rules        data_source         atr_stop / industry
        │                   │                    │
        └─────────┬─────────┴─────────┬──────────┘
                  ▼                   ▼
              trader.py        market_timing.py
                  │                   │
                  ├─────────┬─────────┤
                  ▼         ▼         ▼
            history.csv  portfolio.json  llm_signals/*.jsonl
                  │         │         │
                  └─────────┼─────────┘
                            ▼
                    daily_report.py
                            ▼
                    dailyRepo/*.md
```

**没有反向引用**:`daily_report.py` 不会去改 `trader.py` 状态,只读它的产物。这让任何脚本可以独立替换/升级。

---

## 失败模式与恢复

| 失败 | 检测 | 恢复 |
|---|---|---|
| 腾讯接口挂 | `data_source.fetch_tencent_quotes_with_retry` 自动重试 3 次 | 写 `portfolio.json['last_data_error']` 标记,不崩 |
| LLM 调用挂 | `llm_signal_extractor` try/except 单独标 | 信号缺失但日报仍生成 |
| Qlib 模型过期 | 启动时检查 `pipeline.model_path` 存在 | 缺失 → 警告 + 用 fallback |
| cron 没跑 | `cron-failure-summary` 16:30 扫 | Discord 报警 |
| YAML 语法错 | `config.py` 加载失败自动回退硬编码 | trader.py 用 try/except,功能不丢 |

详细排查见 [PAPER_TRADER.md §故障排查](./PAPER_TRADER.md#故障排查)。
