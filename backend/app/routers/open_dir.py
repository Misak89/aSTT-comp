"""
Endpoint pro otevření lokálního adresáře v průzkumníku souborů.
Používá whitelist — nemůže otevřít libovolnou cestu mimo projekt.
"""
import subprocess
import sys

from fastapi import APIRouter, HTTPException

from ..config import (
    JOBS_ROOT,
    RUNS_ROOT,
    MODEL_STORE_ROOT,
    MODELS_LOG_ROOT,
    SUBTITLES_ROOT,
    ROOT,
    TRANSCRIPTS_ROOT,
    LOGGER_LOGS_ROOT,
    AUDIO_CACHE_ROOT,
)

router = APIRouter(prefix="/api/open-dir")

# Whitelist dovolených adresářů (key → Path)
_DIRS = {
    "jobs":        JOBS_ROOT,
    "runs":        RUNS_ROOT,
    "model_store": MODEL_STORE_ROOT,
    "models_log":  MODELS_LOG_ROOT,
    "subtitles":   SUBTITLES_ROOT,
    "transcripts": TRANSCRIPTS_ROOT,
    "logger_logs": LOGGER_LOGS_ROOT,
    "audio_cache": AUDIO_CACHE_ROOT,
    "root":        ROOT,
}


@router.post("/{dir_key}")
def open_root_dir(dir_key: str):
    """Otevře kořenový adresář kategorie v průzkumníku."""
    path = _DIRS.get(dir_key)
    if path is None:
        raise HTTPException(status_code=404, detail=f"unknown dir key: {dir_key}")
    _open(path)
    return {"path": str(path)}


@router.post("/{dir_key}/{subdir:path}")
def open_sub_dir(dir_key: str, subdir: str):
    """Otevře podadresář (runtime/jobs/{job_id}/, runtime/runs/{run_id}/ atd.)."""
    base = _DIRS.get(dir_key)
    if base is None:
        raise HTTPException(status_code=404, detail=f"unknown dir key: {dir_key}")
    # Bezpečnostní kontrola — resolved path musí zůstat pod base
    target = (base / subdir).resolve()
    if not str(target).startswith(str(base.resolve())):
        raise HTTPException(status_code=403, detail="path traversal not allowed")
    if not target.exists():
        # Pokud neexistuje, otevři nadřazený
        target = base
    _open(target)
    return {"path": str(target)}


def _open(path) -> None:
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer.exe", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to open directory: {exc}") from exc
