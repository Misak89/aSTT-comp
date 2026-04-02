from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ts_to_utc_iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def build_signature(history_dir: Path, stats_file: Path) -> tuple[str, int, str | None]:
    rows: list[str] = []
    latest_mtime: float | None = None
    history_files = sorted(history_dir.glob("*.md")) if history_dir.exists() else []
    for p in history_files:
        st = p.stat()
        rows.append(f"{p.name}|{st.st_size}|{st.st_mtime_ns}")
        latest_mtime = max(latest_mtime or st.st_mtime, st.st_mtime)

    if stats_file.exists():
        st = stats_file.stat()
        rows.append(f"{stats_file.name}|{st.st_size}|{st.st_mtime_ns}")
        latest_mtime = max(latest_mtime or st.st_mtime, st.st_mtime)
    else:
        rows.append("stats_file_missing")

    digest = hashlib.sha1("\n".join(rows).encode("utf-8")).hexdigest()
    return digest, len(history_files), ts_to_utc_iso(latest_mtime)


def run_learning(script_path: Path, history_dir: Path, stats_file: Path) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        str(script_path),
        "--history-dir",
        str(history_dir),
        "--stats-file",
        str(stats_file),
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Continuously run SpecStory failure-learning when source data changes."
    )
    parser.add_argument("--history-dir", default=".specstory/history")
    parser.add_argument("--stats-file", default=".specstory/statistics.json")
    parser.add_argument("--script", default="scripts/specstory_failure_learning.py")
    parser.add_argument("--status-file", default="runtime/specstory_live_status.json")
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--run-once", action="store_true")
    args = parser.parse_args()

    history_dir = Path(args.history_dir)
    stats_file = Path(args.stats_file)
    script_path = Path(args.script)
    status_file = Path(args.status_file)

    state: dict[str, Any] = {
        "generated_at_utc": utc_now_iso(),
        "loop_started_utc": utc_now_iso(),
        "loop_pid": os.getpid(),
        "interval_seconds": int(args.interval_seconds),
        "last_input_signature": None,
        "history_files_count": 0,
        "history_latest_mtime_utc": None,
        "last_run_utc": None,
        "last_success_utc": None,
        "last_exit_code": None,
        "last_error": None,
        "last_stdout_tail": "",
        "last_stderr_tail": "",
        "runs_total": 0,
        "runs_failed": 0,
    }

    while True:
        signature, count, latest_mtime = build_signature(history_dir, stats_file)
        state["history_files_count"] = count
        state["history_latest_mtime_utc"] = latest_mtime

        should_run = signature != state.get("last_input_signature")
        state["generated_at_utc"] = utc_now_iso()

        if should_run:
            state["last_run_utc"] = utc_now_iso()
            proc = run_learning(script_path=script_path, history_dir=history_dir, stats_file=stats_file)
            state["last_input_signature"] = signature
            state["last_exit_code"] = proc.returncode
            state["last_stdout_tail"] = (proc.stdout or "")[-2000:]
            state["last_stderr_tail"] = (proc.stderr or "")[-2000:]
            state["runs_total"] = int(state.get("runs_total") or 0) + 1
            if proc.returncode == 0:
                state["last_success_utc"] = utc_now_iso()
                state["last_error"] = None
            else:
                state["runs_failed"] = int(state.get("runs_failed") or 0) + 1
                state["last_error"] = f"specstory_failure_learning exit={proc.returncode}"

        write_json_atomic(status_file, state)

        if args.run_once:
            break
        time.sleep(max(5, int(args.interval_seconds)))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
