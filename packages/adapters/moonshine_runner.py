"""
Moonshine adapter — streaming ASR (moonshine-voice).

Instalace: pip install moonshine-voice
Modely: tiny (34M), small (123M), medium (245M)
Jazyky: en, es, zh, ja, ko, vi, uk, ar (zatím NE cs)

Architektura:
- Plně streaming — přijímá audio chunky (list[float]) přes live session API
- Nevyžaduje soubor — vhodný pro mic i yt-dlp stream
- Pro benchmark s lokálním souborem: soubor se čte a posílá po chuncích
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from packages.common.network_access import ensure_online_allowed
from packages.ingest.source_resolver import SourceEntry

try:
    import psutil
except ModuleNotFoundError:
    psutil = None  # type: ignore[assignment]


@dataclass(frozen=True)
class MoonshineRunConfig:
    model_path: str
    model_arch: str = "medium"          # "tiny" | "small" | "medium"
    analysis_interval_ms: int = 500
    language: str = "en"


@dataclass
class MoonshineLiveSessionState:
    transcriber: Any
    started_perf: float
    audio_samples_processed: int = 0
    first_wall_ms: int | None = None
    first_audio_ms: int | None = None
    current_text: str = ""
    lines: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.lines is None:
            self.lines = []


# ---------------------------------------------------------------------------
# Benchmark mode (soubor → chunky → live session)
# ---------------------------------------------------------------------------

def run_moonshine_source(
    *,
    source: SourceEntry,
    sample_seconds: int,
    start_offset_seconds: int = 0,
    output_dir: str | Path,
    config: MoonshineRunConfig,
) -> dict[str, Any]:
    """Spustí Moonshine benchmark z lokálního audio souboru."""
    if source.origin_type != "local_file":
        raise ValueError(f"moonshine real mode supports local_file only: {source.source_id}")

    import subprocess
    import wave
    from array import array

    source_path = Path(source.value)
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    clip_path = out_dir / f"{source.source_id}_clip.wav"
    transcript_path = out_dir / f"{source.source_id}_moonshine.txt"
    json_path = out_dir / f"{source.source_id}_moonshine.json"

    # Extrahuj clip přes ffmpeg
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", str(max(0, int(start_offset_seconds))),
            "-t", str(max(1, int(sample_seconds))),
            "-i", str(source_path),
            "-vn", "-ac", "1", "-ar", "16000", str(clip_path),
        ],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg clip extraction failed for moonshine: {proc.stderr[-500:]}")

    # Načti WAV jako f32
    with wave.open(str(clip_path), "rb") as wf:
        sample_rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    pcm = array("h")
    pcm.frombytes(raw)
    samples = [max(-1.0, min(1.0, s / 32768.0)) for s in pcm]

    started = datetime.now(UTC)
    started_perf = time.perf_counter()
    process = psutil.Process(os.getpid()) if psutil is not None else None
    start_rss = _safe_rss_mb(process)
    start_cpu = _safe_cpu_seconds(process)

    # Spusť přes live session (chunk-by-chunk simulace streamingu)
    session = create_moonshine_live_session(config=config)
    result = transcribe_moonshine_live_chunk(session=session, sample_rate=sample_rate, samples=samples)
    final = finalize_moonshine_live_session(session=session)

    elapsed_s = max(0.001, time.perf_counter() - started_perf)
    end_rss = _safe_rss_mb(process)
    end_cpu = _safe_cpu_seconds(process)

    transcript_text = final.get("text") or result.get("text") or ""
    transcript_path.write_text(transcript_text + ("\n" if transcript_text else ""), encoding="utf-8")

    import json as _json
    payload = {
        "source_id": source.source_id,
        "clip_path": str(clip_path),
        "started_at_utc": started.isoformat(),
        "elapsed_seconds": round(elapsed_s, 4),
        "text": transcript_text,
        "model_arch": config.model_arch,
        "language": config.language,
    }
    json_path.write_text(_json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    clip_duration = float(sample_seconds)
    rtf = elapsed_s / max(0.1, clip_duration)

    cpu_percent = None
    if start_cpu is not None and end_cpu is not None:
        delta_cpu = max(0.0, end_cpu - start_cpu)
        cpu_count = max(1, os.cpu_count() or 1)
        cpu_percent = min(100.0, (delta_cpu / elapsed_s) * 100.0 / cpu_count)

    ram_mb = end_rss if end_rss is not None else start_rss
    first_word_latency_ms = final.get("first_word_latency_ms") or result.get("first_word_latency_ms")
    latency_ms = first_word_latency_ms if isinstance(first_word_latency_ms, (int, float)) else int(elapsed_s * 1000.0)

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
        "first_word_wall_ms": final.get("first_word_wall_ms"),
        "first_word_audio_ms": final.get("first_word_audio_ms"),
        "rtf": round(float(rtf), 4),
        "cpu_percent": round(cpu_percent, 1) if isinstance(cpu_percent, (int, float)) else None,
        "ram_mb": round(float(ram_mb), 1) if isinstance(ram_mb, (int, float)) else None,
        "speaker_attribution_accuracy": None,
        "speaker_confusion_rate": None,
        "transcript_text": transcript_text,
        "transcript_path": str(transcript_path),
        "json_path": str(json_path),
        "engine": "moonshine",
        "engine_started_at_utc": started.isoformat(),
        "engine_elapsed_seconds": round(elapsed_s, 4),
        "latency_mode": "online_first_text_or_elapsed_ms",
        "model_arch": config.model_arch,
    }


# ---------------------------------------------------------------------------
# Live session API (mic / streaming)
# ---------------------------------------------------------------------------

def create_moonshine_live_session(*, config: MoonshineRunConfig) -> MoonshineLiveSessionState:
    transcriber = _load_moonshine_transcriber(config)
    return MoonshineLiveSessionState(
        transcriber=transcriber,
        started_perf=time.perf_counter(),
    )


def transcribe_moonshine_live_chunk(
    *,
    session: MoonshineLiveSessionState,
    sample_rate: int,
    samples: list[float],
) -> dict[str, Any]:
    """Předá audio chunky do Moonshine transcriberu."""
    started_perf = time.perf_counter()
    previous_text = session.current_text

    try:
        import numpy as np
        audio_np = np.array(samples, dtype=np.float32)
        session.transcriber.add_audio(audio_np, sample_rate)
        session.audio_samples_processed += len(samples)
    except Exception as exc:
        raise RuntimeError(f"moonshine add_audio failed: {exc}") from exc

    # Načti aktuální stav přepisu
    current_text = _extract_moonshine_text(session.transcriber)
    session.current_text = current_text

    if session.first_wall_ms is None and current_text:
        session.first_wall_ms = int((time.perf_counter() - session.started_perf) * 1000.0)
        session.first_audio_ms = int((session.audio_samples_processed / max(1, sample_rate)) * 1000.0)

    latency_ms = None
    if session.first_wall_ms is not None or session.first_audio_ms is not None:
        latency_ms = max(session.first_wall_ms or 0, session.first_audio_ms or 0)

    transcript_delta = _diff_suffix(previous_text, current_text)
    elapsed_s = max(0.0001, time.perf_counter() - started_perf)

    return {
        "text": current_text,
        "text_delta": transcript_delta,
        "latency_ms": round(float(latency_ms), 1) if isinstance(latency_ms, (int, float)) else round(elapsed_s * 1000.0, 1),
        "first_word_latency_ms": round(float(latency_ms), 1) if isinstance(latency_ms, (int, float)) else None,
        "first_word_wall_ms": round(float(session.first_wall_ms), 1) if isinstance(session.first_wall_ms, (int, float)) else None,
        "first_word_audio_ms": round(float(session.first_audio_ms), 1) if isinstance(session.first_audio_ms, (int, float)) else None,
        "engine_elapsed_seconds": round(elapsed_s, 4),
        "audio_samples_processed": session.audio_samples_processed,
    }


def finalize_moonshine_live_session(*, session: MoonshineLiveSessionState) -> dict[str, Any]:
    """Dokončí session a vrátí finální přepis."""
    try:
        if hasattr(session.transcriber, "flush"):
            session.transcriber.flush()
    except Exception:
        pass

    current_text = _extract_moonshine_text(session.transcriber)
    session.current_text = current_text

    if session.first_wall_ms is None and current_text:
        session.first_wall_ms = int((time.perf_counter() - session.started_perf) * 1000.0)
    if session.first_audio_ms is None and current_text:
        session.first_audio_ms = int((session.audio_samples_processed / max(1, 16000)) * 1000.0)

    latency_ms = None
    if session.first_wall_ms is not None or session.first_audio_ms is not None:
        latency_ms = max(session.first_wall_ms or 0, session.first_audio_ms or 0)

    return {
        "text": current_text,
        "latency_ms": round(float(latency_ms), 1) if isinstance(latency_ms, (int, float)) else None,
        "first_word_latency_ms": round(float(latency_ms), 1) if isinstance(latency_ms, (int, float)) else None,
        "first_word_wall_ms": round(float(session.first_wall_ms), 1) if isinstance(session.first_wall_ms, (int, float)) else None,
        "first_word_audio_ms": round(float(session.first_audio_ms), 1) if isinstance(session.first_audio_ms, (int, float)) else None,
        "audio_samples_processed": session.audio_samples_processed,
    }


def resolve_moonshine_model_path(
    model_store_root: str | Path,
    model_arch: str = "medium",
) -> str | None:
    """
    Hledá Moonshine model v model_store.
    Pokud nenalezen, vrátí None — moonshine-voice si model stáhne sám při prvním použití
    přes get_model_for_language().
    """
    root = Path(model_store_root)
    model_id = f"moonshine_{model_arch}_en"
    candidates = [
        root / model_id,
        root / f"moonshine_{model_arch}",
    ]
    for c in candidates:
        if c.exists() and any(c.iterdir()):
            return str(c)
    # Moonshine stáhne model automaticky — vrátíme None a necháme to na knihovně
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_moonshine_transcriber(config: MoonshineRunConfig):
    try:
        from moonshine_voice import Transcriber  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Python package moonshine-voice is not installed. "
            "Install it: pip install moonshine-voice"
        ) from exc

    # Pokud model_path existuje, použij ho; jinak auto-download
    model_path = config.model_path if config.model_path and Path(config.model_path).exists() else None

    if model_path:
        transcriber = Transcriber(model_path=model_path, model_arch=config.model_arch)
    else:
        try:
            ensure_online_allowed(
                component="packages.adapters.moonshine_runner",
                action="_load_moonshine_transcriber",
                reason="moonshine-voice stahuje model při prvním použití pokud není lokálně",
                target="moonshine_voice:get_model_for_language",
                details={"language": config.language, "model_arch": config.model_arch},
            )
            from moonshine_voice import get_model_for_language  # type: ignore[import-not-found]
            auto_path = get_model_for_language(config.language)
            transcriber = Transcriber(model_path=auto_path, model_arch=config.model_arch)
        except Exception as exc:
            raise RuntimeError(
                f"moonshine-voice: nelze načíst model '{config.model_arch}' pro jazyk '{config.language}': {exc}"
            ) from exc

    return transcriber


def _extract_moonshine_text(transcriber) -> str:
    """Extrahuje aktuální přepis z transcriberu."""
    # moonshine-voice API: transcriber.lines nebo transcriber.get_text() nebo transcriber.transcript
    for attr in ("transcript", "lines", "get_text"):
        value = getattr(transcriber, attr, None)
        if callable(value):
            try:
                result = value()
                if isinstance(result, list):
                    return " ".join(str(line) for line in result if line).strip()
                if isinstance(result, str):
                    return result.strip()
            except Exception:
                pass
        elif isinstance(value, list):
            return " ".join(str(line) for line in value if line).strip()
        elif isinstance(value, str):
            return value.strip()
    return ""


def _diff_suffix(previous: str, current: str) -> str:
    before = str(previous or "").strip()
    after = str(current or "").strip()
    if not before:
        return after
    if after.startswith(before):
        return after[len(before):].strip()
    return after


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
