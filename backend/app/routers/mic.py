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

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Any, Optional

from ..services import mic_service

router = APIRouter(prefix="/api/mic", tags=["mic"])


class CreateSessionRequest(BaseModel):
    model_id: str
    model_params: Optional[dict] = None


class CreateSessionResponse(BaseModel):
    session_id: str
    model_id: str
    created_at: str
    orchestrator_mode: str | None = None
    run_id: str | None = None
    sequence_id: str | None = None
    sequence_index: int | None = None
    sequence_total: int | None = None
    event_contract_version: str | None = None
    preflight_ok: bool = True
    preflight_errors: list[str] = Field(default_factory=list)
    preflight_warnings: list[str] = Field(default_factory=list)


class ManualMicRecordRequest(BaseModel):
    model_id: str
    metrics: dict = Field(default_factory=dict)
    note: Optional[str] = None
    quality_assessment: Optional[str] = None
    transcript: Optional[str] = None
    source: Optional[str] = "manual_user_input"


class ManualMicRecordResponse(BaseModel):
    record_id: str
    history_path: str
    latest_model_path: str
    latest_path: str
    saved_at: str


class MicClientSequenceEventRequest(BaseModel):
    event: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)


class MicClientSequenceEventResponse(BaseModel):
    ok: bool
    event: str
    session_id: str | None = None


class ManualMicRecordListItem(BaseModel):
    record_id: str
    saved_at: str
    model_id: str
    note: str
    quality_assessment: str
    source: str
    transcript: str
    metrics: dict[str, Any] = Field(default_factory=dict)


class ManualMicRecordListResponse(BaseModel):
    records: list[ManualMicRecordListItem] = Field(default_factory=list)


class ManualMicRecordDeleteResponse(BaseModel):
    record_id: str
    deleted: bool
    deleted_count: int


class ManualMicRecordBulkDeleteResponse(BaseModel):
    deleted: int
    remaining: int
    model_id: str | None = None
    mic_test_mode: str | None = None


class MobileLoopPackageRequest(BaseModel):
    video_id: str = Field(min_length=1)
    clip_from_s: float = Field(ge=0)
    clip_to_s: float = Field(gt=0)
    pause_s: float = Field(default=15, ge=0, le=3600)
    repeat_count: int = Field(default=1, ge=1, le=200)
    include_sync_round: bool = True


class MobileLoopPackageResponse(BaseModel):
    package_id: str
    created_at: str
    video_id: str
    video_title: str
    clip_from_s: float
    clip_to_s: float
    clip_duration_s: float
    pause_s: float
    measured_rounds: int
    sync_rounds: int
    total_rounds: int
    total_duration_s: float
    wav_url: str
    download_url: str
    instructions: str
    reference_excerpt: str | None = None


class MobileLoopPackageListItem(BaseModel):
    package_id: str
    created_at: str
    video_id: str
    video_title: str
    clip_from_s: float
    clip_to_s: float
    clip_duration_s: float
    pause_s: float
    measured_rounds: int
    sync_rounds: int
    total_rounds: int
    total_duration_s: float
    wav_url: str
    download_url: str
    instructions: str = ""
    reference_excerpt: str | None = None
    wav_exists: bool = False
    zip_exists: bool = False


class MobileLoopPackageListResponse(BaseModel):
    packages: list[MobileLoopPackageListItem] = Field(default_factory=list)


class MobileLoopPackageDeleteResponse(BaseModel):
    package_id: str
    deleted: bool


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
    orchestrator = mic_service.get_orchestrator_payload(state)
    return CreateSessionResponse(
        session_id=session_id,
        model_id=state.model_id,
        created_at=state.created_at,
        orchestrator_mode=state.orchestrator_mode,
        run_id=state.run_id,
        sequence_id=state.sequence_id,
        sequence_index=state.sequence_index,
        sequence_total=state.sequence_total,
        event_contract_version=orchestrator.get("event_contract_version"),
        preflight_ok=state.preflight_ok,
        preflight_errors=list(state.preflight_errors or []),
        preflight_warnings=list(state.preflight_warnings or []),
    )


@router.post("/manual-records", response_model=ManualMicRecordResponse, status_code=201)
def save_manual_record(req: ManualMicRecordRequest):
    result = mic_service.save_manual_record(
        model_id=req.model_id,
        metrics=req.metrics or {},
        note=req.note,
        quality_assessment=req.quality_assessment,
        transcript=req.transcript,
        source=req.source or "manual_user_input",
    )
    return ManualMicRecordResponse(**result)


@router.post("/sequence-events", response_model=MicClientSequenceEventResponse, status_code=201)
def log_client_sequence_event(req: MicClientSequenceEventRequest):
    result = mic_service.log_client_sequence_event(req.event, req.payload or {})
    return MicClientSequenceEventResponse(**result)


@router.get("/manual-records", response_model=ManualMicRecordListResponse)
def list_manual_records(
    limit: int = Query(default=50, ge=1, le=500),
    model_id: Optional[str] = Query(default=None),
):
    records = mic_service.list_manual_records(limit=limit, model_id=model_id)
    return ManualMicRecordListResponse(records=[ManualMicRecordListItem(**item) for item in records])


@router.delete("/manual-records/{record_id}", response_model=ManualMicRecordDeleteResponse)
def delete_manual_record(record_id: str):
    try:
        result = mic_service.delete_manual_record(record_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ManualMicRecordDeleteResponse(**result)


@router.delete("/manual-records", response_model=ManualMicRecordBulkDeleteResponse)
def delete_manual_records(
    model_id: Optional[str] = Query(default=None),
    mic_test_mode: Optional[str] = Query(default=None),
):
    try:
        result = mic_service.delete_manual_records(model_id=model_id, mic_test_mode=mic_test_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ManualMicRecordBulkDeleteResponse(**result)


@router.post("/mobile-loop-packages", response_model=MobileLoopPackageResponse, status_code=201)
def create_mobile_loop_package(req: MobileLoopPackageRequest):
    try:
        payload = mic_service.generate_mobile_loop_package(
            video_id=req.video_id,
            clip_from_s=req.clip_from_s,
            clip_to_s=req.clip_to_s,
            pause_s=req.pause_s,
            repeat_count=req.repeat_count,
            include_sync_round=req.include_sync_round,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Generování loop balíčku selhalo: {exc}") from exc
    return MobileLoopPackageResponse(**payload)


@router.get("/mobile-loop-packages", response_model=MobileLoopPackageListResponse)
def list_mobile_loop_packages(
    limit: int = Query(default=50, ge=1, le=500),
    video_id: Optional[str] = Query(default=None),
):
    rows = mic_service.list_mobile_loop_packages(limit=limit, video_id=video_id)
    return MobileLoopPackageListResponse(
        packages=[MobileLoopPackageListItem(**row) for row in rows]
    )


@router.delete("/mobile-loop-packages/{package_id}", response_model=MobileLoopPackageDeleteResponse)
def delete_mobile_loop_package(package_id: str):
    try:
        result = mic_service.delete_mobile_loop_package(package_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MobileLoopPackageDeleteResponse(**result)


@router.get("/mobile-loop-packages/{package_id}/download")
def download_mobile_loop_package(package_id: str):
    try:
        files = mic_service.get_mobile_loop_package_files(package_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path=str(files["zip"]),
        media_type="application/zip",
        filename=files["zip"].name,
    )


@router.get("/mobile-loop-packages/{package_id}/wav")
def download_mobile_loop_wav(package_id: str):
    try:
        files = mic_service.get_mobile_loop_package_files(package_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path=str(files["wav"]),
        media_type="audio/wav",
        filename=files["wav"].name,
    )


@router.get("/sequences/{token}")
def get_sequence_report(token: str):
    """Vrátí agregovaný report sekvence (všechny trialy, klasifikace, metriky)."""
    report = mic_service.get_sequence_report(token)
    if report is None:
        raise HTTPException(status_code=404, detail="sequence report not found")
    return report


@router.get("/sequences/{token}/readiness")
def get_sequence_readiness(
    token: str,
    min_models: int = Query(default=3, ge=1, le=20),
):
    readiness = mic_service.get_sequence_readiness(token, min_models=min_models)
    if readiness is None:
        raise HTTPException(status_code=404, detail="sequence report not found")
    return readiness


@router.get("/contract")
def get_v7_contract():
    return mic_service.get_v7_contract_metadata()


@router.get("/sequences/{token}/export.csv")
def export_sequence_report_csv(token: str):
    """Vrátí sequence report jako CSV soubor ke stažení."""
    csv_path = mic_service.get_sequence_report_csv_path(token)
    if csv_path is None:
        raise HTTPException(status_code=404, detail="sequence CSV not found")
    return FileResponse(
        path=str(csv_path),
        media_type="text/csv",
        filename=f"sequence_{token[:8]}.csv",
    )


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    """Vrátí aktuální stav session (transcript, metriky, status)."""
    state = mic_service.get_session(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="session not found")
    orchestrator = mic_service.get_orchestrator_payload(state)
    return {
        "session_id": state.session_id,
        "model_id": state.model_id,
        "status": state.status,
        "transcript": state.transcript,
        "first_word_latency_ms": state.first_word_latency_ms,
        "first_word_wall_ms": state.first_word_wall_ms,
        "first_word_audio_ms": state.first_word_audio_ms,
        "first_token_ms_p50": state.first_token_ms_p50,
        "first_token_ms_p95": state.first_token_ms_p95,
        "segment_finalize_ms_p50": state.segment_finalize_ms_p50,
        "segment_finalize_ms_p95": state.segment_finalize_ms_p95,
        "processing_ms_p50": state.processing_ms_p50,
        "processing_ms_p95": state.processing_ms_p95,
        "capture_jitter_ms_p50": state.capture_jitter_ms_p50,
        "capture_jitter_ms_p95": state.capture_jitter_ms_p95,
        "capture_lag_ms_p50": state.capture_lag_ms_p50,
        "capture_lag_ms_p95": state.capture_lag_ms_p95,
        "drop_rate": state.drop_rate,
        "session_resets": state.session_resets,
        "worker_rss_peak_mb": state.worker_rss_peak_mb,
        "sample_rate": state.target_sample_rate,
        "input_gain_db": state.input_gain_db,
        "queue_high_watermark_s": state.queue_high_watermark_s,
        "queue_low_watermark_s": state.queue_low_watermark_s,
        "queue_depth_s": state.queue_depth_s,
        "queue_depth_peak_s": state.queue_depth_peak_s,
        "backpressure_events": state.backpressure_events,
        "backpressure_active": state.backpressure_active,
        "chunk_count": state.chunk_count,
        "dropped_chunks": state.dropped_chunks,
        "elapsed_s": state.elapsed_s,
        "rtf": state.rtf,
        "total_audio_s": state.total_audio_s,
        "sequence_timing": dict(state.sequence_timing or {}),
        "orchestrator_mode": state.orchestrator_mode,
        "run_id": state.run_id,
        "sequence_id": state.sequence_id,
        "sequence_index": state.sequence_index,
        "sequence_total": state.sequence_total,
        "global_timeline_ms": orchestrator.get("global_timeline_ms"),
        "event_contract_schema": orchestrator.get("event_contract_schema"),
        "event_contract_version": orchestrator.get("event_contract_version"),
        "preflight_ok": state.preflight_ok,
        "preflight_errors": list(state.preflight_errors or []),
        "preflight_warnings": list(state.preflight_warnings or []),
        "reason_code": state.reason_code,
        "error": state.error,
        "final": dict(state.final) if isinstance(state.final, dict) else None,
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
    mic_service.log_transport_event(session_id, "ws_accept")

    state = mic_service.get_session(session_id)
    if state is None:
        mic_service.log_transport_event(session_id, "ws_session_not_found")
        await websocket.send_text(json.dumps({"error": "session not found"}))
        await websocket.close()
        return

    try:
        mic_service.start_recording(session_id)
        mic_service.log_transport_event(session_id, "ws_start_ok")
        await websocket.send_text(
            json.dumps(
                {
                    "type": "started",
                    "session_id": session_id,
                    "orchestrator_mode": state.orchestrator_mode,
                    "run_id": state.run_id,
                    "sequence_id": state.sequence_id,
                    "sequence_index": state.sequence_index,
                    "sequence_total": state.sequence_total,
                    "event_contract_version": mic_service.get_orchestrator_payload(state).get("event_contract_version"),
                }
            )
        )
    except Exception as exc:
        reason_code = mic_service.classify_error_reason(exc, fallback="start_recording_failed")
        mic_service.record_session_start_failure(
            session_id,
            error=str(exc),
            reason_code=reason_code,
        )
        mic_service.log_transport_event(
            session_id,
            "ws_start_failed",
            {"error": str(exc), "reason_code": reason_code},
        )
        await websocket.send_text(
            json.dumps(
                {
                    "error": f"Start session selhal: {exc}",
                    "reason_code": reason_code,
                    "session_id": session_id,
                    "model_id": state.model_id,
                }
            )
        )
        await websocket.close()
        return

    try:
        while True:
            # Přijmi zprávu — buď binary (PCM) nebo text (JSON control)
            message = await websocket.receive()

            if "bytes" in message and message["bytes"]:
                samples, capture_ts_ms = mic_service.decode_audio_frame(message["bytes"])
                if samples:
                    result = mic_service.process_audio_chunk(
                        session_id=session_id,
                        samples=samples,
                        capture_ts_ms=capture_ts_ms,
                    )
                    await websocket.send_text(json.dumps(result))

            elif "text" in message and message["text"]:
                try:
                    payload = json.loads(message["text"])
                except Exception:
                    payload = {}

                action = payload.get("action", "")
                if action == "stop":
                    stop_extra = {
                        str(key): value
                        for key, value in payload.items()
                        if key != "action" and not str(key).startswith("samples")
                    }
                    mic_service.update_session_runtime_metadata(session_id, stop_extra)
                    mic_service.log_transport_event(session_id, "ws_stop_action", stop_extra)
                    break

                # Alternativa: JSON {"samples": [...]}
                samples, capture_ts_ms = mic_service.decode_audio_frame(message["text"])
                if samples:
                    result = mic_service.process_audio_chunk(
                        session_id=session_id,
                        samples=samples,
                        capture_ts_ms=capture_ts_ms,
                    )
                    await websocket.send_text(json.dumps(result))

    except WebSocketDisconnect as exc:
        mic_service.log_transport_event(
            session_id,
            "ws_disconnect",
            {"close_code": getattr(exc, "code", None)},
        )
    except Exception as exc:
        mic_service.log_transport_event(session_id, "ws_loop_error", {"error": str(exc)})
        try:
            await websocket.send_text(json.dumps({"error": str(exc)}))
        except Exception:
            pass

    # Finalizuj
    try:
        final = mic_service.stop_recording(session_id)
        try:
            await websocket.send_text(json.dumps(final))
            mic_service.log_transport_event(
                session_id,
                "ws_final_sent",
                {
                    "reason_code": final.get("reason_code"),
                    "elapsed_s": final.get("elapsed_s"),
                    "drop_rate": final.get("drop_rate"),
                },
            )
        except Exception as exc:
            mic_service.log_transport_event(session_id, "ws_final_send_failed", {"error": str(exc)})
    except Exception as exc:
        mic_service.log_transport_event(session_id, "ws_finalize_failed", {"error": str(exc)})

    try:
        await websocket.close()
        mic_service.log_transport_event(session_id, "ws_closed")
    except Exception as exc:
        mic_service.log_transport_event(session_id, "ws_close_failed", {"error": str(exc)})
