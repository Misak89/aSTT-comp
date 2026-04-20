from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


ORCHESTRATOR_MODE_V7 = "v7_cs_online"

MIC_V7_EVENT_SCHEMA = "astt.mic.v7.event"
MIC_V7_EVENT_VERSION = "1.0.0"

MIC_V7_REQUIRED_EVENT_FIELDS: tuple[str, ...] = (
    "run_id",
    "sequence_id",
    "sequence_index",
    "sequence_total",
    "global_timeline_ms",
)

MIC_V7_EVENT_NAMES = frozenset(
    {
        "created",
        "started",
        "start_failed",
        "chunk_error",
        "finalized",
        "ws_accept",
        "ws_session_not_found",
        "ws_start_ok",
        "ws_start_failed",
        "ws_stop_action",
        "ws_disconnect",
        "ws_loop_error",
        "ws_final_sent",
        "ws_final_send_failed",
        "ws_finalize_failed",
        "ws_closed",
        "ws_close_failed",
    }
)

MIC_V7_REASON_CODES = frozenset(
    {
        "adapter_error",
        "backpressure_drop",
        "buffer_overrun",
        "decode_error",
        "empty_chunk",
        "no_tokens",
        "not_recording",
        "preflight_failed",
        "session_not_found",
        "start_recording_failed",
        "timeout",
        "unknown",
        "ws_transport_error",
    }
)

_REASON_CODE_ALIASES: dict[str, str] = {
    "backpressure": "backpressure_drop",
    "buffer_overflow": "buffer_overrun",
    "preflight": "preflight_failed",
    "start_failed": "start_recording_failed",
    "transport_error": "ws_transport_error",
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except Exception:
            return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return float(raw)
        except Exception:
            return None
    return None


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return int(raw)
        except Exception:
            return None
    return None


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if q <= 0:
        return float(min(values))
    if q >= 1:
        return float(max(values))
    arr = sorted(float(v) for v in values)
    idx = (len(arr) - 1) * q
    lo = int(idx)
    hi = min(lo + 1, len(arr) - 1)
    frac = idx - lo
    return arr[lo] * (1.0 - frac) + arr[hi] * frac


def normalize_reason_code(
    raw_reason: Any,
    *,
    allow_none: bool = True,
    allow_unknown: bool = False,
    fallback: str = "unknown",
) -> str | None:
    if raw_reason is None:
        return None if allow_none else fallback

    text = str(raw_reason).strip().lower()
    if not text:
        return None if allow_none else fallback

    normalized = []
    for ch in text:
        if ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "_":
            normalized.append(ch)
        elif ch in {" ", "-"}:
            normalized.append("_")
    candidate = "".join(normalized).strip("_")
    while "__" in candidate:
        candidate = candidate.replace("__", "_")
    if not candidate:
        return None if allow_none else fallback

    canonical = _REASON_CODE_ALIASES.get(candidate, candidate)
    if canonical in MIC_V7_REASON_CODES:
        return canonical
    if allow_unknown:
        return canonical
    return fallback


def build_contract_metadata() -> dict[str, Any]:
    return {
        "schema": MIC_V7_EVENT_SCHEMA,
        "version": MIC_V7_EVENT_VERSION,
        "required_fields_v7": list(MIC_V7_REQUIRED_EVENT_FIELDS),
        "event_names": sorted(MIC_V7_EVENT_NAMES),
        "reason_codes": sorted(MIC_V7_REASON_CODES),
    }


def apply_v7_event_contract(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out["contract_schema"] = MIC_V7_EVENT_SCHEMA
    out["contract_version"] = MIC_V7_EVENT_VERSION
    out["contract_emitted_at"] = _utc_now()
    event_name = str(out.get("event") or "").strip()
    out["event_known"] = event_name in MIC_V7_EVENT_NAMES

    mode = str(out.get("orchestrator_mode") or "").strip().lower()
    required: tuple[str, ...] = MIC_V7_REQUIRED_EVENT_FIELDS if mode == ORCHESTRATOR_MODE_V7 else tuple()
    missing = [field for field in required if _is_missing(out.get(field))]
    out["contract_required_fields"] = list(required)
    out["contract_missing_fields"] = missing
    out["contract_valid"] = (len(missing) == 0) and bool(out["event_known"])

    reason_code = normalize_reason_code(out.get("reason_code"), allow_none=True, allow_unknown=True)
    out["reason_code"] = reason_code
    out["reason_known"] = reason_code is None or reason_code in MIC_V7_REASON_CODES
    return out


def classify_latency_lane(
    latency_ms: float | int | None,
    *,
    target_low_ms: float = 5000.0,
    target_high_ms: float = 8000.0,
    hard_limit_ms: float = 12000.0,
) -> str:
    if not isinstance(latency_ms, (int, float)):
        return "unknown"
    ms = float(latency_ms)
    if ms > hard_limit_ms:
        return "over_hard_limit"
    if ms > target_high_ms:
        return "over_target"
    if ms >= target_low_ms:
        return "within_target"
    return "under_target"


def compute_trial_kpi(
    trial: dict[str, Any],
    *,
    hard_limit_ms: float = 12000.0,
) -> dict[str, Any]:
    latency_ms = _safe_float(trial.get("first_word_wall_ms"))
    if latency_ms is None:
        latency_ms = _safe_float(trial.get("first_word_latency_ms"))
    if latency_ms is None:
        latency_ms = _safe_float(trial.get("first_word_audio_ms"))

    drop_rate = _safe_float(trial.get("drop_rate")) or 0.0
    quality_score = max(0.0, 1.0 - drop_rate)
    lane = classify_latency_lane(latency_ms, hard_limit_ms=hard_limit_ms)

    return {
        "latency_ms": round(float(latency_ms), 1) if isinstance(latency_ms, float) else None,
        "latency_lane": lane,
        "latency_hard_violation": lane == "over_hard_limit",
        "quality_score": round(quality_score, 4),
        "hw_rss_peak_mb": _safe_float(trial.get("worker_rss_peak_mb")),
    }


def validate_timeline_monotonic(
    rows: list[dict[str, Any]],
    *,
    timeline_key: str = "global_timeline_ms",
    seq_key: str = "seq_index",
) -> dict[str, Any]:
    issues: list[str] = []
    regressions = 0
    max_regression_ms = 0.0
    checked_points = 0
    prev_timeline_ms: float | None = None
    prev_seq: int | None = None
    min_timeline_ms: float | None = None
    max_timeline_ms: float | None = None

    for idx, row in enumerate(rows):
        seq = _safe_int(row.get(seq_key))
        if prev_seq is not None and seq is not None and seq < prev_seq:
            issues.append(f"sequence_index_regression:{prev_seq}->{seq}@row={idx}")
        if seq is not None:
            prev_seq = seq

        timeline_ms = _safe_float(row.get(timeline_key))
        if timeline_ms is None:
            continue
        checked_points += 1
        if min_timeline_ms is None:
            min_timeline_ms = timeline_ms
        max_timeline_ms = timeline_ms if max_timeline_ms is None else max(max_timeline_ms, timeline_ms)
        if prev_timeline_ms is not None and timeline_ms < prev_timeline_ms:
            regressions += 1
            delta = prev_timeline_ms - timeline_ms
            max_regression_ms = max(max_regression_ms, delta)
            issues.append(
                f"timeline_regression:{prev_timeline_ms:.1f}->{timeline_ms:.1f}@seq={seq if seq is not None else 'n/a'}"
            )
        prev_timeline_ms = timeline_ms

    return {
        "ok": regressions == 0 and not any(issue.startswith("sequence_index_regression") for issue in issues),
        "issues": issues,
        "checked_points": checked_points,
        "regressions": regressions,
        "max_regression_ms": round(max_regression_ms, 1),
        "min_timeline_ms": round(min_timeline_ms, 1) if isinstance(min_timeline_ms, float) else None,
        "max_timeline_ms": round(max_timeline_ms, 1) if isinstance(max_timeline_ms, float) else None,
    }


def compute_kpi_summary(
    trials: list[dict[str, Any]],
    *,
    hard_limit_ms: float = 12000.0,
) -> dict[str, Any]:
    lane_counts: dict[str, int] = {
        "under_target": 0,
        "within_target": 0,
        "over_target": 0,
        "over_hard_limit": 0,
        "unknown": 0,
    }
    latency_values: list[float] = []
    rtf_values: list[float] = []
    drop_values: list[float] = []
    quality_values: list[float] = []
    rss_values: list[float] = []
    hard_limit_violations = 0

    for trial in trials:
        kpi = compute_trial_kpi(trial, hard_limit_ms=hard_limit_ms)
        lane = str(kpi.get("latency_lane") or "unknown")
        lane_counts[lane] = lane_counts.get(lane, 0) + 1
        if kpi.get("latency_hard_violation"):
            hard_limit_violations += 1

        lat = _safe_float(kpi.get("latency_ms"))
        if isinstance(lat, float):
            latency_values.append(lat)
        rtf = _safe_float(trial.get("rtf"))
        if isinstance(rtf, float):
            rtf_values.append(rtf)
        drop = _safe_float(trial.get("drop_rate"))
        if isinstance(drop, float):
            drop_values.append(drop)
        quality = _safe_float(kpi.get("quality_score"))
        if isinstance(quality, float):
            quality_values.append(quality)
        rss = _safe_float(kpi.get("hw_rss_peak_mb"))
        if isinstance(rss, float):
            rss_values.append(rss)

    def _avg(values: list[float], ndigits: int = 4) -> float | None:
        if not values:
            return None
        return round(sum(values) / len(values), ndigits)

    def _p95(values: list[float], ndigits: int = 1) -> float | None:
        p = _percentile(values, 0.95)
        return round(float(p), ndigits) if isinstance(p, (int, float)) else None

    return {
        "hard_limit_ms": hard_limit_ms,
        "hard_limit_violations": hard_limit_violations,
        "latency_lane_counts": lane_counts,
        "latency_ms_p50": round(float(_percentile(latency_values, 0.50)), 1) if latency_values else None,
        "latency_ms_p95": _p95(latency_values, ndigits=1),
        "rtf_avg": _avg(rtf_values, ndigits=4),
        "rtf_p95": _p95(rtf_values, ndigits=4),
        "drop_rate_avg": _avg(drop_values, ndigits=4),
        "drop_rate_p95": _p95(drop_values, ndigits=4),
        "quality_score_avg": _avg(quality_values, ndigits=4),
        "quality_score_min": round(min(quality_values), 4) if quality_values else None,
        "hw_rss_peak_mb_avg": _avg(rss_values, ndigits=2),
        "hw_rss_peak_mb_max": round(max(rss_values), 1) if rss_values else None,
    }


def evaluate_v7_readiness(
    *,
    trials: list[dict[str, Any]],
    timeline_validation: dict[str, Any] | None,
    kpi_summary: dict[str, Any] | None,
    min_models: int = 3,
    require_v7_mode: bool = True,
    require_finalized: bool = True,
) -> dict[str, Any]:
    models = {str(t.get("model_id") or "").strip() for t in trials if str(t.get("model_id") or "").strip()}
    v7_count = sum(1 for t in trials if str(t.get("orchestrator_mode") or "").strip() == ORCHESTRATOR_MODE_V7)
    finalized_count = sum(1 for t in trials if str(t.get("status") or "").strip() == "stopped" or t.get("stopped_at"))
    invalid_contract_count = sum(1 for t in trials if t.get("contract_valid") is False)
    hard_limit_violations = int((kpi_summary or {}).get("hard_limit_violations") or 0)
    timeline_ok = bool((timeline_validation or {}).get("ok"))

    checks = [
        {
            "id": "has_trials",
            "pass": len(trials) > 0,
            "actual": len(trials),
            "expected": ">=1",
        },
        {
            "id": "model_coverage",
            "pass": len(models) >= max(1, int(min_models)),
            "actual": len(models),
            "expected": f">={max(1, int(min_models))}",
        },
        {
            "id": "v7_mode_only",
            "pass": (not require_v7_mode) or v7_count == len(trials),
            "actual": v7_count,
            "expected": len(trials) if require_v7_mode else "n/a",
        },
        {
            "id": "timeline_monotonic",
            "pass": timeline_ok,
            "actual": bool(timeline_ok),
            "expected": True,
        },
        {
            "id": "contract_valid",
            "pass": invalid_contract_count == 0,
            "actual": invalid_contract_count,
            "expected": 0,
        },
        {
            "id": "latency_hard_limit",
            "pass": hard_limit_violations == 0,
            "actual": hard_limit_violations,
            "expected": 0,
        },
        {
            "id": "finalized_trials",
            "pass": (not require_finalized) or finalized_count == len(trials),
            "actual": finalized_count,
            "expected": len(trials) if require_finalized else "n/a",
        },
    ]

    failed = [str(c["id"]) for c in checks if not bool(c.get("pass"))]
    return {
        "pass": len(failed) == 0,
        "checked_at": _utc_now(),
        "checks": checks,
        "failed_checks": failed,
    }
