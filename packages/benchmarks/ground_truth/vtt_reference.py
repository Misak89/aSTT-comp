"""
Extract reference text from VTT subtitle files for a given time window.
Used as WER ground-truth fallback when no reference manifest is configured.
"""
from __future__ import annotations
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

# Matches "HH:MM:SS.mmm --> HH:MM:SS.mmm" or "MM:SS.mmm --> MM:SS.mmm"
_TS_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})"
    r"\s*-->\s*"
    r"(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})"
)
_TAG_RE = re.compile(r"<[^>]+>")
_WORD_RE = re.compile(r"\w+", flags=re.UNICODE)
_TOKEN_SPLIT_RE = re.compile(r"[.\-_\s]+")


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


def _stem_has_token(path: Path, token: str) -> bool:
    parts = [p for p in _TOKEN_SPLIT_RE.split(path.stem.lower()) if p]
    return token.lower() in parts


def _pick_preferred_vtt(vtt_files: list[Path]) -> Path:
    """Prefer edit/manual subtitles over auto-generated ones."""
    sorted_files = sorted(vtt_files, key=lambda p: p.name.lower())
    edits = [f for f in sorted_files if _stem_has_token(f, "edit")]
    manual = [f for f in sorted_files if not _stem_has_token(f, "auto")]
    return (edits or manual or sorted_files)[0]


def _is_edit_md(path: Path) -> bool:
    return _stem_has_token(path, "edit")


def _extract_md_body_text(md_path: Path) -> str:
    lines: list[str] = []
    for raw_line in md_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower = line.lower()
        if line.startswith("#"):
            continue
        if lower.startswith("url:") or lower.startswith("published:") or lower.startswith("kind:"):
            continue
        lines.append(line)
    return " ".join(lines).strip()


def _normalize_words(text: str) -> list[str]:
    return [token.lower() for token in _WORD_RE.findall(text)]


def _build_edit_phrase_replacements(
    base_text: str,
    edit_text: str,
) -> list[tuple[str, str]]:
    base_tokens = _normalize_words(base_text)
    edit_tokens = _normalize_words(edit_text)
    if not base_tokens or not edit_tokens:
        return []

    matcher = SequenceMatcher(a=base_tokens, b=edit_tokens, autojunk=False)
    replacements: dict[str, str] = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace":
            continue
        src_len = i2 - i1
        dst_len = j2 - j1
        if src_len != dst_len:
            continue
        if src_len < 1 or src_len > 6:
            continue
        # Single-token global replacements jsou rizikové; povol jen delší výrazy.
        if src_len == 1 and len(base_tokens[i1]) < 5:
            continue
        src = " ".join(base_tokens[i1:i2]).strip()
        dst = " ".join(edit_tokens[j1:j2]).strip()
        if not src or not dst or src == dst:
            continue
        replacements[src] = dst

    # Delší fráze aplikuj dřív.
    return sorted(replacements.items(), key=lambda pair: (-len(pair[0].split()), -len(pair[0])))


def _apply_edit_replacements_to_clip(
    clip_text: str,
    replacements: list[tuple[str, str]],
) -> str:
    if not clip_text or not replacements:
        return clip_text
    updated = clip_text
    for src, dst in replacements:
        pattern = re.compile(
            r"(?<!\w)" + r"\s+".join(re.escape(token) for token in src.split()) + r"(?!\w)",
            flags=re.IGNORECASE | re.UNICODE,
        )
        if not pattern.search(updated):
            continue
        updated = pattern.sub(dst, updated)
    return updated


def _apply_edit_md_overrides(
    *,
    sub_dir: Path,
    clip_text: str,
) -> str:
    md_files = sorted([f for f in sub_dir.glob("*.md") if f.is_file()], key=lambda p: p.name.lower())
    if not md_files:
        return clip_text

    edit_md = next((f for f in md_files if _is_edit_md(f)), None)
    if edit_md is None:
        return clip_text

    non_edit_mds = [f for f in md_files if not _is_edit_md(f)]
    if not non_edit_mds:
        return clip_text

    edit_base_stem = re.sub(r"[-_]edit$", "", edit_md.stem, flags=re.IGNORECASE)
    base_md = next((f for f in non_edit_mds if f.stem == edit_base_stem), non_edit_mds[0])

    base_text = _extract_md_body_text(base_md)
    edit_text = _extract_md_body_text(edit_md)
    if not base_text or not edit_text:
        return clip_text

    replacements = _build_edit_phrase_replacements(base_text, edit_text)
    if not replacements:
        return clip_text

    return _apply_edit_replacements_to_clip(clip_text, replacements)


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
    vtt_files = sorted([f for f in sub_dir.glob("*.vtt") if f.is_file()], key=lambda p: p.name.lower())
    edit_root = subtitles_root.parent / "subtitles_edit" / video_id
    edit_files = sorted([f for f in edit_root.glob("*.vtt") if f.is_file()], key=lambda p: p.name.lower()) if edit_root.exists() else []
    if edit_files:
        vtt_files = edit_files
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
    clip_text = " ".join(deduped)
    return _apply_edit_md_overrides(sub_dir=sub_dir, clip_text=clip_text)
