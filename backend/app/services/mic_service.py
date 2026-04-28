"""
Mic service — správa live mic sessions přes WebSocket.

Každá session:
  - má unikátní session_id
  - přijímá audio chunky z frontendu (PCM float32 base64 nebo binary frames)
  - posílá partial + final přepis zpět přes WebSocket
  - měří RTF, first word latency, RAM

Session lifecycle:
  create_session() → session_id
  WebSocket connection → audio frames → partial results
  stop_session()   → final result + metriky
"""
from __future__ import annotations

import base64
import csv
import io
import json
import math
import secrets
import shutil
import string
import struct
import threading
import time
import uuid
import wave
import zipfile
from array import array
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import MIC_SEQUENCES_ROOT, MIC_SESSIONS_ROOT, MODEL_STORE_ROOT, RUNTIME_ROOT, SUBTITLES_ROOT
from .mic_v7_contract import (
    MIC_V7_EVENT_SCHEMA,
    MIC_V7_EVENT_VERSION,
    MIC_V7_REASON_CODES,
    apply_v7_event_contract,
    build_contract_metadata,
    compute_kpi_summary,
    compute_trial_kpi,
    evaluate_v7_readiness,
    normalize_reason_code,
    validate_timeline_monotonic,
)
from packages.adapters._registry import REGISTRY

try:
    import psutil
except ModuleNotFoundError:
    psutil = None  # type: ignore[assignment]

SAMPLE_RATE = 16000
WS_AUDIO_FRAME_MAGIC = b"ASTT"
MOBILE_LOOP_ROOT = MIC_SESSIONS_ROOT / "mobile_loops"
MIC_EVENTS_LOG_PATH = RUNTIME_ROOT / "logs" / "mic_sequence_events.jsonl"
MOBILE_LOOP_PAIRING_CODE_LEN = 6
MOBILE_LOOP_PAIRING_ALPHABET = string.ascii_letters + string.digits
_AUTO_SEQUENCE_MAX_TRACKED = 256
_V7_SEQUENCE_MAX_TRACKED = 256
ORCHESTRATOR_MODE_LEGACY = "legacy_sequence"
ORCHESTRATOR_MODE_V7 = "v7_cs_online"
_V7_LATENCY_HARD_LIMIT_MS = 12_000.0


def _model_default_params(model_id: str) -> dict[str, Any]:
    descriptor = REGISTRY.get(model_id)
    if descriptor is None:
        return {}
    return {param.name: param.default for param in descriptor.params}
_ORCHESTRATOR_MODE_ALIASES = {
    "legacy": ORCHESTRATOR_MODE_LEGACY,
    "legacy_sequence": ORCHESTRATOR_MODE_LEGACY,
    "v7": ORCHESTRATOR_MODE_V7,
    "v7_preview": ORCHESTRATOR_MODE_V7,
    "v7_cs_online": ORCHESTRATOR_MODE_V7,
}
_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "idle": {"recording", "stopped"},
    "recording": {"stopped"},
    "stopped": {"recording"},
}


@dataclass
class MicSessionState:
    session_id: str
    model_id: str
    model_params: dict
    created_at: str
    started_at: str | None = None
    stopped_at: str | None = None
    status: str = "idle"        # idle | recording | stopped
    transcript: str = ""
    partial: str = ""
    first_word_latency_ms: float | None = None
    first_word_wall_ms: float | None = None
    first_word_audio_ms: float | None = None
    first_token_ms_p50: float | None = None
    first_token_ms_p95: float | None = None
    segment_finalize_ms_p50: float | None = None
    segment_finalize_ms_p95: float | None = None
    processing_ms_p50: float | None = None
    processing_ms_p95: float | None = None
    capture_jitter_ms_p50: float | None = None
    capture_jitter_ms_p95: float | None = None
    capture_lag_ms_p50: float | None = None
    capture_lag_ms_p95: float | None = None
    drop_rate: float = 0.0
    session_resets: int = 0
    worker_rss_peak_mb: float | None = None
    target_sample_rate: int = SAMPLE_RATE
    input_gain_db: float = 0.0
    queue_high_watermark_s: float = 1.2
    queue_low_watermark_s: float = 0.4
    queue_depth_s: float = 0.0
    queue_depth_peak_s: float = 0.0
    backpressure_events: int = 0
    backpressure_active: bool = False
    chunk_count: int = 0
    dropped_chunks: int = 0
    reason_code: str | None = None
    elapsed_s: float = 0.0
    rtf: float = 0.0
    total_audio_s: float = 0.0
    sequence_timing: dict[str, Any] = field(default_factory=dict)
    orchestrator_mode: str = ORCHESTRATOR_MODE_LEGACY
    run_id: str | None = None
    sequence_id: str | None = None
    sequence_index: int | None = None
    sequence_total: int | None = None
    global_timeline_anchor_epoch_ms: float | None = None
    preflight_ok: bool = True
    preflight_errors: list[str] = field(default_factory=list)
    preflight_warnings: list[str] = field(default_factory=list)
    error: str | None = None
    final: dict[str, Any] | None = None
    _session_obj: Any = field(default=None, repr=False)
    _started_perf: float = field(default=0.0, repr=False)
    _total_samples: int = field(default=0, repr=False)
    _chunk_processing_ms: list[float] = field(default_factory=list, repr=False)
    _segment_finalize_ms: list[float] = field(default_factory=list, repr=False)
    _capture_jitter_ms: list[float] = field(default_factory=list, repr=False)
    _capture_lag_ms: list[float] = field(default_factory=list, repr=False)
    _last_capture_ts_ms: float | None = field(default=None, repr=False)
    _last_arrival_perf: float | None = field(default=None, repr=False)
    _last_text_change_perf: float | None = field(default=None, repr=False)
    _last_sequence_report_persist_perf: float = field(default=0.0, repr=False)
    _pid: int | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


_sessions: dict[str, MicSessionState] = {}
_sessions_lock = threading.Lock()
_auto_sequence_timing: dict[str, dict[str, Any]] = {}
_auto_sequence_lock = threading.Lock()
_v7_sequence_timing: dict[str, dict[str, float]] = {}
_v7_sequence_lock = threading.Lock()


def _slugify_filename(value: str, *, fallback: str = "manual") -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return fallback
    out_chars: list[str] = []
    for ch in raw:
        if ("a" <= ch <= "z") or ("0" <= ch <= "9"):
            out_chars.append(ch)
        elif ch in {" ", "-", "_", "."}:
            out_chars.append("_")
    slug = "".join(out_chars).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or fallback


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _iso_to_epoch_ms(value: str | None) -> float | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp() * 1000.0


def _epoch_ms_to_iso(value: float | int | None) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC).isoformat()
    except Exception:
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


def _safe_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
    return None


def _safe_str_list(value: Any, *, limit: int = 200) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value[: max(0, int(limit))]:
        text = str(item).strip()
        if text:
            out.append(text[:160])
    return out


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _normalize_reason_code(raw_reason: Any, *, allow_none: bool = True, fallback: str = "unknown") -> str | None:
    return normalize_reason_code(
        raw_reason,
        allow_none=allow_none,
        allow_unknown=False,
        fallback=fallback,
    )


def _set_reason_code(state: MicSessionState, raw_reason: Any, *, allow_none: bool = True, fallback: str = "unknown") -> str | None:
    reason = _normalize_reason_code(raw_reason, allow_none=allow_none, fallback=fallback)
    state.reason_code = reason
    return reason


def _session_segment_id(state: MicSessionState) -> str:
    chunk_no = max(0, int(state.chunk_count))
    return f"{state.session_id}:seg:{chunk_no}"


def _orchestrator_mode_from_params(model_params: dict[str, Any]) -> str:
    raw = str(model_params.get("mic_orchestrator_mode") or "").strip().lower()
    if not raw:
        return ORCHESTRATOR_MODE_LEGACY
    return _ORCHESTRATOR_MODE_ALIASES.get(raw, ORCHESTRATOR_MODE_LEGACY)


def _parse_auto_sequence_meta(model_params: dict[str, Any]) -> dict[str, Any]:
    token_raw = model_params.get("auto_model_sequence_token")
    token = str(token_raw).strip() if token_raw is not None else ""
    sequence_index = _safe_int(model_params.get("auto_model_sequence_index"))
    sequence_total = _safe_int(model_params.get("auto_model_sequence_total"))
    speech_s = _safe_float(model_params.get("mobile_loop_speech_s"))
    capture_speech_s = _safe_float(model_params.get("mobile_loop_capture_speech_s"))
    early_stop_s = _safe_float(model_params.get("mobile_loop_early_stop_s"))
    pause_s = _safe_float(model_params.get("mobile_loop_pause_s"))
    audio_start_delay_s = _safe_float(model_params.get("mobile_loop_audio_start_delay_s"))
    cycle_s = (speech_s + pause_s) if isinstance(speech_s, (int, float)) and isinstance(pause_s, (int, float)) else None
    hard_trial_s = _safe_float(model_params.get("auto_model_sequence_hard_trial_s"))
    hard_trial_base_s = _safe_float(model_params.get("auto_model_sequence_hard_trial_base_s"))
    effective_hard_trial_s = _safe_float(model_params.get("auto_model_sequence_effective_hard_trial_s"))
    return {
        "token": token or None,
        "index": sequence_index,
        "total": sequence_total,
        "speech_s": speech_s,
        "capture_speech_s": capture_speech_s,
        "early_stop_s": early_stop_s,
        "pause_s": pause_s,
        "audio_start_delay_s": audio_start_delay_s if isinstance(audio_start_delay_s, (int, float)) else 0.0,
        "cycle_s": cycle_s,
        "slot_s": _safe_float(model_params.get("auto_model_sequence_slot_s")) or cycle_s,
        "mobile_loop_enabled": bool(model_params.get("mobile_loop_enabled")),
        "mobile_loop_auto_stop": _safe_bool(model_params.get("mobile_loop_auto_stop")),
        "mobile_loop_sync_first_round": _safe_bool(model_params.get("mobile_loop_sync_first_round")),
        "mobile_loop_measured_rounds": _safe_int(model_params.get("mobile_loop_measured_rounds")),
        "mobile_loop_package_id": str(model_params.get("mobile_loop_package_id") or "").strip() or None,
        "audio_start_source": str(model_params.get("mobile_loop_audio_start_source") or "").strip() or None,
        "audio_start_known": _safe_bool(model_params.get("mobile_loop_audio_start_known")),
        "queue_model_ids": _safe_str_list(model_params.get("auto_model_sequence_queue")),
        "selected_model_ids": _safe_str_list(model_params.get("auto_model_sequence_selected_models")),
        "lead_start_s": _safe_float(model_params.get("auto_model_sequence_lead_start_s")),
        "preparation_s": _safe_float(model_params.get("auto_model_sequence_preparation_s")),
        "hard_trial_base_s": hard_trial_base_s,
        "hard_trial_s": hard_trial_s,
        "effective_hard_trial_s": effective_hard_trial_s if isinstance(effective_hard_trial_s, (int, float)) else hard_trial_s,
        "silence_stop_s": _safe_float(model_params.get("auto_model_sequence_silence_stop_s")),
        "silence_min_elapsed_s": _safe_float(model_params.get("auto_model_sequence_silence_min_elapsed_s")),
        "silence_min_audio_fraction": _safe_float(model_params.get("auto_model_sequence_silence_min_audio_fraction")),
        "grace_s": _safe_float(model_params.get("auto_model_sequence_grace_s")),
        "latency_guard_s": _safe_float(model_params.get("auto_model_sequence_latency_guard_s")),
        "adaptive_max_cut_s": _safe_float(model_params.get("auto_model_sequence_adaptive_max_cut_s")),
        "tuning_series_id": str(model_params.get("tuning_series_id") or "").strip() or None,
        "tuning_mode": str(model_params.get("tuning_mode") or "").strip() or None,
        "tuning_step_size": _safe_float(model_params.get("tuning_step_size")),
        "tuning_slot_index": _safe_int(model_params.get("tuning_slot_index")),
        "tuning_slot_total": _safe_int(model_params.get("tuning_slot_total")),
        "tuning_variant_id": str(model_params.get("tuning_variant_id") or "").strip() or None,
        "tuning_variant_label": str(model_params.get("tuning_variant_label") or "").strip() or None,
        "tuning_repeat_index": _safe_int(model_params.get("tuning_repeat_index")),
        "tuning_repeat_total": _safe_int(model_params.get("tuning_repeat_total")),
        "tuning_changed_params": _safe_dict(model_params.get("tuning_changed_params")),
        "tuning_baseline_params": _safe_dict(model_params.get("tuning_baseline_params")),
        "tuning_max_lag_s": _safe_float(model_params.get("tuning_max_lag_s")),
    }


def _sequence_plan_payload_from_meta(meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "mobile_loop_enabled": meta.get("mobile_loop_enabled"),
        "mobile_loop_speech_s": meta.get("speech_s"),
        "mobile_loop_capture_speech_s": meta.get("capture_speech_s"),
        "mobile_loop_early_stop_s": meta.get("early_stop_s"),
        "mobile_loop_pause_s": meta.get("pause_s"),
        "mobile_loop_audio_start_delay_s": meta.get("audio_start_delay_s"),
        "mobile_loop_cycle_s": meta.get("cycle_s"),
        "mobile_loop_sync_first_round": meta.get("mobile_loop_sync_first_round"),
        "mobile_loop_measured_rounds": meta.get("mobile_loop_measured_rounds"),
        "mobile_loop_auto_stop": meta.get("mobile_loop_auto_stop"),
        "mobile_loop_package_id": meta.get("mobile_loop_package_id"),
        "mobile_loop_audio_start_source": meta.get("audio_start_source"),
        "mobile_loop_audio_start_known": meta.get("audio_start_known"),
        "auto_model_sequence_queue": list(meta.get("queue_model_ids") or []),
        "auto_model_sequence_selected_models": list(meta.get("selected_model_ids") or []),
        "auto_model_sequence_lead_start_s": meta.get("lead_start_s"),
        "auto_model_sequence_preparation_s": meta.get("preparation_s"),
        "auto_model_sequence_hard_trial_base_s": meta.get("hard_trial_base_s"),
        "auto_model_sequence_hard_trial_s": meta.get("hard_trial_s"),
        "auto_model_sequence_effective_hard_trial_s": meta.get("effective_hard_trial_s"),
        "auto_model_sequence_silence_stop_s": meta.get("silence_stop_s"),
        "auto_model_sequence_silence_min_elapsed_s": meta.get("silence_min_elapsed_s"),
        "auto_model_sequence_silence_min_audio_fraction": meta.get("silence_min_audio_fraction"),
        "auto_model_sequence_grace_s": meta.get("grace_s"),
        "auto_model_sequence_slot_s": meta.get("slot_s"),
        "auto_model_sequence_latency_guard_s": meta.get("latency_guard_s"),
        "auto_model_sequence_adaptive_max_cut_s": meta.get("adaptive_max_cut_s"),
        "tuning_series_id": meta.get("tuning_series_id"),
        "tuning_mode": meta.get("tuning_mode"),
        "tuning_step_size": meta.get("tuning_step_size"),
        "tuning_slot_index": meta.get("tuning_slot_index"),
        "tuning_slot_total": meta.get("tuning_slot_total"),
        "tuning_variant_id": meta.get("tuning_variant_id"),
        "tuning_variant_label": meta.get("tuning_variant_label"),
        "tuning_repeat_index": meta.get("tuning_repeat_index"),
        "tuning_repeat_total": meta.get("tuning_repeat_total"),
        "tuning_changed_params": dict(meta.get("tuning_changed_params") or {}),
        "tuning_baseline_params": dict(meta.get("tuning_baseline_params") or {}),
        "tuning_max_lag_s": meta.get("tuning_max_lag_s"),
    }


def _evaluate_session_preflight(
    *,
    model_id: str,
    model_params: dict[str, Any],
    orchestrator_mode: str,
    run_id: str | None,
    sequence_id: str | None,
    sequence_index: int | None,
    sequence_total: int | None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    try:
        _adapter_key(model_id)
        checks.append({"id": "adapter_known", "status": "ok", "detail": model_id})
    except Exception:
        errors.append("model_not_supported")
        checks.append({"id": "adapter_known", "status": "error", "detail": model_id})

    if str(model_id).startswith("qwen"):
        errors.append("model_not_mic_capable")
        checks.append({"id": "mic_capable", "status": "error", "detail": "qwen_asr is batch-only"})
    else:
        checks.append({"id": "mic_capable", "status": "ok", "detail": "adapter supports mic mode"})

    sample_rate = _safe_int(model_params.get("sample_rate"))
    if sample_rate is not None and (sample_rate < 8000 or sample_rate > 48000):
        errors.append("sample_rate_out_of_range")
        checks.append({"id": "sample_rate_range", "status": "error", "detail": sample_rate})
    else:
        checks.append({"id": "sample_rate_range", "status": "ok", "detail": sample_rate or "default"})

    lang_raw = model_params.get("language")
    if lang_raw is None:
        lang_raw = model_params.get("lang")
    lang = str(lang_raw or "").strip().lower()
    if lang and lang not in {"cs", "cs-cz"}:
        warnings.append("language_not_cs")
        checks.append({"id": "language_code", "status": "warn", "detail": lang})
    else:
        checks.append({"id": "language_code", "status": "ok", "detail": lang or "implicit"})

    if orchestrator_mode == ORCHESTRATOR_MODE_V7:
        if not str(run_id or "").strip():
            errors.append("v7_missing_run_id")
            checks.append({"id": "v7_run_id", "status": "error", "detail": None})
        else:
            checks.append({"id": "v7_run_id", "status": "ok", "detail": run_id})

        if not str(sequence_id or "").strip():
            errors.append("v7_missing_sequence_id")
            checks.append({"id": "v7_sequence_id", "status": "error", "detail": None})
        else:
            checks.append({"id": "v7_sequence_id", "status": "ok", "detail": sequence_id})

        if sequence_index is None:
            warnings.append("v7_missing_sequence_index")
            checks.append({"id": "v7_sequence_index", "status": "warn", "detail": None})
        elif sequence_index < 1:
            errors.append("v7_sequence_index_invalid")
            checks.append({"id": "v7_sequence_index", "status": "error", "detail": sequence_index})
        else:
            checks.append({"id": "v7_sequence_index", "status": "ok", "detail": sequence_index})

        if sequence_total is None:
            warnings.append("v7_missing_sequence_total")
            checks.append({"id": "v7_sequence_total", "status": "warn", "detail": None})
        elif sequence_total < 1:
            errors.append("v7_sequence_total_invalid")
            checks.append({"id": "v7_sequence_total", "status": "error", "detail": sequence_total})
        else:
            checks.append({"id": "v7_sequence_total", "status": "ok", "detail": sequence_total})

        if (
            isinstance(sequence_index, int)
            and isinstance(sequence_total, int)
            and sequence_index > sequence_total
        ):
            errors.append("v7_sequence_index_gt_total")
            checks.append(
                {
                    "id": "v7_sequence_order",
                    "status": "error",
                    "detail": f"{sequence_index}>{sequence_total}",
                }
            )
        else:
            checks.append({"id": "v7_sequence_order", "status": "ok", "detail": "valid"})

    return {
        "ok": len(errors) == 0,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "checks": checks,
    }


def _prune_v7_sequence_cache_unlocked() -> None:
    if len(_v7_sequence_timing) <= _V7_SEQUENCE_MAX_TRACKED:
        return
    ordered = sorted(
        _v7_sequence_timing.items(),
        key=lambda kv: float(kv[1].get("last_update_ms", 0.0)),
    )
    to_drop = len(_v7_sequence_timing) - _V7_SEQUENCE_MAX_TRACKED
    for key, _ in ordered[:to_drop]:
        _v7_sequence_timing.pop(key, None)


def _compute_global_timeline_ms_unlocked(
    state: MicSessionState,
    *,
    now_epoch_ms: float | None = None,
) -> float | None:
    if state.orchestrator_mode != ORCHESTRATOR_MODE_V7:
        return None
    anchor_ms = _safe_float(state.global_timeline_anchor_epoch_ms)
    if not isinstance(anchor_ms, float):
        anchor_ms = _iso_to_epoch_ms(state.started_at) or _iso_to_epoch_ms(state.created_at)
    if not isinstance(anchor_ms, float):
        return None
    now_ms = float(now_epoch_ms) if isinstance(now_epoch_ms, (int, float)) else time.time() * 1000.0
    return round(max(0.0, now_ms - anchor_ms), 1)


def _build_orchestrator_payload(state: MicSessionState, *, now_epoch_ms: float | None = None) -> dict[str, Any]:
    with state._lock:
        payload = {
            "orchestrator_mode": state.orchestrator_mode,
            "event_contract_schema": MIC_V7_EVENT_SCHEMA,
            "event_contract_version": MIC_V7_EVENT_VERSION,
        }
        if state.orchestrator_mode != ORCHESTRATOR_MODE_V7:
            return payload
        payload.update(
            {
                "run_id": state.run_id,
                "sequence_id": state.sequence_id,
                "sequence_index": state.sequence_index,
                "sequence_total": state.sequence_total,
                "global_timeline_ms": _compute_global_timeline_ms_unlocked(state, now_epoch_ms=now_epoch_ms),
            }
        )
        return payload


def _prune_auto_sequence_cache_unlocked() -> None:
    if len(_auto_sequence_timing) <= _AUTO_SEQUENCE_MAX_TRACKED:
        return
    ordered = sorted(
        _auto_sequence_timing.items(),
        key=lambda kv: float(kv[1].get("last_update_ms", 0.0)),
    )
    to_drop = len(_auto_sequence_timing) - _AUTO_SEQUENCE_MAX_TRACKED
    for key, _ in ordered[:to_drop]:
        _auto_sequence_timing.pop(key, None)


def _update_sequence_timing_on_start(state: MicSessionState) -> dict[str, Any]:
    with state._lock:
        params = dict(state.model_params or {})
        created_at = state.created_at
        started_at = state.started_at

    meta = _parse_auto_sequence_meta(params)
    started_ms = _iso_to_epoch_ms(started_at)
    created_ms = _iso_to_epoch_ms(created_at)

    timing: dict[str, Any] = {
        "mobile_loop_enabled": meta["mobile_loop_enabled"],
        "planned_speech_s": round(float(meta["speech_s"]), 3) if isinstance(meta["speech_s"], (int, float)) else None,
        "planned_capture_speech_s": round(float(meta["capture_speech_s"]), 3) if isinstance(meta["capture_speech_s"], (int, float)) else None,
        "planned_early_stop_s": round(float(meta["early_stop_s"]), 3) if isinstance(meta["early_stop_s"], (int, float)) else None,
        "planned_pause_s": round(float(meta["pause_s"]), 3) if isinstance(meta["pause_s"], (int, float)) else None,
        "planned_audio_start_delay_s": round(float(meta["audio_start_delay_s"]), 3)
        if isinstance(meta["audio_start_delay_s"], (int, float))
        else 0.0,
        "planned_cycle_s": round(float(meta["cycle_s"]), 3) if isinstance(meta["cycle_s"], (int, float)) else None,
        "planned_slot_s": round(float(meta["slot_s"]), 3) if isinstance(meta["slot_s"], (int, float)) else None,
        "planned_hard_trial_base_s": round(float(meta["hard_trial_base_s"]), 3) if isinstance(meta["hard_trial_base_s"], (int, float)) else None,
        "planned_hard_trial_s": round(float(meta["hard_trial_s"]), 3) if isinstance(meta["hard_trial_s"], (int, float)) else None,
        "planned_effective_hard_trial_s": round(float(meta["effective_hard_trial_s"]), 3) if isinstance(meta["effective_hard_trial_s"], (int, float)) else None,
        "planned_silence_stop_s": round(float(meta["silence_stop_s"]), 3) if isinstance(meta["silence_stop_s"], (int, float)) else None,
        "planned_silence_min_elapsed_s": round(float(meta["silence_min_elapsed_s"]), 3) if isinstance(meta["silence_min_elapsed_s"], (int, float)) else None,
        "planned_silence_min_audio_fraction": round(float(meta["silence_min_audio_fraction"]), 3) if isinstance(meta["silence_min_audio_fraction"], (int, float)) else None,
        "planned_grace_s": round(float(meta["grace_s"]), 3) if isinstance(meta["grace_s"], (int, float)) else None,
        "planned_lead_start_s": round(float(meta["lead_start_s"]), 3) if isinstance(meta["lead_start_s"], (int, float)) else None,
        "mobile_loop_package_id": meta["mobile_loop_package_id"],
        "audio_start_source": meta["audio_start_source"],
        "audio_start_known": meta["audio_start_known"],
        "sequence_token": meta["token"],
        "sequence_index": meta["index"],
        "sequence_total": meta["total"],
        "sequence_queue": list(meta.get("queue_model_ids") or []),
        "created_at": created_at,
        "started_at": started_at,
        "created_to_started_ms": round(float(started_ms - created_ms), 1)
        if isinstance(started_ms, (int, float)) and isinstance(created_ms, (int, float))
        else None,
    }

    token = meta["token"]
    index = meta["index"]
    cycle_s = meta["cycle_s"]
    if (
        isinstance(started_ms, (int, float))
        and token
        and isinstance(index, int)
        and index >= 1
        and isinstance(cycle_s, (int, float))
        and cycle_s > 0
    ):
        with _auto_sequence_lock:
            seq = _auto_sequence_timing.get(token)
            if seq is None or index <= 1:
                seq = {
                    "anchor_start_ms": float(started_ms),
                    "last_start_ms": None,
                    "last_stop_ms": None,
                    "last_index": 0,
                    "planned_speech_s": float(meta["speech_s"]) if isinstance(meta["speech_s"], (int, float)) else None,
                    "planned_pause_s": float(meta["pause_s"]) if isinstance(meta["pause_s"], (int, float)) else None,
                    "planned_cycle_s": float(cycle_s),
                    "last_update_ms": time.time() * 1000.0,
                }
                _auto_sequence_timing[token] = seq
            prev_start_ms = _safe_float(seq.get("last_start_ms"))
            prev_stop_ms = _safe_float(seq.get("last_stop_ms"))
            anchor_start_ms = _safe_float(seq.get("anchor_start_ms")) or float(started_ms)
            expected_start_ms = anchor_start_ms + (index - 1) * float(cycle_s) * 1000.0
            audio_start_delay_s = _safe_float(meta.get("audio_start_delay_s")) or 0.0
            expected_audio_start_ms = expected_start_ms + float(audio_start_delay_s) * 1000.0
            timing["observed_start_epoch_ms"] = round(float(started_ms), 1)
            timing["expected_start_epoch_ms"] = round(float(expected_start_ms), 1)
            timing["expected_start_at"] = _epoch_ms_to_iso(expected_start_ms)
            timing["expected_audio_start_epoch_ms"] = round(float(expected_audio_start_ms), 1)
            timing["expected_audio_start_at"] = _epoch_ms_to_iso(expected_audio_start_ms)
            timing["start_drift_ms"] = round(float(started_ms - expected_start_ms), 1)
            if isinstance(prev_start_ms, (int, float)):
                observed_gap_s = (float(started_ms) - float(prev_start_ms)) / 1000.0
                expected_gap_s = float(cycle_s)
                timing["observed_start_gap_s"] = round(observed_gap_s, 3)
                timing["expected_start_gap_s"] = round(expected_gap_s, 3)
                timing["start_gap_error_s"] = round(observed_gap_s - expected_gap_s, 3)
            if isinstance(prev_stop_ms, (int, float)):
                observed_pause_s = (float(started_ms) - float(prev_stop_ms)) / 1000.0
                timing["observed_pause_after_prev_stop_s"] = round(observed_pause_s, 3)
            seq["last_start_ms"] = float(started_ms)
            seq["last_index"] = int(index)
            seq["planned_speech_s"] = float(meta["speech_s"]) if isinstance(meta["speech_s"], (int, float)) else seq.get("planned_speech_s")
            seq["planned_pause_s"] = float(meta["pause_s"]) if isinstance(meta["pause_s"], (int, float)) else seq.get("planned_pause_s")
            seq["planned_cycle_s"] = float(cycle_s)
            seq["last_update_ms"] = time.time() * 1000.0
            _prune_auto_sequence_cache_unlocked()

    with state._lock:
        state.sequence_timing = dict(timing)
    return timing


def _update_sequence_timing_on_stop(state: MicSessionState) -> dict[str, Any]:
    with state._lock:
        timing = dict(state.sequence_timing or {})
        params = dict(state.model_params or {})
        started_at = state.started_at
        stopped_at = state.stopped_at
        elapsed_s = state.elapsed_s

    meta = _parse_auto_sequence_meta(params)
    started_ms = _iso_to_epoch_ms(started_at)
    stopped_ms = _iso_to_epoch_ms(stopped_at)
    if stopped_at:
        timing["stopped_at"] = stopped_at
    if isinstance(started_ms, (int, float)) and isinstance(stopped_ms, (int, float)):
        timing["observed_duration_s"] = round(max(0.0, (stopped_ms - started_ms) / 1000.0), 3)
    elif isinstance(elapsed_s, (int, float)):
        timing["observed_duration_s"] = round(float(elapsed_s), 3)

    speech_s = meta["speech_s"]
    audio_start_delay_s = _safe_float(meta.get("audio_start_delay_s")) or 0.0
    if isinstance(speech_s, (int, float)) and isinstance(timing.get("observed_duration_s"), (int, float)):
        timing["duration_vs_speech_delta_s"] = round(float(timing["observed_duration_s"]) - float(speech_s), 3)
        timing["duration_vs_audio_plan_delta_s"] = round(
            float(timing["observed_duration_s"]) - (float(audio_start_delay_s) + float(speech_s)),
            3,
        )

    expected_start_ms = _safe_float(timing.get("expected_start_epoch_ms"))
    if isinstance(expected_start_ms, (int, float)) and isinstance(speech_s, (int, float)) and isinstance(stopped_ms, (int, float)):
        expected_stop_ms = float(expected_start_ms) + (float(audio_start_delay_s) + float(speech_s)) * 1000.0
        timing["expected_stop_at"] = _epoch_ms_to_iso(expected_stop_ms)
        timing["stop_drift_vs_planned_speech_ms"] = round(float(stopped_ms - expected_stop_ms), 1)

    token = meta["token"]
    if token and isinstance(stopped_ms, (int, float)):
        with _auto_sequence_lock:
            seq = _auto_sequence_timing.get(token)
            if seq is not None:
                seq["last_stop_ms"] = float(stopped_ms)
                seq["last_update_ms"] = time.time() * 1000.0
                _prune_auto_sequence_cache_unlocked()

    with state._lock:
        state.sequence_timing = dict(timing)
    return timing


def _update_v7_timing_on_start(state: MicSessionState) -> dict[str, Any]:
    with state._lock:
        if state.orchestrator_mode != ORCHESTRATOR_MODE_V7:
            return {}
        started_ms = _iso_to_epoch_ms(state.started_at)
        sequence_id = str(state.sequence_id or "").strip()
        sequence_index = state.sequence_index
        sequence_total = state.sequence_total
        run_id = state.run_id
        if not run_id:
            run_id = f"run_{state.session_id}"
            state.run_id = run_id
        if not sequence_id:
            sequence_id = state.session_id
            state.sequence_id = sequence_id

    anchor_ms: float | None = None
    if isinstance(started_ms, (int, float)):
        with _v7_sequence_lock:
            seq_entry = _v7_sequence_timing.get(sequence_id)
            if seq_entry is None or (isinstance(sequence_index, int) and sequence_index <= 1):
                seq_entry = {
                    "anchor_epoch_ms": float(started_ms),
                    "last_update_ms": time.time() * 1000.0,
                }
                _v7_sequence_timing[sequence_id] = seq_entry
            else:
                seq_entry["last_update_ms"] = time.time() * 1000.0
            _prune_v7_sequence_cache_unlocked()
            anchor_ms = _safe_float(seq_entry.get("anchor_epoch_ms"))

    with state._lock:
        if isinstance(anchor_ms, float):
            state.global_timeline_anchor_epoch_ms = anchor_ms
        elif state.global_timeline_anchor_epoch_ms is None and isinstance(started_ms, (int, float)):
            state.global_timeline_anchor_epoch_ms = float(started_ms)
        return {
            "event_contract_schema": MIC_V7_EVENT_SCHEMA,
            "event_contract_version": MIC_V7_EVENT_VERSION,
            "run_id": state.run_id,
            "sequence_id": state.sequence_id,
            "sequence_index": sequence_index,
            "sequence_total": sequence_total,
            "global_timeline_ms": _compute_global_timeline_ms_unlocked(state, now_epoch_ms=started_ms),
        }


# ---------------------------------------------------------------------------
# Trial classification & sequence report persistence
# ---------------------------------------------------------------------------

_FAIL_DROP_RATE = 0.35
_FAIL_FIRST_WORD_MS = 10_000.0
_FAIL_QUEUE_PEAK_S = 3.0
_BORDERLINE_DROP_RATE = 0.10
_BORDERLINE_RTF = 0.8
_BORDERLINE_FIRST_WORD_MS = 5_000.0


def classify_trial(state: MicSessionState) -> str:
    """Klasifikuje výsledek trialu: ok | borderline | too_slow_for_slot | fail."""
    drop = float(state.drop_rate or 0.0)
    rtf = float(state.rtf or 0.0)
    fw_ms = state.first_word_wall_ms
    q_peak = float(state.queue_depth_peak_s or 0.0)
    reason = _normalize_reason_code(state.reason_code, allow_none=True, fallback="unknown") or ""
    error = state.error or ""

    if (
        drop > _FAIL_DROP_RATE
        or (fw_ms is not None and fw_ms > _FAIL_FIRST_WORD_MS)
        or q_peak > _FAIL_QUEUE_PEAK_S
        or bool(error.strip())
        or reason in {"start_recording_failed", "backpressure_drop"} and drop > _FAIL_DROP_RATE
    ):
        return "fail"

    if (
        (fw_ms is not None and fw_ms > _FAIL_FIRST_WORD_MS)
        or q_peak > _FAIL_QUEUE_PEAK_S
    ):
        return "too_slow_for_slot"

    if (
        drop > _BORDERLINE_DROP_RATE
        or rtf > _BORDERLINE_RTF
        or (fw_ms is not None and fw_ms > _BORDERLINE_FIRST_WORD_MS)
    ):
        return "borderline"

    return "ok"


def _sequence_identity(state: MicSessionState) -> tuple[str, int | None, int | None]:
    timing = state.sequence_timing or {}
    token = str(timing.get("sequence_token") or "").strip()
    seq_index = _safe_int(timing.get("sequence_index"))
    seq_total = _safe_int(timing.get("sequence_total"))

    if token:
        return token, seq_index, seq_total

    meta = _parse_auto_sequence_meta(state.model_params or {})
    token = str(meta.get("token") or "").strip()
    if seq_index is None:
        seq_index = meta.get("index")
    if seq_total is None:
        seq_total = meta.get("total")
    if not token and state.orchestrator_mode == ORCHESTRATOR_MODE_V7:
        token = str(state.sequence_id or state.session_id or "").strip()
    if seq_index is None and state.orchestrator_mode == ORCHESTRATOR_MODE_V7:
        seq_index = _safe_int(state.sequence_index)
    if seq_total is None and state.orchestrator_mode == ORCHESTRATOR_MODE_V7:
        seq_total = _safe_int(state.sequence_total)
    return token, seq_index, seq_total


def _build_sequence_trial_entry(
    state: MicSessionState,
    *,
    phase: str,
    payload: dict[str, Any] | None,
    seq_index: int | None,
    seq_total: int | None,
) -> dict[str, Any]:
    p = payload or {}
    orchestrator = _build_orchestrator_payload(state)
    reason_code = _normalize_reason_code(p.get("reason_code", state.reason_code), allow_none=True)
    state_params = dict(state.model_params or {})
    sequence_meta = _parse_auto_sequence_meta(state_params)
    sequence_timing = p.get("sequence_timing")
    if not isinstance(sequence_timing, dict):
        sequence_timing = dict(state.sequence_timing or {})
    raw_model_params_used = p.get("model_params_used")
    if not isinstance(raw_model_params_used, dict):
        raw_model_params_used = state_params.get("model_params_used")
    raw_common_params_used = p.get("sequence_common_params_used")
    if not isinstance(raw_common_params_used, dict):
        raw_common_params_used = state_params.get("sequence_common_params_used")
    raw_param_profile = p.get("sequence_param_profile", state_params.get("sequence_param_profile"))
    entry = {
        "seq_index": seq_index,
        "seq_total": seq_total,
        "session_id": state.session_id,
        "model_id": state.model_id,
        "model_params_used": dict(raw_model_params_used or {}) if isinstance(raw_model_params_used, dict) else {},
        "sequence_common_params_enabled": bool(state_params.get("sequence_common_params_enabled")),
        "sequence_common_params_used": dict(raw_common_params_used or {}) if isinstance(raw_common_params_used, dict) else {},
        "sequence_param_profile": str(raw_param_profile).strip() if raw_param_profile else None,
        "sequence_timing": dict(sequence_timing),
        **_sequence_plan_payload_from_meta(sequence_meta),
        "orchestrator_mode": orchestrator.get("orchestrator_mode"),
        "run_id": orchestrator.get("run_id"),
        "sequence_id": orchestrator.get("sequence_id"),
        "global_timeline_ms": orchestrator.get("global_timeline_ms"),
        "phase": str(phase),
        "status": state.status,
        "created_at": state.created_at,
        "started_at": state.started_at,
        "stopped_at": state.stopped_at,
        "updated_at": _iso_now(),
        "trial_status": classify_trial(state),
        "rtf": p.get("rtf", state.rtf),
        "elapsed_s": p.get("elapsed_s", state.elapsed_s),
        "total_audio_s": p.get("total_audio_s", state.total_audio_s),
        "drop_rate": p.get("drop_rate", state.drop_rate),
        "first_word_latency_ms": p.get("first_word_latency_ms", state.first_word_latency_ms),
        "first_word_wall_ms": p.get("first_word_wall_ms", state.first_word_wall_ms),
        "first_word_audio_ms": p.get("first_word_audio_ms", state.first_word_audio_ms),
        "segment_finalize_ms_p50": p.get("segment_finalize_ms_p50", state.segment_finalize_ms_p50),
        "segment_finalize_ms_p95": p.get("segment_finalize_ms_p95", state.segment_finalize_ms_p95),
        "queue_depth_peak_s": p.get("queue_depth_peak_s", state.queue_depth_peak_s),
        "backpressure_events": p.get("backpressure_events", state.backpressure_events),
        "worker_rss_peak_mb": p.get("worker_rss_peak_mb", state.worker_rss_peak_mb),
        "reason_code": reason_code,
        "error": p.get("error", state.error),
        "segment_id": _session_segment_id(state),
        "event_contract_schema": MIC_V7_EVENT_SCHEMA,
        "event_contract_version": MIC_V7_EVENT_VERSION,
        "reason_known": reason_code in MIC_V7_REASON_CODES if reason_code else True,
    }
    entry.update(compute_trial_kpi(entry, hard_limit_ms=_V7_LATENCY_HARD_LIMIT_MS))
    entry.update(_sequence_timing_flat_fields(entry.get("sequence_timing")))
    return entry


def _sequence_timing_flat_fields(sequence_timing: Any) -> dict[str, Any]:
    if not isinstance(sequence_timing, dict):
        return {}
    planned_pause_s = _safe_float(sequence_timing.get("planned_pause_s"))
    observed_pause_s = _safe_float(sequence_timing.get("observed_pause_after_prev_stop_s"))
    fields: dict[str, Any] = {
        "planned_pause_s": round(float(planned_pause_s), 3) if isinstance(planned_pause_s, float) else None,
        "planned_audio_start_delay_s": _safe_float(sequence_timing.get("planned_audio_start_delay_s")),
        "observed_pause_after_prev_stop_s": round(float(observed_pause_s), 3) if isinstance(observed_pause_s, float) else None,
        "observed_start_gap_s": _safe_float(sequence_timing.get("observed_start_gap_s")),
        "start_gap_error_s": _safe_float(sequence_timing.get("start_gap_error_s")),
        "observed_duration_s": _safe_float(sequence_timing.get("observed_duration_s")),
        "duration_vs_audio_plan_delta_s": _safe_float(sequence_timing.get("duration_vs_audio_plan_delta_s")),
        "stop_drift_vs_planned_speech_ms": _safe_float(sequence_timing.get("stop_drift_vs_planned_speech_ms")),
    }
    if isinstance(planned_pause_s, float) and isinstance(observed_pause_s, float):
        fields["pause_deviation_s"] = round(float(observed_pause_s - planned_pause_s), 3)
    else:
        fields["pause_deviation_s"] = None
    return fields


def _sequence_trial_float(trial: dict[str, Any], flat_key: str, timing_key: str | None = None) -> float | None:
    value = _safe_float(trial.get(flat_key))
    if isinstance(value, float):
        return value
    timing = trial.get("sequence_timing")
    if isinstance(timing, dict):
        value = _safe_float(timing.get(timing_key or flat_key))
        if isinstance(value, float):
            return value
    return None


def _build_sequence_pause_validation(trials: list[dict[str, Any]], *, tolerance_s: float = 2.0) -> dict[str, Any]:
    checked: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    planned_values: set[float] = set()
    observed_values: list[float] = []
    max_abs_deviation: float | None = None

    for trial in sorted(trials, key=lambda t: t.get("seq_index") if isinstance(t.get("seq_index"), int) else 10**9):
        planned_pause_s = _sequence_trial_float(trial, "planned_pause_s", "planned_pause_s")
        observed_pause_s = _sequence_trial_float(trial, "observed_pause_after_prev_stop_s", "observed_pause_after_prev_stop_s")
        if not isinstance(planned_pause_s, float) or not isinstance(observed_pause_s, float):
            continue
        planned_pause_s = round(planned_pause_s, 3)
        observed_pause_s = round(observed_pause_s, 3)
        deviation_s = round(observed_pause_s - planned_pause_s, 3)
        abs_deviation = abs(deviation_s)
        planned_values.add(planned_pause_s)
        observed_values.append(observed_pause_s)
        max_abs_deviation = abs_deviation if max_abs_deviation is None else max(max_abs_deviation, abs_deviation)
        row = {
            "seq_index": trial.get("seq_index"),
            "model_id": trial.get("model_id"),
            "planned_pause_s": planned_pause_s,
            "observed_pause_s": observed_pause_s,
            "deviation_s": deviation_s,
        }
        checked.append(row)
        if abs_deviation > tolerance_s:
            violations.append(row)

    planned_sorted = sorted(planned_values)
    return {
        "ok": len(violations) == 0,
        "checked_points": len(checked),
        "tolerance_s": round(float(tolerance_s), 3),
        "planned_pause_s": planned_sorted[0] if len(planned_sorted) == 1 else None,
        "planned_pause_values_s": planned_sorted,
        "observed_pause_min_s": round(min(observed_values), 3) if observed_values else None,
        "observed_pause_max_s": round(max(observed_values), 3) if observed_values else None,
        "max_abs_deviation_s": round(max_abs_deviation, 3) if isinstance(max_abs_deviation, float) else None,
        "violations": violations,
    }


def _trial_brief(trial: dict[str, Any]) -> dict[str, Any]:
    return {
        "seq_index": trial.get("seq_index"),
        "model_id": trial.get("model_id"),
        "trial_status": trial.get("trial_status"),
        "rtf": _safe_float(trial.get("rtf")),
        "drop_rate": _safe_float(trial.get("drop_rate")),
        "reason_code": _normalize_reason_code(trial.get("reason_code"), allow_none=True, fallback="unknown"),
    }


def _build_sequence_conclusion(
    trials: list[dict[str, Any]],
    *,
    pause_validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    finalized_trials = [trial for trial in trials if trial.get("stopped_at") or str(trial.get("phase") or "") == "final"]
    usable: list[dict[str, Any]] = []
    borderline: list[dict[str, Any]] = []
    performance_failed: list[dict[str, Any]] = []
    quality_not_reliable: list[dict[str, Any]] = []

    for trial in finalized_trials:
        status = str(trial.get("trial_status") or "")
        reason = _normalize_reason_code(trial.get("reason_code"), allow_none=True, fallback="unknown") or ""
        drop = _safe_float(trial.get("drop_rate"))
        rtf = _safe_float(trial.get("rtf"))
        drop_value = drop if isinstance(drop, float) else 0.0
        rtf_value = rtf if isinstance(rtf, float) else None
        brief = _trial_brief(trial)

        if drop_value > _BORDERLINE_DROP_RATE:
            quality_not_reliable.append(brief)

        if (
            status == "fail"
            or drop_value > _FAIL_DROP_RATE
            or reason == "backpressure_drop" and drop_value > _FAIL_DROP_RATE
            or (isinstance(rtf_value, float) and rtf_value > 2.0)
        ):
            performance_failed.append(brief)
            continue

        if (
            status == "ok"
            or (
                status == "borderline"
                and drop_value <= _BORDERLINE_DROP_RATE
                and (rtf_value is None or rtf_value <= 1.2)
            )
        ):
            usable.append(brief)
            continue

        if status in {"borderline", "too_slow_for_slot"}:
            borderline.append(brief)

    def rank_key(item: dict[str, Any]) -> tuple[float, float, float]:
        drop = item.get("drop_rate") if isinstance(item.get("drop_rate"), float) else 1.0
        rtf = item.get("rtf") if isinstance(item.get("rtf"), float) else 999.0
        seq = item.get("seq_index") if isinstance(item.get("seq_index"), int) else 10**9
        return (float(drop), abs(float(rtf) - 1.0), float(seq))

    ranked_usable = sorted(usable, key=rank_key)
    ranked_borderline = sorted(borderline, key=rank_key)
    best = ranked_usable[0] if ranked_usable else (ranked_borderline[0] if ranked_borderline else None)

    notes: list[str] = []
    if pause_validation and pause_validation.get("ok") is False:
        max_dev = _safe_float(pause_validation.get("max_abs_deviation_s"))
        if isinstance(max_dev, float):
            notes.append(f"Pauzy mimo plán; max odchylka {max_dev:.1f}s. Porovnání opakovat po kontrole časování.")
        else:
            notes.append("Pauzy mimo plán. Porovnání opakovat po kontrole časování.")
    if quality_not_reliable:
        notes.append("Modely s dropem nad 10 % nehodnotit textově bez opakování, protože část audia byla zahozena.")
    if not finalized_trials:
        headline = "Zatím není dokončený žádný trial."
    elif ranked_usable:
        headline = f"Použitelný online model: {ranked_usable[0].get('model_id')}."
    elif ranked_borderline:
        headline = f"Nejbližší použitelnému je hraniční model: {ranked_borderline[0].get('model_id')}."
    else:
        headline = "V tomto nastavení není technicky použitelný žádný model."

    return {
        "headline": headline,
        "best_model_id": best.get("model_id") if best else None,
        "usable_models": ranked_usable,
        "borderline_models": ranked_borderline,
        "performance_failed_models": performance_failed,
        "quality_not_reliable_models": quality_not_reliable,
        "notes": notes,
    }


def _build_sequence_summary(trials: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"ok": 0, "borderline": 0, "too_slow_for_slot": 0, "fail": 0}
    running = 0
    finalized = 0
    reasons: dict[str, int] = {}
    rtf_values: list[float] = []
    drop_values: list[float] = []
    latency_values: list[float] = []
    quality_values: list[float] = []
    hw_values: list[float] = []
    unknown_reason_codes = 0

    for trial in trials:
        status = str(trial.get("trial_status") or "")
        if status in counts:
            counts[status] += 1
        if str(trial.get("status") or "") == "recording":
            running += 1
        if trial.get("stopped_at"):
            finalized += 1
        reason_code = _normalize_reason_code(trial.get("reason_code"), allow_none=True, fallback="unknown")
        if reason_code:
            reasons[reason_code] = reasons.get(reason_code, 0) + 1
            if reason_code not in MIC_V7_REASON_CODES:
                unknown_reason_codes += 1

        rtf = _safe_float(trial.get("rtf"))
        if isinstance(rtf, float):
            rtf_values.append(rtf)
        drop = _safe_float(trial.get("drop_rate"))
        if isinstance(drop, float):
            drop_values.append(drop)
        latency_ms = _safe_float(trial.get("latency_ms"))
        if isinstance(latency_ms, float):
            latency_values.append(latency_ms)
        quality = _safe_float(trial.get("quality_score"))
        if isinstance(quality, float):
            quality_values.append(quality)
        hw = _safe_float(trial.get("worker_rss_peak_mb"))
        if isinstance(hw, float):
            hw_values.append(hw)

    avg_rtf = round(sum(rtf_values) / len(rtf_values), 4) if rtf_values else None
    avg_drop = round(sum(drop_values) / len(drop_values), 4) if drop_values else None
    return {
        "counts": counts,
        "running": running,
        "finalized": finalized,
        "reasons": reasons,
        "avg_rtf": avg_rtf,
        "avg_drop_rate": avg_drop,
        "avg_latency_ms": round(sum(latency_values) / len(latency_values), 1) if latency_values else None,
        "avg_quality_score": round(sum(quality_values) / len(quality_values), 4) if quality_values else None,
        "avg_worker_rss_peak_mb": round(sum(hw_values) / len(hw_values), 1) if hw_values else None,
        "unknown_reason_codes": unknown_reason_codes,
    }


def _persist_sequence_report(
    state: MicSessionState,
    payload: dict[str, Any] | None = None,
    *,
    phase: str = "final",
) -> None:
    """Uloží / aktualizuje report.json + report.csv pro sekvenci průběžně i finálně."""
    token, seq_index, seq_total = _sequence_identity(state)
    if not token:
        return

    seq_dir = MIC_SEQUENCES_ROOT / token
    try:
        seq_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    report_path = seq_dir / "report.json"
    csv_path = seq_dir / "report.csv"

    report: dict[str, Any] = {}
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            report = {}

    trials_raw = report.get("trials")
    trials: list[dict[str, Any]] = list(trials_raw) if isinstance(trials_raw, list) else []
    trial_entry = _build_sequence_trial_entry(
        state,
        phase=phase,
        payload=payload,
        seq_index=seq_index,
        seq_total=seq_total,
    )

    replaced = False
    if seq_index is not None:
        for i, trial in enumerate(trials):
            if trial.get("seq_index") == seq_index and trial.get("model_id") == state.model_id:
                trials[i] = trial_entry
                replaced = True
                break
    if not replaced:
        for i, trial in enumerate(trials):
            if trial.get("session_id") == state.session_id:
                trials[i] = trial_entry
                replaced = True
                break
    if not replaced:
        trials.append(trial_entry)

    trials_sorted = sorted(
        trials,
        key=lambda t: (
            t.get("seq_index") if isinstance(t.get("seq_index"), int) else 10**9,
            str(t.get("model_id") or ""),
            str(t.get("session_id") or ""),
        ),
    )

    sequence_total = seq_total
    if sequence_total is None:
        sequence_total = _safe_int(report.get("sequence_total"))
    if sequence_total is None:
        for trial in trials_sorted:
            maybe_total = _safe_int(trial.get("seq_total"))
            if isinstance(maybe_total, int):
                sequence_total = maybe_total
                break

    timeline_validation = validate_timeline_monotonic(
        trials_sorted,
        timeline_key="global_timeline_ms",
        seq_key="seq_index",
    )
    kpi_summary = compute_kpi_summary(trials_sorted, hard_limit_ms=_V7_LATENCY_HARD_LIMIT_MS)
    pause_validation = _build_sequence_pause_validation(trials_sorted)
    conclusion = _build_sequence_conclusion(trials_sorted, pause_validation=pause_validation)
    summary = _build_sequence_summary(trials_sorted)
    summary["timeline_validation"] = timeline_validation
    summary["kpi"] = kpi_summary
    summary["pause_validation"] = pause_validation
    summary["conclusion"] = conclusion
    sequence_plan = _sequence_plan_payload_from_meta(_parse_auto_sequence_meta(state.model_params or {}))
    if not any(v not in (None, [], "") for v in sequence_plan.values()):
        previous_plan = report.get("sequence_plan")
        sequence_plan = dict(previous_plan) if isinstance(previous_plan, dict) else sequence_plan
    readiness = evaluate_v7_readiness(
        trials=trials_sorted,
        timeline_validation=timeline_validation,
        kpi_summary=kpi_summary,
        min_models=3,
        require_v7_mode=False,
        require_finalized=False,
    )

    report = {
        "sequence_token": token,
        "updated_at": _iso_now(),
        "sequence_total": sequence_total,
        "trials_count": len(trials_sorted),
        "contract": build_contract_metadata(),
        "timeline_validation": timeline_validation,
        "pause_validation": pause_validation,
        "conclusion": conclusion,
        "kpi": kpi_summary,
        "readiness": readiness,
        "sequence_plan": sequence_plan,
        "summary": summary,
        "trials": trials_sorted,
    }

    try:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return

    csv_cols = [
        "seq_index",
        "seq_total",
        "model_id",
        "sequence_param_profile",
        "tuning_series_id",
        "tuning_mode",
        "tuning_step_size",
        "tuning_slot_index",
        "tuning_slot_total",
        "tuning_variant_id",
        "tuning_variant_label",
        "tuning_repeat_index",
        "tuning_repeat_total",
        "tuning_changed_params",
        "tuning_baseline_params",
        "tuning_max_lag_s",
        "mobile_loop_speech_s",
        "mobile_loop_capture_speech_s",
        "mobile_loop_early_stop_s",
        "mobile_loop_pause_s",
        "mobile_loop_audio_start_delay_s",
        "mobile_loop_cycle_s",
        "mobile_loop_package_id",
        "planned_pause_s",
        "planned_audio_start_delay_s",
        "observed_pause_after_prev_stop_s",
        "pause_deviation_s",
        "observed_start_gap_s",
        "start_gap_error_s",
        "observed_duration_s",
        "duration_vs_audio_plan_delta_s",
        "stop_drift_vs_planned_speech_ms",
        "auto_model_sequence_slot_s",
        "auto_model_sequence_hard_trial_base_s",
        "auto_model_sequence_hard_trial_s",
        "auto_model_sequence_effective_hard_trial_s",
        "auto_model_sequence_silence_stop_s",
        "auto_model_sequence_silence_min_elapsed_s",
        "auto_model_sequence_silence_min_audio_fraction",
        "auto_model_sequence_grace_s",
        "orchestrator_mode",
        "event_contract_schema",
        "event_contract_version",
        "run_id",
        "sequence_id",
        "segment_id",
        "global_timeline_ms",
        "phase",
        "status",
        "trial_status",
        "latency_ms",
        "latency_lane",
        "latency_hard_violation",
        "quality_score",
        "rtf",
        "drop_rate",
        "first_word_wall_ms",
        "segment_finalize_ms_p50",
        "segment_finalize_ms_p95",
        "queue_depth_peak_s",
        "backpressure_events",
        "worker_rss_peak_mb",
        "elapsed_s",
        "total_audio_s",
        "reason_code",
        "reason_known",
        "error",
        "session_id",
        "created_at",
        "started_at",
        "stopped_at",
        "updated_at",
    ]
    try:
        sio = io.StringIO()
        writer = csv.writer(sio, lineterminator="\n")
        writer.writerow(csv_cols)
        for trial in trials_sorted:
            row: list[str] = []
            for col in csv_cols:
                value = trial.get(col, "")
                if isinstance(value, (dict, list)):
                    row.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
                else:
                    row.append("" if value is None else str(value))
            writer.writerow(row)
        csv_path.write_text(sio.getvalue(), encoding="utf-8")
    except Exception:
        pass


def _maybe_persist_sequence_progress(
    state: MicSessionState,
    *,
    phase: str = "partial",
    force: bool = False,
) -> None:
    token, _, _ = _sequence_identity(state)
    if not token:
        return

    now_perf = time.perf_counter()
    with state._lock:
        if not force and state._last_sequence_report_persist_perf > 0:
            if (now_perf - state._last_sequence_report_persist_perf) < 1.0:
                return
        state._last_sequence_report_persist_perf = now_perf

        elapsed_s = state.elapsed_s
        if state.status == "recording" and state._started_perf > 0:
            elapsed_s = max(0.0, now_perf - state._started_perf)
        total_audio_s = state.total_audio_s
        if state.target_sample_rate > 0:
            total_audio_s = state._total_samples / max(1, state.target_sample_rate)
        rtf = state.rtf
        if total_audio_s > 0:
            rtf = elapsed_s / max(0.1, total_audio_s)
        payload = {
            "rtf": round(float(rtf), 4) if isinstance(rtf, (int, float)) else None,
            "elapsed_s": round(float(elapsed_s), 3) if isinstance(elapsed_s, (int, float)) else None,
            "total_audio_s": round(float(total_audio_s), 2) if isinstance(total_audio_s, (int, float)) else None,
            "drop_rate": round(float(state.drop_rate), 4) if isinstance(state.drop_rate, (int, float)) else None,
            "first_word_latency_ms": state.first_word_latency_ms,
            "first_word_wall_ms": state.first_word_wall_ms,
            "first_word_audio_ms": state.first_word_audio_ms,
            "segment_finalize_ms_p50": state.segment_finalize_ms_p50,
            "segment_finalize_ms_p95": state.segment_finalize_ms_p95,
            "queue_depth_peak_s": state.queue_depth_peak_s,
            "backpressure_events": state.backpressure_events,
            "worker_rss_peak_mb": state.worker_rss_peak_mb,
            "reason_code": _normalize_reason_code(state.reason_code, allow_none=True, fallback="unknown"),
            "error": state.error,
        }

    _persist_sequence_report(state, payload, phase=phase)


def get_sequence_report(token: str) -> dict[str, Any] | None:
    """Vrátí sequence report dict, nebo None pokud neexistuje."""
    path = MIC_SEQUENCES_ROOT / token / "report.json"
    if not path.exists():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(report, dict):
        return None

    trials_raw = report.get("trials")
    trials = list(trials_raw) if isinstance(trials_raw, list) else []
    if trials:
        for idx, trial in enumerate(trials):
            if not isinstance(trial, dict):
                continue
            trial.setdefault("seq_index", idx + 1)
            trial.setdefault("event_contract_schema", MIC_V7_EVENT_SCHEMA)
            trial.setdefault("event_contract_version", MIC_V7_EVENT_VERSION)
            trial["reason_code"] = _normalize_reason_code(trial.get("reason_code"), allow_none=True, fallback="unknown")
            trial["reason_known"] = trial["reason_code"] in MIC_V7_REASON_CODES if trial.get("reason_code") else True
            trial.update(compute_trial_kpi(trial, hard_limit_ms=_V7_LATENCY_HARD_LIMIT_MS))
            trial.update(_sequence_timing_flat_fields(trial.get("sequence_timing")))

        timeline_validation = validate_timeline_monotonic(trials, timeline_key="global_timeline_ms", seq_key="seq_index")
        kpi_summary = compute_kpi_summary(trials, hard_limit_ms=_V7_LATENCY_HARD_LIMIT_MS)
        pause_validation = _build_sequence_pause_validation(trials)
        conclusion = _build_sequence_conclusion(trials, pause_validation=pause_validation)
        readiness = evaluate_v7_readiness(
            trials=trials,
            timeline_validation=timeline_validation,
            kpi_summary=kpi_summary,
            min_models=3,
            require_v7_mode=False,
            require_finalized=False,
        )
        summary = report.get("summary")
        if not isinstance(summary, dict):
            summary = _build_sequence_summary(trials)
        summary["timeline_validation"] = timeline_validation
        summary["kpi"] = kpi_summary
        summary["pause_validation"] = pause_validation
        summary["conclusion"] = conclusion
        report["summary"] = summary
        report["timeline_validation"] = timeline_validation
        report["pause_validation"] = pause_validation
        report["conclusion"] = conclusion
        report["kpi"] = kpi_summary
        report["readiness"] = readiness
        if not isinstance(report.get("sequence_plan"), dict):
            first_plan: dict[str, Any] = {}
            if isinstance(trials[0], dict):
                plan_keys = [
                    "mobile_loop_enabled",
                    "mobile_loop_speech_s",
                    "mobile_loop_capture_speech_s",
                    "mobile_loop_early_stop_s",
                    "mobile_loop_pause_s",
                    "mobile_loop_cycle_s",
                    "mobile_loop_package_id",
                    "tuning_series_id",
                    "tuning_mode",
                    "tuning_step_size",
                    "tuning_slot_total",
                    "tuning_repeat_total",
                    "tuning_max_lag_s",
                    "auto_model_sequence_slot_s",
                    "auto_model_sequence_hard_trial_s",
                    "auto_model_sequence_silence_stop_s",
                    "auto_model_sequence_grace_s",
                ]
                first_plan = {key: trials[0].get(key) for key in plan_keys if key in trials[0]}
            report["sequence_plan"] = first_plan

    report["contract"] = build_contract_metadata()
    report["trials"] = trials
    return report


def get_sequence_report_csv_path(token: str) -> Path | None:
    """Vrátí cestu k report.csv, nebo None."""
    path = MIC_SEQUENCES_ROOT / token / "report.csv"
    return path if path.exists() else None


def get_sequence_readiness(token: str, *, min_models: int = 3) -> dict[str, Any] | None:
    report = get_sequence_report(token)
    if report is None:
        return None
    trials = report.get("trials")
    if not isinstance(trials, list):
        trials = []
    timeline = report.get("timeline_validation")
    if not isinstance(timeline, dict):
        timeline = validate_timeline_monotonic(trials, timeline_key="global_timeline_ms", seq_key="seq_index")
    kpi = report.get("kpi")
    if not isinstance(kpi, dict):
        kpi = compute_kpi_summary(trials, hard_limit_ms=_V7_LATENCY_HARD_LIMIT_MS)
    readiness = evaluate_v7_readiness(
        trials=trials,
        timeline_validation=timeline,
        kpi_summary=kpi,
        min_models=max(1, int(min_models)),
        require_v7_mode=False,
        require_finalized=False,
    )
    return {
        "sequence_token": token,
        "contract": build_contract_metadata(),
        "timeline_validation": timeline,
        "kpi": kpi,
        "readiness": readiness,
    }


_DIAGNOSTIC_SEQUENCE_TOKENS = {"seq_abc", "shared_seq", "tok_tuning_unit"}


def _sequence_token_from_event(event: dict[str, Any]) -> str:
    for key in ("sequence_id", "auto_model_sequence_token", "sequence_token"):
        value = str(event.get(key) or "").strip()
        if value:
            return value
    return ""


def _is_diagnostic_sequence_token(token: str | None) -> bool:
    text = str(token or "").strip().lower()
    if not text:
        return False
    return text in _DIAGNOSTIC_SEQUENCE_TOKENS or text.startswith(("_test", "test_", "unit_"))


def _is_legacy_client_contract_event(event: dict[str, Any]) -> bool:
    return (
        event.get("contract_valid") is False
        and str(event.get("event") or "").strip() == "client_sequence_event"
        and bool(event.get("client_event"))
    )


def _sequence_report_is_complete(report: dict[str, Any]) -> bool:
    trials = report.get("trials")
    trial_count = len(trials) if isinstance(trials, list) else int(report.get("trials_count") or 0)
    sequence_total = int(report.get("sequence_total") or trial_count or 0)
    if sequence_total > 0 and trial_count < sequence_total:
        return False
    if not isinstance(trials, list) or not trials:
        return False
    return any(
        bool(t.get("started_at") or t.get("stopped_at"))
        or str(t.get("status") or "").strip().lower() in {"recording", "stopped", "finalized"}
        for t in trials
        if isinstance(t, dict)
    )


def get_v7_runtime_mapping_status(*, max_reports: int = 80, max_events: int = 2500) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    if MIC_EVENTS_LOG_PATH.exists():
        try:
            lines = MIC_EVENTS_LOG_PATH.read_text(encoding="utf-8").splitlines()
            for raw in lines[-max(1, int(max_events)):]:
                if not raw.strip():
                    continue
                try:
                    row = json.loads(raw)
                except Exception:
                    continue
                if isinstance(row, dict):
                    events.append(row)
        except Exception:
            events = []

    event_types: dict[str, int] = {}
    v7_events = 0
    invalid_contract_events = 0
    legacy_invalid_contract_events_ignored = 0
    diagnostic_events_ignored = 0
    unknown_reason_events = 0
    missing_field_events = 0
    last_event_ts: str | None = None
    for event in events:
        if _is_diagnostic_sequence_token(_sequence_token_from_event(event)):
            diagnostic_events_ignored += 1
            continue
        event_name = str(event.get("event") or "").strip() or "unknown"
        event_types[event_name] = event_types.get(event_name, 0) + 1
        last_event_ts = str(event.get("ts") or last_event_ts or "")
        mode = str(event.get("orchestrator_mode") or "").strip()
        if mode == ORCHESTRATOR_MODE_V7:
            v7_events += 1
        contract_valid = event.get("contract_valid")
        if contract_valid is False:
            if _is_legacy_client_contract_event(event):
                legacy_invalid_contract_events_ignored += 1
            else:
                invalid_contract_events += 1
        if event.get("reason_known") is False:
            unknown_reason_events += 1
        missing = event.get("contract_missing_fields")
        if isinstance(missing, list):
            missing_field_events += len(missing)

    report_files = sorted(
        MIC_SEQUENCES_ROOT.glob("*/report.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    report_tokens: list[str] = []
    timeline_fail = 0
    readiness_fail = 0
    incomplete_reports = 0
    diagnostic_reports_ignored = 0
    latest_sequence_updated_at: str | None = None
    latest_sequence_token: str | None = None

    for report_path in report_files[: max(1, int(max_reports))]:
        token = report_path.parent.name
        if _is_diagnostic_sequence_token(token):
            diagnostic_reports_ignored += 1
            continue
        report_tokens.append(token)
        report = get_sequence_report(token)
        if not isinstance(report, dict):
            continue
        is_complete = _sequence_report_is_complete(report)
        if not is_complete:
            incomplete_reports += 1
        timeline = report.get("timeline_validation")
        if is_complete and isinstance(timeline, dict) and not bool(timeline.get("ok")):
            timeline_fail += 1
        readiness = report.get("readiness")
        readiness_pass = bool(readiness.get("pass")) if isinstance(readiness, dict) else False
        if is_complete and not readiness_pass:
            readiness_fail += 1
        updated_at = str(report.get("updated_at") or "").strip()
        if latest_sequence_updated_at is None and updated_at:
            latest_sequence_updated_at = updated_at
            latest_sequence_token = token

    if v7_events == 0 and not report_tokens:
        status = "missing"
    elif invalid_contract_events > 0 or timeline_fail > 0:
        status = "error"
    elif readiness_fail > 0 or unknown_reason_events > 0 or missing_field_events > 0:
        status = "warn"
    else:
        status = "ok"

    return {
        "status": status,
        "generated_at_utc": _iso_now(),
        "contract": build_contract_metadata(),
        "events_total": len(events),
        "events_v7_total": v7_events,
        "invalid_contract_events": invalid_contract_events,
        "legacy_invalid_contract_events_ignored": legacy_invalid_contract_events_ignored,
        "diagnostic_events_ignored": diagnostic_events_ignored,
        "unknown_reason_events": unknown_reason_events,
        "missing_required_field_events": missing_field_events,
        "last_event_ts": last_event_ts,
        "event_types": event_types,
        "sequence_reports_total": len(report_tokens),
        "timeline_fail_reports": timeline_fail,
        "readiness_fail_reports": readiness_fail,
        "incomplete_sequence_reports": incomplete_reports,
        "diagnostic_sequence_reports_ignored": diagnostic_reports_ignored,
        "latest_sequence_token": latest_sequence_token,
        "latest_sequence_updated_at": latest_sequence_updated_at,
    }


def _append_mic_event(
    event: str,
    *,
    state: MicSessionState | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "ts": _iso_now(),
        "event": str(event),
    }
    now_epoch_ms = time.time() * 1000.0
    if state is not None:
        with state._lock:
            payload.update(
                {
                    "session_id": state.session_id,
                    "model_id": state.model_id,
                    "status": state.status,
                    "reason_code": state.reason_code,
                    "error": state.error,
                    "created_at": state.created_at,
                    "started_at": state.started_at,
                    "stopped_at": state.stopped_at,
                    "orchestrator_mode": state.orchestrator_mode,
                    "segment_id": _session_segment_id(state),
                }
            )
            if state.orchestrator_mode == ORCHESTRATOR_MODE_V7:
                payload.update(
                    {
                        "run_id": state.run_id,
                        "sequence_id": state.sequence_id,
                        "sequence_index": state.sequence_index,
                        "sequence_total": state.sequence_total,
                        "global_timeline_ms": _compute_global_timeline_ms_unlocked(state, now_epoch_ms=now_epoch_ms),
                    }
                )
    if extra:
        payload.update(extra)
    payload["reason_code"] = _normalize_reason_code(payload.get("reason_code"), allow_none=True, fallback="unknown")
    payload = apply_v7_event_contract(payload)
    try:
        MIC_EVENTS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with MIC_EVENTS_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False))
            fh.write("\n")
    except Exception:
        # Event log je best-effort, nesmí blokovat mic pipeline.
        return


def _build_session_snapshot(
    state: MicSessionState,
    *,
    phase: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with state._lock:
        payload: dict[str, Any] = {
            "session_id": state.session_id,
            "phase": phase,
            "snapshot_at": _iso_now(),
            "created_at": state.created_at,
            "started_at": state.started_at,
            "stopped_at": state.stopped_at,
            "model_id": state.model_id,
            "model_params": dict(state.model_params or {}),
            "status": state.status,
            "transcript": state.transcript,
            "partial": state.partial,
            "first_word_latency_ms": state.first_word_latency_ms,
            "first_word_wall_ms": state.first_word_wall_ms,
            "first_word_audio_ms": state.first_word_audio_ms,
            "first_token_ms_p50": state.first_token_ms_p50,
            "first_token_ms_p95": state.first_token_ms_p95,
            "segment_finalize_ms_p50": state.segment_finalize_ms_p50,
            "segment_finalize_ms_p95": state.segment_finalize_ms_p95,
            "processing_ms_p50": state.processing_ms_p50,
            "processing_ms_p95": state.processing_ms_p95,
            "capture_jitter_ms_p50": state.capture_jitter_ms_p50,
            "capture_jitter_ms_p95": state.capture_jitter_ms_p95,
            "capture_lag_ms_p50": state.capture_lag_ms_p50,
            "capture_lag_ms_p95": state.capture_lag_ms_p95,
            "drop_rate": state.drop_rate,
            "session_resets": state.session_resets,
            "worker_rss_peak_mb": state.worker_rss_peak_mb,
            "sample_rate": state.target_sample_rate,
            "input_gain_db": state.input_gain_db,
            "queue_high_watermark_s": state.queue_high_watermark_s,
            "queue_low_watermark_s": state.queue_low_watermark_s,
            "queue_depth_s": state.queue_depth_s,
            "queue_depth_peak_s": state.queue_depth_peak_s,
            "backpressure_events": state.backpressure_events,
            "backpressure_active": state.backpressure_active,
            "chunk_count": state.chunk_count,
            "dropped_chunks": state.dropped_chunks,
            "elapsed_s": state.elapsed_s,
            "rtf": state.rtf,
            "total_audio_s": state.total_audio_s,
            "sequence_timing": dict(state.sequence_timing or {}),
            "orchestrator_mode": state.orchestrator_mode,
            "run_id": state.run_id,
            "sequence_id": state.sequence_id,
            "sequence_index": state.sequence_index,
            "sequence_total": state.sequence_total,
            "global_timeline_ms": _compute_global_timeline_ms_unlocked(state),
            "event_contract_schema": MIC_V7_EVENT_SCHEMA,
            "event_contract_version": MIC_V7_EVENT_VERSION,
            "segment_id": _session_segment_id(state),
            "reason_code": _normalize_reason_code(state.reason_code, allow_none=True, fallback="unknown"),
            "reason_known": state.reason_code in MIC_V7_REASON_CODES if state.reason_code else True,
            "preflight_ok": state.preflight_ok,
            "preflight_errors": list(state.preflight_errors or []),
            "preflight_warnings": list(state.preflight_warnings or []),
            "error": state.error,
        }
    if extra:
        payload.update(extra)
    return payload


def _persist_session_snapshot(
    state: MicSessionState,
    *,
    phase: str,
    extra: dict[str, Any] | None = None,
) -> None:
    payload = _build_session_snapshot(state, phase=phase, extra=extra)
    try:
        MIC_SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)
        latest_path = MIC_SESSIONS_ROOT / f"{state.session_id}.json"
        latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        if phase in {"final", "error", "stopped"}:
            history_dir = MIC_SESSIONS_ROOT / "history"
            history_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            history_path = history_dir / f"{stamp}_{state.session_id}_{phase}.json"
            history_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # Perzistence je best-effort: nesmí shodit live session.
        return


def create_session(model_id: str, model_params: dict | None = None) -> str:
    session_id = f"mic_{uuid.uuid4().hex[:8]}"
    params = {
        **_model_default_params(model_id),
        **dict(model_params or {}),
    }
    state = MicSessionState(
        session_id=session_id,
        model_id=model_id,
        model_params=params,
        created_at=_iso_now(),
    )
    initial_target_sr = _parse_int_param(params, "sample_rate", SAMPLE_RATE, 8000, 48000)
    initial_gain_db = _parse_float_param(params, "input_gain_db", 0.0, -24.0, 24.0)
    initial_high_wm = _parse_float_param(params, "backpressure_high_s", 1.2, 0.2, 5.0)
    initial_low_wm = _parse_float_param(params, "backpressure_low_s", 0.4, 0.05, 3.0)
    if initial_low_wm >= initial_high_wm:
        initial_low_wm = max(0.05, min(initial_high_wm * 0.5, initial_high_wm - 0.05))
    state.target_sample_rate = initial_target_sr
    state.input_gain_db = initial_gain_db
    state.queue_high_watermark_s = initial_high_wm
    state.queue_low_watermark_s = initial_low_wm
    seq_meta = _parse_auto_sequence_meta(state.model_params)
    state.orchestrator_mode = _orchestrator_mode_from_params(state.model_params)
    if state.orchestrator_mode == ORCHESTRATOR_MODE_V7:
        state.run_id = f"run_{session_id}"
        state.sequence_id = str(seq_meta.get("token") or session_id)
        seq_index = _safe_int(seq_meta.get("index")) or 1
        seq_total = _safe_int(seq_meta.get("total")) or seq_index
        state.sequence_index = max(1, int(seq_index))
        state.sequence_total = max(int(state.sequence_index), int(seq_total))

    preflight = _evaluate_session_preflight(
        model_id=state.model_id,
        model_params=state.model_params,
        orchestrator_mode=state.orchestrator_mode,
        run_id=state.run_id,
        sequence_id=state.sequence_id,
        sequence_index=state.sequence_index,
        sequence_total=state.sequence_total,
    )
    with state._lock:
        state.preflight_ok = bool(preflight.get("ok"))
        state.preflight_errors = list(preflight.get("errors") or [])
        state.preflight_warnings = list(preflight.get("warnings") or [])

    with _sessions_lock:
        _sessions[session_id] = state
    _persist_session_snapshot(state, phase="created")
    _append_mic_event(
        "created",
        state=state,
        extra={
            "auto_model_sequence_token": state.model_params.get("auto_model_sequence_token"),
            "auto_model_sequence_index": state.model_params.get("auto_model_sequence_index"),
            "auto_model_sequence_total": state.model_params.get("auto_model_sequence_total"),
            **_sequence_plan_payload_from_meta(seq_meta),
            "preflight_ok": state.preflight_ok,
            "preflight_errors": list(state.preflight_errors or []),
            "preflight_warnings": list(state.preflight_warnings or []),
            "run_id": state.run_id,
            "sequence_id": state.sequence_id,
            "sequence_index": state.sequence_index,
            "sequence_total": state.sequence_total,
        },
    )
    _maybe_persist_sequence_progress(state, phase="created", force=True)
    return session_id


def get_session(session_id: str) -> MicSessionState | None:
    with _sessions_lock:
        return _sessions.get(session_id)


def get_orchestrator_payload(state: MicSessionState) -> dict[str, Any]:
    return _build_orchestrator_payload(state)


def get_v7_contract_metadata() -> dict[str, Any]:
    return build_contract_metadata()


def log_transport_event(session_id: str, event: str, extra: dict[str, Any] | None = None) -> None:
    state = get_session(session_id)
    payload_extra = dict(extra or {})
    if state is None:
        payload_extra.setdefault("session_id", session_id)
    _append_mic_event(str(event), state=state, extra=payload_extra)


def update_session_runtime_metadata(session_id: str, updates: dict[str, Any] | None) -> None:
    """Best-effort update of UI runtime metadata before finalization."""
    state = get_session(session_id)
    if state is None or not isinstance(updates, dict):
        return

    allowed_keys = {
        "mobile_loop_audio_start_delay_s",
        "mobile_loop_audio_start_known",
        "mobile_loop_audio_start_source",
        "auto_model_sequence_hard_trial_s",
        "auto_model_sequence_effective_hard_trial_s",
        "auto_model_sequence_silence_min_elapsed_s",
        "auto_model_sequence_silence_min_audio_fraction",
    }
    sanitized: dict[str, Any] = {}
    for key in allowed_keys:
        if key not in updates:
            continue
        value = updates.get(key)
        if key.endswith("_s") or key.endswith("_fraction"):
            numeric = _safe_float(value)
            if isinstance(numeric, float):
                sanitized[key] = numeric
        elif key.endswith("_known"):
            parsed = _safe_bool(value)
            if parsed is not None:
                sanitized[key] = parsed
        else:
            text = str(value or "").strip()
            if text:
                sanitized[key] = text[:120]

    if not sanitized:
        return

    with state._lock:
        state.model_params.update(sanitized)
        if "mobile_loop_audio_start_delay_s" in sanitized:
            delay_s = _safe_float(sanitized.get("mobile_loop_audio_start_delay_s")) or 0.0
            state.sequence_timing["planned_audio_start_delay_s"] = round(float(delay_s), 3)
            expected_start_ms = _safe_float(state.sequence_timing.get("expected_start_epoch_ms"))
            if isinstance(expected_start_ms, float):
                expected_audio_start_ms = expected_start_ms + float(delay_s) * 1000.0
                state.sequence_timing["expected_audio_start_epoch_ms"] = round(expected_audio_start_ms, 1)
                state.sequence_timing["expected_audio_start_at"] = _epoch_ms_to_iso(expected_audio_start_ms)
        if "mobile_loop_audio_start_known" in sanitized:
            state.sequence_timing["audio_start_known"] = sanitized["mobile_loop_audio_start_known"]
        if "mobile_loop_audio_start_source" in sanitized:
            state.sequence_timing["audio_start_source"] = sanitized["mobile_loop_audio_start_source"]


def _sanitize_client_event_name(value: Any) -> str:
    raw = str(value or "").strip().lower()
    cleaned = []
    for ch in raw[:80]:
        if ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "_":
            cleaned.append(ch)
        elif ch in {" ", "-", "."}:
            cleaned.append("_")
    event = "".join(cleaned).strip("_")
    while "__" in event:
        event = event.replace("__", "_")
    if event.startswith("client_sequence_") or event.startswith("mobile_loop_"):
        return event
    return "client_sequence_event"


def _sanitize_client_event_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        try:
            return float(value) if isinstance(value, float) else int(value)
        except Exception:
            return None
    if isinstance(value, str):
        return value[:2000]
    if depth >= 3:
        return str(value)[:500]
    if isinstance(value, list):
        return [_sanitize_client_event_value(item, depth=depth + 1) for item in value[:200]]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= 120:
                break
            key_text = str(key).strip()[:120]
            if key_text:
                out[key_text] = _sanitize_client_event_value(item, depth=depth + 1)
        return out
    return str(value)[:500]


def log_client_sequence_event(event: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Zapíše best-effort klientskou událost orchestrace sekvence do mic JSONL logu."""
    extra_raw = payload if isinstance(payload, dict) else {}
    extra = _sanitize_client_event_value(extra_raw)
    if not isinstance(extra, dict):
        extra = {}
    session_id = str(extra.get("session_id") or "").strip()
    state = get_session(session_id) if session_id else None
    extra["client_event"] = True
    extra["client_event_source"] = "benchmark_mic_ui"
    if state is not None:
        for identity_key in {
            "orchestrator_mode",
            "run_id",
            "sequence_id",
            "sequence_index",
            "sequence_total",
            "global_timeline_ms",
        }:
            extra.pop(identity_key, None)
    else:
        if session_id:
            extra.setdefault("session_id", session_id)
        if str(extra.get("orchestrator_mode") or "").strip().lower() == ORCHESTRATOR_MODE_V7:
            token = str(extra.get("sequence_id") or extra.get("sequence_token") or "").strip()
            if token:
                extra.setdefault("sequence_id", token)
                extra.setdefault("run_id", f"run_client_{token}")
            if _safe_float(extra.get("global_timeline_ms")) is None:
                extra["global_timeline_ms"] = 0.0
    event_name = _sanitize_client_event_name(event)
    _append_mic_event(event_name, state=state, extra=extra)
    return {"ok": True, "event": event_name, "session_id": session_id or None}


def _iter_manual_history_files() -> list[Path]:
    history_dir = MIC_SESSIONS_ROOT / "history"
    if not history_dir.exists():
        return []
    return sorted(history_dir.glob("*_manual.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def _read_json_dict(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _extract_ws_final_transcript_from_session_payload(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    final = payload.get("final")
    if isinstance(final, dict):
        final_text = final.get("text")
        if isinstance(final_text, str) and final_text.strip():
            return final_text
    return ""


def _manual_record_linked_session_id(metrics: dict[str, Any]) -> str:
    session_id = str(metrics.get("mic_session_id") or "").strip()
    if not session_id:
        return ""
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    if any(ch not in allowed for ch in session_id):
        return ""
    return session_id


def _session_transcript_from_manual_metrics(metrics: dict[str, Any]) -> str:
    session_id = _manual_record_linked_session_id(metrics)
    if not session_id:
        return ""

    with _sessions_lock:
        state = _sessions.get(session_id)
    if state is not None:
        with state._lock:
            transcript = _extract_ws_final_transcript_from_session_payload({"final": dict(state.final or {})})
        if transcript:
            return transcript

    latest_payload = _read_json_dict(MIC_SESSIONS_ROOT / f"{session_id}.json")
    return _extract_ws_final_transcript_from_session_payload(latest_payload)


def _apply_authoritative_manual_record_transcript(payload: dict[str, Any]) -> dict[str, Any]:
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        return payload
    if not _manual_record_linked_session_id(metrics):
        return payload

    transcript = _session_transcript_from_manual_metrics(metrics)
    next_payload = dict(payload)
    next_metrics = dict(metrics)
    next_metrics["transcript_source"] = "mic_ws_final" if transcript else "none"
    next_payload["transcript"] = transcript
    next_payload["metrics"] = next_metrics
    return next_payload


def _manual_record_id(payload: dict[str, Any], path: Path) -> str:
    return str(payload.get("session_id") or path.stem)


def _manual_mic_test_mode(metrics: dict[str, Any]) -> str:
    raw_mode = str(metrics.get("mic_test_mode") or "").strip()
    if raw_mode in {"free_speech", "reference_video"}:
        return raw_mode
    return "unknown"


def _refresh_manual_latest_snapshots() -> None:
    # latest soubory jsou odvozené z history a musí zůstat konzistentní po mazání.
    latest_files = list(MIC_SESSIONS_ROOT.glob("manual_latest*.json"))
    for latest in latest_files:
        try:
            if latest.is_file():
                latest.unlink()
        except Exception:
            continue

    latest_overall: dict[str, Any] | None = None
    latest_by_model: dict[str, dict[str, Any]] = {}
    for path in _iter_manual_history_files():
        payload = _read_json_dict(path)
        if payload is None:
            continue
        payload = _apply_authoritative_manual_record_transcript(payload)
        if latest_overall is None:
            latest_overall = payload
        model_id = str(payload.get("model_id") or "").strip()
        if model_id and model_id not in latest_by_model:
            latest_by_model[model_id] = payload

    if latest_overall is not None:
        try:
            latest_path = MIC_SESSIONS_ROOT / "manual_latest.json"
            latest_path.write_text(json.dumps(latest_overall, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    for model_id, payload in latest_by_model.items():
        try:
            model_slug = _slugify_filename(model_id, fallback="unknown_model")
            latest_model_path = MIC_SESSIONS_ROOT / f"manual_latest_{model_slug}.json"
            latest_model_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            continue


def save_manual_record(
    *,
    model_id: str,
    metrics: dict[str, Any] | None = None,
    note: str | None = None,
    quality_assessment: str | None = None,
    transcript: str | None = None,
    source: str = "manual_user_input",
) -> dict[str, Any]:
    """
    Uloží ručně zadaný MIC záznam do runtime/mic_sessions/history.
    Použij pro případy, kdy metriky vznikly mimo běžný websocket/session tok.
    """
    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    model_slug = _slugify_filename(model_id, fallback="unknown_model")
    rec_id = f"manual_{stamp}_{uuid.uuid4().hex[:6]}"

    payload: dict[str, Any] = {
        "session_id": rec_id,
        "phase": "final",
        "snapshot_at": now.isoformat(),
        "source": source,
        "model_id": model_id,
        "status": "stopped",
        "quality_assessment": quality_assessment or "manual_record",
        "note": note or "",
        "transcript": transcript or "",
        "metrics": dict(metrics or {}),
    }
    payload = _apply_authoritative_manual_record_transcript(payload)

    MIC_SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)
    history_dir = MIC_SESSIONS_ROOT / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    history_path = history_dir / f"{stamp}_{model_slug}_manual.json"
    latest_model_path = MIC_SESSIONS_ROOT / f"manual_latest_{model_slug}.json"
    latest_path = MIC_SESSIONS_ROOT / "manual_latest.json"

    history_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_model_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "record_id": rec_id,
        "history_path": str(history_path),
        "latest_model_path": str(latest_model_path),
        "latest_path": str(latest_path),
        "saved_at": now.isoformat(),
    }


def list_manual_records(
    *,
    limit: int = 50,
    model_id: str | None = None,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(500, int(limit)))
    model_filter = (model_id or "").strip().lower()

    records: list[dict[str, Any]] = []
    for path in _iter_manual_history_files():
        payload = _read_json_dict(path)
        if payload is None:
            continue
        payload = _apply_authoritative_manual_record_transcript(payload)

        rec_model_id = str(payload.get("model_id") or "").strip()
        if model_filter and rec_model_id.lower() != model_filter:
            continue

        metrics = payload.get("metrics")
        if not isinstance(metrics, dict):
            metrics = {}

        saved_at = str(payload.get("snapshot_at") or "").strip()
        if not saved_at:
            saved_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()

        records.append(
            {
                "record_id": str(payload.get("session_id") or path.stem),
                "saved_at": saved_at,
                "model_id": rec_model_id,
                "note": str(payload.get("note") or ""),
                "quality_assessment": str(payload.get("quality_assessment") or ""),
                "source": str(payload.get("source") or "manual_user_input"),
                "transcript": str(payload.get("transcript") or ""),
                "metrics": metrics,
            }
        )
        if len(records) >= safe_limit:
            break

    return records


def delete_manual_record(record_id: str) -> dict[str, Any]:
    target_id = (record_id or "").strip()
    if not target_id:
        raise ValueError("Chybí record_id.")

    deleted_count = 0
    for path in _iter_manual_history_files():
        payload = _read_json_dict(path)
        if payload is None:
            continue
        if _manual_record_id(payload, path) != target_id:
            continue
        try:
            path.unlink()
            deleted_count += 1
        except Exception as exc:
            raise ValueError(f"Nelze smazat záznam '{target_id}': {exc}") from exc

    if deleted_count == 0:
        raise ValueError(f"Záznam '{target_id}' neexistuje.")

    _refresh_manual_latest_snapshots()
    return {
        "record_id": target_id,
        "deleted": True,
        "deleted_count": deleted_count,
    }


def delete_manual_records(
    *,
    model_id: str | None = None,
    mic_test_mode: str | None = None,
) -> dict[str, Any]:
    model_filter = (model_id or "").strip().lower()
    mode_filter = (mic_test_mode or "").strip().lower()
    if mode_filter and mode_filter not in {"free_speech", "reference_video", "unknown"}:
        raise ValueError("Neplatný mic_test_mode. Použij free_speech, reference_video nebo unknown.")

    deleted_count = 0
    for path in _iter_manual_history_files():
        payload = _read_json_dict(path)
        if payload is None:
            continue

        rec_model_id = str(payload.get("model_id") or "").strip().lower()
        if model_filter and rec_model_id != model_filter:
            continue

        metrics = payload.get("metrics")
        if not isinstance(metrics, dict):
            metrics = {}
        mode = _manual_mic_test_mode(metrics)
        if mode_filter and mode != mode_filter:
            continue

        try:
            path.unlink()
            deleted_count += 1
        except Exception as exc:
            raise ValueError(f"Mazání historie selhalo: {exc}") from exc

    _refresh_manual_latest_snapshots()
    remaining = len(_iter_manual_history_files())
    return {
        "deleted": deleted_count,
        "remaining": remaining,
        "model_id": model_filter or None,
        "mic_test_mode": mode_filter or None,
    }


def _mobile_loop_package_dir(package_id: str) -> Path:
    trimmed = (package_id or "").strip()
    if not trimmed:
        raise ValueError("Neplatné package_id.")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    if any(ch not in allowed for ch in trimmed):
        raise ValueError("Neplatné package_id.")
    target = (MOBILE_LOOP_ROOT / trimmed).resolve()
    root = MOBILE_LOOP_ROOT.resolve()
    if not str(target).startswith(str(root)):
        raise ValueError("Neplatné package_id.")
    return target


def _seconds_tag(value: float) -> str:
    safe = round(max(0.0, float(value)), 3)
    text = f"{safe:.3f}".rstrip("0").rstrip(".")
    if not text:
        text = "0"
    return text.replace(".", "p")


def _mobile_loop_pairing_code_from_package_id(package_id: str) -> str | None:
    if not package_id.startswith("loop_"):
        return None
    first_token = package_id[len("loop_"):].split("_", 1)[0]
    code = first_token.split("-", 1)[0]
    if len(code) != MOBILE_LOOP_PAIRING_CODE_LEN:
        return None
    if any(ch not in MOBILE_LOOP_PAIRING_ALPHABET for ch in code):
        return None
    return code


def _mobile_loop_pairing_code_exists(code: str) -> bool:
    if not MOBILE_LOOP_ROOT.exists():
        return False
    normalized = (code or "").strip()
    if not normalized:
        return False
    package_prefix = f"loop_{normalized}-"
    for package_dir in MOBILE_LOOP_ROOT.iterdir():
        if not package_dir.is_dir():
            continue
        if package_dir.name.startswith(package_prefix):
            return True
        manifest_path = package_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(raw, dict) and raw.get("pairing_code") == normalized:
            return True
    return False


def _generate_mobile_loop_pairing_code() -> str:
    for _ in range(500):
        code = "".join(
            secrets.choice(MOBILE_LOOP_PAIRING_ALPHABET)
            for _ in range(MOBILE_LOOP_PAIRING_CODE_LEN)
        )
        if not any(ch.islower() for ch in code):
            continue
        if not any(ch.isupper() for ch in code):
            continue
        if not any(ch.isdigit() for ch in code):
            continue
        if not _mobile_loop_pairing_code_exists(code):
            return code
    raise ValueError("Nepodařilo se vygenerovat unikátní párovací kód balíčku.")


def get_mobile_loop_package_files(package_id: str) -> dict[str, Path]:
    package_dir = _mobile_loop_package_dir(package_id)
    if not package_dir.exists():
        raise ValueError(f"Balíček '{package_id}' neexistuje.")

    zip_path = package_dir / f"{package_id}.zip"
    wav_path = package_dir / f"{package_id}.wav"
    if not zip_path.exists():
        raise ValueError(f"Balíček '{package_id}' nemá ZIP soubor.")
    if not wav_path.exists():
        raise ValueError(f"Balíček '{package_id}' nemá WAV soubor.")
    return {"dir": package_dir, "zip": zip_path, "wav": wav_path}


def list_mobile_loop_packages(
    *,
    limit: int = 100,
    video_id: str | None = None,
) -> list[dict[str, Any]]:
    def _safe_float(value: Any, fallback: float = 0.0) -> float:
        try:
            out = float(value)
            if math.isfinite(out):
                return out
        except Exception:
            pass
        return fallback

    def _safe_int(value: Any, fallback: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return fallback

    safe_limit = max(1, min(500, int(limit)))
    video_filter = (video_id or "").strip()
    if not MOBILE_LOOP_ROOT.exists():
        return []

    rows: list[dict[str, Any]] = []
    package_dirs = [p for p in MOBILE_LOOP_ROOT.iterdir() if p.is_dir()]
    package_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    for package_dir in package_dirs:
        package_id = package_dir.name
        manifest_path = package_dir / "manifest.json"
        instructions_path = package_dir / "instructions.txt"
        payload: dict[str, Any] = {}

        if manifest_path.exists():
            try:
                raw = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    payload = raw
            except Exception:
                payload = {}

        row_video_id = str(payload.get("video_id") or "")
        if video_filter and row_video_id != video_filter:
            continue

        created_at = str(payload.get("created_at") or "").strip()
        if not created_at:
            created_at = datetime.fromtimestamp(package_dir.stat().st_mtime, tz=UTC).isoformat()

        try:
            files = get_mobile_loop_package_files(package_id)
            wav_exists = files["wav"].exists()
            zip_exists = files["zip"].exists()
        except Exception:
            wav_exists = (package_dir / f"{package_id}.wav").exists()
            zip_exists = (package_dir / f"{package_id}.zip").exists()

        instructions_text = ""
        if instructions_path.exists():
            try:
                instructions_text = instructions_path.read_text(encoding="utf-8")
            except Exception:
                instructions_text = ""

        row: dict[str, Any] = {
            "package_id": package_id,
            "pairing_code": str(payload.get("pairing_code") or "").strip()
            or _mobile_loop_pairing_code_from_package_id(package_id),
            "created_at": created_at,
            "video_id": row_video_id,
            "video_title": str(payload.get("video_title") or ""),
            "clip_from_s": _safe_float(payload.get("clip_from_s"), 0.0),
            "clip_to_s": _safe_float(payload.get("clip_to_s"), 0.0),
            "clip_duration_s": _safe_float(payload.get("clip_duration_s"), 0.0),
            "pause_s": _safe_float(payload.get("pause_s"), 0.0),
            "measured_rounds": _safe_int(payload.get("measured_rounds"), 0),
            "sync_rounds": _safe_int(payload.get("sync_rounds"), 0),
            "total_rounds": _safe_int(payload.get("total_rounds"), 0),
            "total_duration_s": _safe_float(payload.get("total_duration_s"), 0.0),
            "reference_excerpt": str(payload.get("reference_excerpt") or "").strip() or None,
            "instructions": instructions_text,
            "wav_url": f"/api/mic/mobile-loop-packages/{package_id}/wav",
            "download_url": f"/api/mic/mobile-loop-packages/{package_id}/download",
            "wav_exists": wav_exists,
            "zip_exists": zip_exists,
        }
        rows.append(row)
        if len(rows) >= safe_limit:
            break

    return rows


def delete_mobile_loop_package(package_id: str) -> dict[str, Any]:
    package_dir = _mobile_loop_package_dir(package_id)
    if not package_dir.exists():
        raise ValueError(f"Balíček '{package_id}' neexistuje.")
    if not package_dir.is_dir():
        raise ValueError(f"Balíček '{package_id}' není adresář.")

    shutil.rmtree(package_dir)
    return {
        "package_id": package_id,
        "deleted": True,
    }


def generate_mobile_loop_package(
    *,
    video_id: str,
    clip_from_s: float,
    clip_to_s: float,
    pause_s: float,
    repeat_count: int,
    include_sync_round: bool = True,
) -> dict[str, Any]:
    from . import library_service
    from packages.benchmarks.ground_truth.vtt_reference import extract_vtt_clip_text

    video_id_clean = (video_id or "").strip()
    if not video_id_clean:
        raise ValueError("Chybí video_id.")

    items = {item.video_id: item for item in library_service.list_items()}
    item = items.get(video_id_clean)
    if item is None:
        raise ValueError(f"Video '{video_id_clean}' není v knihovně.")

    source_wav = library_service.resolve_audio_cache_file_for_library_item(video_id_clean, extensions=(".wav",))
    if source_wav is None or not source_wav.exists():
        raise ValueError(
            f"Audio cache chybí pro video '{video_id_clean}'. Otevři Knihovnu a stáhni audio cache pro toto video."
        )

    start_s = max(0.0, float(clip_from_s))
    end_s = max(0.0, float(clip_to_s))
    if end_s <= start_s:
        raise ValueError("Neplatný rozsah: 'do' musí být větší než 'od'.")

    safe_pause_s = max(0.0, float(pause_s))
    safe_repeats = max(1, min(200, int(repeat_count)))
    sync_rounds = 1 if include_sync_round else 0
    total_rounds = sync_rounds + safe_repeats

    with wave.open(str(source_wav), "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        frame_count = wf.getnframes()
        total_audio_s = frame_count / max(1, sample_rate)

        if start_s >= total_audio_s:
            raise ValueError(
                f"Počátek {start_s:.2f}s je mimo audio (délka {total_audio_s:.2f}s)."
            )

        bounded_end_s = min(end_s, total_audio_s)
        start_frame = int(round(start_s * sample_rate))
        end_frame = int(round(bounded_end_s * sample_rate))
        if end_frame <= start_frame:
            raise ValueError("Vybraná pasáž je prázdná.")

        wf.setpos(start_frame)
        segment_bytes = wf.readframes(end_frame - start_frame)
        if not segment_bytes:
            raise ValueError("Vybraná pasáž neobsahuje audio data.")

    clip_duration_s = (end_frame - start_frame) / max(1, sample_rate)
    pause_frames = int(round(safe_pause_s * sample_rate))
    silence_bytes = b"\x00" * (pause_frames * channels * sample_width) if pause_frames > 0 else b""

    assembled: list[bytes] = []
    for idx in range(total_rounds):
        assembled.append(segment_bytes)
        if idx < total_rounds - 1 and silence_bytes:
            assembled.append(silence_bytes)
    output_frames = b"".join(assembled)

    created_at = _iso_now()
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    pairing_code = _generate_mobile_loop_pairing_code()
    range_tag = f"f{_seconds_tag(start_s)}-t{_seconds_tag(bounded_end_s)}"
    pause_tag = f"g{_seconds_tag(safe_pause_s)}"
    sync_tag = f"s{sync_rounds}"
    package_id = f"loop_{pairing_code}-{safe_repeats}x_{range_tag}_{pause_tag}_{sync_tag}_{stamp}"
    package_dir = MOBILE_LOOP_ROOT / package_id
    package_dir.mkdir(parents=True, exist_ok=True)

    wav_path = package_dir / f"{package_id}.wav"
    with wave.open(str(wav_path), "wb") as wf_out:
        wf_out.setnchannels(channels)
        wf_out.setsampwidth(sample_width)
        wf_out.setframerate(sample_rate)
        wf_out.writeframes(output_frames)

    reference_excerpt = extract_vtt_clip_text(
        video_id=video_id_clean,
        clip_start_s=start_s,
        clip_end_s=bounded_end_s,
        subtitles_root=SUBTITLES_ROOT,
    )

    timeline: list[dict[str, Any]] = []
    cursor = 0.0
    for idx in range(total_rounds):
        kind = "sync" if idx < sync_rounds else "measure"
        row: dict[str, Any] = {
            "round": idx + 1,
            "kind": kind,
            "speech_start_s": round(cursor, 3),
            "speech_end_s": round(cursor + clip_duration_s, 3),
        }
        cursor += clip_duration_s
        if idx < total_rounds - 1:
            row["pause_end_s"] = round(cursor + safe_pause_s, 3)
            cursor += safe_pause_s
        timeline.append(row)

    instructions = [
        "1) Stáhni WAV do mobilu a nastav opakované přehrávání.",
        "2) Ve webu na záložce Benchmark → Mikrofon vyber stejné video a model.",
        "3) Klikni Start a spusť přehrávání WAV v mobilu současně (nebo do 1-2 s).",
        "4) První kolo je SYNC (pokud je zapnuto), další kola jsou měřená.",
        "5) Po dokončení testu porovnej modely podle RTF, latence, drop rate a přepisu.",
        "",
        f"Párovací kód: {pairing_code}-{safe_repeats}x",
        f"Video: {item.title} ({video_id_clean})",
        f"Pasáž: {start_s:.2f}s až {bounded_end_s:.2f}s (délka {clip_duration_s:.2f}s)",
        f"Pauza mezi koly: {safe_pause_s:.2f}s",
        f"Měřená opakování: {safe_repeats}",
        f"SYNC kola: {sync_rounds}",
        f"Celkem kol: {total_rounds}",
        f"Odhad délky loopu: {cursor:.2f}s",
    ]
    if reference_excerpt:
        instructions.extend(["", "Referenční text (z titulků):", reference_excerpt])

    instructions_text = "\n".join(instructions).strip() + "\n"
    instructions_path = package_dir / "instructions.txt"
    instructions_path.write_text(instructions_text, encoding="utf-8")

    if reference_excerpt:
        (package_dir / "reference_excerpt.txt").write_text(reference_excerpt.strip() + "\n", encoding="utf-8")

    manifest = {
        "package_id": package_id,
        "pairing_code": pairing_code,
        "created_at": created_at,
        "video_id": video_id_clean,
        "video_title": item.title,
        "video_url": item.url,
        "clip_from_s": round(start_s, 3),
        "clip_to_s": round(bounded_end_s, 3),
        "clip_duration_s": round(clip_duration_s, 3),
        "pause_s": round(safe_pause_s, 3),
        "measured_rounds": safe_repeats,
        "sync_rounds": sync_rounds,
        "total_rounds": total_rounds,
        "total_duration_s": round(cursor, 3),
        "source_audio_wav": str(source_wav),
        "loop_audio_wav": str(wav_path),
        "reference_excerpt": reference_excerpt,
        "timeline": timeline,
    }
    manifest_path = package_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    zip_path = package_dir / f"{package_id}.zip"
    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(wav_path, arcname=wav_path.name)
        zf.write(manifest_path, arcname=manifest_path.name)
        zf.write(instructions_path, arcname=instructions_path.name)
        ref_path = package_dir / "reference_excerpt.txt"
        if ref_path.exists():
            zf.write(ref_path, arcname=ref_path.name)

    _append_mic_event(
        "mobile_loop_package_created",
        extra={
            "package_id": package_id,
            "pairing_code": pairing_code,
            "video_id": video_id_clean,
            "clip_from_s": round(start_s, 3),
            "clip_to_s": round(bounded_end_s, 3),
            "clip_duration_s": round(clip_duration_s, 3),
            "pause_s": round(safe_pause_s, 3),
            "measured_rounds": safe_repeats,
            "sync_rounds": sync_rounds,
            "total_rounds": total_rounds,
            "total_duration_s": round(cursor, 3),
            "wav_path": str(wav_path),
            "zip_path": str(zip_path),
        },
    )

    return {
        "package_id": package_id,
        "pairing_code": pairing_code,
        "created_at": created_at,
        "video_id": video_id_clean,
        "video_title": item.title,
        "clip_from_s": round(start_s, 3),
        "clip_to_s": round(bounded_end_s, 3),
        "clip_duration_s": round(clip_duration_s, 3),
        "pause_s": round(safe_pause_s, 3),
        "measured_rounds": safe_repeats,
        "sync_rounds": sync_rounds,
        "total_rounds": total_rounds,
        "total_duration_s": round(cursor, 3),
        "wav_url": f"/api/mic/mobile-loop-packages/{package_id}/wav",
        "download_url": f"/api/mic/mobile-loop-packages/{package_id}/download",
        "instructions": instructions_text,
        "reference_excerpt": reference_excerpt,
    }


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if q <= 0:
        return float(min(values))
    if q >= 1:
        return float(max(values))
    ordered = sorted(float(v) for v in values)
    idx = int(round((len(ordered) - 1) * q))
    idx = max(0, min(len(ordered) - 1, idx))
    return float(ordered[idx])


def _append_capped(values: list[float], value: float, cap: int = 5000) -> None:
    values.append(float(value))
    if len(values) > cap:
        del values[: len(values) - cap]


def _parse_int_param(params: dict, name: str, default: int, min_v: int, max_v: int) -> int:
    raw = params.get(name, default)
    try:
        val = int(raw)
    except Exception:
        return default
    return max(min_v, min(max_v, val))


def _parse_float_param(params: dict, name: str, default: float, min_v: float, max_v: float) -> float:
    raw = params.get(name, default)
    try:
        val = float(raw)
    except Exception:
        return default
    return max(min_v, min(max_v, val))


def _set_status(state: MicSessionState, new_status: str) -> None:
    old = state.status
    if old == new_status:
        return
    allowed = _STATUS_TRANSITIONS.get(old, set())
    if new_status not in allowed:
        raise RuntimeError(f"Neplatný přechod session: {old} -> {new_status}")
    state.status = new_status


def _resample_linear(samples: list[float], source_rate: int, target_rate: int) -> list[float]:
    if not samples:
        return []
    if source_rate <= 0 or target_rate <= 0 or source_rate == target_rate:
        return [float(v) for v in samples]
    source = [float(v) for v in samples]
    target_len = max(1, int(round(len(source) * float(target_rate) / float(source_rate))))
    if target_len <= 1:
        return [source[0]]
    ratio = float(source_rate) / float(target_rate)
    max_idx = len(source) - 1
    out: list[float] = []
    for idx in range(target_len):
        src_idx = idx * ratio
        left = int(src_idx)
        right = min(max_idx, left + 1)
        frac = src_idx - left
        sample = source[left] * (1.0 - frac) + source[right] * frac
        out.append(float(sample))
    return out


def _normalize_samples(samples: list[float], gain_db: float) -> list[float]:
    if not samples:
        return []
    gain = math.pow(10.0, float(gain_db) / 20.0) if abs(float(gain_db)) > 0.001 else 1.0
    out: list[float] = []
    for value in samples:
        x = float(value) * gain
        if x > 1.0:
            x = 1.0
        elif x < -1.0:
            x = -1.0
        out.append(x)
    return out


def _prepare_chunk_audio(
    *,
    state: MicSessionState,
    samples: list[float],
    sample_rate: int,
) -> tuple[list[float], int]:
    normalized = _normalize_samples(samples, state.input_gain_db)
    if sample_rate != state.target_sample_rate:
        normalized = _resample_linear(normalized, source_rate=sample_rate, target_rate=state.target_sample_rate)
    return normalized, state.target_sample_rate


def _classify_reason(exc: BaseException | str) -> str:
    text = str(exc).lower()
    if "preflight" in text:
        return "preflight_failed"
    if "timeout" in text:
        return "timeout"
    if "buffer" in text and ("overrun" in text or "overflow" in text):
        return "buffer_overrun"
    if "session not recording" in text:
        return "not_recording"
    if "session not found" in text:
        return "session_not_found"
    if "decode" in text:
        return "decode_error"
    return "adapter_error"


def classify_error_reason(exc: BaseException | str, *, fallback: str = "adapter_error") -> str:
    reason = _normalize_reason_code(_classify_reason(exc), allow_none=False, fallback=fallback)
    return reason or fallback


def _current_process_rss_mb() -> float | None:
    if psutil is None:
        return None
    try:
        proc = psutil.Process()
        return float(proc.memory_info().rss / (1024 * 1024))
    except Exception:
        return None


def start_recording(session_id: str) -> None:
    """Inicializuje STT live session pro daný model."""
    state = get_session(session_id)
    if state is None:
        raise ValueError(f"Session not found: {session_id}")

    with state._lock:
        preflight_ok = bool(state.preflight_ok)
        preflight_errors = list(state.preflight_errors or [])
    if not preflight_ok and preflight_errors:
        joined = ", ".join(preflight_errors)
        raise RuntimeError(f"preflight_failed: {joined}")

    adapter_key = _adapter_key(state.model_id)
    target_sr = _parse_int_param(state.model_params, "sample_rate", SAMPLE_RATE, 8000, 48000)
    gain_db = _parse_float_param(state.model_params, "input_gain_db", 0.0, -24.0, 24.0)
    high_wm = _parse_float_param(state.model_params, "backpressure_high_s", 1.2, 0.2, 5.0)
    low_wm = _parse_float_param(state.model_params, "backpressure_low_s", 0.4, 0.05, 3.0)
    if low_wm >= high_wm:
        low_wm = max(0.05, min(high_wm * 0.5, high_wm - 0.05))
    state.model_params["sample_rate"] = target_sr
    session_obj = _create_adapter_session(adapter_key, state.model_id, state.model_params)

    with state._lock:
        state._session_obj = session_obj
        state._started_perf = time.perf_counter()
        state.started_at = _iso_now()
        state.stopped_at = None
        state._last_text_change_perf = None
        state._chunk_processing_ms.clear()
        state._segment_finalize_ms.clear()
        state._capture_jitter_ms.clear()
        state._capture_lag_ms.clear()
        state._last_capture_ts_ms = None
        state._last_arrival_perf = None
        state._total_samples = 0
        state.chunk_count = 0
        state.dropped_chunks = 0
        state.drop_rate = 0.0
        state.session_resets = 0
        state.worker_rss_peak_mb = _current_process_rss_mb()
        state.first_word_latency_ms = None
        state.first_word_wall_ms = None
        state.first_word_audio_ms = None
        state.first_token_ms_p50 = None
        state.first_token_ms_p95 = None
        state.segment_finalize_ms_p50 = None
        state.segment_finalize_ms_p95 = None
        state.processing_ms_p50 = None
        state.processing_ms_p95 = None
        state.capture_jitter_ms_p50 = None
        state.capture_jitter_ms_p95 = None
        state.capture_lag_ms_p50 = None
        state.capture_lag_ms_p95 = None
        state.target_sample_rate = target_sr
        state.input_gain_db = gain_db
        state.queue_high_watermark_s = high_wm
        state.queue_low_watermark_s = low_wm
        state.queue_depth_s = 0.0
        state.queue_depth_peak_s = 0.0
        state.backpressure_events = 0
        state.backpressure_active = False
        _set_reason_code(state, None, allow_none=True)
        state.error = None
        state.transcript = ""
        state.partial = ""
        state.sequence_timing = {}
        if state.orchestrator_mode == ORCHESTRATOR_MODE_V7:
            if not state.run_id:
                state.run_id = f"run_{state.session_id}"
            meta = _parse_auto_sequence_meta(state.model_params or {})
            if not state.sequence_id:
                state.sequence_id = str(meta.get("token") or state.session_id)
            seq_index = _safe_int(state.sequence_index)
            if seq_index is None:
                seq_index = _safe_int(meta.get("index")) or 1
            seq_total = _safe_int(state.sequence_total)
            if seq_total is None:
                seq_total = _safe_int(meta.get("total")) or seq_index
            state.sequence_index = max(1, int(seq_index))
            state.sequence_total = max(int(state.sequence_index), int(seq_total))
        _set_status(state, "recording")
    sequence_timing = _update_sequence_timing_on_start(state)
    orchestrator_payload = _update_v7_timing_on_start(state)
    sequence_meta = _parse_auto_sequence_meta(state.model_params or {})
    _persist_session_snapshot(state, phase="started")
    _append_mic_event(
        "started",
        state=state,
        extra={
            "sample_rate": state.target_sample_rate,
            "input_gain_db": state.input_gain_db,
            "queue_high_watermark_s": state.queue_high_watermark_s,
            "queue_low_watermark_s": state.queue_low_watermark_s,
            "sequence_timing": sequence_timing,
            **_sequence_plan_payload_from_meta(sequence_meta),
            **orchestrator_payload,
        },
    )
    _maybe_persist_sequence_progress(state, phase="started", force=True)


def record_session_start_failure(
    session_id: str,
    *,
    error: str,
    reason_code: str = "start_recording_failed",
) -> None:
    """Zapíše explicitní chybu při startu session, aby ji šlo číst přes API/UI."""
    state = get_session(session_id)
    if state is None:
        return
    reason = _normalize_reason_code(reason_code, allow_none=False, fallback="start_recording_failed") or "start_recording_failed"
    with state._lock:
        state.error = str(error)
        state.reason_code = reason
        try:
            _set_status(state, "stopped")
        except Exception:
            state.status = "stopped"
        state.stopped_at = _iso_now()
    _persist_session_snapshot(state, phase="error")
    _append_mic_event(
        "start_failed",
        state=state,
        extra={
            "failure_reason_code": reason,
            "failure_error": str(error),
            **_build_orchestrator_payload(state),
        },
    )
    _maybe_persist_sequence_progress(state, phase="start_failed", force=True)


def process_audio_chunk(
    session_id: str,
    samples: list[float],
    sample_rate: int = SAMPLE_RATE,
    capture_ts_ms: float | None = None,
) -> dict[str, Any]:
    """Přijme audio chunk a vrátí partial výsledek."""
    state = get_session(session_id)
    if state is None:
        return {
            "error": "session not found",
            "reason_code": _normalize_reason_code("session_not_found", allow_none=False, fallback="session_not_found"),
        }
    if state.status != "recording":
        return {
            "error": f"session not recording (status={state.status})",
            "reason_code": _normalize_reason_code("not_recording", allow_none=False, fallback="not_recording"),
        }
    if not samples:
        with state._lock:
            state.chunk_count += 1
            state.dropped_chunks += 1
            state.drop_rate = state.dropped_chunks / max(1, state.chunk_count)
            _set_reason_code(state, "empty_chunk", allow_none=False, fallback="empty_chunk")
        _maybe_persist_sequence_progress(state, phase="partial")
        return {
            "error": "empty chunk",
            "reason_code": _normalize_reason_code("empty_chunk", allow_none=False, fallback="empty_chunk"),
        }

    in_sr = max(1, int(sample_rate))
    prepared_samples, prepared_sr = _prepare_chunk_audio(state=state, samples=samples, sample_rate=in_sr)
    if not prepared_samples:
        with state._lock:
            state.chunk_count += 1
            state.dropped_chunks += 1
            state.drop_rate = state.dropped_chunks / max(1, state.chunk_count)
            _set_reason_code(state, "empty_chunk", allow_none=False, fallback="empty_chunk")
        _maybe_persist_sequence_progress(state, phase="partial")
        return {
            "error": "empty chunk",
            "reason_code": _normalize_reason_code("empty_chunk", allow_none=False, fallback="empty_chunk"),
        }

    chunk_audio_s = len(prepared_samples) / max(1, prepared_sr)
    now_perf = time.perf_counter()
    now_wall_ms = time.time() * 1000.0

    backpressure_payload: dict[str, Any] | None = None
    with state._lock:
        if state._last_arrival_perf is not None:
            interarrival_ms = (now_perf - state._last_arrival_perf) * 1000.0
            expected_ms = max(1.0, chunk_audio_s * 1000.0)
            _append_capped(state._capture_jitter_ms, abs(interarrival_ms - expected_ms), cap=1200)
        state._last_arrival_perf = now_perf

        if isinstance(capture_ts_ms, (int, float)):
            cap_ts = float(capture_ts_ms)
            _append_capped(state._capture_lag_ms, max(0.0, now_wall_ms - cap_ts), cap=1200)
            if state._last_capture_ts_ms is not None:
                capture_delta = cap_ts - state._last_capture_ts_ms
                expected_ms = max(1.0, chunk_audio_s * 1000.0)
                _append_capped(state._capture_jitter_ms, abs(capture_delta - expected_ms), cap=1200)
            state._last_capture_ts_ms = cap_ts

        if state.backpressure_active and state.queue_depth_s >= state.queue_high_watermark_s:
            state.chunk_count += 1
            state.dropped_chunks += 1
            state.backpressure_events += 1
            # Drop chunk and reduce queue debt by one chunk duration.
            state.queue_depth_s = max(0.0, state.queue_depth_s - chunk_audio_s)
            if state.queue_depth_s <= state.queue_low_watermark_s:
                state.backpressure_active = False
            state.drop_rate = state.dropped_chunks / max(1, state.chunk_count)
            _set_reason_code(state, "backpressure_drop", allow_none=False, fallback="backpressure_drop")
            backpressure_payload = {
                "type": "partial",
                "text": state.transcript,
                "text_delta": "",
                "first_word_latency_ms": state.first_word_latency_ms,
                "chunk_processing_ms": 0.0,
                "chunk_audio_ms": round(chunk_audio_s * 1000.0, 1),
                "processing_debt_ms": round(state.queue_depth_s * 1000.0, 1),
                "queue_depth_peak_ms": round(state.queue_depth_peak_s * 1000.0, 1),
                "drop_rate": round(float(state.drop_rate), 4),
                "worker_rss_peak_mb": round(float(state.worker_rss_peak_mb), 1) if isinstance(state.worker_rss_peak_mb, (int, float)) else None,
                "reason_code": state.reason_code,
                "backpressure_active": state.backpressure_active,
                "backpressure_events": state.backpressure_events,
                "dropped_by_backpressure": True,
            }
    if backpressure_payload is not None:
        backpressure_payload.update(_build_orchestrator_payload(state))
        _maybe_persist_sequence_progress(state, phase="partial")
        return backpressure_payload

    adapter_key = _adapter_key(state.model_id)
    chunk_fn = _get_chunk_fn(adapter_key)
    chunk_started = time.perf_counter()

    try:
        result = chunk_fn(session=state._session_obj, sample_rate=prepared_sr, samples=prepared_samples)
    except Exception as exc:
        with state._lock:
            state.error = str(exc)
            _set_reason_code(state, classify_error_reason(exc), allow_none=False, fallback="adapter_error")
            state.chunk_count += 1
            state.dropped_chunks += 1
            state.drop_rate = state.dropped_chunks / max(1, state.chunk_count)
            _set_status(state, "stopped")
            state.stopped_at = _iso_now()
        _persist_session_snapshot(state, phase="error")
        _append_mic_event(
            "chunk_error",
            state=state,
            extra={
                "chunk_audio_s": round(chunk_audio_s, 4),
                "sample_rate": prepared_sr,
                "failure_reason_code": state.reason_code,
                "failure_error": str(exc),
            },
        )
        _maybe_persist_sequence_progress(state, phase="chunk_error", force=True)
        error_payload = {"error": str(exc), "reason_code": state.reason_code}
        error_payload.update(_build_orchestrator_payload(state))
        return error_payload

    processing_ms = max(0.0, (time.perf_counter() - chunk_started) * 1000.0)
    rss_now = _current_process_rss_mb()

    with state._lock:
        state._total_samples += len(prepared_samples)
        state.chunk_count += 1
        _append_capped(state._chunk_processing_ms, processing_ms)
        state.queue_depth_s = max(0.0, state.queue_depth_s + (processing_ms / 1000.0) - chunk_audio_s)
        state.queue_depth_peak_s = max(state.queue_depth_peak_s, state.queue_depth_s)
        if state.queue_depth_s >= state.queue_high_watermark_s:
            state.backpressure_active = True
        elif state.queue_depth_s <= state.queue_low_watermark_s:
            state.backpressure_active = False
        if isinstance(rss_now, (int, float)):
            if state.worker_rss_peak_mb is None:
                state.worker_rss_peak_mb = float(rss_now)
            else:
                state.worker_rss_peak_mb = max(float(state.worker_rss_peak_mb), float(rss_now))
        state.partial = result.get("text_delta", "")
        new_text = str(result.get("text", state.transcript) or "")
        previous_text = state.transcript
        state.transcript = new_text
        if new_text and new_text != previous_text:
            now_perf = time.perf_counter()
            if state._last_text_change_perf is not None:
                delta_ms = (now_perf - state._last_text_change_perf) * 1000.0
                if delta_ms >= 1.0:
                    _append_capped(state._segment_finalize_ms, delta_ms)
            state._last_text_change_perf = now_perf
        if state.first_word_latency_ms is None:
            first = result.get("first_word_latency_ms")
            state.first_word_latency_ms = float(first) if isinstance(first, (int, float)) else None
        if state.first_word_wall_ms is None:
            first_wall = result.get("first_word_wall_ms")
            state.first_word_wall_ms = float(first_wall) if isinstance(first_wall, (int, float)) else None
        if state.first_word_audio_ms is None:
            first_audio = result.get("first_word_audio_ms")
            state.first_word_audio_ms = float(first_audio) if isinstance(first_audio, (int, float)) else None
        state.drop_rate = state.dropped_chunks / max(1, state.chunk_count)
        _set_reason_code(state, None, allow_none=True)

    _maybe_persist_sequence_progress(state, phase="partial")

    partial_payload = {
        "type": "partial",
        "text": result.get("text", ""),
        "text_delta": result.get("text_delta", ""),
        "first_word_latency_ms": result.get("first_word_latency_ms"),
        "first_word_wall_ms": result.get("first_word_wall_ms"),
        "first_word_audio_ms": result.get("first_word_audio_ms"),
        "chunk_processing_ms": round(float(processing_ms), 1),
        "chunk_audio_ms": round(chunk_audio_s * 1000.0, 1),
        "processing_debt_ms": round(state.queue_depth_s * 1000.0, 1),
        "queue_depth_peak_ms": round(state.queue_depth_peak_s * 1000.0, 1),
        "drop_rate": round(float(state.drop_rate), 4),
        "worker_rss_peak_mb": round(float(state.worker_rss_peak_mb), 1) if isinstance(state.worker_rss_peak_mb, (int, float)) else None,
        "reason_code": state.reason_code,
        "backpressure_active": state.backpressure_active,
        "backpressure_events": state.backpressure_events,
        "dropped_by_backpressure": False,
    }
    partial_payload.update(_build_orchestrator_payload(state))
    return partial_payload


def stop_recording(session_id: str) -> dict[str, Any]:
    """Finalizuje session a vrátí kompletní výsledek."""
    state = get_session(session_id)
    if state is None:
        return {"error": "session not found"}

    adapter_key = _adapter_key(state.model_id)
    finalize_fn = _get_finalize_fn(adapter_key)

    finalize_started = time.perf_counter()
    try:
        final = finalize_fn(session=state._session_obj)
    except Exception as exc:
        final = {}
        with state._lock:
            state.error = str(exc)
            _set_reason_code(state, classify_error_reason(exc), allow_none=False, fallback="adapter_error")
            state.dropped_chunks += 1

    elapsed_s = max(0.001, time.perf_counter() - state._started_perf)
    total_audio_s = state._total_samples / max(1, state.target_sample_rate)
    rtf = elapsed_s / max(0.1, total_audio_s)
    finalize_processing_ms = max(0.0, (time.perf_counter() - finalize_started) * 1000.0)
    rss_now = _current_process_rss_mb()

    with state._lock:
        _set_status(state, "stopped")
        state.stopped_at = _iso_now()
        state.transcript = final.get("text", state.transcript)
        state.elapsed_s = round(elapsed_s, 3)
        state.rtf = round(rtf, 4)
        state.total_audio_s = round(total_audio_s, 2)
        if state.first_word_latency_ms is None:
            first = final.get("first_word_latency_ms")
            state.first_word_latency_ms = float(first) if isinstance(first, (int, float)) else None
        if state.first_word_wall_ms is None:
            first_wall = final.get("first_word_wall_ms")
            state.first_word_wall_ms = float(first_wall) if isinstance(first_wall, (int, float)) else None
        if state.first_word_audio_ms is None:
            first_audio = final.get("first_word_audio_ms")
            state.first_word_audio_ms = float(first_audio) if isinstance(first_audio, (int, float)) else None
        if isinstance(state.first_word_latency_ms, (int, float)):
            state.first_token_ms_p50 = round(float(state.first_word_latency_ms), 1)
            state.first_token_ms_p95 = round(float(state.first_word_latency_ms), 1)
        if finalize_processing_ms >= 1.0:
            _append_capped(state._segment_finalize_ms, finalize_processing_ms)
        p50 = _percentile(state._segment_finalize_ms, 0.50)
        p95 = _percentile(state._segment_finalize_ms, 0.95)
        state.segment_finalize_ms_p50 = round(float(p50), 1) if isinstance(p50, (int, float)) else None
        state.segment_finalize_ms_p95 = round(float(p95), 1) if isinstance(p95, (int, float)) else None
        proc_p50 = _percentile(state._chunk_processing_ms, 0.50)
        proc_p95 = _percentile(state._chunk_processing_ms, 0.95)
        state.processing_ms_p50 = round(float(proc_p50), 1) if isinstance(proc_p50, (int, float)) else None
        state.processing_ms_p95 = round(float(proc_p95), 1) if isinstance(proc_p95, (int, float)) else None
        jitter_p50 = _percentile(state._capture_jitter_ms, 0.50)
        jitter_p95 = _percentile(state._capture_jitter_ms, 0.95)
        state.capture_jitter_ms_p50 = round(float(jitter_p50), 1) if isinstance(jitter_p50, (int, float)) else None
        state.capture_jitter_ms_p95 = round(float(jitter_p95), 1) if isinstance(jitter_p95, (int, float)) else None
        lag_p50 = _percentile(state._capture_lag_ms, 0.50)
        lag_p95 = _percentile(state._capture_lag_ms, 0.95)
        state.capture_lag_ms_p50 = round(float(lag_p50), 1) if isinstance(lag_p50, (int, float)) else None
        state.capture_lag_ms_p95 = round(float(lag_p95), 1) if isinstance(lag_p95, (int, float)) else None
        if isinstance(rss_now, (int, float)):
            if state.worker_rss_peak_mb is None:
                state.worker_rss_peak_mb = float(rss_now)
            else:
                state.worker_rss_peak_mb = max(float(state.worker_rss_peak_mb), float(rss_now))
        state.drop_rate = state.dropped_chunks / max(1, state.chunk_count)
        state.queue_depth_s = max(0.0, state.queue_depth_s)
        if state.reason_code is None and state.drop_rate > 0.05:
            _set_reason_code(state, "backpressure_drop", allow_none=False, fallback="backpressure_drop")
        elif state.reason_code is None and not state.transcript.strip():
            _set_reason_code(state, "no_tokens", allow_none=False, fallback="no_tokens")
    sequence_timing = _update_sequence_timing_on_stop(state)

    final_payload = {
        "type": "final",
        "text": state.transcript,
        "first_word_latency_ms": state.first_word_latency_ms,
        "first_word_wall_ms": state.first_word_wall_ms,
        "first_word_audio_ms": state.first_word_audio_ms,
        "first_token_ms_p50": state.first_token_ms_p50,
        "first_token_ms_p95": state.first_token_ms_p95,
        "segment_finalize_ms_p50": state.segment_finalize_ms_p50,
        "segment_finalize_ms_p95": state.segment_finalize_ms_p95,
        "processing_ms_p50": state.processing_ms_p50,
        "processing_ms_p95": state.processing_ms_p95,
        "capture_jitter_ms_p50": state.capture_jitter_ms_p50,
        "capture_jitter_ms_p95": state.capture_jitter_ms_p95,
        "capture_lag_ms_p50": state.capture_lag_ms_p50,
        "capture_lag_ms_p95": state.capture_lag_ms_p95,
        "drop_rate": round(float(state.drop_rate), 4),
        "session_resets": state.session_resets,
        "worker_rss_peak_mb": round(float(state.worker_rss_peak_mb), 1) if isinstance(state.worker_rss_peak_mb, (int, float)) else None,
        "sample_rate": state.target_sample_rate,
        "input_gain_db": state.input_gain_db,
        "queue_high_watermark_s": round(float(state.queue_high_watermark_s), 3),
        "queue_low_watermark_s": round(float(state.queue_low_watermark_s), 3),
        "queue_depth_peak_s": round(float(state.queue_depth_peak_s), 3),
        "queue_depth_last_s": round(float(state.queue_depth_s), 3),
        "backpressure_events": state.backpressure_events,
        "chunk_count": state.chunk_count,
        "dropped_chunks": state.dropped_chunks,
        "elapsed_s": state.elapsed_s,
        "rtf": state.rtf,
        "total_audio_s": state.total_audio_s,
        "sequence_timing": sequence_timing,
        "reason_code": _normalize_reason_code(state.reason_code, allow_none=True, fallback="unknown"),
        "error": state.error,
    }
    final_payload.update(_build_orchestrator_payload(state))
    with state._lock:
        state.final = dict(final_payload)
    _persist_session_snapshot(state, phase="final", extra={"final": final_payload})
    _persist_sequence_report(state, final_payload, phase="final")
    _append_mic_event(
        "finalized",
        state=state,
        extra={
            "elapsed_s": state.elapsed_s,
            "total_audio_s": state.total_audio_s,
            "rtf": state.rtf,
            "drop_rate": round(float(state.drop_rate), 4),
            "backpressure_events": state.backpressure_events,
            "chunk_count": state.chunk_count,
            "dropped_chunks": state.dropped_chunks,
            "sequence_timing": sequence_timing,
        },
    )
    return final_payload


def decode_audio_frame(data: bytes | str) -> tuple[list[float], float | None]:
    """
    Dekóduje audio frame z WebSocket zprávy na list[float].

    Podporované formáty:
    - bytes: raw PCM int16 little-endian
    - str (JSON): {"samples": [0.1, -0.2, ...]}
    - str (base64): base64 enkódovaný PCM int16
    """
    if isinstance(data, bytes):
        capture_ts_ms: float | None = None
        payload = data
        if len(data) >= 12 and data[:4] == WS_AUDIO_FRAME_MAGIC:
            try:
                capture_ts_ms = float(struct.unpack("<d", data[4:12])[0])
                payload = data[12:]
            except Exception:
                capture_ts_ms = None
                payload = data
        pcm = array("h")
        pcm.frombytes(payload[: len(payload) - (len(payload) % 2)])
        return [max(-1.0, min(1.0, s / 32768.0)) for s in pcm], capture_ts_ms

    if isinstance(data, str):
        stripped = data.strip()
        if stripped.startswith("{"):
            payload = json.loads(stripped)
            capture_ts_raw = payload.get("capture_ts_ms")
            capture_ts_ms = float(capture_ts_raw) if isinstance(capture_ts_raw, (int, float)) else None
            return [float(s) for s in payload.get("samples", [])], capture_ts_ms
        # base64
        raw = base64.b64decode(stripped)
        pcm = array("h")
        pcm.frombytes(raw)
        return [max(-1.0, min(1.0, s / 32768.0)) for s in pcm], None

    return [], None


def list_audio_devices() -> list[dict]:
    """Vrátí seznam dostupných audio zařízení pro výběr v UI."""
    try:
        import sounddevice as sd  # type: ignore[import-not-found]
        devices = sd.query_devices()
        return [
            {
                "index": i,
                "name": d["name"],
                "max_input_channels": d["max_input_channels"],
                "default_samplerate": d["default_samplerate"],
            }
            for i, d in enumerate(devices)
            if d["max_input_channels"] > 0
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Adapter dispatch helpers
# ---------------------------------------------------------------------------

def _adapter_key(model_id: str) -> str:
    if model_id.startswith("whisper_cpp"):
        return "whisper_cpp"
    if model_id.startswith("vosk"):
        return "vosk"
    if model_id.startswith("sherpa_onnx"):
        return "sherpa_onnx"
    if model_id.startswith("qwen"):
        return "qwen_asr"
    if model_id.startswith("moonshine"):
        return "moonshine"
    if model_id.startswith("faster_whisper"):
        return "faster_whisper"
    raise ValueError(f"Neznámý model_id pro mic session: {model_id}")


def _create_adapter_session(adapter: str, model_id: str, params: dict) -> Any:
    model_store = MODEL_STORE_ROOT

    if adapter == "vosk":
        from packages.adapters.vosk_runner import VoskRunConfig, resolve_vosk_model_dir, create_vosk_live_session
        model_dir = resolve_vosk_model_dir(model_store)
        if not model_dir:
            raise RuntimeError("VOSK model nenalezen")
        cfg = VoskRunConfig(
            model_dir=str(model_dir),
            sample_rate=int(params.get("sample_rate", SAMPLE_RATE)),
            chunk_seconds=float(params.get("chunk_seconds", 0.20)),
            set_words=bool(params.get("set_words", False)),
        )
        return create_vosk_live_session(config=cfg)

    elif adapter == "sherpa_onnx":
        from packages.adapters.sherpa_onnx_runner import SherpaRunConfig, resolve_sherpa_model_bundle, create_sherpa_live_session
        if model_id == "sherpa_onnx_parakeet_cs_int8":
            raise RuntimeError(
                "Model sherpa_onnx_parakeet_cs_int8 je dočasně vypnutý: nekompatibilita sherpa runtime metadata ('window_size')."
            )
        scoped_root = model_store / model_id
        resolver_root = scoped_root if scoped_root.exists() else model_store
        bundle = resolve_sherpa_model_bundle(resolver_root, preferred_language=None)
        if bundle is None and resolver_root != model_store:
            bundle = resolve_sherpa_model_bundle(model_store, preferred_language=None)
        if not bundle:
            raise RuntimeError(f"Sherpa model bundle nenalezen pro model_id={model_id}")
        if model_id == "sherpa_onnx_small" and "parakeet" in str(bundle.model_dir).lower():
            raise RuntimeError(
                "sherpa_onnx_small mapuje na nekompatibilní Parakeet bundle. "
                "Ověř runtime/model_store/sherpa_onnx_small."
            )
        cfg = SherpaRunConfig(
            tokens=bundle.tokens,
            encoder=bundle.encoder,
            decoder=bundle.decoder,
            joiner=bundle.joiner,
            provider=str(params.get("provider", "cpu")),
            num_threads=int(params.get("num_threads", 2)),
            sample_rate=int(params.get("sample_rate", SAMPLE_RATE)),
            decoding_method=str(params.get("decoding_method", "greedy_search")),
        )
        return create_sherpa_live_session(config=cfg)

    elif adapter == "moonshine":
        from packages.adapters.moonshine_runner import MoonshineRunConfig, resolve_moonshine_model_path, create_moonshine_live_session
        model_arch = str(params.get("model_arch", "medium"))
        model_path = resolve_moonshine_model_path(model_store, model_arch) or ""
        cfg = MoonshineRunConfig(
            model_path=model_path,
            model_arch=model_arch,
            analysis_interval_ms=int(params.get("analysis_interval_ms", 500)),
            language=str(params.get("language", "en")),
        )
        return create_moonshine_live_session(config=cfg)

    elif adapter == "whisper_cpp":
        from packages.adapters.whisper_cpp_runner import (
            WhisperRunConfig,
            create_whisper_live_session,
            resolve_whisper_cli,
            resolve_whisper_model_file,
        )
        whisper_bin = resolve_whisper_cli(model_store)
        if not whisper_bin:
            raise RuntimeError("whisper-cli binary nenalezen")
        model_file = resolve_whisper_model_file(model_store, model_id)
        if not model_file:
            raise RuntimeError(f"whisper model file nenalezen pro {model_id}")

        cfg = WhisperRunConfig(
            whisper_bin=str(whisper_bin),
            model_path=str(model_file),
            language=str(params.get("language", "cs")),
            threads=int(params.get("threads", 4)),
            beam_size=int(params["beam_size"]) if params.get("beam_size") is not None else None,
            best_of=int(params["best_of"]) if params.get("best_of") is not None else None,
            no_fallback=bool(params.get("no_fallback", True)),
            initial_prompt=(str(params.get("initial_prompt", "")).strip() or None),
            use_server_cache=True,
        )
        return create_whisper_live_session(
            config=cfg,
            sample_rate=int(params.get("sample_rate", SAMPLE_RATE)),
            analysis_interval_ms=int(params.get("analysis_interval_ms", 1200)),
            analysis_window_seconds=int(params.get("analysis_window_seconds", 12)),
        )

    elif adapter == "faster_whisper":
        from packages.adapters.faster_whisper_runner import (
            FasterWhisperRunConfig,
            create_faster_whisper_live_session,
            resolve_faster_whisper_model_path,
        )
        model_path = resolve_faster_whisper_model_path(model_store, model_id)
        if model_path is None:
            raise RuntimeError(
                f"faster-whisper model bundle nenalezen pro {model_id}. "
                f"Očekáván CTranslate2 model pod runtime/model_store/{model_id}/model.bin"
            )
        cfg = FasterWhisperRunConfig(
            model_path=str(model_path),
            language=str(params.get("language", "cs")),
            threads=int(params.get("threads", 4)),
            beam_size=int(params.get("beam_size", 1)),
            best_of=int(params.get("best_of", 1)),
            device=str(params.get("device", "cpu")),
            compute_type=str(params.get("compute_type", "int8")),
        )
        return create_faster_whisper_live_session(
            config=cfg,
            sample_rate=int(params.get("sample_rate", SAMPLE_RATE)),
            analysis_interval_ms=int(params.get("analysis_interval_ms", 1200)),
            analysis_window_seconds=int(params.get("analysis_window_seconds", 12)),
        )

    elif adapter == "qwen_asr":
        raise ValueError(
            f"Model '{model_id}' nepodporuje mic mode — potřebuje celý audio soubor. "
            "Použij vosk, sherpa_onnx, faster_whisper nebo moonshine."
        )

    raise ValueError(f"Neznámý adapter: {adapter}")


def _get_chunk_fn(adapter: str):
    if adapter == "vosk":
        from packages.adapters.vosk_runner import transcribe_vosk_live_chunk
        return transcribe_vosk_live_chunk
    if adapter == "sherpa_onnx":
        from packages.adapters.sherpa_onnx_runner import transcribe_sherpa_live_chunk
        return transcribe_sherpa_live_chunk
    if adapter == "moonshine":
        from packages.adapters.moonshine_runner import transcribe_moonshine_live_chunk
        return transcribe_moonshine_live_chunk
    if adapter == "whisper_cpp":
        from packages.adapters.whisper_cpp_runner import transcribe_whisper_live_chunk
        return transcribe_whisper_live_chunk
    if adapter == "faster_whisper":
        from packages.adapters.faster_whisper_runner import transcribe_faster_whisper_live_chunk
        return transcribe_faster_whisper_live_chunk
    raise ValueError(f"Chunk fn pro '{adapter}' neexistuje")


def _get_finalize_fn(adapter: str):
    if adapter == "vosk":
        from packages.adapters.vosk_runner import finalize_vosk_live_session
        return finalize_vosk_live_session
    if adapter == "sherpa_onnx":
        from packages.adapters.sherpa_onnx_runner import finalize_sherpa_live_session
        return finalize_sherpa_live_session
    if adapter == "moonshine":
        from packages.adapters.moonshine_runner import finalize_moonshine_live_session
        return finalize_moonshine_live_session
    if adapter == "whisper_cpp":
        from packages.adapters.whisper_cpp_runner import finalize_whisper_live_session
        return finalize_whisper_live_session
    if adapter == "faster_whisper":
        from packages.adapters.faster_whisper_runner import finalize_faster_whisper_live_session
        return finalize_faster_whisper_live_session
    raise ValueError(f"Finalize fn pro '{adapter}' neexistuje")
