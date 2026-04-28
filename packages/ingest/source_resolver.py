from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Iterable
from urllib.parse import unquote, urlparse

_MARKDOWN_LINK = re.compile(r"\[(?P<label>[^\]]+)\]\((?P<url>https?://[^)]+)\)")
_URL = re.compile(r"https?://[^\s)\]]+")
_MEDIA_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".wav",
    ".webm",
}


@dataclass(frozen=True)
class SourceEntry:
    source_id: str
    label: str
    origin_type: str
    value: str
    exists: bool
    error: str | None = None
    canonical_url: str | None = None
    video_id: str | None = None

    def to_record(self) -> dict[str, object]:
        return asdict(self)


def canonicalize_online_source_url(url: str) -> tuple[str, str | None]:
    value = str(url or "").strip()
    if not value:
        return "", None

    video_id = extract_youtube_video_id(value)
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}", video_id
    return value, None


def local_path_from_file_url(value: str) -> Path | None:
    """Convert a file:// URL to a local Path without dropping the Windows drive slash."""
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme.lower() != "file":
        return None

    path_value = unquote(parsed.path or "")
    if parsed.netloc and parsed.netloc.lower() not in ("", "localhost"):
        path_value = f"//{parsed.netloc}{path_value}"

    # urlparse("file:///C:/x") gives "/C:/x". Path("/C:/x") is invalid
    # for Windows file access, so keep the drive marker as "C:/x".
    if re.match(r"^/[A-Za-z]:", path_value):
        path_value = path_value[1:]

    return Path(path_value)


def extract_youtube_video_id(value: str) -> str | None:
    if not value:
        return None
    try:
        parsed = urlparse(str(value).strip())
        host = (parsed.hostname or "").lower()
        candidate: str | None = None
        if "youtu.be" in host:
            candidate = parsed.path.split("/")[1] if len(parsed.path.split("/")) > 1 else None
        elif "youtube.com" in host:
            if parsed.path == "/watch":
                query = parsed.query or ""
                for part in query.split("&"):
                    if part.startswith("v="):
                        candidate = part[2:]
                        break
            elif parsed.path.startswith("/embed/") or parsed.path.startswith("/shorts/"):
                parts = [item for item in parsed.path.split("/") if item]
                candidate = parts[1] if len(parts) > 1 else None
        if not candidate:
            return None
        return candidate if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", candidate) else None
    except Exception:
        return None


def parse_source_entries(
    lines: Iterable[str],
    *,
    max_sources: int,
    base_dir: str | Path | None = None,
) -> list[SourceEntry]:
    """Parse mixed input lines into online URLs or local file references."""
    entries: list[SourceEntry] = []
    base_path = Path(base_dir).resolve() if base_dir else None

    for raw_line in lines:
        if len(entries) >= max_sources:
            break

        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if set(line) == {"-"}:
            continue

        entry = _line_to_entry(line, source_index=len(entries) + 1, base_path=base_path)
        if entry:
            entries.append(entry)

    return entries


def parse_source_text(
    source_text: str,
    *,
    max_sources: int,
    base_dir: str | Path | None = None,
) -> list[SourceEntry]:
    return parse_source_entries(
        source_text.splitlines(),
        max_sources=max_sources,
        base_dir=base_dir,
    )


def load_source_file(
    path: str | Path,
    *,
    max_sources: int,
    base_dir: str | Path | None = None,
) -> list[SourceEntry]:
    source_path = Path(path)
    text = source_path.read_text(encoding="utf-8")
    effective_base = base_dir if base_dir else source_path.parent
    return parse_source_text(text, max_sources=max_sources, base_dir=effective_base)


def load_source_directory(
    path: str | Path,
    *,
    max_sources: int,
    recursive: bool = False,
) -> list[SourceEntry]:
    directory = Path(path).resolve()
    if not directory.exists():
        raise FileNotFoundError(f"Source directory not found: {directory}")
    if not directory.is_dir():
        raise ValueError(f"Source directory is not a directory: {directory}")

    files = _list_media_files(directory, recursive=recursive)
    selected = files[:max_sources]

    entries: list[SourceEntry] = []
    for index, file_path in enumerate(selected, start=1):
        entries.append(
            SourceEntry(
                source_id=f"src-{index:03d}",
                label=file_path.name,
                origin_type="local_file",
                value=str(file_path),
                exists=True,
            )
        )
    return entries


def _list_media_files(directory: Path, *, recursive: bool) -> list[Path]:
    iterator = directory.rglob("*") if recursive else directory.iterdir()
    media_files = [path.resolve() for path in iterator if path.is_file() and path.suffix.lower() in _MEDIA_EXTENSIONS]
    media_files.sort(key=lambda path: str(path).lower())
    return media_files


def _line_to_entry(line: str, *, source_index: int, base_path: Path | None) -> SourceEntry | None:
    markdown_match = _MARKDOWN_LINK.search(line)
    if markdown_match:
        label = markdown_match.group("label").strip()
        url = markdown_match.group("url").strip()
        canonical_url, video_id = canonicalize_online_source_url(url)
        return SourceEntry(
            source_id=f"src-{source_index:03d}",
            label=label,
            origin_type="online_url",
            value=url,
            exists=True,
            canonical_url=canonical_url,
            video_id=video_id,
        )

    url_match = _URL.search(line)
    if url_match:
        url = url_match.group(0).strip()
        prefix = line[: url_match.start()].strip(" -:\t")
        label = prefix if prefix else (urlparse(url).netloc or url)
        canonical_url, video_id = canonicalize_online_source_url(url)
        return SourceEntry(
            source_id=f"src-{source_index:03d}",
            label=label,
            origin_type="online_url",
            value=url,
            exists=True,
            canonical_url=canonical_url,
            video_id=video_id,
        )

    file_url_path = local_path_from_file_url(line)
    if file_url_path is not None:
        resolved = file_url_path.resolve() if file_url_path.exists() else file_url_path
        exists = resolved.exists()
        return SourceEntry(
            source_id=f"src-{source_index:03d}",
            label=resolved.name,
            origin_type="local_file",
            value=str(resolved),
            exists=exists,
            error=None if exists else "File does not exist",
        )

    raw_path = Path(line.strip('"'))
    candidate = raw_path
    if not raw_path.is_absolute() and base_path:
        candidate = base_path / raw_path

    resolved = candidate.resolve()
    exists = resolved.exists()
    return SourceEntry(
        source_id=f"src-{source_index:03d}",
        label=resolved.name,
        origin_type="local_file",
        value=str(resolved),
        exists=exists,
        error=None if exists else "File does not exist",
    )
