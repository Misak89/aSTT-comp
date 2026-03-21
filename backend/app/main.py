"""
aSTT-comp backend — FastAPI entry point.
Mounts routers, serves built frontend from /dist.
"""
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Add packages to Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from .routers import health, library, benchmark, runs, models, mic

app = FastAPI(title="aSTT-comp", version="0.1.0")

app.include_router(health.router)
app.include_router(library.router)
app.include_router(benchmark.router)
app.include_router(runs.router)
app.include_router(models.router)
app.include_router(mic.router)

# Serve built frontend (production)
_DIST = Path(__file__).parent.parent.parent / "frontend" / "dist"
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        return FileResponse(_DIST / "index.html")
