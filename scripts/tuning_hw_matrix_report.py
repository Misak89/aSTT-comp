#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.common.console_io import configure_console_io
from packages.common.runtime_paths import runtime_subpath

configure_console_io()

DEFAULT_TUNING_ROOT = runtime_subpath("tuning")


@dataclass
class JobSummary:
    job_id: str
    created_at: str
    label: str | None
    hardware_profile: str
    hardware_note: str | None
    load_profile: str | None
    load_cpu_target_pct: float | None
    load_ram_target_pct: float | None
    load_cpu_actual_avg_pct: float | None
    load_ram_actual_avg_pct: float | None
    constraints_profile: str | None
    constraints_cpu_cores: int | None
    constraints_ram_limit_mb: int | None
    constraints_priority: str | None
    constraints_ram_mode: str | None
    hostname: str | None
    best_trial_idx: int | None
    best_model_id: str | None
    best_params: dict[str, Any]
    wer: float | None
    wer_soft: float | None
    rtf: float | None
    perceived_delay_s: float | None
    latency_ms: float | None
    ram_peak_mb: float | None
    success_rate: float | None
    reproducibility_n: int


def _parse_status(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _best_trial(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid = [r for r in results if (not r.get("is_repeat")) and r.get("wer") is not None and not r.get("error")]
    if not valid:
        return None
    viable = [r for r in valid if isinstance(r.get("rtf"), (int, float)) and float(r["rtf"]) <= 1.0]
    target = viable if viable else valid
    return min(
        target,
        key=lambda r: (
            float(r.get("wer", 999.0)),
            float(r.get("rtf", 999.0)) if isinstance(r.get("rtf"), (int, float)) else 999.0,
        ),
    )


def _collect_jobs(tuning_root: Path, label_contains: str | None) -> list[JobSummary]:
    out: list[JobSummary] = []
    for job_dir in sorted(tuning_root.glob("tune_*")):
        status_path = job_dir / "status.json"
        if not status_path.exists():
            continue
        data = _parse_status(status_path)
        if not data:
            continue
        if data.get("status") != "completed":
            continue
        label = data.get("label")
        if label_contains and label_contains.lower() not in (str(label or "").lower()):
            continue

        results = data.get("results") or []
        best = _best_trial(results)
        hw_profile = str(data.get("hardware_profile") or "unknown")
        hw_info = data.get("hardware_info") or {}
        repro = data.get("reproducibility") or []
        out.append(
            JobSummary(
                job_id=str(data.get("job_id") or job_dir.name),
                created_at=str(data.get("created_at") or ""),
                label=label,
                hardware_profile=hw_profile,
                hardware_note=data.get("hardware_note"),
                load_profile=(str(data.get("load_profile")) if data.get("load_profile") else None),
                load_cpu_target_pct=(float(data["load_cpu_target_pct"]) if isinstance(data.get("load_cpu_target_pct"), (int, float)) else None),
                load_ram_target_pct=(float(data["load_ram_target_pct"]) if isinstance(data.get("load_ram_target_pct"), (int, float)) else None),
                load_cpu_actual_avg_pct=(float(best["load_cpu_actual_avg_pct"]) if best and isinstance(best.get("load_cpu_actual_avg_pct"), (int, float)) else None),
                load_ram_actual_avg_pct=(float(best["load_ram_actual_avg_pct"]) if best and isinstance(best.get("load_ram_actual_avg_pct"), (int, float)) else None),
                constraints_profile=(str(data.get("constraints_profile")) if data.get("constraints_profile") else None),
                constraints_cpu_cores=(int(data["constraints_cpu_cores"]) if isinstance(data.get("constraints_cpu_cores"), int) else None),
                constraints_ram_limit_mb=(int(data["constraints_ram_limit_mb"]) if isinstance(data.get("constraints_ram_limit_mb"), int) else None),
                constraints_priority=(str(data.get("constraints_priority")) if data.get("constraints_priority") else None),
                constraints_ram_mode=(str(data.get("constraints_ram_mode")) if data.get("constraints_ram_mode") else None),
                hostname=hw_info.get("hostname"),
                best_trial_idx=(int(best["trial_idx"]) if best and isinstance(best.get("trial_idx"), int) else None),
                best_model_id=(str(best.get("model_id")) if best and best.get("model_id") else None),
                best_params=(dict(best.get("params") or {}) if best else {}),
                wer=(float(best["wer"]) if best and isinstance(best.get("wer"), (int, float)) else None),
                wer_soft=(float(best["wer_soft"]) if best and isinstance(best.get("wer_soft"), (int, float)) else None),
                rtf=(float(best["rtf"]) if best and isinstance(best.get("rtf"), (int, float)) else None),
                perceived_delay_s=(float(best["perceived_delay_s"]) if best and isinstance(best.get("perceived_delay_s"), (int, float)) else None),
                latency_ms=(float(best["latency_ms"]) if best and isinstance(best.get("latency_ms"), (int, float)) else None),
                ram_peak_mb=(float(best["ram_peak_mb"]) if best and isinstance(best.get("ram_peak_mb"), (int, float)) else None),
                success_rate=(float(best["success_rate"]) if best and isinstance(best.get("success_rate"), (int, float)) else None),
                reproducibility_n=max((int(x.get("runs_ok", 0)) for x in repro if isinstance(x, dict)), default=0),
            )
        )
    return out


def _group_by_profile(rows: list[JobSummary]) -> dict[str, list[JobSummary]]:
    groups: dict[str, list[JobSummary]] = {}
    for r in rows:
        groups.setdefault(r.hardware_profile, []).append(r)
    return groups


def _row_sort_key(r: JobSummary) -> tuple[float, float]:
    wer = r.wer if r.wer is not None else 999.0
    rtf = r.rtf if r.rtf is not None else 999.0
    return (wer, rtf)


def _fmt(v: float | None, scale: float = 1.0, suffix: str = "") -> str:
    if v is None:
        return "-"
    return f"{v * scale:.2f}{suffix}"


def _print_text(rows: list[JobSummary]) -> None:
    if not rows:
        print("No completed tuning jobs matched filters.")
        return
    groups = _group_by_profile(rows)
    print("HW MATRIX REPORT")
    print(f"jobs={len(rows)} profiles={len(groups)}")
    print("")

    for profile in sorted(groups.keys()):
        g = sorted(groups[profile], key=_row_sort_key)
        hostnames = sorted({x.hostname for x in g if x.hostname})
        best = g[0]
        print(f"[{profile}] jobs={len(g)} hosts={', '.join(hostnames) if hostnames else 'n/a'}")
        print(
            " best:"
            f" job={best.job_id}"
            f" load={best.load_profile or 'none'}"
            f" cap={best.constraints_profile or 'none'}"
            f" cap_cores={best.constraints_cpu_cores if best.constraints_cpu_cores is not None else '-'}"
            f" cap_ram={_fmt(float(best.constraints_ram_limit_mb) if best.constraints_ram_limit_mb is not None else None, 1.0, 'MB')}"
            f" cap_ram_mode={best.constraints_ram_mode or 'none'}"
            f" target_cpu={_fmt(best.load_cpu_target_pct, 1.0, '%')}"
            f" target_ram={_fmt(best.load_ram_target_pct, 1.0, '%')}"
            f" actual_cpu={_fmt(best.load_cpu_actual_avg_pct, 1.0, '%')}"
            f" actual_ram={_fmt(best.load_ram_actual_avg_pct, 1.0, '%')}"
            f" wer={_fmt(best.wer, 100.0, '%')}"
            f" wer_soft={_fmt(best.wer_soft, 100.0, '%')}"
            f" rtf={_fmt(best.rtf)}"
            f" delay={_fmt(best.perceived_delay_s, 1.0, 's')}"
            f" ram_peak={_fmt(best.ram_peak_mb, 1.0, 'MB')}"
            f" repro_n={best.reproducibility_n}"
        )
        print(f" model={best.best_model_id or 'n/a'} params={json.dumps(best.best_params, ensure_ascii=False, sort_keys=True)}")
        print("")

    print("Top jobs (global):")
    for i, row in enumerate(sorted(rows, key=_row_sort_key)[:10], start=1):
        created = row.created_at
        try:
            created = datetime.fromisoformat(row.created_at.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
        print(
            f"{i:02d}. {row.job_id} [{row.hardware_profile}] "
            f"cap={row.constraints_profile or 'none'} "
            f"load={row.load_profile or 'none'} "
            f"wer={_fmt(row.wer, 100.0, '%')} rtf={_fmt(row.rtf)} "
            f"delay={_fmt(row.perceived_delay_s, 1.0, 's')} label={row.label or '-'} created={created}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate completed tuning jobs by hardware profile.")
    parser.add_argument("--tuning-root", default=str(DEFAULT_TUNING_ROOT), help="Path to runtime/tuning root.")
    parser.add_argument("--label-contains", default="", help="Only include jobs whose label contains this text.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text.")
    args = parser.parse_args()

    tuning_root = Path(args.tuning_root)
    if not tuning_root.exists():
        print(f"Tuning root not found: {tuning_root}")
        return 2

    rows = _collect_jobs(tuning_root, args.label_contains.strip() or None)
    if args.json:
        print(json.dumps([r.__dict__ for r in rows], ensure_ascii=False, indent=2))
    else:
        _print_text(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
