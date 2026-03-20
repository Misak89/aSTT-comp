from __future__ import annotations

from array import array
from dataclasses import dataclass
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
class VoskRunConfig:
    model_dir: str
    sample_rate: int = 16000
    chunk_seconds: float = 0.20
    set_words: bool = False


@dataclass
class VoskLiveSessionState:
    recognizer: Any
    sample_rate: int
    started_perf: float
    chunk_seconds: float = 0.20
    audio_samples_processed: int = 0
    first_wall_ms: int | None = None
    first_audio_ms: int | None = None
    committed_text: str = ""
    current_text: str = ""


def detect_vosk_model_language(model_dir: str | Path) -> str | None:
    value = str(model_dir or "").replace("\\", "/").lower()
    if any(token in value for token in ("-cs-", "_cs_", "/cs/", "czech", "cesky", "czechia")):
        return "cs"
    if any(token in value for token in ("-en-", "_en_", "/en/", "english")):
        return "en"
    return None


def resolve_vosk_model_dir(model_store_root: str | Path) -> Path | None:
    root = Path(model_store_root)
    candidate_roots = [
        root / "vosk_small_cs_0_4",
        root / "vosk_model_small_cs_0_4",
        root,
    ]
    candidates: list[Path] = []
    seen: set[str] = set()

    for candidate_root in candidate_roots:
        if not candidate_root.exists():
            continue
        for conf in candidate_root.rglob("conf/model.conf"):
            directory = conf.parent.parent
            if not directory.is_dir():
                continue
            if not (directory / "am" / "final.mdl").exists():
                continue
            key = str(directory.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(directory)

    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            0 if detect_vosk_model_language(item) == "cs" else 1,
            len(str(item)),
            str(item).lower(),
        )
    )
    return candidates[0]


def run_vosk_source(
    *,
    source: SourceEntry,
    sample_seconds: int,
    start_offset_seconds: int = 0,
    output_dir: str | Path,
    config: VoskRunConfig,
) -> dict[str, Any]:
    if source.origin_type != "local_file":
        raise ValueError(f"VOSK real mode supports local_file only: {source.source_id}")

    source_path = Path(source.value)
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    clip_path = out_dir / f"{source.source_id}_clip.wav"
    transcript_path = out_dir / f"{source.source_id}_vosk.txt"
    json_path = out_dir / f"{source.source_id}_vosk.json"

    _extract_clip(
        input_path=source_path,
        output_path=clip_path,
        start_offset_seconds=start_offset_seconds,
        sample_seconds=sample_seconds,
        sample_rate=config.sample_rate,
    )

    started = datetime.now(UTC)
    started_perf = time.perf_counter()
    process = psutil.Process(os.getpid()) if psutil is not None else None
    start_rss = _safe_rss_mb(process)
    start_cpu = _safe_cpu_seconds(process)

    sample_rate, samples = _read_wave_f32(clip_path)
    session = create_vosk_live_session(
        config=VoskRunConfig(
            model_dir=config.model_dir,
            sample_rate=config.sample_rate,
            chunk_seconds=config.chunk_seconds,
            set_words=config.set_words,
        )
    )
    stream_result = transcribe_vosk_live_chunk(
        session=session,
        sample_rate=sample_rate,
        samples=samples,
    )
    final_result = finalize_vosk_live_session(session=session)

    transcript_text = str(final_result.get("text") or stream_result.get("text") or "").strip()
    elapsed_s = max(0.001, time.perf_counter() - started_perf)
    end_rss = _safe_rss_mb(process)
    end_cpu = _safe_cpu_seconds(process)

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
        "chunk_seconds": config.chunk_seconds,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    clip_duration = _probe_duration_seconds(clip_path) or float(sample_seconds)
    rtf = elapsed_s / max(0.1, clip_duration)

    cpu_percent = None
    if start_cpu is not None and end_cpu is not None:
        delta_cpu = max(0.0, end_cpu - start_cpu)
        cpu_count = max(1, os.cpu_count() or 1)
        cpu_percent = min(100.0, (delta_cpu / elapsed_s) * 100.0 / cpu_count)

    first_word_wall_ms = stream_result.get("first_word_wall_ms") or final_result.get("first_word_wall_ms")
    first_word_audio_ms = stream_result.get("first_word_audio_ms") or final_result.get("first_word_audio_ms")
    first_word_latency_ms = stream_result.get("first_word_latency_ms") or final_result.get("first_word_latency_ms")
    if not isinstance(first_word_latency_ms, (int, float)):
        wall = float(first_word_wall_ms) if isinstance(first_word_wall_ms, (int, float)) else 0.0
        audio = float(first_word_audio_ms) if isinstance(first_word_audio_ms, (int, float)) else 0.0
        first_word_latency_ms = max(wall, audio) if wall or audio else None

    latency_ms = first_word_latency_ms if isinstance(first_word_latency_ms, (int, float)) else int(elapsed_s * 1000.0)
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
        "first_word_latency_ms": round(float(first_word_latency_ms), 1) if isinstance(first_word_latency_ms, (int, float)) else None,
        "first_word_wall_ms": round(float(first_word_wall_ms), 1) if isinstance(first_word_wall_ms, (int, float)) else None,
        "first_word_audio_ms": round(float(first_word_audio_ms), 1) if isinstance(first_word_audio_ms, (int, float)) else None,
        "rtf": round(float(rtf), 4),
        "cpu_percent": round(cpu_percent, 1) if isinstance(cpu_percent, (int, float)) else None,
        "ram_mb": round(float(ram_mb), 1) if isinstance(ram_mb, (int, float)) else None,
        "speaker_attribution_accuracy": None,
        "speaker_confusion_rate": None,
        "transcript_text": transcript_text,
        "transcript_path": str(transcript_path),
        "json_path": str(json_path),
        "engine": "vosk",
        "engine_started_at_utc": started.isoformat(),
        "engine_elapsed_seconds": round(elapsed_s, 4),
        "latency_mode": "online_first_text_or_elapsed_ms",
    }


def create_vosk_live_session(*, config: VoskRunConfig) -> VoskLiveSessionState:
    model = _load_vosk_model(config.model_dir)
    recognizer = _create_vosk_recognizer(
        model=model,
        sample_rate=config.sample_rate,
        set_words=config.set_words,
    )
    return VoskLiveSessionState(
        recognizer=recognizer,
        sample_rate=int(max(8000, config.sample_rate)),
        started_perf=time.perf_counter(),
        chunk_seconds=max(0.05, float(config.chunk_seconds)),
    )


def transcribe_vosk_live_chunk(
    *,
    session: VoskLiveSessionState,
    sample_rate: int,
    samples: list[float],
) -> dict[str, Any]:
    started_perf = time.perf_counter()
    previous_text = session.current_text
    normalized_samples = _resample_f32_samples(
        samples=samples,
        source_rate=max(1, int(sample_rate)),
        target_rate=max(1, int(session.sample_rate)),
    )
    pcm_bytes = _f32_to_pcm16le_bytes(normalized_samples)
    if not pcm_bytes:
        elapsed_s = max(0.0001, time.perf_counter() - started_perf)
        return {
            "text": session.current_text,
            "text_delta": "",
            "latency_ms": round(elapsed_s * 1000.0, 1),
            "first_word_latency_ms": session.first_wall_ms,
            "first_word_wall_ms": session.first_wall_ms,
            "first_word_audio_ms": session.first_audio_ms,
            "engine_elapsed_seconds": round(elapsed_s, 4),
            "audio_samples_processed": session.audio_samples_processed,
        }

    chunk_bytes = max(3200, int(session.sample_rate * max(0.05, session.chunk_seconds) * 2))
    offset = 0
    partial = ""
    while offset < len(pcm_bytes):
        piece = pcm_bytes[offset : offset + chunk_bytes]
        offset += len(piece)
        session.audio_samples_processed += len(piece) // 2
        finalized = bool(session.recognizer.AcceptWaveform(piece))
        if finalized:
            committed_delta = _parse_vosk_result_text(session.recognizer.Result(), key="text")
            session.committed_text = _append_transcript_text(session.committed_text, committed_delta)
            partial = ""
        else:
            partial = _parse_vosk_result_text(session.recognizer.PartialResult(), key="partial")
        session.current_text = _compose_vosk_text(session.committed_text, partial)
        if session.first_wall_ms is None and session.current_text:
            session.first_wall_ms = int((time.perf_counter() - session.started_perf) * 1000.0)
            session.first_audio_ms = int((session.audio_samples_processed / max(1, session.sample_rate)) * 1000.0)

    if not partial:
        partial = _parse_vosk_result_text(session.recognizer.PartialResult(), key="partial")
    session.current_text = _compose_vosk_text(session.committed_text, partial)
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


def finalize_vosk_live_session(*, session: VoskLiveSessionState) -> dict[str, Any]:
    final_delta = _parse_vosk_result_text(session.recognizer.FinalResult(), key="text")
    session.committed_text = _append_transcript_text(session.committed_text, final_delta)
    session.current_text = session.committed_text
    if session.first_wall_ms is None and session.current_text:
        session.first_wall_ms = int((time.perf_counter() - session.started_perf) * 1000.0)
    if session.first_audio_ms is None and session.current_text:
        session.first_audio_ms = int((session.audio_samples_processed / max(1, session.sample_rate)) * 1000.0)

    latency_ms = None
    if session.first_wall_ms is not None or session.first_audio_ms is not None:
        latency_ms = max(session.first_wall_ms or 0, session.first_audio_ms or 0)

    return {
        "text": session.current_text,
        "text_delta": final_delta,
        "latency_ms": round(float(latency_ms), 1) if isinstance(latency_ms, (int, float)) else None,
        "first_word_latency_ms": round(float(latency_ms), 1) if isinstance(latency_ms, (int, float)) else None,
        "first_word_wall_ms": round(float(session.first_wall_ms), 1) if isinstance(session.first_wall_ms, (int, float)) else None,
        "first_word_audio_ms": round(float(session.first_audio_ms), 1) if isinstance(session.first_audio_ms, (int, float)) else None,
        "audio_samples_processed": session.audio_samples_processed,
    }


def _load_vosk_model(model_dir: str):
    try:
        import vosk  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Python package vosk is not installed. Install it before real mode with vosk_small_cs_0_4."
        ) from exc

    try:
        vosk.SetLogLevel(-1)
    except Exception:
        pass
    return vosk.Model(model_dir)


def _create_vosk_recognizer(*, model: Any, sample_rate: int, set_words: bool):
    try:
        import vosk  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Python package vosk is not installed. Install it before real mode with vosk_small_cs_0_4."
        ) from exc
    recognizer = vosk.KaldiRecognizer(model, float(sample_rate))
    if hasattr(recognizer, "SetWords"):
        recognizer.SetWords(bool(set_words))
    if hasattr(recognizer, "SetPartialWords"):
        recognizer.SetPartialWords(False)
    return recognizer


def _parse_vosk_result_text(raw: object, *, key: str) -> str:
    text = ""
    if isinstance(raw, dict):
        text = str(raw.get(key) or raw.get("text") or raw.get("partial") or "")
    elif isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return ""
        try:
            payload = json.loads(stripped)
        except Exception:
            return stripped
        if isinstance(payload, dict):
            text = str(payload.get(key) or payload.get("text") or payload.get("partial") or "")
    return " ".join(text.strip().split())


def _append_transcript_text(current: str, delta: str) -> str:
    left = str(current or "").strip()
    right = str(delta or "").strip()
    if not left:
        return right
    if not right:
        return left
    return f"{left} {right}".strip()


def _compose_vosk_text(committed: str, partial: str) -> str:
    committed_clean = str(committed or "").strip()
    partial_clean = str(partial or "").strip()
    if not committed_clean:
        return partial_clean
    if not partial_clean:
        return committed_clean
    return f"{committed_clean} {partial_clean}".strip()


def _diff_transcript_suffix(previous_text: str, current_text: str) -> str:
    before = str(previous_text or "").strip()
    after = str(current_text or "").strip()
    if not before:
        return after
    if after.startswith(before):
        return after[len(before) :].strip()
    return after


def _f32_to_pcm16le_bytes(samples: list[float]) -> bytes:
    pcm = array("h")
    for value in samples:
        sample = max(-1.0, min(1.0, float(value)))
        pcm.append(int(sample * 32767.0))
    return pcm.tobytes()


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


def _read_wave_f32(path: Path) -> tuple[int, list[float]]:
    with wave.open(str(path), "rb") as handle:
        sample_rate = int(handle.getframerate())
        channels = int(handle.getnchannels())
        sample_width = int(handle.getsampwidth())
        frame_count = int(handle.getnframes())
        raw = handle.readframes(frame_count)

    if sample_width != 2:
        raise RuntimeError(f"Expected 16-bit PCM WAV input for VOSK adapter, got sample_width={sample_width}")

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
        raise RuntimeError(f"ffmpeg clip extraction failed for VOSK input: {proc.stderr[-500:]}")


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
