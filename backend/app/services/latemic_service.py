from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import wave
from array import array
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import LATE_MIC_ROOT, MODEL_STORE_ROOT
from packages.adapters._registry import REGISTRY
from packages.benchmarks.runners.streaming_runner import transcribe_latemic_segment

MAX_PCM16_BYTES = 64 * 1024 * 1024


def create_segment_transcription(
    *,
    model_id: str,
    pcm16_base64: str,
    sample_rate: int,
    model_params: dict[str, Any] | None = None,
    lag_budget_s: float | None = None,
    target_segment_s: float | None = None,
    queue_wait_s: float | None = None,
    segment_index: int | None = None,
    delete_policy: str = "after_transcript",
    client_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if model_id not in REGISTRY:
        raise ValueError(f"Neznámý model_id: {model_id}")
    safe_sample_rate = int(sample_rate)
    if safe_sample_rate < 8000 or safe_sample_rate > 48000:
        raise ValueError("sample_rate musí být v rozsahu 8000-48000 Hz")
    raw = _decode_pcm16(pcm16_base64)
    if not raw:
        raise ValueError("Prázdný LateMic segment")
    if len(raw) % 2 != 0:
        raise ValueError("pcm16_base64 musí obsahovat celé 16-bit vzorky")
    safe_queue_wait_s = max(0.0, float(queue_wait_s)) if isinstance(queue_wait_s, (int, float)) else 0.0

    created_at = _iso_now()
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    segment_id = f"latemic_{stamp}_{_short_hash(raw)}_{secrets.token_hex(3)}"
    segment_dir = LATE_MIC_ROOT / "segments" / segment_id
    segment_dir.mkdir(parents=True, exist_ok=True)
    wav_path = segment_dir / "segment.wav"
    transcript_dir = segment_dir / "transcript"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = segment_dir / "manifest.json"
    result_path = segment_dir / "result.json"

    _write_pcm16_wav(wav_path, raw, sample_rate=safe_sample_rate)
    audio_sha256 = hashlib.sha256(raw).hexdigest()
    duration_s = len(raw) / 2 / max(1, safe_sample_rate)
    manifest = {
        "schema": "latemic.segment.v1",
        "segment_id": segment_id,
        "created_at": created_at,
        "model_id": model_id,
        "model_params": dict(model_params or {}),
        "audio_authority": "real_mic_pcm16_upload",
        "transcript_source_required": "latemic_segment",
        "reference_text_allowed": False,
        "history_fallback_allowed": False,
        "sample_rate": safe_sample_rate,
        "channels": 1,
        "sample_width_bytes": 2,
        "audio_payload_bytes": len(raw),
        "audio_sha256": audio_sha256,
        "audio_duration_s": round(duration_s, 3),
        "lag_budget_s": lag_budget_s,
        "target_segment_s": target_segment_s,
        "queue_wait_s": round(safe_queue_wait_s, 4),
        "segment_index": segment_index,
        "delete_policy": delete_policy,
        "client_meta": client_meta or {},
        "wav_path": str(wav_path),
    }
    _write_json(manifest_path, manifest)

    started = time.perf_counter()
    success = False
    error: str | None = None
    helper_result: dict[str, Any] = {}
    try:
        helper_result = transcribe_latemic_segment(
            wav_path=wav_path,
            model_id=model_id,
            model_params=dict(model_params or {}),
            model_store_root=MODEL_STORE_ROOT,
            output_dir=transcript_dir,
            source_id=segment_id,
            label=f"LateMic segment {segment_id}",
        )
        success = True
    except Exception as exc:
        error = str(exc)
    elapsed_s = max(0.001, time.perf_counter() - started)

    transcript = str(helper_result.get("transcript_text") or "").strip() if success else ""
    decode_s = float(helper_result.get("engine_elapsed_seconds") or elapsed_s) if success else elapsed_s
    max_visible_lag_s = duration_s + safe_queue_wait_s + decode_s
    tail_lag_s = decode_s
    over_budget_s = None
    if isinstance(lag_budget_s, (int, float)):
        over_budget_s = max(0.0, max_visible_lag_s - float(lag_budget_s))

    wav_deleted = _apply_delete_policy(wav_path, delete_policy=delete_policy, success=success)
    result = {
        "schema": "latemic.result.v1",
        "segment_id": segment_id,
        "created_at": created_at,
        "finished_at": _iso_now(),
        "status": "ok" if success else "error",
        "error": error,
        "model_id": model_id,
        "model_params_used": dict(model_params or {}),
        "transcript": transcript,
        "transcript_source": "latemic_segment",
        "audio_authority": "real_mic_segment_wav",
        "reference_text_used": False,
        "history_fallback_used": False,
        "audio_sha256": audio_sha256,
        "audio_payload_bytes": len(raw),
        "audio_duration_s": round(duration_s, 3),
        "sample_rate": safe_sample_rate,
        "lag_budget_s": lag_budget_s,
        "target_segment_s": target_segment_s,
        "queue_wait_s": round(safe_queue_wait_s, 4),
        "decode_s": round(decode_s, 4),
        "tail_lag_s": round(tail_lag_s, 4),
        "max_visible_lag_s": round(max_visible_lag_s, 4),
        "over_budget_s": round(over_budget_s, 4) if over_budget_s is not None else None,
        "rtf": helper_result.get("rtf"),
        "latency_ms": helper_result.get("latency_ms"),
        "helper_result": helper_result,
        "manifest_path": str(manifest_path),
        "result_path": str(result_path),
        "wav_path": str(wav_path),
        "wav_deleted": wav_deleted,
        "wav_retained": wav_path.exists(),
    }
    _write_json(result_path, result)
    return result


def get_segment_result(segment_id: str) -> dict[str, Any]:
    if not _safe_segment_id(segment_id):
        raise ValueError("Neplatný LateMic segment_id")
    result_path = LATE_MIC_ROOT / "segments" / segment_id / "result.json"
    if not result_path.exists():
        raise FileNotFoundError(segment_id)
    return json.loads(result_path.read_text(encoding="utf-8"))


def delete_segment_wav(segment_id: str) -> dict[str, Any]:
    if not _safe_segment_id(segment_id):
        raise ValueError("Neplatný LateMic segment_id")
    segment_dir = LATE_MIC_ROOT / "segments" / segment_id
    result_path = segment_dir / "result.json"
    wav_path = segment_dir / "segment.wav"
    if not result_path.exists():
        raise FileNotFoundError(segment_id)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    existed = wav_path.exists()
    deleted = False
    if existed:
        try:
            wav_path.unlink()
            deleted = True
        except Exception:
            deleted = False
    if deleted or not wav_path.exists():
        result["wav_deleted"] = True
        result["wav_retained"] = False
        result["wav_deleted_at"] = _iso_now()
        _write_json(result_path, result)
    return {
        "segment_id": segment_id,
        "wav_existed": existed,
        "wav_deleted": deleted,
        "wav_retained": wav_path.exists(),
    }


def _decode_pcm16(value: str) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise ValueError("pcm16_base64 není validní base64") from exc
    if len(raw) > MAX_PCM16_BYTES:
        raise ValueError(f"LateMic segment je příliš velký ({len(raw)} B)")
    return raw


def _write_pcm16_wav(path: Path, raw: bytes, *, sample_rate: int) -> None:
    pcm = array("h")
    pcm.frombytes(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


def _apply_delete_policy(path: Path, *, delete_policy: str, success: bool) -> bool:
    should_delete = False
    if delete_policy == "after_transcript":
        should_delete = success
    elif delete_policy == "keep_failed":
        should_delete = success
    elif delete_policy == "after_run":
        should_delete = False
    elif delete_policy == "keep_all":
        should_delete = False
    else:
        should_delete = success
    if should_delete:
        try:
            path.unlink(missing_ok=True)
            return True
        except Exception:
            return False
    return False


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _short_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:8]


def _safe_segment_id(value: str) -> bool:
    return bool(value) and all(ch.isalnum() or ch in {"_", "-"} for ch in value)
