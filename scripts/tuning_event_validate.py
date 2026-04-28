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
from packages.common.runtime_paths import runtime_subpath
from packages.common.tuning_event_store import get_event_stats, read_events, summarize_event_sequence

configure_console_io()


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate tuning event-store sequence integrity.")
    parser.add_argument("--job-id", required=True, help="Tuning job id.")
    parser.add_argument("--tuning-root", default=str(runtime_subpath("tuning")), help="Path to runtime/tuning.")
    parser.add_argument("--after-seq", type=int, default=0, help="Read events with seq > after-seq.")
    parser.add_argument("--limit", type=int, default=2000, help="Max events to read.")
    parser.add_argument("--expect-terminal", default="", help="Expected terminal event type.")
    parser.add_argument("--require-event", action="append", default=[], help="Required event type(s), repeat flag.")
    parser.add_argument("--min-progress", type=int, default=0, help="Minimum progress events required.")
    parser.add_argument("--min-trial-result", type=int, default=0, help="Minimum trial_result events required.")
    parser.add_argument("--json", action="store_true", help="Print JSON payload.")
    args = parser.parse_args()

    job_dir = Path(args.tuning_root) / args.job_id
    if not job_dir.exists():
        print(f"FAIL: job not found: {job_dir}")
        return 2

    events = read_events(
        job_dir,
        after_seq=max(0, int(args.after_seq)),
        limit=max(1, int(args.limit)),
    )
    stats = get_event_stats(job_dir)
    summary = summarize_event_sequence(
        events,
        expected_terminal=(args.expect_terminal.strip() or None),
    )

    issues = list(summary.get("issues") or [])
    counts = summary.get("event_type_counts") or {}
    for ev in args.require_event:
        key = str(ev).strip()
        if key and int(counts.get(key, 0)) <= 0:
            issues.append(f"missing_required_event:{key}")
    if int(counts.get("progress", 0)) < max(0, int(args.min_progress)):
        issues.append(f"progress_below_min:{counts.get('progress', 0)}<{max(0, int(args.min_progress))}")
    if int(counts.get("trial_result", 0)) < max(0, int(args.min_trial_result)):
        issues.append(f"trial_result_below_min:{counts.get('trial_result', 0)}<{max(0, int(args.min_trial_result))}")

    ok = len(issues) == 0
    payload = {
        "job_id": args.job_id,
        "after_seq": max(0, int(args.after_seq)),
        "count": len(events),
        "stats": stats,
        "summary": summary,
        "issues": issues,
        "ok": ok,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"EVENT VALIDATE job={args.job_id} ok={ok} count={len(events)} "
            f"total={stats.get('count')} max_seq={stats.get('max_seq')}"
        )
        if counts:
            print(f"event_type_counts={json.dumps(counts, ensure_ascii=False, sort_keys=True)}")
        if summary.get("terminal_event"):
            print(f"terminal={summary.get('terminal_event')}@{summary.get('terminal_seq')}")
        if issues:
            for issue in issues:
                print(f"- {issue}")
        else:
            print("PASS")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
