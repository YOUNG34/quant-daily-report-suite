# Quant Daily Report Suite

一套完整的 A 股自动化日报 / 净值跟踪 / 大盘择时 流水线。

每天 15:30 自动完成:
1. **拉行情** — 腾讯免费 API + akshare
2. **算净值** — 10 只等权 CSI300 持仓,实时计算当日收益
3. **画 dashboard** — matplotlib 三线图(持仓净值 / 基准 / 单只标的)
4. **LLM 信号抽取** — 拉公告/研报 → GLM-5.1 结构化提取 → 写 llm_signals/YYYY-MM-DD.jsonl
5. **写日报** — Markdown 格式 → `dailyRepo/YYYY-MM-DD.md`
6. **last30days 早报** — 早盘 9:15 多源(Reddit/HN/GitHub/Polymarket)研究简报

整套系统由 **Hermes Agent + cron** 自动调度,无人工介入。

---

## 目录结构

```
quant-daily-report-suite/
├── README.md                       ← 你正在看
├── LICENSE                         ← MIT
├── .gitignore
│
├── paper_trader/                   ← A 股纸交模拟器(8 文件)
│   ├── trader.py                   ← 36K 主程序,日常跑净值
│   ├── daily_report.py             ← 11K 日报生成器
│   ├── build_dashboard.py          ← 4K matplotlib dashboard
│   ├── llm_signal_extractor.py     ← 14K LLM 信号抽取
│   ├── market_timing.py            ← 5K 沪深 300 MA60 大盘择时
│   ├── portfolio.json              ← 当前持仓(敏感,gitignore)
│   ├── history.csv                 ← 历史净值(敏感,gitignore)
│   └── sh000300_kline.pkl          ← 31K K 线缓存

├── assets/                         ← 模型 + 选股历史 + 警觉性 metadata(LFS,⚠ 必读 MODEL_META.json)
│   ├── MODEL_META.json             ← ⚠ 模型 OOS 警觉性 metadata
│   ├── models/                     ← 主模型 + 备份(LFS)
│   │   ├── qlib_model_alpha162.pkl
│   │   ├── qlib_model_alpha158.pkl
│   │   └── qlib_model_alpha162_backup.pkl
│   └── selection_history/          ← 实盘选股快照(LFS,非回测业绩)
│       ├── qlib_picks_20260526.json
│       └── qlib_picks_20260604.json

├── quant_dashboard/                ← A 股指标看板(代码 + web,数据/日志排除)
│   ├── start.sh                    ← 启动服务(8765)
│   ├── scripts/                    ← 7 个 .py 脚本(已脱敏)
│   └── web/                        ← HTML/JS 静态资源
│   # data/(持仓/选股) + logs/(时间戳) 已 .gitignore
│
├── quant_trade/                    ← 通用量化交易骨架(AAPL 模拟数据)
│   ├── src/quanttrade/             ← 7 个模块(数据/回测/风控/执行/清算)
│   ├── data/sample_ticks.csv       ← AAPL tick 样本(2.1 万行)
│   ├── artifacts/factors.full.csv  ← AAPL 因子结果(7200 行)
│   └── Makefile + pyproject.toml
│   # .venv/(367MB) + data/cache/ 已 .gitignore
│
├── quant_core/                     ← 共享工具库(9 文件,68K)
│   ├── paths.py                    ← 统一路径常量,环境变量覆盖
│   ├── config.py                   ← YAML 配置加载器
│   ├── logger.py                   ← loguru 风格日志
│   ├── dashboard.py                ← dashboard 渲染
│   ├── data_source.py              ← 腾讯/akshare 数据源
│   ├── industry.py                 ← 行业分类 + 暴露上限检查
│   ├── market_rules.py             ← 涨跌停/停牌规则
│   └── atr_stop.py                 ← ATR 动态止损
│
├── quant_config.yaml               ← 全局配置(13 个区段)
│
├── cron_jobs/
│   └── jobs.json                   ← 16 个 cron 任务备份
│
├── last30days_config/
│   └── .env.example                ← last30days 多源研究配置模板
│
├── samples/                        ← dailyRepo 历史输出(32 文件 / 1.4M)
│   ├── 2026-06-08.md ~ 2026-06-22.md          ← A 股日报(12 天)
│   ├── last30days-2026-06-09.md ~ 06-23.md   ← 早报(10 天)
│   ├── nav_dashboard_*.png                   ← matplotlib dashboard(11 张)
│   └── _engine-notes-...md                   ← 引擎调试笔记
│
├── docs/
│   ├── ARCHITECTURE.md             ← 系统架构图
│   ├── PAPER_TRADER.md             ← 纸交子系统详解
│   ├── CRON_SETUP.md               ← 怎么装 cron
│   └── CONFIG_REFERENCE.md         ← quant_config.yaml 字段说明
│
└── skills/
    └── quant-daily-suite/SKILL.md  ← Hermes Agent skill
```

---

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/<your-name>/quant-daily-report-suite.git
cd quant-daily-report-suite
```

### 2. 安装依赖

```bash
pip install pandas numpy matplotlib loguru pyyaml akshare requests
```

### 3. 配置路径(关键)

`quant_core/paths.py` 已支持 4 级 fallback,优先使用环境变量:

```bash
# 方式 1:设置项目根(最常用,自动拼所有子路径)
export QUANT_PROJECTS=/your/workspace

# 方式 2:单独指定 quant_core 安装位置
export QUANT_HOME=/your/workspace/quant_core

# 方式 3:指向自己的配置文件
export QUANT_CONFIG=/your/workspace/your-config.yaml

# 方式 4:不设环境变量,使用默认(~/Projects/...,代码用 Path.home() 派生)
```

**优先级**:参数 > `QUANT_CONFIG` > `QUANT_PROJECTS` > 默认硬编码

### 4. 修改 quant_config.yaml

仓库内 `quant_config.yaml` 是占位符配置,**使用前必须改**为你自己的路径:

```yaml
storage:
  data_root: "/your/data/path"
  pipeline_output: "/your/pipeline/output"
  paper_trader_dir: "/your/workspace/paper_trader"
  daily_repo: "/your/daily-report/output"
  logs_dir: "/your/logs"
```

每个字段的具体含义见 [`docs/CONFIG_REFERENCE.md`](docs/CONFIG_REFERENCE.md)。

### 5. 跑起来

```bash
# 单次跑(选股)
python paper_trader/trader.py --rebalance

# 日常跑(更新净值 + dashboard + 日报)
python paper_trader/trader.py
python paper_trader/build_dashboard.py
python paper_trader/daily_report.py
```

或者用 cron 自动调度(见 [`docs/CRON_SETUP.md`](docs/CRON_SETUP.md))。

---

## ⚠️ 已知硬编码路径清单(使用前必读)

仓库里**仍有部分硬编码路径未环境变量化**,这是有意的取舍 — 全部环境变量化会过度设计,而下面这些路径有更简单的"改一处即可"方案。

### 类别 A:配置驱动(改 `quant_config.yaml` 一处即可)

| 字段 | 用途 | 文件 |
|---|---|---|
| `storage.data_root` | Qlib 数据目录 | `quant_core/paths.py:QLIB_DATA` |
| `storage.pipeline_output` | 量化流水线输出 | `quant_core/paths.py:PIPELINE_OUTPUT` |
| `storage.paper_trader_dir` | 纸交根目录 | `quant_core/paths.py:PAPER_TRADER` |
| `storage.alpha_gpt_dir` | AlphaGPT ETF 项目根 | `quant_core/paths.py:ALPHA_GPT` |
| `storage.daily_repo` | 日报落盘点 | `quant_core/paths.py:DAILY_REPO` |
| `storage.logs_dir` | 日志目录 | `quant_core/paths.py:LOGS_DIR` |
| `storage.snapshots_dir` | 行情快照目录 | `quant_core/paths.py:SNAPSHOTS_DIR` |
| `pipeline.model_path` | Qlib 模型文件 | `trader.py` 启动时检查 |
| `timing.kline_cache` | K 线缓存文件 | `market_timing.py` |
| `daily_report.output_dir` | 日报输出 | `daily_report.py` |

**怎么改**:直接编辑 `quant_config.yaml` 顶部 `storage:` 区段,所有脚本运行时自动读。

### 类别 B:Python 代码 fallback(改文件中的字符串)

当 `quant_config.yaml` 加载失败时(比如 YAML 语法错),脚本会回退到下面的硬编码 fallback。**正常使用不会触发**,但若需要修改:

| 文件 | 行 | 路径 | 怎么改 |
|---|---|---|---|
| `paper_trader/daily_report.py` | 40-41 | `DAILY_REPO_DIR`, `SIG_DIR` | 改成你的实际路径,或保留(只在 yaml 加载失败时用) |
| `paper_trader/llm_signal_extractor.py` | 36 | `SIG_DIR` | 改成你的 LLM 信号落盘目录 |
| `paper_trader/llm_signal_extractor.py` | 39 | `_DSA_ENV` | 改成你的 daily_stock_analysis `.env` 路径,或设 `DSA_ENV_FILE` 环境变量 |
| `paper_trader/market_timing.py` | 27, 36 | `CACHE_FILE` | 改成你的 K 线缓存路径(默认已用 `_cfg.timing.kline_cache`) |
| `paper_trader/trader.py` | 808 | `out_dir` | 改 `PIPELINE_OUTPUT` 环境变量,或直接改这一行 |
| `paper_trader/build_dashboard.py` | 18 | 注释 | 仅文档,无需改 |
| `paper_trader/daily_report.py` | 17 | 注释 | 仅文档,无需改 |

### 类别 C:文档/示例中的路径

`quant_core/paths.py` 第 4/19 行、`quant_core/logger.py` 第 67 行等是 **docstring 中的示例**,说明默认路径是什么,**不影响运行**。

### 验证零残留

```bash
grep -rn "/Users/<user>" --include="*.py" --include="*.yaml" .
```

应该看到:
- 仅在文档/注释中(`/your/dockstring`)
- 在 `quant_core/paths.py` 的 `_DEFAULT_*` 常量中(被环境变量覆盖)
- 在 `quant_config.yaml` 的 `storage:` 区段(用户必改)

---

## 🔧 环境变量优先级速查

| 优先级 | 来源 | 影响范围 |
|---|---|---|
| 1 (最高) | Python 函数参数 `path=` | 单次调用 |
| 2 | `os.environ['QUANT_CONFIG']` | 全局配置文件位置 |
| 3 | `os.environ['QUANT_PROJECTS']` | 项目根,自动拼所有子路径 |
| 4 | `os.environ['QUANT_HOME']` | quant_core 安装位置 |
| 5 | `os.environ['QLIB_DATA']` | Qlib 数据路径 |
| 6 | `os.environ['PIPELINE_OUTPUT']` | 流水线输出 |
| 7 | `os.environ['LLM_SIGNALS_DIR']` | LLM 信号落盘点 |
| 8 | `os.environ['DSA_ENV_FILE']` | daily_stock_analysis .env |
| 9 | `os.environ['PYTHON_BIN']` | daily_report 调用子进程时用 |
| 10 (最低) | `paths.py` 内的 `_DEFAULT_*` 常量 | 兜底 |

---

## 📊 Sample 输出说明

`samples/` 目录是真实运行产物,**仅供学习参考,不代表本仓库推荐的策略**。

| 文件类型 | 来源 | 说明 |
|---|---|---|
| `2026-06-08.md ~ 2026-06-22.md` | `daily_report.py` | A 股纸交日报,持仓 + 净值 + 收益归因 |
| `last30days-*.md` | last30days skill | 早盘前瞻,多源 Reddit/HN/GitHub 摘要 |
| `nav_dashboard_*.png` | `build_dashboard.py` | matplotlib 三线净值曲线 |
| `_engine-notes-*.md` | Hermes engine | 调试笔记 |
| `ai-quantitative-*-raw-cron-*.md` | last30days | 一次失败 run 的原始输出 |

**敏感信息**:`samples/dailyRepo/` 里包含原 workflow 的真实持仓明细(10 只 CSI300 票 + 成本价 + 现价 + 浮动盈亏)。`paper_trader/portfolio.json` 和 `history.csv` 已被 .gitignore 排除,不会进仓库。**生产环境使用前请删除 samples/ 目录**,或自行替换为脱敏数据:

```bash
rm -rf samples/dailyRepo/   # 32 文件,1.4M,含 12 个真实日报 md
# paper_trader/portfolio.json / history.csv / .stock_name_cache.json
# 已在 .gitignore 排除,clone 下来的仓库本来就没这些
```

---

## 🤖 与 Hermes Agent 集成

仓库内 `skills/quant-daily-suite/SKILL.md` 是 Hermes Agent 可直接加载的 skill,描述了:
- 整套系统的架构与依赖
- 常见运维任务的步骤(改路径、调试 cron、查信号)
- 故障排查 checklist

把 skill 装到 Hermes profile:

```bash
cp -r skills/quant-daily-suite ~/.hermes/profiles/<your-profile>/skills/
```

---

## 📜 License

MIT — 见 [LICENSE](LICENSE)。

---

## 🙏 致谢

- [腾讯免费行情 API](https://web.ifzq.gtimg.cn) — 实时行情 + 历史 K 线
- [akshare](https://github.com/akfamily/akshare) — A 股财务/公告数据
- [Qlib](https://github.com/microsoft/qlib) — 量化研究平台
- [loguru](https://github.com/Delgan/loguru) — 日志
- [matplotlib](https://matplotlib.org) — dashboard

---

## ⚠️ 免责声明

本仓库仅为量化研究 / 学习用途。**所有 sample 输出为历史模拟盘数据,不构成投资建议**。  
实盘前请充分回测 + 风险评估,任何盈亏由使用者自行承担。