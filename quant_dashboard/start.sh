#!/usr/bin/env bash
# quant-dashboard starter
# 路径通过环境变量覆盖,默认走 ~/Projects/
#   QUANT_DASHBOARD_HOME   (默认: ~/Projects/quant-dashboard)
#   FINGPT_PYTHON          (默认: ~/Projects/fingpt-env/bin/python3)

set -e

: "${QUANT_DASHBOARD_HOME:=$HOME/Projects/quant-dashboard}"
: "${FINGPT_PYTHON:=$HOME/Projects/fingpt-env/bin/python3}"

cd "$QUANT_DASHBOARD_HOME" || { echo "[ERR] dir not found: $QUANT_DASHBOARD_HOME"; exit 1; }

# activate fingpt-env 如果存在
DIR_FINGPT_ENV="$(dirname "$FINGPT_PYTHON")"
if [ -f "$DIR_FINGPT_ENV/activate" ]; then
    # shellcheck disable=SC1090
    source "$DIR_FINGPT_ENV/activate"
fi

exec python3 scripts/serve.py "$@"
