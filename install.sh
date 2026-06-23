#!/usr/bin/env bash
# install.sh — 一键安装 A股量化日报自动化套件
#
# 用法:
#   bash install.sh         # 完整安装(创建 venv + 装依赖 + 配置检查)
#   bash install.sh --skip-deps   # 跳过 pip install,只创建 venv
#   bash install.sh --help
#
# 设计原则:
#   - 无 sudo / 无 brew / 无 docker,纯 venv + pip
#   - macOS 优先(Linux 也兼容)
#   - set -e 失败即退出,不静默吞错
#   - 所有输出用中文 + emoji 提示,人类可读

set -euo pipefail

# ── 颜色输出 ────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLUE}ℹ️  $*${NC}"; }
ok()    { echo -e "${GREEN}✅ $*${NC}"; }
warn()  { echo -e "${YELLOW}⚠️  $*${NC}"; }
fail()  { echo -e "${RED}❌ $*${NC}"; exit 1; }

# ── 参数解析 ────────────────────────────────────────
SKIP_DEPS=0
for arg in "$@"; do
  case "$arg" in
    --skip-deps) SKIP_DEPS=1 ;;
    --help|-h)
      echo "用法: bash install.sh [选项]"
      echo "  --skip-deps   跳过 pip install(只创建 venv)"
      echo "  --help, -h    显示本帮助"
      exit 0 ;;
    *) fail "未知参数: $arg  (用 --help 看用法)" ;;
  esac
done

# ── 前置检查 ────────────────────────────────────────
info "检查 Python 版本..."
PYTHON_BIN=""
for cmd in python3 python; do
  if command -v "$cmd" >/dev/null 2>&1; then
    version=$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "")
    major=$(echo "$version" | cut -d. -f1)
    minor=$(echo "$version" | cut -d. -f2)
    # 最低 3.9 — 代码用 from __future__ import annotations,
    # 3.9 + venv 内 pandas/matplotlib 现代版本都跑得动。
    if [ "${major:-0}" -ge 3 ] && [ "${minor:-0}" -ge 9 ]; then
      PYTHON_BIN="$cmd"
      ok "Python $version ($PYTHON_BIN)"
      break
    fi
  fi
done
[ -z "$PYTHON_BIN" ] && fail "找不到 Python 3.9+,请先装 Python(brew install python)"

info "检查 curl..."
command -v curl >/dev/null 2>&1 || fail "找不到 curl,macOS 自带,如缺失请装: brew install curl"
ok "curl $(curl --version | head -1 | awk '{print $2}')"

# ── 路径定位 ────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
info "工作目录: $SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/.venv"
REQUIREMENTS="$SCRIPT_DIR/requirements.txt"

# ── 创建虚拟环境 ────────────────────────────────────
if [ -d "$VENV_DIR" ]; then
  warn ".venv 已存在,复用"
else
  info "创建虚拟环境 .venv..."
  "$PYTHON_BIN" -m venv "$VENV_DIR" || fail "venv 创建失败"
  ok "虚拟环境创建完成"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
info "Python in venv: $(python3 --version)"
info "pip: $(python3 -m pip --version | awk '{print $2}')"

# ── 升级 pip ────────────────────────────────────────
info "升级 pip..."
python3 -m pip install --upgrade pip --quiet 2>&1 | tail -5 || warn "pip 升级失败,继续"

# ── 装依赖 ──────────────────────────────────────────
if [ "$SKIP_DEPS" -eq 1 ]; then
  warn "--skip-deps 跳过 pip install"
else
  if [ ! -f "$REQUIREMENTS" ]; then
    fail "找不到 requirements.txt: $REQUIREMENTS"
  fi
  info "安装 requirements.txt (这可能要 2-5 分钟)..."
  python3 -m pip install -r "$REQUIREMENTS" --quiet || fail "依赖安装失败,看上方错误"
  ok "依赖安装完成"
fi

# ── 配置检查 ────────────────────────────────────────
info "配置检查..."

# 1. quant_config.yaml 存在?
CONFIG_FILE="$SCRIPT_DIR/quant_config.yaml"
if [ ! -f "$CONFIG_FILE" ]; then
  fail "找不到 quant_config.yaml,请从仓库恢复"
fi
ok "quant_config.yaml 存在"

# 2. .env 是否需要创建?
ENV_FILE="$SCRIPT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
  ok ".env 已存在(跳过)"
else
  if [ -f "$SCRIPT_DIR/.env.example" ]; then
    cp "$SCRIPT_DIR/.env.example" "$ENV_FILE"
    warn ".env 已从 .env.example 复制,请编辑填入真实 API key"
  else
    warn ".env 不存在,跳过(部分功能可能受限)"
  fi
fi

# 3. portfolio.json 是否需要初始化?
PORTFOLIO="$SCRIPT_DIR/paper_trader/portfolio.json"
if [ -f "$PORTFOLIO" ]; then
  ok "portfolio.json 已存在"
else
  if [ -f "$SCRIPT_DIR/paper_trader/portfolio.json.template" ]; then
    cp "$SCRIPT_DIR/paper_trader/portfolio.json.template" "$PORTFOLIO"
    warn "portfolio.json 已从 template 初始化"
  else
    warn "portfolio.json 不存在,模板未找到"
  fi
fi

# ── 健全性测试 ──────────────────────────────────────
if [ "$SKIP_DEPS" -eq 1 ]; then
  warn "--skip-deps 跳过健全性测试"
else
  info "运行健全性测试..."
  TEST_RESULT=$(python3 -c "
import sys
errs = []
for mod in ['pandas', 'numpy', 'matplotlib', 'yaml', 'pydantic', 'httpx']:
    try:
        __import__(mod)
    except ImportError as e:
        errs.append(f'{mod}: {e}')
print('OK' if not errs else 'FAIL:' + '; '.join(errs))
" 2>&1)
  if echo "$TEST_RESULT" | grep -q "^OK$"; then
    ok "所有核心库 import 成功"
  else
    fail "健全性测试失败: $TEST_RESULT"
  fi

  # ── 自检 paths.py ───────────────────────────────────
  info "自检 paths.py..."
  TEST_PATHS=$(python3 -c "
import os
os.environ['QUANT_KB_NAME'] = 'TestKB'
os.environ['QUANT_PROJECTS'] = '/tmp/test-projects'
from quant_core.paths import Paths
p = Paths()
print(f'OK DAILY_REPO={p.DAILY_REPO} KB_NAME={p.KB_NAME}')
" 2>&1) || fail "paths.py 自检失败: $TEST_PATHS"
  ok "paths.py 自检: $TEST_PATHS"
fi

# ── 收尾 ────────────────────────────────────────────
echo ""
ok "🎉 安装完成!"
echo ""
echo "后续步骤:"
echo "  1. 编辑 .env,填入真实 API key(Discord webhook + LLM API key)"
echo "  2. 编辑 quant_config.yaml,确认 KB 路径"
echo "  3. 编辑 paper_trader/portfolio.json,初始化模拟持仓"
echo "  4. 测试跑一次:"
echo "     source .venv/bin/activate"
echo "     python3 paper_trader/daily_report.py --dry-run"
echo "  5. 设置 cron 任务:参考 docs/CRON_SETUP.md"
echo ""
echo "📖 完整文档:docs/FIRST_RUN.md"
