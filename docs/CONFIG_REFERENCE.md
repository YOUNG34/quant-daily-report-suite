# 配置文件参考

> `quant_config.yaml` 是本系统的**单一配置源**(single source of truth)。13 个区段,改一处全系统生效。本文逐字段说明。

## 加载顺序

```
1. Python 函数参数 path=         ← 单次调用
2. os.environ['QUANT_CONFIG']    ← 全局配置文件
3. os.environ['QUANT_PROJECTS']  ← 项目根,自动拼 /quant_config.yaml
4. ~/Projects/quant_config.yaml  ← 默认(代码用 Path.home() / "Projects" / "quant_config.yaml" 派生)
```

如果都不存在 → `FileNotFoundError`,提示复制 `quant_config.example.yaml`。

## 加载失败的回退

每个脚本都用 try/except 加载配置。加载失败 → 用代码里的硬编码默认值。**这意味着 YAML 写错时,系统仍能跑**(但不更新配置)。这种"软降级"是设计取舍,见 [ARCHITECTURE.md §关键设计决策](./ARCHITECTURE.md#关键设计决策)。

---

## 区段总览

| 区段 | 作用 | 修改频率 |
|---|---|---|
| `paths` | 全局路径常量 | 首次部署 |
| `pipeline` | Qlib 选股流水线 | 偶尔 |
| `timing` | 大盘择时 (sh000300) | 偶尔 |
| `risk` | 风控参数(止损/暴露/ATR) | 调优时 |
| `data_source` | 数据源容错(重试/超时) | 偶尔 |
| `paper_a` | A 股纸交参数 | 偶尔 |
| `paper_etf` | ETF 纸交参数 | 偶尔 |
| `report` | 日报输出 | 偶尔 |
| `logging` | 日志级别/轮转 | 偶尔 |
| `alerts` | 告警阈值 | 偶尔 |
| `cron_schedule` | cron 时间表(监控用) | 偶尔 |
| `schema_version` | 向前兼容用 | 改 schema 时 |

---

## 字段详解

### `paths` — 路径

| 字段 | 类型 | 含义 | 示例 |
|---|---|---|---|
| `data_root` | str | Qlib 数据根 | `~/.qlib/qlib_data/baostock_cn` |
| `pipeline_output` | str | 选股流水线输出 | `/Users/<user>/Projects/pipeline_output` |
| `paper_trader_dir` | str | 纸交根 | `/Users/<user>/Projects/paper_trader` |
| `alpha_gpt_dir` | str | AlphaGPT ETF 项目根 | `/Users/<user>/Projects/AlphaGPT` |
| `daily_repo` | str | 日报落盘 | `/Users/<user>/Projects/<your-knowledge-base>/Agent/quant/dailyRepo` |
| `logs_dir` | str | 日志目录 | `/Users/<user>/Projects/quant_logs` |
| `snapshots_dir` | str | 行情快照 | `/Users/<user>/Projects/quant_snapshots` |

**首次部署必改**。设置后,代码侧 `from quant_core.config import get_config()` 自动读。

### `pipeline` — 选股流水线

| 字段 | 类型 | 默认 | 范围 | 含义 |
|---|---|---|---|---|
| `model_path` | str | — | 绝对路径 | Qlib 模型文件 (.pkl) |
| `instruments` | str | `csi300` | — | 股票池(csi300/csi500) |
| `fit_start` | str | — | YYYY-MM-DD | 训练起始日 |
| `fit_end` | str | — | YYYY-MM-DD | 训练截止日(通常是 -1 天) |
| `feature_lookback_days` | int | 180 | ≥30 | 因子计算 lookback |
| `top_n` | int | 5 | 1–50 | 最终入选数 |
| `top_buffer` | int | 3 | 0–10 | 流动性过滤 buffer |

**调优建议**:
- `top_n` 越大,等权后单只权重越低,风险越分散
- `fit_end` 不要用今天(look-ahead bias),用昨天
- `fit_start` 太长(>3 年)会让模型适应旧市场结构,失效

### `timing` — 大盘择时

| 字段 | 类型 | 默认 | 范围 | 含义 |
|---|---|---|---|---|
| `enabled` | bool | true | — | 是否启用 |
| `ma_window` | int | 60 | 5–250 | MA 周期 |
| `full_threshold` | float | 1.00 | (0, 2) | ratio ≥ 此值 → 满仓 |
| `half_threshold` | float | 0.95 | (0, 2) | ratio ≥ 此值 → 半仓 |
| `kline_cache` | str | — | 绝对路径 | K 线 pkl 缓存 |
| `kline_start` | str | `2020-01-01` | YYYY-MM-DD | 拉数据起始日 |

**调优建议**:
- `ma_window=60` 是中线,短线用 20,长线用 120
- `full_threshold=1.00` 表示"站上 MA 才满仓",改成 1.02 更严格
- `half_threshold < full_threshold` 校验由 `config.py:_check_thresholds` 保证

⚠️ **已知 gap**:`trader.py` 没接 market_timing 输出,改这些参数**不影响实际持仓**,只影响 daily_report 里展示的"应该仓位"。

### `risk` — 风控

| 字段 | 类型 | 默认 | 范围 | 含义 |
|---|---|---|---|---|
| `stop_loss_pct` | float | 0.08 | [0, 0.5] | 从 HWM 回撤触发止损 |
| `min_turnover_pct` | float | 0.30 | [0, 1] | rebalance 换手 < 此值 → 跳过 |
| `liquidity_floor` | float | 1.0e8 | >0 | 20 日均成交额下限(元) |
| `pe_max` | float | 100 | >0 | PE 过滤上限 |
| `pb_max` | float | 15 | >0 | PB 过滤上限 |
| `single_stock_cap_pct` | float | 0.10 | [0.01, 1] | 单只 ≤ 组合净值 |
| `industry_cap_pct` | float | 0.30 | [0.05, 1] | 单一行业 ≤ 组合净值 |
| `atr_period` | int | 14 | [5, 60] | ATR 计算窗口 |
| `atr_k` | float | 2.5 | [1, 5] | ATR 倍数(止损线 = price - k*ATR) |
| `atr_fallback_pct` | float | 0.08 | [0.01, 0.5] | 冷启动固定 % 兜底 |
| `limit_up_pct_main` | float | 0.10 | (0, 1) | 主板涨停 |
| `limit_up_pct_gem` | float | 0.20 | (0, 1) | 创业板/科创板/北交所涨停 |

**调优建议**:
- `stop_loss_pct=0.08` 是中线(8% 回撤止损),短线改 0.05,长线改 0.12
- `single_stock_cap_pct=0.10` + 10 只票 = 100% 利用率,改 0.05 + 10 只 = 50%(更保守)
- `atr_k=2.5` 是海龟法则标准,激进改 1.5,保守改 3.5
- `pe_max=100` 过滤掉亏损股,严格改 30,松散改 300

### `data_source` — 数据源容错

| 字段 | 类型 | 默认 | 范围 | 含义 |
|---|---|---|---|---|
| `max_retries` | int | 3 | [0, 10] | 单次请求最大重试 |
| `retry_base_delay_sec` | float | 1.0 | >0 | 重试基础间隔(实际 2^attempt) |
| `timeout_sec` | int | 10 | [1, 60] | curl --max-time |
| `batch_size` | int | 50 | [1, 200] | 单次请求最大股票数 |
| `user_agent` | str | `Mozilla/5.0` | — | curl UA |
| `fail_threshold` | int | 2 | ≥1 | 连续失败 N 次标 `last_data_error` |

### `paper_a` — A 股纸交

| 字段 | 类型 | 默认 | 范围 | 含义 |
|---|---|---|---|---|
| `init_capital` | float | 1_000_000 | >0 | 初始资金 |
| `weight_strategy` | str | `equal` | equal/score_weighted | 权重策略 |
| `lot_size` | int | 100 | ≥1 | A 股 1 手 = 100 股 |
| `rebalance_day_of_month` | int | 1 | 1–28 | 调仓日 |
| `rebalance_hour` | int | 15 | 0–23 | 调仓时刻(15:30) |
| `skip_holiday_to_next` | bool | true | — | 节假日顺延 |
| `commission_rate` | float | 0.00025 | [0, 0.01] | 万 2.5 佣金 |
| `stamp_tax_rate` | float | 0.001 | [0, 0.01] | 千 1 印花税(仅卖) |
| `min_commission` | float | 5.0 | ≥0 | 最低 5 元/笔 |

### `paper_etf` — ETF 纸交

| 字段 | 类型 | 默认 | 范围 | 含义 |
|---|---|---|---|---|
| `init_capital` | float | 1_000_000 | >0 | 初始资金 |
| `lot_size` | int | 100 | ≥1 | ETF 1 手 = 100 份 |
| `rebalance_day_of_month` | int | 1 | 1–28 | 调仓日 |
| `rebalance_hour` | int | 9 | 0–23 | 调仓时刻(09:25) |

### `report` — 日报

| 字段 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `enabled` | bool | true | 是否启用 |
| `output_dir` | str | — | Markdown 输出目录 |
| `format` | str | `markdown` | 仅支持 markdown |
| `include_history_rows` | int | 10 | 历史净值展示行数 |
| `disclaimer` | str | "⚠️ 以上为模拟结果..." | 日报末尾免责 |

### `logging` — 日志

| 字段 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `level` | str | `INFO` | DEBUG/INFO/WARNING/ERROR |
| `rotation` | str | `20 MB` | loguru 轮转阈值 |
| `retention` | str | `30 days` | 保留期 |
| `console` | bool | true | 同时 stdout |
| `file` | bool | true | 同时写文件 |

### `alerts` — 告警

| 字段 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `nav_daily_drop_pct` | float | 0.03 | 单日跌幅 > 3% → 告警 |
| `cron_missing_jobs` | bool | true | 缺当日产物 → 告警 |
| `data_fetch_failure_max` | int | 2 | 连续失败 N 次 → 告警 |
| `model_oos_min_sharpe` | float | 1.5 | OOS Sharpe < 1.5 → 告警 |

### `cron_schedule` — cron 时间表(监控用)

**只用于 dashboard 展示**,实际调度由 Hermes cronjob 负责(见 [CRON_SETUP.md](./CRON_SETUP.md))。

| 字段 | 含义 | 默认 |
|---|---|---|
| `pipeline_daily` | 选股 cron | `15 15 * * 1-5` |
| `paper_a_nav_daily` | A 股净值 cron | `30 15 * * 1-5` |
| `paper_etf_nav_daily` | ETF 净值 cron | `30 15 * * 1-5` |
| `daily_report` | 日报 cron | `0 16 * * 1-5` |
| `rebalance_monthly` | 月度调仓 cron | `30 15 1 * *` |
| `etf_signal_monthly` | ETF 信号 cron | `25 9 1 * *` |
| `etf_backtest_monthly` | ETF 回测 cron | `35 9 1 * *` |

### `schema_version`

当前 `2`。改 schema 时 +1。**注意**:这是**配置**的版本,不是 portfolio.json 的版本(那是 `trader.py:_CURRENT_SCHEMA_VERSION = 1`)。

---

## 校验规则

`config.py` 用 pydantic 校验所有字段,违反约束 → 启动失败。

**例子**:
- `risk.stop_loss_pct` 不在 [0, 0.5] → `ValueError`
- `timing.full_threshold` 不在 (0, 2) → `ValueError`
- `paths.data_root` 为空 → 缺失字段错误

**好处**:启动时就能抓到配置错误,不会运行时才崩。

---

## 配置 vs 代码 fallback

每个脚本顶部的 `try/except` 块:

```python
try:
    from quant_core.config import get_config
    _cfg = get_config()
    _STOP_LOSS_PCT = _cfg.risk.stop_loss_pct
except Exception:
    # 加载失败 → 用硬编码
    _STOP_LOSS_PCT = 0.08
```

**含义**:
- 配置加载成功 → 用 YAML 值
- 配置加载失败 → 用硬编码值(代码里写死的)
- 不会出现"加载失败 + 抛异常 + 脚本崩"

**实际意义**:
- 你可以**临时**改硬编码值做实验,不影响 YAML
- YAML 写错时,系统不崩,只 warning
- 但代价是:YAML 改了可能不生效(因为走了 fallback)

**建议**:
- 改完 YAML 后,跑 `python -c "from quant_core.config import get_config; print(get_config().risk.stop_loss_pct)"` 验证加载
