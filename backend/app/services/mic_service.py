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
import json
import math
import shutil
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

from ..config import AUDIO_CACHE_ROOT, MIC_SEQUENCES_ROOT, MIC_SESSIONS_ROOT, MODEL_STORE_ROOT, RUNTIME_ROOT, SUBTITLES_ROOT

try:
    import psutil
except ModuleNotFoundError:
    psutil = None  # type: ignore[assignment]

SAMPLE_RATE = 16000
WS_AUDIO_FRAME_MAGIC = b"ASTT"
MOBILE_LOOP_ROOT = MIC_SESSIONS_ROOT / "mobile_loops"
MIC_EVENTS_LOG_PATH = RUNTIME_ROOT / "logs" / "mic_sequence_events.jsonl"
_AUTO_SEQUENCE_MAX_TRACKED = 256
_V7_SEQUENCE_MAX_TRACKED = 256
ORCHESTRATOR_MODE_LEGACY = "legacy_sequence"
ORCHESTRATOR_MODE_V7 = "v7_cs_online"
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
    error: str | None = None
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
    pause_s = _safe_float(model_params.get("mobile_loop_pause_s"))
    cycle_s = (speech_s + pause_s) if isinstance(speech_s, (int, float)) and isinstance(pause_s, (int, float)) else None
    return {
        "token": token or None,
        "index": sequence_index,
        "total": sequence_total,
        "speech_s": speech_s,
        "pause_s": pause_s,
        "cycle_s": cycle_s,
        "mobile_loop_enabled": bool(model_params.get("mobile_loop_enabled")),
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
        "planned_pause_s": round(float(meta["pause_s"]), 3) if isinstance(meta["pause_s"], (int, float)) else None,
        "planned_cycle_s": round(float(meta["cycle_s"]), 3) if isinstance(meta["cycle_s"], (int, float)) else None,
        "sequence_token": meta["token"],
        "sequence_index": meta["index"],
        "sequence_total": meta["total"],
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
            timing["observed_start_epoch_ms"] = round(float(started_ms), 1)
            timing["expected_start_epoch_ms"] = round(float(expected_start_ms), 1)
            timing["expected_start_at"] = _epoch_ms_to_iso(expected_start_ms)
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
    if isinstance(speech_s, (int, float)) and isinstance(timing.get("observed_duration_s"), (int, float)):
        timing["duration_vs_speech_delta_s"] = round(float(timing["observed_duration_s"]) - float(speech_s), 3)

    expected_start_ms = _safe_float(timing.get("expected_start_epoch_ms"))
    if isinstance(expected_start_ms, (int, float)) and isinstance(speech_s, (int, float)) and isinstance(stopped_ms, (int, float)):
        expected_stop_ms = float(expected_start_ms) + float(speech_s) * 1000.0
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
    reason = state.reason_code or ""
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
    return {
        "seq_index": seq_index,
        "seq_total": seq_total,
        "session_id": state.session_id,
        "model_id": state.model_id,
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
        "reason_code": p.get("reason_code", state.reason_code),
        "error": p.get("error", state.error),
    }


def _build_sequence_summary(trials: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"ok": 0, "borderline": 0, "too_slow_for_slot": 0, "fail": 0}
    running = 0
    finalized = 0
    reasons: dict[str, int] = {}
    rtf_values: list[float] = []
    drop_values: list[float] = []

    for trial in trials:
        status = str(trial.get("trial_status") or "")
        if status in counts:
            counts[status] += 1
        if str(trial.get("status") or "") == "recording":
            running += 1
        if trial.get("stopped_at"):
            finalized += 1
        reason_code = str(trial.get("reason_code") or "").strip()
        if reason_code:
            reasons[reason_code] = reasons.get(reason_code, 0) + 1

        rtf = _safe_float(trial.get("rtf"))
        if isinstance(rtf, float):
            rtf_values.append(rtf)
        drop = _safe_float(trial.get("drop_rate"))
        if isinstance(drop, float):
            drop_values.append(drop)

    avg_rtf = round(sum(rtf_values) / len(rtf_values), 4) if rtf_values else None
    avg_drop = round(sum(drop_values) / len(drop_values), 4) if drop_values else None
    return {
        "counts": counts,
        "running": running,
        "finalized": finalized,
        "reasons": reasons,
        "avg_rtf": avg_rtf,
        "avg_drop_rate": avg_drop,
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

    report = {
        "sequence_token": token,
        "updated_at": _iso_now(),
        "sequence_total": sequence_total,
        "trials_count": len(trials_sorted),
        "summary": _build_sequence_summary(trials_sorted),
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
        "orchestrator_mode",
        "run_id",
        "sequence_id",
        "global_timeline_ms",
        "phase",
        "status",
        "trial_status",
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
        "error",
        "session_id",
        "created_at",
        "started_at",
        "stopped_at",
        "updated_at",
    ]
    try:
        lines = [",".join(csv_cols)]
        for trial in trials_sorted:
            lines.append(",".join(str(trial.get(col, "")) for col in csv_cols))
        csv_path.write_text("\n".join(lines), encoding="utf-8")
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
            "reason_code": state.reason_code,
            "error": state.error,
        }

    _persist_sequence_report(state, payload, phase=phase)


def get_sequence_report(token: str) -> dict[str, Any] | None:
    """Vrátí sequence report dict, nebo None pokud neexistuje."""
    path = MIC_SEQUENCES_ROOT / token / "report.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_sequence_report_csv_path(token: str) -> Path | None:
    """Vrátí cestu k report.csv, nebo None."""
    path = MIC_SEQUENCES_ROOT / token / "report.csv"
    return path if path.exists() else None


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
            "reason_code": state.reason_code,
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
    params = dict(model_params or {})
    state = MicSessionState(
        session_id=session_id,
        model_id=model_id,
        model_params=params,
        created_at=_iso_now(),
    )
    seq_meta = _parse_auto_sequence_meta(state.model_params)
    state.orchestrator_mode = _orchestrator_mode_from_params(state.model_params)
    if state.orchestrator_mode == ORCHESTRATOR_MODE_V7:
        state.run_id = f"run_{session_id}"
        state.sequence_id = str(seq_meta.get("token") or session_id)
        state.sequence_index = _safe_int(seq_meta.get("index"))
        state.sequence_total = _safe_int(seq_meta.get("total"))

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
            "mobile_loop_enabled": seq_meta["mobile_loop_enabled"],
            "mobile_loop_speech_s": seq_meta["speech_s"],
            "mobile_loop_pause_s": seq_meta["pause_s"],
            "mobile_loop_cycle_s": seq_meta["cycle_s"],
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


def log_transport_event(session_id: str, event: str, extra: dict[str, Any] | None = None) -> None:
    state = get_session(session_id)
    payload_extra = dict(extra or {})
    if state is None:
        payload_extra.setdefault("session_id", session_id)
    _append_mic_event(str(event), state=state, extra=payload_extra)


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

    source_wav = AUDIO_CACHE_ROOT / f"{video_id_clean}.wav"
    if not source_wav.exists():
        raise ValueError(
            f"Audio cache chybí: '{source_wav.name}'. Otevři Knihovnu a stáhni audio cache pro toto video."
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
    range_tag = f"f{_seconds_tag(start_s)}-t{_seconds_tag(bounded_end_s)}"
    pause_tag = f"g{_seconds_tag(safe_pause_s)}"
    repeats_tag = f"r{safe_repeats}"
    sync_tag = f"s{sync_rounds}"
    package_id = (
        f"loop_{video_id_clean}_{range_tag}_{pause_tag}_{repeats_tag}_{sync_tag}_{stamp}_{uuid.uuid4().hex[:6]}"
    )
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

    return {
        "package_id": package_id,
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
        state.reason_code = None
        state.error = None
        state.transcript = ""
        state.partial = ""
        state.sequence_timing = {}
        if state.orchestrator_mode == ORCHESTRATOR_MODE_V7:
            if not state.run_id:
                state.run_id = f"run_{state.session_id}"
            if not state.sequence_id:
                meta = _parse_auto_sequence_meta(state.model_params or {})
                state.sequence_id = str(meta.get("token") or state.session_id)
                state.sequence_index = _safe_int(meta.get("index"))
                state.sequence_total = _safe_int(meta.get("total"))
        _set_status(state, "recording")
    sequence_timing = _update_sequence_timing_on_start(state)
    orchestrator_payload = _update_v7_timing_on_start(state)
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
    with state._lock:
        state.error = str(error)
        state.reason_code = str(reason_code)
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
            "failure_reason_code": str(reason_code),
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
        return {"error": "session not found", "reason_code": "session_not_found"}
    if state.status != "recording":
        return {
            "error": f"session not recording (status={state.status})",
            "reason_code": "not_recording",
        }
    if not samples:
        with state._lock:
            state.chunk_count += 1
            state.dropped_chunks += 1
            state.drop_rate = state.dropped_chunks / max(1, state.chunk_count)
            state.reason_code = "empty_chunk"
        _maybe_persist_sequence_progress(state, phase="partial")
        return {"error": "empty chunk", "reason_code": "empty_chunk"}

    in_sr = max(1, int(sample_rate))
    prepared_samples, prepared_sr = _prepare_chunk_audio(state=state, samples=samples, sample_rate=in_sr)
    if not prepared_samples:
        with state._lock:
            state.chunk_count += 1
            state.dropped_chunks += 1
            state.drop_rate = state.dropped_chunks / max(1, state.chunk_count)
            state.reason_code = "empty_chunk"
        _maybe_persist_sequence_progress(state, phase="partial")
        return {"error": "empty chunk", "reason_code": "empty_chunk"}

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
            state.reason_code = "backpressure_drop"
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
            state.reason_code = _classify_reason(exc)
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
        state.reason_code = None

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
            state.reason_code = _classify_reason(exc)
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
            state.reason_code = "backpressure_drop"
        elif state.reason_code is None and not state.transcript.strip():
            state.reason_code = "no_tokens"
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
        "reason_code": state.reason_code,
        "error": state.error,
    }
    final_payload.update(_build_orchestrator_payload(state))
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
