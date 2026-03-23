"""
Streaming runner — dispatch audio streamu na správný live session adapter.

Podporované adaptery (streaming):
  vosk       → create_vosk_live_session / transcribe_vosk_live_chunk / finalize_vosk_live_session
  sherpa_onnx → create_sherpa_live_session / transcribe_sherpa_live_chunk / finalize_sherpa_live_session
  moonshine  → create_moonshine_live_session / transcribe_moonshine_live_chunk / finalize_moonshine_live_session
  whisper_cpp → bufferuje do temp WAV, pak spustí whisper-cli (nepodporuje streaming nativně)
  qwen_asr   → bufferuje do temp WAV, pak spustí Qwen model

Vstup: generátor (samples: list[float], sample_rate: int) — z stream_pipe.py nebo mic
Výstup: dict se stejnou strukturou jako run_*_source() — wer, cer, latency_ms, rtf, transcript_text, ...
"""
from __future__ import annotations

import os
import threading
import time
import wave
from array import array
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Generator

from packages.ingest.source_resolver import SourceEntry


@dataclass
class StreamingRunConfig:
    """Konfigurace streaming benchmarku."""
    model_id: str
    model_params: dict           # z _registry.py ParamSpec.default + user overrides
    model_store_root: str
    output_dir: str
    sample_seconds: int = 120
    chunk_seconds: int = 15      # délka jednoho chunku — z toho se odvozují milníky
    progress_callback: Callable[[str], None] | None = None
    transcript_callback: Callable[[str, int, str], None] | None = None  # (plain_text, percent, timestamped_text)


def run_streaming_benchmark(
    *,
    source: SourceEntry,
    audio_generator: Generator[tuple[list[float], int], None, None],
    config: StreamingRunConfig,
) -> dict[str, Any]:
    """
    Spustí streaming benchmark pro jeden zdroj a jeden model.

    Args:
        source: Metadata zdroje (video_id, URL, label).
        audio_generator: Generátor (samples, sample_rate) — z stream_pipe nebo mic.
        config: Konfigurace modelu a parametrů.

    Returns:
        Dict se stejnými klíči jako run_*_source() — kompatibilní s matrix runnerem.
    """
    adapter = _get_adapter_key(config.model_id)
    out_dir = Path(config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if adapter in ("vosk", "sherpa_onnx", "moonshine"):
        return _run_live_session(
            source=source,
            audio_generator=audio_generator,
            config=config,
            adapter=adapter,
        )
    elif adapter in ("whisper_cpp", "qwen_asr"):
        return _run_buffered(
            source=source,
            audio_generator=audio_generator,
            config=config,
            adapter=adapter,
        )
    else:
        raise ValueError(f"Neznámý adapter pro streaming: '{adapter}' (model_id={config.model_id})")


# ---------------------------------------------------------------------------
# Live session path (vosk, sherpa_onnx, moonshine)
# ---------------------------------------------------------------------------

def _run_live_session(
    *,
    source: SourceEntry,
    audio_generator: Generator[tuple[list[float], int], None, None],
    config: StreamingRunConfig,
    adapter: str,
) -> dict[str, Any]:
    session_factory, chunk_fn, finalize_fn, run_config = _build_live_session_components(
        adapter=adapter,
        config=config,
    )

    started = datetime.now(UTC)
    started_perf = time.perf_counter()

    _cb(config.progress_callback, f"Inicializuji {adapter} live session...")
    session = session_factory(config=run_config)

    total_samples = 0
    sample_rate = 16000
    last_result: dict = {}

    _cb(config.progress_callback, "Streamuji audio...")
    for samples, sr in audio_generator:
        sample_rate = sr
        total_samples += len(samples)
        last_result = chunk_fn(session=session, sample_rate=sr, samples=samples)

        elapsed = time.perf_counter() - started_perf
        if elapsed >= config.sample_seconds:
            break

    _cb(config.progress_callback, "Finalizuji přepis...")
    final = finalize_fn(session=session)

    elapsed_s = max(0.001, time.perf_counter() - started_perf)
    transcript_text = final.get("text") or last_result.get("text") or ""

    # Ulož transcript
    transcript_path = Path(config.output_dir) / f"{source.source_id}_{adapter}.txt"
    transcript_path.write_text(transcript_text + ("\n" if transcript_text else ""), encoding="utf-8")

    clip_duration = total_samples / max(1, sample_rate)
    rtf = elapsed_s / max(0.1, clip_duration)

    first_word_latency_ms = final.get("first_word_latency_ms") or last_result.get("first_word_latency_ms")
    latency_ms = first_word_latency_ms if isinstance(first_word_latency_ms, (int, float)) else int(elapsed_s * 1000.0)

    return _build_result(
        source=source,
        adapter=adapter,
        started=started,
        elapsed_s=elapsed_s,
        rtf=rtf,
        latency_ms=latency_ms,
        first_word_latency_ms=first_word_latency_ms,
        first_word_wall_ms=final.get("first_word_wall_ms"),
        first_word_audio_ms=final.get("first_word_audio_ms"),
        transcript_text=transcript_text,
        transcript_path=str(transcript_path),
        latency_mode="online_first_text_or_elapsed_ms",
    )


# ---------------------------------------------------------------------------
# Buffered path (whisper_cpp, qwen_asr)
# ---------------------------------------------------------------------------

def _run_buffered(
    *,
    source: SourceEntry,
    audio_generator: Generator[tuple[list[float], int], None, None],
    config: StreamingRunConfig,
    adapter: str,
) -> dict[str, Any]:
    """Bufferuje celé audio a spustí whisper JEDNOU → žádné halucinace, plný kontext.

    Po dokončení whisper provede „replay" podle timestamp segmentů — text postupně naskakuje
    s prodlevami odvozenými z časových razítek (20× zrychleno), takže UI vidí živý přepis.
    """
    out_dir = Path(config.output_dir)
    sample_rate = 16000

    started = datetime.now(UTC)
    started_at = time.perf_counter()

    all_pcm: list[int] = []
    total_pcm_samples = 0
    last_progress_s = 0.0

    _cb(config.progress_callback, f"⬇ Stahuji audio ({config.sample_seconds}s)...")

    for samples, sr in audio_generator:
        sample_rate = sr
        for s in samples:
            all_pcm.append(int(max(-32768, min(32767, s * 32767.0))))
        total_pcm_samples += len(samples)
        audio_pos_s = total_pcm_samples / max(1, sample_rate)
        if audio_pos_s - last_progress_s >= 10.0:
            last_progress_s = audio_pos_s
            _cb(config.progress_callback,
                f"⬇ Stahuji audio... {int(audio_pos_s)}s / {config.sample_seconds}s")
        if audio_pos_s >= config.sample_seconds:
            break

    # Zapiš celé audio do temp WAV
    temp_wav = out_dir / f"{source.source_id}_{adapter}_full.wav"
    if all_pcm:
        pcm_array = array("h", all_pcm)
        with wave.open(str(temp_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_array.tobytes())

    clip_duration = total_pcm_samples / max(1, sample_rate)
    _cb(config.progress_callback,
        f"▶ Spouštím {adapter} batch přepis ({round(clip_duration, 1)}s audia)...")

    batch_source = SourceEntry(
        source_id=source.source_id,
        label=source.label,
        origin_type="local_file",
        value=str(temp_wav),
        exists=True,
        canonical_url=source.canonical_url,
        video_id=source.video_id,
    )

    whisper_start = time.perf_counter()

    # Heartbeat thread — hlásí každých 5s kolik sekund whisper již běží
    _stop_heartbeat = threading.Event()
    def _heartbeat():
        while not _stop_heartbeat.wait(5.0):
            elapsed = round(time.perf_counter() - whisper_start, 0)
            _cb(config.progress_callback,
                f"⏳ Přepisuji... ({int(elapsed)}s zprac. / ~{int(clip_duration)}s audia)")
    threading.Thread(target=_heartbeat, daemon=True).start()

    batch_result = _run_batch_adapter(source=batch_source, adapter=adapter, config=config)
    _stop_heartbeat.set()
    whisper_elapsed = round(time.perf_counter() - whisper_start, 2)

    try:
        temp_wav.unlink()
    except Exception:
        pass

    elapsed_s = max(0.001, time.perf_counter() - started_at)
    full_transcript = batch_result.get("transcript_text", "")
    segments = batch_result.get("_segments", [])

    whisper_rtf_val = round(whisper_elapsed / max(0.1, clip_duration), 3)
    word_count = len(full_transcript.split()) if full_transcript else 0
    _cb(config.progress_callback,
        f"✓ Přepis hotov | zprac.: {whisper_elapsed}s | RTF: {whisper_rtf_val} | slov: {word_count}")
    time.sleep(0.1)  # krátká pauza aby polling stihl zaznamenat zprávu

    # Timestamp replay — emituje po skupinách chunk_seconds (plynulý nárůst textu)
    if segments and config.transcript_callback:
        total_duration_ms = max(1, segments[-1].get("offsets", {}).get("to", int(config.sample_seconds * 1000)))
        cs_ms = max(5000, config.chunk_seconds * 1000)
        next_milestone_ms = cs_ms
        accumulated_plain: list[str] = []
        accumulated_ts: list[str] = []
        for seg in segments:
            text = str(seg.get("text", "")).strip()
            offsets = seg.get("offsets", {})
            from_ms = offsets.get("from", 0)
            if text:
                mm = from_ms // 60000
                ss = (from_ms // 1000) % 60
                accumulated_plain.append(text)
                accumulated_ts.append(f"[{mm:02d}:{ss:02d}] {text}")
            # Emituj při překročení milníku chunk_seconds
            while from_ms >= next_milestone_ms:
                if accumulated_plain:
                    pct = min(10 + int(next_milestone_ms / total_duration_ms * 80), 89)
                    try:
                        config.transcript_callback(
                            " ".join(accumulated_plain), pct,
                            "\n".join(accumulated_ts),
                        )
                    except Exception:
                        pass
                    time.sleep(0.3)
                next_milestone_ms += cs_ms
        try:
            config.transcript_callback(full_transcript, 100, "\n".join(accumulated_ts))
        except Exception:
            pass
    elif config.transcript_callback and full_transcript:
        try:
            config.transcript_callback(full_transcript, 100, "")
        except Exception:
            pass

    rtf = elapsed_s / max(0.1, clip_duration)
    whisper_rtf = round(whisper_elapsed / max(0.1, clip_duration), 3)

    chunk_metrics: list[dict[str, Any]] = [{
        "chunk_start_s": 0.0,
        "chunk_end_s": round(clip_duration, 1),
        "chunk_duration_s": round(clip_duration, 1),
        "processing_s": whisper_elapsed,
        "rtf": whisper_rtf,
        "total_elapsed_s": round(elapsed_s, 1),
        "words": len(full_transcript.split()) if full_transcript else 0,
    }]

    transcript_path = out_dir / f"{source.source_id}_{adapter}.txt"
    transcript_path.write_text(full_transcript + ("\n" if full_transcript else ""), encoding="utf-8")

    result = _build_result(
        source=source,
        adapter=adapter,
        started=started,
        elapsed_s=elapsed_s,
        rtf=rtf,
        latency_ms=int(elapsed_s * 1000),
        first_word_latency_ms=None,
        first_word_wall_ms=None,
        first_word_audio_ms=None,
        transcript_text=full_transcript,
        transcript_path=str(transcript_path),
        latency_mode="single_batch_replay",
    )
    result["chunk_metrics"] = chunk_metrics
    return result


def _run_batch_adapter(*, source: SourceEntry, adapter: str, config: StreamingRunConfig) -> dict[str, Any]:
    model_store = Path(config.model_store_root)
    params = config.model_params
    out_dir = config.output_dir

    if adapter == "whisper_cpp":
        from packages.adapters.whisper_cpp_runner import (
            WhisperRunConfig, resolve_whisper_cli, resolve_whisper_model_file, run_whisper_source,
        )
        whisper_bin = resolve_whisper_cli(model_store)
        if not whisper_bin:
            raise RuntimeError("whisper-cli nenalezen v model_store")
        model_file = resolve_whisper_model_file(model_store, config.model_id)
        if not model_file:
            raise RuntimeError(f"Model soubor nenalezen pro {config.model_id}")
        run_config = WhisperRunConfig(
            whisper_bin=whisper_bin,
            model_path=str(model_file),
            language=str(params.get("language", "cs")),
            threads=int(params.get("threads", 4)),
            beam_size=params.get("beam_size"),
            best_of=params.get("best_of"),
            no_fallback=bool(params.get("no_fallback", True)),
        )
        return run_whisper_source(
            source=source,
            sample_seconds=config.sample_seconds,
            output_dir=out_dir,
            config=run_config,
        )

    elif adapter == "qwen_asr":
        from packages.adapters.qwen_asr_runner import QwenRunConfig, run_qwen_source
        model_path = str(model_store / config.model_id)
        run_config = QwenRunConfig(
            model_path=model_path,
            language=str(params.get("language", "Czech")),
            dtype=str(params.get("dtype", "float32")),
            device_map=str(params.get("device_map", "cpu")),
            max_new_tokens=int(params.get("max_new_tokens", 256)),
        )
        return run_qwen_source(
            source=source,
            sample_seconds=config.sample_seconds,
            output_dir=out_dir,
            config=run_config,
        )

    raise ValueError(f"Neznámý batch adapter: {adapter}")


# ---------------------------------------------------------------------------
# Live session factory helper
# ---------------------------------------------------------------------------

def _build_live_session_components(*, adapter: str, config: StreamingRunConfig):
    """Vrátí (session_factory, chunk_fn, finalize_fn, run_config) pro daný adapter."""
    model_store = Path(config.model_store_root)
    params = config.model_params

    if adapter == "vosk":
        from packages.adapters.vosk_runner import (
            VoskRunConfig, resolve_vosk_model_dir,
            create_vosk_live_session, transcribe_vosk_live_chunk, finalize_vosk_live_session,
        )
        model_dir = resolve_vosk_model_dir(model_store)
        if not model_dir:
            raise RuntimeError("VOSK model nenalezen v model_store")
        run_config = VoskRunConfig(
            model_dir=str(model_dir),
            sample_rate=int(params.get("sample_rate", 16000)),
            chunk_seconds=float(params.get("chunk_seconds", 0.20)),
            set_words=bool(params.get("set_words", False)),
        )
        return create_vosk_live_session, transcribe_vosk_live_chunk, finalize_vosk_live_session, run_config

    elif adapter == "sherpa_onnx":
        from packages.adapters.sherpa_onnx_runner import (
            SherpaRunConfig, resolve_sherpa_model_bundle,
            create_sherpa_live_session, transcribe_sherpa_live_chunk, finalize_sherpa_live_session,
        )
        bundle = resolve_sherpa_model_bundle(model_store)
        if not bundle:
            raise RuntimeError("Sherpa model bundle nenalezen v model_store")
        run_config = SherpaRunConfig(
            tokens=bundle.tokens,
            encoder=bundle.encoder,
            decoder=bundle.decoder,
            joiner=bundle.joiner,
            provider=str(params.get("provider", "cpu")),
            num_threads=int(params.get("num_threads", 2)),
            sample_rate=int(params.get("sample_rate", 16000)),
            decoding_method=str(params.get("decoding_method", "greedy_search")),
        )
        return create_sherpa_live_session, transcribe_sherpa_live_chunk, finalize_sherpa_live_session, run_config

    elif adapter == "moonshine":
        from packages.adapters.moonshine_runner import (
            MoonshineRunConfig, resolve_moonshine_model_path,
            create_moonshine_live_session, transcribe_moonshine_live_chunk, finalize_moonshine_live_session,
        )
        model_arch = str(params.get("model_arch", "medium"))
        model_path = resolve_moonshine_model_path(model_store, model_arch) or ""
        run_config = MoonshineRunConfig(
            model_path=model_path,
            model_arch=model_arch,
            analysis_interval_ms=int(params.get("analysis_interval_ms", 500)),
            language=str(params.get("language", "en")),
        )
        return create_moonshine_live_session, transcribe_moonshine_live_chunk, finalize_moonshine_live_session, run_config

    raise ValueError(f"Nepodporovaný live session adapter: {adapter}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_adapter_key(model_id: str) -> str:
    """Vrátí klíč adapteru z model_id."""
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
    raise ValueError(f"Nelze určit adapter pro model_id='{model_id}'")


def _build_result(
    *,
    source: SourceEntry,
    adapter: str,
    started: datetime,
    elapsed_s: float,
    rtf: float,
    latency_ms: float,
    first_word_latency_ms: float | None,
    first_word_wall_ms: float | None,
    first_word_audio_ms: float | None,
    transcript_text: str,
    transcript_path: str,
    latency_mode: str,
    cpu_percent: float | None = None,
    ram_mb: float | None = None,
) -> dict[str, Any]:
    return {
        "source_id": source.source_id,
        "source_label": source.label,
        "origin_type": source.origin_type,
        "wer": None,
        "cer": None,
        "latency_ms": round(float(latency_ms), 1),
        "first_word_latency_ms": round(float(first_word_latency_ms), 1) if isinstance(first_word_latency_ms, (int, float)) else None,
        "first_word_wall_ms": round(float(first_word_wall_ms), 1) if isinstance(first_word_wall_ms, (int, float)) else None,
        "first_word_audio_ms": round(float(first_word_audio_ms), 1) if isinstance(first_word_audio_ms, (int, float)) else None,
        "rtf": round(float(rtf), 4),
        "cpu_percent": round(float(cpu_percent), 1) if isinstance(cpu_percent, (int, float)) else None,
        "ram_mb": round(float(ram_mb), 1) if isinstance(ram_mb, (int, float)) else None,
        "speaker_attribution_accuracy": None,
        "speaker_confusion_rate": None,
        "transcript_text": transcript_text,
        "transcript_path": transcript_path,
        "json_path": None,
        "engine": adapter,
        "engine_started_at_utc": started.isoformat(),
        "engine_elapsed_seconds": round(elapsed_s, 4),
        "latency_mode": latency_mode,
    }


def _cb(callback: Callable[[str], None] | None, message: str) -> None:
    if callback is not None:
        try:
            callback(message)
        except Exception:
            pass
