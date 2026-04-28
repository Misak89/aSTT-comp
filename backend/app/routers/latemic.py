from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import latemic_service

router = APIRouter(prefix="/api/latemic", tags=["latemic"])


class LateMicSegmentRequest(BaseModel):
    model_id: str = Field(min_length=1)
    model_params: dict[str, Any] = Field(default_factory=dict)
    pcm16_base64: str = Field(min_length=1)
    sample_rate: int = Field(default=16000, ge=8000, le=48000)
    lag_budget_s: float | None = Field(default=None, ge=0)
    target_segment_s: float | None = Field(default=None, ge=0)
    queue_wait_s: float | None = Field(default=None, ge=0)
    segment_index: int | None = Field(default=None, ge=0)
    delete_policy: str = Field(default="after_transcript")
    client_meta: dict[str, Any] = Field(default_factory=dict)


class LateMicSegmentResponse(BaseModel):
    segment_id: str
    created_at: str
    finished_at: str
    status: str
    error: str | None = None
    model_id: str
    model_params_used: dict[str, Any] = Field(default_factory=dict)
    transcript: str
    transcript_source: str
    audio_authority: str
    reference_text_used: bool
    history_fallback_used: bool
    audio_sha256: str
    audio_payload_bytes: int
    audio_duration_s: float
    sample_rate: int
    lag_budget_s: float | None = None
    target_segment_s: float | None = None
    queue_wait_s: float = 0.0
    decode_s: float
    tail_lag_s: float
    max_visible_lag_s: float
    over_budget_s: float | None = None
    rtf: float | None = None
    latency_ms: float | None = None
    wav_deleted: bool
    wav_retained: bool


class LateMicDeleteWavResponse(BaseModel):
    segment_id: str
    wav_existed: bool
    wav_deleted: bool
    wav_retained: bool


@router.post("/segments", response_model=LateMicSegmentResponse, status_code=201)
def create_segment(req: LateMicSegmentRequest):
    try:
        result = latemic_service.create_segment_transcription(
            model_id=req.model_id,
            model_params=req.model_params,
            pcm16_base64=req.pcm16_base64,
            sample_rate=req.sample_rate,
            lag_budget_s=req.lag_budget_s,
            target_segment_s=req.target_segment_s,
            queue_wait_s=req.queue_wait_s,
            segment_index=req.segment_index,
            delete_policy=req.delete_policy,
            client_meta=req.client_meta,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return LateMicSegmentResponse(**result)


@router.get("/segments/{segment_id}", response_model=LateMicSegmentResponse)
def get_segment(segment_id: str):
    try:
        result = latemic_service.get_segment_result(segment_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"LateMic segment nenalezen: {segment_id}") from exc
    return LateMicSegmentResponse(**result)


@router.delete("/segments/{segment_id}/wav", response_model=LateMicDeleteWavResponse)
def delete_segment_wav(segment_id: str):
    try:
        result = latemic_service.delete_segment_wav(segment_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"LateMic segment nenalezen: {segment_id}") from exc
    return LateMicDeleteWavResponse(**result)
