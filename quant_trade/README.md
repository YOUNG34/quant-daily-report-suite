# QuantTrade

可正式上线的量化交易系统骨架（Python），涵盖数据、回测、风控、执行、清算与监控模块，便于进一步扩展至实盘。

## 快速开始
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
make test    # 运行单元测试
python -m quanttrade.demo_pipeline  # 跑通端到端示例
python -m quanttrade.workflow       # 完整工作流（数据->因子->回测->灰度->清算）
python factor_mine.py               # 单独因子挖掘与IC报告
```

## 目录结构
- `src/quanttrade/common`：统一数据模型与配置。
- `src/quanttrade/data`：数据采集、ETL 与存储抽象。
- `src/quanttrade/backtest`：事件驱动回测引擎与绩效指标。
- `src/quanttrade/risk`：实时/批量风控检查。
- `src/quanttrade/execution`：订单路由与拆单算法。
- `src/quanttrade/clearing`：日终清算与归因。
- `src/quanttrade/ops`：监控与告警钩子。
- `tests/`：端到端与单元测试样例。

## 架构原则
- 分层解耦：策略通过统一 API 与引擎交互，不直接依赖底层实现。
- 可观测性：关键路径输出指标与日志，便于接入 Prometheus/Loki。
- 配置优先：运行参数集中在 `QuantConfig`，环境切换方便。

## 下一步可扩展
- 接入真实行情源（券商/FIX/专线）与撮合所需的延迟模型。
- 增强拆单算法（动态 POV/IS）、交易成本分析（TCA）闭环。
- 将风控规则下推到流处理框架（Flink/Aeron/CEP），支持高频。
- 用 ClickHouse/Parquet 做历史数据湖，S3/HDFS 分层存储。
- 引入策略管理与灰度发布流水线。
