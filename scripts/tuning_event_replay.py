#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.common.console_io import configure_console_io
from packages.common.tuning_event_store import get_event_stats, read_events

configure_console_io()


def _safe_console_text(text: str) -> str:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        return text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    except Exception:
        return text


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay tuning events from SQLite event-store.")
    parser.add_argument("--job-id", required=True, help="Tuning job id.")
    parser.add_argument("--tuning-root", default=str(ROOT / "runtime" / "tuning"), help="Path to runtime/tuning.")
    parser.add_argument("--after-seq", type=int, default=0, help="Read events with seq > after-seq.")
    parser.add_argument("--limit", type=int, default=200, help="Max events to print.")
    parser.add_argument("--event-type", default="", help="Optional event_type filter.")
    parser.add_argument("--json", action="store_true", help="Print JSON payload.")
    args = parser.parse_args()

    job_dir = Path(args.tuning_root) / args.job_id
    if not job_dir.exists():
        print(f"FAIL: job not found: {job_dir}")
        return 2

    stats = get_event_stats(job_dir)
    events = read_events(
        job_dir,
        after_seq=max(0, int(args.after_seq)),
        limit=max(1, int(args.limit)),
        event_type=(args.event_type.strip() or None),
    )

    if args.json:
        raw = json.dumps(
            {
                "job_id": args.job_id,
                "after_seq": max(0, int(args.after_seq)),
                "count": len(events),
                "stats": stats,
                "events": events,
            },
            ensure_ascii=False,
            indent=2,
        )
        print(_safe_console_text(raw))
        return 0

    print(_safe_console_text(
        f"EVENT REPLAY job={args.job_id} count={len(events)} "
        f"db_exists={stats.get('exists')} total={stats.get('count')} max_seq={stats.get('max_seq')}"
    ))
    if stats.get("event_types"):
        print(
            _safe_console_text(
                f"event_types={json.dumps(stats.get('event_types'), ensure_ascii=False, sort_keys=True)}"
            )
        )
    for e in events:
        line = (
            f"#{e.get('seq')} {e.get('ts_utc')} {e.get('event_type')} "
            f"{json.dumps(e.get('payload') or {}, ensure_ascii=False, sort_keys=True)}"
        )
        print(_safe_console_text(line))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
