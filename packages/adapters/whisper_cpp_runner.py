from __future__ import annotations

import atexit
from dataclasses import dataclass
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
    ceil_s = max(floor_s, _env_int("ASTT_WHISPER_TIMEOUT_MAX_S", 900))

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
