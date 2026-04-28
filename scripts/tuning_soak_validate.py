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

configure_console_io()

TUNING_ROOT = runtime_subpath("tuning")


def _load_status(job_id: str) -> dict:
    path = TUNING_ROOT / job_id / "status.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing status.json for job {job_id}: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    arr = sorted(values)
    n = len(arr)
    mid = n // 2
    if n % 2 == 1:
        return arr[mid]
    return (arr[mid - 1] + arr[mid]) / 2.0


def _fmt(v: float | None, unit: str = "", digits: int = 2) -> str:
    if v is None:
        return "-"
    return f"{v:.{digits}f}{unit}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate tuning soak stability.")
    parser.add_argument("--job-id", required=True, help="Tuning job id (runtime/tuning/<job-id>/status.json).")
    parser.add_argument("--min-trials", type=int, default=30, help="Minimum completed trials for soak decision.")
    parser.add_argument("--max-error-rate", type=float, default=0.05, help="Max allowed trial error ratio.")
    parser.add_argument("--max-rss-drift-mb", type=float, default=200.0, help="Max allowed RSS drift from first to last trial.")
    parser.add_argument("--max-rss-slope-mb-per-trial", type=float, default=3.0, help="Max allowed linear RSS growth per trial.")
    args = parser.parse_args()

    status = _load_status(args.job_id)
    results = [r for r in (status.get("results") or []) if not r.get("is_repeat")]
    if not results:
        print("FAIL: no non-repeat trials.")
        return 2

    results = sorted(results, key=lambda r: int(r.get("trial_idx", 0)))
    n = len(results)
    errors = [r for r in results if r.get("error")]
    err_rate = len(errors) / n if n > 0 else 1.0

    rss_after = [float(r["worker_rss_after_mb"]) for r in results if isinstance(r.get("worker_rss_after_mb"), (int, float))]
    rss_drift = (rss_after[-1] - rss_after[0]) if len(rss_after) >= 2 else None
    rss_slope = None
    if len(rss_after) >= 2:
        x = list(range(len(rss_after)))
        x_mean = sum(x) / len(x)
        y_mean = sum(rss_after) / len(rss_after)
        denom = sum((xi - x_mean) ** 2 for xi in x)
        if denom > 0:
            rss_slope = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, rss_after)) / denom

    success_rates = [float(r["success_rate"]) for r in results if isinstance(r.get("success_rate"), (int, float))]
    rtf_values = [float(r["rtf"]) for r in results if isinstance(r.get("rtf"), (int, float))]
    latency_values = [float(r["latency_ms"]) for r in results if isinstance(r.get("latency_ms"), (int, float))]

    print(f"job_id={args.job_id}")
    print(f"status={status.get('status')}")
    print(f"trials_non_repeat={n}")
    print(f"error_trials={len(errors)}")
    print(f"error_rate={err_rate:.4f}")
    print(f"median_success_rate={_fmt(_median(success_rates), '', 4)}")
    print(f"median_rtf={_fmt(_median(rtf_values), '', 3)}")
    print(f"median_latency_ms={_fmt(_median(latency_values), 'ms', 1)}")
    print(f"rss_drift_mb={_fmt(rss_drift, 'MB', 1)}")
    print(f"rss_slope_mb_per_trial={_fmt(rss_slope, '', 3)}")

    failures: list[str] = []
    if status.get("status") != "completed":
        failures.append("job not completed")
    if n < args.min_trials:
        failures.append(f"trials_non_repeat < min_trials ({n} < {args.min_trials})")
    if err_rate > args.max_error_rate:
        failures.append(f"error_rate too high ({err_rate:.4f} > {args.max_error_rate:.4f})")
    if rss_drift is not None and rss_drift > args.max_rss_drift_mb:
        failures.append(f"rss_drift too high ({rss_drift:.1f} MB > {args.max_rss_drift_mb:.1f} MB)")
    if rss_slope is not None and rss_slope > args.max_rss_slope_mb_per_trial:
        failures.append(
            f"rss_slope too high ({rss_slope:.3f} MB/trial > {args.max_rss_slope_mb_per_trial:.3f} MB/trial)"
        )

    if failures:
        print("FAIL")
        for f in failures:
            print(f"- {f}")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
