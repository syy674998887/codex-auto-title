#!/bin/bash
# 手动安装模式；插件模式通过 Codex /plugins 管理。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/plugins/codex-auto-title"
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null || { echo "需要 Python 3.9+" >&2; exit 1; }
exec "$PYTHON" -B "$ROOT/hooks/hook_management.py" uninstall --product codex-auto-title --base "$ROOT"
