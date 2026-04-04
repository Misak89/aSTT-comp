from __future__ import annotations
from pydantic import BaseModel
from typing import Literal, Optional
from pydantic import Field


class SubtitleFile(BaseModel):
    filename: str
    size_bytes: int
    ext: str  # "vtt" | "md" | ...


class LibraryItem(BaseModel):
    video_id: str
    title: str
    url: str
    duration_seconds: Optional[float] = None
    language: str = "cs"
    genre: Optional[str] = None
    visible_in_menus: bool = True
    subtitles_local: bool = False
    subtitle_files: list[SubtitleFile] = Field(default_factory=list)
    subtitle_manual: list[str] = Field(default_factory=list)
    subtitle_auto: list[str] = Field(default_factory=list)
    subtitle_languages: list[str] = Field(default_factory=list)
    added_at: Optional[str] = None
    upload_date: Optional[str] = None  # datum vydání na YouTube (YYYY-MM-DD)
    view_count: Optional[int] = None
    metadata_fetched_at: Optional[str] = None  # datum kdy jsme metadata naposledy načetli
    audio_cached: bool = False  # True pokud je full WAV v runtime/audio_cache/
    audio_size_bytes: Optional[int] = None   # velikost WAV souboru v bajtech
    audio_duration_seconds: Optional[float] = None  # délka WAV v sekundách


class LatestResult(BaseModel):
    model_id: str
    setting_id: str
    wer: Optional[float] = None
    cer: Optional[float] = None
    run_id: str
    timestamp: str
    transcript_snippet: Optional[str] = None


class DownloadSubtitlesRequest(BaseModel):
    video_id: str
    url: str


class UpsertLibraryItemRequest(BaseModel):
    video_id: str
    title: str
    url: str
    duration_seconds: Optional[float] = None
    language: str = "cs"
    genre: Optional[str] = None
    visible_in_menus: Optional[bool] = None


class UpdateLibraryVisibilityRequest(BaseModel):
    visible_in_menus: bool


class ScanDirectoryRequest(BaseModel):
    path: str


class LocalFileEntry(BaseModel):
    path: str
    filename: str
    size_bytes: int
    duration_seconds: Optional[float] = None
    ext: str


class ImportLocalFileRequest(BaseModel):
    path: str
    title: str
    language: str = "cs"


class SegmentItem(BaseModel):
    idx: int
    start_s: float
    end_s: float
    duration_s: float
    snapped: bool = False
    snapped_from_s: Optional[float] = None
    snap_delta_ms: Optional[float] = None
    snap_reason: Optional[str] = None


class SegmentBundle(BaseModel):
    source_id: str
    source_type: Literal["library_item", "upload"] = "library_item"
    mode: Literal["preset", "manual"]
    audio_duration_seconds: float
    tolerance_seconds: float = 2.0
    preset_minutes: Optional[int] = None
    points_seconds: list[float] = Field(default_factory=list)
    segments: list[SegmentItem] = Field(default_factory=list)
    created_at: str
    updated_at: str


class SegmentBundlePreviewRequest(BaseModel):
    source_id: str
    source_type: Literal["library_item", "upload"] = "library_item"
    mode: Literal["preset", "manual"] = "preset"
    audio_duration_seconds: float = Field(gt=0.0)
    tolerance_seconds: float = Field(default=2.0, ge=0.0, le=30.0)
    pause_aware: bool = True
    pause_silence_dbfs: float = Field(default=-40.0, ge=-90.0, le=-5.0)
    pause_min_silence_ms: int = Field(default=250, ge=50, le=5000)
    preset_minutes: Optional[int] = None
    manual_points_seconds: list[float] = Field(default_factory=list)
