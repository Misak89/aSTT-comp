from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services.mic_v7_contract import (
    MIC_V7_EVENT_SCHEMA,
    MIC_V7_EVENT_VERSION,
    apply_v7_event_contract,
    compute_kpi_summary,
    evaluate_v7_readiness,
    validate_timeline_monotonic,
)


MIC_SEQUENCES_ROOT = ROOT / "runtime" / "mic_sequences"
MIC_EVENTS_LOG_PATH = ROOT / "runtime" / "logs" / "mic_sequence_events.jsonl"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if raw and (raw.isdigit() or (raw[0] in {"+", "-"} and raw[1:].isdigit())):
            try:
                return int(raw)
            except Exception:
                return None
    return None


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        raw = value.strip().replace(",", ".")
        if not raw:
            return None
        try:
            return float(raw)
        except Exception:
            return None
    return None


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object expected: {path}")
    return payload


def resolve_sequence_token(token: str, mic_sequences_root: Path = MIC_SEQUENCES_ROOT) -> str:
    raw = str(token or "").strip()
    if raw and raw.lower() != "latest":
        return raw

    best_name: str | None = None
    best_mtime = -1.0
    if not mic_sequences_root.exists():
        raise ValueError(f"mic sequences root not found: {mic_sequences_root}")

    for seq_dir in mic_sequences_root.iterdir():
        if not seq_dir.is_dir():
            continue
        report_path = seq_dir / "report.json"
        if not report_path.exists():
            continue
        mtime = report_path.stat().st_mtime
        if mtime > best_mtime:
            best_mtime = mtime
            best_name = seq_dir.name

    if not best_name:
        raise ValueError("no sequence report found under runtime/mic_sequences")
    return best_name


def _extract_event_sequence_token(payload: dict[str, Any]) -> str | None:
    direct = str(payload.get("sequence_id") or "").strip()
    if direct:
        return direct

    sequence_timing = payload.get("sequence_timing")
    if isinstance(sequence_timing, dict):
        from_timing = str(sequence_timing.get("sequence_token") or "").strip()
        if from_timing:
            return from_timing

    from_auto = str(payload.get("auto_model_sequence_token") or "").strip()
    if from_auto:
        return from_auto

    return None


@dataclass
class EventStats:
    scanned_lines: int
    matched_events: int
    matched_v7_events: int
    invalid_contract_events: int
    unknown_reason_events: int


def collect_event_stats(
    *,
    sequence_token: str,
    events_log_path: Path = MIC_EVENTS_LOG_PATH,
) -> EventStats:
    if not events_log_path.exists():
        return EventStats(
            scanned_lines=0,
            matched_events=0,
            matched_v7_events=0,
            invalid_contract_events=0,
            unknown_reason_events=0,
        )

    scanned = 0
    matched = 0
    matched_v7 = 0
    invalid = 0
    unknown_reason = 0
    for line in events_log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        scanned += 1
        row: dict[str, Any]
        try:
            raw = json.loads(line)
            row = raw if isinstance(raw, dict) else {}
        except Exception:
            continue

        event_seq = _extract_event_sequence_token(row)
        if event_seq != sequence_token:
            continue
        matched += 1

        orchestrator_mode = str(row.get("orchestrator_mode") or "").strip()
        if orchestrator_mode != "v7_cs_online":
            continue
        matched_v7 += 1

        normalized = apply_v7_event_contract(dict(row))
        if not bool(normalized.get("contract_valid")):
            invalid += 1
        if not bool(normalized.get("reason_known", True)):
            unknown_reason += 1

    return EventStats(
        scanned_lines=scanned,
        matched_events=matched,
        matched_v7_events=matched_v7,
        invalid_contract_events=invalid,
        unknown_reason_events=unknown_reason,
    )


def _check(check_id: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"id": check_id, "pass": bool(passed), "detail": detail}


def _fetch_json(url: str, timeout_s: float = 6.0) -> tuple[dict[str, Any] | None, str | None]:
    try:
        with urlopen(url, timeout=timeout_s) as response:  # nosec B310 - controlled localhost by caller
            content = response.read().decode("utf-8", errors="replace")
        payload = json.loads(content)
        if isinstance(payload, dict):
            return payload, None
        return None, f"non_object_json:{url}"
    except HTTPError as exc:
        return None, f"http_error:{exc.code}:{url}"
    except URLError as exc:
        return None, f"url_error:{exc.reason}:{url}"
    except Exception as exc:
        return None, f"request_error:{exc}:{url}"


def evaluate_sequence(
    *,
    sequence_token: str,
    report: dict[str, Any],
    csv_exists: bool,
    events: EventStats,
    min_models: int,
    max_models: int,
    require_v7_mode: bool,
    require_finalized: bool,
    dashboard_payload: dict[str, Any] | None = None,
    dashboard_error: str | None = None,
) -> dict[str, Any]:
    trials_raw = report.get("trials")
    trials = [t for t in (trials_raw if isinstance(trials_raw, list) else []) if isinstance(t, dict)]
    model_ids = sorted({str(t.get("model_id") or "").strip() for t in trials if str(t.get("model_id") or "").strip()})
    model_count = len(model_ids)
    started_count = sum(1 for t in trials if str(t.get("started_at") or "").strip())
    stopped_count = sum(1 for t in trials if str(t.get("stopped_at") or "").strip())
    latency_known_count = sum(1 for t in trials if _safe_float(t.get("latency_ms")) is not None)

    timeline_raw = report.get("timeline_validation")
    timeline = timeline_raw if isinstance(timeline_raw, dict) else validate_timeline_monotonic(trials)
    kpi_raw = report.get("kpi")
    kpi = kpi_raw if isinstance(kpi_raw, dict) else compute_kpi_summary(trials)

    strict_readiness = evaluate_v7_readiness(
        trials=trials,
        timeline_validation=timeline,
        kpi_summary=kpi,
        min_models=min_models,
        require_v7_mode=require_v7_mode,
        require_finalized=require_finalized,
    )

    checks = [
        _check("has_trials", len(trials) > 0, {"trials_count": len(trials)}),
        _check(
            "model_count_range",
            model_count >= max(1, int(min_models)) and model_count <= max(1, int(max_models)),
            {"actual": model_count, "expected": f"{min_models}-{max_models}", "models": model_ids},
        ),
        _check("all_trials_started", started_count == len(trials), {"actual": started_count, "expected": len(trials)}),
        _check("all_trials_finalized", (not require_finalized) or stopped_count == len(trials), {"actual": stopped_count, "expected": len(trials) if require_finalized else "n/a"}),
        _check("timeline_monotonic", bool(timeline.get("ok")), timeline),
        _check("timeline_coverage", _safe_int(timeline.get("checked_points")) is not None and int(timeline.get("checked_points") or 0) >= len(trials), {"actual": int(timeline.get("checked_points") or 0), "expected": f">={len(trials)}"}),
        _check("contract_schema_version", str((report.get("contract") or {}).get("schema") or "") == MIC_V7_EVENT_SCHEMA and str((report.get("contract") or {}).get("version") or "") == MIC_V7_EVENT_VERSION, {"actual_schema": (report.get("contract") or {}).get("schema"), "actual_version": (report.get("contract") or {}).get("version"), "expected_schema": MIC_V7_EVENT_SCHEMA, "expected_version": MIC_V7_EVENT_VERSION}),
        _check("latency_available_all_trials", latency_known_count == len(trials), {"actual": latency_known_count, "expected": len(trials)}),
        _check("csv_export_present", csv_exists, {"path": "report.csv"}),
        _check("events_for_sequence_present", events.matched_events > 0, {"actual": events.matched_events, "expected": ">0"}),
        _check("v7_events_for_sequence_present", events.matched_v7_events > 0, {"actual": events.matched_v7_events, "expected": ">0"}),
        _check("event_contract_valid", events.invalid_contract_events == 0, {"actual": events.invalid_contract_events, "expected": 0}),
        _check("event_reason_known", events.unknown_reason_events == 0, {"actual": events.unknown_reason_events, "expected": 0}),
        _check("strict_readiness", bool(strict_readiness.get("pass")), {"failed_checks": strict_readiness.get("failed_checks", [])}),
    ]

    if dashboard_payload is not None or dashboard_error is not None:
        checks.append(
            _check(
                "dashboard_runtime_mapping_endpoint",
                dashboard_error is None and isinstance(dashboard_payload, dict),
                {"error": dashboard_error},
            )
        )
        if isinstance(dashboard_payload, dict):
            checks.append(
                _check(
                    "dashboard_runtime_mapping_status",
                    str(dashboard_payload.get("status") or "").strip().lower() == "ok",
                    {
                        "status": dashboard_payload.get("status"),
                        "sequence_reports_total": dashboard_payload.get("sequence_reports_total"),
                        "readiness_fail_reports": dashboard_payload.get("readiness_fail_reports"),
                        "invalid_contract_events": dashboard_payload.get("invalid_contract_events"),
                    },
                )
            )

    failed_checks = [str(c.get("id")) for c in checks if not bool(c.get("pass"))]
    return {
        "generated_at_utc": _utc_now(),
        "sequence_token": sequence_token,
        "overall_pass": len(failed_checks) == 0,
        "failed_checks": failed_checks,
        "strict_readiness": strict_readiness,
        "summary": {
            "trials_count": len(trials),
            "model_count": model_count,
            "models": model_ids,
            "started_count": started_count,
            "stopped_count": stopped_count,
            "latency_known_count": latency_known_count,
            "timeline_checked_points": timeline.get("checked_points"),
        },
        "events": {
            "scanned_lines": events.scanned_lines,
            "matched_events": events.matched_events,
            "matched_v7_events": events.matched_v7_events,
            "invalid_contract_events": events.invalid_contract_events,
            "unknown_reason_events": events.unknown_reason_events,
        },
        "checks": checks,
    }


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# V7 readiness checklist",
        "",
        f"- generated_at_utc: `{result.get('generated_at_utc')}`",
        f"- sequence_token: `{result.get('sequence_token')}`",
        f"- overall_pass: `{bool(result.get('overall_pass'))}`",
        "",
        "## Failed Checks",
    ]
    failed = list(result.get("failed_checks") or [])
    if not failed:
        lines.append("- none")
    else:
        for check_id in failed:
            lines.append(f"- `{check_id}`")

    lines.extend(["", "## Checks"])
    for check in list(result.get("checks") or []):
        marker = "PASS" if bool(check.get("pass")) else "FAIL"
        detail = json.dumps(check.get("detail"), ensure_ascii=False, sort_keys=True)
        lines.append(f"- `{marker}` `{check.get('id')}`: `{detail}`")

    lines.append("")
    return "\n".join(lines)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False)
    if path.exists() and path.read_text(encoding="utf-8").strip():
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n")
            fh.write(line)
    else:
        path.write_text(line + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate strict V7 readiness checklist from runtime artifacts.")
    parser.add_argument("--sequence-token", default="latest", help="Sequence token under runtime/mic_sequences (or 'latest').")
    parser.add_argument("--mic-sequences-root", default=str(MIC_SEQUENCES_ROOT), help="Path to runtime/mic_sequences root.")
    parser.add_argument("--events-log-path", default=str(MIC_EVENTS_LOG_PATH), help="Path to runtime/logs/mic_sequence_events.jsonl.")
    parser.add_argument("--min-models", type=int, default=3, help="Minimum required model count.")
    parser.add_argument("--max-models", type=int, default=5, help="Maximum expected model count for one validation scenario.")
    parser.add_argument("--allow-non-v7-mode", action="store_true", help="Do not require all trials in v7_cs_online mode.")
    parser.add_argument("--allow-unfinalized", action="store_true", help="Do not require all trials finalized.")
    parser.add_argument("--api-base", default="", help="Optional API base URL (example: http://127.0.0.1:8012).")
    parser.add_argument("--api-max-reports", type=int, default=80, help="max_reports for /api/health/mic-orchestrator-v7.")
    parser.add_argument("--api-max-events", type=int, default=2500, help="max_events for /api/health/mic-orchestrator-v7.")
    parser.add_argument("--output-json", default="", help="Optional output JSON file path.")
    parser.add_argument("--output-md", default="", help="Optional output Markdown file path.")
    parser.add_argument("--output-jsonl", default="", help="Optional output JSONL file path (append mode).")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    sequences_root = Path(args.mic_sequences_root).resolve()
    events_log_path = Path(args.events_log_path).resolve()
    try:
        sequence_token = resolve_sequence_token(args.sequence_token, sequences_root)
    except Exception as exc:
        print(f"v7-readiness: resolve sequence failed: {exc}", file=sys.stderr)
        return 2

    report_path = sequences_root / sequence_token / "report.json"
    csv_path = sequences_root / sequence_token / "report.csv"
    if not report_path.exists():
        print(f"v7-readiness: report not found: {report_path}", file=sys.stderr)
        return 2

    try:
        report = _read_json(report_path)
    except Exception as exc:
        print(f"v7-readiness: report parse failed: {exc}", file=sys.stderr)
        return 2

    events = collect_event_stats(sequence_token=sequence_token, events_log_path=events_log_path)

    dashboard_payload: dict[str, Any] | None = None
    dashboard_error: str | None = None
    api_base = str(args.api_base or "").strip().rstrip("/")
    if api_base:
        dashboard_url = (
            f"{api_base}/api/health/mic-orchestrator-v7"
            f"?max_reports={max(1, int(args.api_max_reports))}"
            f"&max_events={max(100, int(args.api_max_events))}"
        )
        dashboard_payload, dashboard_error = _fetch_json(dashboard_url)

    result = evaluate_sequence(
        sequence_token=sequence_token,
        report=report,
        csv_exists=csv_path.exists(),
        events=events,
        min_models=max(1, int(args.min_models)),
        max_models=max(1, int(args.max_models)),
        require_v7_mode=not bool(args.allow_non_v7_mode),
        require_finalized=not bool(args.allow_unfinalized),
        dashboard_payload=dashboard_payload,
        dashboard_error=dashboard_error,
    )
    result["sources"] = {
        "report_json": str(report_path),
        "report_csv": str(csv_path),
        "events_log": str(events_log_path),
        "api_base": api_base or None,
    }

    out_json = str(args.output_json or "").strip()
    if out_json:
        target = Path(out_json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    out_md = str(args.output_md or "").strip()
    if out_md:
        target = Path(out_md)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_markdown(result), encoding="utf-8")

    out_jsonl = str(args.output_jsonl or "").strip()
    if out_jsonl:
        _append_jsonl(Path(out_jsonl), result)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if bool(result.get("overall_pass")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
