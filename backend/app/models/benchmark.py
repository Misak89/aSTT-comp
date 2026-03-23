from __future__ import annotations
from pydantic import BaseModel, Field, model_validator
from typing import Optional, Literal


class BenchmarkJobRequest(BaseModel):
    # Videa lze zadat buď přes video_ids z knihovny, nebo přímo přes URLs/cesty
    video_ids: Optional[list[str]] = Field(default=None, description="Video IDs z knihovny")
    sources: Optional[list[str]] = Field(default=None, description="Přímé YouTube URLs nebo lokální cesty")

    model_ids: Optional[list[str]] = None      # None = all default models
    setting_ids: Optional[list[str]] = None    # None = all default settings
    sample_seconds: int = 120                  # default 120s dle specifikace
    chunk_seconds: int = 15                    # délka jednoho chunku v streaming módu
    evaluation_mode: Literal["real", "synthetic", "streaming"] = "synthetic"
    clip_strategy: Literal["random", "uniform"] = "random"
    clip_seed: Optional[int] = None
    label: Optional[str] = None
    scenario_id: Optional[str] = None         # reference na uložený scénář
    # Per-model parametry: {"whisper_cpp_small": {"language": "cs", "threads": 8}, ...}
    # nebo společné: {"language": "cs"} — aplikuje se na všechny modely
    model_params: Optional[dict] = Field(default=None, description="Per-model nebo sdílené parametry")

    @model_validator(mode="after")
    def check_sources(self) -> "BenchmarkJobRequest":
        if not self.video_ids and not self.sources:
            raise ValueError("Zadej buď video_ids (z knihovny) nebo sources (přímé URLs)")
        return self


class BenchmarkJobStatus(BaseModel):
    job_id: str
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    label: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    progress_message: Optional[str] = None
    progress_percent: int = 0              # % průběhu pro tabulku jobů
    error: Optional[str] = None
    run_id: Optional[str] = None
    result_url: Optional[str] = None
    conditions_clean: Optional[bool] = None   # HW podmínky byly čisté?
    pre_cpu: Optional[float] = None           # CPU% systému před startem
    pre_ram_mb: Optional[float] = None        # RAM MB systému před startem
    video_ids: Optional[list[str]] = None     # videa z requestu (pro live embed)
    evaluation_mode: Optional[str] = None     # synthetic / streaming / real


class Scenario(BaseModel):
    scenario_id: str
    description: Optional[str] = None
    clip_seconds: int = 120
    clip_seed: Optional[int] = None
    model_ids: list[str] = []
    setting_ids: list[str] = []
    video_ids: list[str] = []
    created_at: Optional[str] = None


class JobListResponse(BaseModel):
    jobs: list[BenchmarkJobStatus]


class LiveJobProgress(BaseModel):
    """Live data z running jobu: progress + HW série pro grafy."""
    job_id: str
    status: str
    percent: int = 0
    message: str = ""
    message_log: list[str] = []        # Req 1: historie všech zpráv (nemaže se)
    updated_at: Optional[str] = None
    hw_series: list[dict] = []         # Série HW vzorků — posledních 120 (60s)
    transcript: str = ""               # Req 2: poslední transkript (live nebo finální)
    transcript_ts: str = ""            # Transkript s časovými značkami [MM:SS] z whisper segmentů
    pre_cpu: Optional[float] = None    # Req 3: CPU% před spuštěním benchmarku
    pre_ram_mb: Optional[float] = None # Req 3: RAM MB před spuštěním benchmarku
    model_params_used: dict = {}       # Req 4: přesné nastavení modelu
