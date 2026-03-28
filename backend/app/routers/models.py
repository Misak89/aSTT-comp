import subprocess
import sys
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from ..models.models import ModelStatus
from ..services import models_service
from ..config import MODELS_LOG_ROOT, MODEL_STORE_ROOT
from packages.adapters._registry import get_params_schema, list_models as registry_list, REGISTRY

router = APIRouter(prefix="/api/models")


class InstallRequest(BaseModel):
    version: Optional[str] = None
    size_mb: Optional[float] = None


class UninstallRequest(BaseModel):
    reason: Optional[str] = None


class NoteRequest(BaseModel):
    note: str


class AutostartStatus(BaseModel):
    supported: bool
    enabled: bool
    startup_dir: str
    entry_path: str
    script_path: str
    launch_url: str = "http://127.0.0.1:8012/models"


@router.get("/registry")
def get_registry():
    """Vrátí kompletní registry modelů s parametry a capabilities."""
    return [
        {
            "model_id": m.model_id,
            "label": m.label,
            "adapter": m.adapter,
            "languages": m.languages,
            "supports_streaming": m.supports_streaming,
            "supports_microphone": m.supports_microphone,
            "notes": m.notes,
            "params": get_params_schema(m.model_id),
        }
        for m in registry_list()
    ]


@router.get("/{model_id}/params")
def get_model_params(model_id: str):
    """Vrátí schéma parametrů pro konkrétní model."""
    schema = get_params_schema(model_id)
    if not schema and model_id not in REGISTRY:
        raise HTTPException(status_code=404, detail="model not found in registry")
    descriptor = REGISTRY.get(model_id)
    return {
        "model_id": model_id,
        "supports_streaming": descriptor.supports_streaming if descriptor else False,
        "supports_microphone": descriptor.supports_microphone if descriptor else False,
        "params": schema,
    }


@router.get("", response_model=list[ModelStatus])
def list_models():
    return models_service.list_models()


@router.post("/{model_id}/install", response_model=ModelStatus)
def record_install(model_id: str, req: InstallRequest = InstallRequest()):
    m = models_service.get_model(model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="model not found")
    return models_service.record_install(model_id, version=req.version, size_mb=req.size_mb)


@router.delete("/{model_id}", response_model=ModelStatus)
def record_uninstall(model_id: str, req: UninstallRequest = UninstallRequest()):
    m = models_service.record_uninstall(model_id, reason=req.reason)
    if m is None:
        raise HTTPException(status_code=404, detail="model not found")
    return m


@router.post("/{model_id}/note", response_model=ModelStatus)
def add_note(model_id: str, req: NoteRequest):
    m = models_service.add_note(model_id, note=req.note)
    if m is None:
        raise HTTPException(status_code=404, detail="model not found")
    return m


@router.post("/open-store")
def open_store():
    """Otevře kořenový adresář runtime/model_store/ v průzkumníku."""
    _open_in_explorer(MODEL_STORE_ROOT)
    return {"path": str(MODEL_STORE_ROOT)}


@router.post("/open-logs-dir")
def open_logs_dir():
    """Otevře adresář s logy instalací modelů (docs/models/) v průzkumníku."""
    _open_in_explorer(MODELS_LOG_ROOT)
    return {"path": str(MODELS_LOG_ROOT)}


@router.post("/{model_id}/open-store-dir")
def open_model_store_dir(model_id: str):
    """Otevře adresář modelu v runtime/model_store/ v průzkumníku."""
    model_dir = MODEL_STORE_ROOT / model_id
    if not model_dir.exists():
        raise HTTPException(status_code=404, detail="model dir not found")
    _open_in_explorer(model_dir)
    return {"path": str(model_dir)}


@router.get("/webapp-autostart", response_model=AutostartStatus)
def get_webapp_autostart():
    startup_dir = _get_startup_dir()
    entry = _get_startup_entry_path()
    script = _get_startup_script_path()
    supported = sys.platform == "win32"
    enabled = bool(supported and entry.exists())
    return AutostartStatus(
        supported=supported,
        enabled=enabled,
        startup_dir=str(startup_dir),
        entry_path=str(entry),
        script_path=str(script),
    )


@router.post("/webapp-autostart/enable", response_model=AutostartStatus)
def enable_webapp_autostart():
    if sys.platform != "win32":
        raise HTTPException(status_code=400, detail="autostart currently supported on Windows only")
    script_path = _get_startup_script_path()
    if not script_path.exists():
        raise HTTPException(status_code=400, detail=f"missing script: {script_path}")

    startup_dir = _get_startup_dir()
    startup_dir.mkdir(parents=True, exist_ok=True)
    entry = _get_startup_entry_path()
    cmd = (
        "@echo off\r\n"
        f"powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"{script_path}\" -SkipFrontendBuild\r\n"
    )
    entry.write_text(cmd, encoding="ascii")
    return get_webapp_autostart()


@router.post("/webapp-autostart/disable", response_model=AutostartStatus)
def disable_webapp_autostart():
    if sys.platform != "win32":
        raise HTTPException(status_code=400, detail="autostart currently supported on Windows only")
    entry = _get_startup_entry_path()
    if entry.exists():
        entry.unlink()
    return get_webapp_autostart()


@router.post("/webapp-autostart/open-startup-dir")
def open_webapp_startup_dir():
    startup_dir = _get_startup_dir()
    startup_dir.mkdir(parents=True, exist_ok=True)
    _open_in_explorer(startup_dir)
    return {"path": str(startup_dir)}


@router.get("/{model_id}", response_model=ModelStatus)
def get_model(model_id: str):
    m = models_service.get_model(model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="model not found")
    return m


def _open_in_explorer(path) -> None:
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def _get_startup_dir() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    # fallback (non-windows or missing APPDATA) -> project root path placeholder
    return Path.home() / ".config" / "autostart"


def _get_startup_entry_path() -> Path:
    return _get_startup_dir() / "aSTT-comp WebApp.cmd"


def _get_startup_script_path() -> Path:
    return (MODEL_STORE_ROOT.parent.parent / "scripts" / "start_web_app_background.ps1").resolve()
