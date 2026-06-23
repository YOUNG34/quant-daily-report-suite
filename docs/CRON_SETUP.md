# Cron 调度设置

> 本系统的 16 个 cron 任务由 [Hermes Agent](https://hermes-agent.nousresearch.com) 调度,不是传统的 `crontab`。本文讲:任务清单 + 怎么装 + 怎么从 `jobs.json` 恢复。

## 1. 任务清单(16 个)

| 时刻 | 名称 | 类型 | 任务 ID (前 8 位) | 说明 |
|---|---|---|---|---|
| `*/30 * * * *` | akshare-progress-report | no_agent | 2f46a6b6 | 已暂停,30 分钟一次,数据源健康度 |
| `15 9 * * 1-5` | 早盘前瞻简报 (last30days) | agent | bf77c19b | 9:15 早盘前瞻,Discord + dailyRepo/ |
| `25 9 1 * *` | AlphaGPT ETF 月度信号 | agent | d0a808e7 | 每月 1 号 9:25 跑 ETF 选股 |
| `35 9 1 * *` | etf-monthly-backtest | agent | 15ec975b | 每月 1 号 9:35 跑 ETF 回测 |
| `15 15 * * 1-5` | 每日量化选股流水线 | agent | 46f41b0e | 15:15 跑 Qlib 选股 |
| `30 15 * * 1-5` | 纸交每日净值更新 | agent | 0ededed1 | 15:30 跑 trader.py 算净值 |
| `30 15 * * 1-5` | AlphaGPT ETF 纸交净值 | agent | c5174270 | 15:30 跑 ETF paper_trader |
| `30 15 1 * *` | paper_trader_rebalance_monthly | agent | 1ceb09f3 | 每月 1 号 15:30 调仓 |
| `35 15 * * 1-5` | AlphaGPT ETF multi 纸交 | agent | 740e3951 | 15:35 跑 ETF multi 净值 |
| `40 15 * * 1-5` | 纸交 dashboard 重建 | agent | 64813bf4 | 15:40 跑 build_dashboard.py |
| `0 16 * * 1-5` | 纸交每日日报 | agent | 85a89018 | 16:00 跑 daily_report.py |
| `0 10-16 * * 1-5` | last30days watchdog | no_agent | db271399 | 10:00–16:00 每小时,失败告警 |
| `30 16 * * *` | cron-failure-summary | no_agent | 3f0fc759 | 16:30 扫当天 cron 失败 |
| `35 16 * * 1-5` | quant EOD health check | no_agent | 528938e4 | 16:35 检产物齐不齐 |
| `0 17 * * 0` | quant weekly eval | no_agent | bcaa89bf | 周日 17:00 周评估 |
| `0 17 * * 1-5` | revenue_tracker_daily | agent | 5370e3a9 | 工作日 17:00 营收跟踪 |

**agent** = LLM 跑(`no_agent=False`),**no_agent** = 脚本直跑(无 LLM 介入)。

---

## 2. 时间冲突检查

工作日关键时序(避免数据依赖死锁):

```
15:15 选股 → 15:30 净值 → 15:35 ETF → 15:40 dashboard → 16:00 日报
```

| 顺序 | 任务 | 必须在前 | 否则 |
|---|---|---|---|
| 15:15 | 选股 | — | 启动 rebalance 找不到 selected_*.json |
| 15:30 | 净值 | 选股 | 净值用旧持仓对比 OK |
| 15:30 | rebalance (1 号) | 选股 | 找不到 selected → 跳过 |
| 15:40 | dashboard | 净值 | history.csv 没新行 → 图断点 |
| 16:00 | 日报 | 净值 + dashboard | 报告无图无最新净值 |

> **bug 案例**(2026-06):某次 15:15 选股 pipeline 慢到 15:25 才完成,15:30 净值跑时 portfolio.json 还在被写,导致 `trader.py` 读 portfolio 时是中间态。**修复**:用 `trader.py` 文件锁 + `pipeline_output/selected_*.json` 时间戳校验,15:25 前完成即可。

---

## 3. 怎么装 / 恢复

### 3.1 全新安装(用 `cron_jobs/jobs.json`)

仓库内的 `cron_jobs/jobs.json` 是脱敏后的备份,16 个任务完整。

```bash
# 1. 复制并修改路径
cp cron_jobs/jobs.json /tmp/my-jobs.json
# 编辑,把 <user>/<profile> 占位符改成你的实际值

# 2. 用 Hermes cronjob 工具批量 import
hermes cronjob import /tmp/my-jobs.json

# 3. 验证
hermes cronjob list
```

### 3.2 单个任务安装

```bash
# 纸交每日净值(最常用)
hermes cronjob create \
  --name "纸交每日净值更新" \
  --schedule "30 15 * * 1-5" \
  --prompt "cd /Users/<user>/Projects/paper_trader && python3 trader.py" \
  --workdir "/Users/<user>/Projects/paper_trader"
```

### 3.3 改路径(克隆到新机器)

`jobs.json` 里的路径全是 `<user>/...` 占位,需要替换成你的真实用户名:

```bash
# 替换占位符(在 macOS/Linux 都行)
# 注意:jobs.json 里的 <user>、<profile>、<discord-chat-id>、<discord-channel-name> 都是占位符
# 你的实际用户名可通过 `whoami` 查
sed -i '' 's|<user>|你的实际用户名|g' cron_jobs/jobs.json
sed -i '' 's|<profile>|quant|g' cron_jobs/jobs.json
# Discord 投递目标(可选,如果用 Discord 而非 Telegram)
sed -i '' 's|<discord-chat-id>|你的 chat id|g' cron_jobs/jobs.json
sed -i '' 's|<discord-channel-name>|你的频道名|g' cron_jobs/jobs.json
```

> ⚠️ **警告**:`sed -i ''` 是 macOS BSD sed 写法。Linux 用 `sed -i`。

### 3.4 暂停 / 恢复 / 删除

```bash
# 暂停某个
hermes cronjob pause <job_id>

# 恢复
hermes cronjob resume <job_id>

# 删除
hermes cronjob remove <job_id>
```

---

## 4. 任务详细配置

### 4.1 每日量化选股流水线 (46f41b0ecf2a)

```json
{
  "schedule": "15 15 * * 1-5",
  "skills": ["quant-pipeline"],
  "enabled_toolsets": ["terminal", "file", "web"],
  "workdir": "~/Projects"
}
```

**prompt 摘要**:
```bash
cd ~/Projects
source ~/Projects/quant-env/bin/activate
PYTHONUNBUFFERED=1 python3 quant_pipeline.py --top 10 2>&1
```

**输出**:`pipeline_output/selected_*.json` (top 10 票)

### 4.2 纸交每日净值更新 (0ededed120d1)

```json
{
  "schedule": "30 15 * * 1-5",
  "skills": [],
  "workdir": "~/Projects/paper_trader"
}
```

**prompt 摘要**:跑 `trader.py` → 输出 nav / 累计收益% / 各持仓盈亏

### 4.3 早盘前瞻简报 (bf77c19b60b1)

```json
{
  "schedule": "15 9 * * 1-5",
  "skills": ["last30days"],
  "workdir": "~/.hermes/profiles/quant/skills/research/last30days"
}
```

**核心能力**:
- 拉 Reddit / HN / GitHub / Polymarket / YouTube
- 多源汇总 → 4 段中文早报
- Discord 投递 + 写 dailyRepo/

**依赖**:见 `last30days` skill 的 SKILL.md

### 4.4 纸交每日日报 (85a8901828cc)

```json
{
  "schedule": "0 16 * * 1-5",
  "model": "MiniMax-M2.7",
  "workdir": "~/Projects/paper_trader"
}
```

**prompt 摘要**:跑 `daily_report.py` → Markdown → 写 dailyRepo/YYYY-MM-DD.md

---

## 5. no_agent 任务说明

4 个 no_agent 任务(脚本直跑,无 LLM):

| 任务 | 脚本 | 作用 |
|---|---|---|
| `2f46a6b6397e` akshare-progress-report | `akshare_progress.sh` | 数据源健康度(已暂停) |
| `db271399319e` last30days watchdog | `last30days_watchdog.py` | 早报失败告警 |
| `3f0fc75960d1` cron-failure-summary | `cron_failure_summary.py` | 当天 cron 失败汇总 |
| `528938e4ef19` quant EOD health check | `quant_eod_healthcheck.py` | 5 个产物齐不齐 |
| `bcaa89bf1a56` quant weekly eval | `quant_weekly_eval.py` | 周日 17:00 周评估 |

**这些脚本路径**:`~/.hermes/profiles/quant/scripts/`,对应 Hermes profile 的 `scripts/` 目录。

⚠️ **jobs.json 里的 `last_error` 字段可能标记 Script not found** — 说明对应的 .sh/.py 还没写或路径不对。本仓库不包含这些脚本(它们是部署方私有 cron 配套)。

---

## 6. 监控

```bash
# 看所有 cron 状态
hermes cronjob list

# 看某个的最近运行
hermes cronjob show <job_id>

# 手动跑一次
hermes cronjob run <job_id>
```

**关注信号**:
- `last_status: "error"` → 立刻排查
- `last_error: "Script not found"` → 路径或脚本缺失
- `next_run_at` 是过去 → 调度停了
- 连续 N 天 `completed` 没涨 → 任务被暂停

---

## 7. 故障排查

| 症状 | 原因 | 修法 |
|---|---|---|
| `deliver: "discord"` 但 Discord 没收到 | chat_id 过期或 bot token 失效 | `hermes cronjob update <id> --deliver local` |
| `next_run_at` 不更新 | Hermes scheduler 进程挂了 | 重启 scheduler |
| `last_error: rate limit` | LLM 调用超限 | 改用更便宜的 model,或加 retry |
| 时区错乱 | 系统不是 +08:00 | `timedatectl set-timezone Asia/Shanghai` |
| 路径找不到 | 没设 QUANT_PROJECTS | `export QUANT_PROJECTS=/Users/<user>/Projects` |
| LLM 报 "no skills loaded" | prompt 没 skills 引用 | 任务里加 `"skills": ["quant-pipeline"]` |
