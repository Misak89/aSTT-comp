from __future__ import annotations
from pydantic import BaseModel
from typing import Optional, Any


class AggregateMetrics(BaseModel):
    score: Optional[float] = None
    wer: Optional[float] = None
    cer: Optional[float] = None
    latency_ms: Optional[float] = None
    rtf: Optional[float] = None
    cpu_percent: Optional[float] = None
    ram_mb: Optional[float] = None


class SourceMetric(BaseModel):
    video_id: Optional[str] = None
    canonical_url: Optional[str] = None
    clip_start_seconds: Optional[float] = None
    clip_seconds: Optional[float] = None
    transcript: Optional[str] = None
    reference_text: Optional[str] = None
    wer: Optional[float] = None
    cer: Optional[float] = None
    latency_ms: Optional[float] = None
    rtf: Optional[float] = None
    engine_elapsed_seconds: Optional[float] = None  # Req 7: doba přepisu
    model_runtime_config: Optional[dict[str, Any]] = None


class RunResult(BaseModel):
    model_id: str
    model_label: str
    setting_id: str
    setting_label: str
    aggregate: AggregateMetrics
    source_metrics: list[SourceMetric] = []
    model_runtime_config: Optional[dict[str, Any]] = None


class RunSummary(BaseModel):
    run_id: str
    created_at_utc: str
    evaluation_mode: str
    sample_seconds: int
    source_count: int
    result_count: int
    label: Optional[str] = None


class RunDetail(BaseModel):
    run_id: str
    created_at_utc: str
    evaluation_mode: str
    sample_seconds: int
    sources: list[dict[str, Any]] = []
    results: list[RunResult] = []
    host_telemetry_summary: Optional[dict[str, Any]] = None
