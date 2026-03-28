"""
Source library management: CRUD, subtitle download, results storage.
"""
from __future__ import annotations
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from packages.common.network_access import ensure_online_allowed

from ..config import LIBRARY_ROOT, SUBTITLES_ROOT, RESULTS_ROOT, MODEL_STORE_ROOT, AUDIO_CACHE_ROOT
from ..models.library import LibraryItem, SubtitleFile, LatestResult, UpsertLibraryItemRequest

_ITEMS_FILE = LIBRARY_ROOT / "items.json"
_ITEMS_LOCK = threading.Lock()


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
            upload_date=r.get("upload_date"),
            audio_cached=(AUDIO_CACHE_ROOT / f"{video_id}.wav").exists(),
        ))
    return result


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
    with _ITEMS_LOCK:
        raw = _load_raw()
        existing = {r["video_id"]: r for r in raw}
        is_new = req.video_id not in existing
        if not is_new:
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

    # Background: detekce jazyka + datum vydání pro nová videa
    if is_new:
        threading.Thread(
            target=_detect_and_save_language,
            args=(req.video_id, req.url),
            daemon=True,
        ).start()
        threading.Thread(
            target=_fetch_and_save_upload_date,
            args=(req.video_id, req.url),
            daemon=True,
        ).start()

    # Background: stažení audio cache (pro nová i existující videa bez audio)
    if not (AUDIO_CACHE_ROOT / f"{req.video_id}.wav").exists():
        threading.Thread(
            target=_download_and_cache_audio,
            args=(req.video_id, req.url),
            daemon=True,
        ).start()

    return list_items()[list(existing.keys()).index(req.video_id)]


def download_subtitles(video_id: str, url: str) -> dict:
    out_dir = SUBTITLES_ROOT / video_id
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        ensure_online_allowed(
            component="backend.app.services.library_service",
            action="download_subtitles",
            reason="stažení titulků přes yt-dlp",
            target=url,
            details={"video_id": video_id},
        )
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


def _list_subtitle_files(video_id: str) -> list[SubtitleFile]:
    sub_dir = SUBTITLES_ROOT / video_id
    if not sub_dir.exists():
        return []
    files = []
    for f in sorted(sub_dir.iterdir()):
        if f.is_file() and f.suffix in (".vtt", ".srt", ".md", ".txt"):
            files.append(SubtitleFile(filename=f.name, size_bytes=f.stat().st_size, ext=f.suffix.lstrip(".")))
    return files
