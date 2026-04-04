"""
Source library management: CRUD, subtitle download, results storage.
"""
from __future__ import annotations
import array
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

from packages.common.network_access import ensure_online_allowed

from ..config import LIBRARY_ROOT, SUBTITLES_ROOT, RESULTS_ROOT, MODEL_STORE_ROOT, AUDIO_CACHE_ROOT
from ..models.library import (
    LatestResult,
    LibraryItem,
    SegmentBundle,
    SegmentBundlePreviewRequest,
    SegmentItem,
    SubtitleFile,
    UpsertLibraryItemRequest,
)

_ITEMS_FILE = LIBRARY_ROOT / "items.json"
_ITEMS_LOCK = threading.Lock()
_SEGMENTS_ROOT = LIBRARY_ROOT / "segments"
_SEGMENTS_ROOT.mkdir(parents=True, exist_ok=True)
_SEGMENT_PRESET_MINUTES = {5, 10, 15, 30, 45, 60}
_MAX_MANUAL_POINTS = 21
_SEGMENT_TMP_ROOT = _SEGMENTS_ROOT / "_tmp"
_SEGMENT_TMP_ROOT.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _load_raw() -> list[dict]:
    if not _ITEMS_FILE.exists():
        return []
    return json.loads(_ITEMS_FILE.read_text(encoding="utf-8"))


def _save_raw(items: list[dict]) -> None:
    _ITEMS_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _update_item_fields(video_id: str, fields: dict) -> None:
    """Atomicky aktualizuje pole jednoho záznamu — thread-safe."""
    with _ITEMS_LOCK:
        raw = _load_raw()
        for item in raw:
            if item["video_id"] == video_id:
                item.update(fields)
                break
        _save_raw(raw)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_items() -> list[LibraryItem]:
    raw = _load_raw()
    result = []
    for r in raw:
        video_id = r.get("video_id", "")
        subtitle_files = _list_subtitle_files(video_id)
        subtitle_manual = [str(x) for x in (r.get("subtitle_manual") or []) if str(x).strip()]
        subtitle_auto = [str(x) for x in (r.get("subtitle_auto") or []) if str(x).strip()]
        subtitle_languages = [str(x) for x in (r.get("subtitle_languages") or []) if str(x).strip()]
        if not subtitle_languages:
            subtitle_languages = _infer_subtitle_languages_from_files(
                subtitle_files,
                fallback_language=r.get("language"),
            )
        result.append(LibraryItem(
            video_id=video_id,
            title=r.get("title", ""),
            url=r.get("url", ""),
            duration_seconds=r.get("duration_seconds"),
            language=r.get("language", "cs"),
            genre=r.get("genre"),
            visible_in_menus=bool(r.get("visible_in_menus", True)),
            subtitles_local=bool(subtitle_files),
            subtitle_files=subtitle_files,
            subtitle_manual=subtitle_manual,
            subtitle_auto=subtitle_auto,
            subtitle_languages=subtitle_languages,
            added_at=r.get("added_at"),
            upload_date=r.get("upload_date"),
            view_count=r.get("view_count"),
            metadata_fetched_at=r.get("metadata_fetched_at"),
            audio_cached=(_wav := AUDIO_CACHE_ROOT / f"{video_id}.wav").exists(),
            audio_size_bytes=_wav.stat().st_size if _wav.exists() else None,
            audio_duration_seconds=r.get("audio_duration_seconds"),
        ))
    return result


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_segment_source_id(source_id: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]", "_", str(source_id).strip())
    clean = clean.strip("._-")
    if not clean:
        raise ValueError("Invalid source_id")
    return clean[:120]


def _segment_bundle_file(source_id: str) -> Path:
    safe = _sanitize_segment_source_id(source_id)
    return _SEGMENTS_ROOT / f"{safe}.json"


def _library_item_exists(source_id: str) -> bool:
    return any(str(row.get("video_id", "")) == source_id for row in _load_raw())


def resolve_audio_file_for_library_item(video_id: str) -> Optional[Path]:
    for ext in (".wav", ".mp3", ".mp4", ".m4a", ".ogg", ".webm"):
        cached = AUDIO_CACHE_ROOT / f"{video_id}{ext}"
        if cached.exists():
            return cached
    for item in _load_raw():
        if str(item.get("video_id", "")) != video_id:
            continue
        raw_url = str(item.get("url") or "").strip()
        if not raw_url:
            return None
        if raw_url.startswith("file://"):
            parsed = urlparse(raw_url)
            path_str = unquote(parsed.path or "")
            if re.match(r"^/[A-Za-z]:", path_str):
                path_str = path_str[1:]
            local_path = Path(path_str)
            return local_path if local_path.exists() else None
        candidate = Path(raw_url)
        return candidate if candidate.exists() else None
    return None


def _normalize_manual_points(points: list[float], duration_s: float) -> list[float]:
    normalized: list[float] = []
    for point in points:
        value = round(float(point), 3)
        if value <= 0.0 or value >= duration_s:
            continue
        normalized.append(value)
    normalized = sorted(set(normalized))
    if len(normalized) > _MAX_MANUAL_POINTS:
        raise ValueError(f"manual_points_seconds supports max {_MAX_MANUAL_POINTS} points")
    return normalized


def _build_preset_points(*, duration_s: float, preset_minutes: int) -> list[float]:
    if preset_minutes not in _SEGMENT_PRESET_MINUTES:
        allowed = ", ".join(str(v) for v in sorted(_SEGMENT_PRESET_MINUTES))
        raise ValueError(f"preset_minutes must be one of: {allowed}")
    step_s = preset_minutes * 60.0
    points: list[float] = []
    cursor = step_s
    while cursor < duration_s:
        points.append(round(cursor, 3))
        cursor += step_s
    return points


def _ensure_wav_for_silence_scan(path: Path) -> tuple[Path, bool]:
    out_path = _SEGMENT_TMP_ROOT / f"segscan_{uuid.uuid4().hex[:8]}.wav"
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(path),
            "-vn", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0 or not out_path.exists():
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ValueError(f"Failed to prepare WAV for pause-aware scan: {detail}")
    return out_path, True


def _detect_silence_ranges(
    wav_path: Path,
    *,
    silence_dbfs: float,
    min_silence_ms: int,
    frame_ms: int = 20,
) -> list[tuple[float, float]]:
    import wave

    with wave.open(str(wav_path), "rb") as wf:
        sample_width = max(1, int(wf.getsampwidth()))
        sample_rate = max(1, int(wf.getframerate()))
        frame_count = max(1, int(sample_rate * frame_ms / 1000.0))
        max_amp = float((1 << (8 * sample_width - 1)) - 1)
        threshold_amp = max(1.0, max_amp * (10.0 ** (silence_dbfs / 20.0)))

        silence_ranges: list[tuple[float, float]] = []
        in_silence = False
        silence_start_s = 0.0
        cursor_s = 0.0
        chunk_dur_s = frame_count / float(sample_rate)

        while True:
            raw = wf.readframes(frame_count)
            if not raw:
                break
            if sample_width != 2:
                raise ValueError("pause-aware scanner expects 16-bit PCM WAV")
            pcm = array.array("h")
            pcm.frombytes(raw)
            if not pcm:
                break
            sum_sq = 0.0
            for value in pcm:
                sum_sq += float(value) * float(value)
            rms = math.sqrt(sum_sq / float(len(pcm)))
            is_silence = float(rms) <= threshold_amp

            if is_silence and not in_silence:
                in_silence = True
                silence_start_s = cursor_s
            elif not is_silence and in_silence:
                in_silence = False
                end_s = cursor_s
                if (end_s - silence_start_s) * 1000.0 >= float(min_silence_ms):
                    silence_ranges.append((round(silence_start_s, 3), round(end_s, 3)))

            cursor_s += chunk_dur_s

        if in_silence:
            end_s = cursor_s
            if (end_s - silence_start_s) * 1000.0 >= float(min_silence_ms):
                silence_ranges.append((round(silence_start_s, 3), round(end_s, 3)))

    return silence_ranges


def _pause_aware_snap_points(
    *,
    source_audio_path: Path,
    points: list[float],
    duration_s: float,
    tolerance_s: float,
    silence_dbfs: float,
    min_silence_ms: int,
) -> tuple[list[float], list[dict]]:
    if not points or tolerance_s <= 0.0:
        meta = [{"original": p, "snapped": p, "delta_ms": 0.0, "changed": False} for p in points]
        return points, meta

    wav_path, is_temp = _ensure_wav_for_silence_scan(source_audio_path)
    try:
        silence_ranges = _detect_silence_ranges(
            wav_path,
            silence_dbfs=silence_dbfs,
            min_silence_ms=min_silence_ms,
        )
    finally:
        if is_temp:
            wav_path.unlink(missing_ok=True)

    snapped_points: list[float] = []
    metadata: list[dict] = []
    prev_boundary = 0.0

    for idx, original in enumerate(points):
        next_original = points[idx + 1] if idx + 1 < len(points) else duration_s
        left = max(prev_boundary, original - tolerance_s)
        right = min(next_original, original + tolerance_s)
        candidate = original

        nearest_dist = math.inf
        for start_s, end_s in silence_ranges:
            if end_s < left or start_s > right:
                continue
            clipped_start = max(left, start_s)
            clipped_end = min(right, end_s)
            if clipped_end <= clipped_start:
                continue
            center = (clipped_start + clipped_end) / 2.0
            dist = abs(center - original)
            if dist < nearest_dist:
                nearest_dist = dist
                candidate = center

        candidate = round(float(candidate), 3)
        if candidate <= prev_boundary or candidate >= next_original:
            candidate = round(float(original), 3)
        changed = abs(candidate - original) >= 0.001
        delta_ms = round((candidate - original) * 1000.0, 1)
        snapped_points.append(candidate)
        metadata.append(
            {
                "original": round(float(original), 3),
                "snapped": candidate,
                "delta_ms": delta_ms,
                "changed": changed,
            }
        )
        prev_boundary = candidate

    return snapped_points, metadata


def _build_segments(
    points: list[float],
    duration_s: float,
    *,
    boundary_metadata: list[dict] | None = None,
) -> list[SegmentItem]:
    boundaries = [0.0, *points, duration_s]
    segments: list[SegmentItem] = []
    for idx in range(len(boundaries) - 1):
        start = round(float(boundaries[idx]), 3)
        end = round(float(boundaries[idx + 1]), 3)
        if end <= start:
            continue
        seg_meta = boundary_metadata[idx] if boundary_metadata and idx < len(boundary_metadata) else None
        segments.append(
            SegmentItem(
                idx=idx,
                start_s=start,
                end_s=end,
                duration_s=round(end - start, 3),
                snapped=bool(seg_meta and seg_meta.get("changed")),
                snapped_from_s=float(seg_meta["original"]) if seg_meta and seg_meta.get("changed") else None,
                snap_delta_ms=float(seg_meta["delta_ms"]) if seg_meta and seg_meta.get("changed") else None,
                snap_reason="pause-aware" if seg_meta and seg_meta.get("changed") else None,
            )
        )
    if not segments:
        raise ValueError("No valid segments generated")
    return segments


def _compose_segment_bundle(
    req: SegmentBundlePreviewRequest,
    *,
    created_at: str | None = None,
) -> SegmentBundle:
    source_id = str(req.source_id).strip()
    if not source_id:
        raise ValueError("source_id is required")
    if req.source_type == "library_item" and not _library_item_exists(source_id):
        raise ValueError(f"library item not found: {source_id}")

    duration_s = round(float(req.audio_duration_seconds), 3)
    if duration_s <= 0:
        raise ValueError("audio_duration_seconds must be > 0")

    if req.mode == "preset":
        if req.preset_minutes is None:
            raise ValueError("preset_minutes is required for preset mode")
        points = _build_preset_points(duration_s=duration_s, preset_minutes=int(req.preset_minutes))
    else:
        points = _normalize_manual_points(req.manual_points_seconds, duration_s)
    snapped_points = points
    boundary_meta = [{"original": p, "snapped": p, "delta_ms": 0.0, "changed": False} for p in points]

    if req.pause_aware and points:
        if req.source_type != "library_item":
            raise ValueError("pause-aware snapping currently supports source_type=library_item")
        audio_path = resolve_audio_file_for_library_item(source_id)
        if audio_path is None:
            raise ValueError(f"audio file not found for source_id={source_id}")
        snapped_points, boundary_meta = _pause_aware_snap_points(
            source_audio_path=audio_path,
            points=points,
            duration_s=duration_s,
            tolerance_s=float(req.tolerance_seconds),
            silence_dbfs=float(req.pause_silence_dbfs),
            min_silence_ms=int(req.pause_min_silence_ms),
        )

    segments = _build_segments(snapped_points, duration_s, boundary_metadata=boundary_meta)
    now = _utc_now_iso()
    return SegmentBundle(
        source_id=source_id,
        source_type=req.source_type,
        mode=req.mode,
        audio_duration_seconds=duration_s,
        tolerance_seconds=round(float(req.tolerance_seconds), 3),
        preset_minutes=int(req.preset_minutes) if req.preset_minutes is not None else None,
        points_seconds=snapped_points,
        segments=segments,
        created_at=created_at or now,
        updated_at=now,
    )


def preview_segment_bundle(req: SegmentBundlePreviewRequest) -> SegmentBundle:
    return _compose_segment_bundle(req)


def upsert_segment_bundle(source_id: str, req: SegmentBundlePreviewRequest) -> SegmentBundle:
    expected = str(source_id).strip()
    if expected != str(req.source_id).strip():
        raise ValueError("source_id in path must match source_id in request body")
    bundle_file = _segment_bundle_file(expected)
    existing_created_at: str | None = None
    if bundle_file.exists():
        try:
            existing = json.loads(bundle_file.read_text(encoding="utf-8"))
            existing_created_at = str(existing.get("created_at") or "") or None
        except Exception:
            existing_created_at = None
    bundle = _compose_segment_bundle(req, created_at=existing_created_at)
    bundle_file.write_text(
        json.dumps(bundle.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return bundle


def get_segment_bundle(source_id: str) -> SegmentBundle:
    bundle_file = _segment_bundle_file(source_id)
    if not bundle_file.exists():
        raise KeyError(source_id)
    raw = json.loads(bundle_file.read_text(encoding="utf-8"))
    return SegmentBundle(**raw)


def _infer_subtitle_languages_from_files(
    subtitle_files: list[SubtitleFile],
    fallback_language: str | None = None,
) -> list[str]:
    """Odhadne jazyky titulků z názvů lokálních souborů."""
    langs: set[str] = set()
    stop_tokens = {"na", "md", "txt", "vtt", "srt", "orig", "edit"}
    for f in subtitle_files:
        name = f.filename.lower()
        # Hledej tokeny typu ".cs.", "_en_", "-de-" apod.
        for token in re.findall(r"(?:^|[._-])([a-z]{2})(?:$|[._-])", name):
            if token in stop_tokens:
                continue
            langs.add(token)
    if not langs and fallback_language:
        lang = str(fallback_language).strip().lower()
        if len(lang) >= 2:
            langs.add(lang[:2])
    return sorted(langs)


def _normalize_upload_date(raw_date: str | None) -> Optional[str]:
    if not raw_date:
        return None
    value = str(raw_date).strip()
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    if len(value) >= 10:
        return value[:10]
    return None


def _normalize_language(raw_language: str | None) -> Optional[str]:
    if not raw_language:
        return None
    value = str(raw_language).strip().lower()
    if not value:
        return None
    # yt-dlp může vracet varianty typu "cs-CZ"; držíme krátký kód.
    return value.split("-")[0]


def _fetch_video_metadata(video_id: str, url: str) -> dict:
    """Načte metadata videa synchronně a vrátí pole pro items.json."""
    ensure_online_allowed(
        component="backend.app.services.library_service",
        action="_fetch_video_metadata",
        reason="potřebuje načíst metadata videa (délka/jazyk/titulky/žánr/datum/význam zhlédnutí)",
        target=url,
        details={"video_id": video_id},
    )
    import yt_dlp as _yt

    with _yt.YoutubeDL({"quiet": True, "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)

    manual_subs = sorted((info.get("subtitles") or {}).keys())
    auto_subs = sorted((info.get("automatic_captions") or {}).keys())
    subtitle_langs = sorted(set(manual_subs) | set(auto_subs))

    duration_raw = info.get("duration")
    view_count_raw = info.get("view_count")
    categories = info.get("categories") or []

    metadata: dict = {
        "subtitle_manual": manual_subs,
        "subtitle_auto": auto_subs,
        "subtitle_languages": subtitle_langs,
        "metadata_fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    if isinstance(duration_raw, (int, float)):
        metadata["duration_seconds"] = round(float(duration_raw), 1)

    lang = _normalize_language(info.get("language"))
    if lang:
        metadata["language"] = lang

    upload_date = _normalize_upload_date(info.get("upload_date"))
    if upload_date:
        metadata["upload_date"] = upload_date

    if isinstance(view_count_raw, int):
        metadata["view_count"] = int(view_count_raw)

    if categories:
        # Primárně první kategorie, aby genre zůstalo krátké a stabilní.
        metadata["genre"] = str(categories[0]).strip()

    return metadata


def fetch_video_info(url: str) -> dict:
    """Načte základní info o YouTube videu (title, language, duration) pro preview před přidáním."""
    ensure_online_allowed(
        component="backend.app.services.library_service",
        action="fetch_video_info",
        reason="yt-dlp čte název a základní metadata videa",
        target=url,
        details={},
    )
    import yt_dlp as _yt
    with _yt.YoutubeDL({"quiet": True, "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    lang = _normalize_language(info.get("language")) or "cs"
    return {
        "title": info.get("title") or "",
        "language": lang,
        "duration_seconds": round(float(info["duration"]), 1) if isinstance(info.get("duration"), (int, float)) else None,
        "uploader": info.get("uploader") or info.get("channel") or "",
    }


def _fetch_and_save_upload_date(video_id: str, url: str) -> None:
    """Background: stáhne upload_date z YouTube přes yt-dlp a uloží do items.json."""
    try:
        ensure_online_allowed(
            component="backend.app.services.library_service",
            action="_fetch_and_save_upload_date",
            reason="yt-dlp čte metadata videa (upload_date)",
            target=url,
            details={"video_id": video_id},
        )
        import yt_dlp as _yt
        with _yt.YoutubeDL({"quiet": True, "skip_download": True, "extract_flat": True}) as ydl:
            info = ydl.extract_info(url, download=False)
        raw_date = info.get("upload_date") or ""  # YYYYMMDD
        if raw_date and len(raw_date) == 8:
            upload_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
            _update_item_fields(video_id, {"upload_date": upload_date})
    except Exception:
        pass


def _detect_and_save_language(video_id: str, url: str) -> None:
    """Background: stream YouTube audio od 60s, detekuje jazyk přes whisper-cli."""
    try:
        ensure_online_allowed(
            component="backend.app.services.library_service",
            action="_detect_and_save_language",
            reason="potřebuje stream audio z YouTube pro detekci jazyka",
            target=url,
            details={"video_id": video_id},
        )
        from packages.adapters.whisper_cpp_runner import resolve_whisper_cli
        from packages.ingest.youtube.stream_pipe import stream_youtube_audio
        from array import array
        import wave as _wave

        whisper_bin = resolve_whisper_cli(MODEL_STORE_ROOT)
        if not whisper_bin:
            return

        # Nejmenší dostupný model
        model_path = None
        for name in ("whisper_cpp_base", "whisper_cpp_small", "whisper_cpp_large_v3"):
            p = MODEL_STORE_ROOT / name
            bins = list(p.glob("*.bin")) if p.exists() else []
            if bins:
                model_path = str(bins[0])
                break
        if not model_path:
            return

        # Stream audio — přeskoč prvních 60s, vezmi 15s
        SKIP_S = 60.0
        TAKE_S = 15.0
        all_pcm: list[int] = []
        sample_rate = 16000
        collected_s = 0.0
        skipped_s = 0.0

        for samples, sr in stream_youtube_audio(url, chunk_seconds=0.5, max_seconds=SKIP_S + TAKE_S):
            sample_rate = sr
            chunk_s = len(samples) / max(1, sr)
            if skipped_s < SKIP_S:
                skipped_s += chunk_s
                continue
            for s in samples:
                all_pcm.append(int(max(-32768, min(32767, s * 32767.0))))
            collected_s += chunk_s
            if collected_s >= TAKE_S:
                break

        if not all_pcm:
            return

        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "detect.wav"
            with _wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(array("h", all_pcm).tobytes())

            result = subprocess.run(
                [whisper_bin, "-m", model_path, "--detect-language", str(wav_path)],
                capture_output=True, text=True, timeout=30,
                encoding="utf-8", errors="replace",
            )
            output = result.stdout + result.stderr

            lang = None
            for line in output.splitlines():
                line_l = line.lower()
                if "detected language" in line_l or "auto-detected" in line_l:
                    m = re.search(r'\(([a-z]{2})\)', line_l)
                    if not m:
                        m = re.search(r'language[:\s]+([a-z]{2})\b', line_l)
                    if m:
                        lang = m.group(1)
                        break

            if lang:
                _update_item_fields(video_id, {"language": lang})
    except Exception:
        pass


def _download_and_cache_audio(video_id: str, url: str) -> None:
    """Background: stáhne plné audio videa do runtime/audio_cache/{video_id}.wav.
    Po úspěšném stažení aktualizuje duration_seconds v items.json ze skutečné délky WAV.
    """
    out_path = AUDIO_CACHE_ROOT / f"{video_id}.wav"
    if out_path.exists():
        return
    try:
        ensure_online_allowed(
            component="backend.app.services.library_service",
            action="_download_and_cache_audio",
            reason="yt-dlp + ffmpeg stahují plné audio do lokální cache",
            target=url,
            details={"video_id": video_id},
        )
        ytdlp_cmd = [
            sys.executable, "-m", "yt_dlp",
            "--quiet", "--no-playlist",
            "--format", "bestaudio/best",
            "-o", "-",
            url,
        ]
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0",
            "-vn", "-ac", "1", "-ar", "16000",
            str(out_path),
        ]
        ytdlp = subprocess.Popen(ytdlp_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        ffmpeg = subprocess.Popen(
            ffmpeg_cmd,
            stdin=ytdlp.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        ytdlp.stdout.close()  # type: ignore[union-attr]
        _, ffmpeg_err = ffmpeg.communicate()
        ytdlp.wait()
        if ffmpeg.returncode != 0 or not out_path.exists():
            out_path.unlink(missing_ok=True)
            return
        # Přečti skutečnou délku ze WAV a ulož do items.json
        # (YouTube metadata mohou být nepřesná, WAV je autoritativní zdroj)
        try:
            import wave as _wave
            with _wave.open(str(out_path), "rb") as wf:
                duration_s = round(wf.getnframes() / max(1, wf.getframerate()), 1)
            _update_item_fields(video_id, {"duration_seconds": duration_s})
        except Exception:
            pass
    except Exception:
        out_path.unlink(missing_ok=True)


def upsert_item(req: UpsertLibraryItemRequest) -> LibraryItem:
    metadata_fields: dict = {}
    try:
        metadata_fields = _fetch_video_metadata(req.video_id, req.url)
    except Exception:
        # Metadata fetch nesmí blokovat vložení položky do knihovny.
        metadata_fields = {
            "metadata_fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    with _ITEMS_LOCK:
        raw = _load_raw()
        existing = {r["video_id"]: r for r in raw}
        is_new = req.video_id not in existing
        prev = existing.get(req.video_id, {})
        merged = {
            "video_id": req.video_id,
            "title": req.title,
            "url": req.url,
            "duration_seconds": metadata_fields.get(
                "duration_seconds",
                req.duration_seconds if req.duration_seconds is not None else prev.get("duration_seconds"),
            ),
            "language": metadata_fields.get("language", req.language or prev.get("language", "cs")),
            "genre": metadata_fields.get(
                "genre",
                req.genre if req.genre is not None else prev.get("genre"),
            ),
            "visible_in_menus": (
                req.visible_in_menus
                if req.visible_in_menus is not None
                else bool(prev.get("visible_in_menus", True))
            ),
            "upload_date": metadata_fields.get("upload_date", prev.get("upload_date")),
            "view_count": metadata_fields.get("view_count", prev.get("view_count")),
            "subtitle_manual": metadata_fields.get("subtitle_manual", prev.get("subtitle_manual", [])),
            "subtitle_auto": metadata_fields.get("subtitle_auto", prev.get("subtitle_auto", [])),
            "subtitle_languages": metadata_fields.get("subtitle_languages", prev.get("subtitle_languages", [])),
            "metadata_fetched_at": metadata_fields.get("metadata_fetched_at", prev.get("metadata_fetched_at")),
            "added_at": prev.get("added_at") or datetime.now(timezone.utc).isoformat(),
        }
        if not is_new:
            existing[req.video_id].update(merged)
        else:
            existing[req.video_id] = merged
        _save_raw(list(existing.values()))

    # Background: stažení audio cache (pro nová i existující videa bez audio)
    if not (AUDIO_CACHE_ROOT / f"{req.video_id}.wav").exists():
        threading.Thread(
            target=_download_and_cache_audio,
            args=(req.video_id, req.url),
            daemon=True,
        ).start()

    return list_items()[list(existing.keys()).index(req.video_id)]


def set_item_visibility(video_id: str, visible_in_menus: bool) -> LibraryItem:
    with _ITEMS_LOCK:
        raw = _load_raw()
        idx = None
        for i, row in enumerate(raw):
            if row.get("video_id") == video_id:
                idx = i
                break
        if idx is None:
            raise KeyError(video_id)
        raw[idx]["visible_in_menus"] = bool(visible_in_menus)
        _save_raw(raw)

    items = list_items()
    for item in items:
        if item.video_id == video_id:
            return item
    raise KeyError(video_id)


def download_subtitles(video_id: str, url: str) -> dict:
    out_dir = SUBTITLES_ROOT / video_id
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        ensure_online_allowed(
            component="backend.app.services.library_service",
            action="download_subtitles",
            reason="stažení titulků všech dostupných jazyků přes yt-dlp",
            target=url,
            details={"video_id": video_id},
        )
        result = subprocess.run(
            [
                sys.executable, "-m", "yt_dlp",
                "--write-sub", "--write-auto-sub",
                "--sub-langs", "all,-live_chat",
                "--sub-format", "vtt/best",
                "--skip-download",
                "--output", str(out_dir / "%(id)s.%(ext)s"),
                url,
            ],
            capture_output=True, text=True, timeout=120,
        )
        files = _list_subtitle_files(video_id)
        stderr_tail = (result.stderr or "")[-1200:]
        stdout_tail = (result.stdout or "")[-500:]

        # yt-dlp může vrátit non-zero i při částečném stažení.
        # Pokud nevznikl žádný soubor, považujeme to za fail.
        if result.returncode != 0 and not files:
            err = stderr_tail.strip() or stdout_tail.strip() or "yt-dlp failed"
            return {
                "ok": False,
                "error": err,
                "returncode": result.returncode,
            }

        return {
            "ok": True,
            "files": [f.model_dump() for f in files],
            "stderr": stderr_tail,
            "returncode": result.returncode,
        }
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

_LANG_KEYWORDS = {
    "cs": ["čeština", "česky", "czech"],
    "sk": ["slovenčina", "slovensky", "slovak"],
    "pl": ["polština", "po polsku", "polish"],
    "uk": ["українська", "українською", "ukrainian"],
    "en": ["english", "anglicky"],
    "de": ["deutsch", "německy", "german"],
}
_CATEGORY_KEYWORDS = {
    "music":   "hudba music",
    "film":    "film movie",
    "gaming":  "gaming hry",
    "news":    "zprávy news",
    "sport":   "sport",
    "podcast": "podcast",
}
_TYPE_KEYWORDS = {
    "interview": "rozhovor interview",
    "monolog":   "přednáška monolog talk",
}


def search_youtube(
    q: str,
    max_results: int = 20,
    min_duration: int = 0,
    max_duration: int = 0,
    min_views: int = 0,
    uploaded_after: str = "",
    audio_langs: list[str] | None = None,
    subtitle_langs: list[str] | None = None,
    subtitle_type: str = "any",   # any | manual | auto
    content_type: str = "any",    # any | interview | monolog
    categories: list[str] | None = None,
) -> list[dict]:
    """Vyhledá videa na YouTube přes yt-dlp (bez API klíče)."""
    ensure_online_allowed(
        component="backend.app.services.library_service",
        action="search_youtube",
        reason="vyhledání YouTube videí a metadata přes yt-dlp",
        target="https://www.youtube.com",
        details={
            "query": q,
            "max_results": max_results,
            "min_duration": min_duration,
            "max_duration": max_duration,
            "min_views": min_views,
        },
    )
    import yt_dlp as _yt
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Sestav query — přidej klíčová slova z filtrů
    parts = [q] if q.strip() else []
    if audio_langs:
        lang_words = []
        for lang in audio_langs:
            lang_words.extend(_LANG_KEYWORDS.get(lang, [])[:1])
        if lang_words:
            parts.append(" OR ".join(lang_words))
    if content_type != "any" and content_type in _TYPE_KEYWORDS:
        parts.append(_TYPE_KEYWORDS[content_type])
    if categories:
        for cat in categories:
            if cat in _CATEGORY_KEYWORDS:
                parts.append(_CATEGORY_KEYWORDS[cat])
    full_query = " ".join(parts) if parts else "rozhovor OR podcast OR přednáška"

    # Flat search — rychlé, základní metadata
    fetch_count = max_results * 4  # víc, filtrujeme dál
    ydl_opts: dict = {
        "quiet": True,
        "extract_flat": True,
        "skip_download": True,
        "playlist_items": f"1:{fetch_count}",
    }
    if uploaded_after:
        ydl_opts["dateafter"] = uploaded_after

    try:
        with _yt.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{fetch_count}:{full_query}", download=False)
            entries = [e for e in (info.get("entries") or []) if e and e.get("id")]
    except Exception as e:
        return [{"error": str(e)}]

    # Základní filtr bez full fetch
    already_in_library = {r["video_id"] for r in _load_raw()}
    candidates = []
    for e in entries:
        dur = float(e.get("duration") or 0)
        views = int(e.get("view_count") or 0)
        if min_duration > 0 and dur < min_duration:
            continue
        if max_duration > 0 and dur > max_duration:
            continue
        if min_views > 0 and views < min_views:
            continue
        candidates.append(e)
        if len(candidates) >= max_results * 2:
            break

    # Parallel full fetch pro jazyk + titulky
    def _fetch_details(e: dict) -> dict | None:
        vid = e["id"]
        try:
            with _yt.YoutubeDL({"quiet": True, "skip_download": True}) as ydl2:
                vinfo = ydl2.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
            manual_subs = list(vinfo.get("subtitles", {}).keys())
            auto_subs = list(vinfo.get("automatic_captions", {}).keys())
            audio_lang = vinfo.get("language") or ""
            cats = vinfo.get("categories") or []

            # Filtr jazyka audia
            if audio_langs and audio_lang not in audio_langs:
                return None

            # Filtr titulků
            if subtitle_langs:
                if subtitle_type == "manual":
                    available = set(manual_subs)
                elif subtitle_type == "auto":
                    available = set(auto_subs)
                else:
                    available = set(manual_subs) | set(auto_subs)
                if not any(lang in available for lang in subtitle_langs):
                    return None

            # Vyfiltruj auto_subs jen na požadované jazyky (seznam je jinak obří)
            wanted = set(subtitle_langs or [])
            return {
                "video_id": vid,
                "title": vinfo.get("title") or e.get("title") or "",
                "url": f"https://www.youtube.com/watch?v={vid}",
                "duration_seconds": float(vinfo.get("duration") or e.get("duration") or 0),
                "view_count": int(vinfo.get("view_count") or e.get("view_count") or 0),
                "upload_date": vinfo.get("upload_date") or e.get("upload_date") or "",
                "channel": vinfo.get("channel") or e.get("channel") or "",
                "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
                "audio_language": audio_lang,
                "subtitle_manual": [l for l in manual_subs if not wanted or l in wanted],
                "subtitle_auto": [l for l in auto_subs if l in (wanted or {"cs","sk","pl","uk","en","de"})],
                "categories": cats,
                "in_library": vid in already_in_library,
            }
        except Exception:
            return None

    results = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_fetch_details, e): e for e in candidates}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)
            if len(results) >= max_results:
                # Zruš zbývající
                for f in futures:
                    f.cancel()
                break

    # Seřaď podle view_count desc
    results.sort(key=lambda r: r.get("view_count", 0), reverse=True)
    return results[:max_results]


_AUDIO_EXTS = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".opus", ".aac"}
_VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts", ".m2ts"}
_LOCAL_EXTS = _AUDIO_EXTS | _VIDEO_EXTS


def _probe_duration(path: Path) -> Optional[float]:
    """Pokusí se zjistit délku souboru. Vrátí None pokud nelze."""
    if path.suffix.lower() == ".wav":
        try:
            import wave as _wave
            with _wave.open(str(path), "rb") as wf:
                return round(wf.getnframes() / max(1, wf.getframerate()), 1)
        except Exception:
            pass
    # Pro ostatní formáty zkus ffprobe
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=5,
            encoding="utf-8", errors="replace",
        )
        val = result.stdout.strip()
        if val and val not in ("N/A", ""):
            return round(float(val), 1)
    except Exception:
        pass
    return None


def scan_directory(dir_path: str, recursive: bool = False) -> list[dict]:
    """Prohledá adresář a vrátí seznam audio/video souborů."""
    root = Path(dir_path)
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Adresář neexistuje nebo není složka: {dir_path}")
    pattern = "**/*" if recursive else "*"
    entries = []
    for p in sorted(root.glob(pattern)):
        if not p.is_file():
            continue
        if p.suffix.lower() not in _LOCAL_EXTS:
            continue
        entries.append({
            "path": str(p),
            "filename": p.name,
            "size_bytes": p.stat().st_size,
            "duration_seconds": _probe_duration(p),
            "ext": p.suffix.lower().lstrip("."),
        })
    return entries


def import_local_file(file_path: str, title: str, language: str = "cs") -> LibraryItem:
    """Importuje lokální audio/video soubor do knihovny."""
    import hashlib
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        raise ValueError(f"Soubor neexistuje: {file_path}")
    # Stabilní video_id z absolutní cesty
    h = hashlib.sha1(str(p.resolve()).encode("utf-8")).hexdigest()[:8]
    video_id = f"local_{h}"
    url = p.resolve().as_uri()  # file:///...
    duration_seconds = _probe_duration(p)

    req = UpsertLibraryItemRequest(
        video_id=video_id,
        title=title,
        url=url,
        duration_seconds=duration_seconds,
        language=language,
        visible_in_menus=True,
    )
    # Lokální soubory — metadata přes yt-dlp nedělají smysl, vložíme přímo.
    with _ITEMS_LOCK:
        raw = _load_raw()
        existing_map = {r["video_id"]: r for r in raw}
        prev = existing_map.get(video_id, {})
        merged: dict = {
            "video_id": video_id,
            "title": title,
            "url": url,
            "duration_seconds": duration_seconds if duration_seconds is not None else prev.get("duration_seconds"),
            "language": language,
            "genre": prev.get("genre"),
            "visible_in_menus": True,
            "upload_date": prev.get("upload_date"),
            "view_count": prev.get("view_count"),
            "subtitle_manual": prev.get("subtitle_manual", []),
            "subtitle_auto": prev.get("subtitle_auto", []),
            "subtitle_languages": prev.get("subtitle_languages", []),
            "metadata_fetched_at": prev.get("metadata_fetched_at"),
            "added_at": prev.get("added_at") or datetime.now(timezone.utc).isoformat(),
        }
        existing_map[video_id] = merged
        _save_raw(list(existing_map.values()))

    items = list_items()
    for item in items:
        if item.video_id == video_id:
            return item
    raise RuntimeError("Import se nezdařil")


def _list_subtitle_files(video_id: str) -> list[SubtitleFile]:
    sub_dir = SUBTITLES_ROOT / video_id
    if not sub_dir.exists():
        return []
    files = []
    for f in sorted(sub_dir.iterdir()):
        if f.is_file() and f.suffix in (".vtt", ".srt", ".md", ".txt"):
            files.append(SubtitleFile(filename=f.name, size_bytes=f.stat().st_size, ext=f.suffix.lstrip(".")))
    return files
