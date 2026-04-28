#!/usr/bin/env python
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.common.console_io import configure_console_io
from packages.common.runtime_paths import runtime_subpath

configure_console_io()

LOG_PATH = runtime_subpath("network_access", "network_access_log.jsonl")


def main() -> int:
    if not LOG_PATH.exists():
        print(f"Log nenalezen: {LOG_PATH}")
        return 1

    rows: list[dict] = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not rows:
        print("Log je prázdný.")
        return 0

    by_outcome = Counter(str(r.get("outcome", "unknown")) for r in rows)
    by_action = Counter(f"{r.get('component')}::{r.get('action')}" for r in rows)
    by_target = Counter(str(r.get("target") or "n/a") for r in rows)
    by_reason = Counter(str(r.get("reason") or "n/a") for r in rows)
    by_component = Counter(str(r.get("component") or "unknown") for r in rows)

    print(f"Soubor: {LOG_PATH}")
    print(f"Záznamů: {len(rows)}")
    print("\nOutcome:")
    for key, val in by_outcome.most_common():
        print(f"  {key:24} {val}")

    print("\nTop akce:")
    for key, val in by_action.most_common(15):
        print(f"  {val:5}  {key}")

    print("\nTop cíle:")
    for key, val in by_target.most_common(15):
        print(f"  {val:5}  {key}")

    print("\nTop důvody:")
    for key, val in by_reason.most_common(15):
        print(f"  {val:5}  {key}")

    print("\nTop komponenty:")
    for key, val in by_component.most_common(15):
        print(f"  {val:5}  {key}")

    first_ts = rows[0].get("ts_utc")
    last_ts = rows[-1].get("ts_utc")
    print(f"\nČasový rozsah: {first_ts} -> {last_ts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
