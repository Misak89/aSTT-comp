"""Pydantic modely pro Tuning joby."""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class TuningParamSpace(BaseModel):
    """Definice prostoru pro jeden parametr."""
    name: str
    values: list  # seznam hodnot k vyzkoušení


class TuningJobRequest(BaseModel):
    model_id: str
    video_ids: list[str]
    sample_seconds: int = 60
    clip_seed: Optional[int] = None
    strategy: str = "grid"          # grid | random | ablation
    max_trials: int = 20            # max pro random search
    param_space: list[TuningParamSpace]  # které params a jaké hodnoty
    baseline_params: dict = {}      # výchozí hodnoty (zbytek fixní)
    label: Optional[str] = None


class TuningTrialResult(BaseModel):
    trial_idx: int
    params: dict                    # kombinace parametrů tohoto trialu
    chunk_seconds: int
    wer: Optional[float]
    cer: Optional[float]
    wer_normalized: Optional[float]
    mer: Optional[float]
    wil: Optional[float]
    rtf: Optional[float]
    latency_ms: Optional[float]
    error: Optional[str]
    is_pareto: bool = False         # vypočítáno na frontendu / při GET


class TuningJobStatus(BaseModel):
    job_id: str
    status: str                     # pending | running | completed | failed
    model_id: str
    label: Optional[str]
    created_at: str
    total_trials: int
    completed_trials: int
    results: list[TuningTrialResult] = []
    error: Optional[str] = None
    best_trial_idx: Optional[int] = None
