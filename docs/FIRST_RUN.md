# FIRST_RUN.md — 5 分钟跑通指南

> 目标:从 `git clone` 到看到第一份模拟日报,不超过 5 分钟。

---

## 0. 前置条件

- **macOS 13+** (Linux 也兼容)
- **Python 3.10+** (`python3 --version` 检查)
- **curl** (macOS 自带)
- **git** (克隆仓库用)

无需:`sudo`、`brew install`、`docker`、任何系统级配置。

---

## 1. 克隆 + 进入目录

```bash
git clone https://github.com/<your-username>/quant-daily-report-suite.git
cd quant-daily-report-suite
```

---

## 2. 一键安装

```bash
bash install.sh
```

脚本会自动:
- 创建 `.venv` 虚拟环境
- 安装 `requirements.txt` 全部依赖(2-5 分钟)
- 复制 `.env.example` → `.env`
- 复制 `paper_trader/portfolio.json.template` → `portfolio.json`
- 运行健全性测试(import 核心库 + `paths.py` 自检)

看到 `🎉 安装完成!` 即成功。

---

## 3. 编辑 `.env`(2 分钟)

```bash
vim .env   # 或 code / subl / nano 都行
```

**必改项**:
- `ANTHROPIC_API_KEY=sk-...` ← 你的 LLM API key
- 或设 `DSA_ENV_FILE=$HOME/Projects/daily_stock_analysis/.env` 复用现成凭据

**可选改**:
- `QUANT_KB_NAME=<your-knowledge-base>` ← 你的知识库根目录名(默认占位符即可)

保存后:`chmod 600 .env`(防止 token 泄漏)

---

## 4. 编辑 `quant_config.yaml`(1 分钟)

```bash
vim quant_config.yaml
```

**全局替换占位符**:
```bash
# 用你的 macOS 用户名替换 <your-username>(如 owen)
sed -i '' "s/<your-username>/$(whoami)/g" quant_config.yaml
```

然后把 `<your-knowledge-base>` 换成你的 KB 根目录名(或保持默认,运行时通过 `QUANT_KB_NAME` env var 覆盖)。

---

## 5. 编辑 `paper_trader/portfolio.json`(可选)

如果你想从空仓起步(纯模拟净值),**跳过此步**,`portfolio.json` 已有合法 schema。

如果你有模拟持仓想导入:
- 改 `init_date` / `init_capital` / `holdings`
- 参考 `portfolio.json.template` 字段说明

---

## 6. 第一次 dry-run

```bash
source .venv/bin/activate
python3 paper_trader/daily_report.py --dry-run
```

**预期输出**:
- 终端打印 markdown 日报内容
- 不实际推送 / 不写文件

**常见错误**:

| 错误 | 原因 | 解决 |
|---|---|---|
| `ModuleNotFoundError: loguru` | 没装依赖 | `pip install -r requirements.txt` |
| `FileNotFoundError: portfolio.json` | 没 cp template | `cp paper_trader/portfolio.json.template paper_trader/portfolio.json` |
| `KeyError: 'QUANT_KB_NAME'` | env 没设 | 在 `.env` 加 `QUANT_KB_NAME=xxx` |
| LLM 调用 401 | API key 错 | 检查 `ANTHROPIC_API_KEY` |

---

## 7. 实际生成日报

```bash
# 移除 --dry-run 真正写文件
python3 paper_trader/daily_report.py
```

输出文件:
```
$HOME/Projects/$QUANT_KB_NAME/Agent/quant/dailyRepo/YYYY-MM-DD.md
```

---

## 8. 设置定时任务(可选)

参考 [`docs/CRON_SETUP.md`](CRON_SETUP.md) 把日报接到 hermes cron,工作日 16:00 自动跑。

也可以手动加 launchd / crontab:
```cron
0 16 * * 1-5 cd /path/to/quant-daily-report-suite && .venv/bin/python3 paper_trader/daily_report.py
```

---

## 9. 进阶

跑通基础流程后,可选:
- **跑回测**:`python3 paper_trader/trader.py --backtest`
- **看 dashboard**:`python3 paper_trader/build_dashboard.py`
- **跑因子流水线**:参考 `quant-pipeline` skill(`vnpy-alpha-end-to-end` 或 `a-share-alpha-mining`)

---

## 10. 求助

- 看 [`README.md`](../README.md) 顶层概览
- 看 [`docs/CONFIG_REFERENCE.md`](CONFIG_REFERENCE.md) 所有配置项
- 看 [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) 模块关系图
- 看 [`docs/PAPER_TRADER.md`](PAPER_TRADER.md) 纸交模块详解
- GitHub Issues: <https://github.com/<your-username>/quant-daily-report-suite/issues>

---

✅ 至此,你的量化日报系统已经跑通。下一步建议:把日报接入 hermes cron,享受"每天 16:00 自动看昨日模拟结果"的便利。

---

## 11. 仓库资产清单(LFS + 警觉性 metadata)

仓库除了"代码 + 文档",还打包了三类**真实运行产物**,放在 `assets/` 目录下,全部走 **Git LFS**。

### 11.1 资产结构

```
assets/
├── MODEL_META.json                ← ⚠️ 模型警觉性 metadata(必读)
├── models/
│   ├── qlib_model_alpha162.pkl    ← 主模型(790KB,live inference 用)
│   ├── qlib_model_alpha158.pkl    ← baseline 对照(817KB)
│   └── qlib_model_alpha162_backup.pkl ← cn_data 升级前备份(944KB,仅追溯)
└── selection_history/
    ├── qlib_picks_20260526.json   ← 实盘选股快照(5 只)
    └── qlib_picks_20260604.json   ← 实盘选股快照(10 只 + timing signal)
```

**配套子项目**(已在仓库根,非 `assets/`):

```
quant_dashboard/                  ← A 股指标看板(代码 + web,数据/日志排除)
quant_trade/                      ← 通用量化交易骨架(AAPL 模拟数据,venv 排除)
```

### 11.2 ⚠️ 警觉性 metadata(必读)

`assets/MODEL_META.json` 记录了**每个模型的诚实标注**,核心几条:

| 字段 | 主模型(qlib_model_alpha162.pkl) | 说明 |
|---|---|---|
| 训练数据截止 | 2026-05-26 | 截止此后模型无新训练 |
| 训练数据源 | baostock_cn (qlib cn_data 1d) | baostock 包含 ST 但不含已退市股 |
| 特征集 | Alpha162(Alpha158 + 4 自研反转/回撤/流动性因子)| 162 维 |
| **Survivorship bias 估计** | **~3.5%**(CSI300 历史退市率 0.7%/yr × 5yr) | ⚠ 未严格验证 |
| **Look-ahead check** | **❌ 未通过**(未跑严格 OOS 三段切分) | ⚠ 这是最大隐患 |
| OOS 指标(IC/Sharpe/MaxDD) | **全部 unknown** | 未做 OOS 回测 |
| 模型 sha256 | 写入 MODEL_META | 校验用 |

**引用这些模型做研究前,请先**:
1. 读 `assets/MODEL_META.json` 完整内容
2. 跑严格的 OOS backtest(train/valid/test 三段)
3. 重新评估 survivorship bias / lookahead bias

### 11.3 选择历史(selection_history)

`assets/selection_history/*.json` 是**真实 cron 跑出来的实盘选股信号**(2026-05-26 / 2026-06-04 各一份)。这些**不是回测业绩**,只是当时模型的"输出快照"。

**禁止**:
- 直接拿来做"模型历史业绩"展示(无 OOS,误导)
- 当作"未来可重复选股依据"(模型已进化,score 无可比性)

**允许**:
- 研究模型在特定日期对哪些股票有高 score
- 对比 alpha162 vs alpha158 在同一日的选股差异
- 检查 timing_signal 与大盘位置的关系

### 11.4 子项目隐私隔离

```bash
# 这些目录已显式排除,clone 下来是空的:
quant_dashboard/data/             # 真实持仓/选股明细/SQLite
quant_dashboard/logs/             # cron 时间戳/扫描进度
quantTrade/.venv/                 # 367MB,Python 3.14 venv
quantTrade/data/cache/            # 临时 parquet(需要 pyarrow 才可读)
```

**子项目怎么跑**:
```bash
# quant_dashboard(在原 ~/Projects/quant-dashboard 跑,不要在本仓库跑)
cd ~/Projects/quant-dashboard
python3 scripts/init_db.py    # 创建 SQLite(空 db)
python3 scripts/fetch_indicators.py  # 拉当天数据
./start.sh                    # 8765 端口,自动开浏览器

# quantTrade
cd ~/Projects/quantTrade
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
python -m quanttrade.demo_pipeline   # 跑通端到端示例
```

### 11.5 LFS 校验

```bash
# 检查 LFS 是否正常工作
git lfs ls-files
# 应看到:
#   assets/models/qlib_model_alpha162.pkl
#   assets/models/qlib_model_alpha158.pkl
#   assets/models/qlib_model_alpha162_backup.pkl
#   assets/selection_history/qlib_picks_*.json

# 校验模型 sha256
sha256sum assets/models/*.pkl
# 应与 MODEL_META.json 中 model_sha256 字段一致
```

如果 `git lfs ls-files` 为空,说明 LFS 没装或没初始化:
```bash
brew install git-lfs       # macOS
git lfs install            # 仓库级初始化
git lfs pull               # 拉取 LFS 对象(若第一次 clone 没自动拉)
```

---

✅ 至此,你已完整理解本仓库的资产边界 + 警觉性标注。引用模型/选股前务必读完 §11.2。
