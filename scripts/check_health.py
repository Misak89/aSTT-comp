#!/usr/bin/env python
"""Smoke test: ověří zda backend odpovídá na /api/health."""
from __future__ import annotations
from pathlib import Path
import sys
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.common.console_io import configure_console_io

configure_console_io()

URL = "http://127.0.0.1:8012/api/health"


def main() -> int:
    try:
        with urllib.request.urlopen(URL, timeout=5) as resp:
            body = resp.read().decode()
            print(f"OK  {URL} -> {resp.status} {body[:120]}")
            return 0
    except urllib.error.URLError as exc:
        print(f"FAIL  {URL} → {exc}")
        print("Spusť backend: .venv\\Scripts\\python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8012 --reload")
        return 1


if __name__ == "__main__":
    sys.exit(main())
