#!/bin/zsh
set -e
cd "$(dirname "$0")"
PYTHON="/Users/biantongchuangchuanmei/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3)"
fi
if [ ! -x ".venv/bin/python" ]; then
  "$PYTHON" -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python web_app.py
