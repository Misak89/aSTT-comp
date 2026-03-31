from __future__ import annotations

import atexit
from array import array
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
from typing import Any
import urllib.error
import urllib.request
import uuid

from packages.ingest.source_resolver import SourceEntry

try:
    import psutil
except ModuleNotFoundError:
    psutil = None  # type: ignore[assignment]


@dataclass(frozen=True)
class WhisperRunConfig:
    whisper_bin: str
    model_path: str
    language: str = "cs"
    threads: int = 4
    beam_size: int | None = None
    best_of: int | None = None
    no_fallback: bool = True
    initial_prompt: str | None = None
    use_server_cache: bool = False


@dataclass
class _WhisperServerRuntime:
    process: subprocess.Popen
    port: int
    proc_handle: Any


@dataclass
class WhisperLiveSessionState:
    runtime: _WhisperServerRuntime
    sample_rate: int
    analysis_interval_ms: int
    analysis_window_seconds: int
    started_perf: float
    audio_samples_processed: int = 0
    first_wall_ms: int | None = None
    first_audio_ms: int | None = None
    current_text: str = ""
    pcm16: array = field(default_factory=lambda: array("h"))
    request_seq: int = 0
    last_analysis_perf: float = 0.0
    temp_dir: Path | None = None


_SERVER_CACHE: dict[tuple[str, str, str, int], _WhisperServerRuntime] = {}
_SERVER_CACHE_LOCK = threading.Lock()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _compute_timeout_seconds(*, sample_seconds: int, model_path: str | Path | None = None) -> int:
    """Spočítá timeout pro whisper-cli/server s možností řízení přes env."""
    factor = max(1.0, _env_float("ASTT_WHISPER_TIMEOUT_FACTOR", 3.0))
    floor_s = max(30, _env_int("ASTT_WHISPER_TIMEOUT_MIN_S", 120))
    # Výchozí strop 14400s (4h) — dost i pro přepis celých nahrávek přes UI (sample_seconds=99999).
    # Lze přepsat env proměnnou ASTT_WHISPER_TIMEOUT_MAX_S (např. 900 pro benchmark s krátkými klipy).
    ceil_s = max(floor_s, _env_int("ASTT_WHISPER_TIMEOUT_MAX_S", 14400))

    timeout_s = int(max(floor_s, max(1, int(sample_seconds)) * factor))

    model_tag = str(model_path or "").lower()
    if "large-v3" in model_tag or "large_v3" in model_tag:
        large_floor = max(floor_s, _env_int("ASTT_WHISPER_TIMEOUT_LARGE_MIN_S", 240))
        timeout_s = max(timeout_s, large_floor)

    return int(min(timeout_s, ceil_s))


def run_whisper_source(
    *,
    source: SourceEntry,
    sample_seconds: int,
    start_offset_seconds: int = 0,
    output_dir: str | Path,
    config: WhisperRunConfig,
) -> dict[str, Any]:
    if source.origin_type != "local_file":
        raise ValueError(f"whisper_cpp real mode supports local_file only: {source.source_id}")

    audio_path = Path(source.value)
    if not audio_path.exists():
        raise FileNotFoundError(f"Source file not found: {audio_path}")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"{source.source_id}_whisper"

    if config.use_server_cache:
        try:
            return _run_whisper_source_server(
                source=source,
                sample_seconds=sample_seconds,
                start_offset_seconds=start_offset_seconds,
                output_dir=out_dir,
                config=config,
            )
        except Exception as exc:
            # Fallback na CLI path, aby tuning neselhal pokud server není dostupný.
            print(f"WARN: whisper-server cache fallback to CLI ({exc})")

    effective_audio_path = audio_path
    effective_offset_seconds = int(max(0, start_offset_seconds))
    if _needs_transcode_for_whisper(audio_path):
        transcoded = out_dir / f"{source.source_id}_input.wav"
        _transcode_for_whisper(
            input_path=audio_path,
            output_path=transcoded,
            start_offset_seconds=effective_offset_seconds,
            sample_seconds=sample_seconds,
        )
        effective_audio_path = transcoded
        effective_offset_seconds = 0

    cmd = _build_cmd(
        whisper_bin=config.whisper_bin,
        model_path=config.model_path,
        audio_path=str(effective_audio_path),
        output_base=str(base),
        sample_seconds=sample_seconds,
        start_offset_seconds=effective_offset_seconds,
        language=config.language,
        threads=config.threads,
        beam_size=config.beam_size,
        best_of=config.best_of,
        no_fallback=config.no_fallback,
        initial_prompt=config.initial_prompt,
    )

    started = datetime.now(UTC)
    started_perf = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    peak_rss_mb = 0.0
    process_cpu_seconds = 0.0
    proc_handle = None
    if psutil is not None:
        try:
            proc_handle = psutil.Process(proc.pid)
        except Exception:
            proc_handle = None

    timeout_s = _compute_timeout_seconds(
        sample_seconds=sample_seconds,
        model_path=config.model_path,
    )

    # Sbírání psutil metrik v separátním vlákně — bez busy-loop v hlavním vlákně
    _stop_monitor = threading.Event()

    def _monitor_proc():
        nonlocal peak_rss_mb, process_cpu_seconds
        cpu_idle_since: float | None = None
        while not _stop_monitor.wait(0.5):
            if proc_handle is None:
                break
            try:
                mem = proc_handle.memory_info().rss / (1024 * 1024)
                peak_rss_mb = max(peak_rss_mb, mem)
                cpu_times = proc_handle.cpu_times()
                process_cpu_seconds = float(cpu_times.user + cpu_times.system)
                cpu_pct = proc_handle.cpu_percent(interval=None)
                # CPU idle detekce: pokud whisper nezabírá CPU > 30s → pravděpodobně zamrzl
                if cpu_pct is not None and cpu_pct < 1.0:
                    if cpu_idle_since is None:
                        cpu_idle_since = time.perf_counter()
                    elif time.perf_counter() - cpu_idle_since > 30.0:
                        proc.kill()
                        break
                else:
                    cpu_idle_since = None
            except Exception:
                break

    threading.Thread(target=_monitor_proc, daemon=True).start()

    try:
        # communicate() drainuje pipe ve vlastních threadech — zabrání deadlocku při plném pipe bufferu
        stdout_text, stderr_text = proc.communicate(timeout=timeout_s)
    except Exception:  # subprocess.TimeoutExpired nebo jiná chyba
        proc.kill()
        stdout_text, stderr_text = proc.communicate()  # dočisti pipes po kill
        raise RuntimeError(
            f"whisper-cli timeout po {timeout_s}s (audio={sample_seconds}s) — proces zabit"
        )
    finally:
        _stop_monitor.set()

    elapsed_s = max(0.001, time.perf_counter() - started_perf)

    if proc.returncode != 0:
        raise RuntimeError(
            f"whisper-cli failed for {source.source_id}: returncode={proc.returncode}, stderr={stderr_text[:500]}"
        )

    json_path = Path(f"{base}.json")
    txt_path = Path(f"{base}.txt")
    if not json_path.exists():
        raise FileNotFoundError(f"whisper-cli JSON output missing: {json_path}")

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    segments = payload.get("transcription", [])
    transcript_text = "\n".join(str(item.get("text", "")).strip() for item in segments if item.get("text"))
    first_offset_ms = _first_segment_offset_ms(segments)

    effective_audio_s = _effective_audio_seconds(audio_path, sample_seconds)
    rtf = elapsed_s / max(0.1, effective_audio_s)

    cpu_percent = None
    if process_cpu_seconds > 0.0:
        cpu_count = max(1, os.cpu_count() or 1)
        cpu_percent = min(100.0, (process_cpu_seconds / elapsed_s) * 100.0 / cpu_count)

    # Offline CLI does not expose true streaming first-token latency. Use elapsed wall time as proxy.
    latency_ms = int(elapsed_s * 1000)

    record = {
        "source_id": source.source_id,
        "source_label": source.label,
        "origin_type": source.origin_type,
        "sample_seconds": sample_seconds,
        "clip_start_seconds": int(max(0, start_offset_seconds)),
        "source_media_path": str(audio_path),
        "effective_input_path": str(effective_audio_path),
        "wer": None,
        "cer": None,
        "latency_ms": round(float(latency_ms), 1),
        "first_word_latency_ms": None,
        "first_word_wall_ms": None,
        "first_word_audio_ms": round(float(first_offset_ms), 1) if isinstance(first_offset_ms, (int, float)) else None,
        "rtf": round(float(rtf), 4),
        "cpu_percent": round(cpu_percent, 1) if isinstance(cpu_percent, (int, float)) else None,
        "ram_mb": round(float(peak_rss_mb), 1) if peak_rss_mb > 0 else None,
        "speaker_attribution_accuracy": None,
        "speaker_confusion_rate": None,
        "transcript_text": transcript_text,
        "transcript_path": str(txt_path) if txt_path.exists() else None,
        "json_path": str(json_path),
        "_segments": segments,
        "engine": "whisper_cpp",
        "engine_started_at_utc": started.isoformat(),
        "engine_elapsed_seconds": round(elapsed_s, 4),
        "latency_mode": "offline_elapsed_proxy_ms",
        "stdout_tail": stdout_text[-500:] if stdout_text else "",
        "stderr_tail": stderr_text[-500:] if stderr_text else "",
    }
    return record


def _run_whisper_source_server(
    *,
    source: SourceEntry,
    sample_seconds: int,
    start_offset_seconds: int,
    output_dir: Path,
    config: WhisperRunConfig,
) -> dict[str, Any]:
    audio_path = Path(source.value)
    base = output_dir / f"{source.source_id}_whisper"
    effective_offset_seconds = int(max(0, start_offset_seconds))
    effective_audio_path = audio_path
    cleanup_paths: list[Path] = []

    # whisper-server endpoint nebere offset/duration parametry stejně jako CLI,
    # proto si vynutíme přesný vstupní klip při offsetu nebo potenciálně delším vstupu.
    needs_clip = effective_offset_seconds > 0 or audio_path.suffix.lower() != ".wav"
    if audio_path.suffix.lower() == ".wav":
        duration = _audio_duration_seconds(audio_path)
        if duration is not None and duration > float(sample_seconds) + 0.25:
            needs_clip = True

    if needs_clip:
        clipped = output_dir / f"{source.source_id}_server_input.wav"
        _transcode_for_whisper(
            input_path=audio_path,
            output_path=clipped,
            start_offset_seconds=effective_offset_seconds,
            sample_seconds=sample_seconds,
        )
        effective_audio_path = clipped
        cleanup_paths.append(clipped)
        effective_offset_seconds = 0

    runtime = _get_or_start_cached_server(config=config)

    started = datetime.now(UTC)
    started_perf = time.perf_counter()
    cpu_before = rss_before = None
    if runtime.proc_handle is not None:
        try:
            c = runtime.proc_handle.cpu_times()
            cpu_before = float(c.user + c.system)
            rss_before = runtime.proc_handle.memory_info().rss / (1024 * 1024)
        except Exception:
            cpu_before = rss_before = None

    payload = _post_server_inference(
        port=runtime.port,
        audio_path=effective_audio_path,
        timeout_s=_compute_timeout_seconds(
            sample_seconds=sample_seconds,
            model_path=config.model_path,
        ),
    )
    elapsed_s = max(0.001, time.perf_counter() - started_perf)

    cpu_percent = None
    peak_rss_mb = rss_before if isinstance(rss_before, (int, float)) else 0.0
    if runtime.proc_handle is not None:
        try:
            c = runtime.proc_handle.cpu_times()
            cpu_after = float(c.user + c.system)
            rss_after = runtime.proc_handle.memory_info().rss / (1024 * 1024)
            peak_rss_mb = max(float(peak_rss_mb), float(rss_after))
            if cpu_before is not None:
                cpu_count = max(1, os.cpu_count() or 1)
                cpu_percent = min(100.0, ((cpu_after - cpu_before) / elapsed_s) * 100.0 / cpu_count)
        except Exception:
            pass

    segments = payload.get("transcription", []) if isinstance(payload, dict) else []
    transcript_text = ""
    if isinstance(payload, dict):
        text_val = payload.get("text")
        if isinstance(text_val, str):
            transcript_text = text_val.strip()
    if not transcript_text and isinstance(segments, list):
        transcript_text = "\n".join(str(item.get("text", "")).strip() for item in segments if isinstance(item, dict) and item.get("text"))

    json_path = Path(f"{base}.json")
    txt_path = Path(f"{base}.txt")
    json_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    txt_path.write_text(transcript_text + ("\n" if transcript_text else ""), encoding="utf-8")

    first_offset_ms = _first_segment_offset_ms(segments)
    effective_audio_s = _effective_audio_seconds(effective_audio_path, sample_seconds)
    rtf = elapsed_s / max(0.1, effective_audio_s)
    latency_ms = int(elapsed_s * 1000)

    record = {
        "source_id": source.source_id,
        "source_label": source.label,
        "origin_type": source.origin_type,
        "sample_seconds": sample_seconds,
        "clip_start_seconds": int(max(0, start_offset_seconds)),
        "source_media_path": str(audio_path),
        "effective_input_path": str(effective_audio_path),
        "wer": None,
        "cer": None,
        "latency_ms": round(float(latency_ms), 1),
        "first_word_latency_ms": None,
        "first_word_wall_ms": None,
        "first_word_audio_ms": round(float(first_offset_ms), 1) if isinstance(first_offset_ms, (int, float)) else None,
        "rtf": round(float(rtf), 4),
        "cpu_percent": round(cpu_percent, 1) if isinstance(cpu_percent, (int, float)) else None,
        "ram_mb": round(float(peak_rss_mb), 1) if peak_rss_mb > 0 else None,
        "speaker_attribution_accuracy": None,
        "speaker_confusion_rate": None,
        "transcript_text": transcript_text,
        "transcript_path": str(txt_path),
        "json_path": str(json_path),
        "_segments": segments,
        "engine": "whisper_cpp",
        "engine_started_at_utc": started.isoformat(),
        "engine_elapsed_seconds": round(elapsed_s, 4),
        "latency_mode": "offline_elapsed_proxy_ms",
        "stdout_tail": "",
        "stderr_tail": "",
        "model_cached": True,
    }

    for p in cleanup_paths:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
    return record


def create_whisper_live_session(
    *,
    config: WhisperRunConfig,
    sample_rate: int = 16000,
    analysis_interval_ms: int = 1200,
    analysis_window_seconds: int = 12,
) -> WhisperLiveSessionState:
    runtime = _get_or_start_cached_server(config=config)
    temp_dir = Path(".runtime") / "runs" / "mic_whisper_live" / f"session_{uuid.uuid4().hex[:8]}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return WhisperLiveSessionState(
        runtime=runtime,
        sample_rate=max(8000, int(sample_rate)),
        analysis_interval_ms=max(300, int(analysis_interval_ms)),
        analysis_window_seconds=max(3, int(analysis_window_seconds)),
        started_perf=time.perf_counter(),
        temp_dir=temp_dir,
    )


def transcribe_whisper_live_chunk(
    *,
    session: WhisperLiveSessionState,
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

    should_analyze = _should_analyze_live_buffer(session=session)
    if should_analyze:
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


def finalize_whisper_live_session(*, session: WhisperLiveSessionState) -> dict[str, Any]:
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

    if session.temp_dir is not None:
        try:
            shutil.rmtree(session.temp_dir, ignore_errors=True)
        except Exception:
            pass

    return {
        "text": session.current_text,
        "text_delta": _diff_transcript_suffix(previous_text, session.current_text),
        "latency_ms": round(float(latency_ms), 1) if isinstance(latency_ms, (int, float)) else None,
        "first_word_latency_ms": round(float(latency_ms), 1) if isinstance(latency_ms, (int, float)) else None,
        "first_word_wall_ms": round(float(session.first_wall_ms), 1) if isinstance(session.first_wall_ms, (int, float)) else None,
        "first_word_audio_ms": round(float(session.first_audio_ms), 1) if isinstance(session.first_audio_ms, (int, float)) else None,
        "audio_samples_processed": session.audio_samples_processed,
    }


def _should_analyze_live_buffer(*, session: WhisperLiveSessionState) -> bool:
    if not session.pcm16:
        return False
    now = time.perf_counter()
    if session.last_analysis_perf <= 0:
        min_samples = int(max(0.30, session.analysis_interval_ms / 1000.0) * session.sample_rate)
        return len(session.pcm16) >= max(1, min_samples)
    return (now - session.last_analysis_perf) * 1000.0 >= float(session.analysis_interval_ms)


def _infer_live_text(*, session: WhisperLiveSessionState, tail_only: bool) -> str:
    if not session.pcm16:
        return ""

    window_samples = len(session.pcm16)
    if tail_only:
        max_window = max(1, int(session.analysis_window_seconds * session.sample_rate))
        window_samples = min(window_samples, max_window)
    start_idx = max(0, len(session.pcm16) - window_samples)
    pcm_window = session.pcm16[start_idx:]

    session.request_seq += 1
    if session.temp_dir is None:
        session.temp_dir = Path(".runtime") / "runs" / "mic_whisper_live" / f"session_{uuid.uuid4().hex[:8]}"
        session.temp_dir.mkdir(parents=True, exist_ok=True)
    wav_path = session.temp_dir / f"chunk_{session.request_seq:06d}.wav"
    _write_pcm16_wave(path=wav_path, sample_rate=session.sample_rate, pcm=pcm_window)

    timeout_s = _compute_timeout_seconds(
        sample_seconds=max(1, int(round(window_samples / max(1, session.sample_rate)))),
        model_path=None,
    )
    payload = _post_server_inference(
        port=session.runtime.port,
        audio_path=wav_path,
        timeout_s=timeout_s,
    )
    text = _extract_payload_text(payload)
    return " ".join(text.split())


def _extract_payload_text(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    direct = payload.get("text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    segments = payload.get("transcription")
    if isinstance(segments, list):
        return "\n".join(
            str(item.get("text", "")).strip()
            for item in segments
            if isinstance(item, dict) and item.get("text")
        ).strip()
    return ""


def _f32_to_pcm16_array(samples: list[float]) -> array:
    pcm = array("h")
    for value in samples:
        clipped = max(-1.0, min(1.0, float(value)))
        pcm.append(int(clipped * 32767.0))
    return pcm


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
        left = int(source_index)
        right = min(source_max_index, left + 1)
        frac = source_index - left
        sample = source[left] * (1.0 - frac) + source[right] * frac
        output.append(float(sample))
    return output


def _write_pcm16_wave(*, path: Path, sample_rate: int, pcm: array) -> None:
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(max(8000, int(sample_rate)))
        handle.writeframes(pcm.tobytes())


def resolve_whisper_server(
    model_store_root: str | Path = ".runtime/model_store",
    whisper_bin: str | None = None,
) -> str | None:
    env_server = os.environ.get("WHISPER_CPP_SERVER_BIN")
    if env_server:
        p = Path(env_server)
        if p.exists():
            return str(p)

    if whisper_bin:
        wb = Path(whisper_bin)
        sibling_candidates = [
            wb.with_name("whisper-server.exe"),
            wb.with_name("whisper-server"),
            wb.with_name("server.exe"),
            wb.with_name("server"),
        ]
        for c in sibling_candidates:
            if c.exists():
                return str(c)

    root = Path(model_store_root)
    candidates = [
        root / "whisper_cpp_runtime" / "whisper-bin-x64" / "Release" / "whisper-server.exe",
        root / "whisper_cpp_runtime" / "whisper-bin-x64" / "Release" / "whisper-server",
        root / "whisper_cpp_runtime" / "whisper-bin-x64" / "Release" / "server.exe",
        root / "whisper_cpp_runtime" / "whisper-bin-x64" / "Release" / "server",
        root / "whisper_cpp" / "whisper-server.exe",
        root / "whisper_cpp" / "whisper-server",
        root / "whisper_cpp" / "server.exe",
        root / "whisper_cpp" / "server",
    ]
    for c in candidates:
        if c.exists():
            return str(c)

    for name in ["whisper-server.exe", "whisper-server", "server.exe", "server"]:
        found = shutil.which(name)
        if found:
            return found
    return None


def _post_server_inference(*, port: int, audio_path: Path, timeout_s: int) -> dict[str, Any]:
    boundary = f"----astt-{uuid.uuid4().hex}"
    crlf = b"\r\n"
    body_parts: list[bytes] = []

    def _add_field(name: str, value: str) -> None:
        body_parts.append(f"--{boundary}".encode("utf-8"))
        body_parts.append(f'Content-Disposition: form-data; name="{name}"'.encode("utf-8"))
        body_parts.append(b"")
        body_parts.append(str(value).encode("utf-8"))

    _add_field("temperature", "0.0")
    _add_field("response_format", "json")

    file_name = audio_path.name
    file_bytes = audio_path.read_bytes()
    body_parts.append(f"--{boundary}".encode("utf-8"))
    body_parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{file_name}"'.encode("utf-8")
    )
    body_parts.append(b"Content-Type: audio/wav")
    body_parts.append(b"")
    body_parts.append(file_bytes)
    body_parts.append(f"--{boundary}--".encode("utf-8"))
    body_parts.append(b"")
    body = crlf.join(body_parts)

    req = urllib.request.Request(
        url=f"http://127.0.0.1:{port}/inference",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
        raise RuntimeError(f"whisper-server inference HTTP {exc.code}: {detail[:300]}") from exc
    except Exception as exc:
        raise RuntimeError(f"whisper-server inference failed: {exc}") from exc

    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"text": raw}


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_server_ready(port: int, timeout_s: int = 30) -> None:
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1.5) as resp:
                if getattr(resp, "status", 200) == 200:
                    return
        except Exception:
            time.sleep(0.4)
    raise RuntimeError(f"whisper-server startup timeout on port {port}")


def _get_or_start_cached_server(*, config: WhisperRunConfig) -> _WhisperServerRuntime:
    model_store_root = Path(config.model_path).parents[1] if len(Path(config.model_path).parents) >= 2 else Path(".runtime/model_store")
    server_bin = resolve_whisper_server(model_store_root=model_store_root, whisper_bin=config.whisper_bin)
    if not server_bin:
        raise RuntimeError("whisper-server binary nenalezen")

    key = (str(server_bin), str(config.model_path), str(config.language), int(max(1, config.threads)))
    with _SERVER_CACHE_LOCK:
        cached = _SERVER_CACHE.get(key)
        if cached and cached.process.poll() is None:
            return cached

        port = _find_free_port()
        proc = subprocess.Popen(
            [
                server_bin,
                "-m",
                config.model_path,
                "-l",
                str(config.language),
                "-t",
                str(max(1, config.threads)),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            _wait_server_ready(port, timeout_s=30)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            raise

        proc_handle = None
        if psutil is not None:
            try:
                proc_handle = psutil.Process(proc.pid)
            except Exception:
                proc_handle = None
        runtime = _WhisperServerRuntime(process=proc, port=port, proc_handle=proc_handle)
        _SERVER_CACHE[key] = runtime
        return runtime


def _shutdown_cached_servers() -> None:
    with _SERVER_CACHE_LOCK:
        items = list(_SERVER_CACHE.values())
        _SERVER_CACHE.clear()
    for runtime in items:
        try:
            runtime.process.kill()
        except Exception:
            pass


atexit.register(_shutdown_cached_servers)


def resolve_whisper_cli(model_store_root: str | Path = ".runtime/model_store") -> str | None:
    root = Path(model_store_root)
    env_bin = os.environ.get("WHISPER_CPP_BIN")
    if env_bin is not None:
        p = Path(env_bin)
        if p.exists():
            return str(p)
        return None

    candidates = [
        root / "whisper_cpp_runtime" / "whisper-bin-x64" / "Release" / "whisper-cli.exe",
        root / "whisper_cpp_runtime" / "whisper-bin-x64" / "Release" / "whisper-cli",
        root / "whisper_cpp" / "whisper-cli.exe",
        root / "whisper_cpp" / "whisper-cli",
        root / "whisper_cpp" / "main.exe",
        root / "whisper_cpp" / "main",
        root / "whisper_cpp" / "bin" / "whisper-cli.exe",
        root / "whisper_cpp" / "bin" / "whisper-cli",
        root / "whisper_cpp" / "bin" / "main.exe",
        root / "whisper_cpp" / "bin" / "main",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    for name in ["whisper-cli.exe", "whisper-cli"]:
        found = shutil.which(name)
        if found:
            return found
    return None


def resolve_whisper_model_file(model_store_root: str | Path, model_id: str) -> Path | None:
    root = Path(model_store_root)
    candidates_by_model: dict[str, list[Path]] = {
        "whisper_cpp_base": [
            root / "whisper_cpp_base" / "ggml-base.bin",
            root / "whisper_cpp_base" / "ggml-base.en.bin",
        ],
        "whisper_cpp_small": [
            root / "whisper_cpp_small" / "ggml-small.bin",
            root / "whisper_cpp_small" / "ggml-small.en.bin",
        ],
        "whisper_cpp_large_v3": [
            root / "whisper_cpp_large_v3" / "ggml-large-v3.bin",
            root / "whisper_cpp_large_v3" / "ggml-large-v3-q5_0.bin",
            root / "whisper_cpp_large_v3" / "ggml-large-v3-q8_0.bin",
            root / "whisper_large_v3" / "ggml-large-v3.bin",
            root / "whisper_large_v3" / "ggml-large-v3-q5_0.bin",
            root / "whisper_large_v3" / "ggml-large-v3-q8_0.bin",
        ],
        "whisper_cpp_large_v3_turbo": [
            root / "whisper_cpp_large_v3_turbo" / "ggml-large-v3-turbo-q5_0.bin",
            root / "whisper_cpp_large_v3_turbo" / "ggml-large-v3-turbo.bin",
        ],
    }
    for candidate in candidates_by_model.get(str(model_id or "").strip(), []):
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _diff_transcript_suffix(previous_text: str, current_text: str) -> str:
    before = str(previous_text or "").strip()
    after = str(current_text or "").strip()
    if not before:
        return after
    if after.startswith(before):
        return after[len(before) :].strip()
    return after


def _build_cmd(
    *,
    whisper_bin: str,
    model_path: str,
    audio_path: str,
    output_base: str,
    sample_seconds: int,
    start_offset_seconds: int,
    language: str,
    threads: int,
    beam_size: int | None,
    best_of: int | None,
    no_fallback: bool,
    initial_prompt: str | None = None,
) -> list[str]:
    cmd = [
        whisper_bin,
        "-m",
        model_path,
        "-f",
        audio_path,
        "-l",
        language,
        "-ot",
        str(max(0, int(start_offset_seconds)) * 1000),
        "-d",
        str(sample_seconds * 1000),
        "-t",
        str(max(1, threads)),
        "-oj",
        "-otxt",
        "-of",
        output_base,
        "-np",
    ]
    if beam_size is not None:
        cmd.extend(["-bs", str(max(1, beam_size))])
    if best_of is not None:
        cmd.extend(["-bo", str(max(1, best_of))])
    if no_fallback:
        cmd.append("-nf")
    if initial_prompt:
        cmd.extend(["--prompt", initial_prompt])
    return cmd


def _first_segment_offset_ms(segments: Any) -> int | None:
    if not isinstance(segments, list):
        return None
    for item in segments:
        if not isinstance(item, dict):
            continue
        offsets = item.get("offsets")
        if isinstance(offsets, dict):
            from_ms = offsets.get("from")
            if isinstance(from_ms, int):
                return from_ms
    return None


def _effective_audio_seconds(audio_path: Path, sample_seconds: int) -> float:
    # Keep this lightweight: WAV gets exact duration, other formats fall back to requested sample length.
    if audio_path.suffix.lower() == ".wav":
        try:
            import wave

            with wave.open(str(audio_path), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                if frames > 0 and rate > 0:
                    duration = frames / float(rate)
                    return max(0.1, min(duration, float(sample_seconds)))
        except Exception:
            pass
    return max(0.1, float(sample_seconds))


def _audio_duration_seconds(audio_path: Path) -> float | None:
    if audio_path.suffix.lower() != ".wav":
        return None
    try:
        import wave
        with wave.open(str(audio_path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if frames > 0 and rate > 0:
                return frames / float(rate)
    except Exception:
        return None
    return None


def _needs_transcode_for_whisper(path: Path) -> bool:
    return path.suffix.lower() not in {".wav", ".mp3", ".ogg", ".flac"}


def _transcode_for_whisper(
    *,
    input_path: Path,
    output_path: Path,
    start_offset_seconds: int,
    sample_seconds: int,
) -> None:
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
        raise RuntimeError(f"ffmpeg transcode failed for whisper input: {proc.stderr[-500:]}")
