from __future__ import annotations
from pydantic import BaseModel
from typing import Optional


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
    subtitles_local: bool = False
    subtitle_files: list[SubtitleFile] = []
    added_at: Optional[str] = None


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
