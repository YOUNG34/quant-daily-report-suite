"""统一配置加载。

用法:
    from quant_core.config import get_config
    cfg = get_config()
    cfg.risk.stop_loss_pct         # 0.08
    cfg.timing.ma_window           # 60
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator


# ── 各 section 模型 ──────────────────────
class PathsConfig(BaseModel):
    data_root: str
    pipeline_output: str
    paper_trader_dir: str
    alpha_gpt_dir: str
    daily_repo: str
    logs_dir: str
    snapshots_dir: str


class PipelineConfig(BaseModel):
    model_path: str
    instruments: str = "csi300"
    fit_start: str
    fit_end: str
    feature_lookback_days: int = 180
    top_n: int = 5
    top_buffer: int = 3


class TimingConfig(BaseModel):
    enabled: bool = True
    ma_window: int = 60
    full_threshold: float = 1.00
    half_threshold: float = 0.95
    kline_cache: str = ""
    kline_start: str = "2020-01-01"

    @field_validator("full_threshold", "half_threshold")
    @classmethod
    def _check_thresholds(cls, v: float) -> float:
        if not (0.0 < v < 2.0):
            raise ValueError(f"threshold {v} out of range (0, 2)")
        return v


class RiskConfig(BaseModel):
    stop_loss_pct: float = Field(0.08, ge=0.0, le=0.5)
    min_turnover_pct: float = Field(0.30, ge=0.0, le=1.0)
    liquidity_floor: float = 1.0e8
    pe_max: float = 100.0
    pb_max: float = 15.0
    limit_up_pct_main: float = 0.10
    limit_up_pct_gem: float = 0.20
    # ── Step 9 暴露上限 ──
    single_stock_cap_pct: float = Field(0.10, ge=0.01, le=1.0)
    industry_cap_pct: float = Field(0.30, ge=0.05, le=1.0)
    # ── Step 13 ATR 动态止损 ──
    atr_period: int = Field(14, ge=5, le=60)
    atr_k: float = Field(2.5, ge=1.0, le=5.0)
    atr_fallback_pct: float = Field(0.08, ge=0.01, le=0.5)


class PaperAConfig(BaseModel):
    init_capital: float = 1_000_000.0
    weight_strategy: str = "equal"
    lot_size: int = 100
    rebalance_day_of_month: int = 1
    rebalance_hour: int = 15
    skip_holiday_to_next: bool = True
    # ── 交易成本 (Step 10 手续费 + 印花税) ──
    commission_rate: float = Field(0.00025, ge=0.0, le=0.01)  # 万 2.5
    stamp_tax_rate: float = Field(0.001, ge=0.0, le=0.01)     # 千 1，仅卖出
    min_commission: float = Field(5.0, ge=0.0)                # 最低 5 元/笔


class PaperEtfConfig(BaseModel):
    init_capital: float = 1_000_000.0
    lot_size: int = 100
    rebalance_day_of_month: int = 1
    rebalance_hour: int = 9


class ReportConfig(BaseModel):
    enabled: bool = True
    output_dir: str
    format: str = "markdown"
    include_history_rows: int = 10
    disclaimer: str = "⚠️ 以上为模拟结果，不构成投资建议"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    rotation: str = "20 MB"
    retention: str = "30 days"
    console: bool = True
    file: bool = True


class AlertsConfig(BaseModel):
    nav_daily_drop_pct: float = 0.03
    cron_missing_jobs: bool = True
    data_fetch_failure_max: int = 2
    model_oos_min_sharpe: float = 1.5


class DataSourceConfig(BaseModel):
    """数据源拉取容错配置（腾讯接口。"""
    max_retries: int = Field(3, ge=0, le=10)
    retry_base_delay_sec: float = Field(1.0, gt=0.0)
    timeout_sec: int = Field(10, ge=1, le=60)
    batch_size: int = Field(50, ge=1, le=200)
    user_agent: str = "Mozilla/5.0"
    fail_threshold: int = Field(2, ge=1)  # 连续失败 N 次标记 last_data_error


class CronScheduleConfig(BaseModel):
    pipeline_daily: str
    paper_a_nav_daily: str
    paper_etf_nav_daily: str
    daily_report: str
    rebalance_monthly: str
    etf_signal_monthly: str
    etf_backtest_monthly: str


class Config(BaseModel):
    """顶层 config 对象。"""
    paths: PathsConfig
    pipeline: PipelineConfig
    timing: TimingConfig
    risk: RiskConfig
    paper_a: PaperAConfig
    paper_etf: PaperEtfConfig
    report: ReportConfig
    logging: LoggingConfig
    alerts: AlertsConfig
    data_source: DataSourceConfig
    cron_schedule: CronScheduleConfig
    schema_version: int = 2

    def get(self, dotted_key: str, default=None):
        """点分路径读取，如 cfg.get('risk.stop_loss_pct')"""
        node: object = self
        for k in dotted_key.split("."):
            if isinstance(node, BaseModel):
                node = getattr(node, k, default)
            elif isinstance(node, dict):
                node = node.get(k, default)
            else:
                return default
        return node


# ── 加载器（带缓存） ─────────────────────
_lock = threading.Lock()
_cached: Optional[Config] = None


def _find_config_path() -> Path:
    """查找配置文件路径。

    优先级:
        1. 显式传入 path 参数
        2. 环境变量 QUANT_CONFIG
        3. 环境变量 QUANT_PROJECTS 拼 /quant_config.yaml
        4. 默认 $HOME/Projects/quant_config.yaml（原 workflow,基于 Path.home() 派生）
    """
    import os
    env = os.environ.get("QUANT_CONFIG")
    if env:
        return Path(env)
    projects = os.environ.get("QUANT_PROJECTS")
    if projects:
        return Path(projects) / "quant_config.yaml"
    return Path.home() / "Projects" / "quant_config.yaml"


def load_config(path: Path | str | None = None, force: bool = False) -> Config:
    """从 YAML 加载并校验。"""
    global _cached
    cfg_path = Path(path) if path else _find_config_path()
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"配置文件不存在: {cfg_path}\n"
            f"复制 quant_config.example.yaml 到该路径"
        )
    with open(cfg_path) as f:
        raw = yaml.safe_load(f)
    return Config(**raw)


def get_config(force: bool = False) -> Config:
    """获取单例 config。线程安全。"""
    global _cached
    with _lock:
        if _cached is None or force:
            _cached = load_config(force=force)
        return _cached


def reset_cache() -> None:
    """清缓存。用于测试或热重载。"""
    global _cached
    with _lock:
        _cached = None
