"""
Streaming runner — dispatch audio streamu na správný live session adapter.

Podporované adaptery (streaming):
  vosk       → create_vosk_live_session / transcribe_vosk_live_chunk / finalize_vosk_live_session
  sherpa_onnx → create_sherpa_live_session / transcribe_sherpa_live_chunk / finalize_sherpa_live_session
  moonshine  → create_moonshine_live_session / transcribe_moonshine_live_chunk / finalize_moonshine_live_session
  faster_whisper → create_faster_whisper_live_session / transcribe_faster_whisper_live_chunk / finalize_faster_whisper_live_session
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
    source_wav_path: str | None = None  # přímá cesta k WAV — přeskočí double int16→float→int16 konverzi


def run_streaming_benchmark(
    *,
    source: SourceEntry,
    audio_generator: Generator[tuple[list[float], int], None, None] | None,
    config: StreamingRunConfig,
) -> dict[str, Any]:
    """
    Spustí streaming benchmark pro jeden zdroj a jeden model.

    Args:
        source: Metadata zdroje (video_id, URL, label).
        audio_generator: Generátor (samples, sample_rate) — z stream_pipe nebo mic.
            Může být None pro buffered adaptery (whisper_cpp, qwen_asr) pokud je
            config.source_wav_path nastaven — WAV se čte přímo ze souboru.
        config: Konfigurace modelu a parametrů.

    Returns:
        Dict se stejnými klíči jako run_*_source() — kompatibilní s matrix runnerem.
    """
    adapter = _get_adapter_key(config.model_id)
    out_dir = Path(config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if adapter in ("vosk", "sherpa_onnx", "moonshine", "faster_whisper"):
        if audio_generator is None:
            raise ValueError(f"audio_generator je povinný pro live-session adapter '{adapter}'")
        return _run_live_session(
            source=source,
            audio_generator=audio_generator,
            config=config,
            adapter=adapter,
        )
    elif adapter in ("whisper_cpp", "qwen_asr"):
        if audio_generator is None and not config.source_wav_path:
            raise ValueError(
                f"Pro buffered adapter '{adapter}' musí být zadán audio_generator nebo source_wav_path"
            )
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
    chunk_metrics: list[dict[str, Any]] = []
    first_partial_latency_ms: float | None = None

    _cb(config.progress_callback, "Streamuji audio...")
    for samples, sr in audio_generator:
        sample_rate = sr
        total_samples += len(samples)
        chunk_started = time.perf_counter()
        last_result = chunk_fn(session=session, sample_rate=sr, samples=samples)
        chunk_processing_s = max(0.0, time.perf_counter() - chunk_started)

        elapsed = time.perf_counter() - started_perf
        chunk_audio_s = len(samples) / max(1, sr)
        chunk_rtf = chunk_processing_s / max(0.001, chunk_audio_s)
        chunk_text = str(last_result.get("text") or "").strip()
        if chunk_text and first_partial_latency_ms is None:
            first_partial_latency_ms = round(elapsed * 1000.0, 1)
        chunk_metrics.append({
            "chunk_duration_s": round(chunk_audio_s, 3),
            "processing_s": round(chunk_processing_s, 4),
            "rtf": round(chunk_rtf, 4),
            "total_elapsed_s": round(elapsed, 4),
            "words": len(chunk_text.split()) if chunk_text else 0,
        })
        clip_audio_s = total_samples / max(1, sample_rate)
        if clip_audio_s >= config.sample_seconds:
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

    result = _build_result(
        source=source,
        adapter=adapter,
        started=started,
        elapsed_s=elapsed_s,
        rtf=rtf,
        latency_ms=latency_ms,
        first_word_latency_ms=first_word_latency_ms,
        first_partial_latency_ms=first_partial_latency_ms,
        first_word_wall_ms=final.get("first_word_wall_ms"),
        first_word_audio_ms=final.get("first_word_audio_ms"),
        transcript_text=transcript_text,
        transcript_path=str(transcript_path),
        latency_mode="online_first_text_or_elapsed_ms",
    )
    result["chunk_metrics"] = chunk_metrics
    return result


# ---------------------------------------------------------------------------
# Buffered path (whisper_cpp, qwen_asr)
# ---------------------------------------------------------------------------

def _run_buffered(
    *,
    source: SourceEntry,
    audio_generator: Generator[tuple[list[float], int], None, None] | None,
    config: StreamingRunConfig,
    adapter: str,
) -> dict[str, Any]:
    """Bufferuje celé audio a spustí whisper JEDNOU → žádné halucinace, plný kontext.

    Po dokončení whisper provede „replay" podle timestamp segmentů — text postupně naskakuje
    s prodlevami odvozenými z časových razítek (20× zrychleno), takže UI vidí živý přepis.
    """
    out_dir = Path(config.output_dir)

    started = datetime.now(UTC)
    started_at = time.perf_counter()

    _temp_wav_created = False  # True jen pokud jsme vytvořili dočasný WAV (nutno smazat)

    if config.source_wav_path:
        # Přímá cesta k WAV — žádná double konverze int16→float32→int16
        src_wav = Path(config.source_wav_path)
        with wave.open(str(src_wav), "rb") as wf:
            clip_duration = wf.getnframes() / max(1, wf.getframerate())
        temp_wav = src_wav
        _cb(config.progress_callback,
            f"▶ Spouštím {adapter} batch přepis ({round(clip_duration, 1)}s audia)...")
    else:
        # Bufferuj z generátoru → zapiš temp WAV
        sample_rate = 16000
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

        temp_wav = out_dir / f"{source.source_id}_{adapter}_full.wav"
        if all_pcm:
            pcm_array = array("h", all_pcm)
            with wave.open(str(temp_wav), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(pcm_array.tobytes())
            _temp_wav_created = True

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

    try:
        batch_result = _run_batch_adapter(source=batch_source, adapter=adapter, config=config)
    finally:
        _stop_heartbeat.set()  # vždy zastav heartbeat — i při výjimce

    whisper_elapsed = round(time.perf_counter() - whisper_start, 2)

    if _temp_wav_created:
        try:
            temp_wav.unlink()
        except Exception:
            pass

    pipeline_elapsed_s = max(0.001, time.perf_counter() - started_at)
    full_transcript = batch_result.get("transcript_text", "")
    segments = batch_result.get("_segments", [])

    whisper_rtf_val = round(whisper_elapsed / max(0.1, clip_duration), 3)
    word_count = len(full_transcript.split()) if full_transcript else 0
    _cb(config.progress_callback,
        f"✓ Přepis hotov | zprac.: {whisper_elapsed}s | RTF: {whisper_rtf_val} | slov: {word_count}")
    if config.transcript_callback is not None:
        time.sleep(0.1)  # pauza jen pokud UI čeká na replay — při tuningu zbytečné

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

    # Engine elapsed = čisté dekódování modelu (bez UI replay).
    engine_elapsed_s = max(0.001, float(whisper_elapsed))
    rtf = engine_elapsed_s / max(0.1, clip_duration)
    whisper_rtf = round(whisper_elapsed / max(0.1, clip_duration), 3)

    latency_ms = int(engine_elapsed_s * 1000)
    first_word_latency_ms = None
    first_word_audio_ms = None
    latency_mode = "single_batch_replay"

    # True online probe pro whisper: first text latency po prvním chunku (chunk + decode prvního chunku).
    online_probe_enabled = adapter == "whisper_cpp" and (
        bool(config.model_params.get("_online_latency_probe", False))
        or os.environ.get("ASTT_WHISPER_ONLINE_PROBE", "0") == "1"
    )
    if online_probe_enabled:
        try:
            probe = _probe_whisper_online_latency(
                source_wav=Path(temp_wav),
                source=source,
                config=config,
            )
            if probe is not None:
                latency_ms = int(probe.get("first_text_latency_ms", latency_ms))
                first_word_latency_ms = probe.get("first_text_latency_ms")
                first_word_audio_ms = probe.get("first_word_audio_ms")
                latency_mode = "online_probe_first_chunk_ms"
        except Exception as exc:
            _cb(config.progress_callback, f"⚠ online latency probe fail ({exc})")

    chunk_metrics: list[dict[str, Any]] = [{
        "chunk_start_s": 0.0,
        "chunk_end_s": round(clip_duration, 1),
        "chunk_duration_s": round(clip_duration, 1),
        "processing_s": whisper_elapsed,
        "rtf": whisper_rtf,
        "total_elapsed_s": round(pipeline_elapsed_s, 1),
        "words": len(full_transcript.split()) if full_transcript else 0,
    }]

    transcript_path = out_dir / f"{source.source_id}_{adapter}.txt"
    transcript_path.write_text(full_transcript + ("\n" if full_transcript else ""), encoding="utf-8")

    result = _build_result(
        source=source,
        adapter=adapter,
        started=started,
        elapsed_s=engine_elapsed_s,
        rtf=rtf,
        latency_ms=latency_ms,
        first_word_latency_ms=first_word_latency_ms,
        first_partial_latency_ms=None,
        first_word_wall_ms=None,
        first_word_audio_ms=first_word_audio_ms,
        transcript_text=full_transcript,
        transcript_path=str(transcript_path),
        latency_mode=latency_mode,
        cpu_percent=batch_result.get("cpu_percent"),
        ram_mb=batch_result.get("ram_mb"),
    )
    if "model_cached" in batch_result:
        result["model_cached"] = batch_result.get("model_cached")
    result["chunk_metrics"] = chunk_metrics
    result["total_audio_s"] = round(clip_duration, 2)
    if segments:
        result["_segments"] = segments
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
        use_server_cache = bool(params.get("_use_model_cache", False)) or os.environ.get("ASTT_WHISPER_SERVER_CACHE", "0") == "1"
        run_config = WhisperRunConfig(
            whisper_bin=whisper_bin,
            model_path=str(model_file),
            language=str(params.get("language", "cs")),
            threads=int(params.get("threads", 4)),
            beam_size=params.get("beam_size"),
            best_of=params.get("best_of"),
            no_fallback=bool(params.get("no_fallback", True)),
            initial_prompt=params.get("initial_prompt") or None,
            use_server_cache=use_server_cache,
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


def _probe_whisper_online_latency(
    *,
    source_wav: Path,
    source: SourceEntry,
    config: StreamingRunConfig,
) -> dict[str, float] | None:
    """Vrátí odhad first text latence pro whisper v online chunk režimu."""
    if not source_wav.exists():
        return None

    probe_chunk_s = float(max(1, min(config.chunk_seconds, int(max(1, config.sample_seconds)))))
    probe_wav = Path(config.output_dir) / f"{source.source_id}_whisper_probe.wav"

    with wave.open(str(source_wav), "rb") as wf:
        sr = wf.getframerate()
        sw = wf.getsampwidth()
        nc = wf.getnchannels()
        n_frames = min(wf.getnframes(), int(probe_chunk_s * sr))
        raw = wf.readframes(n_frames)
    with wave.open(str(probe_wav), "wb") as wf_out:
        wf_out.setnchannels(nc)
        wf_out.setsampwidth(sw)
        wf_out.setframerate(sr)
        wf_out.writeframes(raw)

    probe_source = SourceEntry(
        source_id=f"{source.source_id}_probe",
        label=source.label,
        origin_type="local_file",
        value=str(probe_wav),
        exists=True,
        canonical_url=source.canonical_url,
        video_id=source.video_id,
    )
    probe_cfg = StreamingRunConfig(
        model_id=config.model_id,
        model_params=dict(config.model_params),
        model_store_root=config.model_store_root,
        output_dir=str(Path(config.output_dir) / "_probe"),
        sample_seconds=max(1, int(round(probe_chunk_s))),
        chunk_seconds=config.chunk_seconds,
    )

    try:
        result = _run_batch_adapter(source=probe_source, adapter="whisper_cpp", config=probe_cfg)
        decode_ms = float(result.get("latency_ms") or 0.0)
        first_text_latency_ms = probe_chunk_s * 1000.0 + decode_ms
        first_word_audio_ms = result.get("first_word_audio_ms")
        out = {"first_text_latency_ms": round(first_text_latency_ms, 1)}
        if isinstance(first_word_audio_ms, (int, float)):
            out["first_word_audio_ms"] = float(first_word_audio_ms)
        return out
    finally:
        try:
            probe_wav.unlink(missing_ok=True)
        except Exception:
            pass


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
        preferred_language = "cs" if config.model_id == "sherpa_onnx_parakeet_cs_int8" else None
        # Nejprve zkus scoped kořen model_id (zabrání tomu, aby sherpa_onnx_small
        # omylem sáhl na parakeet bundle nalezený jinde v model_store rootu).
        scoped_root = model_store / config.model_id
        resolver_root = scoped_root if scoped_root.exists() else model_store
        bundle = resolve_sherpa_model_bundle(resolver_root, preferred_language=preferred_language)
        if bundle is None and resolver_root != model_store:
            bundle = resolve_sherpa_model_bundle(model_store, preferred_language=preferred_language)
        if not bundle:
            raise RuntimeError("Sherpa model bundle nenalezen v model_store")
        if config.model_id == "sherpa_onnx_small" and "parakeet" in str(bundle.model_dir).lower():
            raise RuntimeError(
                "sherpa_onnx_small mapuje na nekompatibilní Parakeet bundle. "
                "Ověř runtime/model_store/sherpa_onnx_small."
            )
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

    elif adapter == "faster_whisper":
        from packages.adapters.faster_whisper_runner import (
            FasterWhisperRunConfig,
            create_faster_whisper_live_session,
            transcribe_faster_whisper_live_chunk,
            finalize_faster_whisper_live_session,
            resolve_faster_whisper_model_path,
        )
        model_path = resolve_faster_whisper_model_path(model_store, config.model_id)
        if model_path is None:
            raise RuntimeError(
                f"faster-whisper model bundle nenalezen pro {config.model_id}. "
                f"Očekáván CTranslate2 model pod runtime/model_store/{config.model_id}/model.bin"
            )
        run_config = FasterWhisperRunConfig(
            model_path=str(model_path),
            language=str(params.get("language", "cs")),
            threads=int(params.get("threads", 4)),
            beam_size=int(params.get("beam_size", 1)),
            best_of=int(params.get("best_of", 1)),
            device=str(params.get("device", "cpu")),
            compute_type=str(params.get("compute_type", "int8")),
        )
        return (
            create_faster_whisper_live_session,
            transcribe_faster_whisper_live_chunk,
            finalize_faster_whisper_live_session,
            run_config,
        )

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
    if model_id.startswith("faster_whisper"):
        return "faster_whisper"
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
    first_partial_latency_ms: float | None,
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
        "first_partial_latency_ms": round(float(first_partial_latency_ms), 1) if isinstance(first_partial_latency_ms, (int, float)) else None,
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
