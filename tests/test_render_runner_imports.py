"""The render runner runs from Aman's crontab under the Obelisk's bare /usr/bin/python3: the standard library and
nothing else. Whatever it imports (render.py's docker_args, helios_jobs) must import there too. On 2026-10-28 a
top-level `from cues import write_kit` in render.py pulled in numpy, and every run died on import with the chat's
clips sitting in the queue. The queue's Telegram check (render_queue_check.py) runs there too."""

import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

# A fresh interpreter that refuses any top-level module outside the standard library or scripts/ itself.
STDLIB_ONLY = r"""
import importlib.abc, sys
scripts = sys.argv[1]
local = {p.stem for p in __import__("pathlib").Path(scripts).glob("*.py")}
class Refuse(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        top = name.partition(".")[0]
        if top in sys.stdlib_module_names or top in local:
            return None
        raise ModuleNotFoundError(f"not on the Obelisk's python3: {name}")
sys.meta_path.insert(0, Refuse())
sys.path.insert(0, scripts)
import render_runner, render_queue_check
assert callable(render_runner.docker_args)
"""


def test_the_runner_imports_with_the_standard_library_only() -> None:
    done = subprocess.run([sys.executable, "-I", "-c", STDLIB_ONLY, str(SCRIPTS)], capture_output=True, text=True,
                          check=False)
    assert done.returncode == 0, done.stderr
