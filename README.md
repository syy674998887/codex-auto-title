# codex-auto-title

[![Codex CLI plugin](https://img.shields.io/badge/Codex_CLI-plugin-24292f)](#quick-start) [![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/) [![License MIT](https://img.shields.io/badge/License-MIT-27855B)](LICENSE)

**English** · [简体中文](README.zh-CN.md)

A Codex CLI plugin that keeps session titles up to date as the conversation changes. By default, the session's model generates a title after its first reply and refreshes it every 10 turns.

| Turn | Example title in `codex resume` |
| ---: | --- |
| 1 | Update the notes pane |
| 11 | Fix arrow keys in the notes dropdown |

Each update uses your model's quota and adds a `TITLE: …` reply to the conversation. The plugin preserves existing names and stops updating a session when it detects a manual `/rename`.

## Quick start

Requires Python 3.9+ and Codex CLI with hooks enabled (tested on macOS with 0.171.0). Run these commands from any directory to install from GitHub.

```sh
codex plugin marketplace add https://github.com/syy674998887/codex-auto-title.git
codex plugin add codex-auto-title@codex-auto-title
```

Start a new session in your project. Before your first prompt, use `/hooks` to trust `UserPromptSubmit` and `Stop` for `codex-auto-title`. If you use `codex-auto-title-MAC`, disable it first.

Disable the plugin in `/plugins` or uninstall it with `codex plugin remove codex-auto-title@codex-auto-title`.

<details>
<summary>Manual installation with Bash</summary>

Choose one installation method per profile. For manual setup, run both scripts from the repository root and trust the same two entries in `/hooks`.

```sh
./install.sh
./enable.sh
```

Edit `plugins/codex-auto-title/config.json` to configure this installation. Use `./disable.sh` to pause it or `./uninstall.sh` to remove the hooks.

</details>

## Configuration

To override the defaults, create `config.json` in the marketplace plugin's data directory (`PLUGIN_DATA`, under `~/.codex/plugins/data/` by default).

```json
{
  "first_turn": true,
  "every": 10
}
```

- `first_turn` controls whether Codex generates a title on turn 1.
- `every` sets the interval for later titles. Use `0` for first-turn naming only, or set both `first_turn` to `false` and `every` to `0` to disable all plugin titles.

New sessions may briefly show `Codex session`. If the first AI title is deferred or its reply is malformed, the plugin uses a short preview of the first message Codex accepted. A rejected prompt never becomes the plugin's title.

## License

[MIT](LICENSE)
