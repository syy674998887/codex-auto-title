#!/bin/bash
# 关闭手动安装模式；插件模式通过 /plugins 停用。
set -euo pipefail
VAR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/plugins/codex-auto-title/var"
rm -f "$VAR/ENABLED"
echo "已关闭手动安装模式；插件请通过 /plugins 停用。"
echo "（手动 hook 仍在 CODEX_HOME/hooks.json；要彻底摘掉用 ./uninstall.sh）"
