"""统一日志：基于 loguru。所有脚本应使用本 logger。

用法:
    from quant_core.logger import get_logger
    log = get_logger(__name__)
    log.info("hello")
"""
from __future__ import annotations

import sys
from pathlib import Path
from loguru import logger as _loguru

# 默认配置
_LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <7}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)
_LOG_FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | "
    "{name}:{function}:{line} - {message}"
)

_initialized = False


def _init_logging(log_dir: Path | None = None, level: str = "INFO") -> None:
    """初始化 loguru handler。只初始化一次。"""
    global _initialized
    if _initialized:
        return

    # 移除默认 handler
    _loguru.remove()

    # 控制台（带颜色）
    _loguru.add(
        sys.stderr,
        format=_LOG_FORMAT,
        level=level,
        colorize=True,
        backtrace=False,
        diagnose=False,
    )

    # 文件
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        _loguru.add(
            log_dir / "quant_{time:YYYY-MM-DD}.log",
            format=_LOG_FILE_FORMAT,
            level=level,
            rotation="20 MB",
            retention="30 days",
            encoding="utf-8",
            enqueue=True,  # 进程安全
        )

    _initialized = True


def get_logger(name: str):
    """获取 logger 实例。

    第一次调用时会自动初始化（用 $HOME/Projects/quant_logs 派生,跨机器自动适配）。
    也可调用 configure_logging() 提前配置。
    """
    if not _initialized:
        # 懒加载默认配置
        try:
            from quant_core.config import get_config
            cfg = get_config()
            _init_logging(
                log_dir=Path(cfg.paths.logs_dir) if cfg.paths.logs_dir else None,
                level=cfg.logging.level,
            )
        except Exception:
            # 配置加载失败 → 用最简配置
            _init_logging(level="INFO")

    return _loguru.bind(module=name)


def configure_logging(log_dir: Path | None = None, level: str = "INFO") -> None:
    """提前初始化日志。在 main() 入口调用一次。"""
    _init_logging(log_dir=log_dir, level=level)
