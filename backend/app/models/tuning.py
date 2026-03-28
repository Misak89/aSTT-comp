"""Pydantic modely pro Tuning joby."""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class TuningParamSpace(BaseModel):
    """Definice prostoru pro jeden parametr."""
    name: str
    values: list  # seznam hodnot k vyzkoušení


class TuningMicProtocol(BaseModel):
    """Povinný protokol pro real mic režim."""
    distance_cm: float = Field(ge=1, le=300)
    phone_volume_pct: float = Field(ge=0, le=100)
    input_gain_pct: float = Field(ge=0, le=100)
    environment: str = "quiet"  # quiet | office_noise
    device_note: Optional[str] = None


class TuningMicCalibration(BaseModel):
    """Výsledek kalibrace zvuku před real mic během."""
    rms_dbfs: float
    clipping_rate_pct: float = Field(ge=0, le=100)
    noise_floor_dbfs: float
    passed: bool = False
    checked_at: Optional[str] = None
    reasons: list[str] = Field(default_factory=list)


class TuningMicCalibrationCheckRequest(BaseModel):
    rms_dbfs: float
    clipping_rate_pct: float = Field(ge=0, le=100)
    noise_floor_dbfs: float


class TuningMicCalibrationCheckResponse(BaseModel):
    passed: bool
    reasons: list[str] = Field(default_factory=list)
    thresholds: dict = Field(default_factory=dict)
    metrics: dict = Field(default_factory=dict)


class TuningJobRequest(BaseModel):
    model_id: str = ""              # backward compat — použij model_ids místo toho
    model_ids: list[str] = Field(default_factory=list)       # více modelů najednou (nové pole)
    input_mode: str = "replay"      # replay | real_mic
    video_ids: list[str]
    sample_seconds: int = 60
    clip_seed: Optional[int] = None
    random_seed: Optional[int] = 42      # seed pro random strategy (reprodukovatelnost pořadí trialů)
    clip_start_seconds: Optional[int] = None  # pevný start pro všechna videa (přepíše clip_seed)
    evaluation_mode: str = "heuristic"        # heuristic | heuristic+llm
    strategy: str = "grid"          # grid | random | ablation
    max_trials: int = 20            # max pro random search
    repeat_top_k: int = 0           # po 1. vlně zopakuj top-K kandidátů
    repeat_runs: int = 1            # celkový počet běhů per kandidát (>=1)
    hardware_profile: Optional[str] = None   # weak_office | mid_office | strong_office | custom
    hardware_note: Optional[str] = None
    constraints_profile: Optional[str] = None      # none | weak_cap | mid_cap | custom
    constraints_cpu_cores: Optional[int] = Field(default=None, ge=1, le=128)
    constraints_ram_limit_mb: Optional[int] = Field(default=None, ge=256, le=262144)
    constraints_priority: Optional[str] = None     # normal | below_normal | idle
    load_profile: Optional[str] = None       # none | light | medium | heavy | custom
    load_cpu_target_pct: Optional[float] = Field(default=None, ge=0, le=95)
    load_ram_target_pct: Optional[float] = Field(default=None, ge=0, le=95)
    param_space: list[TuningParamSpace]  # které params a jaké hodnoty
    baseline_params: dict = Field(default_factory=dict)      # výchozí hodnoty (zbytek fixní)
    label: Optional[str] = None
    validate_beam_preflight: bool = True
    mic_protocol: Optional[TuningMicProtocol] = None
    mic_calibration: Optional[TuningMicCalibration] = None
    mic_device: Optional[int | str] = None
    mic_chunk_seconds: Optional[float] = Field(default=None, ge=0.05, le=2.0)
    mic_prepare_seconds: Optional[int] = Field(default=None, ge=0, le=60)


class TuningTrialResult(BaseModel):
    trial_idx: int
    model_id: Optional[str] = None  # model použitý pro tento trial
    params: dict                    # kombinace parametrů tohoto trialu
    chunk_seconds: int
    wer: Optional[float]
    cer: Optional[float]
    wer_normalized: Optional[float]
    wer_soft: Optional[float] = None    # WER ignorující drobné záměny (char edit dist < 0.40)
    wer_llm: Optional[float] = None     # LLM hodnocení — zatím vždy None (vyžaduje Ollamu)
    mer: Optional[float]
    wil: Optional[float]
    rtf: Optional[float]
    rtf_p50: Optional[float] = None
    rtf_p95: Optional[float] = None
    latency_ms: Optional[float]
    latency_p50_ms: Optional[float] = None
    latency_p95_ms: Optional[float] = None
    latency_quality: Optional[str] = None  # measured_live | probe_online | proxy_offline | mixed | unknown
    first_token_ms_p50: Optional[float] = None
    first_token_ms_p95: Optional[float] = None
    segment_finalize_ms_p50: Optional[float] = None
    segment_finalize_ms_p95: Optional[float] = None
    drop_rate: Optional[float] = None
    session_resets: Optional[int] = None
    reason_code: Optional[str] = None
    ram_mb: Optional[float] = None         # průměrná RAM model procesu přes videa (MB)
    ram_peak_mb: Optional[float] = None    # max RAM model procesu přes videa (MB)
    ram_p95_mb: Optional[float] = None
    worker_rss_before_mb: Optional[float] = None  # RSS workeru před trialem (MB)
    worker_rss_after_mb: Optional[float] = None   # RSS workeru po trialu (MB)
    worker_rss_peak_mb: Optional[float] = None    # max RSS workeru během trialu (MB)
    load_profile: Optional[str] = None
    load_cpu_target_pct: Optional[float] = None
    load_ram_target_pct: Optional[float] = None
    load_cpu_actual_avg_pct: Optional[float] = None
    load_cpu_actual_p95_pct: Optional[float] = None
    load_ram_actual_avg_pct: Optional[float] = None
    load_ram_actual_p95_pct: Optional[float] = None
    load_samples: Optional[int] = None
    load_control_ok: Optional[bool] = None
    constraints_profile: Optional[str] = None
    constraints_cpu_cores: Optional[int] = None
    constraints_ram_limit_mb: Optional[int] = None
    constraints_priority: Optional[str] = None
    constraints_applied: Optional[bool] = None
    constraints_warnings: Optional[list[str]] = None
    constraints_ram_mode: Optional[str] = None   # hard | soft | none
    constraints_cpu_applied: Optional[bool] = None
    constraints_priority_applied: Optional[bool] = None
    constraints_ram_hard_cap_applied: Optional[bool] = None
    constraints_ram_hard_cap_error: Optional[str] = None
    error: Optional[str]
    is_pareto: bool = False         # vypočítáno na frontendu / při GET
    rtf_viable: bool = False        # RTF < 1.0 = použitelné pro live mikrofon
    perceived_delay_s: Optional[float] = None
    perceived_delay_method: Optional[str] = None
    perceived_delay_quality: Optional[str] = None  # high | medium | low | unknown
    source_success_count: Optional[int] = None
    source_error_count: Optional[int] = None
    success_rate: Optional[float] = None
    resource_metrics_available: Optional[bool] = None
    is_repeat: bool = False
    repeat_of_trial_idx: Optional[int] = None
    repeat_no: int = 1
    repeat_group_key: Optional[str] = None
    trial_finished_at: Optional[str] = None
    transcript: Optional[str] = None
    reference_text: Optional[str] = None
    elapsed_s: Optional[float] = None
    total_audio_s: Optional[float] = None
    word_count: Optional[int] = None
    word_diff: Optional[list] = None   # [{op, ref, hyp}] — barevný diff
    chunk_metrics: Optional[list] = None  # per-chunk RTF/timing
    source_metrics: Optional[list] = None  # per-video metriky


class TuningJobStatus(BaseModel):
    job_id: str
    status: str                     # pending | running | completed | failed
    model_id: str = ""              # backward compat — první z model_ids
    model_ids: list[str] = Field(default_factory=list)       # všechny modely v tomto jobu
    input_mode: str = "replay"
    label: Optional[str]
    hardware_profile: Optional[str] = None
    hardware_note: Optional[str] = None
    hardware_info: dict = Field(default_factory=dict)
    constraints_profile: Optional[str] = None
    constraints_cpu_cores: Optional[int] = None
    constraints_ram_limit_mb: Optional[int] = None
    constraints_priority: Optional[str] = None
    constraints_applied: Optional[bool] = None
    constraints_warnings: list[str] = Field(default_factory=list)
    constraints_ram_mode: Optional[str] = None
    constraints_cpu_applied: Optional[bool] = None
    constraints_priority_applied: Optional[bool] = None
    constraints_ram_hard_cap_applied: Optional[bool] = None
    constraints_ram_hard_cap_error: Optional[str] = None
    load_profile: Optional[str] = None
    load_cpu_target_pct: Optional[float] = None
    load_ram_target_pct: Optional[float] = None
    created_at: str
    validate_beam_preflight: bool = True
    mic_protocol: Optional[TuningMicProtocol] = None
    mic_calibration: Optional[TuningMicCalibration] = None
    mic_device: Optional[int | str] = None
    mic_chunk_seconds: Optional[float] = None
    mic_prepare_seconds: Optional[int] = None
    total_trials: int
    completed_trials: int
    results: list[TuningTrialResult] = Field(default_factory=list)
    error: Optional[str] = None
    best_trial_idx: Optional[int] = None
    progress_message: Optional[str] = None
    audio_ready: list[str] = Field(default_factory=list)       # video_ids pro která je audio staženo
    updated_ts: Optional[str] = None  # timestamp poslední aktualizace workeru (pro detekci zaseknutí)
    reproducibility: list[dict] = Field(default_factory=list)  # agregace repeat skupin (mean/std/ci95)
    repro_validation: dict = Field(default_factory=dict)
