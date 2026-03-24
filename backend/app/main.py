"""
aSTT-comp backend — FastAPI entry point.
Mounts routers, serves built frontend from /dist.
"""
import sys
import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Add packages to Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from .routers import health, library, benchmark, runs, models, mic, open_dir, tuning

app = FastAPI(title="aSTT-comp", version="0.1.0")


@app.on_event("startup")
def _detect_languages_on_startup():
    """Spustí detekci jazyka pro všechna videa kde jazyk nebyl auto-detekován."""
    from .services import library_service

    def _run():
        raw = library_service._load_raw()
        for item in raw:
            # Přeskoč videa kde jazyk byl již auto-detekován (krátký kód) nebo není URL
            vid = item.get("video_id", "")
            url = item.get("url", "")
            if not url or not vid:
                continue
            # Spusť detekci jazyka
            threading.Thread(
                target=library_service._detect_and_save_language,
                args=(vid, url),
                daemon=True,
            ).start()
            # Fetch upload_date pokud chybí
            if not item.get("upload_date"):
                threading.Thread(
                    target=library_service._fetch_and_save_upload_date,
                    args=(vid, url),
                    daemon=True,
                ).start()

    threading.Thread(target=_run, daemon=True).start()

app.include_router(health.router)
app.include_router(library.router)
app.include_router(benchmark.router)
app.include_router(runs.router)
app.include_router(models.router)
app.include_router(mic.router)
app.include_router(open_dir.router)
app.include_router(tuning.router)

# Serve built frontend (production)
_DIST = Path(__file__).parent.parent.parent / "frontend" / "dist"
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        return FileResponse(_DIST / "index.html")
