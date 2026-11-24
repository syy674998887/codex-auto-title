#!/bin/bash
# 打开自动命名（只有手动安装模式需要；作为 Codex 插件装上时默认就是开的）。
set -euo pipefail
VAR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/plugins/codex-auto-title/var"
mkdir -p "$VAR"
date +%Y-%m-%dT%H:%M:%S > "$VAR/ENABLED"
echo "已开启手动模式。命名轮次由 plugins/codex-auto-title/config.json 决定。"
echo "运行 ./disable.sh 可暂停。"
