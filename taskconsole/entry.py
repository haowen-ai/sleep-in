"""Small subprocess entrypoint for taskconsole.runtime."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import runpy
import sys
from pathlib import Path


def run(path: str, mode: str) -> None:
    namespace = runpy.run_path(path, run_name="__taskconsole_task__" if mode == "function" else "__main__")
    if mode == "function":
        function = namespace.get("main")
        if not callable(function):
            raise RuntimeError("main is not callable")
        params = json.loads(Path(os.environ["TASK_PARAMS_FILE"]).read_text(encoding="utf-8"))
        result = function(params)
        if inspect.isawaitable(result):
            asyncio.run(result)


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2])
