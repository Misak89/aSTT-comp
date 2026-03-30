from __future__ import annotations

from array import array
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
import subprocess
import time
from typing import Any
import wave

from packages.ingest.source_resolver import SourceEntry

try:
    import psutil
except ModuleNotFoundError:
    psutil = None  # type: ignore[assignment]


@dataclass(frozen=True)
class FasterWhisperRunConfig:
    model_path: str
    language: str = "cs"
    threads: int = 4
    beam_size: int = 1
    best_of: int = 1
    device: str = "cpu"
    compute_type: str = "int8"


@dataclass
class FasterWhisperLiveSessionState:
    model: Any
    sample_rate: int
    analysis_interval_ms: int
    analysis_window_seconds: int
    language: str
    beam_size: int
    best_of: int
    started_perf: float
    audio_samples_processed: int = 0
    first_wall_ms: int | None = None
    first_audio_ms: int | None = None
    current_text: str = ""
    pcm16: array = field(default_factory=lambda: array("h"))
    last_analysis_perf: float = 0.0


def resolve_faster_whisper_model_path(
    model_store_root: str | Path,
    model_id: str = "faster_whisper_small_cs_int8",
) -> Path | None:
    root = Path(model_store_root)
    candidates = [
        root / model_id,
        root / "faster_whisper_small_cs_int8",
        root / "faster_whisper_small_cs",
    ]
    for candidate in candidates:
        if (candidate / "model.bin").exists():
            return candidate
    return None


def run_faster_whisper_source(
    *,
    source: SourceEntry,
    sample_seconds: int,
    start_offset_seconds: int = 0,
    output_dir: str | Path,
    config: FasterWhisperRunConfig,
) -> dict[str, Any]:
    if source.origin_type != "local_file":
        raise ValueError(f"faster_whisper real mode supports local_file only: {source.source_id}")

    source_path = Path(source.value)
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    clip_path = out_dir / f"{source.source_id}_clip.wav"
    transcript_path = out_dir / f"{source.source_id}_faster_whisper.txt"
    json_path = out_dir / f"{source.source_id}_faster_whisper.json"

    _extract_clip(
        input_path=source_path,
        output_path=clip_path,
        start_offset_seconds=start_offset_seconds,
        sample_seconds=sample_seconds,
        sample_rate=16000,
    )
    sample_rate, samples = _read_wave_f32(clip_path)

    model = _load_faster_whisper_model(config)
    process = psutil.Process(os.getpid()) if psutil is not None else None
    start_rss = _safe_rss_mb(process)
    start_cpu = _safe_cpu_seconds(process)

    started = datetime.now(UTC)
    started_perf = time.perf_counter()
    transcript_text, segments = _transcribe_audio_f32(model=model, config=config, samples=samples)
    elapsed_s = max(0.001, time.perf_counter() - started_perf)

    end_rss = _safe_rss_mb(process)
    end_cpu = _safe_cpu_seconds(process)
    clip_duration = _probe_duration_seconds(clip_path) or float(sample_seconds)
    rtf = elapsed_s / max(0.1, clip_duration)

    cpu_percent = None
    if start_cpu is not None and end_cpu is not None:
        delta_cpu = max(0.0, end_cpu - start_cpu)
        cpu_count = max(1, os.cpu_count() or 1)
        cpu_percent = min(100.0, (delta_cpu / elapsed_s) * 100.0 / cpu_count)

    first_word_audio_ms = None
    if segments:
        first = segments[0]
        if isinstance(first.get("start"), (int, float)):
            first_word_audio_ms = float(first["start"]) * 1000.0

    transcript_path.write_text(transcript_text + ("\n" if transcript_text else ""), encoding="utf-8")
    payload = {
        "source_id": source.source_id,
        "source_label": source.label,
        "source_path": str(source_path),
        "clip_path": str(clip_path),
        "started_at_utc": started.isoformat(),
        "elapsed_seconds": round(elapsed_s, 4),
        "text": transcript_text,
        "sample_rate": sample_rate,
        "language": config.language,
        "threads": config.threads,
        "beam_size": config.beam_size,
        "best_of": config.best_of,
        "device": config.device,
        "compute_type": config.compute_type,
        "segments": segments,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    latency_ms = int(elapsed_s * 1000.0)
    ram_mb = end_rss if end_rss is not None else start_rss
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
        "first_word_audio_ms": round(float(first_word_audio_ms), 1) if isinstance(first_word_audio_ms, (int, float)) else None,
        "rtf": round(float(rtf), 4),
        "cpu_percent": round(cpu_percent, 1) if isinstance(cpu_percent, (int, float)) else None,
        "ram_mb": round(float(ram_mb), 1) if isinstance(ram_mb, (int, float)) else None,
        "speaker_attribution_accuracy": None,
        "speaker_confusion_rate": None,
        "transcript_text": transcript_text,
        "transcript_path": str(transcript_path),
        "json_path": str(json_path),
        "_segments": segments,
        "engine": "faster_whisper",
        "engine_started_at_utc": started.isoformat(),
        "engine_elapsed_seconds": round(elapsed_s, 4),
        "latency_mode": "offline_elapsed_proxy_ms",
    }


def create_faster_whisper_live_session(
    *,
    config: FasterWhisperRunConfig,
    sample_rate: int = 16000,
    analysis_interval_ms: int = 1200,
    analysis_window_seconds: int = 12,
) -> FasterWhisperLiveSessionState:
    model = _load_faster_whisper_model(config)
    return FasterWhisperLiveSessionState(
        model=model,
        sample_rate=max(8000, int(sample_rate)),
        analysis_interval_ms=max(300, int(analysis_interval_ms)),
        analysis_window_seconds=max(3, int(analysis_window_seconds)),
        language=str(config.language),
        beam_size=int(max(1, config.beam_size)),
        best_of=int(max(1, config.best_of)),
        started_perf=time.perf_counter(),
    )


def transcribe_faster_whisper_live_chunk(
    *,
    session: FasterWhisperLiveSessionState,
    sample_rate: int,
    samples: list[float],
) -> dict[str, Any]:
    started_perf = time.perf_counter()
    previous_text = session.current_text

    normalized = _resample_f32_samples(
        samples=samples,
        source_rate=max(1, int(sample_rate)),
        target_rate=max(1, int(session.sample_rate)),
    )
    if normalized:
        session.pcm16.extend(_f32_to_pcm16_array(normalized))
        session.audio_samples_processed += len(normalized)

    if _should_analyze_live_buffer(session=session):
        inferred = _infer_live_text(session=session, tail_only=True)
        if inferred:
            session.current_text = inferred
            session.last_analysis_perf = time.perf_counter()

    if session.first_wall_ms is None and session.current_text:
        session.first_wall_ms = int((time.perf_counter() - session.started_perf) * 1000.0)
    if session.first_audio_ms is None and session.current_text:
        session.first_audio_ms = int((session.audio_samples_processed / max(1, session.sample_rate)) * 1000.0)

    latency_ms = None
    if session.first_wall_ms is not None or session.first_audio_ms is not None:
        latency_ms = max(session.first_wall_ms or 0, session.first_audio_ms or 0)

    transcript_delta = _diff_transcript_suffix(previous_text, session.current_text)
    elapsed_s = max(0.0001, time.perf_counter() - started_perf)
    return {
        "text": session.current_text,
        "text_delta": transcript_delta,
        "latency_ms": round(float(latency_ms), 1) if isinstance(latency_ms, (int, float)) else round(elapsed_s * 1000.0, 1),
        "first_word_latency_ms": round(float(latency_ms), 1) if isinstance(latency_ms, (int, float)) else None,
        "first_word_wall_ms": round(float(session.first_wall_ms), 1) if isinstance(session.first_wall_ms, (int, float)) else None,
        "first_word_audio_ms": round(float(session.first_audio_ms), 1) if isinstance(session.first_audio_ms, (int, float)) else None,
        "engine_elapsed_seconds": round(elapsed_s, 4),
        "audio_samples_processed": session.audio_samples_processed,
    }


def finalize_faster_whisper_live_session(*, session: FasterWhisperLiveSessionState) -> dict[str, Any]:
    previous_text = session.current_text
    if session.pcm16:
        inferred = _infer_live_text(session=session, tail_only=False)
        if inferred:
            session.current_text = inferred

    if session.first_wall_ms is None and session.current_text:
        session.first_wall_ms = int((time.perf_counter() - session.started_perf) * 1000.0)
    if session.first_audio_ms is None and session.current_text:
        session.first_audio_ms = int((session.audio_samples_processed / max(1, session.sample_rate)) * 1000.0)

    latency_ms = None
    if session.first_wall_ms is not None or session.first_audio_ms is not None:
        latency_ms = max(session.first_wall_ms or 0, session.first_audio_ms or 0)

    return {
        "text": session.current_text,
        "text_delta": _diff_transcript_suffix(previous_text, session.current_text),
        "latency_ms": round(float(latency_ms), 1) if isinstance(latency_ms, (int, float)) else None,
        "first_word_latency_ms": round(float(latency_ms), 1) if isinstance(latency_ms, (int, float)) else None,
        "first_word_wall_ms": round(float(session.first_wall_ms), 1) if isinstance(session.first_wall_ms, (int, float)) else None,
        "first_word_audio_ms": round(float(session.first_audio_ms), 1) if isinstance(session.first_audio_ms, (int, float)) else None,
        "audio_samples_processed": session.audio_samples_processed,
    }


def _load_faster_whisper_model(config: FasterWhisperRunConfig):
    model_dir = Path(config.model_path)
    if not (model_dir / "model.bin").exists():
        raise RuntimeError(
            f"faster-whisper model missing or invalid: {model_dir} (expected model.bin in CTranslate2 format)"
        )
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Python package faster-whisper is not installed. Install it in venv before using faster_whisper_* models."
        ) from exc

    kwargs: dict[str, Any] = {
        "device": str(config.device),
        "compute_type": str(config.compute_type),
        "cpu_threads": int(max(1, config.threads)),
        "local_files_only": True,
    }
    try:
        return WhisperModel(str(model_dir), **kwargs)
    except TypeError:
        kwargs.pop("local_files_only", None)
        return WhisperModel(str(model_dir), **kwargs)


def _transcribe_audio_f32(
    *,
    model: Any,
    config: FasterWhisperRunConfig,
    samples: list[float],
) -> tuple[str, list[dict[str, Any]]]:
    if not samples:
        return "", []
    try:
        import numpy as np
    except ModuleNotFoundError as exc:
        raise RuntimeError("numpy is required for faster-whisper adapter.") from exc

    audio = np.asarray(samples, dtype=np.float32)
    segments_iter, _info = model.transcribe(
        audio,
        language=str(config.language),
        beam_size=int(max(1, config.beam_size)),
        best_of=int(max(1, config.best_of)),
        condition_on_previous_text=False,
    )
    texts: list[str] = []
    segments: list[dict[str, Any]] = []
    for segment in segments_iter:
        seg_text = str(getattr(segment, "text", "") or "").strip()
        seg_start = float(getattr(segment, "start", 0.0) or 0.0)
        seg_end = float(getattr(segment, "end", 0.0) or 0.0)
        if seg_text:
            texts.append(seg_text)
        segments.append({"start": seg_start, "end": seg_end, "text": seg_text})
    return " ".join(texts).strip(), segments


def _should_analyze_live_buffer(*, session: FasterWhisperLiveSessionState) -> bool:
    if not session.pcm16:
        return False
    now = time.perf_counter()
    if session.last_analysis_perf <= 0:
        min_samples = int(max(0.30, session.analysis_interval_ms / 1000.0) * session.sample_rate)
        return len(session.pcm16) >= max(1, min_samples)
    return (now - session.last_analysis_perf) * 1000.0 >= float(session.analysis_interval_ms)


def _infer_live_text(*, session: FasterWhisperLiveSessionState, tail_only: bool) -> str:
    if not session.pcm16:
        return ""
    window_samples = len(session.pcm16)
    if tail_only:
        max_window = max(1, int(session.analysis_window_seconds * session.sample_rate))
        window_samples = min(window_samples, max_window)

    start_idx = max(0, len(session.pcm16) - window_samples)
    pcm_window = session.pcm16[start_idx:]
    samples = [max(-1.0, min(1.0, s / 32768.0)) for s in pcm_window]

    cfg = FasterWhisperRunConfig(
        model_path="",
        language=str(session.language),
        threads=1,
        beam_size=int(max(1, session.beam_size)),
        best_of=int(max(1, session.best_of)),
        device="cpu",
        compute_type="int8",
    )
    text, _segments = _transcribe_audio_f32(
        model=session.model,
        config=cfg,
        samples=samples,
    )
    return " ".join(text.split())


def _extract_clip(
    *,
    input_path: Path,
    output_path: Path,
    start_offset_seconds: int,
    sample_seconds: int,
    sample_rate: int,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
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
            str(max(8000, int(sample_rate))),
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg clip extraction failed: {proc.stderr[-500:]}")


def _probe_duration_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as wf:
            return wf.getnframes() / max(1, wf.getframerate())
    except Exception:
        return None


def _read_wave_f32(path: Path) -> tuple[int, list[float]]:
    with wave.open(str(path), "rb") as handle:
        sample_rate = int(handle.getframerate())
        channels = int(handle.getnchannels())
        sample_width = int(handle.getsampwidth())
        frame_count = int(handle.getnframes())
        raw = handle.readframes(frame_count)

    if sample_width != 2:
        raise RuntimeError(
            f"Expected 16-bit PCM WAV input for faster-whisper adapter, got sample_width={sample_width}"
        )
    pcm = array("h")
    pcm.frombytes(raw)
    if channels <= 1:
        values = pcm
    else:
        mono = array("h")
        for index in range(0, len(pcm), channels):
            chunk = pcm[index : index + channels]
            if not chunk:
                continue
            mono.append(int(sum(chunk) / len(chunk)))
        values = mono
    return sample_rate, [max(-1.0, min(1.0, sample / 32768.0)) for sample in values]


def _safe_cpu_seconds(proc: Any | None) -> float | None:
    if proc is None:
        return None
    try:
        cpu = proc.cpu_times()
        return float(cpu.user + cpu.system)
    except Exception:
        return None


def _safe_rss_mb(proc: Any | None) -> float | None:
    if proc is None:
        return None
    try:
        return proc.memory_info().rss / (1024 * 1024)
    except Exception:
        return None


def _resample_f32_samples(*, samples: list[float], source_rate: int, target_rate: int) -> list[float]:
    if source_rate <= 0 or target_rate <= 0 or source_rate == target_rate:
        return [float(item) for item in samples]
    if not samples:
        return []
    source = [float(item) for item in samples]
    target_length = max(1, int(round(len(source) * float(target_rate) / float(source_rate))))
    if target_length == 1:
        return [source[0]]
    ratio = float(source_rate) / float(target_rate)
    output: list[float] = []
    source_max_index = len(source) - 1
    for index in range(target_length):
        source_index = index * ratio
        left = int(math.floor(source_index))
        right = min(source_max_index, left + 1)
        frac = source_index - left
        sample = source[left] * (1.0 - frac) + source[right] * frac
        output.append(float(sample))
    return output


def _f32_to_pcm16_array(samples: list[float]) -> array:
    pcm = array("h")
    for value in samples:
        clipped = max(-1.0, min(1.0, float(value)))
        pcm.append(int(clipped * 32767.0))
    return pcm


def _diff_transcript_suffix(previous_text: str, current_text: str) -> str:
    before = str(previous_text or "").strip()
    after = str(current_text or "").strip()
    if not before:
        return after
    if after.startswith(before):
        return after[len(before) :].strip()
    return after
