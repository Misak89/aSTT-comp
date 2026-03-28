#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TUNING_ROOT = ROOT / "runtime" / "tuning"
sys.path.insert(0, str(ROOT))

from packages.common.console_io import configure_console_io

configure_console_io()
try:
    import psutil  # noqa: F401
    PSUTIL_AVAILABLE = True
except Exception:
    PSUTIL_AVAILABLE = False


def _load_status(job_id: str) -> dict:
    status_path = TUNING_ROOT / job_id / "status.json"
    if not status_path.exists():
        raise FileNotFoundError(f"Status file not found: {status_path}")
    return json.loads(status_path.read_text(encoding="utf-8"))


def _ok_trial(trial: dict) -> bool:
    return trial.get("error") in (None, "") and trial.get("wer") is not None


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate quick tuning smoke outputs.")
    parser.add_argument("--job-id", default="", help="Tuning job id (default: runtime/tuning/_last_smoke_job.txt).")
    parser.add_argument("--allow-partial", action="store_true", help="Do not fail if job is not completed.")
    args = parser.parse_args()

    job_id = (args.job_id or "").strip()
    if not job_id:
        marker = TUNING_ROOT / "_last_smoke_job.txt"
        if not marker.exists():
            print("Missing --job-id and runtime/tuning/_last_smoke_job.txt not found.")
            return 1
        job_id = marker.read_text(encoding="utf-8").strip()

    status = _load_status(job_id)
    results = list(status.get("results") or [])
    ok_results = [r for r in results if _ok_trial(r)]

    cache_hits = 0
    missing_ram = 0
    missing_worker_rss = 0
    perceived_mismatch = 0
    perceived_checked = 0

    for r in ok_results:
        source_metrics = list(r.get("source_metrics") or [])
        cache_hits += sum(1 for sm in source_metrics if bool(sm.get("cached")))

        if r.get("ram_mb") is None or r.get("ram_peak_mb") is None:
            missing_ram += 1
        if (
            r.get("worker_rss_before_mb") is None
            or r.get("worker_rss_after_mb") is None
            or r.get("worker_rss_peak_mb") is None
        ):
            missing_worker_rss += 1

        chunk_s = r.get("chunk_seconds")
        rtf = r.get("rtf")
        perceived = r.get("perceived_delay_s")
        method = r.get("perceived_delay_method")
        quality = r.get("perceived_delay_quality")
        if isinstance(perceived, (int, float)):
            perceived_checked += 1
            expected = None
            if method == "chunk_plus_chunk_rtf_proxy" and isinstance(chunk_s, (int, float)) and isinstance(rtf, (int, float)):
                expected = round(float(chunk_s) * (1.0 + float(rtf)), 3)
            elif method == "online_latency_ms":
                vals = [sm.get("latency_ms") for sm in source_metrics if isinstance(sm.get("latency_ms"), (int, float))]
                if vals:
                    expected = round(sum(float(v) for v in vals) / len(vals) / 1000.0, 3)
            elif method == "first_word_live_ms":
                vals = [sm.get("first_word_latency_ms") for sm in source_metrics if isinstance(sm.get("first_word_latency_ms"), (int, float))]
                if vals:
                    expected = round(sum(float(v) for v in vals) / len(vals) / 1000.0, 3)
            if expected is not None and abs(float(perceived) - expected) > 0.03:
                perceived_mismatch += 1
            if method in ("chunk_plus_chunk_rtf_proxy", "elapsed_proxy") and quality != "low":
                perceived_mismatch += 1

    print(f"job_id={job_id}")
    print(f"status={status.get('status')}")
    print(f"completed_trials={status.get('completed_trials')}/{status.get('total_trials')}")
    print(f"ok_trials={len(ok_results)}")
    print(f"cache_hits={cache_hits}")
    print(f"missing_ram_trials={missing_ram}")
    print(f"missing_worker_rss_trials={missing_worker_rss}")
    print(f"perceived_delay_checked={perceived_checked}")
    print(f"perceived_delay_mismatch={perceived_mismatch}")
    print(f"psutil_available={PSUTIL_AVAILABLE}")

    if status.get("status") != "completed" and not args.allow_partial:
        print("FAIL: job is not completed (use --allow-partial).")
        return 2
    if not ok_results:
        print("FAIL: no successful trials.")
        return 3
    if cache_hits == 0:
        print("FAIL: no cache hits detected in source_metrics.")
        return 4
    if missing_ram > 0 or missing_worker_rss > 0:
        if PSUTIL_AVAILABLE:
            print("FAIL: RAM/RSS metrics missing in successful trials.")
            return 5
        print("WARN: RAM/RSS metrics missing (psutil není dostupný v tomto prostředí).")
    if perceived_checked == 0 or perceived_mismatch > 0:
        print("FAIL: perceived_delay_s validation failed.")
        return 6

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
