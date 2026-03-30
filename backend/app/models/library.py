from __future__ import annotations
from pydantic import BaseModel
from typing import Optional
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
