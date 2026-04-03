#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    rank = (len(sorted_vals) - 1) * (p / 100.0)
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return float(sorted_vals[low])
    frac = rank - low
    return float(sorted_vals[low] * (1.0 - frac) + sorted_vals[high] * frac)


def _fetch_json(url: str, timeout_s: float) -> dict[str, Any]:
    req = urllib.request.Request(url=url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _sample_mode(base_url: str, mode: str, samples: int, interval_ms: int, timeout_s: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    query = urllib.parse.urlencode({"mode": mode})
    url = f"{base_url}/api/health/processes?{query}"
    for i in range(1, samples + 1):
        started = time.perf_counter()
        err = None
        payload: dict[str, Any] | None = None
        try:
            payload = _fetch_json(url, timeout_s=timeout_s)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
            err = str(e)
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        out.append(
            {
                "mode": mode,
                "sample_no": i,
                "elapsed_ms": round(elapsed_ms, 1),
                "ok": err is None,
                "error": err,
                "scan_phase": payload.get("scan_phase") if payload else None,
                "status": payload.get("status") if payload else None,
                "count": payload.get("count") if payload else None,
                "zombie_count": payload.get("zombie_count") if payload else None,
                "warnings_count": len(payload.get("warnings") or []) if payload else None,
            }
        )
        if i < samples:
            time.sleep(max(0.0, interval_ms / 1000.0))
    return out


def _mode_summary(rows: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    mode_rows = [r for r in rows if r["mode"] == mode]
    ok_rows = [r for r in mode_rows if r.get("ok")]
    elapsed = [float(r["elapsed_ms"]) for r in ok_rows]
    counts = [float(r["count"]) for r in ok_rows if isinstance(r.get("count"), (int, float))]
    zombies = [float(r["zombie_count"]) for r in ok_rows if isinstance(r.get("zombie_count"), (int, float))]
    return {
        "mode": mode,
        "samples_total": len(mode_rows),
        "samples_ok": len(ok_rows),
        "samples_error": len(mode_rows) - len(ok_rows),
        "elapsed_ms_mean": round(statistics.fmean(elapsed), 1) if elapsed else None,
        "elapsed_ms_p50": round(_percentile(elapsed, 50), 1) if elapsed else None,
        "elapsed_ms_p95": round(_percentile(elapsed, 95), 1) if elapsed else None,
        "mean_process_count": round(statistics.fmean(counts), 2) if counts else None,
        "mean_zombie_count": round(statistics.fmean(zombies), 2) if zombies else None,
    }


def _render_md(report: dict[str, Any]) -> str:
    captured = report["captured_at_utc"]
    base_url = report["base_url"]
    modes = report["modes"]
    summary = report["summary"]
    return "\n".join(
        [
            "# Monitoring Baseline Report - 2026-04-03",
            "",
            "Doc-Meta:",
            "- owner: engineering",
            "- status: active",
            "- doc_file: monitoring_baseline_2026-04-03.md",
            f"- last_updated_utc: {captured}",
            "- review_due_utc: 2026-04-15T00:00:00Z",
            "",
            "## Scope",
            f"- endpoint: `{base_url}/api/health/processes`",
            f"- modes: `{', '.join(modes)}`",
            f"- samples per mode: `{report['samples_per_mode']}`",
            f"- interval_ms: `{report['interval_ms']}`",
            "",
            "## Summary",
            "| mode | ok/total | mean ms | p50 ms | p95 ms | mean process count | mean zombie count |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        + [
            (
                f"| {s['mode']} | {s['samples_ok']}/{s['samples_total']} | "
                f"{s['elapsed_ms_mean'] if s['elapsed_ms_mean'] is not None else '-'} | "
                f"{s['elapsed_ms_p50'] if s['elapsed_ms_p50'] is not None else '-'} | "
                f"{s['elapsed_ms_p95'] if s['elapsed_ms_p95'] is not None else '-'} | "
                f"{s['mean_process_count'] if s['mean_process_count'] is not None else '-'} | "
                f"{s['mean_zombie_count'] if s['mean_zombie_count'] is not None else '-'} |"
            )
            for s in summary
        ]
        + [
            "",
            "## Notes",
            "- This report is baseline telemetry for post-audit v5 tracking.",
            "- Raw per-sample rows are in JSON and JSONL companions.",
        ]
    )


def _write_triplet(report: dict[str, Any], out_dir: Path) -> tuple[Path, Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "monitoring_baseline_2026-04-03.md"
    json_path = out_dir / "monitoring_baseline_2026-04-03.json"
    jsonl_path = out_dir / "monitoring_baseline_2026-04-03.jsonl"

    md_path.write_text(_render_md(report), encoding="utf-8")
    json_payload = {
        "doc_meta": {
            "owner": "engineering",
            "status": "active",
            "doc_file": "monitoring_baseline_2026-04-03.json",
            "last_updated_utc": report["captured_at_utc"],
            "review_due_utc": "2026-04-15T00:00:00Z",
            "source_md": "docs/reports/monitoring_baseline_2026-04-03.md",
            "format_role": "canonical_structured_snapshot",
        },
        **report,
    }
    json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    jsonl_rows = [
        {
            "doc_meta": {
                "owner": "engineering",
                "status": "active",
                "doc_file": "monitoring_baseline_2026-04-03.jsonl",
                "last_updated_utc": report["captured_at_utc"],
                "review_due_utc": "2026-04-15T00:00:00Z",
                "format_role": "append_only_timeline",
                "source_md": "docs/reports/monitoring_baseline_2026-04-03.md",
            },
            "event": "baseline_snapshot",
            "captured_at_utc": report["captured_at_utc"],
            "summary": report["summary"],
        }
    ]
    with jsonl_path.open("w", encoding="utf-8") as f:
        for row in jsonl_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return md_path, json_path, jsonl_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect monitoring baseline and write MD/JSON/JSONL report triplet.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8012", help="Backend base URL.")
    parser.add_argument("--modes", nargs="+", default=["fast", "slow", "full"], help="Process scan modes.")
    parser.add_argument("--samples", type=int, default=12, help="Samples per mode.")
    parser.add_argument("--interval-ms", type=int, default=120, help="Pause between samples.")
    parser.add_argument("--timeout-s", type=float, default=8.0, help="HTTP timeout in seconds.")
    parser.add_argument("--out-dir", default=str(ROOT / "docs" / "reports"), help="Output directory for report triplet.")
    args = parser.parse_args()

    modes = [str(m).strip().lower() for m in args.modes if str(m).strip()]
    if not modes:
        raise SystemExit("No valid modes.")
    rows: list[dict[str, Any]] = []
    for mode in modes:
        rows.extend(
            _sample_mode(
                base_url=args.base_url.rstrip("/"),
                mode=mode,
                samples=max(1, int(args.samples)),
                interval_ms=max(0, int(args.interval_ms)),
                timeout_s=max(1.0, float(args.timeout_s)),
            )
        )
    report = {
        "captured_at_utc": _utc_now(),
        "base_url": args.base_url.rstrip("/"),
        "modes": modes,
        "samples_per_mode": max(1, int(args.samples)),
        "interval_ms": max(0, int(args.interval_ms)),
        "summary": [_mode_summary(rows, mode) for mode in modes],
        "samples": rows,
    }
    md_path, json_path, jsonl_path = _write_triplet(report, Path(args.out_dir))
    print(json.dumps({"md": str(md_path), "json": str(json_path), "jsonl": str(jsonl_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

