---
name: quant-daily-suite
description: 操作 quant-daily-report-suite 仓库的 A 股纸交 + 净值跟踪 + 大盘择时 + 日报系统。触发场景:用户问 paper_trader / 纸交 / 净值 / 大盘择时 / daily report / 选股流水线 / 配置 quant_config.yaml / cron 故障 / 路径迁移。
---

# Quant Daily Suite Skill

## 何时加载

加载本 skill 当用户说:
- "跑一下纸交"、"更新一下净值"、"trader.py 报错"
- "调仓"、"rebalance"、"选股"
- "日报没生成"、"daily_report 出问题"
- "大盘择时"、"market_timing"、"MA60"
- "改 quant_config"、"路径迁移到新机器"
- "加个 cron"、"cron 没跑"、"job 失败了"
- "看 portfolio.json"、"看 history.csv"

不要在以下情况加载:
- 写新策略(那是 quant-pipeline skill 的事)
- 单纯回测(那是 vnpy-alpha-end-to-end)
- ETF 单独调度(那是 AlphaGPT 项目)

---

## 系统一句话

```
行情 → 选股 → 纸交 → 净值 + dashboard + LLM 信号 → 日报
```

完整架构见 `../docs/ARCHITECTURE.md`。

---

## 路径常量(最重要)

**永远不要硬编码**。用 `from quant_core.paths import paths` + `paths.PAPER_TRADER` 等。

| 常量 | 默认值 | 含义 |
|---|---|---|
| `paths.PROJECTS` | `~/Projects` | 项目根,改 `QUANT_PROJECTS` env 覆盖 |
| `paths.PAPER_TRADER` | `<PROJECTS>/paper_trader` | 纸交根 |
| `paths.PAPER_PORTFOLIO` | `<PAPER_TRADER>/portfolio.json` | 当前持仓 |
| `paths.PAPER_HISTORY` | `<PAPER_TRADER>/history.csv` | 净值历史 |
| `paths.PAPER_KLINE_CACHE` | `<PAPER_TRADER>/sh000300_kline.pkl` | K 线缓存 |
| `paths.ALPHA_GPT` | `<PROJECTS>/AlphaGPT` | ETF 项目根 |
| `paths.PIPELINE_OUTPUT` | `<PROJECTS>/pipeline_output` | 选股输出 |
| `paths.DAILY_REPO` | `<PROJECTS>/$QUANT_KB_NAME/Agent/quant/dailyRepo` | 日报 |
| `paths.LOGS_DIR` | `<PROJECTS>/quant_logs` | 日志 |
| `paths.CONFIG_FILE` | `<PROJECTS>/quant_config.yaml` 或 `QUANT_CONFIG` | 配置文件 |

迁移到新机器:

```bash
export QUANT_PROJECTS=/Users/<user>/Projects
```

所有脚本无需修改,自动用新路径。

---

## 8 个核心命令

| 想做什么 | 命令 | 注意 |
|---|---|---|
| 初始化组合 | `python paper_trader/trader.py --init --capital 1000000` | 只用一次 |
| 更新净值 | `python paper_trader/trader.py` | 15:30 cron 跑 |
| 月度调仓 | `python paper_trader/trader.py --rebalance` | 1 号 15:30 |
| 出 dashboard | `python paper_trader/build_dashboard.py` | 依赖 history.csv |
| 出日报 | `python paper_trader/daily_report.py` | 写 dailyRepo/ |
| LLM 信号 | `python paper_trader/llm_signal_extractor.py` | 写 llm_signals/ |
| 大盘择时 | `python paper_trader/market_timing.py` | 看 sh000300 ratio |
| 调试 LLM 拉数据 | `python paper_trader/llm_signal_extractor.py --dry-run` | 不调 LLM |

---

## 常见任务(6 类)

### 任务 1: 用户说"纸交报错了"

```bash
# 1. 看最近错误
cat paper_trader/portfolio.json | python -m json.tool | grep -A2 last_data_error

# 2. 手动跑一次看完整 traceback
cd paper_trader && python3 trader.py 2>&1 | tail -50

# 3. 检查 config 是否能加载
python -c "from quant_core.config import get_config; print(get_config().risk.stop_loss_pct)"

# 4. 如果 config 加载失败,检查 YAML
python -c "import yaml; print(yaml.safe_load(open('/path/to/quant_config.yaml'))['risk'])"
```

**修复套路**:
- YAML 语法错 → `config.py` 走 fallback,用硬编码值,业务照跑但配置不生效 → 修 YAML
- 拉数据失败 → `data_source.fetch_tencent_quotes_with_retry` 自动重试 3 次 → 看 portfolio.json.last_data_error
- rebalance 没下单 → `min_turnover_pct` 触发,换手 < 30% → 检查 selected_*.json 实际变更

### 任务 2: 用户说"加个新 cron"

参考 `../docs/CRON_SETUP.md §3.2`,典型:

```bash
hermes cronjob create \
  --name "周报 17:00" \
  --schedule "0 17 * * 0" \
  --prompt "运行 cd /Users/<user>/Projects/paper_trader && python3 daily_report.py --weekly" \
  --workdir "/Users/<user>/Projects/paper_trader"
```

**时间冲突检查**(见 CRON_SETUP.md §2):
- 15:15 选股 → 15:30 净值 → 15:40 dashboard → 16:00 日报
- 任一环节晚于 5 分钟,会撞后一环节

### 任务 3: 用户说"改 quant_config"

**单字段改**(改完立即生效,无需重启):

```bash
# 1. 验证改完能加载
python -c "from quant_core.config import get_config, reset_cache; reset_cache(); print(get_config().risk.stop_loss_pct)"

# 2. 跑一次 dry-run 验证
cd paper_trader && python3 trader.py 2>&1 | head -20
```

**注意**:`get_config()` 是单例缓存,改完 YAML 后**必须** `reset_cache()` 重新加载,或者重启进程。

**完整字段说明**:`../docs/CONFIG_REFERENCE.md`

### 任务 4: 用户说"路径迁移到新机器"

```bash
# 1. 拷贝仓库
git clone https://github.com/<owner>/quant-daily-report-suite.git
cd quant-daily-report-suite

# 2. 拷贝 quant_config.yaml(从旧机器)
scp <old>:/path/to/quant_config.yaml .

# 3. 改 paths
$EDITOR quant_config.yaml  # 改 data_root / pipeline_output / paper_trader_dir 等

# 4. 验证
python -c "from quant_core.config import get_config; print(get_config().paths)"

# 5. 安装依赖
pip install pandas numpy matplotlib loguru pyyaml akshare requests httpx pydantic

# 6. 试跑
cd paper_trader && python3 trader.py 2>&1 | tail -20
```

完整步骤见 [README §快速开始](../README.md#快速开始)。

### 任务 5: 用户说"日产出问题了"

```bash
# 1. 看 dailyRepo/ 最新文件
ls -la dailyRepo/ | tail -5

# 2. 看 portfolio.json 是否正常
cat paper_trader/portfolio.json | python3 -m json.tool | head -20

# 3. 手动跑日报
cd paper_trader && python3 daily_report.py 2>&1 | tail -30

# 4. 看是不是 llm_signals/ 缺失
ls -la ../"$QUANT_KB_NAME"/Agent/quant/llm_signals/ 2>/dev/null || ls -la ../<your-knowledge-base>/Agent/quant/llm_signals/ | tail -5
```

**常见原因**:
- 拉不到行情 → `portfolio.json.last_data_error` 非空
- 持仓为空 → 跑 `trader.py --init` 重建
- llm_signals 缺失 → 跑 `llm_signal_extractor.py`
- 输出目录不存在 → 手动 `mkdir -p dailyRepo/`

### 任务 6: 用户说"加新因子/新规则"

⚠️ **重要**:本系统是"运营层",不是"研究层"。加新因子 → 改 `quant-pipeline` 项目;新规则 → 改 `trader.py` 但要保持 schema_version 兼容。

**加风控规则**:
1. 改 `quant_config.yaml:risk:` 加字段
2. 改 `quant_core/config.py:RiskConfig` 加 pydantic 字段 + 校验
3. 改 `paper_trader/trader.py` 顶部 try/except 加新常量 + 业务逻辑

**加新输出文件**:
1. 路径加到 `quant_core/paths.py:Paths`
2. 函数加到对应 paper_trader 脚本
3. 加 cron(用 `hermes cronjob create`)

---

## 联动其他 skill

| 任务 | 加载 |
|---|---|
| 选股/因子/IC | `quant-pipeline` |
| 早盘前瞻/last30days | `last30days` |
| ETF 单独调度 | (AlphaGPT 项目 README,不是本 skill) |
| vnpy 因子研究 | `vnpy-alpha-end-to-end` |
| 监控/failure summary | (Hermes cron 默认脚本) |

---

## 故障排查 checklist

| 症状 | 第一步 | 第二步 |
|---|---|---|
| cron 任务报错 | `hermes cronjob show <id>` | `hermes cronjob run <id>` 重跑看 traceback |
| trader.py 数据拉不到 | `cat portfolio.json \| jq .last_data_error` | 手动 `curl https://web.ifzq.gtimg.cn/...` 试 |
| 净值算错 | 检查 portfolio.json 持仓数 | 检查 history.csv 时间戳顺序 |
| 日报没 LLM 信号 | `ls llm_signals/` 缺文件 | `python3 llm_signal_extractor.py --dry-run` |
| dashboard 不更新 | `ls dailyRepo/nav_dashboard_*.png \| tail -3` | 检查 `build_dashboard.py` 输出 |
| rebalance 不下单 | 看 stdout 有没有 "换手<30% 跳过" | 检查 `min_turnover_pct` |
| YAML 改完不生效 | `python -c "from quant_core.config import reset_cache; reset_cache()"` | 重启 cron 任务 |
| 涨跌停触发 | 检查 stock 前缀(60/00/30 是主板 ±10%) | `market_rules.pre_trade_check` 返回 |
| cron 路径错 | jobs.json 里的 `<user>/...` 占位符没改 | `sed -i '' 's|<user>|你的实际用户名|g' cron_jobs/jobs.json` |
| 跨机器迁移 | 没设 `QUANT_PROJECTS` | `export QUANT_PROJECTS=...` |

---

## 关键文件速查

| 想了解 | 看 |
|---|---|
| 整体架构 | `../docs/ARCHITECTURE.md` |
| 纸交怎么跑 | `../docs/PAPER_TRADER.md` |
| cron 怎么装 | `../docs/CRON_SETUP.md` |
| 配置怎么改 | `../docs/CONFIG_REFERENCE.md` |
| 路径常量定义 | `../quant_core/paths.py` |
| 配置加载器 | `../quant_core/config.py` |
| 涨跌停规则 | `../quant_core/market_rules.py` |
| ATR 止损 | `../quant_core/atr_stop.py` |
| 行业暴露 | `../quant_core/industry.py` |
| 真实日报样例 | `../samples/2026-06-22.md` |
| 真实 dashboard | `../samples/nav_dashboard_2026-06-22.png` |
