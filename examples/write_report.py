"""Write a small text artifact."""

import os
from pathlib import Path


def main(params):
    lines = params.get("lines", ["Task Console report"])
    destination = Path(os.environ["TASK_OUTPUT_DIR"], "report.txt")
    destination.write_text("\n".join(str(line) for line in lines) + "\n", encoding="utf-8")
    print(f"Wrote {destination.name}")
