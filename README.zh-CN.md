# codex-auto-title

[![Codex CLI plugin](https://img.shields.io/badge/Codex_CLI-plugin-24292f)](#快速开始) [![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/) [![License MIT](https://img.shields.io/badge/License-MIT-27855B)](LICENSE)

[English](README.md) · **简体中文**

让 Codex CLI 的会话标题跟着对话内容更新。默认由当前会话的模型在第 1 轮答完后起标题，此后每 10 轮更新一次。

| 轮次 | `codex resume` 中的标题示例 |
| ---: | --- |
| 1 | 调整笔记面板 |
| 11 | 修复笔记下拉菜单的方向键 |

每次生成标题都会在对话里多出一行 `TITLE: …`，并消耗当前模型的额度。插件会保留会话已有的名称，检测到你用 `/rename` 手动改名后，就停止更新这条会话。

## 快速开始

需要 Python 3.9+ 和已启用 hooks 的 Codex CLI（已在 macOS 上用 0.171.0 验证）。在任意目录运行以下命令即可从 GitHub 安装。

```sh
codex plugin marketplace add https://github.com/syy674998887/codex-auto-title.git
codex plugin add codex-auto-title@codex-auto-title
```

回到自己的项目，新开一条 Codex 会话。发送第一条消息前，在 `/hooks` 中信任 `codex-auto-title` 的 `UserPromptSubmit` 和 `Stop` 两项。如果正在使用 `codex-auto-title-MAC`，先将它停用。

要暂停插件，在 `/plugins` 里停用。要卸载，运行 `codex plugin remove codex-auto-title@codex-auto-title`。

<details>
<summary>用 Bash 手动安装</summary>

同一套 Codex 配置只选一种安装方式。手动安装时，在仓库根目录运行这两个脚本，再到 `/hooks` 信任上述两项。

```sh
./install.sh
./enable.sh
```

手动安装的配置文件是 `plugins/codex-auto-title/config.json`。运行 `./disable.sh` 可暂停，运行 `./uninstall.sh` 可移除注册的 hooks。

</details>

## 配置

通过 marketplace 安装时，可在插件数据目录（`PLUGIN_DATA`，默认位于 `~/.codex/plugins/data/` 下）创建 `config.json`，覆盖以下默认值。

```json
{
  "first_turn": true,
  "every": 10
}
```

- `first_turn` 控制是否在第 1 轮生成 AI 标题。
- `every` 控制后续更新间隔。设为 `0` 可只保留首轮命名。如果同时将 `first_turn` 设为 `false`，插件就不再命名。

新会话可能短暂显示 `Codex session`。如果跳过首轮 AI 命名，或首轮标题回复格式有误，插件会用 Codex 已接受的首条消息生成简短预览。被拒绝的输入不会成为插件写入的标题。

## 许可证

[MIT](LICENSE)
