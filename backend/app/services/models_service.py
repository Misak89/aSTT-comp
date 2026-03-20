"""
Správa modelů: čtení/zápis install logů, detekce přítomnosti souborů.

Log každého modelu: docs/models/{model_id}.json
Soubory modelů: runtime/model_store/{model_id}/
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..config import MODELS_LOG_ROOT, MODEL_STORE_ROOT
from ..models.models import ModelEvent, ModelLog, ModelStatus
from ..services import benchmark_service

# Mapování model_id → label (sdíleno s benchmark_service)
_MODEL_LABELS: dict[str, str] = {
    m["id"]: m["label"] for m in benchmark_service.DEFAULT_MODELS
}


def list_models() -> list[ModelStatus]:
    statuses = []
    for model_id, label in _MODEL_LABELS.items():
        log = _read_log(model_id)
        installed = _is_installed(model_id)
        last_install = _last_event_date(log, "install")
        last_uninstall = _last_event_date(log, "uninstall")
        size_mb = _installed_size_mb(model_id) if installed else None
        statuses.append(ModelStatus(
            model_id=model_id,
            label=label,
            installed=installed,
            last_install=last_install,
            last_uninstall=last_uninstall,
            size_mb=size_mb,
            events=log.events,
        ))
    return statuses


def get_model(model_id: str) -> Optional[ModelStatus]:
    if model_id not in _MODEL_LABELS:
        return None
    log = _read_log(model_id)
    installed = _is_installed(model_id)
    return ModelStatus(
        model_id=model_id,
        label=_MODEL_LABELS[model_id],
        installed=installed,
        last_install=_last_event_date(log, "install"),
        last_uninstall=_last_event_date(log, "uninstall"),
        size_mb=_installed_size_mb(model_id) if installed else None,
        events=log.events,
    )


def record_install(model_id: str, version: Optional[str] = None,
                   size_mb: Optional[float] = None) -> ModelStatus:
    log = _read_log(model_id)
    log.events.append(ModelEvent(
        type="install",
        date=datetime.now(timezone.utc).isoformat(),
        version=version,
        size_mb=size_mb,
    ))
    _write_log(log)
    return get_model(model_id)  # type: ignore[return-value]


def record_uninstall(model_id: str, reason: Optional[str] = None) -> Optional[ModelStatus]:
    if model_id not in _MODEL_LABELS:
        return None
    log = _read_log(model_id)
    log.events.append(ModelEvent(
        type="uninstall",
        date=datetime.now(timezone.utc).isoformat(),
        reason=reason,
    ))
    _write_log(log)
    return get_model(model_id)


def add_note(model_id: str, note: str) -> Optional[ModelStatus]:
    if model_id not in _MODEL_LABELS:
        return None
    log = _read_log(model_id)
    log.events.append(ModelEvent(
        type="note",
        date=datetime.now(timezone.utc).isoformat(),
        note=note,
    ))
    _write_log(log)
    return get_model(model_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log_path(model_id: str) -> Path:
    return MODELS_LOG_ROOT / f"{model_id}.json"


def _read_log(model_id: str) -> ModelLog:
    path = _log_path(model_id)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ModelLog(**data)
        except Exception:
            pass
    return ModelLog(model_id=model_id)


def _write_log(log: ModelLog) -> None:
    _log_path(log.model_id).write_text(
        json.dumps(log.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _is_installed(model_id: str) -> bool:
    """Model je 'nainstalovaný' pokud existuje jeho adresář s alespoň jedním souborem."""
    model_dir = MODEL_STORE_ROOT / model_id
    if not model_dir.exists():
        return False
    return any(model_dir.iterdir())


def _installed_size_mb(model_id: str) -> Optional[float]:
    model_dir = MODEL_STORE_ROOT / model_id
    if not model_dir.exists():
        return None
    total = sum(f.stat().st_size for f in model_dir.rglob("*") if f.is_file())
    return round(total / 1024 / 1024, 1)


def _last_event_date(log: ModelLog, event_type: str) -> Optional[str]:
    for ev in reversed(log.events):
        if ev.type == event_type:
            return ev.date
    return None
