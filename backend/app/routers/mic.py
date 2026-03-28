"""
Mic router — WebSocket endpoint pro live přepis z mikrofonu.

Protokol:
  Client → Server: binary PCM int16 LE frames NEBO JSON {"samples": [...]}
  Server → Client: JSON {"type": "partial"|"final", "text": "...", ...}

Endpoints:
  GET  /api/mic/devices              — seznam audio zařízení
  POST /api/mic/sessions             — vytvoří session, vrátí session_id
  WS   /api/mic/sessions/{id}/stream — WebSocket pro audio stream
  GET  /api/mic/sessions/{id}        — stav session
  POST /api/mic/sessions/{id}/stop   — zastaví session
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Optional

from ..services import mic_service

router = APIRouter(prefix="/api/mic", tags=["mic"])


class CreateSessionRequest(BaseModel):
    model_id: str
    model_params: Optional[dict] = None


class CreateSessionResponse(BaseModel):
    session_id: str
    model_id: str
    created_at: str


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@router.get("/devices")
def list_devices():
    """Vrátí seznam dostupných mikrofon zařízení."""
    return mic_service.list_audio_devices()


@router.post("/sessions", response_model=CreateSessionResponse, status_code=201)
def create_session(req: CreateSessionRequest):
    """Vytvoří novou mic session. Vrátí session_id pro WebSocket připojení."""
    session_id = mic_service.create_session(req.model_id, req.model_params or {})
    state = mic_service.get_session(session_id)
    return CreateSessionResponse(
        session_id=session_id,
        model_id=state.model_id,
        created_at=state.created_at,
    )


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    """Vrátí aktuální stav session (transcript, metriky, status)."""
    state = mic_service.get_session(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {
        "session_id": state.session_id,
        "model_id": state.model_id,
        "status": state.status,
        "transcript": state.transcript,
        "first_word_latency_ms": state.first_word_latency_ms,
        "first_token_ms_p50": state.first_token_ms_p50,
        "first_token_ms_p95": state.first_token_ms_p95,
        "segment_finalize_ms_p50": state.segment_finalize_ms_p50,
        "segment_finalize_ms_p95": state.segment_finalize_ms_p95,
        "drop_rate": state.drop_rate,
        "session_resets": state.session_resets,
        "worker_rss_peak_mb": state.worker_rss_peak_mb,
        "chunk_count": state.chunk_count,
        "dropped_chunks": state.dropped_chunks,
        "elapsed_s": state.elapsed_s,
        "rtf": state.rtf,
        "total_audio_s": state.total_audio_s,
        "reason_code": state.reason_code,
        "error": state.error,
    }


@router.post("/sessions/{session_id}/stop")
def stop_session(session_id: str):
    """Zastaví mic session a vrátí finální výsledek."""
    state = mic_service.get_session(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="session not found")
    result = mic_service.stop_recording(session_id)
    return result


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@router.websocket("/sessions/{session_id}/stream")
async def ws_mic_stream(websocket: WebSocket, session_id: str):
    """
    WebSocket pro live mic stream.

    Lifecycle:
    1. Client se připojí
    2. Server inicializuje STT session (start_recording)
    3. Client posílá audio frames (binary PCM nebo JSON)
    4. Server posílá zpět partial výsledky
    5. Client pošle JSON {"action": "stop"} nebo zavře spojení
    6. Server finalizuje a pošle {"type": "final", ...}
    """
    await websocket.accept()

    state = mic_service.get_session(session_id)
    if state is None:
        await websocket.send_text(json.dumps({"error": "session not found"}))
        await websocket.close()
        return

    try:
        mic_service.start_recording(session_id)
        await websocket.send_text(json.dumps({"type": "started", "session_id": session_id}))
    except Exception as exc:
        await websocket.send_text(json.dumps({"error": str(exc)}))
        await websocket.close()
        return

    try:
        while True:
            # Přijmi zprávu — buď binary (PCM) nebo text (JSON control)
            message = await websocket.receive()

            if "bytes" in message and message["bytes"]:
                samples = mic_service.decode_audio_frame(message["bytes"])
                if samples:
                    result = mic_service.process_audio_chunk(session_id, samples)
                    await websocket.send_text(json.dumps(result))

            elif "text" in message and message["text"]:
                try:
                    payload = json.loads(message["text"])
                except Exception:
                    payload = {}

                action = payload.get("action", "")
                if action == "stop":
                    break

                # Alternativa: JSON {"samples": [...]}
                samples = mic_service.decode_audio_frame(message["text"])
                if samples:
                    result = mic_service.process_audio_chunk(session_id, samples)
                    await websocket.send_text(json.dumps(result))

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_text(json.dumps({"error": str(exc)}))
        except Exception:
            pass

    # Finalizuj
    try:
        final = mic_service.stop_recording(session_id)
        await websocket.send_text(json.dumps(final))
    except Exception:
        pass

    try:
        await websocket.close()
    except Exception:
        pass
