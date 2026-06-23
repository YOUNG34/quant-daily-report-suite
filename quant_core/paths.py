"""统一路径常量(避免到处写硬编码路径)。

路径覆盖机制(环境变量,可选):
    QUANT_PROJECTS   - 项目根(默认 $HOME/Projects,跨机器自动适配)
    QUANT_KB_NAME    - 知识库根目录名(默认 <your-knowledge-base>,改为你自己的项目名)
    QUANT_HOME       - quant_core 安装路径(默认 $QUANT_PROJECTS/quant_core)
    QUANT_CONFIG     - 配置文件路径(默认 $QUANT_PROJECTS/quant_config.yaml)
    QLIB_DATA        - Qlib 数据路径(默认 $HOME/.hermes/profiles/<profile>/home/.qlib/qlib_data/baostock_cn)

克隆本仓库后,只需在 shell 设置 QUANT_PROJECTS 即可指向你的工作目录,
所有脚本无需修改路径。如果不设环境变量,代码会用 $HOME/Projects 自动派生
(在 macOS 通常是 /Users/<user>/Projects,在 Linux 是 /home/<user>/Projects)。

向后兼容:
    - 不设环境变量时,所有路径与原版完全一致($HOME 派生,在 macOS 通常是 /Users/<user>)
    - 原 cron 工作流(trader.py / daily_report.py 等)保持不变

示例:
    # 默认(用户原 workflow, $HOME=/Users/<user>)
    >>> paths.PAPER_TRADER
    PosixPath('$HOME/Projects/paper_trader')

    # clone 到任意位置后
    $ export QUANT_PROJECTS=/home/alice/my-projects
    >>> paths.PAPER_TRADER
    PosixPath('/home/alice/my-projects/paper_trader')

    # 不设 env var,在 Linux 上(自动用 $HOME/Projects)
    >>> paths.PAPER_TRADER
    PosixPath('/home/alice/Projects/paper_trader')
"""
from __future__ import annotations

import os
from pathlib import Path


# === 默认值(基于 $HOME 派生,跨平台自动适配)===
# 任何用户的 $HOME 下,Projects/ 都是常见工作区根
_DEFAULT_PROJECTS = str(Path.home() / "Projects")
_DEFAULT_QUANT_HOME = str(Path.home() / "Projects" / "quant_core")

# Hermes profile-aware Qlib 数据路径
# Hermes 在 macOS 上的默认 profile home = $HOME/.hermes/profiles/<profile>/home
# 用户可以通过 QLIB_DATA 环境变量覆盖
def _default_qlib_data() -> str:
    """推断 Qlib 数据根,根据 $HOME 和 Hermes profile 命名约定。"""
    hermes_root = Path.home() / ".hermes" / "profiles" / "quant" / "home"
    return str(hermes_root / ".qlib" / "qlib_data" / "baostock_cn")

_DEFAULT_QLIB_DATA = _default_qlib_data()
_DEFAULT_QUANT_CONFIG = str(Path.home() / "Projects" / "quant_config.yaml")


def _env_path(env_var: str, default: str) -> Path:
    """从环境变量读路径,读不到用默认值。"""
    return Path(os.environ.get(env_var, default))


class Paths:
    """系统关键路径。所有脚本应通过 Paths 访问,避免散落硬编码。

    使用 @property 而非 @dataclass 字段,因为:
    - 每次访问重新读环境变量 → 支持运行时切换
    - 不需要 frozen=True 仍然支持子路径派生
    """

    # ── 项目根 ──
    @property
    def PROJECTS(self) -> Path:
        return _env_path("QUANT_PROJECTS", _DEFAULT_PROJECTS)

    @property
    def QUANT_HOME(self) -> Path:
        return _env_path("QUANT_HOME", _DEFAULT_QUANT_HOME)

    # ── 数据 ──
    @property
    def QLIB_DATA(self) -> Path:
        return _env_path("QLIB_DATA", _DEFAULT_QLIB_DATA)

    @property
    def PIPELINE_OUTPUT(self) -> Path:
        return self.PROJECTS / "pipeline_output"

    # ── 纸交 ──
    @property
    def PAPER_TRADER(self) -> Path:
        return self.PROJECTS / "paper_trader"

    @property
    def PAPER_PORTFOLIO(self) -> Path:
        return self.PAPER_TRADER / "portfolio.json"

    @property
    def PAPER_HISTORY(self) -> Path:
        return self.PAPER_TRADER / "history.csv"

    @property
    def PAPER_KLINE_CACHE(self) -> Path:
        return self.PAPER_TRADER / "sh000300_kline.pkl"

    # ── ETF ──
    @property
    def ALPHA_GPT(self) -> Path:
        return self.PROJECTS / "AlphaGPT"

    @property
    def ETF_PORTFOLIO(self) -> Path:
        return self.ALPHA_GPT / "etf_portfolio.json"

    @property
    def ETF_MODEL(self) -> Path:
        return self.ALPHA_GPT / "etf_model.pt"

    @property
    def ETF_SIGNAL(self) -> Path:
        return self.ALPHA_GPT / "etf_signal.json"

    # ── 报告与日志 ──
    @property
    def KB_NAME(self) -> str:
        """知识库根目录名(可由 QUANT_KB_NAME 环境变量覆盖)"""
        return os.environ.get("QUANT_KB_NAME", "<your-knowledge-base>")

    @property
    def DAILY_REPO(self) -> Path:
        return self.PROJECTS / self.KB_NAME / "Agent" / "quant" / "dailyRepo"

    @property
    def LOGS_DIR(self) -> Path:
        return self.PROJECTS / "quant_logs"

    @property
    def SNAPSHOTS_DIR(self) -> Path:
        return self.PROJECTS / "quant_snapshots"

    # ── 配置文件 ──
    @property
    def CONFIG_FILE(self) -> Path:
        return _env_path("QUANT_CONFIG", _DEFAULT_QUANT_CONFIG)

    def ensure_dirs(self) -> None:
        """确保所有写入目录存在。"""
        for d in [self.PIPELINE_OUTPUT, self.LOGS_DIR, self.SNAPSHOTS_DIR, self.DAILY_REPO]:
            d.mkdir(parents=True, exist_ok=True)


# 单例
paths = Paths()
