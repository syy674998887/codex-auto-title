#!/bin/bash
# 零依赖验证入口；不安装工具，不连接真实 Codex 会话。
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null || { echo "需要 Python 3" >&2; exit 1; }
exec "$PYTHON" -B -m unittest discover -s tests -v
