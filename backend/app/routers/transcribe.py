"""
Transcribe router — upload audio, serve audio, správa přepisů (archiv) a nastavení.
"""
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..config import AUDIO_CACHE_ROOT, TRANSCRIPTS_ROOT
from ..services import library_service

UPLOADS_ROOT = AUDIO_CACHE_ROOT / "uploads"
UPLOADS_ROOT.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".mp4", ".m4a", ".ogg", ".flac", ".webm", ".mkv", ".avi", ".mov"}
SETTINGS_FILE = TRANSCRIPTS_ROOT / "ui_settings.json"

router = APIRouter(prefix="/api/transcribe")


# ── Audio upload + serve ──────────────────────────────────────────────────────

@router.post("/upload")
async def upload_audio(file: UploadFile = File(...)):
    """Upload audio/video file for transcription. Returns source_id and audio URL."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: {ext}. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    file_id = str(uuid.uuid4())
    safe_name = f"{file_id}{ext}"
    dest = UPLOADS_ROOT / safe_name

    content = await file.read()
    dest.write_bytes(content)

    return {
        "source_id": file_id,
        "filename": safe_name,
        "original_name": file.filename,
        "url": f"/api/transcribe/audio/{safe_name}",
        "source_path": str(dest),
        "size_bytes": len(content),
    }


@router.get("/audio/{filename}")
def serve_uploaded_audio(filename: str):
    """Serve uploaded audio file to the wavesurfer player."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    path = UPLOADS_ROOT / filename
    if not path.exists():
        raise HTTPException(404, "Audio file not found")
    return FileResponse(str(path))


@router.get("/library-audio/{video_id}")
def serve_library_audio(video_id: str):
    """Serve cached audio for a library video to the wavesurfer player."""
    if not all(c.isalnum() or c in "-_" for c in video_id):
        raise HTTPException(400, "Invalid video_id")
    resolved = library_service.resolve_audio_file_for_library_item(video_id)
    if resolved and resolved.exists():
        return FileResponse(str(resolved))
    raise HTTPException(404, "Audio not cached for this video. Run a benchmark first to cache the audio.")


# ── Archiv přepisů ────────────────────────────────────────────────────────────

class SaveTranscriptRequest(BaseModel):
    title: str
    html: str
    plain_text: str
    transcript_id: str | None = None  # None = nový, jinak update
    source_label: str | None = None   # název videa/souboru
    model_id: str | None = None
    range_from: str | None = None
    range_to: str | None = None


def _load_index() -> list[dict]:
    idx_file = TRANSCRIPTS_ROOT / "index.json"
    if not idx_file.exists():
        return []
    try:
        return json.loads(idx_file.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_index(items: list[dict]) -> None:
    (TRANSCRIPTS_ROOT / "index.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@router.get("/transcripts")
def list_transcripts():
    """Vrátí seznam uložených přepisů (bez HTML obsahu)."""
    return _load_index()


@router.post("/transcripts", status_code=201)
def save_transcript(req: SaveTranscriptRequest):
    """Uloží nebo aktualizuje přepis. Vrátí transcript_id."""
    index = _load_index()
    now = datetime.now(timezone.utc).isoformat()

    tid = req.transcript_id or str(uuid.uuid4())
    entry = next((e for e in index if e["transcript_id"] == tid), None)

    # Filename base: sanitizovaný title pro čitelné soubory na disku
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', req.title or tid).strip().replace(' ', '_')[:60]
    filename_base = safe if safe else tid

    if entry:
        # Update existujícího — smaž staré soubory pokud se filename změnil
        old_base = entry.get("filename_base", tid)
        if old_base != filename_base:
            for ext in (".html", ".txt"):
                old_f = TRANSCRIPTS_ROOT / f"{old_base}{ext}"
                if old_f.exists():
                    old_f.unlink()
        entry["title"] = req.title
        entry["updated_at"] = now
        entry["source_label"] = req.source_label
        entry["model_id"] = req.model_id
        entry["range_from"] = req.range_from
        entry["range_to"] = req.range_to
        entry["plain_text_preview"] = req.plain_text[:200]
        entry["filename_base"] = filename_base
    else:
        # Nový přepis (upsert — přijmout i frontend-generované ID)
        entry = {
            "transcript_id": tid,
            "title": req.title,
            "created_at": now,
            "updated_at": now,
            "source_label": req.source_label,
            "model_id": req.model_id,
            "range_from": req.range_from,
            "range_to": req.range_to,
            "plain_text_preview": req.plain_text[:200],
            "filename_base": filename_base,
        }
        index.insert(0, entry)  # nejnovější první

    # Ulož HTML + TXT pod čitelným jménem
    (TRANSCRIPTS_ROOT / f"{filename_base}.html").write_text(req.html, encoding="utf-8")
    (TRANSCRIPTS_ROOT / f"{filename_base}.txt").write_text(req.plain_text, encoding="utf-8")

    _save_index(index)
    return {"transcript_id": tid, "updated_at": now}


@router.get("/transcripts/{transcript_id}")
def get_transcript(transcript_id: str):
    """Vrátí plný HTML obsah přepisu."""
    if not all(c.isalnum() or c in "-_" for c in transcript_id):
        raise HTTPException(400, "Invalid transcript_id")
    index = _load_index()
    entry = next((e for e in index if e["transcript_id"] == transcript_id), None)
    if entry is None:
        raise HTTPException(404, "Transcript not found")
    fb = entry.get("filename_base", transcript_id)
    html_file = TRANSCRIPTS_ROOT / f"{fb}.html"
    html = html_file.read_text(encoding="utf-8") if html_file.exists() else ""
    return {**entry, "html": html}


@router.delete("/transcripts/{transcript_id}", status_code=204)
def delete_transcript(transcript_id: str):
    """Smaže přepis."""
    if not all(c.isalnum() or c in "-_" for c in transcript_id):
        raise HTTPException(400, "Invalid transcript_id")
    index = _load_index()
    entry = next((e for e in index if e["transcript_id"] == transcript_id), None)
    if entry is None:
        raise HTTPException(404, "Transcript not found")
    fb = entry.get("filename_base", transcript_id)
    for ext in (".html", ".txt"):
        f = TRANSCRIPTS_ROOT / f"{fb}{ext}"
        if f.exists():
            f.unlink()
    _save_index([e for e in index if e["transcript_id"] != transcript_id])


# ── UI Nastavení (pamatuj si nastavení stránky Přepis) ────────────────────────

@router.get("/settings")
def get_settings():
    """Vrátí uložené nastavení UI stránky Přepis."""
    if not SETTINGS_FILE.exists():
        return {}
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


@router.post("/settings")
def save_settings(settings: dict):
    """Uloží nastavení UI stránky Přepis (model, rozsah, layout, rychlost, ...)."""
    # Ochrana: max 10 KB
    raw = json.dumps(settings, ensure_ascii=False)
    if len(raw) > 10_000:
        raise HTTPException(400, "Settings too large (max 10 KB)")
    SETTINGS_FILE.write_text(raw, encoding="utf-8")
    return {"ok": True}
