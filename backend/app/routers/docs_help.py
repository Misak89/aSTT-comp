"""
Docs helper router.

Publishes selected local documentation files for quick access from UI.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..config import ROOT

router = APIRouter(prefix="/api/docs", tags=["docs"])

_DOCS_ROOT = ROOT / "docs"


def _serve_doc(filename: str, media_type: str = "text/plain; charset=utf-8") -> FileResponse:
    target = (_DOCS_ROOT / filename).resolve()
    if not str(target).startswith(str(_DOCS_ROOT.resolve())):
        raise HTTPException(status_code=403, detail="path traversal not allowed")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"doc not found: {filename}")
    return FileResponse(path=str(target), media_type=media_type, filename=Path(filename).name)


@router.get("/install-help.txt")
def get_install_help_txt():
    return _serve_doc("install_help.txt", media_type="text/plain; charset=utf-8")


@router.get("/install-help")
def get_install_help_alias():
    """Alias for install_help.txt, easier to type in browser."""
    return _serve_doc("install_help.txt", media_type="text/plain; charset=utf-8")

