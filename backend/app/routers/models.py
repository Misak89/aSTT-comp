import subprocess
import sys

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


@router.get("/{model_id}", response_model=ModelStatus)
def get_model(model_id: str):
    m = models_service.get_model(model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="model not found")
    return m


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
