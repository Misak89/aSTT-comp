"""
Source library management: CRUD, subtitle download, results storage.
"""
from __future__ import annotations
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..config import LIBRARY_ROOT, SUBTITLES_ROOT, RESULTS_ROOT
from ..models.library import LibraryItem, SubtitleFile, LatestResult, UpsertLibraryItemRequest

_ITEMS_FILE = LIBRARY_ROOT / "items.json"


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _load_raw() -> list[dict]:
    if not _ITEMS_FILE.exists():
        return []
    return json.loads(_ITEMS_FILE.read_text(encoding="utf-8"))


def _save_raw(items: list[dict]) -> None:
    _ITEMS_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_items() -> list[LibraryItem]:
    raw = _load_raw()
    result = []
    for r in raw:
        video_id = r.get("video_id", "")
        subtitle_files = _list_subtitle_files(video_id)
        result.append(LibraryItem(
            video_id=video_id,
            title=r.get("title", ""),
            url=r.get("url", ""),
            duration_seconds=r.get("duration_seconds"),
            language=r.get("language", "cs"),
            genre=r.get("genre"),
            subtitles_local=bool(subtitle_files),
            subtitle_files=subtitle_files,
            added_at=r.get("added_at"),
        ))
    return result


def upsert_item(req: UpsertLibraryItemRequest) -> LibraryItem:
    raw = _load_raw()
    existing = {r["video_id"]: r for r in raw}
    if req.video_id in existing:
        existing[req.video_id].update({
            "title": req.title,
            "url": req.url,
            "duration_seconds": req.duration_seconds,
            "language": req.language,
            "genre": req.genre,
        })
    else:
        existing[req.video_id] = {
            "video_id": req.video_id,
            "title": req.title,
            "url": req.url,
            "duration_seconds": req.duration_seconds,
            "language": req.language,
            "genre": req.genre,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
    _save_raw(list(existing.values()))
    return list_items()[list(existing.keys()).index(req.video_id)]


def download_subtitles(video_id: str, url: str) -> dict:
    out_dir = SUBTITLES_ROOT / video_id
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "yt_dlp",
                "--write-sub", "--write-auto-sub",
                "--sub-lang", "cs",
                "--sub-format", "vtt",
                "--skip-download",
                "--output", str(out_dir / "%(id)s.%(ext)s"),
                url,
            ],
            capture_output=True, text=True, timeout=120,
        )
        files = _list_subtitle_files(video_id)
        return {"ok": True, "files": [f.model_dump() for f in files], "stderr": result.stderr[-500:]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def read_subtitle_file(video_id: str, filename: str) -> Optional[str]:
    # Path traversal protection
    safe_name = Path(filename).name
    path = SUBTITLES_ROOT / video_id / safe_name
    if not path.exists() or path.suffix not in (".vtt", ".md", ".srt", ".txt"):
        return None
    return path.read_text(encoding="utf-8")


def get_latest_results(video_id: str) -> list[LatestResult]:
    results_dir = RESULTS_ROOT / video_id
    if not results_dir.exists():
        return []
    out = []
    for combo_dir in sorted(results_dir.iterdir()):
        if not combo_dir.is_dir():
            continue
        meta_file = combo_dir / "latest_meta.json"
        if not meta_file.exists():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            parts = combo_dir.name.split("__", 1)
            out.append(LatestResult(
                model_id=parts[0] if len(parts) > 0 else combo_dir.name,
                setting_id=parts[1] if len(parts) > 1 else "",
                wer=meta.get("wer"),
                cer=meta.get("cer"),
                run_id=meta.get("run_id", ""),
                timestamp=meta.get("timestamp", ""),
                transcript_snippet=meta.get("transcript_snippet"),
            ))
        except Exception:
            continue
    return sorted(out, key=lambda r: r.timestamp, reverse=True)


def save_run_results(matrix_payload: dict) -> None:
    """Auto-save per-video transcripts and metrics after a benchmark run."""
    run_id = matrix_payload.get("run_id", "")
    for result in matrix_payload.get("results", []):
        model_id = result.get("model_id", "")
        setting_id = result.get("setting_id", "")
        for sm in result.get("source_metrics", []):
            video_id = sm.get("video_id") or ""
            if not video_id:
                continue
            transcript = sm.get("transcript") or ""
            combo_dir = RESULTS_ROOT / video_id / f"{model_id}__{setting_id}"
            history_dir = combo_dir / "history"
            history_dir.mkdir(parents=True, exist_ok=True)

            (combo_dir / "latest.txt").write_text(transcript, encoding="utf-8")
            meta = {
                "run_id": run_id,
                "wer": sm.get("wer"),
                "cer": sm.get("cer"),
                "model_runtime_config": sm.get("model_runtime_config"),
                "clip_start_seconds": sm.get("clip_start_seconds"),
                "clip_seconds": sm.get("clip_seconds"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "transcript_snippet": transcript[:200] if transcript else None,
            }
            (combo_dir / "latest_meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (history_dir / f"{run_id}.txt").write_text(transcript, encoding="utf-8")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _list_subtitle_files(video_id: str) -> list[SubtitleFile]:
    sub_dir = SUBTITLES_ROOT / video_id
    if not sub_dir.exists():
        return []
    files = []
    for f in sorted(sub_dir.iterdir()):
        if f.is_file() and f.suffix in (".vtt", ".srt", ".md", ".txt"):
            files.append(SubtitleFile(filename=f.name, size_bytes=f.stat().st_size, ext=f.suffix.lstrip(".")))
    return files
