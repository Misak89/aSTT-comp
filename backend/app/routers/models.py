from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from ..models.models import ModelStatus
from ..services import models_service

router = APIRouter(prefix="/api/models")


class InstallRequest(BaseModel):
    version: Optional[str] = None
    size_mb: Optional[float] = None


class UninstallRequest(BaseModel):
    reason: Optional[str] = None


class NoteRequest(BaseModel):
    note: str


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
