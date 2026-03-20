"""
Extract reference text from VTT subtitle files for a given time window.
Used as WER ground-truth fallback when no reference manifest is configured.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Optional

# Matches "HH:MM:SS.mmm --> HH:MM:SS.mmm" or "MM:SS.mmm --> MM:SS.mmm"
_TS_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})"
    r"\s*-->\s*"
    r"(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})"
)
_TAG_RE = re.compile(r"<[^>]+>")


def _ts_to_ms(ts: str) -> int:
    ts = ts.replace(",", ".")
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3_600_000 + int(m) * 60_000 + int(float(s) * 1000)
    elif len(parts) == 2:
        m, s = parts
        return int(m) * 60_000 + int(float(s) * 1000)
    return 0


def _pick_preferred_vtt(vtt_files: list[Path]) -> Path:
    """Prefer manually-written subtitles over auto-generated ones."""
    manual = [f for f in vtt_files if ".auto." not in f.name and "-auto." not in f.name]
    return (manual or vtt_files)[0]


def extract_vtt_clip_text(
    video_id: str,
    clip_start_s: float,
    clip_end_s: float,
    subtitles_root: Path,
) -> Optional[str]:
    """
    Return concatenated subtitle text for [clip_start_s, clip_end_s].
    Returns None if no VTT file exists for the video.
    """
    sub_dir = subtitles_root / video_id
    if not sub_dir.exists():
        return None
    vtt_files = [f for f in sub_dir.glob("*.vtt") if f.is_file()]
    if not vtt_files:
        return None

    vtt_path = _pick_preferred_vtt(vtt_files)
    clip_start_ms = int(clip_start_s * 1000)
    clip_end_ms = int(clip_end_s * 1000)

    lines = vtt_path.read_text(encoding="utf-8").splitlines()
    texts: list[str] = []
    i = 0
    while i < len(lines):
        m = _TS_RE.match(lines[i].strip())
        if m:
            cue_start_ms = _ts_to_ms(m.group(1))
            cue_end_ms = _ts_to_ms(m.group(2))
            # Overlap: cue starts before clip ends AND cue ends after clip starts
            if cue_start_ms < clip_end_ms and cue_end_ms > clip_start_ms:
                i += 1
                while i < len(lines) and lines[i].strip():
                    text = _TAG_RE.sub("", lines[i]).strip()
                    if text:
                        texts.append(text)
                    i += 1
                continue
        i += 1

    if not texts:
        return None
    # Deduplicate consecutive identical lines (common in auto-subs)
    deduped: list[str] = []
    for t in texts:
        if not deduped or t != deduped[-1]:
            deduped.append(t)
    return " ".join(deduped)
