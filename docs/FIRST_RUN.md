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
