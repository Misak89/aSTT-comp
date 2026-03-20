from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class OnlineSource:
    source_id: str
    url: str
    label: str


@dataclass(frozen=True)
class FetchResult:
    source_id: str
    url: str
    label: str
    status: str
    requested_clip_seconds: int
    local_path: str | None = None
    subtitle_path: str | None = None
    title: str | None = None
    duration_seconds: float | None = None
    error: str | None = None
    canonical_url: str | None = None
    video_id: str | None = None

    def to_record(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class StreamResolveResult:
    source_id: str
    url: str
    label: str
    status: str
    stream_url: str | None = None
    title: str | None = None
    duration_seconds: float | None = None
    error: str | None = None
    canonical_url: str | None = None
    video_id: str | None = None

    def to_record(self) -> dict[str, object]:
        return asdict(self)
