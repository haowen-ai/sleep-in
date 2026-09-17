"""Fetch public JSON and store it as an artifact using only the standard library."""

import json
import os
from pathlib import Path
from urllib.request import urlopen


def main(params):
    url = params["url"]
    with urlopen(url, timeout=15) as response:
        data = json.load(response)
    destination = Path(os.environ["TASK_OUTPUT_DIR"], "response.json")
    destination.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved JSON from {url}")
