#!/usr/bin/env python
"""Smoke test: ověří zda backend odpovídá na /api/health."""
from __future__ import annotations
import sys
import urllib.request
import urllib.error

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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
