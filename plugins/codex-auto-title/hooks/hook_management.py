"""手动安装管理。只修改本工具的 handler，不启动 Codex。"""

import argparse
import copy
import json
import os
import shlex
import stat
import sys
import tempfile
from pathlib import Path

from title_runtime import atomic_json, codex_home, file_lock

PRODUCT_EVENTS = {"codex-auto-title": ("Stop", "UserPromptSubmit"), "codex-auto-title-MAC": ("Stop",)}
PRODUCTS = tuple(PRODUCT_EVENTS)


def identify(handler):
    if not isinstance(handler, dict):
        return None
    status = handler.get("statusMessage", handler.get("status_message"))
    if status in PRODUCTS:
        return status
    command = handler.get("command") or ""
    if not isinstance(command, str):
        return None
    # 兼容旧注册；完整路径段不会把双生项目互相误认。
    normalized = command.replace("\\", "/")
    for product in PRODUCTS:
        if "/" + product + "/hooks/on-stop.py" in normalized:
            return product
    return None


def validate_document(doc, event="Stop"):
    if not isinstance(doc, dict):
        raise ValueError("hooks.json 顶层必须是对象")
    hooks = doc.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks 必须是对象")
    groups = hooks.get(event, [])
    if not isinstance(groups, list):
        raise ValueError(f"{event} 必须是数组")
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks", []), list):
            raise ValueError(f"{event} group 格式无效")
        if any(not isinstance(h, dict) for h in group.get("hooks", [])):
            raise ValueError("hook handler 格式无效")
    return groups


def update_event(doc, event, product, replacement):
    groups = validate_document(doc, event)
    replaced = False
    for group in groups:
        updated = []
        for handler in group.get("hooks", []):
            if identify(handler) == product:
                if replacement is not None and not replaced:
                    updated.append(copy.deepcopy(replacement))
                    replaced = True
            else:
                updated.append(handler)
        if "hooks" in group:
            group["hooks"] = updated
    if replacement is not None and not replaced:
        doc.setdefault("hooks", {}).setdefault(event, []).append(
            {"hooks": [copy.deepcopy(replacement)]})
    # 保留空组，避免把其他组的索引移动。Codex 会忽略无 handler 的组。


def update_document(doc, product, replacement=None):
    """分别更新各事件中的本工具注册，整份文档一次保存。"""
    result = copy.deepcopy(doc)
    for event in PRODUCT_EVENTS[product]:
        handler = replacement
        if event == "UserPromptSubmit" and handler is not None:
            handler = dict(handler, timeout=1)
        update_event(result, event, product, handler)
    return result


def install_preflight(doc, product):
    for event in PRODUCT_EVENTS[product]:
        for group in validate_document(doc, event):
            for handler in group.get("hooks", []):
                owner = identify(handler)
                if owner and owner != product:
                    raise ValueError("另一个双生项目已注册，请先卸载")


def manage(action, product, base):
    base = Path(base).resolve()
    requested_path = codex_home() / "hooks.json"
    if action == "uninstall" and not requested_path.exists():
        print("没有手动 hook 注册文件，无需卸载。插件请使用 codex plugin remove。")
        return
    # 保留 dotfiles 常用的符号链接，原子替换实际目标文件。
    path = requested_path.resolve()
    with file_lock(path.with_name("auto-title-install.lock"), timeout=2):
        try:
            original = path.read_bytes()
            doc = json.loads(original)
        except FileNotFoundError:
            if action == "uninstall":
                print("没有手动 hook 注册文件，无需卸载。插件请使用 codex plugin remove。")
                return
            original, doc = None, {}
        replacement = None
        if action == "install":
            script = base / "hooks/on-stop.py"
            if not script.is_file():
                raise ValueError("找不到 on-stop.py")
            if product == "codex-auto-title-MAC" and not os.access(base / "tools/fm-title", os.X_OK):
                raise ValueError("fm-title 未编译或不可执行，请先运行 ./build.sh")
            install_preflight(doc, product)
            replacement = {
                "type": "command",
                "command": shlex.join([sys.executable, "-B", str(script)]),
                "timeout": 15,
                "statusMessage": product,
            }
        updated = update_document(doc, product, replacement)
        if updated == doc:
            print("注册没有变化。插件卸载请使用 codex plugin remove。")
            return
        # 不覆盖不遵守本工具锁的外部编辑；发现变化就让用户协调。
        try:
            current = path.read_bytes()
        except FileNotFoundError:
            current = None
        if current != original or requested_path.resolve() != path:
            raise ValueError("hooks.json 被其他进程修改，已取消；请核对后重试")
        mode = stat.S_IMODE(path.stat().st_mode) if original is not None else 0o600
        if original is not None:
            fd, backup = tempfile.mkstemp(prefix="hooks.json.bak-", dir=path.parent)
            with os.fdopen(fd, "wb") as fh:
                fh.write(original)
            os.chmod(backup, mode)
            print("已备份: " + backup)
        atomic_json(path, updated)
        os.chmod(path, mode)
    print("手动 hook 已%s：%s" % ("注册" if action == "install" else "移除", path))
    if action == "install":
        print("下一步：在 Codex /hooks 信任当前定义，然后运行 ./enable.sh。")
        print("请在 /hooks 和 /plugins 确认：自动命名只启用一个项目、一种安装方式。")
    else:
        print("运行状态和备份保留；已启动的写回可能完成。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--product", required=True, choices=PRODUCTS)
    parser.add_argument("--base", required=True)
    args = parser.parse_args()
    try:
        manage(args.action, args.product, args.base)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
