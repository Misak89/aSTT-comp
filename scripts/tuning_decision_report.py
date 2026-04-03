#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services.tuning_decision import get_job_decision_report
from packages.common.console_io import configure_console_io

configure_console_io()


def _fmt(v: float | None, scale: float = 1.0, suffix: str = "", digits: int = 2) -> str:
    if v is None:
        return "-"
    return f"{v * scale:.{digits}f}{suffix}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Final recommendation from one completed tuning job.")
    parser.add_argument("--job-id", required=True, help="Tuning job id.")
    parser.add_argument("--min-success-rate", type=float, default=0.95, help="Minimum success_rate for strict filter.")
    parser.add_argument("--max-rtf", type=float, default=1.0, help="Maximum RTF for strict live-mic filter.")
    parser.add_argument("--allow-proxy", action="store_true", help="Allow proxy-only latency in strict scoring penalties.")
    parser.add_argument("--require-repro-n", type=int, default=3, help="Require at least N runs_ok for strict pool.")
    parser.add_argument("--top", type=int, default=5, help="How many top rows to print.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args()

    report = get_job_decision_report(
        args.job_id,
        min_success_rate=float(args.min_success_rate),
        max_rtf=float(args.max_rtf),
        allow_proxy=bool(args.allow_proxy),
        require_repro_n=max(1, int(args.require_repro_n)),
        top=max(1, int(args.top)),
    )
    if report is None:
        print(f"FAIL: tuning job not found: {args.job_id}")
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if report.get("error"):
        print(f"FAIL: {report['error']}")
        return 3

    best = report.get("best") or {}
    top_rows = list(report.get("top") or [])
    rv = report.get("repro_validation") or {}
    lane_counts = report.get("lane_counts") or {}

    print(f"DECISION REPORT job={args.job_id}")
    print(
        f"status={report.get('status')} hardware_profile={report.get('hardware_profile') or 'n/a'} "
        f"cap={report.get('constraints_profile') or 'none'} "
        f"(cores {report.get('constraints_cpu_cores') if report.get('constraints_cpu_cores') is not None else '-'} "
        f"RAM {report.get('constraints_ram_limit_mb') if report.get('constraints_ram_limit_mb') is not None else '-'}MB "
        f"prio {report.get('constraints_priority') or '-'}) "
        f"load={report.get('load_profile') or 'none'} "
        f"(CPU {report.get('load_cpu_target_pct') if report.get('load_cpu_target_pct') is not None else '-'}% "
        f"RAM {report.get('load_ram_target_pct') if report.get('load_ram_target_pct') is not None else '-'}%) "
        f"pool={report.get('selected_pool')} lane={report.get('selected_lane')} repro_n>={report.get('require_repro_n')}"
    )
    if lane_counts:
        print(f"lane_counts={json.dumps(lane_counts, ensure_ascii=False, sort_keys=True)}")
    if rv:
        print(
            f"repro_validation: required_n={rv.get('required_n')} checked_top_k={rv.get('checked_top_k')} "
            f"passed={rv.get('passed')} missing={rv.get('missing_trial_idxs') or []}"
        )

    print(
        f"BEST trial #{best.get('trial_idx')} model={best.get('model_id')} "
        f"WER={_fmt(best.get('wer'), 100, '%')} WERsoft={_fmt(best.get('wer_soft'), 100, '%')} "
        f"RTF={_fmt(best.get('rtf'), 1, '', 3)} delay={_fmt(best.get('perceived_delay_s'), 1, 's', 2)} "
        f"success={_fmt(best.get('success_rate'), 100, '%', 1)} "
        f"lane={best.get('latency_lane') or 'n/a'} lat_q={best.get('latency_quality') or 'n/a'} "
        f"repro_n={best.get('repro_runs_ok')} ci95_w={_fmt(best.get('repro_wer_ci_width'), 100, '%', 2)} "
        f"score={float(best.get('score') or 0.0):.4f}"
    )
    print(f"params={json.dumps(best.get('params') or {}, ensure_ascii=False, sort_keys=True)}")
    print("")
    print(f"TOP {max(1, int(args.top))}:")
    for i, c in enumerate(top_rows[: max(1, int(args.top))], start=1):
        print(
            f"{i:02d}. trial={c.get('trial_idx')} WER={_fmt(c.get('wer'), 100, '%')} "
            f"RTF={_fmt(c.get('rtf'), 1, '', 3)} delay={_fmt(c.get('perceived_delay_s'), 1, 's', 2)} "
            f"succ={_fmt(c.get('success_rate'), 100, '%', 1)} "
            f"lane={c.get('latency_lane') or 'n/a'} lat_q={c.get('latency_quality') or 'n/a'} "
            f"repro_n={c.get('repro_runs_ok')} score={float(c.get('score') or 0.0):.4f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
