from __future__ import annotations
from pydantic import BaseModel
from typing import Optional, Literal


class ModelEvent(BaseModel):
    type: Literal["install", "uninstall", "note"]
    date: str                          # ISO 8601
    version: Optional[str] = None
    size_mb: Optional[float] = None
    reason: Optional[str] = None
    note: Optional[str] = None


class ModelLog(BaseModel):
    model_id: str
    events: list[ModelEvent] = []


class ModelStatus(BaseModel):
    model_id: str
    label: str
    installed: bool                    # soubory přítomny v model_store
    last_install: Optional[str] = None # datum posledního installu
    last_uninstall: Optional[str] = None
    size_mb: Optional[float] = None
    events: list[ModelEvent] = []
