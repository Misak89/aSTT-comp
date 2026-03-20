from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any

from packages.ingest.source_resolver import SourceEntry

try:
    import psutil
except ModuleNotFoundError:
    psutil = None  # type: ignore[assignment]


@dataclass(frozen=True)
class QwenRunConfig:
    model_path: str
    language: str | None = "Czech"
    device_map: str = "cpu"
    dtype: str = "bfloat16"
    max_new_tokens: int = 256
    cache_model: bool = True


_MODEL_CACHE: dict[tuple[str, str, str, int], Any] = {}


def run_qwen_source(
    *,
    source: SourceEntry,
    sample_seconds: int,
    start_offset_seconds: int = 0,
    output_dir: str | Path,
    config: QwenRunConfig,
) -> dict[str, Any]:
    if source.origin_type != "local_file":
        raise ValueError(f"qwen real mode supports local_file only: {source.source_id}")

    source_path = Path(source.value)
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    clip_path = out_dir / f"{source.source_id}_clip.wav"
    transcript_path = out_dir / f"{source.source_id}_qwen.txt"
    json_path = out_dir / f"{source.source_id}_qwen.json"

    _extract_clip(
        input_path=source_path,
        output_path=clip_path,
        start_offset_seconds=start_offset_seconds,
        sample_seconds=sample_seconds,
    )

    started = datetime.now(UTC)
    started_perf = time.perf_counter()

    process = psutil.Process(os.getpid()) if psutil is not None else None
    start_rss = _safe_rss_mb(process)
    start_cpu = _safe_cpu_seconds(process)

    model = _load_qwen_model(config)
    results = model.transcribe(
        audio=str(clip_path),
        language=config.language,
        return_time_stamps=False,
    )

    elapsed_s = max(0.001, time.perf_counter() - started_perf)
    end_rss = _safe_rss_mb(process)
    end_cpu = _safe_cpu_seconds(process)

    first = results[0] if isinstance(results, list) and results else None
    transcript_text = str(getattr(first, "text", "") or "").strip()
    detected_language = getattr(first, "language", None)

    transcript_path.write_text(transcript_text + ("\n" if transcript_text else ""), encoding="utf-8")
    payload = {
        "source_id": source.source_id,
        "source_label": source.label,
        "source_path": str(source_path),
        "clip_path": str(clip_path),
        "started_at_utc": started.isoformat(),
        "elapsed_seconds": round(elapsed_s, 4),
        "language": detected_language,
        "text": transcript_text,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    clip_duration = _probe_duration_seconds(clip_path) or float(sample_seconds)
    rtf = elapsed_s / max(0.1, clip_duration)

    cpu_percent = None
    if start_cpu is not None and end_cpu is not None:
        delta_cpu = max(0.0, end_cpu - start_cpu)
        cpu_count = max(1, os.cpu_count() or 1)
        cpu_percent = min(100.0, (delta_cpu / elapsed_s) * 100.0 / cpu_count)

    ram_mb = end_rss if end_rss is not None else start_rss
    latency_ms = int(elapsed_s * 1000.0)

    return {
        "source_id": source.source_id,
        "source_label": source.label,
        "origin_type": source.origin_type,
        "sample_seconds": sample_seconds,
        "clip_start_seconds": int(max(0, start_offset_seconds)),
        "source_media_path": str(source_path),
        "effective_input_path": str(clip_path),
        "wer": None,
        "cer": None,
        "latency_ms": round(float(latency_ms), 1),
        "first_word_latency_ms": None,
        "first_word_wall_ms": None,
        "first_word_audio_ms": None,
        "rtf": round(float(rtf), 4),
        "cpu_percent": round(cpu_percent, 1) if isinstance(cpu_percent, (int, float)) else None,
        "ram_mb": round(float(ram_mb), 1) if isinstance(ram_mb, (int, float)) else None,
        "speaker_attribution_accuracy": None,
        "speaker_confusion_rate": None,
        "transcript_text": transcript_text,
        "transcript_path": str(transcript_path),
        "json_path": str(json_path),
        "engine": "qwen_asr",
        "engine_started_at_utc": started.isoformat(),
        "engine_elapsed_seconds": round(elapsed_s, 4),
        "latency_mode": "offline_elapsed_proxy_ms",
        "detected_language": detected_language,
    }


def clear_qwen_model_cache() -> None:
    _MODEL_CACHE.clear()


def _load_qwen_model(config: QwenRunConfig):
    key = (
        str(Path(config.model_path).resolve()),
        config.device_map,
        config.dtype,
        int(config.max_new_tokens),
    )
    if config.cache_model and key in _MODEL_CACHE:
        return _MODEL_CACHE[key]

    from qwen_asr import Qwen3ASRModel

    torch_dtype = _resolve_torch_dtype(config.dtype)
    model = Qwen3ASRModel.from_pretrained(
        config.model_path,
        dtype=torch_dtype,
        device_map=config.device_map,
        max_new_tokens=int(config.max_new_tokens),
    )
    if config.cache_model:
        _MODEL_CACHE[key] = model
    return model


def _resolve_torch_dtype(name: str):
    import torch

    value = (name or "").strip().lower()
    if value == "float32":
        return torch.float32
    if value == "float16":
        return torch.float16
    return torch.bfloat16


def _extract_clip(
    *,
    input_path: Path,
    output_path: Path,
    start_offset_seconds: int,
    sample_seconds: int,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(max(0, int(start_offset_seconds))),
        "-t",
        str(max(1, int(sample_seconds))),
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(output_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg clip extraction failed: {proc.stderr[-500:]}")


def _probe_duration_seconds(path: Path) -> float | None:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    raw = (proc.stdout or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _safe_rss_mb(process) -> float | None:
    if process is None:
        return None
    try:
        return process.memory_info().rss / (1024 * 1024)
    except Exception:
        return None


def _safe_cpu_seconds(process) -> float | None:
    if process is None:
        return None
    try:
        cpu_times = process.cpu_times()
        return float(cpu_times.user + cpu_times.system)
    except Exception:
        return None
