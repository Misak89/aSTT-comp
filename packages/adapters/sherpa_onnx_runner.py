from __future__ import annotations

from array import array
from dataclasses import dataclass
from datetime import UTC, datetime
import inspect
import json
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
class SherpaModelBundle:
    model_dir: str
    tokens: str
    encoder: str
    decoder: str
    joiner: str


@dataclass(frozen=True)
class SherpaRunConfig:
    tokens: str
    encoder: str
    decoder: str
    joiner: str
    provider: str = "cpu"
    num_threads: int = 2
    sample_rate: int = 16000
    feature_dim: int = 80
    decoding_method: str = "greedy_search"


@dataclass
class SherpaLiveSessionState:
    recognizer: Any
    stream: Any
    sample_rate: int
    started_perf: float
    audio_samples_processed: int = 0
    first_wall_ms: int | None = None
    first_audio_ms: int | None = None
    current_text: str = ""


def resolve_sherpa_model_bundle(
    model_store_root: str | Path,
    *,
    preferred_language: str | None = None,
) -> SherpaModelBundle | None:
    bundles = list_sherpa_model_bundles(model_store_root)
    if not bundles:
        return None

    language = normalize_language_code(preferred_language)
    if language:
        for bundle in bundles:
            bundle_language = detect_sherpa_bundle_language(bundle.model_dir)
            if _language_matches(bundle_language, language):
                return bundle
        return None

    # CZ has highest priority for live use when available.
    for bundle in bundles:
        if _language_matches(detect_sherpa_bundle_language(bundle.model_dir), "cs"):
            return bundle
    return bundles[0]


def list_sherpa_model_bundles(model_store_root: str | Path) -> list[SherpaModelBundle]:
    root = Path(model_store_root)
    candidate_roots = [
        root / "sherpa_onnx_small",
        root / "sherpa_onnx_streaming_en_zipformer",
        root,
    ]
    bundles: list[SherpaModelBundle] = []
    seen: set[str] = set()
    for candidate_root in candidate_roots:
        if not candidate_root.exists():
            continue
        for tokens_path in sorted(candidate_root.rglob("tokens*.txt")):
            bundle = _bundle_from_tokens(tokens_path)
            if bundle is None:
                continue
            key = str(Path(bundle.model_dir).resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            bundles.append(bundle)
    bundles.sort(
        key=lambda bundle: (
            0 if _language_matches(detect_sherpa_bundle_language(bundle.model_dir), "cs") else 1,
            str(bundle.model_dir).lower(),
        )
    )
    return bundles


def detect_sherpa_bundle_language(model_dir: str | Path) -> str | None:
    value = str(model_dir or "").replace("\\", "/").lower()
    if "parakeet-tdt-0.6b-v3" in value or "parakeet-tdt-0.6b-v2" in value:
        # NeMo Parakeet v2/v3 bundles are multilingual (include Czech among supported EU langs).
        return "cs"
    if any(token in value for token in ("-cs-", "_cs_", "/cs/", "czech", "cesky")):
        return "cs"
    if any(token in value for token in ("-en-", "_en_", "/en/", "english")):
        return "en"
    return None


def normalize_language_code(value: str | None) -> str | None:
    raw = str(value or "").strip().lower().replace("_", "-")
    if not raw:
        return None
    if raw in {"auto", "default", "any"}:
        return None
    return raw


def _language_matches(bundle_language: str | None, requested_language: str) -> bool:
    normalized_bundle = normalize_language_code(bundle_language)
    normalized_requested = normalize_language_code(requested_language)
    if not normalized_bundle or not normalized_requested:
        return False
    if normalized_bundle == normalized_requested:
        return True
    requested_primary = normalized_requested.split("-", 1)[0]
    bundle_primary = normalized_bundle.split("-", 1)[0]
    return requested_primary == bundle_primary


def run_sherpa_source(
    *,
    source: SourceEntry,
    sample_seconds: int,
    start_offset_seconds: int = 0,
    output_dir: str | Path,
    config: SherpaRunConfig,
) -> dict[str, Any]:
    if source.origin_type != "local_file":
        raise ValueError(f"sherpa_onnx real mode supports local_file only: {source.source_id}")

    source_path = Path(source.value)
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    clip_path = out_dir / f"{source.source_id}_clip.wav"
    transcript_path = out_dir / f"{source.source_id}_sherpa.txt"
    json_path = out_dir / f"{source.source_id}_sherpa.json"

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

    effective_sample_rate, samples = _read_wave_f32(clip_path)
    transcript_text, first_text_ms, first_word_wall_ms, first_word_audio_ms = _transcribe_sherpa(
        config=config,
        sample_rate=effective_sample_rate,
        samples=samples,
    )

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
        "sample_rate": effective_sample_rate,
        "provider": config.provider,
        "num_threads": config.num_threads,
        "decoding_method": config.decoding_method,
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
    latency_ms = first_text_ms if first_text_ms is not None else int(elapsed_s * 1000.0)

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
        "first_word_latency_ms": round(float(first_text_ms), 1) if isinstance(first_text_ms, (int, float)) else None,
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
        "engine": "sherpa_onnx",
        "engine_started_at_utc": started.isoformat(),
        "engine_elapsed_seconds": round(elapsed_s, 4),
        "latency_mode": "online_first_text_or_elapsed_ms",
    }


def create_sherpa_live_session(*, config: SherpaRunConfig) -> SherpaLiveSessionState:
    recognizer = _load_sherpa_online_recognizer(config)
    stream = recognizer.create_stream()
    return SherpaLiveSessionState(
        recognizer=recognizer,
        stream=stream,
        sample_rate=int(config.sample_rate),
        started_perf=time.perf_counter(),
    )


def transcribe_sherpa_live_chunk(
    *,
    session: SherpaLiveSessionState,
    sample_rate: int,
    samples: list[float],
) -> dict[str, Any]:
    started_perf = time.perf_counter()
    chunk_samples = max(1, int(sample_rate * 0.08))
    sent_samples = 0
    total_samples = len(samples)
    decode_loops = 0
    previous_text = session.current_text

    while sent_samples < total_samples:
        chunk = samples[sent_samples : sent_samples + chunk_samples]
        sent_samples += len(chunk)
        _accept_waveform(stream=session.stream, sample_rate=sample_rate, samples=chunk)
        session.audio_samples_processed += len(chunk)
        while session.recognizer.is_ready(session.stream):
            _decode_stream(recognizer=session.recognizer, stream=session.stream)
            decode_loops += 1
            partial = _extract_online_result(recognizer=session.recognizer, stream=session.stream)
            if session.first_wall_ms is None and partial:
                session.first_wall_ms = int((time.perf_counter() - session.started_perf) * 1000.0)
                session.first_audio_ms = int((session.audio_samples_processed / max(1, sample_rate)) * 1000.0)
            if decode_loops > 20000:
                raise RuntimeError("sherpa_onnx live decode did not converge (loop guard hit).")

    current_text = _extract_online_result(recognizer=session.recognizer, stream=session.stream)
    session.current_text = current_text
    if session.first_wall_ms is None and current_text:
        session.first_wall_ms = int((time.perf_counter() - session.started_perf) * 1000.0)
    if session.first_audio_ms is None and current_text:
        session.first_audio_ms = int((session.audio_samples_processed / max(1, sample_rate)) * 1000.0)

    latency_ms = None
    if session.first_wall_ms is not None or session.first_audio_ms is not None:
        latency_ms = max(session.first_wall_ms or 0, session.first_audio_ms or 0)

    transcript_delta = _diff_transcript_suffix(previous_text, current_text)
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


def finalize_sherpa_live_session(*, session: SherpaLiveSessionState) -> dict[str, Any]:
    if hasattr(session.stream, "input_finished"):
        session.stream.input_finished()
    decode_loops = 0
    while session.recognizer.is_ready(session.stream):
        _decode_stream(recognizer=session.recognizer, stream=session.stream)
        decode_loops += 1
        if decode_loops > 40000:
            raise RuntimeError("sherpa_onnx live finalize did not converge (loop guard hit).")

    current_text = _extract_online_result(recognizer=session.recognizer, stream=session.stream)
    previous_text = session.current_text
    session.current_text = current_text
    if session.first_wall_ms is None and current_text:
        session.first_wall_ms = int((time.perf_counter() - session.started_perf) * 1000.0)
    if session.first_audio_ms is None and current_text:
        session.first_audio_ms = int((session.audio_samples_processed / max(1, session.sample_rate)) * 1000.0)

    latency_ms = None
    if session.first_wall_ms is not None or session.first_audio_ms is not None:
        latency_ms = max(session.first_wall_ms or 0, session.first_audio_ms or 0)

    return {
        "text": current_text,
        "text_delta": _diff_transcript_suffix(previous_text, current_text),
        "latency_ms": round(float(latency_ms), 1) if isinstance(latency_ms, (int, float)) else None,
        "first_word_latency_ms": round(float(latency_ms), 1) if isinstance(latency_ms, (int, float)) else None,
        "first_word_wall_ms": round(float(session.first_wall_ms), 1) if isinstance(session.first_wall_ms, (int, float)) else None,
        "first_word_audio_ms": round(float(session.first_audio_ms), 1) if isinstance(session.first_audio_ms, (int, float)) else None,
        "audio_samples_processed": session.audio_samples_processed,
    }


def read_wave_f32(path: Path) -> tuple[int, list[float]]:
    return _read_wave_f32(path)


def _bundle_from_tokens(tokens_path: Path) -> SherpaModelBundle | None:
    model_dir = tokens_path.parent
    encoder = _pick_onnx(model_dir, includes="encoder")
    decoder = _pick_onnx(model_dir, includes="decoder")
    joiner = _pick_onnx(model_dir, includes="joiner")
    if not encoder or not decoder or not joiner:
        return None
    return SherpaModelBundle(
        model_dir=str(model_dir),
        tokens=str(tokens_path),
        encoder=str(encoder),
        decoder=str(decoder),
        joiner=str(joiner),
    )


def _pick_onnx(directory: Path, *, includes: str) -> Path | None:
    includes_lower = includes.lower()
    candidates = [
        path
        for path in directory.glob("*.onnx")
        if path.is_file() and includes_lower in path.name.lower()
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (len(item.name), item.name))
    return candidates[0]


def _transcribe_sherpa(
    *,
    config: SherpaRunConfig,
    sample_rate: int,
    samples: list[float],
) -> tuple[str, int | None, int | None, int | None]:
    errors: list[str] = []

    try:
        recognizer = _load_sherpa_online_recognizer(config)
        text, first_text_ms, first_word_wall_ms, first_word_audio_ms = _decode_online_stream(
            recognizer=recognizer,
            sample_rate=sample_rate,
            samples=samples,
        )
        return text, first_text_ms, first_word_wall_ms, first_word_audio_ms
    except Exception as exc:
        errors.append(f"online decode failed: {type(exc).__name__}: {exc}")

    try:
        recognizer = _load_sherpa_offline_recognizer(config)
        stream = recognizer.create_stream()
        _accept_waveform(stream=stream, sample_rate=sample_rate, samples=samples)
        _decode_stream(recognizer=recognizer, stream=stream)
        return _extract_stream_text(stream), None, None, None
    except Exception as exc:
        errors.append(f"offline decode failed: {type(exc).__name__}: {exc}")

    raise RuntimeError("Sherpa transcription failed. " + " | ".join(errors))


def _load_sherpa_online_recognizer(config: SherpaRunConfig):
    try:
        import sherpa_onnx  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Python package sherpa_onnx is not installed. Install it before real mode with sherpa_onnx_small."
        ) from exc

    recognizer_cls = getattr(sherpa_onnx, "OnlineRecognizer", None)
    if recognizer_cls is None:
        raise RuntimeError("sherpa_onnx OnlineRecognizer is unavailable in installed package.")

    from_transducer = getattr(recognizer_cls, "from_transducer", None)
    if not callable(from_transducer):
        raise RuntimeError("sherpa_onnx OnlineRecognizer.from_transducer is unavailable.")

    kwargs = {
        "tokens": config.tokens,
        "encoder": config.encoder,
        "decoder": config.decoder,
        "joiner": config.joiner,
        "provider": config.provider,
        "num_threads": int(max(1, config.num_threads)),
        "sample_rate": int(config.sample_rate),
        "feature_dim": int(config.feature_dim),
        "decoding_method": config.decoding_method,
    }
    return _call_with_supported_kwargs(from_transducer, kwargs)


def _load_sherpa_offline_recognizer(config: SherpaRunConfig):
    try:
        import sherpa_onnx  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Python package sherpa_onnx is not installed. Install it before real mode with sherpa_onnx_small."
        ) from exc

    recognizer_cls = getattr(sherpa_onnx, "OfflineRecognizer", None)
    if recognizer_cls is None:
        raise RuntimeError("sherpa_onnx OfflineRecognizer is unavailable in installed package.")

    from_transducer = getattr(recognizer_cls, "from_transducer", None)
    if callable(from_transducer):
        kwargs = {
            "tokens": config.tokens,
            "encoder": config.encoder,
            "decoder": config.decoder,
            "joiner": config.joiner,
            "provider": config.provider,
            "num_threads": int(max(1, config.num_threads)),
            "sample_rate": int(config.sample_rate),
            "feature_dim": int(config.feature_dim),
            "decoding_method": config.decoding_method,
        }
        return _call_with_supported_kwargs(from_transducer, kwargs)

    feature_config_cls = getattr(sherpa_onnx, "FeatureConfig", None)
    offline_model_config_cls = getattr(sherpa_onnx, "OfflineModelConfig", None)
    transducer_config_cls = getattr(sherpa_onnx, "OfflineTransducerModelConfig", None)
    recognizer_config_cls = getattr(sherpa_onnx, "OfflineRecognizerConfig", None)
    if not all([feature_config_cls, offline_model_config_cls, transducer_config_cls, recognizer_config_cls]):
        raise RuntimeError("Unsupported sherpa_onnx API: cannot build OfflineRecognizer config.")

    feature_config = feature_config_cls(
        sample_rate=int(config.sample_rate),
        feature_dim=int(config.feature_dim),
    )
    transducer_config = transducer_config_cls(
        encoder=config.encoder,
        decoder=config.decoder,
        joiner=config.joiner,
    )
    model_config = offline_model_config_cls(
        transducer=transducer_config,
        tokens=config.tokens,
        num_threads=int(max(1, config.num_threads)),
        provider=config.provider,
    )
    recognizer_config = recognizer_config_cls(
        feat_config=feature_config,
        model_config=model_config,
        decoding_method=config.decoding_method,
    )
    return recognizer_cls(recognizer_config)


def _decode_online_stream(*, recognizer, sample_rate: int, samples: list[float]) -> tuple[str, int | None, int | None, int | None]:
    stream = recognizer.create_stream()
    decode_started = time.perf_counter()
    first_wall_ms: int | None = None
    first_audio_ms: int | None = None

    if hasattr(recognizer, "is_ready"):
        loops = 0
        chunk_samples = max(1, int(sample_rate * 0.08))
        sent_samples = 0
        total_samples = len(samples)
        while sent_samples < total_samples:
            chunk = samples[sent_samples : sent_samples + chunk_samples]
            sent_samples += len(chunk)
            _accept_waveform(stream=stream, sample_rate=sample_rate, samples=chunk)
            while recognizer.is_ready(stream):
                _decode_stream(recognizer=recognizer, stream=stream)
                loops += 1
                if first_wall_ms is None:
                    partial = _extract_online_result(recognizer=recognizer, stream=stream)
                    if partial:
                        first_wall_ms = int((time.perf_counter() - decode_started) * 1000.0)
                        first_audio_ms = int((sent_samples / max(1, sample_rate)) * 1000.0)
                if loops > 20000:
                    raise RuntimeError("sherpa_onnx online decode did not converge (loop guard hit).")
        if hasattr(stream, "input_finished"):
            stream.input_finished()
        while recognizer.is_ready(stream):
            _decode_stream(recognizer=recognizer, stream=stream)
            loops += 1
            if first_wall_ms is None:
                partial = _extract_online_result(recognizer=recognizer, stream=stream)
                if partial:
                    first_wall_ms = int((time.perf_counter() - decode_started) * 1000.0)
                    first_audio_ms = int((total_samples / max(1, sample_rate)) * 1000.0)
            if loops > 40000:
                raise RuntimeError("sherpa_onnx online decode did not converge (finalize loop guard hit).")
    else:
        _accept_waveform(stream=stream, sample_rate=sample_rate, samples=samples)
        if hasattr(stream, "input_finished"):
            stream.input_finished()
        _decode_stream(recognizer=recognizer, stream=stream)

    text = _extract_online_result(recognizer=recognizer, stream=stream)
    if first_wall_ms is None and text:
        first_wall_ms = int((time.perf_counter() - decode_started) * 1000.0)
    if first_audio_ms is None and text:
        first_audio_ms = int((len(samples) / max(1, sample_rate)) * 1000.0)

    first_text_ms: int | None = None
    if first_wall_ms is not None or first_audio_ms is not None:
        first_text_ms = max(first_wall_ms or 0, first_audio_ms or 0)
    return text, first_text_ms, first_wall_ms, first_audio_ms


def _call_with_supported_kwargs(func, kwargs: dict[str, Any]):
    try:
        signature = inspect.signature(func)
        accepted = {name for name, param in signature.parameters.items() if param.kind in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY)}
        filtered = {key: value for key, value in kwargs.items() if key in accepted}
        return func(**filtered)
    except Exception:
        # Some pybind callables don't expose full signatures.
        pass

    for optional in [[], ["decoding_method"], ["sample_rate", "feature_dim"], ["sample_rate", "feature_dim", "decoding_method"]]:
        attempt = {key: value for key, value in kwargs.items() if key not in optional}
        try:
            return func(**attempt)
        except TypeError:
            continue
    return func(**kwargs)


def _diff_transcript_suffix(previous_text: str, current_text: str) -> str:
    before = str(previous_text or "").strip()
    after = str(current_text or "").strip()
    if not before:
        return after
    if after.startswith(before):
        return after[len(before) :].strip()
    return after


def _accept_waveform(*, stream, sample_rate: int, samples: list[float]) -> None:
    if not hasattr(stream, "accept_waveform"):
        raise RuntimeError("Sherpa stream does not expose accept_waveform().")
    stream.accept_waveform(sample_rate, samples)


def _decode_stream(*, recognizer, stream) -> None:
    if hasattr(recognizer, "decode_stream"):
        recognizer.decode_stream(stream)
        return
    if hasattr(recognizer, "decode_streams"):
        recognizer.decode_streams([stream])
        return
    raise RuntimeError("Sherpa recognizer does not expose decode_stream()/decode_streams().")


def _extract_online_result(*, recognizer, stream) -> str:
    if hasattr(recognizer, "get_result"):
        try:
            result = recognizer.get_result(stream)
            if isinstance(result, str):
                return result.strip()
            return str(result or "").strip()
        except Exception:
            pass
    return _extract_stream_text(stream)


def _extract_stream_text(stream) -> str:
    result = getattr(stream, "result", None)
    if callable(result):
        result = result()
    if result is None and hasattr(stream, "get_result"):
        result = stream.get_result()

    if isinstance(result, dict):
        return str(result.get("text", "") or "").strip()
    if isinstance(result, str):
        return result.strip()

    text = getattr(result, "text", None)
    if text is not None:
        return str(text).strip()

    return str(result or "").strip()


def _read_wave_f32(path: Path) -> tuple[int, list[float]]:
    with wave.open(str(path), "rb") as wf:
        sample_rate = int(wf.getframerate())
        channels = int(wf.getnchannels())
        sample_width = int(wf.getsampwidth())
        frame_count = int(wf.getnframes())
        raw = wf.readframes(frame_count)

    if sample_width != 2:
        raise RuntimeError(f"Expected 16-bit PCM WAV input for sherpa adapter, got sample_width={sample_width}")

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
        str(max(8000, int(sample_rate))),
        str(output_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg clip extraction failed for sherpa input: {proc.stderr[-500:]}")


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
