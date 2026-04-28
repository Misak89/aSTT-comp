from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

from packages.common.runtime_paths import runtime_subpath


_LOCK = threading.Lock()
_RUNTIME_DIR = runtime_subpath("network_access")
_LOG_FILE = _RUNTIME_DIR / "network_access_log.jsonl"
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_PROD_VALUES = {"prod", "production"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_bool_env(var_name: str) -> bool | None:
    raw = os.environ.get(var_name)
    if raw is None:
        return None
    val = str(raw).strip().lower()
    if val in _TRUE_VALUES:
        return True
    if val in _FALSE_VALUES:
        return False
    return None


def _is_production_runtime() -> bool:
    for key in ("ASTT_APP_ENV", "ASTT_ENV", "APP_ENV", "ENV", "FASTAPI_ENV"):
        raw = os.environ.get(key)
        if raw is None:
            continue
        if str(raw).strip().lower() in _PROD_VALUES:
            return True
    return False


def strict_offline_enabled() -> bool:
    explicit = _parse_bool_env("ASTT_STRICT_OFFLINE")
    if explicit is not None:
        return explicit
    # Production default: když není explicitně nastaveno, strict offline je zapnutý.
    return _is_production_runtime()


def record_network_event(
    *,
    component: str,
    action: str,
    reason: str,
    target: str | None = None,
    required_online: bool = True,
    details: dict[str, Any] | None = None,
    outcome: str = "attempt",
) -> None:
    payload: dict[str, Any] = {
        "ts_utc": _utc_now(),
        "pid": os.getpid(),
        "component": component,
        "action": action,
        "reason": reason,
        "target": target,
        "required_online": bool(required_online),
        "strict_offline": strict_offline_enabled(),
        "outcome": outcome,
    }
    if details:
        payload["details"] = details

    _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False)
    with _LOCK:
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def ensure_online_allowed(
    *,
    component: str,
    action: str,
    reason: str,
    target: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    if strict_offline_enabled():
        record_network_event(
            component=component,
            action=action,
            reason=reason,
            target=target,
            required_online=True,
            details=details,
            outcome="blocked_offline_mode",
        )
        raise RuntimeError(
            f"Síťový přístup zablokován (ASTT_STRICT_OFFLINE=1): {component}.{action} -> {target or 'n/a'}"
        )

    record_network_event(
        component=component,
        action=action,
        reason=reason,
        target=target,
        required_online=True,
        details=details,
        outcome="allowed",
    )
