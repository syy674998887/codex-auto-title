import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "plugins/codex-auto-title/hooks"
if not HOOKS.is_dir():
    HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))


def load_hook():
    spec = importlib.util.spec_from_file_location("tested_hook", HOOKS / "on-stop.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Backend:
    def __init__(self):
        self.name = None
        self.preview = ""
        self.writes = []
        self.reads = 0
        self.on_read = None
        self.on_set = None

    def server(self, *args, **kwargs):
        backend = self

        class Server:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read_name(self, sid):
                backend.reads += 1
                if backend.on_read:
                    backend.on_read()
                return backend.name

            def read_thread(self, sid):
                return {"id": sid, "name": self.read_name(sid), "preview": backend.preview}

            def set_name(self, sid, title):
                backend.name = title
                backend.writes.append(title)
                if backend.on_set:
                    backend.on_set()
        return Server()
