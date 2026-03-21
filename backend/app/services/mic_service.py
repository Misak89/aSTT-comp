"""
Mic service — správa live mic sessions přes WebSocket.

Každá session:
  - má unikátní session_id
  - přijímá audio chunky z frontendu (PCM float32 base64 nebo binary frames)
  - posílá partial + final přepis zpět přes WebSocket
  - měří RTF, first word latency, RAM

Session lifecycle:
  create_session() → session_id
  WebSocket connection → audio frames → partial results
  stop_session()   → final result + metriky
"""
from __future__ import annotations

import base64
import json
import threading
import time
import uuid
from array import array
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import MODEL_STORE_ROOT, RUNS_ROOT

SAMPLE_RATE = 16000


@dataclass
class MicSessionState:
    session_id: str
    model_id: str
    model_params: dict
    created_at: str
    status: str = "idle"        # idle | recording | stopped
    transcript: str = ""
    partial: str = ""
    first_word_latency_ms: float | None = None
    elapsed_s: float = 0.0
    rtf: float = 0.0
    total_audio_s: float = 0.0
    error: str | None = None
    _session_obj: Any = field(default=None, repr=False)
    _started_perf: float = field(default=0.0, repr=False)
    _total_samples: int = field(default=0, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


_sessions: dict[str, MicSessionState] = {}
_sessions_lock = threading.Lock()


def create_session(model_id: str, model_params: dict | None = None) -> str:
    session_id = f"mic_{uuid.uuid4().hex[:8]}"
    state = MicSessionState(
        session_id=session_id,
        model_id=model_id,
        model_params=model_params or {},
        created_at=datetime.now(UTC).isoformat(),
    )
    with _sessions_lock:
        _sessions[session_id] = state
    return session_id


def get_session(session_id: str) -> MicSessionState | None:
    with _sessions_lock:
        return _sessions.get(session_id)


def start_recording(session_id: str) -> None:
    """Inicializuje STT live session pro daný model."""
    state = get_session(session_id)
    if state is None:
        raise ValueError(f"Session not found: {session_id}")

    adapter_key = _adapter_key(state.model_id)
    session_obj = _create_adapter_session(adapter_key, state.model_id, state.model_params)

    with state._lock:
        state._session_obj = session_obj
        state._started_perf = time.perf_counter()
        state.status = "recording"


def process_audio_chunk(
    session_id: str,
    samples: list[float],
    sample_rate: int = SAMPLE_RATE,
) -> dict[str, Any]:
    """Přijme audio chunk a vrátí partial výsledek."""
    state = get_session(session_id)
    if state is None:
        return {"error": "session not found"}
    if state.status != "recording":
        return {"error": f"session not recording (status={state.status})"}

    adapter_key = _adapter_key(state.model_id)
    chunk_fn = _get_chunk_fn(adapter_key)

    try:
        result = chunk_fn(session=state._session_obj, sample_rate=sample_rate, samples=samples)
    except Exception as exc:
        with state._lock:
            state.error = str(exc)
            state.status = "stopped"
        return {"error": str(exc)}

    with state._lock:
        state._total_samples += len(samples)
        state.partial = result.get("text_delta", "")
        state.transcript = result.get("text", state.transcript)
        if state.first_word_latency_ms is None:
            state.first_word_latency_ms = result.get("first_word_latency_ms")

    return {
        "type": "partial",
        "text": result.get("text", ""),
        "text_delta": result.get("text_delta", ""),
        "first_word_latency_ms": result.get("first_word_latency_ms"),
    }


def stop_recording(session_id: str) -> dict[str, Any]:
    """Finalizuje session a vrátí kompletní výsledek."""
    state = get_session(session_id)
    if state is None:
        return {"error": "session not found"}

    adapter_key = _adapter_key(state.model_id)
    finalize_fn = _get_finalize_fn(adapter_key)

    try:
        final = finalize_fn(session=state._session_obj)
    except Exception as exc:
        final = {}
        with state._lock:
            state.error = str(exc)

    elapsed_s = max(0.001, time.perf_counter() - state._started_perf)
    total_audio_s = state._total_samples / max(1, SAMPLE_RATE)
    rtf = elapsed_s / max(0.1, total_audio_s)

    with state._lock:
        state.status = "stopped"
        state.transcript = final.get("text", state.transcript)
        state.elapsed_s = round(elapsed_s, 3)
        state.rtf = round(rtf, 4)
        state.total_audio_s = round(total_audio_s, 2)
        if state.first_word_latency_ms is None:
            state.first_word_latency_ms = final.get("first_word_latency_ms")

    return {
        "type": "final",
        "text": state.transcript,
        "first_word_latency_ms": state.first_word_latency_ms,
        "elapsed_s": state.elapsed_s,
        "rtf": state.rtf,
        "total_audio_s": state.total_audio_s,
        "error": state.error,
    }


def decode_audio_frame(data: bytes | str) -> list[float]:
    """
    Dekóduje audio frame z WebSocket zprávy na list[float].

    Podporované formáty:
    - bytes: raw PCM int16 little-endian
    - str (JSON): {"samples": [0.1, -0.2, ...]}
    - str (base64): base64 enkódovaný PCM int16
    """
    if isinstance(data, bytes):
        pcm = array("h")
        pcm.frombytes(data)
        return [max(-1.0, min(1.0, s / 32768.0)) for s in pcm]

    if isinstance(data, str):
        stripped = data.strip()
        if stripped.startswith("{"):
            payload = json.loads(stripped)
            return [float(s) for s in payload.get("samples", [])]
        # base64
        raw = base64.b64decode(stripped)
        pcm = array("h")
        pcm.frombytes(raw)
        return [max(-1.0, min(1.0, s / 32768.0)) for s in pcm]

    return []


def list_audio_devices() -> list[dict]:
    """Vrátí seznam dostupných audio zařízení pro výběr v UI."""
    try:
        import sounddevice as sd  # type: ignore[import-not-found]
        devices = sd.query_devices()
        return [
            {
                "index": i,
                "name": d["name"],
                "max_input_channels": d["max_input_channels"],
                "default_samplerate": d["default_samplerate"],
            }
            for i, d in enumerate(devices)
            if d["max_input_channels"] > 0
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Adapter dispatch helpers
# ---------------------------------------------------------------------------

def _adapter_key(model_id: str) -> str:
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
    raise ValueError(f"Neznámý model_id pro mic session: {model_id}")


def _create_adapter_session(adapter: str, model_id: str, params: dict) -> Any:
    model_store = MODEL_STORE_ROOT

    if adapter == "vosk":
        from packages.adapters.vosk_runner import VoskRunConfig, resolve_vosk_model_dir, create_vosk_live_session
        model_dir = resolve_vosk_model_dir(model_store)
        if not model_dir:
            raise RuntimeError("VOSK model nenalezen")
        cfg = VoskRunConfig(
            model_dir=str(model_dir),
            sample_rate=int(params.get("sample_rate", SAMPLE_RATE)),
            chunk_seconds=float(params.get("chunk_seconds", 0.20)),
            set_words=bool(params.get("set_words", False)),
        )
        return create_vosk_live_session(config=cfg)

    elif adapter == "sherpa_onnx":
        from packages.adapters.sherpa_onnx_runner import SherpaRunConfig, resolve_sherpa_model_bundle, create_sherpa_live_session
        bundle = resolve_sherpa_model_bundle(model_store)
        if not bundle:
            raise RuntimeError("Sherpa model bundle nenalezen")
        cfg = SherpaRunConfig(
            tokens=bundle.tokens,
            encoder=bundle.encoder,
            decoder=bundle.decoder,
            joiner=bundle.joiner,
            provider=str(params.get("provider", "cpu")),
            num_threads=int(params.get("num_threads", 2)),
            sample_rate=int(params.get("sample_rate", SAMPLE_RATE)),
            decoding_method=str(params.get("decoding_method", "greedy_search")),
        )
        return create_sherpa_live_session(config=cfg)

    elif adapter == "moonshine":
        from packages.adapters.moonshine_runner import MoonshineRunConfig, resolve_moonshine_model_path, create_moonshine_live_session
        model_arch = str(params.get("model_arch", "medium"))
        model_path = resolve_moonshine_model_path(model_store, model_arch) or ""
        cfg = MoonshineRunConfig(
            model_path=model_path,
            model_arch=model_arch,
            analysis_interval_ms=int(params.get("analysis_interval_ms", 500)),
            language=str(params.get("language", "en")),
        )
        return create_moonshine_live_session(config=cfg)

    elif adapter in ("whisper_cpp", "qwen_asr"):
        raise ValueError(
            f"Model '{model_id}' nepodporuje mic mode — potřebuje celý audio soubor. "
            "Použij vosk, sherpa_onnx nebo moonshine."
        )

    raise ValueError(f"Neznámý adapter: {adapter}")


def _get_chunk_fn(adapter: str):
    if adapter == "vosk":
        from packages.adapters.vosk_runner import transcribe_vosk_live_chunk
        return transcribe_vosk_live_chunk
    if adapter == "sherpa_onnx":
        from packages.adapters.sherpa_onnx_runner import transcribe_sherpa_live_chunk
        return transcribe_sherpa_live_chunk
    if adapter == "moonshine":
        from packages.adapters.moonshine_runner import transcribe_moonshine_live_chunk
        return transcribe_moonshine_live_chunk
    raise ValueError(f"Chunk fn pro '{adapter}' neexistuje")


def _get_finalize_fn(adapter: str):
    if adapter == "vosk":
        from packages.adapters.vosk_runner import finalize_vosk_live_session
        return finalize_vosk_live_session
    if adapter == "sherpa_onnx":
        from packages.adapters.sherpa_onnx_runner import finalize_sherpa_live_session
        return finalize_sherpa_live_session
    if adapter == "moonshine":
        from packages.adapters.moonshine_runner import finalize_moonshine_live_session
        return finalize_moonshine_live_session
    raise ValueError(f"Finalize fn pro '{adapter}' neexistuje")
