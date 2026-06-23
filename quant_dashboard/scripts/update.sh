#!/usr/bin/env bash
# Update quant-dashboard indicators + scan
# 环境变量:
#   QUANT_DASHBOARD_HOME (默认: ~/Projects/quant-dashboard)
#   FINGPT_PYTHON        (默认: ~/Projects/fingpt-env/bin/python3)

set -e

: "${QUANT_DASHBOARD_HOME:=$HOME/Projects/quant-dashboard}"
: "${FINGPT_PYTHON:=$HOME/Projects/fingpt-env/bin/python3}"

cd "$QUANT_DASHBOARD_HOME"

DIR_FINGPT_ENV="$(dirname "$FINGPT_PYTHON")"
if [ -f "$DIR_FINGPT_ENV/activate" ]; then
    # shellcheck disable=SC1090
    source "$DIR_FINGPT_ENV/activate"
fi

exec python3 scripts/fetch_indicators.py "$@"
